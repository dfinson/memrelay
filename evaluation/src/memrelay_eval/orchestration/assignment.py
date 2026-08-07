"""Deterministic opaque assignment over Story 1.5 frozen enrollment inputs."""

from __future__ import annotations

import hmac
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from threading import Lock
from typing import TypeVar

from memrelay_eval.canonical import attach_digest, canonical_bytes, canonical_digest, verify_digest
from memrelay_eval.domain.assignment import (
    AssignmentPlan,
    OpaqueAssignment,
    ProvisioningResolution,
)
from memrelay_eval.domain.entities import EnrollmentPlan, FrozenArtifactInput
from memrelay_eval.domain.errors import (
    AssignmentResolutionDeniedError,
    DurableConformanceRequiredError,
    InvalidAssignmentAlgorithmError,
    SeedCommitmentMismatchError,
)
from memrelay_eval.domain.ids import AssignmentId, ExperimentId, RunId
from memrelay_eval.domain.policies import require_no_secret_values, require_treatment_neutral
from memrelay_eval.domain.ports import ArtifactStorePort

ASSIGNMENT_PLAN_SCHEMA_VERSION = "1.0.0"
_Result = TypeVar("_Result")


@dataclass(frozen=True, slots=True)
class AssignmentRequest:
    """One ordinary, treatment-neutral assignment request."""

    experiment_id: ExperimentId
    run_id: RunId
    ordered_input_hash: str


@dataclass(frozen=True, slots=True)
class FixtureBalancedBlockAlgorithm:
    """A deterministic fixture-only allocator, never a durable-study authority."""

    name: str
    version: str
    slot_count: int

    def __post_init__(self) -> None:
        if not self.name or not self.version or self.slot_count < 2:
            raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)


class AssignmentAlgorithmRegistry:
    """Explicit registry, with no implicit algorithm or version fallback."""

    def __init__(self, algorithms: Sequence[FixtureBalancedBlockAlgorithm]) -> None:
        self._algorithms = {(item.name, item.version): item for item in algorithms}
        if len(self._algorithms) != len(algorithms):
            raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)

    def require(self, name: str, version: str) -> FixtureBalancedBlockAlgorithm:
        algorithm = self._algorithms.get((name, version))
        if algorithm is None:
            raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
        return algorithm


class ProvisioningAuthority:
    """Capability holder injected only at the provisioning composition boundary."""

    __slots__ = ("_service", "_capability")

    def __init__(self, service: ConcealedAssignmentService, capability: object) -> None:
        self._service = service
        self._capability = capability

    def resolve(self, assignment_id: AssignmentId) -> ProvisioningResolution:
        return self._service.resolve_for_provisioning(assignment_id, self._capability)

    def use_resolution(
        self, assignment_id: AssignmentId, operation: Callable[[object], _Result]
    ) -> _Result:
        resolution = self.resolve(assignment_id)
        return operation(resolution._material)

    def verify_balance(self, assignments: Sequence[OpaqueAssignment]) -> bool:
        return self._service._verify_balance(assignments, self._capability)


@dataclass(frozen=True, slots=True)
class _PrivateAssignment:
    assignment: OpaqueAssignment
    block_id: str
    slot_index: int


