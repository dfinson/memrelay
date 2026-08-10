"""Pre-exposure paired-arm parity evidence and execution gating."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from types import MappingProxyType

from memrelay_eval.canonical import attach_digest, canonical_bytes, verify_digest
from memrelay_eval.domain.entities import (
    ArtifactRef,
    Attempt,
    EffectiveConfigurationArtifact,
    EnrollmentPlan,
    FrozenArtifactInput,
)
from memrelay_eval.domain.environment import (
    AgentEnvironmentParityRecord,
    EnvironmentFingerprint,
    verify_agent_environment_parity,
)
from memrelay_eval.domain.errors import (
    AgentParityMismatchError,
    FrozenInputMutationError,
    ProtocolDeltaAuthorityError,
)
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.orchestration.stages import verify_stage_locks

PARITY_EVIDENCE_SCHEMA_VERSION = "1.0.0"
_PROTOCOL_DELTA_SCHEMA_VERSION = "1.0.0"
_SEAL = object()


class SealedProtocolDeltaAuthority:
    """Verified opaque delta pairs derived only from a sealed protocol artifact."""

    __slots__ = ("_protocol_artifact_sha256", "_protocol_document_digest", "_pairs")

    def __init__(
        self,
        seal: object,
        protocol_artifact_sha256: str,
        protocol_document_digest: str,
        pairs: Mapping[str, tuple[str, str]],
    ) -> None:
        if seal is not _SEAL:
            raise TypeError("sealed protocol delta authority must be derived from an artifact")
        self._protocol_artifact_sha256 = protocol_artifact_sha256
        self._protocol_document_digest = protocol_document_digest
        self._pairs = MappingProxyType(dict(pairs))

    @property
    def protocol_artifact_sha256(self) -> str:
        return self._protocol_artifact_sha256

    @property
    def protocol_document_digest(self) -> str:
        return self._protocol_document_digest

    def equivalent_to(self, other: object) -> bool:
        return (
            isinstance(other, SealedProtocolDeltaAuthority)
            and self._protocol_artifact_sha256 == other._protocol_artifact_sha256
            and self._protocol_document_digest == other._protocol_document_digest
            and self._pairs == other._pairs
        )

    def require_exact_pair(
        self,
        pair_id: str,
        left: AgentEnvironmentParityRecord,
        right: AgentEnvironmentParityRecord,
    ) -> tuple[str, ...]:
        """Authenticate both delta triples against one sealed opaque pair."""

        expected = self._pairs.get(pair_id)
        if expected is None:
            return ("protocol_delta_pair_missing",)
        if tuple(sorted((left.delta_commitment, right.delta_commitment))) != tuple(
            sorted(expected)
        ):
            return ("protocol_delta_commitment",)
        return ()


def derive_sealed_protocol_delta_authority(
    protocol: FrozenArtifactInput, artifact_store: ArtifactStorePort
) -> SealedProtocolDeltaAuthority:
    """Load strict protocol delta commitments through the verified artifact port."""

    raw = artifact_store.open_verified(protocol.artifact)
    try:
        document = json.loads(raw, object_pairs_hook=_unique_json_object)
    except json.JSONDecodeError as error:
        raise ProtocolDeltaAuthorityError("protocol_delta_authority_invalid_json") from error
    if not isinstance(document, dict):
        raise ProtocolDeltaAuthorityError("protocol_delta_authority_invalid_document")
    if raw != canonical_bytes(document) or not verify_digest(document):
        raise ProtocolDeltaAuthorityError("protocol_delta_authority_integrity_failure")
    if document.get("artifact_type") != "protocol_lock":
        raise ProtocolDeltaAuthorityError("protocol_delta_authority_invalid_artifact")
    section = document.get("agent_parity_delta_pairs")
    pairs = _parse_protocol_delta_pairs(section)
    return SealedProtocolDeltaAuthority(
        _SEAL,
        protocol.artifact.sha256,
        str(document["digest"]),
        pairs,
    )


@dataclass(frozen=True, slots=True)
class EnrollmentParityBinding:
    """The immutable enrollment values and sealed protocol authority at execution."""

    enrollment_parity_inputs_digest: str
    effective_configuration_digest: str
    environment_fingerprint_digest: str
    protocol_delta_authority: SealedProtocolDeltaAuthority


@dataclass(frozen=True, slots=True)
class PairedParityAttempt:
    """One opaque attempt's parity record bound to a sealed opaque pair identifier."""

    attempt: Attempt
    record: AgentEnvironmentParityRecord
    protocol_delta_pair_id: str
    enrollment: EnrollmentParityBinding

    def __post_init__(self) -> None:
        _require_sha256(self.protocol_delta_pair_id)


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
    artifact_store: ArtifactStorePort,
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
        derive_sealed_protocol_delta_authority(plan.inputs["protocol"], artifact_store),
    )


def verify_paired_parity(
    left: PairedParityAttempt, right: PairedParityAttempt
) -> ParityPreflightEvidence:
    """Produce evidence for both arms before either task is delivered."""
    mismatches = list(verify_agent_environment_parity(left.record, right.record))
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
    left_authority = left.enrollment.protocol_delta_authority
    right_authority = right.enrollment.protocol_delta_authority
    if not left_authority.equivalent_to(right_authority):
        mismatches.append("protocol_delta_authority_binding")
    elif left.protocol_delta_pair_id != right.protocol_delta_pair_id:
        mismatches.append("protocol_delta_pair_binding")
    else:
        mismatches.extend(
            left_authority.require_exact_pair(
                left.protocol_delta_pair_id, left.record, right.record
            )
        )
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


def _parse_protocol_delta_pairs(value: object) -> Mapping[str, tuple[str, str]]:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "pairs"}:
        raise ProtocolDeltaAuthorityError("protocol_delta_authority_invalid_schema")
    if value["schema_version"] != _PROTOCOL_DELTA_SCHEMA_VERSION:
        raise ProtocolDeltaAuthorityError("protocol_delta_authority_invalid_schema")
    entries = value["pairs"]
    if not isinstance(entries, list) or not entries:
        raise ProtocolDeltaAuthorityError("protocol_delta_authority_missing_pairs")
    pairs: dict[str, tuple[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, Mapping) or set(entry) != {"pair_id", "delta_commitments"}:
            raise ProtocolDeltaAuthorityError("protocol_delta_authority_invalid_pair")
        pair_id = entry["pair_id"]
        commitments = entry["delta_commitments"]
        if not isinstance(pair_id, str) or not isinstance(commitments, list):
            raise ProtocolDeltaAuthorityError("protocol_delta_authority_invalid_pair")
        _require_sha256(pair_id)
        if len(commitments) != 2 or any(
            not isinstance(commitment, str) for commitment in commitments
        ):
            raise ProtocolDeltaAuthorityError("protocol_delta_authority_invalid_pair")
        for commitment in commitments:
            _require_sha256(commitment)
        if pair_id in pairs:
            raise ProtocolDeltaAuthorityError("protocol_delta_authority_duplicate_pair")
        pairs[pair_id] = (commitments[0], commitments[1])
    return MappingProxyType(pairs)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolDeltaAuthorityError("protocol_delta_authority_duplicate_field")
        result[key] = value
    return result


def _require_sha256(value: object) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ProtocolDeltaAuthorityError("protocol_delta_authority_invalid_digest")
