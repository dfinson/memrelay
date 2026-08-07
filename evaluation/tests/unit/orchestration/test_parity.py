from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from hashlib import sha256

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.adapters.inspect.task import (
    InspectTaskRequest,
    NativeTerminalRecord,
    SessionLimits,
)
from memrelay_eval.canonical import canonical_bytes, verify_digest
from memrelay_eval.domain.entities import (
    Attempt,
    NativeModel,
    NativeModelCatalog,
    Protocol,
    RuntimeIdentity,
)
from memrelay_eval.domain.environment import (
    AgentEnvironmentParityRecord,
    EnvironmentFingerprint,
    PromptByteHashes,
    ProtocolDeltaAllowance,
)
from memrelay_eval.domain.errors import AgentParityMismatchError, ConformancePauseError
from memrelay_eval.domain.ids import AttemptId, ProtocolId, RunId
from memrelay_eval.domain.states import AttemptTerminalKind
from memrelay_eval.orchestration.attempt import AttemptTerminalRecorder
from memrelay_eval.orchestration.inspect import InspectAttemptController
from memrelay_eval.orchestration.parity import (
    EnrollmentParityBinding,
    PairedParityAttempt,
    persist_parity_preflight,
    preflight_paired_execution,
    verify_locked_parity_record,
    verify_paired_parity,
)


def _sha(value: bytes) -> str:
    return sha256(value).hexdigest()


def _environment(*, power_mode: str = "ac") -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        os_name="Windows",
        os_build="22631",
        cpu={"architecture": "x86_64", "logical_cores": 8},
        memory={"total_bytes": 17179869184},
        storage_class="local_ssd",
        power_mode=power_mode,
        python_version="3.13.0",
        runtime_version="cpython-3.13.0",
        process_limits={"max_workers": 1, "wall_seconds": 600},
        network_policy={"mode": "deny"},
        background_load_policy={"mode": "idle_only"},
    )


def _runtime(*, runtime_version: str = "1.0.8") -> RuntimeIdentity:
    return RuntimeIdentity(
        sdk_version="1.0.8",
        wheel_filename="github_copilot_sdk-1.0.8-py3-none-any.whl",
        wheel_sha256=_sha(b"sdk-wheel"),
        runtime_version=runtime_version,
        runtime_sha256=_sha(b"runtime"),
        transport="local",
        auth_mode="copilot_subscription",
        subscription_identity_sha256=_sha(b"subscription"),
    )


def _model(*, native_id: str = "native-model") -> NativeModel:
    return NativeModel(
        native_id,
        "fixture-family",
        {
            "tools": True,
            "permissions": True,
            "context": True,
            "events": True,
            "cancellation": True,
            "sessions": True,
        },
        "high",
        "large",
    )


def _record(
    *,
    system_delta: bytes = b"system-delta",
    user_delta: bytes = b"user-delta",
    access_delta: bytes = b"access-delta",
    environment: EnvironmentFingerprint | None = None,
) -> AgentEnvironmentParityRecord:
    fingerprint = environment or _environment()
    return AgentEnvironmentParityRecord(
        runtime=_runtime(),
        runtime_lock_sha256=_sha(b"runtime-lock"),
        model_lock_sha256=_sha(b"model-lock"),
        model=_model(),
        system_prompt=PromptByteHashes.from_bytes(b"system-common", system_delta),
        user_prompt=PromptByteHashes.from_bytes(b"user-common", user_delta),
        tool_schemas={"terminal": {"input": {"type": "object"}}},
        permission_policy={"allowed": ["read", "write"]},
        network_policy={"mode": "deny"},
        limits={"active_seconds": 30, "token_limit": 100},
        timeout_seconds=30,
        workspace_layout={
            "provider": "temporary_worktree",
            "roots": ["workspace", "agent-session", "cache"],
            "private_git": True,
        },
        built_in_memory_enabled=False,
        cross_session_store_enabled=False,
        retry_policy={"inspect": 0, "sdk": 0, "memrelay": 0, "grader": 0},
        effective_configuration_digest=_sha(b"effective-config"),
        environment_fingerprint_digest=fingerprint.digest,
        environment_stratum=fingerprint.stratum,
        enrollment_parity_inputs_digest=_sha(b"enrollment-inputs"),
        access_delta_sha256=_sha(access_delta),
    )


def _binding(record: AgentEnvironmentParityRecord) -> EnrollmentParityBinding:
    return EnrollmentParityBinding(
        record.enrollment_parity_inputs_digest,
        record.effective_configuration_digest,
        record.environment_fingerprint_digest,
        _sha(b"protocol-projection"),
    )


