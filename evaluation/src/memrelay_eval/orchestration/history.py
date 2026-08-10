"""Protocol-defined, arm-concealed dynamic-history sequence orchestration.

Only this module has treatment-aware state.  Generic execution receives an
``AttemptSpecification`` and remains unable to observe arm meanings.

The same module also owns Story 2.8 controlled-history restoration: building one
frozen golden checkpoint per protocol/stratum before assignment exposure, and
restoring it byte-identically into every fresh attempt-local root.  Controlled and
dynamic histories are separate, never-pooled regimes that happen to share this file
because both are treatment-aware history orchestration, matching Story 2.9's layout.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock

from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.assignment import AttemptSpecification
from memrelay_eval.domain.entities import (
    ArtifactManifest,
    ArtifactRef,
    AttemptTerminal,
    ControlledAnalysisIdentity,
    ControlledHistoryBundle,
    ControlledHistoryItem,
    ControlledRestoreManifest,
    DynamicEpisode,
    DynamicSequence,
    DynamicSequenceCleanup,
    DynamicSequenceTerminal,
)
from memrelay_eval.domain.errors import (
    AnalysisBoundaryError,
    ControlledEstimandPoolingError,
    ControlledHistoryMutationError,
    ControlledHistoryViolationError,
    ControlledRestoreMismatchError,
    DynamicHistoryViolationError,
    UnsupportedArmError,
)
from memrelay_eval.domain.ids import (
    AttemptId,
    EpisodeId,
    ExperimentId,
    HistoryId,
    ProtocolId,
    RetentionPolicyId,
    RunId,
    SequenceId,
)
from memrelay_eval.domain.ports import ArtifactStorePort, LedgerPort, TreatmentPort
from memrelay_eval.domain.states import ArtifactScope, EvaluationStratum, HistoryMode, SequenceState
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


# ---------------------------------------------------------------------------
# Story 2.8: controlled immutable history restoration.
#
# A controlled history is the opposite of a dynamic sequence: its bytes are frozen
# once, before any assignment exposure, and every arm must restore the identical
# bytes into a fresh attempt-local root.  No episode may consume a prior attempt's
# outcome and a failed restore is never repaired; a retry (governed entirely by the
# existing Story 1.7 ``RetryAuthorizer``/``retry_eligibility_denial_code`` policy)
# only ever gets a brand-new attempt ID and a brand-new root.
# ---------------------------------------------------------------------------


class ControlledHistoryBuilder:
    """Builds the one frozen golden checkpoint per protocol/stratum, never mid-trial.

    This is deliberately a nonstudy operation: it never touches assignment, exposure,
    or any treatment-aware state.  It only freezes immutable CAS bytes that every arm
    will later restore identically.
    """

    def __init__(self, artifact_store: ArtifactStorePort) -> None:
        self._artifact_store = artifact_store
        self._bundles: dict[HistoryId, ControlledHistoryBundle] = {}
        self._lock = Lock()

    def build_golden_checkpoint(
        self,
        history_id: HistoryId,
        protocol_id: ProtocolId,
        stratum: EvaluationStratum,
        experiment_id: ExperimentId,
        items: Sequence[ControlledHistoryItem],
    ) -> ControlledHistoryBundle:
        """Freeze ``items`` as the sole golden checkpoint for ``history_id``.

        Calling this again for the same ``history_id`` with identical bytes is an
        idempotent no-op; calling it with different bytes is a protocol-freeze
        mutation and is rejected closed.
        """

        record = {
            "schema_version": "1.0.0",
            "history_id": str(history_id),
            "protocol_id": str(protocol_id),
            "mode": HistoryMode.CONTROLLED.value,
            "stratum": stratum.value,
            "ordered_items": [item.to_record() for item in items],
        }
        content_sha256 = canonical_digest(record)
        bundle = ControlledHistoryBundle(
            history_id, protocol_id, stratum, tuple(items), content_sha256
        )
        with self._lock:
            existing = self._bundles.get(history_id)
            if existing is not None:
                if existing.content_sha256 != bundle.content_sha256:
                    raise ControlledHistoryMutationError()
                return existing
        # Evidence is frozen (immutably content-addressed, so re-attempts are safe)
        # before the bundle is ever committed as authoritative; a failed evidence
        # write must never leave an in-memory "frozen" bundle with no CAS record.
        self._freeze_evidence(bundle, experiment_id)
        with self._lock:
            existing = self._bundles.get(history_id)
            if existing is not None:
                if existing.content_sha256 != bundle.content_sha256:
                    raise ControlledHistoryMutationError()
                return existing
            self._bundles[history_id] = bundle
        return bundle

    def frozen_bundle(self, history_id: HistoryId) -> ControlledHistoryBundle | None:
        with self._lock:
            return self._bundles.get(history_id)

    def _freeze_evidence(
        self, bundle: ControlledHistoryBundle, experiment_id: ExperimentId
    ) -> None:
        payload = canonical_bytes(bundle.to_record())
        artifact = self._artifact_store.put_bytes(
            payload,
            media_type="application/json",
            classification="controlled_history_bundle",
        )
        manifest = ArtifactManifest(
            artifact_id=artifact.artifact_id,
            kind="controlled_history_bundle",
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            media_type="application/json",
            created_at=datetime.now(UTC),
            producer_component="memrelay_eval.orchestration.history",
            producer_version=bundle.schema_version,
            classification="controlled_history_bundle",
            contains_secrets=False,
            source_artifact_ids=tuple(item.artifact.artifact_id for item in bundle.ordered_items),
            retention_policy_id=RetentionPolicyId.new(),
            encryption=None,
            scope=ArtifactScope.EXPERIMENT,
            experiment_id=experiment_id,
        )
        self._artifact_store.write_manifest(manifest)


class ControlledHistoryCoordinator:
    """Restores one frozen bundle into a fresh attempt-local root, proving parity."""

    def __init__(
        self,
        builder: ControlledHistoryBuilder,
        artifact_store: ArtifactStorePort,
        ledger: LedgerPort,
    ) -> None:
        self._builder = builder
        self._artifact_store = artifact_store
        self._ledger = ledger
        # Restore-once-per-attempt is enforced by this single trusted instance, the
        # same single-control-process-owned-authority pattern already used by the
        # sibling `DynamicHistoryCoordinator._provisioned_episodes` (Story 2.9) and by
        # `ConcealedAssignmentService._records` (Story 1.6): exactly one coordinator
        # instance is constructed per experiment/control process (AD-08) and is the
        # sole caller of `restore()`. Constructing a second coordinator instance over
        # the same ledger/store is a composition-root misuse, not a supported entry
        # point, and is called out as a disclosed residual risk in the story record.
        self._restored_attempts: set[AttemptId] = set()
        self._lock = Lock()

    async def restore(
        self,
        attempt_id: AttemptId,
        run_id: RunId,
        history_id: HistoryId,
        stratum: EvaluationStratum,
        handle: object,
        treatment: TreatmentPort,
    ) -> ControlledRestoreManifest:
        """Restore the frozen bundle and block exposure on any divergence.

        Every call re-verifies bytes from scratch on the caller's fresh root; a
        retry (a new ``attempt_id``, per AD-11/AD-18) can never repair a genuinely
        divergent source, because it re-runs this exact same closed verification.
        """

        bundle = self._builder.frozen_bundle(history_id)
        if bundle is None:
            raise ControlledHistoryViolationError("controlled_history_not_frozen")
        if bundle.stratum is not stratum:
            raise ControlledHistoryViolationError("controlled_history_stratum_mismatch")
        with self._lock:
            if attempt_id in self._restored_attempts:
                raise ControlledHistoryViolationError("controlled_restore_already_consumed")
            self._restored_attempts.add(attempt_id)
        await treatment.restore_history(handle, bundle)
        collected = await treatment.collect_state(handle)
        restored_content_sha256 = self._verify_restoration(bundle, collected)
        parity_hash = canonical_digest(
            {
                "history_id": str(history_id),
                "bundle_content_sha256": bundle.content_sha256,
                "restored_content_sha256": restored_content_sha256,
            }
        )
        manifest = ControlledRestoreManifest(
            attempt_id,
            history_id,
            bundle.content_sha256,
            restored_content_sha256,
            parity_hash,
            datetime.now(UTC),
        )
        self._record_manifest_evidence(manifest, run_id)
        return manifest

    def analysis_identity(self, history_id: HistoryId) -> ControlledAnalysisIdentity:
        bundle = self._builder.frozen_bundle(history_id)
        if bundle is None:
            raise ControlledHistoryViolationError("controlled_history_not_frozen")
        return ControlledAnalysisIdentity(
            HistoryMode.CONTROLLED, bundle.stratum, bundle.content_sha256
        )

    def _verify_restoration(
        self, bundle: ControlledHistoryBundle, collected: Sequence[ArtifactRef]
    ) -> str:
        expected = bundle.ordered_items
        if len(collected) != len(expected):
            raise ControlledRestoreMismatchError(
                "controlled_restore_missing_all_items"
                if not collected
                else "controlled_restore_item_count_mismatch"
            )
        for item, actual in zip(expected, collected, strict=True):
            if not isinstance(actual, ArtifactRef):
                raise ControlledRestoreMismatchError("controlled_restore_content_mismatch")
            if (
                actual.sha256 != item.artifact.sha256
                or actual.size_bytes != item.artifact.size_bytes
            ):
                raise ControlledRestoreMismatchError("controlled_restore_content_mismatch")
        return canonical_digest(
            {
                "schema_version": bundle.schema_version,
                "history_id": str(bundle.history_id),
                "protocol_id": str(bundle.protocol_id),
                "mode": HistoryMode.CONTROLLED.value,
                "stratum": bundle.stratum.value,
                "ordered_items": [
                    {**item.to_record(), "artifact_sha256": actual.sha256}
                    for item, actual in zip(expected, collected, strict=True)
                ],
            }
        )

    def _record_manifest_evidence(self, manifest: ControlledRestoreManifest, run_id: RunId) -> None:
        from memrelay_eval.domain.entities import ArtifactLink

        payload = canonical_bytes(manifest.to_record())
        artifact = self._artifact_store.put_bytes(
            payload,
            media_type="application/json",
            classification="controlled_restore_manifest",
        )
        self._ledger.append_artifact_link(
            ArtifactLink(
                artifact,
                purpose="controlled_restore_manifest",
                run_id=run_id,
                attempt_id=manifest.attempt_id,
            )
        )


def require_same_controlled_analysis_identity(
    identities: Sequence[ControlledAnalysisIdentity],
) -> ControlledAnalysisIdentity:
    """Reject cross-stratum/history pooling; only one controlled identity may aggregate."""

    if not identities:
        raise ControlledEstimandPoolingError()
    first = identities[0]
    if any(identity != first for identity in identities[1:]):
        raise ControlledEstimandPoolingError()
    if first.history_mode is not HistoryMode.CONTROLLED:
        raise ControlledEstimandPoolingError()
    return first


def require_no_cross_regime_pooling(
    controlled_identities: Sequence[ControlledAnalysisIdentity],
    dynamic_identities: Sequence[SequenceAnalysisIdentity],
) -> None:
    """Reject any query that would combine controlled and dynamic outcomes."""

    if controlled_identities and dynamic_identities:
        raise ControlledEstimandPoolingError()
