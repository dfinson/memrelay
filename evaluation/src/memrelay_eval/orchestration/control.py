"""Pre-discovery admission control and atomic evaluator lock persistence."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import TypeVar, cast

from memrelay_eval.application.copilot_catalog import (
    CatalogArchive,
    ModelSelection,
    qualification_summary,
    select_models,
)
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import ModelQualification, QualificationCaps, QualificationUsage
from memrelay_eval.domain.errors import ConformancePauseError, CrossRepositoryDeniedError
from memrelay_eval.domain.governance import (
    AuthorizationDecision,
    AuthorizationResult,
    DenialEvidence,
    DenyByDefaultRepositoryAuthorization,
    GovernanceDenialReason,
    RepositoryAccessRequest,
)
from memrelay_eval.domain.intents import (
    ArtifactLinkIntent,
    AttemptTerminalIntent,
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    InclusionDecisionIntent,
    IntentAck,
    IntentRejection,
    LedgerIntentType,
    RetryLineageIntent,
    RunTransitionIntent,
)
from memrelay_eval.domain.ports import (
    DenialEvidencePort,
    LedgerPort,
    RepositoryAuthorizationPort,
)

_Result = TypeVar("_Result")


class InMemoryDenialEvidenceSink:
    """Deterministic local sink for conformance and CLI refusal evidence."""

    def __init__(self) -> None:
        self._records: list[DenialEvidence] = []

    def append_denial(self, evidence: DenialEvidence) -> None:
        self._records.append(evidence)

    @property
    def records(self) -> tuple[DenialEvidence, ...]:
        return tuple(self._records)


class CrossRepositoryAdmissionController:
    """Checks authority at entry and again immediately before starting work."""

    def __init__(
        self,
        *,
        authority: RepositoryAuthorizationPort | None = None,
        evidence_sink: DenialEvidencePort | None = None,
    ) -> None:
        self._deny_by_default = DenyByDefaultRepositoryAuthorization()
        self._authority = authority
        self._evidence_sink = evidence_sink or InMemoryDenialEvidenceSink()
        self._admission_lock = Lock()

    def authorize_at_entry(self, request: RepositoryAccessRequest, now: datetime) -> None:
        self._authorize(request, now)

    def start_repository_operation(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], _Result],
    ) -> _Result:
        """Atomically recheck governance before a synchronous repository operation."""

        self._authorize_local_policy(request, now)
        if self._authority is not None:
            result, operation_result = self._admit_and_start(request, now, operation)
            self._deny_if_needed(request, result)
            return cast(_Result, operation_result)
        with self._admission_lock:
            self._authorize_local_policy(request, now)
            return operation()

    async def start_repository_operation_async(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], Awaitable[_Result]],
    ) -> _Result:
        """Atomically recheck governance before an asynchronous repository operation."""

        self._authorize_local_policy(request, now)
        if self._authority is not None:
            result, operation_result = await self._admit_and_start_async(request, now, operation)
            self._deny_if_needed(request, result)
            return cast(_Result, operation_result)
        return await operation()

    def _authorize(self, request: RepositoryAccessRequest, now: datetime) -> None:
        self._authorize_local_policy(request, now)
        if self._authority is not None:
            self._deny_if_needed(request, self._authority_result(request, now))

    def _authorize_local_policy(self, request: RepositoryAccessRequest, now: datetime) -> None:
        self._deny_if_needed(request, self._deny_by_default.authorize(request, now))

    def _authority_result(
        self, request: RepositoryAccessRequest, now: datetime
    ) -> AuthorizationResult:
        if self._authority is None:
            return self._invalid_authority_result(request)
        authorize = getattr(self._authority, "authorize", None)
        if not callable(authorize):
            return self._invalid_authority_result(request)
        result = authorize(request, now)
        return self._validated_authority_result(request, result)

    def _admit_and_start(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], _Result],
    ) -> tuple[AuthorizationResult, _Result | None]:
        if self._authority is None:
            return self._invalid_authority_result(request), None
        admit_and_start = getattr(self._authority, "admit_and_start", None)
        if not callable(admit_and_start):
            return self._invalid_authority_result(request), None
        admission = admit_and_start(request, now, operation)
        if not isinstance(admission, tuple) or len(admission) != 2:
            return self._invalid_authority_result(request), None
        result, operation_result = admission
        return self._validated_authority_result(request, result), operation_result

    async def _admit_and_start_async(
        self,
        request: RepositoryAccessRequest,
        now: datetime,
        operation: Callable[[], Awaitable[_Result]],
    ) -> tuple[AuthorizationResult, _Result | None]:
        if self._authority is None:
            return self._invalid_authority_result(request), None
        admit_and_start_async = getattr(self._authority, "admit_and_start_async", None)
        if not callable(admit_and_start_async):
            return self._invalid_authority_result(request), None
        admission = await admit_and_start_async(request, now, operation)
        if not isinstance(admission, tuple) or len(admission) != 2:
            return self._invalid_authority_result(request), None
        result, operation_result = admission
        return self._validated_authority_result(request, result), operation_result

    def _validated_authority_result(
        self, request: RepositoryAccessRequest, result: object
    ) -> AuthorizationResult:
        if (
            not isinstance(result, AuthorizationResult)
            or result.policy_version != request.policy_version
        ):
            return self._invalid_authority_result(request)
        return result

    def _invalid_authority_result(self, request: RepositoryAccessRequest) -> AuthorizationResult:
        return AuthorizationResult(
            AuthorizationDecision.DENIED,
            request.policy_version,
            GovernanceDenialReason.AUTHORIZATION_NOT_CURRENT,
        )

    def _deny_if_needed(
        self, request: RepositoryAccessRequest, result: AuthorizationResult
    ) -> None:
        if result.decision is AuthorizationDecision.DENIED:
            self._deny(request, result)

    def _deny(self, request: RepositoryAccessRequest, result: AuthorizationResult) -> None:
        evidence = DenialEvidence.from_result(request, result)
        self._evidence_sink.append_denial(evidence)
        raise CrossRepositoryDeniedError(result.reason)
from memrelay_eval.domain.intents import (
    ArtifactLinkIntent,
    AttemptTerminalIntent,
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    InclusionDecisionIntent,
    IntentAck,
    IntentRejection,
    LedgerIntentType,
    RetryLineageIntent,
    RunTransitionIntent,
)
from memrelay_eval.domain.intents import (
    ArtifactLinkIntent,
    AttemptTerminalIntent,
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    InclusionDecisionIntent,
    IntentAck,
    IntentRejection,
    LedgerIntentType,
    RetryLineageIntent,
    RunTransitionIntent,
)
from memrelay_eval.domain.ports import LedgerPort


class LockRepository:
    """Stores complete immutable lock documents without replacing a valid predecessor."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def read(self, name: str) -> dict[str, object] | None:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConformancePauseError("lock_invalid_json", f"{name} is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ConformancePauseError("lock_shape_invalid", f"{name} must contain a JSON object")
        return value

    def read_bytes(self, name: str) -> bytes | None:
        path = self._path(name)
        return path.read_bytes() if path.exists() else None

    def write(self, name: str, document: Mapping[str, object]) -> Path:
        _assert_redacted(document)
        payload = canonical_bytes(document)
        self._root.mkdir(parents=True, exist_ok=True)
        destination = self._path(name)
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=self._root, prefix=f".{name}.", suffix=".tmp"
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def write_catalog_artifact(self, artifact_name: str, data: bytes) -> str:
        """Archive catalog bytes under their content digest without overwrite semantics."""

        digest = sha256(data).hexdigest()
        destination = self._root / f"{artifact_name}-{digest}.json"
        self._root.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != data:
                raise ConformancePauseError(
                    "catalog_artifact_conflict", "catalog artifact path does not match its digest"
                )
            return digest
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, dir=self._root, prefix=f".{artifact_name}.", suffix=".tmp"
        ) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        try:
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return digest

    def _path(self, name: str) -> Path:
        if name not in {"runtime-lock.json", "model-lock.json"}:
            raise ValueError("unsupported lock document")
        return self._root / name


