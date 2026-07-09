"""memrelay configuration: TOML load with defaults, path resolution, env overrides.

Zero-config by design — :func:`load_config` returns a working default when no file
is present. Overrides layer in this precedence (highest wins):

1. explicit keyword overrides passed to :func:`load_config`
2. ``MEMRELAY_*`` environment variables (``__`` nests, e.g. ``MEMRELAY_LLM__STRATEGY``)
3. a config file — ``MEMRELAY_CONFIG`` path, else the first that exists of
   ``$XDG_CONFIG_HOME/memrelay/config.toml``, ``~/.config/memrelay/config.toml``,
   ``~/.memrelay/config.toml``
4. built-in defaults (below)

The env-override convention mirrors traceforge's ``TRACEFORGE_*`` scheme (prefix +
``__`` nesting + scalar coercion) so both layers feel the same to operators.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ENV_PREFIX = "MEMRELAY_"
#: Env vars that configure loading itself rather than a config field.
_META_ENV = frozenset({"MEMRELAY_CONFIG", "MEMRELAY_HOME"})


# ─── Schema ──────────────────────────────────────────────────────────────────


@dataclass
class GraphConfig:
    backend: str = "ladybug"
    path: str = "~/.memrelay/graph.db"


@dataclass
class LLMConfig:
    # borrow-host reuses the host agent's own model (zero API keys); see SPEC §6.2.
    strategy: str = "borrow-host"
    host: str = "copilot"
    # byo-key strategy only (SPEC §6.4). Left None for the key-less default so the
    # borrow-host/local paths never need them. ``api_key_env`` names the environment
    # variable holding the key (the key itself is never stored in config).
    provider: str | None = None
    api_key_env: str | None = None
    model: str | None = None


@dataclass
class EmbeddingsConfig:
    provider: str = "local"
    model: str = "BAAI/bge-small-en-v1.5"
    # byo-key embeddings only (e.g. provider="openai", model="text-embedding-3-small").
    api_key_env: str | None = None


@dataclass
class IngestConfig:
    """Observation/normalization pipeline knobs (SPEC §3.3, delta #7).

    traceforge's ``EventPipeline`` defaults both ML inferencers ``True`` (they
    lazy-load packaged ONNX bundles). memrelay defaults them **off** for a lean,
    deterministic, offline transport pipeline; later epics flip them on once their
    inputs are guaranteed present.
    """

    enable_phase: bool = False
    enable_boundary: bool = False


@dataclass
class Config:
    """Fully resolved memrelay configuration."""

    home: str = "~/.memrelay"
    graph: GraphConfig = field(default_factory=GraphConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    embeddings: EmbeddingsConfig = field(default_factory=EmbeddingsConfig)
    ingest: IngestConfig = field(default_factory=IngestConfig)

    @property
    def home_path(self) -> Path:
        """The resolved ``~/.memrelay`` data directory (``~`` / env expanded)."""
        return _expand(self.home)

    @property
    def graph_path(self) -> Path:
        """Absolute path to the embedded graph database file."""
        return _expand(self.graph.path)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ─── Path resolution ─────────────────────────────────────────────────────────


_VAR_PATTERN = re.compile(r"\$(\w+)|\$\{([^}]*)\}|%([^%]*)%")


def _expanduser_with(value: str, env: Mapping[str, str]) -> str:
    """Expand a leading ``~`` using *env* rather than the real process environment."""
    if not value.startswith("~"):
        return value
    if len(value) > 1 and value[1] not in ("/", "\\"):
        return value  # ``~user`` form is unsupported — leave it untouched
    home = env.get("HOME") or env.get("USERPROFILE")
    if not home:
        drive, tail = env.get("HOMEDRIVE", ""), env.get("HOMEPATH")
        home = drive + tail if tail else None
    if not home:
        return value  # nothing to expand against — keep the literal ``~``
    return home + value[1:]


def _expandvars_with(value: str, env: Mapping[str, str]) -> str:
    """Expand ``$VAR`` / ``${VAR}`` / ``%VAR%`` using *env*, leaving unknowns intact."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2) or match.group(3)
        return env.get(name, match.group(0))

    return _VAR_PATTERN.sub(_sub, value)


def _expand(value: str, environ: Mapping[str, str] | None = None) -> Path:
    """Expand ``~`` and ``$VAR`` / ``${VAR}`` references to an absolute path.

    When *environ* is provided, expansion honors that mapping instead of the real
    process environment, so :func:`load_config` with an injected ``environ`` is
    genuinely isolated from the caller's real home directory and ``MEMRELAY_*`` /
    ``XDG_*`` variables (this is what makes the config default tests hermetic).
    """
    if environ is None:
        expanded = os.path.expandvars(os.path.expanduser(value))
    else:
        expanded = _expandvars_with(_expanduser_with(value, environ), environ)
    return Path(expanded).resolve()


