"""Opaque assignment records and the provisioning-only resolution boundary."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .entities import ArtifactRef
from .errors import InvalidArtifactManifestError
from .ids import AssignmentId, ExperimentId, RunId

_SHA256 = re.compile(r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class AssignmentPlan:
    """Immutable, ordinary evidence for a concealed assignment policy."""

    enrollment_plan_id: str
    artifact: ArtifactRef
    document_digest: str
    assignment_plan_hash: str
    algorithm_name: str
    algorithm_version: str
    seed_commitment: str
    blocks_digest: str
    blocks_artifact: ArtifactRef
    ordered_inputs_artifact: ArtifactRef
    ordered_input_hashes: tuple[str, ...]
    frozen_input_hashes: Mapping[str, str]
    unpaid_conformance: bool

    def __post_init__(self) -> None:
        if not all(
            _SHA256.fullmatch(value)
            for value in (
                self.document_digest,
                self.assignment_plan_hash,
                self.seed_commitment,
                self.blocks_digest,
            )
        ):
            raise InvalidArtifactManifestError("assignment plan hashes must be lowercase SHA-256")
        if not self.algorithm_name or not self.algorithm_version:
            raise InvalidArtifactManifestError("assignment algorithm identity is required")
        if self.blocks_artifact.sha256 != self.blocks_digest:
            raise InvalidArtifactManifestError("assignment blocks must match their sealed digest")
        if not self.ordered_input_hashes or not all(
            _SHA256.fullmatch(value) for value in self.ordered_input_hashes
        ):
            raise InvalidArtifactManifestError("ordered input hashes must be lowercase SHA-256")
        expected_inputs = {
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
        }
        if set(self.frozen_input_hashes) != expected_inputs or not all(
            _SHA256.fullmatch(value) for value in self.frozen_input_hashes.values()
        ):
            raise InvalidArtifactManifestError("frozen assignment inputs are incomplete")
        object.__setattr__(self, "ordered_input_hashes", tuple(self.ordered_input_hashes))
        object.__setattr__(
            self,
            "frozen_input_hashes",
            MappingProxyType(dict(self.frozen_input_hashes)),
        )


@dataclass(frozen=True, slots=True)
class OpaqueAssignment:
    """The only assignment shape allowed in ordinary manifests and attempt specs."""

    id: AssignmentId
    experiment_id: ExperimentId
    run_id: RunId
    assignment_plan_hash: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.assignment_plan_hash):
            raise InvalidArtifactManifestError("assignment plan hash must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class AttemptSpecification:
    """A treatment-neutral execution specification passed outside provisioning."""

    run_id: RunId
    assignment_id: AssignmentId
    assignment_plan_hash: str

    def __post_init__(self) -> None:
        if not _SHA256.fullmatch(self.assignment_plan_hash):
            raise InvalidArtifactManifestError("assignment plan hash must be lowercase SHA-256")


class ProvisioningResolution:
    """Opaque result whose material is available only to the authority that created it."""

    __slots__ = ("_assignment_id", "_material")

    def __init__(self, assignment_id: AssignmentId, material: object) -> None:
        self._assignment_id = assignment_id
        self._material = material

    @property
    def assignment_id(self) -> AssignmentId:
        return self._assignment_id

    def __repr__(self) -> str:
        return f"ProvisioningResolution(assignment_id={self._assignment_id!s})"