def _allowance(record: AgentEnvironmentParityRecord) -> ProtocolDeltaAllowance:
    return ProtocolDeltaAllowance(
        _sha(b"protocol-projection"),
        record.system_prompt.allowed_delta_bytes_sha256,
        record.user_prompt.allowed_delta_bytes_sha256,
        record.access_delta_sha256,
    )


def _paired(
    left: AgentEnvironmentParityRecord, right: AgentEnvironmentParityRecord
) -> tuple[PairedParityAttempt, PairedParityAttempt]:
    return (
        PairedParityAttempt(
            Attempt(AttemptId.new(), RunId.new()),
            left,
            _allowance(left),
            _binding(left),
        ),
        PairedParityAttempt(
            Attempt(AttemptId.new(), RunId.new()),
            right,
            _allowance(right),
            _binding(right),
        ),
    )


def _lock_inputs(record: AgentEnvironmentParityRecord) -> dict[str, object]:
    catalog = NativeModelCatalog(_sha(b"raw"), _sha(b"projection"), (record.model,))
    return {
        "runtime_lock": {
            "lock_sha256": record.runtime_lock_sha256,
            "runtime": {
                "sdk_version": record.runtime.sdk_version,
                "wheel_filename": record.runtime.wheel_filename,
                "wheel_sha256": record.runtime.wheel_sha256,
                "runtime_version": record.runtime.runtime_version,
                "runtime_sha256": record.runtime.runtime_sha256,
                "transport": record.runtime.transport,
                "auth_mode": record.runtime.auth_mode,
                "subscription_identity_sha256": record.runtime.subscription_identity_sha256,
            },
        },
        "model_lock": {
            "lock_sha256": record.model_lock_sha256,
            "runtime_lock_sha256": record.runtime_lock_sha256,
            "catalog_raw_sha256": catalog.raw_sha256,
            "catalog_projection_sha256": catalog.projection_sha256,
            "selected_models": [
                {
                    "native_id": record.model.native_id,
                    "family": record.model.family,
                    "capabilities": dict(record.model.capabilities),
                    "reasoning_effort": record.model.reasoning_effort,
                    "context_tier": record.model.context_tier,
                }
            ],
        },
        "current_catalog": catalog,
    }


def test_declared_prompt_and_access_deltas_preserve_canonical_neutral_parity() -> None:
    left = _record()
    right = _record(
        system_delta=b"system-delta-other",
        user_delta=b"user-delta-other",
        access_delta=b"access-delta-other",
    )
    first, second = _paired(left, right)

    evidence = verify_paired_parity(first, second)
    document = evidence.to_document()
    raw = canonical_bytes(document)

    assert evidence.is_verified
    assert evidence.is_execution_ready is False
    assert left.neutral_digest == right.neutral_digest
    assert raw == canonical_bytes(json.loads(raw))
    assert verify_digest(document)
    assert b"system-common" not in raw
    assert b"user-common" not in raw
    assert b"control" not in raw.lower()
    assert b"treatment" not in raw.lower()

    execution_preflight = preflight_paired_execution(first, second, **_lock_inputs(left))

    assert execution_preflight.is_execution_ready is True


@pytest.mark.parametrize(
    ("field", "change"),
    (
        ("runtime", lambda record: replace(record, runtime=_runtime(runtime_version="1.0.9"))),
        ("runtime_lock_sha256", lambda record: replace(record, runtime_lock_sha256=_sha(b"other"))),
        ("model_lock_sha256", lambda record: replace(record, model_lock_sha256=_sha(b"other"))),
        ("model", lambda record: replace(record, model=_model(native_id="native-model-other"))),
        (
            "prompts",
            lambda record: replace(
                record,
                system_prompt=PromptByteHashes.from_bytes(b"system-common-other", b"system-delta"),
            ),
        ),
        (
            "tool_schemas",
            lambda record: replace(record, tool_schemas={"terminal": {"input": "other"}}),
        ),
        (
            "permission_policy",
            lambda record: replace(record, permission_policy={"allowed": ["read"]}),
        ),
        ("network_policy", lambda record: replace(record, network_policy={"mode": "allow"})),
        (
            "limits",
            lambda record: replace(record, limits={"active_seconds": 31, "token_limit": 100}),
        ),
        ("timeout_seconds", lambda record: replace(record, timeout_seconds=31)),
        (
            "workspace_layout",
            lambda record: replace(
                record,
                workspace_layout={
                    "provider": "isolated_clone",
                    "roots": ["workspace", "agent-session", "cache"],
                    "private_git": True,
                },
            ),
        ),
        ("built_in_memory_enabled", lambda record: replace(record, built_in_memory_enabled=True)),
        (
            "cross_session_store_enabled",
            lambda record: replace(record, cross_session_store_enabled=True),
        ),
        ("retry_policy", lambda record: replace(record, retry_policy={"inspect": 1})),
        (
            "effective_configuration_digest",
            lambda record: replace(record, effective_configuration_digest=_sha(b"other")),
        ),
        (
            "environment",
            lambda record: replace(
                record,
                environment_fingerprint_digest=_environment(power_mode="battery").digest,
                environment_stratum=_environment(power_mode="battery").stratum,
            ),
        ),
        (
            "enrollment_parity_inputs_digest",
            lambda record: replace(record, enrollment_parity_inputs_digest=_sha(b"other")),
        ),
    ),
)
def test_each_neutral_field_mutation_is_a_pre_exposure_mismatch(field: str, change) -> None:
    left = _record()
    right = change(_record())
    first, second = _paired(left, right)

    evidence = verify_paired_parity(first, second)

    assert evidence.is_verified is False
    assert field in evidence.mismatched_fields


