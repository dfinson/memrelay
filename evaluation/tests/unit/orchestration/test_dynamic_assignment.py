from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger
from memrelay_eval.canonical import attach_digest, canonical_bytes
from memrelay_eval.domain.entities import (
    ArtifactRef,
    AttemptTerminal,
    EnrollmentPlan,
    FrozenArtifactInput,
)
from memrelay_eval.domain.errors import DynamicHistoryViolationError, UnsupportedArmError
from memrelay_eval.domain.ids import (
    AttemptId,
    EnrollmentPlanId,
    EpisodeId,
    ExperimentId,
    SequenceId,
)
from memrelay_eval.domain.states import AttemptTerminalKind, EvaluationStratum
from memrelay_eval.orchestration.assignment import (
    AssignmentAlgorithmRegistry,
    ConcealedAssignmentService,
    FixtureBalancedBlockAlgorithm,
    seal_assignment_plan,
)
from memrelay_eval.orchestration.history import (
    DynamicHistoryCoordinator,
    DynamicHistoryProtocol,
    DynamicSequenceRequest,
    protocol_arm_contracts,
)


def _frozen(store: InMemoryArtifactStore, value: object) -> FrozenArtifactInput:
    artifact = store.put_bytes(
        canonical_bytes(attach_digest({"value": value})),
        media_type="application/json",
        classification="synthetic",
    )
    return FrozenArtifactInput(artifact, "1.0.0", "fixture")


def _coordinator(
    provisioner: object = None, arms: dict[int, object] | None = None
) -> tuple[DynamicHistoryCoordinator, str, InMemoryLedger]:
    store = InMemoryArtifactStore()
    seed = b"dynamic-sequence-fixture-seed"
    hashes = tuple(sha256(f"sequence-{index}".encode()).hexdigest() for index in range(8))
    inputs = {
        name: _frozen(store, {"fixture": name})
        for name in (
            "catalog",
            "protocol",
            "effective_configuration",
            "environment_fingerprint",
            "native_model_catalog",
            "price_tables",
            "eligibility_dispositions",
        )
    }
    inputs.update(
        {
            "assignment_algorithm": _frozen(store, {"name": "dynamic-fixture", "version": "v1"}),
            "seed_commitment": _frozen(store, sha256(seed).hexdigest()),
            "blocks": _frozen(
                store, {"blocks": [{"block_id": "fixture", "ordered_input_hashes": list(hashes)}]}
            ),
            "ordered_inputs": _frozen(store, [{"input_hash": value} for value in hashes]),
        }
    )
    document = attach_digest({"artifact_type": "enrollment", "fixture": "dynamic"})
    enrollment = EnrollmentPlan(
        EnrollmentPlanId.from_digest(str(document["digest"])),
        store.put_bytes(
            canonical_bytes(document), media_type="application/json", classification="x"
        ),
        str(document["digest"]),
        inputs,
        sha256(b"parity").hexdigest(),
        unpaid_conformance=True,
    )
    service = ConcealedAssignmentService(
        seal_assignment_plan(enrollment, store),
        store,
        seed,
        AssignmentAlgorithmRegistry((FixtureBalancedBlockAlgorithm("dynamic-fixture", "v1", 8),)),
    )
    protocol = DynamicHistoryProtocol(
        sha256(seed).hexdigest(),
        dict(enumerate(protocol_arm_contracts())) if arms is None else arms,
    )
    callback = provisioner if callable(provisioner) else None
    ledger = InMemoryLedger()
    return DynamicHistoryCoordinator(service, protocol, ledger, callback), hashes[0], ledger


def _request(input_hash: str, episode_count: int = 3) -> DynamicSequenceRequest:
    return DynamicSequenceRequest(
        SequenceId.new(),
        ExperimentId.new(),
        input_hash,
        tuple(EpisodeId.new() for _ in range(episode_count)),
        EvaluationStratum.PRODUCT,
    )


