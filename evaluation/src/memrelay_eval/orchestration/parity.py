"""Pre-exposure paired-arm parity evidence and execution gating."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from memrelay_eval.canonical import attach_digest, canonical_bytes
from memrelay_eval.domain.entities import (
    ArtifactRef,
    Attempt,
    EffectiveConfigurationArtifact,
    EnrollmentPlan,
)
from memrelay_eval.domain.environment import (
    AgentEnvironmentParityRecord,
    EnvironmentFingerprint,
    ProtocolDeltaAllowance,
    verify_agent_environment_parity,
)
from memrelay_eval.domain.errors import AgentParityMismatchError, FrozenInputMutationError
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.orchestration.stages import verify_stage_locks

PARITY_EVIDENCE_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class EnrollmentParityBinding:
    """The immutable enrollment values that execution parity must reproduce."""

    enrollment_parity_inputs_digest: str
    effective_configuration_digest: str
    environment_fingerprint_digest: str
    protocol_projection_sha256: str


@dataclass(frozen=True, slots=True)
class PairedParityAttempt:
    """One opaque attempt's parity record and its protocol-declared delta allowance."""

    attempt: Attempt
    record: AgentEnvironmentParityRecord
    allowance: ProtocolDeltaAllowance
    enrollment: EnrollmentParityBinding


@dataclass(frozen=True, slots=True)
class ParityPreflightEvidence:
    """Typed, prompt-free evidence for a paired pre-exposure parity decision."""

    attempt_ids: tuple[str, str]
    left_record_digest: str
    right_record_digest: str
    left_neutral_digest: str
    right_neutral_digest: str
    mismatched_fields: tuple[str, ...]
    runtime_model_locks_verified: bool = False

    @property
    def is_verified(self) -> bool:
        return not self.mismatched_fields

    @property
    def is_execution_ready(self) -> bool:
        return self.is_verified and self.runtime_model_locks_verified

    def to_document(self) -> dict[str, object]:
        return attach_digest(
            {
                "artifact_type": "paired_agent_environment_parity",
                "schema_version": PARITY_EVIDENCE_SCHEMA_VERSION,
                "attempt_ids": list(self.attempt_ids),
                "left_record_sha256": self.left_record_digest,
                "right_record_sha256": self.right_record_digest,
                "left_neutral_sha256": self.left_neutral_digest,
                "right_neutral_sha256": self.right_neutral_digest,
                "mismatched_fields": list(self.mismatched_fields),
                "verified": self.is_verified,
                "runtime_model_locks_verified": self.runtime_model_locks_verified,
            }
        )

    def require_execution_ready_for(self, attempt: Attempt) -> None:
        if str(attempt.id) not in self.attempt_ids:
            raise AgentParityMismatchError("agent_parity_attempt_not_bound", ("attempt_binding",))
        if not self.is_verified:
            raise AgentParityMismatchError(
                "agent_environment_parity_mismatch", self.mismatched_fields
            )
        if not self.runtime_model_locks_verified:
            raise AgentParityMismatchError(
                "agent_parity_locks_not_verified",
                ("runtime_model_lock_preflight",),
            )


def bind_enrollment_parity(
    plan: EnrollmentPlan,
    configuration: EffectiveConfigurationArtifact,
    environment: EnvironmentFingerprint,
) -> EnrollmentParityBinding:
    """Prove execution derives its parity inputs from the frozen enrollment plan."""
    configuration_input = plan.inputs.get("effective_configuration")
    environment_input = plan.inputs.get("environment_fingerprint")
    if (
        configuration_input is None
        or configuration_input.artifact != configuration.artifact
        or environment_input is None
        or environment_input.artifact.sha256
        != ArtifactRef.from_bytes(canonical_bytes(environment.to_document())).sha256
    ):
        raise FrozenInputMutationError("execution_parity_enrollment_binding")
    return EnrollmentParityBinding(
        plan.parity_inputs_digest,
        configuration.document_digest,
        environment.digest,
        plan.inputs["protocol"].artifact.sha256,
    )