def test_undeclared_delta_is_denied_without_normalizing_it() -> None:
    left = _record()
    right = replace(_record(), access_delta_sha256=_sha(b"undeclared"))
    first = PairedParityAttempt(
        Attempt(AttemptId.new(), RunId.new()),
        left,
        _allowance(left),
        _binding(left),
    )
    second = PairedParityAttempt(
        Attempt(AttemptId.new(), RunId.new()),
        right,
        _allowance(_record()),
        _binding(right),
    )

    evidence = verify_paired_parity(first, second)

    assert evidence.is_verified is False
    assert "access_delta" in evidence.mismatched_fields


def test_execution_record_must_reproduce_the_frozen_enrollment_binding() -> None:
    left = _record()
    right = _record()
    first, second = _paired(left, right)
    invalid_binding = replace(
        second.enrollment,
        effective_configuration_digest=_sha(b"revised-config"),
    )

    evidence = verify_paired_parity(first, replace(second, enrollment=invalid_binding))

    assert evidence.is_verified is False
    assert "effective_configuration_binding" in evidence.mismatched_fields


def test_parity_preflight_blocks_scheduler_and_records_pre_exposure_terminal() -> None:
    class Scheduler:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, task: InspectTaskRequest) -> NativeTerminalRecord:
            del task
            self.calls += 1
            return NativeTerminalRecord("succeeded", (), (), {})

    left = _record()
    right = replace(_record(), network_policy={"mode": "allow"})
    first, second = _paired(left, right)
    preflight = preflight_paired_execution(first, second, **_lock_inputs(left))
    scheduler = Scheduler()
    ledger = InMemoryLedger()
    controller = InspectAttemptController(
        scheduler,
        InMemoryArtifactStore(),
        AttemptTerminalRecorder(ledger, InMemoryTelemetry()),
    )
    task = InspectTaskRequest(
        "opaque-task",
        {},
        "synthetic task",
        "native-model",
        {"tools": True},
        "high",
        "large",
        ("terminal",),
        ("read",),
        SessionLimits(10, 10, 10),
    )

    with pytest.raises(AgentParityMismatchError) as raised:
        asyncio.run(
            controller.execute_after_parity(
                task,
                attempt_id=first.attempt.id,
                run_id=first.attempt.run_id,
                inspect_state="succeeded",
                eval_bytes=b"must-not-be-used",
                inspect_export={"status": "succeeded"},
                parity_preflight=preflight,
            )
        )

    terminal = ledger.attempt_terminal_for(first.attempt.id)
    assert raised.value.code == "agent_environment_parity_mismatch"
    assert scheduler.calls == 0
    assert terminal is not None
    assert terminal.classification is AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE
    assert terminal.evidence_refs
    assert ledger.retry_authorizations == ()


def test_parity_evidence_is_persisted_through_the_fake_artifact_port() -> None:
    first, second = _paired(_record(), _record())
    store = InMemoryArtifactStore()

    artifact = persist_parity_preflight(verify_paired_parity(first, second), store)
    document = json.loads(store.open_verified(artifact))

    assert verify_digest(document)
    assert document["verified"] is True


def test_runtime_and_model_locks_are_verified_without_substitution() -> None:
    record = _record()
    lock_inputs = _lock_inputs(record)

    verify_locked_parity_record(
        record,
        **lock_inputs,
    )
    with pytest.raises(ConformancePauseError, match="not linked"):
        verify_locked_parity_record(
            replace(record, model_lock_sha256=_sha(b"other")),
            **lock_inputs,
        )


def test_pre_exposure_parity_failure_does_not_authorize_an_automatic_retry() -> None:
    protocol = Protocol(ProtocolId.new(), allows_pre_exposure_infrastructure_retry=True)
    first, second = _paired(_record(), replace(_record(), timeout_seconds=31))
    evidence = verify_paired_parity(first, second)

    with pytest.raises(AgentParityMismatchError):
        evidence.require_execution_ready_for(first.attempt)

    assert protocol.allows_pre_exposure_infrastructure_retry is True