class ConcealedAssignmentService:
    """Seals ordinary records while retaining resolution material behind a capability."""

    def __init__(
        self,
        plan: AssignmentPlan,
        artifact_store: ArtifactStorePort,
        seed_material: bytes,
        registry: AssignmentAlgorithmRegistry,
    ) -> None:
        if not isinstance(seed_material, bytes) or (
            sha256(seed_material).hexdigest() != plan.seed_commitment
        ):
            raise SeedCommitmentMismatchError(SeedCommitmentMismatchError.code)
        self._plan = plan
        self._algorithm = registry.require(plan.algorithm_name, plan.algorithm_version)
        self._seed_material = bytes(seed_material)
        self._artifact_store = artifact_store
        self._block_by_input = _blocks_by_input(plan, artifact_store)
        self._records: dict[AssignmentId, _PrivateAssignment] = {}
        self._lock = Lock()
        self._capability = object()

    def assign(self, request: AssignmentRequest) -> OpaqueAssignment:
        block_id, position = self._block_by_input.get(request.ordered_input_hash, ("", -1))
        if position < 0:
            raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
        identity = canonical_digest(
            {
                "experiment_id": str(request.experiment_id),
                "run_id": str(request.run_id),
                "assignment_plan_hash": self._plan.assignment_plan_hash,
                "ordered_input_hash": request.ordered_input_hash,
            }
        )
        assignment = OpaqueAssignment(
            AssignmentId.from_digest(identity),
            request.experiment_id,
            request.run_id,
            self._plan.assignment_plan_hash,
        )
        with self._lock:
            existing = self._records.get(assignment.id)
            if existing is not None:
                return existing.assignment
            phase = int.from_bytes(
                hmac.new(
                    self._seed_material,
                    block_id.encode("utf-8"),
                    sha256,
                ).digest()[:8],
                "big",
            ) % self._algorithm.slot_count
            slot_index = (phase + position) % self._algorithm.slot_count
            self._records[assignment.id] = _PrivateAssignment(assignment, block_id, slot_index)
        return assignment

    def provisioning_authority(self) -> ProvisioningAuthority:
        return ProvisioningAuthority(self, self._capability)

    def resolve_for_provisioning(
        self, assignment_id: AssignmentId, capability: object
    ) -> ProvisioningResolution:
        if capability is not self._capability:
            raise AssignmentResolutionDeniedError(AssignmentResolutionDeniedError.code)
        with self._lock:
            record = self._records.get(assignment_id)
            if record is None:
                raise AssignmentResolutionDeniedError(AssignmentResolutionDeniedError.code)
            return ProvisioningResolution(record.assignment.id, record.slot_index)

    def ordinary_manifest(self, assignment: OpaqueAssignment) -> dict[str, str]:
        """Return only opaque identities and commitment hashes for ordinary surfaces."""
        with self._lock:
            authoritative = self._records.get(assignment.id)
            if authoritative is None or authoritative.assignment != assignment:
                raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
        return {
            "schema_version": ASSIGNMENT_PLAN_SCHEMA_VERSION,
            "experiment_id": str(assignment.experiment_id),
            "run_id": str(assignment.run_id),
            "assignment_id": str(assignment.id),
            "assignment_plan_hash": assignment.assignment_plan_hash,
        }

    def require_durable_execution(self) -> None:
        """Hard stop until the future durable ledger and collector adapters conform."""
        raise DurableConformanceRequiredError(DurableConformanceRequiredError.code)

    def _verify_balance(self, assignments: Sequence[OpaqueAssignment], capability: object) -> bool:
        if capability is not self._capability:
            raise AssignmentResolutionDeniedError(AssignmentResolutionDeniedError.code)
        buckets: dict[str, list[int]] = {}
        with self._lock:
            for assignment in assignments:
                record = self._records.get(assignment.id)
                if record is None:
                    raise AssignmentResolutionDeniedError(AssignmentResolutionDeniedError.code)
                buckets.setdefault(record.block_id, []).append(record.slot_index)
        for slots in buckets.values():
            counts = [slots.count(index) for index in range(self._algorithm.slot_count)]
            if max(counts) - min(counts) > 1:
                return False
        return True


