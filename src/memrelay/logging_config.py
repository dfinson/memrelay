"""Structured logging for memrelay (E11-S6, #22).

Configures structlog *over* the stdlib ``logging`` module so the existing
``logging.getLogger(__name__)`` call sites across the daemon, ingester, and engine emit
structured, secret-redacted output with no per-site rewrite, and the daemon and MCP
entrypoints get the same treatment.

Two hard rules shape this module:

* **Everything goes to stderr.** On the MCP stdio transport stdout *is* the protocol
  channel (see :func:`memrelay.mcp.server.run_stdio`), so a stray log line on stdout would
  corrupt it.
* **No secrets in logs (AC4).** :func:`redact_processor` scrubs the event dict before it is
  rendered -- masking values under secret-looking keys and any password embedded in a
  connection URI. It reuses the same primitives as the ``memrelay config`` redactor (#119)
  so both surfaces mask identically.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

import structlog

if TYPE_CHECKING:
    from typing import TextIO

# ─── Secret redaction (shared with ``memrelay config``, #119) ──────────────────────

#: Placeholder shown in place of any redacted secret.
_REDACTED = "***redacted***"

#: Keys whose *value* is always a secret and must never be logged. Matched case-insensitively
#: as a substring, so ``db_password`` / ``authToken`` / ``x-api-key`` are all caught.
_SECRET_KEY_RE = re.compile(
    r"pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key|authorization|credential",
    re.IGNORECASE,
)


def _redact_uri_credentials(uri: str) -> str:
    """Mask a password embedded inline in a connection URI (``scheme://user:***@host``).

    Only the password token is replaced; scheme, user, host, and port are not secrets and
    stay visible. A URI without an inline password is returned unchanged. The host is
    delimited by the *last* ``@``, so a ``@`` inside the password is still fully masked.
    """
    try:
        parts = urlsplit(uri)
    except ValueError:
        return uri
    if not parts.password:
        return uri
    userinfo, _, hostport = parts.netloc.rpartition("@")
    user = userinfo.partition(":")[0]
    netloc = f"{user}:{_REDACTED}@{hostport}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _is_secret_key(key: Any) -> bool:
    """True if ``key`` names a value that must be redacted."""
    return isinstance(key, str) and _SECRET_KEY_RE.search(key) is not None


def _redact_text(value: str) -> str:
    """Mask any URI-embedded credentials inside a free-text string.

    Whitespace-delimited tokens that look like a credentialled URL are run through
    :func:`_redact_uri_credentials`; everything else is left byte-for-byte intact (including
    the original whitespace). This keeps the reused, well-tested URI masker authoritative
    (incl. the ``@``-in-password edge case) rather than re-deriving credential parsing.
    """
    if "://" not in value:
        return value
    tokens = re.split(r"(\s+)", value)
    changed = False
    for i, token in enumerate(tokens):
        if "://" in token and "@" in token:
            masked = _redact_uri_credentials(token)
            if masked != token:
                tokens[i] = masked
                changed = True
    return "".join(tokens) if changed else value


def _scrub(value: Any) -> Any:
    """Recursively redact secrets in a log value (secret keys, URIs, nested structures)."""
    if isinstance(value, dict):
        return {
            key: (_REDACTED if _is_secret_key(key) else _scrub(val)) for key, val in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(_scrub(item) for item in value)
    if isinstance(value, str):
        return _redact_text(value)
    return value


def redact_processor(_logger: Any, _method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: scrub secrets from the event dict before it is rendered.

    Applied to *both* native structlog records and (via ``foreign_pre_chain``) stdlib
    ``logging`` records, so every emitted line -- regardless of which logger produced it --
    is redacted. A value under a secret-looking key becomes ``_REDACTED``; every string value
    (including the ``event`` message and a formatted ``exception`` traceback) has any
    URI-embedded password masked; nested dicts / lists recurse.
    """
    for key in list(event_dict):
        if _is_secret_key(key):
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _scrub(event_dict[key])
    return event_dict


# ─── Configuration ─────────────────────────────────────────────────────────────────


def _resolve_level(level: str | int | None) -> int:
    """Coerce a config level to a stdlib level number, defaulting to INFO on anything odd.

    Startup must never crash on a bad ``[logging] level``: an unknown string simply falls
    back to INFO instead of raising.
    """
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(str(level or "INFO").upper())
    return resolved if isinstance(resolved, int) else logging.INFO


def _shared_processors() -> list[Any]:
    """Processor chain shared by native structlog records and bridged stdlib records.

    ``redact_processor`` runs last so it sees the fully-assembled event dict (message,
    positional args, and any formatted exception) and can scrub every string value.
    """
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.format_exc_info,
        redact_processor,
    ]


def configure_logging(level: str | int | None = "INFO", stream: TextIO | None = None) -> None:
    """Install structlog-over-stdlib structured logging on the root logger (idempotent).

    All output is JSON written to ``stream`` (default: ``sys.stderr`` -- never stdout, which
    is the MCP protocol channel). Existing stdlib ``getLogger`` call sites are bridged
    through the same processor chain -- including redaction -- via
    :class:`structlog.stdlib.ProcessorFormatter`, so they need no change. Safe to call more
    than once: it replaces the root handler and level each time.
    """
    shared = _shared_processors()
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )
    handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(_resolve_level(level))