def lock_digest(document: Mapping[str, object]) -> str:
    """Stable lock linkage digest; lock documents contain no credentials or raw identity."""

    payload = canonical_bytes(document)
    return sha256(payload).hexdigest()


def write_model_lock(
    repository: LockRepository,
    runtime_lock: Mapping[str, object],
    archive: CatalogArchive,
    caps: QualificationCaps,
    qualifications: tuple[ModelQualification, ...],
    consumption: QualificationUsage,
) -> tuple[dict[str, object], ModelSelection]:
    """Archive a complete catalog and atomically replace only a complete model lock."""

    runtime_lock_sha256 = runtime_lock.get("lock_sha256")
    if not isinstance(runtime_lock_sha256, str):
        raise ConformancePauseError(
            "runtime_lock_invalid", "model lock requires a hashed runtime lock"
        )
    expected_sessions = len(qualifications) * 8
    if caps.session_limit != expected_sessions or consumption.sessions != expected_sessions:
        raise ConformancePauseError(
            "qualification_session_accounting_invalid",
            "model lock requires exactly eight consumed sessions per eligible model",
        )
    if (
        consumption.credits > caps.credit_limit
        or consumption.tokens > caps.token_limit
        or consumption.active_seconds > caps.active_seconds_limit
        or consumption.wall_seconds > caps.wall_seconds_limit
    ):
        raise ConformancePauseError(
            "qualification_consumption_exceeded", "qualification consumption exceeds its frozen cap"
        )
    selection = select_models(archive.catalog, qualifications)
    raw_reference = repository.write_catalog_artifact("native-catalog", archive.raw_bytes)
    projection_reference = repository.write_catalog_artifact(
        "native-catalog-projection", archive.projection_bytes
    )
    selected_models = [
        _locked_model_document(selection.m0),
        *([] if selection.m1 is None else [_locked_model_document(selection.m1)]),
        *([] if selection.m2 is None else [_locked_model_document(selection.m2)]),
        *[_locked_model_document(model) for model in selection.judges],
    ]
    document: dict[str, object] = {
        "schema_version": "1.0.0",
        "runtime_lock_sha256": runtime_lock_sha256,
        "catalog_raw_ref": f"sha256:{raw_reference}",
        "catalog_raw_sha256": archive.catalog.raw_sha256,
        "catalog_projection_ref": f"sha256:{projection_reference}",
        "catalog_projection_sha256": archive.catalog.projection_sha256,
        "eligible_model_count": len(qualifications),
        "qualification_protocol": "copilot-native-eight-task-nonstudy/1.0.0",
        "qualification_caps": {
            "sessions": caps.session_limit,
            "credits": caps.credit_limit,
            "tokens": caps.token_limit,
            "active_seconds": caps.active_seconds_limit,
            "wall_seconds": caps.wall_seconds_limit,
        },
        "qualification_consumption": {
            "sessions": consumption.sessions,
            "credits": consumption.credits,
            "tokens": consumption.tokens,
            "active_seconds": consumption.active_seconds,
            "wall_seconds": consumption.wall_seconds,
        },
        "qualification_evidence": qualification_summary(qualifications),
        "selected_models": selected_models,
        "omissions": dict(selection.omissions),
    }
    document["lock_sha256"] = lock_digest(document)
    repository.write("model-lock.json", document)
    return document, selection


