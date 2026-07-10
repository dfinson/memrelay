"""Robust, cross-platform provisioning of Ladybug's FTS extension (#76).

Ladybug ships full-text search (FTS) as a **loadable extension** that is never
statically bundled (Ladybug source ``src/extension/extension_entries.cpp``);
graphiti's Kuzu wiring triggers a download by issuing ``INSTALL FTS;`` at open
time (Delta 2). On most platforms that native download works, but the manylinux
wheel's *native* downloader fails TLS verification on Linux (GitHub Actions
Ubuntu)::

    IO exception: Failed to download extension: fts at URL
    https://extension.ladybugdb.com/v0.18.0/linux_amd64/fts/libfts.lbug_extension
    (ERROR: SSL server verification failed)

That leaves ``QUERY_FTS_INDEX`` / ``CREATE_FTS_INDEX`` undefined and silently
breaks graphiti's hybrid (RRF) search (the ``INSTALL FTS`` error is swallowed).
The wheel's statically-linked OpenSSL cannot verify the CDN's chain on Linux;
Ladybug's installer honours ``SSL_CERT_FILE``/``SSL_CERT_DIR`` first in
``getCaCertPath`` (``src/extension/extension.cpp``) but, per the vendor's own
analysis, that alone may not fix it.

So we do not depend on the native downloader. Primary path: fetch the extension
with Python's TLS (certifi CA bundle + an ordinary User-Agent — the CDN rejects
the default ``Python-urllib`` UA with HTTP 403) into a per-user cache and load it
with ``LOAD EXTENSION '<path>'`` (the LOAD binder allows an arbitrary filesystem
path; ``src/binder/bind/bind_extension.cpp``). This bypasses the flaky native TLS
entirely, is fully offline after the first fetch, and was proven equivalent to a
successful ``INSTALL FTS; LOAD FTS;`` in #76. We also export ``SSL_CERT_FILE`` and
keep the native ``INSTALL FTS`` as a fallback.

Resilience to a *yanked* upstream artifact (#118): the prefetch URL is derived
from the installed ``ladybug.__version__``, so a single missing build must not
hard-fail us. Upstream pulled v0.18.1's FTS extension from the CDN (HTTP 404)
while v0.18.0 stayed live, which reddened every note->recall test project-wide
(#114). When the installed version's artifact is unreachable we fall back, in
order, to a small fixed list of known-good published versions
(``_FALLBACK_VERSIONS``) and use the first that is reachable, warning which
version was loaded in place of the requested one. The native ``INSTALL FTS`` path
does *not* help here — it re-derives the same host and version and 404s
identically — so this Python-side version fallback is the actual cure.

The manylinux ABI decides Ladybug's platform tag — ``linux_amd64`` (new C++ ABI)
vs. ``linux_old_amd64`` (old ABI); ``getPlatform()`` in ``extension.cpp``. The
installed 0.18.0 wheel resolved to ``linux_amd64`` in CI, but we try both so a
future ABI change cannot silently break us. Only the matching build will ``LOAD``.
"""

from __future__ import annotations

import logging
import os
import platform
import ssl
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from memrelay.engine.backends._deltas import load_fts_extension_native

if TYPE_CHECKING:
    from graphiti_core.driver.driver import GraphDriver

logger = logging.getLogger(__name__)

_EXTENSION_HOST = "https://extension.ladybugdb.com"
_EXTENSION_FILENAME = "libfts.lbug_extension"
# The extension CDN rejects the default ``Python-urllib/x.y`` User-Agent with an
# HTTP 403; any ordinary UA is accepted (verified in #76).
_USER_AGENT = "memrelay-ladybug-fts/1.0 (+https://github.com/dfinson/memrelay)"
# Override of the prefetch cache location (mainly for tests and locked-down envs).
_CACHE_ENV = "MEMRELAY_EXTENSION_DIR"
# Known-good published Ladybug versions, newest first, probed only when the
# *installed* version's FTS artifact is unreachable (e.g. upstream yanked it from
# the CDN — #118, resilience follow-up to #114). Bounded on purpose: a small,
# fixed list, never an unbounded scan or algorithmic version-guessing. Extend it
# as upstream publishes new known-good builds. The installed version is always
# tried first (it is the only build guaranteed to match the loaded engine's ABI),
# so this list is a recovery path, not the primary source of truth.
_FALLBACK_VERSIONS: tuple[str, ...] = ("0.18.0",)


