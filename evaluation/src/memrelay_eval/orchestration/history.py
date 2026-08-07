"""Protocol-defined, arm-concealed dynamic-history sequence orchestration.

Only this module has treatment-aware state.  Generic execution receives an
``AttemptSpecification`` and remains unable to observe arm meanings.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from threading import Lock

from memrelay_eval.canonical import canonical_digest
from memrelay_eval.domain.assignment import AttemptSpecification
from memrelay_eval.domain.entities import (
    ArtifactRef,
    AttemptTerminal,
    DynamicEpisode,
    DynamicSequence,
    DynamicSequenceCleanup,
    DynamicSequenceTerminal,
)
from memrelay_eval.domain.errors import (
    AnalysisBoundaryError,
    DynamicHistoryViolationError,
    UnsupportedArmError,
)
from memrelay_eval.domain.ids import AttemptId, EpisodeId, ExperimentId, RunId, SequenceId
from memrelay_eval.domain.ports import LedgerPort
from memrelay_eval.domain.states import EvaluationStratum, HistoryMode, SequenceState
from memrelay_eval.orchestration.assignment import (
    AssignmentRequest,
    ConcealedAssignmentService,
    ProvisioningAuthority,
)

_ARM_CODES = frozenset({"N0", "E0", "YL", "MI", "TR", "OR", "AI", "WO"})


@dataclass(frozen=True, slots=True)
class ArmContract:
    """Frozen protocol-owned treatment behavior, never exposed to executors."""

    code: str
    tool_available: bool
    reads_available: bool
    writes_available: bool
    read_response: str
    evidence_source: str
    equal_startup_and_budget: bool = True
    discard_writes: bool = False
    pre_outcome_latency_yoked: bool = False
    certified_irrelevant: bool = False

    def __post_init__(self) -> None:
        if self.code not in _ARM_CODES:
            raise UnsupportedArmError()
        if not self.equal_startup_and_budget:
            raise DynamicHistoryViolationError("arm_parity_contract_incomplete")
        if self.discard_writes and not self.writes_available:
            raise DynamicHistoryViolationError("discarded_writes_require_write_access")
        if self.pre_outcome_latency_yoked and self.read_response != "empty":
            raise DynamicHistoryViolationError("latency_yoke_requires_empty_response")
        if self.certified_irrelevant and self.evidence_source != "certified_irrelevant":
            raise DynamicHistoryViolationError("irrelevant_arm_requires_certification")


def protocol_arm_contracts() -> tuple[ArmContract, ...]:
    """Return the complete frozen set; generic code never branches on these labels."""

    return (
        ArmContract("N0", False, False, False, "unavailable", "none"),
        ArmContract("E0", True, True, True, "empty", "zero_item", discard_writes=True),
        ArmContract("YL", True, True, True, "empty", "zero_item", pre_outcome_latency_yoked=True),
        ArmContract(
            "MI", True, True, True, "matched", "certified_irrelevant", certified_irrelevant=True
        ),
        ArmContract("TR", True, True, True, "production", "production_pipeline"),
        ArmContract("OR", True, True, True, "curated", "minimum_necessary"),
        ArmContract(
            "AI", True, True, True, "replaced", "certified_irrelevant", certified_irrelevant=True
        ),
        ArmContract("WO", True, False, True, "unavailable", "none"),
    )


@dataclass(frozen=True, slots=True)
class DynamicHistoryProtocol:
    """Frozen sequence regime and its sealed arm contract map."""

    allocation_seed_commitment: str
    arms: Mapping[int, ArmContract]

    def __post_init__(self) -> None:
        if not _is_sha256(self.allocation_seed_commitment):
            raise DynamicHistoryViolationError("dynamic_seed_commitment_invalid")
        if not self.arms:
            raise DynamicHistoryViolationError("dynamic_protocol_has_no_arms")
        if any(not isinstance(slot, int) or slot < 0 for slot in self.arms):
            raise DynamicHistoryViolationError("dynamic_protocol_slot_invalid")
        if len({contract.code for contract in self.arms.values()}) != len(self.arms):
            raise DynamicHistoryViolationError("dynamic_protocol_arm_duplicate")
        object.__setattr__(self, "arms", dict(self.arms))

    def arm_for_slot(self, slot: object) -> ArmContract:
        contract = self.arms.get(slot) if isinstance(slot, int) else None
        if contract is None:
            raise UnsupportedArmError()
        return contract


@dataclass(frozen=True, slots=True)
class DynamicSequenceRequest:
    """Entire ordered sequence supplied before any episode can be provisioned."""

    sequence_id: SequenceId
    experiment_id: ExperimentId
    ordered_input_hash: str
    episode_ids: tuple[EpisodeId, ...]
    stratum: EvaluationStratum

    def __post_init__(self) -> None:
        if not _is_sha256(self.ordered_input_hash):
            raise DynamicHistoryViolationError("dynamic_ordered_input_hash_invalid")
        if not self.episode_ids or len(set(self.episode_ids)) != len(self.episode_ids):
            raise DynamicHistoryViolationError("dynamic_episode_order_invalid")
        object.__setattr__(self, "episode_ids", tuple(self.episode_ids))


@dataclass(frozen=True, slots=True)
class SequenceResourceIdentity:
    """Every mutable resource is namespaced by its single assigned sequence."""

    sequence_id: SequenceId
    episode_id: EpisodeId
    graph_root: str
    workspace: str
    cache_namespace: str
    output_namespace: str
    credential_namespace: str


@dataclass(frozen=True, slots=True)
class ProvisionedEpisode:
    """Authority-only provisioning result plus the opaque executor input."""

    episode: DynamicEpisode
    executor_specification: AttemptSpecification
    resources: SequenceResourceIdentity


@dataclass(frozen=True, slots=True)
class SequenceAnalysisIdentity:
    """Identity required to aggregate sequence-level total-policy estimands."""

    history_mode: HistoryMode
    stratum: EvaluationStratum
    assignment_plan_hash: str


class DynamicHistoryCoordinator:
    """Assigns a complete sequence once and provisions only its next episode."""

    def __init__(
        self,
        assignment_service: ConcealedAssignmentService,
        protocol: DynamicHistoryProtocol,
        ledger: LedgerPort,
        treatment_provisioner: (
            Callable[[ArmContract, SequenceResourceIdentity], object] | None
        ) = None,
    ) -> None:
        self._assignment_service = assignment_service
        self._provisioning = assignment_service.provisioning_authority()
        self._protocol = protocol
        self._ledger = ledger
        self._treatment_provisioner = treatment_provisioner
        self._sequences: dict[SequenceId, DynamicSequence] = {}
        self._episode_index: dict[SequenceId, int] = {}
        self._terminals: dict[SequenceId, list[ArtifactRef]] = {}
        self._terminal_attempts: dict[SequenceId, list[str]] = {}
        self._resource_owners: dict[str, SequenceId] = {}
        self._episode_runs: dict[tuple[SequenceId, EpisodeId], RunId] = {}
        self._provisioned_episodes: set[tuple[SequenceId, EpisodeId]] = set()
        self._cleanup: dict[SequenceId, DynamicSequenceCleanup] = {}
        self._lock = Lock()

    def enroll(self, request: DynamicSequenceRequest) -> DynamicSequence:
        """Commit the full sequence to one opaque allocation before episode zero."""

        anchor_run = RunId.from_digest(
            canonical_digest(
                {
                    "sequence_id": str(request.sequence_id),
                    "assignment_plan_hash": self._assignment_service.assignment_plan_hash,
                }
            )
        )
        assignment = self._assignment_service.assign(
            AssignmentRequest(request.experiment_id, anchor_run, request.ordered_input_hash)
        )
        sequence = DynamicSequence(
            request.sequence_id,
            request.experiment_id,
            assignment.id,
            assignment.assignment_plan_hash,
            request.episode_ids,
            HistoryMode.DYNAMIC,
            request.stratum,
            self._protocol.allocation_seed_commitment,
        )
        with self._lock:
            previous = self._sequences.get(sequence.id)
            if previous is not None:
                if previous != sequence:
                    raise DynamicHistoryViolationError("dynamic_sequence_enrollment_mutation")
                return previous
            self._sequences[sequence.id] = sequence
            self._episode_index[sequence.id] = 0
            self._terminals[sequence.id] = []
            self._terminal_attempts[sequence.id] = []
        return sequence

    def provisioning_authority(self) -> DynamicProvisioningAuthority:
        """Return the only object allowed to resolve an arm or allocate state."""

        return DynamicProvisioningAuthority(self, self._provisioning)

    def analysis_identity(self, sequence_id: SequenceId) -> SequenceAnalysisIdentity:
        sequence = self._require_sequence(sequence_id)
        return SequenceAnalysisIdentity(
            sequence.history_mode, sequence.stratum, sequence.assignment_plan_hash
        )

    def _provision_next(
        self, sequence_id: SequenceId, authority: ProvisioningAuthority
    ) -> ProvisionedEpisode:
        with self._lock:
            sequence = self._require_sequence(sequence_id)
            ordinal = self._episode_index[sequence_id]
            if ordinal >= len(sequence.episode_ids):
                raise DynamicHistoryViolationError("dynamic_sequence_already_terminal")
            episode_id = sequence.episode_ids[ordinal]
            episode_key = (sequence_id, episode_id)
            if episode_key in self._provisioned_episodes:
                raise DynamicHistoryViolationError("dynamic_episode_already_exposed")
            self._provisioned_episodes.add(episode_key)
            prior_attempts = tuple(
                AttemptId(value) for value in self._terminal_attempts[sequence_id]
            )

        def provision(slot: object) -> ProvisionedEpisode:
            # This is the sole treatment-aware branch and runs inside assignment authority.
            contract = self._protocol.arm_for_slot(slot)
            resources = self._resources(sequence, episode_id)
            if self._treatment_provisioner is not None:
                self._treatment_provisioner(contract, resources)
            with self._lock:
                for resource in _resource_values(resources):
                    owner = self._resource_owners.setdefault(resource, sequence.id)
                    if owner != sequence.id:
                        raise DynamicHistoryViolationError("cross_sequence_resource_reuse")
            episode = DynamicEpisode(sequence.id, episode_id, ordinal, prior_attempts)
            specification = AttemptSpecification(
                RunId.from_digest(
                    canonical_digest(
                        {"sequence_id": str(sequence.id), "episode_id": str(episode_id)}
                    )
                ),
                sequence.assignment_id,
                sequence.assignment_plan_hash,
            )
            with self._lock:
                self._episode_runs[(sequence.id, episode_id)] = specification.run_id
            return ProvisionedEpisode(
                episode,
                specification,
                resources,
            )

        return authority.use_resolution(sequence.assignment_id, provision)

    def record_episode_terminal(
        self,
        sequence_id: SequenceId,
        episode_id: EpisodeId,
        terminal: AttemptTerminal,
    ) -> DynamicSequenceTerminal | None:
        """Retain every assigned outcome and advance without replacement."""

        with self._lock:
            sequence = self._require_sequence(sequence_id)
            ordinal = self._episode_index[sequence_id]
            if ordinal >= len(sequence.episode_ids) or sequence.episode_ids[ordinal] != episode_id:
                raise DynamicHistoryViolationError("dynamic_episode_terminal_out_of_order")
            expected_run = self._episode_runs.get((sequence_id, episode_id))
            if expected_run is None or terminal.run_id != expected_run:
                raise DynamicHistoryViolationError("dynamic_episode_terminal_run_mismatch")
            if self._ledger.attempt_terminal_for(terminal.attempt_id) != terminal:
                raise DynamicHistoryViolationError("dynamic_episode_terminal_not_authoritative")
            if not terminal.evidence_refs:
                raise DynamicHistoryViolationError("dynamic_terminal_evidence_required")
            if str(terminal.attempt_id) in self._terminal_attempts[sequence_id]:
                raise DynamicHistoryViolationError("dynamic_episode_terminal_duplicate")
            self._terminal_attempts[sequence_id].append(str(terminal.attempt_id))
            self._terminals[sequence_id].extend(terminal.evidence_refs)
            self._episode_index[sequence_id] += 1
            if self._episode_index[sequence_id] != len(sequence.episode_ids):
                return None
            sequence_terminal = DynamicSequenceTerminal(
                sequence_id,
                SequenceState.TERMINAL,
                tuple(AttemptId(value) for value in self._terminal_attempts[sequence_id]),
                tuple(self._terminals[sequence_id]),
            )
            self._ledger.append_dynamic_sequence_terminal(sequence_terminal)
            return sequence_terminal

    def record_cleanup(
        self, sequence_id: SequenceId, evidence_refs: Sequence[ArtifactRef]
    ) -> DynamicSequenceCleanup:
        with self._lock:
            sequence = self._require_sequence(sequence_id)
            if self._episode_index[sequence_id] != len(sequence.episode_ids):
                raise DynamicHistoryViolationError("dynamic_cleanup_before_terminal")
            if sequence_id in self._cleanup:
                raise DynamicHistoryViolationError("dynamic_cleanup_already_recorded")
            cleanup = DynamicSequenceCleanup(
                sequence_id, SequenceState.CLEANED_UP, tuple(evidence_refs)
            )
            self._ledger.append_dynamic_sequence_cleanup(cleanup)
            self._cleanup[sequence_id] = cleanup
            return cleanup

    def _resources(
        self, sequence: DynamicSequence, episode_id: EpisodeId
    ) -> SequenceResourceIdentity:
        root = canonical_digest(
            {
                "sequence_id": str(sequence.id),
                "assignment_id": str(sequence.assignment_id),
                "assignment_plan_hash": sequence.assignment_plan_hash,
            }
        )
        episode = canonical_digest({"root": root, "episode_id": str(episode_id)})
        return SequenceResourceIdentity(
            sequence.id,
            episode_id,
            f"graph-{root}",
            f"workspace-{episode}",
            f"cache-{episode}",
            f"output-{episode}",
            f"credential-{root}",
        )

    def _require_sequence(self, sequence_id: SequenceId) -> DynamicSequence:
        sequence = self._sequences.get(sequence_id)
        if sequence is None:
            raise DynamicHistoryViolationError("dynamic_sequence_not_enrolled")
        return sequence


class DynamicProvisioningAuthority:
    """Narrow capability that authorizes treatment-specific state only after allocation."""

    def __init__(
        self, coordinator: DynamicHistoryCoordinator, authority: ProvisioningAuthority
    ) -> None:
        self._coordinator = coordinator
        self._authority = authority

    def provision_next(self, sequence_id: SequenceId) -> ProvisionedEpisode:
        return self._coordinator._provision_next(sequence_id, self._authority)


def require_same_sequence_analysis_identity(
    identities: Sequence[SequenceAnalysisIdentity],
) -> SequenceAnalysisIdentity:
    """Reject cross-regime/stratum pooling; the sequence is the only analysis unit."""

    if not identities:
        raise AnalysisBoundaryError()
    first = identities[0]
    if any(identity != first for identity in identities[1:]):
        raise AnalysisBoundaryError()
    if first.history_mode is not HistoryMode.DYNAMIC:
        raise AnalysisBoundaryError()
    return first


def _resource_values(resources: SequenceResourceIdentity) -> tuple[str, ...]:
    return (
        resources.graph_root,
        resources.workspace,
        resources.cache_namespace,
        resources.output_namespace,
        resources.credential_namespace,
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)