def default_home(environ: dict[str, str] | None = None) -> Path:
    """Resolve the memrelay data directory (``~/.memrelay`` by default).

    Honors ``MEMRELAY_HOME`` and ``XDG_DATA_HOME`` (→ ``$XDG_DATA_HOME/memrelay``).
    """
    env = os.environ if environ is None else environ
    if env.get("MEMRELAY_HOME"):
        return _expand(env["MEMRELAY_HOME"], environ)
    if env.get("XDG_DATA_HOME"):
        return _expand(os.path.join(env["XDG_DATA_HOME"], "memrelay"), environ)
    return _expand("~/.memrelay", environ)


def candidate_config_paths(environ: dict[str, str] | None = None) -> list[Path]:
    """Config file locations in precedence order (first existing one wins).

    An explicit ``MEMRELAY_CONFIG`` short-circuits discovery.
    """
    env = os.environ if environ is None else environ
    if env.get("MEMRELAY_CONFIG"):
        return [_expand(env["MEMRELAY_CONFIG"], environ)]

    paths: list[Path] = []
    if env.get("XDG_CONFIG_HOME"):
        xdg = os.path.join(env["XDG_CONFIG_HOME"], "memrelay", "config.toml")
        paths.append(_expand(xdg, environ))
    paths.append(_expand("~/.config/memrelay/config.toml", environ))
    paths.append(_expand("~/.memrelay/config.toml", environ))
    return paths


def resolve_config_path(environ: dict[str, str] | None = None) -> Path | None:
    """Return the first existing config file, or ``None`` (→ pure defaults)."""
    for path in candidate_config_paths(environ):
        if path.is_file():
            return path
    return None


# ─── Merge / env / coercion helpers ──────────────────────────────────────────


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (dicts merge, scalars replace)."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _coerce(value: str) -> Any:
    """Best-effort coercion of an env-var string to a Python scalar."""
    low = value.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def env_overrides(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Build a nested override dict from ``MEMRELAY_*`` env vars.

    ``MEMRELAY_LLM__STRATEGY=byo-key`` → ``{"llm": {"strategy": "byo-key"}}``.
    Meta vars (``MEMRELAY_CONFIG``, ``MEMRELAY_HOME``) are excluded.
    """
    env = os.environ if environ is None else environ
    result: dict[str, Any] = {}
    for key, value in env.items():
        if not key.startswith(ENV_PREFIX) or key in _META_ENV:
            continue
        parts = key[len(ENV_PREFIX) :].lower().split("__")
        target = result
        for part in parts[:-1]:
            node = target.get(part)
            if not isinstance(node, dict):
                node = {}
                target[part] = node
            target = node
        target[parts[-1]] = _coerce(value)
    return result


# ─── Construction ────────────────────────────────────────────────────────────


def _config_from_dict(data: dict[str, Any]) -> Config:
    """Build a :class:`Config`, ignoring unknown keys defensively."""
    graph = GraphConfig(**_known(GraphConfig, data.get("graph")))
    llm = LLMConfig(**_known(LLMConfig, data.get("llm")))
    embeddings = EmbeddingsConfig(**_known(EmbeddingsConfig, data.get("embeddings")))
    ingest = IngestConfig(**_known(IngestConfig, data.get("ingest")))
    home = data.get("home", Config.home)
    return Config(home=home, graph=graph, llm=llm, embeddings=embeddings, ingest=ingest)


def _known(cls: type, section: Any) -> dict[str, Any]:
    """Filter ``section`` to the fields declared on dataclass ``cls``."""
    if not isinstance(section, dict):
        return {}
    allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    return {k: v for k, v in section.items() if k in allowed}


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
    **overrides: Any,
) -> Config:
    """Load configuration with full precedence (see module docstring).

    Args:
        path: force a specific config file; skips discovery. ``None`` uses the
            standard search (or pure defaults when nothing is found).
        environ: environment mapping to read (defaults to ``os.environ``); passing
            an explicit dict keeps tests hermetic.
        **overrides: highest-precedence explicit section overrides, e.g.
            ``load_config(llm={"strategy": "local"})``.
    """
    if path is not None:
        file_path: Path | None = _expand(str(path))
        if not file_path.is_file():
            raise FileNotFoundError(f"config file not found: {file_path}")
    else:
        file_path = resolve_config_path(environ)

    data: dict[str, Any] = {}
    if file_path is not None:
        with open(file_path, "rb") as fh:
            data = _deep_merge(data, tomllib.load(fh))

    data = _deep_merge(data, env_overrides(environ))
    if overrides:
        data = _deep_merge(data, overrides)

    if "home" not in data:
        # Reflect MEMRELAY_HOME / XDG_DATA_HOME in the resolved home directory.
        data["home"] = str(default_home(environ))

    return _config_from_dict(data)


def ensure_home(config: Config) -> Path:
    """Create the memrelay data directory if absent and return it."""
    home = config.home_path
    home.mkdir(parents=True, exist_ok=True)
    return home