def _terminal(ledger: InMemoryLedger, run_id: object, evidence: ArtifactRef) -> AttemptTerminal:
    terminal = AttemptTerminal(
        AttemptId.new(),
        run_id,  # type: ignore[arg-type]
        AttemptTerminalKind.SUCCEEDED,
        datetime.now(UTC),
        "fixture terminal",
        (evidence,),
    )
    ledger.append_attempt_terminal(terminal)
    return terminal


def test_entire_sequence_is_assigned_once_before_first_episode_and_remains_opaque() -> None:
    observed_contracts: list[str] = []

    def provision(contract: object, resources: object) -> None:
        observed_contracts.append(contract.code)  # type: ignore[attr-defined]
        assert resources.graph_root.startswith("graph-")  # type: ignore[attr-defined]

    coordinator, input_hash, ledger = _coordinator(provision)
    sequence = coordinator.enroll(_request(input_hash))
    authority = coordinator.provisioning_authority()

    first = authority.provision_next(sequence.id)
    evidence = ArtifactRef.from_bytes(b"first-terminal")
    first_terminal = _terminal(ledger, first.executor_specification.run_id, evidence)
    assert (
        coordinator.record_episode_terminal(sequence.id, first.episode.episode_id, first_terminal)
        is None
    )
    second = authority.provision_next(sequence.id)

    assert first.executor_specification.assignment_id == second.executor_specification.assignment_id
    assert second.episode.prior_terminal_attempt_ids == (first_terminal.attempt_id,)
    assert first.resources.graph_root == second.resources.graph_root
    assert first.resources.workspace != second.resources.workspace
    assert first.resources.cache_namespace != second.resources.cache_namespace
    assert "N0" not in repr(first.executor_specification)
    assert observed_contracts


def test_out_of_order_completion_and_duplicate_or_post_terminal_provisioning_fail_closed() -> None:
    coordinator, input_hash, ledger = _coordinator()
    sequence = coordinator.enroll(_request(input_hash, episode_count=1))
    authority = coordinator.provisioning_authority()
    first = authority.provision_next(sequence.id)
    evidence = ArtifactRef.from_bytes(b"terminal")

    with pytest.raises(DynamicHistoryViolationError) as duplicate_exposure:
        authority.provision_next(sequence.id)
    assert duplicate_exposure.value.code == "dynamic_episode_already_exposed"

    with pytest.raises(DynamicHistoryViolationError) as out_of_order:
        coordinator.record_episode_terminal(
            sequence.id,
            EpisodeId.new(),
            _terminal(ledger, first.executor_specification.run_id, evidence),
        )
    assert out_of_order.value.code == "dynamic_episode_terminal_out_of_order"

    terminal = coordinator.record_episode_terminal(
        sequence.id,
        first.episode.episode_id,
        _terminal(ledger, first.executor_specification.run_id, evidence),
    )
    assert terminal is not None
    with pytest.raises(DynamicHistoryViolationError) as post_terminal:
        authority.provision_next(sequence.id)
    assert post_terminal.value.code == "dynamic_sequence_already_terminal"


def test_unsupported_protocol_slot_fails_before_treatment_provisioning() -> None:
    calls: list[object] = []
    coordinator, input_hash, _ledger = _coordinator(
        lambda contract, resources: calls.append(contract), {99: protocol_arm_contracts()[0]}
    )
    sequence = coordinator.enroll(_request(input_hash))

    with pytest.raises(UnsupportedArmError):
        coordinator.provisioning_authority().provision_next(sequence.id)
    assert calls == []


def test_resource_identities_are_unique_between_sequences_even_when_arm_slots_match() -> None:
    coordinator, input_hash, _ledger = _coordinator()
    first = coordinator.enroll(_request(input_hash, episode_count=1))
    second = coordinator.enroll(_request(input_hash, episode_count=1))
    authority = coordinator.provisioning_authority()

    first_episode = authority.provision_next(first.id)
    second_episode = authority.provision_next(second.id)

    assert first_episode.resources.graph_root != second_episode.resources.graph_root
    assert first_episode.resources.workspace != second_episode.resources.workspace
    assert first_episode.resources.output_namespace != second_episode.resources.output_namespace