def verify_paired_parity(
    left: PairedParityAttempt, right: PairedParityAttempt
) -> ParityPreflightEvidence:
    """Produce evidence for both arms before either task is delivered."""
    mismatches = list(
        verify_agent_environment_parity(
            left.record,
            left.allowance,
            right.record,
            right.allowance,
        )
    )
    for paired in (left, right):
        if (
            paired.record.enrollment_parity_inputs_digest
            != paired.enrollment.enrollment_parity_inputs_digest
        ):
            mismatches.append("enrollment_parity_inputs")
        if (
            paired.record.effective_configuration_digest
            != paired.enrollment.effective_configuration_digest
        ):
            mismatches.append("effective_configuration_binding")
        if (
            paired.record.environment_fingerprint_digest
            != paired.enrollment.environment_fingerprint_digest
        ):
            mismatches.append("environment_binding")
        if (
            paired.allowance.protocol_projection_sha256
            != paired.enrollment.protocol_projection_sha256
        ):
            mismatches.append("protocol_delta_binding")
    return ParityPreflightEvidence(
        (str(left.attempt.id), str(right.attempt.id)),
        left.record.digest,
        right.record.digest,
        left.record.neutral_digest,
        right.record.neutral_digest,
        tuple(dict.fromkeys(mismatches)),
    )


def verify_locked_parity_record(
    record: AgentEnvironmentParityRecord,
    *,
    runtime_lock: Mapping[str, object],
    model_lock: Mapping[str, object],
    current_catalog: object,
) -> None:
    """Require the official runtime and selected native model lock before pairing."""
    from memrelay_eval.domain.entities import NativeModelCatalog
    from memrelay_eval.domain.errors import ConformancePauseError

    if not isinstance(current_catalog, NativeModelCatalog):
        raise ConformancePauseError(
            "native_model_catalog_invalid", "parity preflight requires the native catalog"
        )
    if (
        runtime_lock.get("lock_sha256") != record.runtime_lock_sha256
        or model_lock.get("lock_sha256") != record.model_lock_sha256
    ):
        raise ConformancePauseError(
            "parity_lock_link_drift",
            "parity record is not linked to the active runtime/model locks",
        )
    verify_stage_locks(runtime_lock, model_lock, record.runtime, current_catalog)
    selected = model_lock.get("selected_models")
    if not isinstance(selected, list):
        raise ConformancePauseError("model_lock_invalid", "model lock has no selected models")
    model_pin = next(
        (
            pin
            for pin in selected
            if isinstance(pin, Mapping) and pin.get("native_id") == record.model.native_id
        ),
        None,
    )
    if model_pin is None:
        raise ConformancePauseError(
            "model_not_locked", "agent parity model is absent from the native model lock"
        )
    if (
        model_pin.get("capabilities") != dict(record.model.capabilities)
        or model_pin.get("reasoning_effort") != record.model.reasoning_effort
        or model_pin.get("context_tier") != record.model.context_tier
    ):
        raise ConformancePauseError(
            "model_parity_lock_drift", "agent parity model differs from its locked controls"
        )


def preflight_paired_execution(
    left: PairedParityAttempt,
    right: PairedParityAttempt,
    *,
    runtime_lock: Mapping[str, object],
    model_lock: Mapping[str, object],
    current_catalog: object,
) -> ParityPreflightEvidence:
    """Combine paired comparison with the mandatory official runtime/model lock check."""
    from memrelay_eval.domain.errors import ConformancePauseError

    evidence = verify_paired_parity(left, right)
    lock_failures: list[str] = []
    for paired in (left, right):
        try:
            verify_locked_parity_record(
                paired.record,
                runtime_lock=runtime_lock,
                model_lock=model_lock,
                current_catalog=current_catalog,
            )
        except ConformancePauseError as error:
            lock_failures.append(error.code)
    if not lock_failures:
        return replace(evidence, runtime_model_locks_verified=True)
    return replace(
        evidence,
        mismatched_fields=tuple(dict.fromkeys((*evidence.mismatched_fields, *lock_failures))),
    )


def persist_parity_preflight(
    evidence: ParityPreflightEvidence, artifact_store: ArtifactStorePort
) -> ArtifactRef:
    """Persist canonical, value-free parity evidence through the sole artifact port."""
    return artifact_store.put_bytes(
        canonical_bytes(evidence.to_document()),
        media_type="application/json",
        classification="agent_environment_parity",
    )


def require_single_preflight(
    attempts: Sequence[PairedParityAttempt],
) -> ParityPreflightEvidence:
    """Reject incomplete or non-paired input rather than selecting a favorable record."""
    if len(attempts) != 2:
        raise AgentParityMismatchError("paired_agent_parity_required", ("pair_cardinality",))
    return verify_paired_parity(attempts[0], attempts[1])
