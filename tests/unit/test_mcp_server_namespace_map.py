"""Round-trip tests for the config namespace map on the RECALL/note path (issue #106).

#39 wired the config ``[namespaces.*]`` map into the CAPTURE/observe path
(``run_observe(namespace_map=cfg.namespaces.repo_map) -> resolve_context``). #106 closes
the symmetric RECALL/note side: :func:`~memrelay.mcp.server.build_mcp_server` now binds
that same map into the tools' context resolver, and :func:`~memrelay.mcp.server.run_stdio`
threads ``cfg.namespaces.repo_map`` down. Without it an opt-in repo aliased via
``[namespaces.*]`` was *ingested* under its configured namespace but *recalled/noted*
under the derived git-owner namespace — a split-brain the opt-in user could never recall
across.

These tests are hermetic: they monkeypatch ``namespace.current_repo`` (as #39's
``tests/unit/test_namespace_resolver.py`` does) and drive the *real* FastMCP tools via
``call_tool`` against a recording fake client, so no daemon/socket/git is touched. They
prove the tools resolve the SAME namespace the observe path writes (a wiring assertion,
not just the pure resolver), that the zero-config path is byte-identical, and that
``run_stdio`` threads the config map while keeping ``config`` as a test injection seam.
"""

from __future__ import annotations

import asyncio
from typing import Any

from memrelay.config import Config, _namespaces_from_dict
from memrelay.mcp import namespace, server
from memrelay.mcp.server import build_mcp_server

#: A remote URL's ``owner/name`` is verbatim (often mixed-case); the #41 config keys are
#: lowercased. Using a mixed-case repo exercises the normalization contract end to end.
MIXED_CASE_REPO = "Dfinson/MemRelay"


class RecordingClient:
    """A duck-typed :class:`~memrelay.mcp.client.DaemonClient` that records call args.

    ``build_mcp_server`` only calls ``search``/``detail``/``note`` on its client, so a
    recording stand-in lets us assert exactly which namespace (and repo) the tools
    forwarded to the daemon, with no transport. The returned shapes are the minimal
    valid daemon responses that ``format_as_map`` / ``format_detail`` accept.
    """

    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str, str | None]] = []
        self.detail_calls: list[tuple[str, str]] = []
        self.note_calls: list[tuple[str, str, str | None]] = []

    async def search(
        self, query: str, namespace: str, prefer_repo: str | None = None
    ) -> dict[str, Any]:
        self.search_calls.append((query, namespace, prefer_repo))
        return {"nodes": [], "edges": [], "scores": []}

    async def detail(self, node_uuid: str, namespace: str) -> dict[str, Any]:
        self.detail_calls.append((node_uuid, namespace))
        return {"node": {}}

    async def note(self, content: str, namespace: str, repo: str | None = None) -> str:
        self.note_calls.append((content, namespace, repo))
        return "ok"


def _config_with_alias(alias_namespace: str, repo: str) -> Config:
    """A ``Config`` whose ``[namespaces.*]`` aliases ``repo`` to ``alias_namespace``.

    Routes the declaration through the *real* section parser so key normalization
    (strip+lower, #41) is exercised rather than a hand-built pre-lowercased dict.
    """
    return Config(namespaces=_namespaces_from_dict({alias_namespace: {"repos": [repo]}}))


# --- the fix: recall/note resolve the config-mapped namespace (round-trip) -----------


