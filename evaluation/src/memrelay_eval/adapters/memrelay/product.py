"""Shipped-process daemon and MCP boundary for the product treatment stratum."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from memrelay.daemon.transport import Endpoint, resolve_endpoint
from memrelay.mcp.client import DaemonClient
from memrelay_eval.adapters.memrelay.controls import (
    FrameworkPreflightEvidence,
    ProductToolContract,
    ProductToolVisibilityEvidence,
    product_tool_contract,
    require_product_tool_visibility,
    verify_framework_preflight,
)
from memrelay_eval.adapters.process.environment import ProcessRole
from memrelay_eval.adapters.process.launcher import (
    DisposableProcessLauncher,
    LaunchedProcess,
    ProcessLaunchRequest,
    ProcessRunReport,
)
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ArtifactRef, ProductIdentityChain
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.domain.ports import ArtifactStorePort, TreatmentPort


class _HealthClient(Protocol):
    async def health(self) -> Mapping[str, object]: ...


HealthClientFactory = Callable[[Path], _HealthClient]
MCPToolSurfaceProbe = Callable[[], Awaitable[Sequence[str]]]


def shipped_observation_path(home_path: Path) -> Path:
    """Return the daemon-owned canonical observation artifact location."""

    return home_path / "spool" / "spool.db"


@dataclass(frozen=True, slots=True)
class MCPToolCallEvidence:
    """Value-free evidence from an explicit, non-agent conformance probe."""

    tool_name: str
    arguments: Mapping[str, object]
    is_error: bool
    result_kind: str

    def to_document(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "is_error": self.is_error,
            "result_kind": self.result_kind,
        }


@dataclass(frozen=True, slots=True)
class ProductStateEvidence:
    """Evidence emitted after the external daemon has become ready."""

    identity_chain: ProductIdentityChain
    preflight: FrameworkPreflightEvidence
    tool_visibility: ProductToolVisibilityEvidence
    daemon_health: Mapping[str, object]
    tool_calls: tuple[MCPToolCallEvidence, ...]
    endpoint: str
    observation_path: str
    observation_artifact_exists: bool
    process_pid: int

    def to_document(self) -> dict[str, object]:
        return {
            "identity_chain": self.identity_chain.to_record(),
            "preflight": self.preflight.to_document(),
            "tool_visibility": self.tool_visibility.to_document(),
            "daemon_health": dict(self.daemon_health),
            "tool_calls": [call.to_document() for call in self.tool_calls],
            "endpoint": self.endpoint,
            "observation_path": self.observation_path,
            "observation_artifact_exists": self.observation_artifact_exists,
            "process_pid": self.process_pid,
        }


@dataclass(frozen=True, slots=True)
class ProductCleanupEvidence:
    """Process-tree cleanup evidence for a product treatment attempt."""

    identity_chain: ProductIdentityChain
    endpoint: str
    observation_path: str
    process_report: ProcessRunReport

    def to_document(self) -> dict[str, object]:
        return {
            "identity_chain": self.identity_chain.to_record(),
            "endpoint": self.endpoint,
            "observation_path": self.observation_path,
            "process": {
                "pid": self.process_report.start.pid,
                "outcome": self.process_report.exit.outcome,
                "returncode": self.process_report.exit.returncode,
                "process_tree_stopped": self.process_report.cleanup.process_tree_stopped,
                "socket_paths_removed": self.process_report.cleanup.socket_paths_removed,
                "errors": list(self.process_report.cleanup.errors),
            },
        }


@dataclass(frozen=True, slots=True)
class ProductTreatmentPaths:
    """Resolved, attempt-owned paths used only by the shipped daemon."""

    home_path: Path
    workspace_root: Path
    endpoint: Endpoint
    observation_path: Path


@dataclass(frozen=True, slots=True)
class AgentMCPServerConfiguration:
    """The only MCP command/environment supplied to the live task agent."""

    command: tuple[str, ...]
    environment: Mapping[str, str]
    tool_contract: ProductToolContract


@dataclass(slots=True)
class ProductProvisionRequest:
    """Inputs required to launch one foreground ``memrelay _serve`` process."""

    attempt_id: str
    home_path: Path
    workspace_root: Path
    namespace: str
    repo: str | None = None
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
    config_path: Path | None = None
    daemon_command: tuple[str, ...] | None = None
    readiness_timeout_seconds: float = 30.0
    mcp_tool_surface_probe: MCPToolSurfaceProbe | None = None
    self_probe_conformance: bool = False

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
    """A supervised foreground daemon process, never an in-process server."""

    request: ProductProvisionRequest
    identity_chain: ProductIdentityChain
    preflight: FrameworkPreflightEvidence
    product_contract: ProductToolContract
    paths: ProductTreatmentPaths
    daemon_process: LaunchedProcess
    daemon_client: _HealthClient
    agent_mcp: AgentMCPServerConfiguration
    launcher: DisposableProcessLauncher
    artifact_store: ArtifactStorePort | None
    evidence_refs: list[ArtifactRef] = field(default_factory=list)
    closed: bool = False


class MemrelayProductTreatment(TreatmentPort):
    """Own the shipped daemon process tree and expose only its MCP contract."""

    def __init__(
        self,
        *,
        artifact_store: ArtifactStorePort | None = None,
        launcher: DisposableProcessLauncher | None = None,
        health_client_factory: HealthClientFactory | None = None,
    ) -> None:
        self._artifact_store = artifact_store
        self._launcher = launcher or DisposableProcessLauncher()
        self._health_client_factory = health_client_factory or DaemonClient.for_home

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
        if not preflight.is_ready:
            raise ConformancePauseError(
                "product_preflight_not_ready",
                "product process may not start before preflight is ready",
            )
        paths = spec.resolved_paths()
        if not spec.attempt_id or paths.home_path == paths.workspace_root:
            raise ConformancePauseError(
                "product_attempt_isolation_invalid",
                "product attempt requires distinct isolated roots",
            )
        config_path = spec.config_path or paths.home_path / "config.toml"
        if not config_path.is_file():
            raise ConformancePauseError(
                "product_pinned_config_missing",
                "shipped daemon requires an attempt-owned pinned configuration file",
            )
        environment = dict(spec.daemon_environment)
        environment["MEMRELAY_HOME"] = str(paths.home_path)
        environment["MEMRELAY_CONFIG"] = str(config_path)
        mcp_environment = dict(spec.mcp_environment)
        mcp_environment["MEMRELAY_HOME"] = str(paths.home_path)
        mcp_environment["MEMRELAY_CONFIG"] = str(config_path)
        command = spec.daemon_command or (sys.executable, "-m", "memrelay", "_serve")
        launched = self._launcher.start(
            ProcessLaunchRequest(
                attempt_id=spec.attempt_id,
                role=ProcessRole.MEMRELAY_DAEMON,
                command=command,
                cwd=paths.workspace_root,
                environment=environment,
                timeout_seconds=spec.readiness_timeout_seconds,
                socket_paths=(
                    paths.endpoint.port_path
                    if paths.endpoint.use_loopback
                    else paths.endpoint.socket_path,
                ),
            )
        )
        client = self._health_client_factory(paths.home_path)
        try:
            await self._await_live_health(launched, client, spec.readiness_timeout_seconds)
        except BaseException:
            self._launcher.cancel(launched)
            raise
        return ProductTreatmentHandle(
            request=spec,
            identity_chain=preflight.identity_chain,
            preflight=preflight,
            product_contract=product_tool_contract(),
            paths=paths,
            daemon_process=launched,
            daemon_client=client,
            agent_mcp=AgentMCPServerConfiguration(
                command=(sys.executable, "-m", "memrelay", "mcp"),
                environment=mcp_environment,
                tool_contract=product_tool_contract(),
            ),
            launcher=self._launcher,
            artifact_store=self._artifact_store,
        )

    async def restore_history(self, handle: object, history: object) -> None:
        del history
        self._require_handle(handle, "product_restore_handle_invalid")

    async def collect_state(self, handle: object) -> Sequence[ArtifactRef]:
        product = self._require_handle(handle, "product_collect_handle_invalid")
        health = await product.daemon_client.health()
        self._require_live_health(health)
        if not product.paths.observation_path.is_file():
            raise ConformancePauseError(
                "product_observation_artifact_missing",
                "the daemon did not create its canonical observation artifact",
            )
        tool_names: Sequence[str] = ()
        if product.request.mcp_tool_surface_probe is not None:
            tool_names = await product.request.mcp_tool_surface_probe()
        else:
            # The frozen product contract is injected into the live agent; an explicit
            # conformance probe is required before claiming observed tool evidence.
            tool_names = product.product_contract.tool_names
        visibility = require_product_tool_visibility(tool_names)
        evidence = ProductStateEvidence(
            identity_chain=product.identity_chain,
            preflight=product.preflight,
            tool_visibility=visibility,
            daemon_health=health,
            tool_calls=(),
            endpoint=product.paths.endpoint.describe(),
            observation_path=str(product.paths.observation_path),
            observation_artifact_exists=True,
            process_pid=product.daemon_process.start.pid,
        )
        reference = self._persist(product, evidence.to_document(), "product_state")
        product.evidence_refs.append(reference)
        return (reference,)

    async def close(self, handle: object) -> None:
        product = self._require_handle(handle, "product_close_handle_invalid")
        if product.closed:
            return
        report = product.launcher.cancel(product.daemon_process)
        evidence = ProductCleanupEvidence(
            identity_chain=product.identity_chain,
            endpoint=product.paths.endpoint.describe(),
            observation_path=str(product.paths.observation_path),
            process_report=report,
        )
        reference = self._persist(product, evidence.to_document(), "product_cleanup")
        product.evidence_refs.append(reference)
        product.closed = True

    async def _await_live_health(
        self,
        launched: LaunchedProcess,
        client: _HealthClient,
        timeout_seconds: float,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_error: Exception | None = None
        while asyncio.get_running_loop().time() < deadline:
            if launched.process.poll() is not None:
                raise ConformancePauseError(
                    "product_daemon_crashed", "shipped foreground daemon exited before readiness"
                )
            try:
                health = await client.health()
                self._require_live_health(health)
                return
            except (ConnectionError, OSError, TimeoutError, ConformancePauseError) as error:
                last_error = error
                await asyncio.sleep(0.05)
        raise ConformancePauseError(
            "product_daemon_readiness_timeout",
            "shipped foreground daemon did not pass LiveHealthBackend readiness",
        ) from last_error

    @staticmethod
    def _require_live_health(health: Mapping[str, object]) -> None:
        if health.get("status") != "running" or not {
            "sessions_observed",
            "active_sessions",
            "episodes_ingested",
            "spool_pending",
            "notes_failed",
            "poison_skipped",
        }.issubset(health):
            raise ConformancePauseError(
                "product_live_health_invalid",
                "daemon health is not the shipped LiveHealthBackend projection",
            )

    @staticmethod
    def _require_handle(handle: object, code: str) -> ProductTreatmentHandle:
        if not isinstance(handle, ProductTreatmentHandle):
            raise ConformancePauseError(code, "product treatment requires a ProductTreatmentHandle")
        return handle

    def _persist(
        self,
        handle: ProductTreatmentHandle,
        document: Mapping[str, object],
        kind: str,
    ) -> ArtifactRef:
        del kind
        payload = canonical_bytes(document)
        if handle.artifact_store is None:
            return ArtifactRef.from_bytes(payload)
        return handle.artifact_store.put_bytes(
            payload,
            media_type="application/json",
            classification="unpaid_conformance",
        )
