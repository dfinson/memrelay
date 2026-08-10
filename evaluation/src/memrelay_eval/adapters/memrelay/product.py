"""Shipped memrelay daemon and MCP product-stratum harness."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from memrelay.daemon.protocol import Backend, StubBackend
from memrelay.daemon.server import DaemonServer
from memrelay.daemon.transport import Endpoint, resolve_endpoint
from memrelay.mcp.client import DaemonClient
from memrelay.mcp.server import build_mcp_server
from memrelay_eval.adapters.memrelay.controls import (
    FrameworkPreflightEvidence,
    ProductToolContract,
    ProductToolVisibilityEvidence,
    product_tool_contract,
    require_product_tool_visibility,
    verify_framework_preflight,
)
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef, ProductIdentityChain
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.domain.ports import ArtifactStorePort, TreatmentPort


def shipped_observation_path(home_path: Path) -> Path:
    """Return the daemon-owned shipped observation spool path."""

    return home_path / "spool" / "spool.db"


@dataclass(frozen=True, slots=True)
class MCPToolCallEvidence:
    """Value-free evidence for one MCP tool invocation."""

    tool_name: str
    arguments: Mapping[str, object]
    is_error: bool
    result_kind: str
    text: str

    def to_document(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "is_error": self.is_error,
            "result_kind": self.result_kind,
            "text": self.text,
        }


@dataclass(frozen=True, slots=True)
class ProductStateEvidence:
    """Evidence for the shipped daemon, MCP surface, and process boundaries."""

    identity_chain: ProductIdentityChain
    preflight: FrameworkPreflightEvidence
    tool_visibility: ProductToolVisibilityEvidence
    daemon_health: Mapping[str, object]
    tool_calls: tuple[MCPToolCallEvidence, ...]
    endpoint: str
    observation_path: str

    def to_document(self) -> dict[str, object]:
        return {
            "identity_chain": self.identity_chain.to_record(),
            "preflight": self.preflight.to_document(),
            "tool_visibility": self.tool_visibility.to_document(),
            "daemon_health": dict(self.daemon_health),
            "tool_calls": [call.to_document() for call in self.tool_calls],
            "endpoint": self.endpoint,
            "observation_path": self.observation_path,
        }


@dataclass(frozen=True, slots=True)
class ProductCleanupEvidence:
    """Cleanup evidence for a single shipped daemon and MCP run."""

    identity_chain: ProductIdentityChain
    daemon_stopped: bool
    endpoint: str
    observation_path: str
    mcp_closed: bool

    def to_document(self) -> dict[str, object]:
        return {
            "identity_chain": self.identity_chain.to_record(),
            "daemon_stopped": self.daemon_stopped,
            "endpoint": self.endpoint,
            "observation_path": self.observation_path,
            "mcp_closed": self.mcp_closed,
        }


@dataclass(frozen=True, slots=True)
class ProductTreatmentPaths:
    """Resolved home, endpoint, and observation paths for the shipped product."""

    home_path: Path
    workspace_root: Path
    endpoint: Endpoint
    observation_path: Path

    def to_document(self) -> dict[str, object]:
        return {
            "home_path": str(self.home_path),
            "workspace_root": str(self.workspace_root),
            "endpoint": self.endpoint.describe(),
            "observation_path": str(self.observation_path),
        }


@dataclass(slots=True)
class ProductProvisionRequest:
    """Inputs for the shipped daemon and MCP product-stratum harness."""

    home_path: Path
    workspace_root: Path
    namespace: str
    repo: str | None = None
    backend: Backend | None = None
    llm_strategy: str = "byo-key"
    framework_model: str = "gpt-4.1-mini-2025-04-14"
    openai_base_url: str = "https://api.openai.com/v1"
    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_digest: str | None = None
    client_name: str = "OpenAIClient"
    daemon_environment: Mapping[str, str] = field(default_factory=dict)
    agent_environment: Mapping[str, str] = field(default_factory=dict)
    mcp_environment: Mapping[str, str] = field(default_factory=dict)
    probe_query: str = "memrelay product-stratum probe"
    probe_node_uuid: str = "probe-node"
    probe_note: str = "product-stratum probe note"
    prefer_repo: str | None = None

    def context_resolver(self) -> tuple[str, str | None]:
        return self.namespace, self.repo

    def resolved_paths(self) -> ProductTreatmentPaths:
        endpoint = resolve_endpoint(self.home_path)
        return ProductTreatmentPaths(
            home_path=self.home_path,
            workspace_root=self.workspace_root,
            endpoint=endpoint,
            observation_path=shipped_observation_path(self.home_path),
        )


@dataclass(slots=True)
class ProductTreatmentHandle:
    """Live daemon plus in-process MCP server for one product-stratum run."""

    request: ProductProvisionRequest
    identity_chain: ProductIdentityChain
    preflight: FrameworkPreflightEvidence
    product_contract: ProductToolContract
    paths: ProductTreatmentPaths
    daemon_server: DaemonServer
    daemon_client: DaemonClient
    mcp_server: object
    backend: Backend
    artifact_store: ArtifactStorePort | None
    evidence_refs: list[ArtifactRef] = field(default_factory=list)
    closed: bool = False


class MemrelayProductTreatment(TreatmentPort):
    """Shipped daemon and MCP harness, with deterministic unpaid-only seams."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStorePort | None = None,
        backend_factory: Callable[[], Backend] | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._backend_factory = backend_factory or StubBackend

    async def provision(self, spec: object) -> ProductTreatmentHandle:
        if not isinstance(spec, ProductProvisionRequest):
            raise ConformancePauseError(
                "product_provision_spec_invalid",
                "product treatment requires a ProductProvisionRequest",
            )

        preflight = verify_framework_preflight(
            llm_strategy=spec.llm_strategy,
            framework_model=spec.framework_model,
            openai_base_url=spec.openai_base_url,
            daemon_environment=spec.daemon_environment,
            agent_environment=spec.agent_environment,
            mcp_environment=spec.mcp_environment,
            embedding_provider=spec.embedding_provider,
            embedding_model=spec.embedding_model,
            embedding_digest=spec.embedding_digest,
            client_name=spec.client_name,
        )
        paths = spec.resolved_paths()
        backend = spec.backend or self._backend_factory()
        daemon_server = DaemonServer(backend, paths.endpoint)
        await daemon_server.start()
        daemon_client = DaemonClient.for_home(spec.home_path)
        mcp_server = build_mcp_server(daemon_client, context_resolver=spec.context_resolver)
        identity_chain = preflight.identity_chain
        return ProductTreatmentHandle(
            request=spec,
            identity_chain=identity_chain,
            preflight=preflight,
            product_contract=product_tool_contract(),
            paths=paths,
            daemon_server=daemon_server,
            daemon_client=daemon_client,
            mcp_server=mcp_server,
            backend=backend,
            artifact_store=self._artifact_store,
        )

    async def restore_history(self, handle: object, history: object) -> None:
        del history
        if not isinstance(handle, ProductTreatmentHandle):
            raise ConformancePauseError(
                "product_restore_handle_invalid",
                "product treatment requires a ProductTreatmentHandle",
            )
        return None

    async def collect_state(self, handle: object) -> Sequence[ArtifactRef]:
        if not isinstance(handle, ProductTreatmentHandle):
            raise ConformancePauseError(
                "product_collect_handle_invalid",
                "product treatment requires a ProductTreatmentHandle",
            )
        tool_listing = await handle.mcp_server.list_tools()
        tool_names = self._tool_names(tool_listing)
        tool_visibility = require_product_tool_visibility(tool_names)
        health = await handle.daemon_client.health()
        tool_calls = await self._collect_tool_calls(handle)
        evidence = ProductStateEvidence(
            identity_chain=handle.identity_chain,
            preflight=handle.preflight,
            tool_visibility=tool_visibility,
            daemon_health=health,
            tool_calls=tool_calls,
            endpoint=handle.paths.endpoint.describe(),
            observation_path=str(handle.paths.observation_path),
        )
        ref = self._persist(handle, evidence.to_document(), "product_state")
        handle.evidence_refs.append(ref)
        return (ref,)

    async def close(self, handle: object) -> None:
        if not isinstance(handle, ProductTreatmentHandle):
            raise ConformancePauseError(
                "product_close_handle_invalid",
                "product treatment requires a ProductTreatmentHandle",
            )
        if handle.closed:
            return None
        await handle.daemon_server.stop()
        evidence = ProductCleanupEvidence(
            identity_chain=handle.identity_chain,
            daemon_stopped=True,
            endpoint=handle.paths.endpoint.describe(),
            observation_path=str(handle.paths.observation_path),
            mcp_closed=True,
        )
        ref = self._persist(handle, evidence.to_document(), "product_cleanup")
        handle.evidence_refs.append(ref)
        handle.closed = True
        return None

    async def _collect_tool_calls(
        self, handle: ProductTreatmentHandle
    ) -> tuple[MCPToolCallEvidence, ...]:
        recall_args: dict[str, object] = {"query": handle.request.probe_query}
        if handle.request.prefer_repo is not None:
            recall_args["prefer_repo"] = handle.request.prefer_repo
        recall = await handle.mcp_server.call_tool("memory_recall", recall_args)
        detail = await handle.mcp_server.call_tool(
            "memory_detail",
            {"node_uuid": handle.request.probe_node_uuid},
        )
        note = await handle.mcp_server.call_tool(
            "memory_note",
            {"content": handle.request.probe_note},
        )
        return (
            self._call_evidence("memory_recall", recall_args, recall),
            self._call_evidence(
                "memory_detail",
                {"node_uuid": handle.request.probe_node_uuid},
                detail,
            ),
            self._call_evidence("memory_note", {"content": handle.request.probe_note}, note),
        )

    def _tool_names(self, listing: object) -> tuple[str, ...]:
        tools = getattr(listing, "tools", listing)
        return tuple(tool.name for tool in tools)

    def _call_evidence(
        self, tool_name: str, arguments: Mapping[str, object], result: object
    ) -> MCPToolCallEvidence:
        blocks = result[0] if isinstance(result, tuple) else getattr(result, "content", result)
        text = "".join(getattr(block, "text", "") for block in blocks)
        is_error = bool(getattr(result, "isError", False))
        normalized = text.strip().casefold()
        if is_error:
            result_kind = "error"
        elif (
            not normalized
            or "no relevant memories found" in normalized
            or "not found" in normalized
        ):
            result_kind = "zero_result"
        else:
            result_kind = "success"
        return MCPToolCallEvidence(tool_name, dict(arguments), is_error, result_kind, text)

    def _persist(
        self,
        handle: ProductTreatmentHandle,
        document: Mapping[str, object],
        kind: str,
    ) -> object:
        payload = canonical_bytes(document)
        if self._artifact_store is None:
            return ArtifactRef.from_bytes(payload)
        return self._artifact_store.put_bytes(
            payload,
            media_type="application/json",
            classification="unpaid_conformance",
        )