def test_recall_and_note_resolve_config_mapped_namespace(monkeypatch) -> None:
    """A ``[namespaces.*]``-aliased (mixed-case) repo recalls/notes under its namespace.

    This is the core #106 round-trip: build the tools with the config map, and the same
    mixed-case repo the observe path writes under resolves to the SAME namespace on the
    recall/note side. ``memory_note`` additionally carries the repo id *verbatim*.
    """
    cfg = _config_with_alias("acme", MIXED_CASE_REPO)
    monkeypatch.setattr(namespace, "current_repo", lambda cwd=None: MIXED_CASE_REPO)

    client = RecordingClient()
    mcp = build_mcp_server(client, namespace_map=cfg.namespaces.repo_map)

    asyncio.run(mcp.call_tool("memory_recall", {"query": "auth system"}))
    asyncio.run(mcp.call_tool("memory_note", {"content": "remember me"}))

    # recall + note both forwarded the config-mapped namespace. Recall now also carries the
    # resolved current repo as its prefer_repo tiebreaker default (#57); note carries it verbatim.
    assert client.search_calls == [("auth system", "acme", MIXED_CASE_REPO)]
    assert client.note_calls == [("remember me", "acme", MIXED_CASE_REPO)]

    # Symmetry: that is exactly what the observe/capture path resolves for this repo,
    # so a repo is now recalled under the same namespace it is ingested under.
    observe_namespace, observe_repo = namespace.resolve_context(
        namespace_map=cfg.namespaces.repo_map
    )
    assert observe_namespace == "acme"
    assert observe_repo == MIXED_CASE_REPO
    assert client.search_calls[0][1] == observe_namespace


def test_memory_detail_resolves_config_mapped_namespace(monkeypatch) -> None:
    """``memory_detail`` is map-aware too (all three tools share the bound resolver)."""
    cfg = _config_with_alias("acme", MIXED_CASE_REPO)
    monkeypatch.setattr(namespace, "current_repo", lambda cwd=None: MIXED_CASE_REPO)

    client = RecordingClient()
    mcp = build_mcp_server(client, namespace_map=cfg.namespaces.repo_map)

    asyncio.run(mcp.call_tool("memory_detail", {"node_uuid": "xyz-1"}))

    assert client.detail_calls == [("xyz-1", "acme")]


# --- zero-config: byte-identical owner derivation on both sides ----------------------


def test_zero_config_owner_namespace_is_byte_identical(monkeypatch) -> None:
    """Empty map -> owner namespace on the recall side, identical to the observe side.

    Zero-config users have no ``[namespaces.*]`` section (``repo_map == {}``), so recall
    must derive the git-owner namespace exactly as before #106 — and exactly as the
    observe path does with an empty (or absent) map.
    """
    cfg = Config()  # no [namespaces.*] -> repo_map == {}
    assert cfg.namespaces.repo_map == {}
    monkeypatch.setattr(namespace, "current_repo", lambda cwd=None: MIXED_CASE_REPO)

    client = RecordingClient()
    mcp = build_mcp_server(client, namespace_map=cfg.namespaces.repo_map)

    asyncio.run(mcp.call_tool("memory_recall", {"query": "q"}))

    # Owner half of owner/name, unchanged from the pre-#106 zero-config behavior.
    assert client.search_calls[0][1] == "Dfinson"
    # Byte-identical to the bare resolver (no map) and to an empty-map observe resolve.
    assert namespace.resolve_context()[0] == "Dfinson"
    assert namespace.resolve_context(namespace_map={})[0] == "Dfinson"


def test_default_build_without_map_matches_empty_map(monkeypatch) -> None:
    """Omitting ``namespace_map`` entirely (the old signature) still derives the owner.

    Guards backward compatibility: callers on the pre-#106 ``build_mcp_server(client)``
    shape get the same owner-namespace behavior as an explicit empty map.
    """
    monkeypatch.setattr(namespace, "current_repo", lambda cwd=None: MIXED_CASE_REPO)

    client = RecordingClient()
    mcp = build_mcp_server(client)  # no namespace_map kwarg at all

    asyncio.run(mcp.call_tool("memory_recall", {"query": "q"}))

    assert client.search_calls[0][1] == "Dfinson"


# --- an aliased map must not capture repos that are not declared ---------------------


