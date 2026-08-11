"""Observation-sentinel adapters for the shipped poll/replay/live-tail product paths."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.entities import ArtifactManifest, ArtifactRef
from memrelay_eval.domain.ids import ExperimentId, RetentionPolicyId
from memrelay_eval.domain.observation import (
    OBSERVATION_ESTIMAND,
    OBSERVATION_NON_CLAIMS,
    OBSERVATION_QUALIFICATION_SCHEMA_VERSION,
    ObservationAssessment,
    ObservationBoundary,
    ObservationContract,
    ObservationEvidence,
    ObservationIdentity,
    assess_observation,
    observation_protocol_version,
)
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.domain.states import ArtifactScope
from memrelay_eval.evidence.manifest import observation_qualification_manifest

OBSERVATION_RECONCILIATION_POLICY_VERSION = "1.0.0"


def hash_observation_sources(source_files: Sequence[Path]) -> str:
    """Hash source bytes only, never paths or source content, for implementation identity."""

    if not source_files:
        raise ValueError("observation_source_files_empty")
    hashes: list[str] = []
    for source in source_files:
        try:
            payload = source.read_bytes()
        except OSError as error:
            raise ValueError("observation_source_unreadable") from error
        hashes.append(sha256(payload).hexdigest())
    return canonical_digest({"source_file_sha256": sorted(hashes)})


def build_observation_identity(
    *,
    source_files: Sequence[Path],
    semantic_map: Mapping[str, object],
    configuration: Mapping[str, object],
    runtime_lock: bytes,
) -> ObservationIdentity:
    """Freeze all implementation inputs before injecting a sentinel sequence."""

    if not runtime_lock:
        raise ValueError("observation_runtime_lock_empty")
    source_implementation_sha256 = hash_observation_sources(source_files)
    semantic_map_sha256 = canonical_digest({"semantic_map": dict(semantic_map)})
    configuration_sha256 = canonical_digest({"configuration": dict(configuration)})
    runtime_lock_sha256 = sha256(runtime_lock).hexdigest()
    sentinel_contract_sha256 = canonical_digest(
        {
            "schema_version": OBSERVATION_QUALIFICATION_SCHEMA_VERSION,
            "boundaries": [item.value for item in ObservationBoundary],
            "estimand": OBSERVATION_ESTIMAND,
            "non_claims": OBSERVATION_NON_CLAIMS,
        }
    )
    reconciliation_policy_sha256 = canonical_digest(
        {
            "version": OBSERVATION_RECONCILIATION_POLICY_VERSION,
            "fail_closed": True,
            "required_boundary_count": len(ObservationBoundary),
        }
    )
    identity_inputs = {
        "schema_version": OBSERVATION_QUALIFICATION_SCHEMA_VERSION,
        "source_implementation_sha256": source_implementation_sha256,
        "semantic_map_sha256": semantic_map_sha256,
        "configuration_sha256": configuration_sha256,
        "runtime_lock_sha256": runtime_lock_sha256,
        "sentinel_contract_sha256": sentinel_contract_sha256,
        "reconciliation_policy_sha256": reconciliation_policy_sha256,
    }
    conformance_sha256 = canonical_digest(identity_inputs)
    return ObservationIdentity(
        source_implementation_sha256=source_implementation_sha256,
        semantic_map_sha256=semantic_map_sha256,
        configuration_sha256=configuration_sha256,
        runtime_lock_sha256=runtime_lock_sha256,
        sentinel_contract_sha256=sentinel_contract_sha256,
        reconciliation_policy_sha256=reconciliation_policy_sha256,
        conformance_sha256=conformance_sha256,
        protocol_version=observation_protocol_version(conformance_sha256),
    )


@dataclass(frozen=True, slots=True)
class ObservationQualificationDecision:
    """Canonical, immutable outcome retained separately for each configured path."""

    assessment: ObservationAssessment
    decision_sha256: str
    decided_at: datetime

    def __post_init__(self) -> None:
        if len(self.decision_sha256) != 64 or not all(
            character in "0123456789abcdef" for character in self.decision_sha256
        ):
            raise ValueError("observation_decision_hash_invalid")
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ValueError("observation_decision_timestamp_invalid")
        object.__setattr__(self, "decided_at", self.decided_at.astimezone(UTC))

    @property
    def qualified(self) -> bool:
        return self.assessment.qualified

    def to_document(self) -> dict[str, object]:
        return {
            **self.assessment.to_document(),
            "decision_sha256": self.decision_sha256,
            "decided_at": self.decided_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        }


@dataclass(frozen=True, slots=True)
class PersistedObservationQualification:
    """CAS references for an immutable path decision and its two manifest boundaries."""

    decision: ObservationQualificationDecision
    decision_ref: ArtifactRef
    artifact_manifest_ref: ArtifactRef
    qualification_manifest_ref: ArtifactRef


class ObservationQualificationService:
    """Canonicalize, decide, and persist one independent replay or file-watch path."""

    def qualify(
        self,
        contract: ObservationContract,
        evidence: ObservationEvidence,
        *,
        decided_at: datetime,
    ) -> ObservationQualificationDecision:
        if decided_at.tzinfo is None or decided_at.utcoffset() is None:
            raise ValueError("observation_decision_timestamp_invalid")
        assessment = assess_observation(contract, evidence)
        decision_body = {
            **assessment.to_document(),
            "decided_at": decided_at.astimezone(UTC)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z"),
        }
        return ObservationQualificationDecision(
            assessment=assessment,
            decision_sha256=canonical_digest(decision_body),
            decided_at=decided_at,
        )

    def persist(
        self,
        store: ArtifactStorePort,
        decision: ObservationQualificationDecision,
        *,
        retention_policy_id: RetentionPolicyId | None = None,
    ) -> PersistedObservationQualification:
        """Persist both the decision and a manifest that binds the original identity."""

        payload = canonical_bytes(decision.to_document())
        decision_ref = store.put_bytes(
            payload,
            media_type="application/json",
            classification="observation_qualification",
        )
        identity = decision.assessment.contract.identity
        retention = retention_policy_id or RetentionPolicyId.from_digest(
            identity.conformance_sha256
        )
        artifact_manifest = ArtifactManifest(
            artifact_id=decision_ref.artifact_id,
            kind="observation_qualification",
            sha256=decision_ref.sha256,
            size_bytes=decision_ref.size_bytes,
            media_type="application/json",
            created_at=decision.decided_at,
            producer_component="memrelay_eval.observation",
            producer_version=OBSERVATION_QUALIFICATION_SCHEMA_VERSION,
            classification="observation_qualification",
            contains_secrets=False,
            source_artifact_ids=(),
            retention_policy_id=retention,
            encryption=None,
            scope=ArtifactScope.EXPERIMENT,
            experiment_id=ExperimentId.from_digest(identity.conformance_sha256),
        )
        artifact_manifest_ref = store.write_manifest(artifact_manifest)
        protocol_sha256 = canonical_digest(
            {
                "protocol_version": identity.protocol_version,
                "conformance_sha256": identity.conformance_sha256,
            }
        )
        manifest_payload = observation_qualification_manifest(
            path=decision.assessment.contract.path.value,
            conformance_sha256=identity.conformance_sha256,
            protocol_version=identity.protocol_version,
            protocol_sha256=protocol_sha256,
            terminal_status="succeeded" if decision.qualified else "failed",
            error_code=None if decision.qualified else decision.assessment.reason_code,
            input_hashes={
                "configuration": identity.configuration_sha256,
                "reconciliation_policy": identity.reconciliation_policy_sha256,
                "semantic_map": identity.semantic_map_sha256,
                "sentinel_contract": identity.sentinel_contract_sha256,
                "source_implementation": identity.source_implementation_sha256,
            },
            output_hashes={"observation_qualification": decision.decision_sha256},
            runtime_lock_sha256=identity.runtime_lock_sha256,
        )
        qualification_manifest_ref = store.put_bytes(
            manifest_payload,
            media_type="application/json",
            classification="observation_qualification_manifest",
        )
        return PersistedObservationQualification(
            decision=decision,
            decision_ref=decision_ref,
            artifact_manifest_ref=artifact_manifest_ref,
            qualification_manifest_ref=qualification_manifest_ref,
        )