def _ladybug_platform_candidates() -> tuple[str, ...]:
    """Return candidate ``<os>_<arch>`` FTS extension tags for this interpreter.

    Linux publishes two ABI variants — ``linux_amd64`` (new C++ ABI) and
    ``linux_old_amd64`` (manylinux/old ABI) — and only the matching one will
    ``LOAD``; we try the new-ABI tag first (what the 0.18.0 wheel used in CI),
    then the old-ABI tag. Other OSes publish a single tag. An empty tuple means
    no published build (e.g. Windows/arm64) → caller uses the native installer.
    """
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64", "x64"):
        arch = "amd64"
    elif machine in ("aarch64", "arm64"):
        arch = "arm64"
    else:
        return ()

    if sys.platform.startswith("linux"):
        return (f"linux_{arch}", f"linux_old_{arch}")
    if sys.platform in ("win32", "cygwin"):
        return () if arch == "arm64" else (f"win_{arch}",)
    if sys.platform == "darwin":
        return (f"osx_{arch}",)
    return ()


def _configure_ssl_cert_env() -> None:
    """Point Ladybug's native downloader at a current CA bundle (belt, not braces).

    Ladybug honours ``SSL_CERT_FILE``/``SSL_CERT_DIR`` first in ``getCaCertPath``;
    setting the former to certifi's always-current bundle helps the *native*
    fallback on Linux. Only set when unset (respect user/CI overrides); no-op if
    certifi is missing. The primary prefetch path passes certifi to its own SSL
    context directly, so it does not rely on this.
    """
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
    except Exception:  # pragma: no cover - certifi is a declared dependency
        pass


def _ladybug_version() -> str:
    # Safe to import here: this module is only reached from the Ladybug backend's
    # runtime open path, where the native library is already loaded.
    import ladybug

    return ladybug.__version__


