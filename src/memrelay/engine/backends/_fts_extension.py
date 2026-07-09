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


def _ensure_extension_file(plat: str) -> Path | None:
    """Return a local path to the ``plat`` FTS extension, downloading once.

    ``None`` when the download fails (missing build / network / HTTP), so the
    caller can try the next candidate tag or the native installer.
    """
    version = _ladybug_version()
    dst = _cache_dir() / f"ladybug-{version}" / plat / _EXTENSION_FILENAME
    if dst.is_file() and dst.stat().st_size > 0:
        return dst

    url = f"{_EXTENSION_HOST}/v{version}/{plat}/fts/{_EXTENSION_FILENAME}"
    try:
        _download(url, dst)
    except Exception as exc:  # noqa: BLE001 - fall back to the next candidate/native
        logger.warning("Ladybug FTS extension prefetch from %s failed: %s", url, exc)
        return None
    logger.debug("Cached Ladybug FTS extension at %s", dst)
    return dst


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