def test_unaliased_repo_falls_through_to_owner_with_map_present(monkeypatch) -> None:
    """A repo absent from a non-empty map still resolves to its own owner namespace."""
    cfg = _config_with_alias("acme", "dfinson/memrelay")
    monkeypatch.setattr(namespace, "current_repo", lambda cwd=None: "other/Thing")

    client = RecordingClient()
    mcp = build_mcp_server(client, namespace_map=cfg.namespaces.repo_map)

    asyncio.run(mcp.call_tool("memory_recall", {"query": "q"}))

    assert client.search_calls[0][1] == "other"


# --- precedence: an explicit injected resolver still wins (existing test seam) -------


def test_explicit_context_resolver_takes_precedence_over_map(monkeypatch) -> None:
    """An injected ``context_resolver`` bypasses the map (keeps existing tests valid)."""
    cfg = _config_with_alias("acme", "dfinson/memrelay")
    monkeypatch.setattr(namespace, "current_repo", lambda cwd=None: "dfinson/memrelay")

    client = RecordingClient()
    mcp = build_mcp_server(
        client,
        namespace_map=cfg.namespaces.repo_map,
        context_resolver=lambda: ("injected", "x/y"),
    )

    asyncio.run(mcp.call_tool("memory_recall", {"query": "q"}))

    assert client.search_calls[0][1] == "injected"


# --- run_stdio: threads the config map, config is the injection seam -----------------


def _stub_client(monkeypatch) -> None:
    """Neuter ``DaemonClient.for_home`` so run_stdio touches no endpoint/socket."""
    monkeypatch.setattr(
        server.DaemonClient, "for_home", staticmethod(lambda home, **kwargs: object())
    )


def test_run_stdio_threads_config_repo_map_into_build_mcp_server(monkeypatch, tmp_path) -> None:
    """``run_stdio(config=cfg)`` uses the injected cfg and threads its map — no load."""
    cfg = Config(
        home=str(tmp_path),
        namespaces=_namespaces_from_dict({"acme": {"repos": ["dfinson/memrelay"]}}),
    )
    _stub_client(monkeypatch)

    def _no_load(*args, **kwargs):
        raise AssertionError("load_config must not run when a config is injected")

    monkeypatch.setattr(server, "load_config", _no_load)

    captured: dict[str, Any] = {}

    class _FakeServer:
        def run(self, transport: str) -> None:
            captured["transport"] = transport

    def _fake_build(client, *, namespace_map=None, context_resolver=None):
        captured["namespace_map"] = namespace_map
        return _FakeServer()

    monkeypatch.setattr(server, "build_mcp_server", _fake_build)

    server.run_stdio(config=cfg)

    assert captured["namespace_map"] == cfg.namespaces.repo_map
    assert captured["namespace_map"] == {"dfinson/memrelay": "acme"}
    assert captured["transport"] == "stdio"


def test_run_stdio_loads_config_when_none_and_threads_its_map(monkeypatch, tmp_path) -> None:
    """No injected config -> ``run_stdio`` loads it and threads its map (cli.py path).

    ``memrelay mcp`` calls ``run_stdio()`` with no argument; this proves that zero-arg
    path keeps working *and* now carries the config map — so cli.py needs no change.
    """
    cfg = Config(
        home=str(tmp_path),
        namespaces=_namespaces_from_dict({"team": {"repos": ["dfinson/memrelay"]}}),
    )
    _stub_client(monkeypatch)

    load_calls: list[bool] = []

    def _fake_load() -> Config:
        load_calls.append(True)
        return cfg

    monkeypatch.setattr(server, "load_config", _fake_load)

    captured: dict[str, Any] = {}

    class _FakeServer:
        def run(self, transport: str) -> None:
            captured["transport"] = transport

    def _fake_build(client, *, namespace_map=None, context_resolver=None):
        captured["namespace_map"] = namespace_map
        return _FakeServer()

    monkeypatch.setattr(server, "build_mcp_server", _fake_build)

    server.run_stdio()

    assert load_calls == [True]
    assert captured["namespace_map"] == {"dfinson/memrelay": "team"}
    assert captured["transport"] == "stdio"
