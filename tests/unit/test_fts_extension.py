"""Unit tests for the Ladybug FTS extension provisioning (#76).

These are deterministic and **native-free**: they never import ``ladybug`` and
never touch the network (``_download`` / ``_ensure_extension_file`` are patched),
so they run in every CI matrix job. They lock in the behaviour that keeps the
OOTB Linux guarantee working around Ladybug's native downloader TLS bug.
"""

from __future__ import annotations

import asyncio
import logging
import os
import urllib.error
from pathlib import Path

import pytest

from memrelay.engine.backends import _fts_extension as fx


class _RecordingDriver:
    """Minimal async driver stand-in that records the queries it is asked to run."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.queries: list[str] = []
        self._fail_on = fail_on

    async def execute_query(self, query: str, **_: object) -> None:
        self.queries.append(query)
        if self._fail_on is not None and query.startswith(self._fail_on):
            raise RuntimeError("simulated execute failure")


def _make_fake_download(reachable: set[str], recorder: list[str] | None = None):
    """Return a fake ``_download`` that 404s any version not in ``reachable``.

    ``reachable`` holds the version strings whose CDN artifact "exists"; a request
    for any other version raises an HTTP 404, exactly like a yanked artifact. This
    is the single seam that keeps the fallback tests offline — no socket is opened.
    """

    def fake_download(url: str, dst: Path) -> None:
        if recorder is not None:
            recorder.append(url)
        # URL shape: {host}/v{version}/{plat}/fts/{filename}
        version = url.split("/v", 1)[1].split("/", 1)[0]
        if version not in reachable:
            raise urllib.error.HTTPError(url, 404, "Not Found", hdrs={}, fp=None)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(f"ext-{version}".encode())

    return fake_download


@pytest.mark.parametrize(
    ("sys_platform", "machine", "expected"),
    [
        ("linux", "x86_64", ("linux_amd64", "linux_old_amd64")),
        ("linux", "aarch64", ("linux_arm64", "linux_old_arm64")),
        ("win32", "AMD64", ("win_amd64",)),
        ("win32", "ARM64", ()),  # no published Windows/arm64 FTS build
        ("darwin", "arm64", ("osx_arm64",)),
        ("darwin", "x86_64", ("osx_amd64",)),
        ("linux", "riscv64", ()),  # unknown arch → native installer only
    ],
)
def test_platform_candidates(monkeypatch, sys_platform, machine, expected):
    monkeypatch.setattr(fx.sys, "platform", sys_platform)
    monkeypatch.setattr(fx.platform, "machine", lambda: machine)
    assert fx._ladybug_platform_candidates() == expected


def test_configure_ssl_cert_env_sets_certifi_when_unset(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)

    fx._configure_ssl_cert_env()

    import certifi

    assert os.environ["SSL_CERT_FILE"] == certifi.where()


def test_configure_ssl_cert_env_respects_existing_file(monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", "/custom/ca.pem")

    fx._configure_ssl_cert_env()

    assert os.environ["SSL_CERT_FILE"] == "/custom/ca.pem"


def test_configure_ssl_cert_env_respects_cert_dir(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.setenv("SSL_CERT_DIR", "/etc/ssl/certs")

    fx._configure_ssl_cert_env()

    assert "SSL_CERT_FILE" not in os.environ


def test_cache_dir_honours_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(tmp_path))
    assert fx._cache_dir() == tmp_path


def test_ensure_extension_file_downloads_once_then_caches(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(tmp_path))
    monkeypatch.setattr(fx, "_ladybug_version", lambda: "9.9.9")
    downloads: list[str] = []

    def fake_download(url: str, dst: Path) -> None:
        downloads.append(url)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"fake-extension")

    monkeypatch.setattr(fx, "_download", fake_download)

    first = fx._ensure_extension_file("linux_amd64")
    assert first is not None
    assert first.read_bytes() == b"fake-extension"
    assert downloads == [
        "https://extension.ladybugdb.com/v9.9.9/linux_amd64/fts/libfts.lbug_extension"
    ]

    # Second call is a cache hit — no second download.
    second = fx._ensure_extension_file("linux_amd64")
    assert second == first
    assert len(downloads) == 1


def test_ensure_extension_file_returns_none_on_download_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(tmp_path))
    monkeypatch.setattr(fx, "_ladybug_version", lambda: "9.9.9")

    def boom(url: str, dst: Path) -> None:
        raise RuntimeError("no network")

    monkeypatch.setattr(fx, "_download", boom)

    assert fx._ensure_extension_file("linux_amd64") is None


def test_load_uses_prefetched_extension(monkeypatch):
    monkeypatch.setattr(fx, "_configure_ssl_cert_env", lambda: None)
    monkeypatch.setattr(
        fx, "_ladybug_platform_candidates", lambda: ("linux_amd64", "linux_old_amd64")
    )
    monkeypatch.setattr(
        fx, "_ensure_extension_file", lambda plat: Path(f"/cache/{plat}/libfts.lbug_extension")
    )

    driver = _RecordingDriver()
    asyncio.run(fx.load_ladybug_fts_extension(driver))

    # First candidate loads cleanly → no second tag, no native fallback.
    assert driver.queries == ["LOAD EXTENSION '/cache/linux_amd64/libfts.lbug_extension'"]


def test_load_falls_back_to_native_when_no_prefetch(monkeypatch):
    monkeypatch.setattr(fx, "_configure_ssl_cert_env", lambda: None)
    monkeypatch.setattr(fx, "_ladybug_platform_candidates", lambda: ("linux_amd64",))
    monkeypatch.setattr(fx, "_ensure_extension_file", lambda plat: None)

    driver = _RecordingDriver()
    asyncio.run(fx.load_ladybug_fts_extension(driver))

    assert driver.queries == ["INSTALL FTS;", "LOAD FTS;"]


def test_load_tries_next_tag_then_native_on_load_error(monkeypatch):
    monkeypatch.setattr(fx, "_configure_ssl_cert_env", lambda: None)
    monkeypatch.setattr(
        fx, "_ladybug_platform_candidates", lambda: ("linux_amd64", "linux_old_amd64")
    )
    monkeypatch.setattr(fx, "_ensure_extension_file", lambda plat: Path(f"/c/{plat}.ext"))

    driver = _RecordingDriver(fail_on="LOAD EXTENSION")
    asyncio.run(fx.load_ladybug_fts_extension(driver))

    assert driver.queries == [
        "LOAD EXTENSION '/c/linux_amd64.ext'",
        "LOAD EXTENSION '/c/linux_old_amd64.ext'",
        "INSTALL FTS;",
        "LOAD FTS;",
    ]


def test_prefetch_warms_all_candidates(monkeypatch, tmp_path):
    """``prefetch_fts_extension`` warms *every* candidate tag (#93).

    At prefetch time there is no driver to test which ABI will ``LOAD`` at runtime, so on
    Linux both the new- and old-ABI builds are cached — guaranteeing the runtime loader never
    has to reach the network for whichever tag it ends up using.
    """
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(tmp_path))
    monkeypatch.setattr(fx, "_ladybug_version", lambda: "9.9.9")
    monkeypatch.setattr(
        fx, "_ladybug_platform_candidates", lambda: ("linux_amd64", "linux_old_amd64")
    )
    downloads: list[str] = []

    def fake_download(url: str, dst: Path) -> None:
        downloads.append(url)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"fake-extension")

    monkeypatch.setattr(fx, "_download", fake_download)

    fx.prefetch_fts_extension()

    assert downloads == [
        "https://extension.ladybugdb.com/v9.9.9/linux_amd64/fts/libfts.lbug_extension",
        "https://extension.ladybugdb.com/v9.9.9/linux_old_amd64/fts/libfts.lbug_extension",
    ]
    for plat in ("linux_amd64", "linux_old_amd64"):
        assert (tmp_path / "ladybug-9.9.9" / plat / fx._EXTENSION_FILENAME).is_file()


def test_prefetch_is_idempotent_when_cached(monkeypatch, tmp_path):
    """A cached extension is not re-downloaded (keeps a re-run fast and offline)."""
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(tmp_path))
    monkeypatch.setattr(fx, "_ladybug_version", lambda: "9.9.9")
    monkeypatch.setattr(fx, "_ladybug_platform_candidates", lambda: ("linux_amd64",))

    cached = tmp_path / "ladybug-9.9.9" / "linux_amd64" / fx._EXTENSION_FILENAME
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"cached")

    def must_not_download(url: str, dst: Path) -> None:
        raise AssertionError("a cached extension must not be re-downloaded")

    monkeypatch.setattr(fx, "_download", must_not_download)

    fx.prefetch_fts_extension()

    assert cached.read_bytes() == b"cached"


def test_prefetch_swallows_download_failure(monkeypatch, tmp_path):
    """A download failure never propagates (the runtime keeps its native fallback)."""
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(tmp_path))
    monkeypatch.setattr(fx, "_ladybug_version", lambda: "9.9.9")
    monkeypatch.setattr(fx, "_ladybug_platform_candidates", lambda: ("linux_amd64",))

    def boom(url: str, dst: Path) -> None:
        raise RuntimeError("no network")

    monkeypatch.setattr(fx, "_download", boom)

    fx.prefetch_fts_extension()  # must not raise


def test_prefetch_swallows_ensure_failure(monkeypatch):
    """Even an error *inside* ``_ensure_extension_file`` (e.g. no ladybug) never breaks init."""
    monkeypatch.setattr(fx, "_ladybug_platform_candidates", lambda: ("linux_amd64",))

    def boom(plat: str) -> None:
        raise RuntimeError("ladybug import failed")

    monkeypatch.setattr(fx, "_ensure_extension_file", boom)

    fx.prefetch_fts_extension()  # must swallow and not raise


def test_prefetch_noop_when_no_published_build(monkeypatch):
    """No candidate tags (e.g. Windows/arm64) → nothing to warm, no error."""
    monkeypatch.setattr(fx, "_ladybug_platform_candidates", lambda: ())
    calls: list[str] = []
    monkeypatch.setattr(fx, "_ensure_extension_file", lambda plat: calls.append(plat))

    fx.prefetch_fts_extension()

    assert calls == []


# --- #118: resilience to a yanked upstream artifact (version fallback) ----------


def test_candidate_versions_orders_installed_first_and_dedupes(monkeypatch):
    """Installed version leads; known-good fallbacks follow; no version probed twice."""
    monkeypatch.setattr(fx, "_FALLBACK_VERSIONS", ("0.18.0", "0.17.5"))

    # Installed distinct from every fallback → it leads, fallbacks follow in order.
    assert fx._candidate_versions("0.18.1") == ("0.18.1", "0.18.0", "0.17.5")
    # Installed coincides with a fallback entry → deduped, still leads.
    assert fx._candidate_versions("0.18.0") == ("0.18.0", "0.17.5")


def test_ensure_extension_file_prefers_installed_and_skips_fallback_probe(monkeypatch, tmp_path):
    """Happy path is byte-identical: reachable installed version, fallback never touched."""
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(tmp_path))
    monkeypatch.setattr(fx, "_ladybug_version", lambda: "0.18.1")
    monkeypatch.setattr(fx, "_FALLBACK_VERSIONS", ("0.18.0",))
    requested: list[str] = []
    monkeypatch.setattr(fx, "_download", _make_fake_download({"0.18.1", "0.18.0"}, requested))

    path = fx._ensure_extension_file("linux_amd64")

    assert path == tmp_path / "ladybug-0.18.1" / "linux_amd64" / fx._EXTENSION_FILENAME
    assert path.read_bytes() == b"ext-0.18.1"
    # Only the installed version is fetched — the fallback URL is never requested.
    assert requested == [
        "https://extension.ladybugdb.com/v0.18.1/linux_amd64/fts/libfts.lbug_extension"
    ]


def test_ensure_extension_file_falls_back_to_reachable_version(monkeypatch, tmp_path, caplog):
    """Installed 404 → newest reachable fallback 200 → the fallback binary is used."""
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(tmp_path))
    monkeypatch.setattr(fx, "_ladybug_version", lambda: "0.18.1")
    monkeypatch.setattr(fx, "_FALLBACK_VERSIONS", ("0.18.0",))
    requested: list[str] = []
    monkeypatch.setattr(fx, "_download", _make_fake_download({"0.18.0"}, requested))

    with caplog.at_level(logging.WARNING, logger="memrelay.engine.backends._fts_extension"):
        path = fx._ensure_extension_file("linux_amd64")

    assert path == tmp_path / "ladybug-0.18.0" / "linux_amd64" / fx._EXTENSION_FILENAME
    assert path.read_bytes() == b"ext-0.18.0"
    # Installed version probed first (404), then the fallback (200).
    assert requested == [
        "https://extension.ladybugdb.com/v0.18.1/linux_amd64/fts/libfts.lbug_extension",
        "https://extension.ladybugdb.com/v0.18.0/linux_amd64/fts/libfts.lbug_extension",
    ]
    # An actionable warning names requested vs. loaded version and the host.
    assert "0.18.1" in caplog.text
    assert "0.18.0" in caplog.text
    assert fx._EXTENSION_HOST in caplog.text


def test_ensure_extension_file_all_candidates_fail_is_clean(monkeypatch, tmp_path, caplog):
    """Every candidate 404 → ``None`` and one clean, actionable warning (no traceback)."""
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(tmp_path))
    monkeypatch.setattr(fx, "_ladybug_version", lambda: "0.18.1")
    monkeypatch.setattr(fx, "_FALLBACK_VERSIONS", ("0.18.0",))
    monkeypatch.setattr(fx, "_download", _make_fake_download(set()))  # nothing reachable

    with caplog.at_level(logging.WARNING, logger="memrelay.engine.backends._fts_extension"):
        result = fx._ensure_extension_file("linux_amd64")  # must not raise

    assert result is None
    # Exactly one summary line, naming every version tried and the host.
    summaries = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("No reachable Ladybug FTS extension")
    ]
    assert len(summaries) == 1
    summary = summaries[0]
    assert "linux_amd64" in summary
    assert "0.18.1" in summary
    assert "0.18.0" in summary
    assert fx._EXTENSION_HOST in summary


def test_load_end_to_end_falls_back_to_reachable_version(monkeypatch, tmp_path):
    """Loader path: installed yanked → the cached fallback binary is LOADed, not native."""
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(tmp_path))
    monkeypatch.setattr(fx, "_configure_ssl_cert_env", lambda: None)
    monkeypatch.setattr(fx, "_ladybug_version", lambda: "0.18.1")
    monkeypatch.setattr(fx, "_FALLBACK_VERSIONS", ("0.18.0",))
    monkeypatch.setattr(fx, "_ladybug_platform_candidates", lambda: ("linux_amd64",))
    monkeypatch.setattr(fx, "_download", _make_fake_download({"0.18.0"}))

    driver = _RecordingDriver()
    asyncio.run(fx.load_ladybug_fts_extension(driver))

    # Installed 0.18.1 is yanked, so the reachable 0.18.0 binary is LOADed — no native.
    fallback = tmp_path / "ladybug-0.18.0" / "linux_amd64" / fx._EXTENSION_FILENAME
    assert driver.queries == [f"LOAD EXTENSION '{fallback.as_posix()}'"]