def reuse_or_reject_model_lock(
    repository: LockRepository,
    runtime_lock: Mapping[str, object],
    *,
    credit_limit: float,
    token_limit: int,
    active_seconds_limit: float,
    wall_seconds_limit: float,
) -> dict[str, object] | None:
    """Reuse an identical immutable model lock before any provider interaction."""

    existing = repository.read("model-lock.json")
    if existing is None:
        return None
    _verify_lock_digest(existing)
    runtime_lock_sha256 = runtime_lock.get("lock_sha256")
    if existing.get("runtime_lock_sha256") != runtime_lock_sha256:
        raise ConformancePauseError(
            "model_lock_runtime_conflict",
            "existing model lock is linked to a different runtime lock",
        )
    caps = existing.get("qualification_caps")
    if not isinstance(caps, Mapping):
        raise ConformancePauseError(
            "model_lock_invalid", "existing model lock lacks qualification caps"
        )
    requested = {
        "credits": credit_limit,
        "tokens": token_limit,
        "active_seconds": active_seconds_limit,
        "wall_seconds": wall_seconds_limit,
    }
    if any(caps.get(key) != value for key, value in requested.items()):
        raise ConformancePauseError(
            "model_lock_request_conflict",
            "requested qualification caps differ from the immutable model lock",
        )
    return existing


def _locked_model_document(model: object) -> dict[str, object]:
    return {
        "role": model.role,
        "native_id": model.native_id,
        "family": model.family,
        "capabilities": dict(model.capabilities),
        "reasoning_effort": model.reasoning_effort,
        "context_tier": model.context_tier,
    }


