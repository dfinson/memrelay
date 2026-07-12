"""Unit tests for ``init``'s FTS-extension prefetch (#93).

On a cold first run the daemon fetches Ladybug's FTS extension over the network *before* it
serves health, so ``start``'s fixed readiness window can elapse mid-download and report a
spurious failure. ``init`` must warm that per-user cache — while staying idempotent and never
failing setup if the prefetch can't happen — so the first ``start`` is offline for FTS.

These tests stay fully offline: the real download (``_download``) and the ladybug version
probe (``_ladybug_version``) are faked, so no network and no ``ladybug`` import. The autouse
``stub_fts_prefetch`` fixture (see ``conftest.py``) neutralizes the seam for every other unit
test; here we restore the *real* seam (via ``real_fts_prefetch``) so ``init`` exercises the
genuine prefetch path end-to-end.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from memrelay import cli
from memrelay.cli import main
from memrelay.config import Config
from memrelay.engine.backends import _fts_extension as fx


def _fake_version(monkeypatch, version: str = "9.9.9") -> None:
    """Pin the ladybug version so the prefetch never imports the native library."""
    monkeypatch.setattr(fx, "_ladybug_version", lambda: version)


def test_init_invokes_fts_prefetch_once(cli_env, stub_fts_prefetch) -> None:
    """``init`` calls the FTS prefetch seam exactly once with the resolved config."""
    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert len(stub_fts_prefetch) == 1
    assert isinstance(stub_fts_prefetch[0], Config)


def test_init_warms_fts_cache_when_absent(cli_env, real_fts_prefetch, monkeypatch) -> None:
    """A first run downloads the extension into the per-user cache and announces it."""
    monkeypatch.setattr(cli, "_prefetch_fts_extension", real_fts_prefetch)
    ext_dir = cli_env[0] / "extensions"
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(ext_dir))
    monkeypatch.setattr(fx, "_ladybug_platform_candidates", lambda: ("linux_amd64",))
    _fake_version(monkeypatch)

    downloads: list[str] = []

    def fake_download(url: str, dst: Path) -> None:
        downloads.append(url)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b"fake-extension")

    monkeypatch.setattr(fx, "_download", fake_download)

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert len(downloads) == 1, "the extension must be fetched once when absent"
    cached = ext_dir / "ladybug-9.9.9" / "linux_amd64" / fx._EXTENSION_FILENAME
    assert cached.is_file(), "prefetch must warm the per-user extension cache"
    lowered = result.output.lower()
    assert "prefetching" in lowered
    assert "ready" in lowered


def test_init_skips_fts_download_when_cached(cli_env, real_fts_prefetch, monkeypatch) -> None:
    """Re-run with the extension cached: no re-download, stays offline."""
    monkeypatch.setattr(cli, "_prefetch_fts_extension", real_fts_prefetch)
    ext_dir = cli_env[0] / "extensions"
    monkeypatch.setenv("MEMRELAY_EXTENSION_DIR", str(ext_dir))
    monkeypatch.setattr(fx, "_ladybug_platform_candidates", lambda: ("linux_amd64",))
    _fake_version(monkeypatch)

    cached = ext_dir / "ladybug-9.9.9" / "linux_amd64" / fx._EXTENSION_FILENAME
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"already-here")

    def must_not_download(url: str, dst: Path) -> None:
        raise AssertionError("_download must not run when the extension is already cached")

    monkeypatch.setattr(fx, "_download", must_not_download)

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert cached.read_bytes() == b"already-here", "a cached extension must be left untouched"
    assert "ready" in result.output.lower()


def test_init_survives_fts_prefetch_failure(cli_env, real_fts_prefetch, monkeypatch) -> None:
    """A prefetch failure is non-fatal: config + MCP still land and init exits 0."""
    home, copilot = cli_env
    monkeypatch.setattr(cli, "_prefetch_fts_extension", real_fts_prefetch)

    def boom() -> None:
        raise RuntimeError("offline")

    monkeypatch.setattr(fx, "prefetch_fts_extension", boom)

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert (home / "config.toml").is_file(), "config must still be written"
    assert (copilot / "mcp-config.json").is_file(), "MCP must still be registered"
    assert "could not prefetch" in result.output.lower()


def test_init_fts_prefetch_noop_for_non_ladybug_backend(
    cli_env, real_fts_prefetch, monkeypatch
) -> None:
    """A cloud opt-in graph backend has no Ladybug FTS extension to fetch."""
    monkeypatch.setattr(cli, "_prefetch_fts_extension", real_fts_prefetch)
    monkeypatch.setenv("MEMRELAY_GRAPH__BACKEND", "falkordb")

    def must_not_prefetch() -> None:
        raise AssertionError("no FTS prefetch for a non-ladybug backend")

    monkeypatch.setattr(fx, "prefetch_fts_extension", must_not_prefetch)

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    assert "none to prefetch" in result.output.lower()


def test_init_and_engine_agree_on_mixedcase_backend(
    cli_env, real_fts_prefetch, monkeypatch
) -> None:
    """A whitespace/mixed-case Ladybug id is green-lit by ``init`` AND resolves in the engine.

    rt-backends: pre-fix ``init`` lowercased the id (no strip) while the engine
    (``resolve_backend``) took it raw, so ``" Ladybug "`` both *skipped* the FTS prefetch here
    (``.lower()`` left the spaces, so it looked like a non-ladybug backend) **and** raised
    ``KeyError`` at engine start. Both sides now route through the registry's single
    normalizer, so they agree: ``init`` prefetches Ladybug's FTS and the engine resolves the
    very same raw id to the embedded backend. This asserts that agreement across the
    doctor↔engine boundary — it fails against the old case-sensitive behavior.
    """
    from memrelay.engine.backends import resolve_backend

    monkeypatch.setattr(cli, "_prefetch_fts_extension", real_fts_prefetch)
    raw = " Ladybug "  # mixed case + surrounding whitespace
    monkeypatch.setenv("MEMRELAY_GRAPH__BACKEND", raw)

    prefetched: list[bool] = []
    monkeypatch.setattr(fx, "prefetch_fts_extension", lambda: prefetched.append(True))

    result = CliRunner().invoke(main, ["init"])
    assert result.exit_code == 0, result.output
    # doctor side: init recognizes the id as Ladybug and warms the FTS cache.
    assert prefetched == [True], result.output
    assert "none to prefetch" not in result.output.lower()
    assert "ready" in result.output.lower()
    # engine side: the SAME raw id resolves to the embedded backend (KeyError pre-fix).
    assert type(resolve_backend(raw)).__name__ == "LadybugBackend"
    assert resolve_backend(raw).id == "ladybug"
