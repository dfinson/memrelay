"""Unit tests for the Ladybug FTS extension provisioning (#76).

These are deterministic and **native-free**: they never import ``ladybug`` and
never touch the network (``_download`` / ``_ensure_extension_file`` are patched),
so they run in every CI matrix job. They lock in the behaviour that keeps the
OOTB Linux guarantee working around Ladybug's native downloader TLS bug.
"""

from __future__ import annotations

import asyncio
import os
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