def seal_assignment_plan(plan: EnrollmentPlan, artifact_store: ArtifactStorePort) -> AssignmentPlan:
    """Materialize a complete immutable assignment-plan commitment from frozen inputs."""
    algorithm = _algorithm_value(plan.inputs["assignment_algorithm"], artifact_store)
    seed_commitment = _string_value(plan.inputs["seed_commitment"], artifact_store)
    if not isinstance(seed_commitment, str) or not _is_sha256(seed_commitment):
        raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
    name = algorithm.get("name")
    version = algorithm.get("version")
    if (
        set(algorithm) != {"name", "version"}
        or not isinstance(name, str)
        or not isinstance(version, str)
        or not name
        or not version
    ):
        raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
    ordered_input_hashes, aliases = _ordered_input_hashes(
        plan.inputs["ordered_inputs"], artifact_store
    )
    _blocks_by_input_values(
        _mapping_value(plan.inputs["blocks"], artifact_store),
        ordered_input_hashes,
        aliases,
    )
    frozen_input_hashes = {name: value.artifact.sha256 for name, value in plan.inputs.items()}
    seal = {
        "schema_version": ASSIGNMENT_PLAN_SCHEMA_VERSION,
        "enrollment_plan_id": str(plan.id),
        "frozen_input_hashes": frozen_input_hashes,
        "algorithm": {"name": name, "version": version},
        "seed_commitment": seed_commitment,
        "blocks": {
            "artifact_identity": str(plan.inputs["blocks"].artifact.artifact_id),
            "digest": plan.inputs["blocks"].artifact.sha256,
        },
        "ordered_input_hashes": list(ordered_input_hashes),
    }
    assignment_plan_hash = canonical_digest(seal)
    document = attach_digest(
        {
            "artifact_type": "concealed_assignment_plan",
            **seal,
            "assignment_plan_hash": assignment_plan_hash,
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
        classification="concealed_assignment_plan",
    )
    return AssignmentPlan(
        str(plan.id),
        artifact,
        str(document["digest"]),
        assignment_plan_hash,
        name,
        version,
        seed_commitment,
        plan.inputs["blocks"].artifact.sha256,
        plan.inputs["blocks"].artifact,
        plan.inputs["ordered_inputs"].artifact,
        ordered_input_hashes,
        frozen_input_hashes,
        unpaid_conformance=not getattr(artifact_store, "eligible_for_paid_or_study", False),
    )


def _algorithm_value(
    input_value: FrozenArtifactInput, store: ArtifactStorePort
) -> Mapping[str, object]:
    value = _frozen_value(input_value, store)
    if not isinstance(value, Mapping):
        raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
    require_no_secret_values(value)
    require_treatment_neutral(value)
    return value


def _string_value(input_value: FrozenArtifactInput, store: ArtifactStorePort) -> object:
    return _frozen_value(input_value, store)


def _mapping_value(
    input_value: FrozenArtifactInput, store: ArtifactStorePort
) -> Mapping[str, object]:
    value = _frozen_value(input_value, store)
    if not isinstance(value, Mapping):
        raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
    return value


def _ordered_input_hashes(
    input_value: FrozenArtifactInput, store: ArtifactStorePort
) -> tuple[tuple[str, ...], Mapping[str, str]]:
    value = _frozen_value(input_value, store)
    if not isinstance(value, list) or not value:
        raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
    hashes: list[str] = []
    aliases: dict[str, str] = {}
    for position, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
        explicit_hash = item.get("input_hash")
        item_hash = (
            explicit_hash
            if isinstance(explicit_hash, str) and _is_sha256(explicit_hash)
            else canonical_digest({"position": position, "input": item})
        )
        if item_hash in hashes:
            raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
        hashes.append(item_hash)
        aliases[item_hash] = item_hash
        for key in ("input_hash", "input_id", "task_identity"):
            value = item.get(key)
            if isinstance(value, str) and value:
                prior = aliases.get(value)
                if prior is not None and prior != item_hash:
                    raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
                aliases[value] = item_hash
    return tuple(hashes), aliases


def _blocks_by_input(plan: AssignmentPlan, store: ArtifactStorePort) -> dict[str, tuple[str, int]]:
    blocks = _mapping_value_from_plan(plan, store)
    hashes, aliases = _ordered_input_hashes(
        FrozenArtifactInput(plan.ordered_inputs_artifact, "1.0.0", "frozen_ordered_inputs"),
        store,
    )
    if hashes != plan.ordered_input_hashes:
        raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
    return _blocks_by_input_values(blocks, plan.ordered_input_hashes, aliases)


def _mapping_value_from_plan(
    plan: AssignmentPlan, store: ArtifactStorePort
) -> Mapping[str, object]:
    return _mapping_value(
        FrozenArtifactInput(plan.blocks_artifact, "1.0.0", "frozen_blocks"),
        store,
    )


def _blocks_by_input_values(
    blocks: Mapping[str, object],
    ordered_input_hashes: Sequence[str],
    aliases: Mapping[str, str],
) -> dict[str, tuple[str, int]]:
    raw_blocks = blocks.get("blocks")
    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
    resolved: dict[str, tuple[str, int]] = {}
    expected = set(ordered_input_hashes)
    for raw_block in raw_blocks:
        if not isinstance(raw_block, Mapping):
            raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
        block_id = raw_block.get("block_id")
        values = raw_block.get("ordered_input_hashes", raw_block.get("run_order"))
        if not isinstance(block_id, str) or not isinstance(values, list) or not values:
            raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
        for position, identity in enumerate(values):
            input_hash = aliases.get(identity) if isinstance(identity, str) else None
            if input_hash is None or input_hash not in expected or input_hash in resolved:
                raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
            resolved[input_hash] = (block_id, position)
    if set(resolved) != expected:
        raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
    return resolved


def _frozen_value(input_value: FrozenArtifactInput, store: ArtifactStorePort) -> object:
    try:
        raw = store.open_verified(input_value.artifact)
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code) from error
    if (
        not isinstance(document, Mapping)
        or canonical_bytes(document) != raw
        or not verify_digest(document)
        or "value" not in document
    ):
        raise InvalidAssignmentAlgorithmError(InvalidAssignmentAlgorithmError.code)
    return document["value"]


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
