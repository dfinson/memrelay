"""Pre-assignment sealing of immutable, treatment-neutral enrollment inputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from memrelay_eval.canonical import attach_digest, canonical_bytes, canonical_digest
from memrelay_eval.domain.entities import (
    EffectiveConfigurationArtifact,
    EnrollmentPlan,
    EnrollmentPlanLineage,
    FrozenArtifactInput,
)
from memrelay_eval.domain.environment import EnvironmentFingerprint, persist_environment_fingerprint
from memrelay_eval.domain.errors import (
    EnrollmentLineageError,
    FrozenInputMutationError,
    InvalidConfigurationError,
)
from memrelay_eval.domain.ids import AssignmentId, EnrollmentPlanId
from memrelay_eval.domain.policies import (
    require_eligible_disposition,
    require_no_secret_values,
    require_treatment_neutral,
)
from memrelay_eval.domain.ports import ArtifactStorePort
from memrelay_eval.orchestration.blocks import EnvironmentBlocks, require_same_environment_stratum

ENROLLMENT_PLAN_SCHEMA_VERSION = "1.0.0"
_INPUT_NAMES = (
    "catalog",
    "protocol",
    "effective_configuration",
    "environment_fingerprint",
    "native_model_catalog",
    "assignment_algorithm",
    "seed_commitment",
    "blocks",
    "ordered_inputs",
    "price_tables",
    "eligibility_dispositions",
)


@dataclass(frozen=True, slots=True)
class EnrollmentFreezeRequest:
    """All values that must be sealed before an assignment can exist."""

    catalog: FrozenArtifactInput
    protocol: FrozenArtifactInput
    effective_configuration: EffectiveConfigurationArtifact
    environment: EnvironmentFingerprint
    native_model_catalog: FrozenArtifactInput
    assignment_algorithm: Mapping[str, object]
    seed_commitment: str
    blocks: EnvironmentBlocks
    ordered_inputs: Sequence[Mapping[str, object]]
    price_tables: Mapping[str, object]
    eligibility_dispositions: Sequence[Mapping[str, object]]
    lineage: EnrollmentPlanLineage | None = None


def freeze_enrollment_inputs(
    request: EnrollmentFreezeRequest, artifact_store: ArtifactStorePort
) -> EnrollmentPlan:
    """Persist and seal every pre-enrollment input through ``ArtifactStorePort``."""
    _validate_request(request, artifact_store)
    inputs = {
        "catalog": request.catalog,
        "protocol": request.protocol,
        "effective_configuration": request.effective_configuration.to_frozen_input(),
        "environment_fingerprint": persist_environment_fingerprint(
            request.environment, artifact_store
        ),
        "native_model_catalog": request.native_model_catalog,
        "assignment_algorithm": _persist_inline_input(
            artifact_store,
            "assignment_algorithm",
            request.assignment_algorithm,
            "frozen_protocol",
        ),
        "seed_commitment": _persist_inline_input(
            artifact_store,
            "seed_commitment",
            request.seed_commitment,
            "frozen_protocol",
        ),
        "blocks": _persist_inline_input(
            artifact_store,
            "blocks",
            request.blocks.to_document(),
            "environment_blocking",
        ),
        "ordered_inputs": _persist_inline_input(
            artifact_store,
            "ordered_inputs",
            list(request.ordered_inputs),
            "frozen_protocol",
        ),
        "price_tables": _persist_inline_input(
            artifact_store,
            "price_tables",
            request.price_tables,
            "fixture_or_observed_price_table",
        ),
        "eligibility_dispositions": _persist_inline_input(
            artifact_store,
            "eligibility_dispositions",
            list(request.eligibility_dispositions),
            "story_1_4_disposition",
        ),
    }
    parity_hash_inputs = {name: inputs[name].to_record() for name in _INPUT_NAMES}
    parity_inputs_digest = canonical_digest(parity_hash_inputs)
    document = attach_digest(
        {
            "artifact_type": "pre_enrollment_plan",
            "schema_version": ENROLLMENT_PLAN_SCHEMA_VERSION,
            "inputs": parity_hash_inputs,
            "parity_hash_inputs": parity_hash_inputs,
            "parity_hash": parity_inputs_digest,
            "lineage": request.lineage.to_record() if request.lineage is not None else None,
            "evidence_provenance": (
                "qualified"
                if getattr(artifact_store, "eligible_for_paid_or_study", False)
                else "unpaid_conformance"
            ),
        }
    )
    artifact = artifact_store.put_bytes(
        canonical_bytes(document),
        media_type="application/json",
        classification="pre_enrollment_plan",
    )
    return EnrollmentPlan(
        EnrollmentPlanId.from_digest(str(document["digest"])),
        artifact,
        str(document["digest"]),
        inputs,
        parity_inputs_digest,
        request.lineage,
        unpaid_conformance=not getattr(artifact_store, "eligible_for_paid_or_study", False),
    )


def assert_assigned_plan_unchanged(assigned: EnrollmentPlan, candidate: EnrollmentPlan) -> None:
    """Reject every in-place frozen value change after an assignment exists."""
    for name in _INPUT_NAMES:
        if assigned.inputs.get(name) != candidate.inputs.get(name):
            raise FrozenInputMutationError(name)
    if assigned.parity_inputs_digest != candidate.parity_inputs_digest:
        raise FrozenInputMutationError("parity_hash_inputs")
    if assigned.lineage != candidate.lineage:
        raise FrozenInputMutationError("lineage")
    if assigned.id != candidate.id:
        raise FrozenInputMutationError("plan_identity")


def require_successor_lineage(previous: EnrollmentPlan, successor: EnrollmentPlan) -> None:
    """Allow changed inputs only in a separately sealed successor with valid lineage."""
    if previous.id == successor.id:
        if (
            previous.inputs != successor.inputs
            or previous.parity_inputs_digest != successor.parity_inputs_digest
            or previous.lineage != successor.lineage
        ):
            raise EnrollmentLineageError(EnrollmentLineageError.code)
        return
    lineage = successor.lineage
    if lineage is None or lineage.parent_plan_id != previous.id:
        raise EnrollmentLineageError(EnrollmentLineageError.code)
    protocol_changed = previous.inputs["protocol"] != successor.inputs["protocol"]
    configuration_changed = (
        previous.inputs["effective_configuration"] != successor.inputs["effective_configuration"]
    )
    if protocol_changed and lineage.reason == "protocol_revision":
        return
    if (
        configuration_changed
        and lineage.reason == "attempt_configuration_revision"
        and lineage.replacement_attempt_id is not None
    ):
        return
    raise EnrollmentLineageError(EnrollmentLineageError.code)


class AssignedEnrollmentPlans:
    """In-memory guard for the immutable plan bound to each assignment."""

    def __init__(self) -> None:
        self._plans: dict[AssignmentId, EnrollmentPlan] = {}

    def bind(self, assignment_id: AssignmentId, plan: EnrollmentPlan) -> None:
        existing = self._plans.get(assignment_id)
        if existing is not None:
            assert_assigned_plan_unchanged(existing, plan)
            return
        self._plans[assignment_id] = plan

    def verify(self, assignment_id: AssignmentId, candidate: EnrollmentPlan) -> None:
        assigned = self._plans.get(assignment_id)
        if assigned is None:
            raise FrozenInputMutationError("assignment_plan_missing")
        assert_assigned_plan_unchanged(assigned, candidate)


def _validate_request(request: EnrollmentFreezeRequest, artifact_store: ArtifactStorePort) -> None:
    for source in (
        request.catalog,
        request.protocol,
        request.effective_configuration.to_frozen_input(),
        request.native_model_catalog,
    ):
        artifact_store.open_verified(source.artifact)
    if not isinstance(request.seed_commitment, str) or len(request.seed_commitment) != 64:
        raise InvalidConfigurationError()
    if any(character not in "0123456789abcdef" for character in request.seed_commitment):
        raise InvalidConfigurationError()
    if not request.ordered_inputs or not request.eligibility_dispositions:
        raise InvalidConfigurationError()
    require_same_environment_stratum(request.blocks, request.environment)
    for disposition in request.eligibility_dispositions:
        if not isinstance(disposition, Mapping):
            raise InvalidConfigurationError()
        require_eligible_disposition(disposition)
        digest = disposition.get("digest")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise InvalidConfigurationError()
    raw_values = (
        request.assignment_algorithm,
        request.blocks.to_document(),
        list(request.ordered_inputs),
        request.price_tables,
        list(request.eligibility_dispositions),
    )
    for value in raw_values:
        require_no_secret_values(value)
        require_treatment_neutral(value)
        _reject_mutable_path_authority(value)
        try:
            canonical_bytes(value)
        except Exception as error:
            raise InvalidConfigurationError() from error


def _persist_inline_input(
    artifact_store: ArtifactStorePort, name: str, value: object, provenance: str
) -> FrozenArtifactInput:
    document = attach_digest(
        {
            "artifact_type": f"frozen_{name}",
            "schema_version": ENROLLMENT_PLAN_SCHEMA_VERSION,
            "provenance": provenance,
            "value": value,
        }
    )
    artifact = artifact_store.put_bytes(
        canonical_bytes(document),
        media_type="application/json",
        classification="pre_enrollment_input",
    )
    return FrozenArtifactInput(
        artifact,
        schema_version=ENROLLMENT_PLAN_SCHEMA_VERSION,
        provenance=provenance,
    )


def _reject_mutable_path_authority(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise InvalidConfigurationError()
            if key == "path" or key.endswith("_path") or key.endswith("_root"):
                raise InvalidConfigurationError()
            _reject_mutable_path_authority(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _reject_mutable_path_authority(nested)