def _verify_lock_digest(document: Mapping[str, object]) -> None:
    recorded = document.get("lock_sha256")
    if not isinstance(recorded, str):
        raise ConformancePauseError("model_lock_invalid", "model lock has no digest")
    content = dict(document)
    content.pop("lock_sha256")
    if lock_digest(content) != recorded:
        raise ConformancePauseError(
            "model_lock_integrity_failure", "model lock digest does not verify"
        )


def _assert_redacted(value: object, path: str = "") -> None:
    sensitive = {
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "password",
        "secret",
        "credential",
        "prompt",
        "repository",
        "repo",
    }
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in sensitive:
                raise ConformancePauseError(
                    "lock_secret_field", f"lock document contains prohibited field at {path or key}"
                )
            _assert_redacted(item, f"{path}.{key}" if path else str(key))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_redacted(item, f"{path}[{index}]")
    elif isinstance(value, str) and any(term in value.lower() for term in ("ghp_", "github_pat_")):
        raise ConformancePauseError(
            "lock_secret_value", f"lock document contains credential-like data at {path}"
        )
class LedgerControl:
    """The only orchestration component allowed to submit worker lifecycle intents."""

    def __init__(self, ledger: LedgerPort) -> None:
        self._ledger = ledger

    def emit(self, intent: LedgerIntentType) -> IntentAck | IntentRejection:
        """Receive an attempt-scoped worker intent without granting control authority."""

        if isinstance(intent, (CreateExperimentIntent, CreateRunIntent)):
            return self._ledger.reject_intent(intent, "identity_creation_control_only")
        if isinstance(intent, (CreateAttemptIntent, RetryLineageIntent)):
            return self._ledger.reject_intent(intent, "attempt_creation_control_only")
        if isinstance(intent, InclusionDecisionIntent):
            return self._ledger.reject_intent(intent, "inclusion_control_only")
        if not isinstance(intent, (RunTransitionIntent, AttemptTerminalIntent, ArtifactLinkIntent)):
            return self._ledger.reject_intent(intent, "unsupported_worker_intent")
        if intent.metadata.source_attempt_id is None:
            return self._ledger.reject_intent(intent, "missing_worker_source_attempt")
        if (
            isinstance(intent, AttemptTerminalIntent)
            and intent.metadata.source_attempt_id != intent.attempt_id
        ):
            return self._ledger.reject_intent(intent, "worker_attempt_scope_mismatch")
        if isinstance(intent, ArtifactLinkIntent) and intent.link.run_id is None:
            return self._ledger.reject_intent(intent, "worker_artifact_requires_run")
        if (
            isinstance(intent, ArtifactLinkIntent)
            and intent.link.attempt_id is not None
            and intent.metadata.source_attempt_id != intent.link.attempt_id
        ):
            return self._ledger.reject_intent(intent, "worker_attempt_scope_mismatch")
        return self._ledger.submit_intent(intent)

    def handle(self, intent: LedgerIntentType) -> IntentAck | IntentRejection:
        """Backward-compatible name for the worker sink receiver."""

        return self.emit(intent)

    def create_initial_attempt(self, intent: CreateAttemptIntent) -> IntentAck | IntentRejection:
        """Create the sole initial attempt after the control process has provisioned a run."""

        return self._ledger.submit_intent(intent)

    def create_experiment(self, intent: CreateExperimentIntent) -> IntentAck | IntentRejection:
        """Record an experiment identity under control-process authority."""

        return self._ledger.submit_intent(intent)

    def create_run(self, intent: CreateRunIntent) -> IntentAck | IntentRejection:
        """Record a run identity under control-process authority."""

        return self._ledger.submit_intent(intent)

    def record_artifact(self, intent: ArtifactLinkIntent) -> IntentAck | IntentRejection:
        """Record a control-owned experiment or run artifact link."""

        return self._ledger.submit_intent(intent)

    def authorize_retry(self, intent: RetryLineageIntent) -> IntentAck | IntentRejection:
        """Create the sole successor only after the ledger verifies retry preconditions."""

        return self._ledger.submit_intent(intent)

    def record_inclusion(self, intent: InclusionDecisionIntent) -> IntentAck | IntentRejection:
        """Record a reconciled inclusion decision under control-process authority."""

        return self._ledger.submit_intent(intent)