def _cache_dir() -> Path:
    override = os.environ.get(_CACHE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".memrelay" / "extensions"


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover - certifi is a declared dependency
        return ssl.create_default_context()


def _download(url: str, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    context = _ssl_context()
    fd, tmp_name = tempfile.mkstemp(dir=str(dst.parent), suffix=".part")
    tmp = Path(tmp_name)
    try:
        with (
            urllib.request.urlopen(request, context=context, timeout=60) as resp,
            os.fdopen(fd, "wb") as out,
        ):
            out.write(resp.read())
        os.replace(tmp, dst)  # atomic publish so a partial file is never LOADed
    finally:
        if tmp.exists():
            tmp.unlink()


def _candidate_versions(installed: str) -> tuple[str, ...]:
    """Return the probe order: the installed version first, then known-good fallbacks.

    Deduplicated with order preserved, so when the installed version already *is* a
    ``_FALLBACK_VERSIONS`` entry (e.g. 0.18.0) it is not probed twice. The installed
    version leads because it is the only extension build guaranteed to match the
    loaded engine's ABI; fallbacks are used only when it is unreachable.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for version in (installed, *_FALLBACK_VERSIONS):
        if version not in seen:
            seen.add(version)
            ordered.append(version)
    return tuple(ordered)


def _extension_url(version: str, plat: str) -> str:
    return f"{_EXTENSION_HOST}/v{version}/{plat}/fts/{_EXTENSION_FILENAME}"


def _cache_path(version: str, plat: str) -> Path:
    return _cache_dir() / f"ladybug-{version}" / plat / _EXTENSION_FILENAME


def _ensure_extension_file(plat: str) -> Path | None:
    """Return a local path to the ``plat`` FTS extension, downloading once.

    Probes candidate versions in order — the installed Ladybug version first, then
    the known-good ``_FALLBACK_VERSIONS`` — and returns the first that is already
    cached or downloads cleanly. This makes the prefetch resilient to a yanked
    upstream artifact (#118): the installed version's happy path is unchanged (it
    is tried first and returns immediately when reachable), and a lower, known-good
    version is used only when the installed one is missing.

    ``None`` when *every* candidate fails (missing build / network / HTTP) — after
    emitting a single clean, actionable warning naming the versions tried and the
    host, never a raw traceback — so the caller can try the next platform tag or
    the native installer.
    """
    installed = _ladybug_version()
    versions = _candidate_versions(installed)
    for version in versions:
        dst = _cache_path(version, plat)
        if dst.is_file() and dst.stat().st_size > 0:
            if version != installed:
                logger.warning(
                    "Ladybug FTS extension for installed version %s unavailable at %s; "
                    "using cached fallback version %s",
                    installed,
                    _EXTENSION_HOST,
                    version,
                )
            return dst

        url = _extension_url(version, plat)
        try:
            _download(url, dst)
        except Exception as exc:  # noqa: BLE001 - try the next candidate version
            logger.warning("Ladybug FTS extension prefetch from %s failed: %s", url, exc)
            continue

        if version != installed:
            logger.warning(
                "Ladybug FTS extension for installed version %s unavailable at %s; "
                "using reachable fallback version %s",
                installed,
                _EXTENSION_HOST,
                version,
            )
        else:
            logger.debug("Cached Ladybug FTS extension at %s", dst)
        return dst

    logger.warning(
        "No reachable Ladybug FTS extension for %s; tried version(s) %s at %s",
        plat,
        ", ".join(versions),
        _EXTENSION_HOST,
    )
    return None


def prefetch_fts_extension() -> None:
    """Warm the per-user FTS-extension cache so the first daemon start is offline (#93).

    On a cold first run the daemon's engine build fetches Ladybug's FTS extension over the
    network (the #76 TLS-workaround path) *before* it serves health, so ``memrelay start``'s
    fixed readiness window can elapse mid-download. Calling this from ``init`` — exactly as
    ``init`` prefetches the embedding model (#13) — moves that fetch to setup time, leaving
    the first ``start`` fully offline for FTS.

    Driver-free by design: only the download half (``_ensure_extension_file``) is exercised;
    the ``LOAD EXTENSION`` half needs a live driver and stays in
    :func:`load_ladybug_fts_extension`. We warm **every** candidate tag rather than stopping
    at the first success: at prefetch time there is no driver to test which ABI will actually
    ``LOAD``, so on Linux (two ABIs — ``linux_amd64`` / ``linux_old_amd64``) we cache both,
    guaranteeing the runtime loader finds a cached file for whichever tag it ends up using.

    Best-effort and **never raises**: any failure (missing build, offline, HTTP, or even a
    failure to import ``ladybug`` for its version) is logged as a warning and skipped, because
    the runtime loader retains its native ``INSTALL FTS`` fallback — a prefetch miss only
    defers the fetch to first daemon use, it must never break ``init``.
    """
    for plat in _ladybug_platform_candidates():
        try:
            _ensure_extension_file(plat)
        except Exception as exc:  # noqa: BLE001 - prefetch is best-effort; must never break init
            logger.warning("Ladybug FTS extension prefetch for %s failed: %s", plat, exc)


async def load_ladybug_fts_extension(driver: GraphDriver) -> None:
    """Load Ladybug's FTS extension, robust to its native downloader's Linux TLS bug.

    Primary: a Python-fetched, locally cached extension loaded via
    ``LOAD EXTENSION '<path>'`` — bypasses the native TLS entirely and is offline
    after the first fetch. Fallback: the native ``INSTALL FTS; LOAD FTS;`` (correct
    where TLS works, or if a future build bundles FTS statically).
    """
    _configure_ssl_cert_env()

    for plat in _ladybug_platform_candidates():
        path = _ensure_extension_file(plat)
        if path is None:
            continue
        try:
            await driver.execute_query(f"LOAD EXTENSION '{path.as_posix()}'")
            return
        except Exception as exc:  # noqa: BLE001 - try the next tag, then native
            logger.warning("LOAD of prefetched %s FTS extension failed: %s", plat, exc)

    await load_fts_extension_native(driver)
