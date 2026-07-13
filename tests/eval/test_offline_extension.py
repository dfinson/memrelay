"""Guard: retrieval-eval is genuinely offline when its extension cache is warm (rt-release F2).

The eval builds a real embedded-Ladybug engine. On a cold cache that engine build fetches
Ladybug's FTS extension from the CDN, which contradicts the eval's "fully offline" claim and
goes red whenever the CDN is unreachable (cf. the #114/#118 yank incidents). The harness now
calls ``prefetch_fts_extension()`` up-front, and CI provisions/caches ``MEMRELAY_EXTENSION_DIR``
so the fetch is a one-time, cache-hit-thereafter operation.

These tests pin the property CI relies on **hermetically** — no network, no engine build, no
``ladybug`` import — by pre-populating the cache dir and asserting the resolver performs zero
downloads. They import ``_fts_extension`` (which lives in the engine/backends lane, outside this
session's fence) strictly read-only, only to monkeypatch its network seam in-process; the module
source is not modified.
"""

from __future__ import annotations

from pathlib import Path

import _harness
import pytest

from memrelay.engine.backends import _fts_extension as fts

_VERSION = "0.18.0"
_PLAT = "linux_amd64"


def test_pre_provisioned_dir_needs_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-populated cache dir resolves the extension with zero network I/O."""
    monkeypatch.setenv(fts._CACHE_ENV, str(tmp_path))
    monkeypatch.setattr(fts, "_ladybug_version", lambda: _VERSION)
    downloads: list[str] = []
    monkeypatch.setattr(fts, "_download", lambda url, dst: downloads.append(url))

    cached = fts._cache_path(_VERSION, _PLAT)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"dummy-fts-extension")

    result = fts._ensure_extension_file(_PLAT)
    assert result == cached
    assert result.read_bytes() == b"dummy-fts-extension"
    assert downloads == [], "resolved a pre-provisioned extension without any network"


def test_prefetch_is_offline_when_cache_warm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``prefetch_fts_extension`` — the exact call the harness makes — hits no CDN when warm."""
    monkeypatch.setenv(fts._CACHE_ENV, str(tmp_path))
    monkeypatch.setattr(fts, "_ladybug_version", lambda: _VERSION)
    monkeypatch.setattr(fts, "_ladybug_platform_candidates", lambda: (_PLAT,))
    downloads: list[str] = []
    monkeypatch.setattr(fts, "_download", lambda url, dst: downloads.append(url))

    cached = fts._cache_path(_VERSION, _PLAT)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(b"dummy-fts-extension")

    fts.prefetch_fts_extension()
    assert downloads == [], "prefetch hit the network despite a warm cache"


def test_run_eval_prefetches_before_building_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """``run_eval`` warms the extension cache before it ever builds the engine.

    Stubs out the engine build (``measure`` + ``asyncio.run``) so this stays hermetic while
    still exercising the real wiring: the prefetch spy must fire during ``run_eval``.
    """
    calls: list[bool] = []
    monkeypatch.setattr(_harness, "prefetch_fts_extension", lambda: calls.append(True))
    monkeypatch.setattr(_harness, "measure", lambda home, corpus, ks: "coro-sentinel")
    monkeypatch.setattr(_harness.asyncio, "run", lambda coro: {"p@1": 1.0})

    report = _harness.run_eval(n_topics=1, facts_per_topic=1)
    assert calls, "run_eval must prefetch the FTS extension before building the engine"
    assert report["metrics"] == {"p@1": 1.0}
