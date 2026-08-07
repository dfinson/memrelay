from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from threading import Barrier

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.canonical import attach_digest, canonical_bytes
from memrelay_eval.domain.entities import EnrollmentPlan, FrozenArtifactInput
from memrelay_eval.domain.errors import (
    AssignmentResolutionDeniedError,
    InvalidAssignmentAlgorithmError,
    SeedCommitmentMismatchError,
)
from memrelay_eval.domain.ids import EnrollmentPlanId, ExperimentId, RunId
from memrelay_eval.orchestration.assignment import (
    AssignmentAlgorithmRegistry,
    AssignmentRequest,
    ConcealedAssignmentService,
    FixtureBalancedBlockAlgorithm,
    seal_assignment_plan,
)

_DEFAULT_ALGORITHM = object()


def _frozen_input(store: InMemoryArtifactStore, value: object) -> FrozenArtifactInput:
    artifact = store.put_bytes(
        canonical_bytes(attach_digest({"value": value})),
        media_type="application/json",
        classification="synthetic",
    )
    return FrozenArtifactInput(artifact, "1.0.0", "fixture")


def _plan(
    store: InMemoryArtifactStore,
    *,
    algorithm: object = _DEFAULT_ALGORITHM,
    seed_material: bytes = b"fixture-assignment-seed",
    ordered_input_hashes: tuple[str, ...] | None = None,
) -> tuple[EnrollmentPlan, tuple[str, ...], bytes]:
    hashes = ordered_input_hashes or tuple(
        sha256(f"input-{index}".encode()).hexdigest() for index in range(4)
    )
    algorithm_value = (
        {"name": "balanced_block", "version": "fixture-v1"}
        if algorithm is _DEFAULT_ALGORITHM
        else algorithm
    )
    inputs = {
        name: _frozen_input(store, {"fixture": name})
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
            "assignment_algorithm": _frozen_input(store, algorithm_value),
            "seed_commitment": _frozen_input(store, sha256(seed_material).hexdigest()),
            "blocks": _frozen_input(
                store,
                {
                    "blocks": [
                        {
                            "block_id": "block_0001",
                            "ordered_input_hashes": list(hashes),
                        }
                    ]
                },
            ),
            "ordered_inputs": _frozen_input(
                store, [{"input_hash": input_hash} for input_hash in hashes]
            ),
        }
    )
    document = attach_digest({"artifact_type": "pre_enrollment_plan", "inputs": "fixture"})
    artifact = store.put_bytes(
        canonical_bytes(document),
        media_type="application/json",
        classification="synthetic",
    )
    return (
        EnrollmentPlan(
            EnrollmentPlanId.from_digest(str(document["digest"])),
            artifact,
            str(document["digest"]),
            inputs,
            sha256(b"parity").hexdigest(),
            unpaid_conformance=True,
        ),
        hashes,
        seed_material,
    )


def _registry() -> AssignmentAlgorithmRegistry:
    return AssignmentAlgorithmRegistry(
        (FixtureBalancedBlockAlgorithm("balanced_block", "fixture-v1", slot_count=2),)
    )


def test_assignment_seal_reproducibly_commits_every_frozen_input() -> None:
    store = InMemoryArtifactStore()
    plan, hashes, seed_material = _plan(store)

    first = seal_assignment_plan(plan, store)
    second = seal_assignment_plan(plan, store)
    service = ConcealedAssignmentService(first, store, seed_material, _registry())
    assignment = service.assign(AssignmentRequest(ExperimentId.new(), RunId.new(), hashes[0]))
    document = json.loads(store.open_verified(first.artifact))

    assert first == second
    assert len(first.assignment_plan_hash) == 64
    assert first.ordered_input_hashes == hashes
    assert assignment.assignment_plan_hash == first.assignment_plan_hash
    assert {
        "algorithm",
        "seed_commitment",
        "blocks",
        "ordered_input_hashes",
        "assignment_plan_hash",
    }.issubset(document)
    assert "treatment" not in json.dumps(document).casefold()
    assert (
        assignment.id
        == service.assign(
            AssignmentRequest(assignment.experiment_id, assignment.run_id, hashes[0])
        ).id
    )


@pytest.mark.parametrize(
    "algorithm",
    (
        None,
        {},
        {"name": "balanced_block"},
    ),
)
def test_missing_algorithm_version_fails_closed(algorithm: object) -> None:
    store = InMemoryArtifactStore()
    if algorithm is None:
        plan, _, _ = _plan(store, algorithm={})
    else:
        plan, _, _ = _plan(store, algorithm=algorithm)

    with pytest.raises(InvalidAssignmentAlgorithmError):
        seal_assignment_plan(plan, store)


@pytest.mark.parametrize(
    "algorithm",
    (
        {"name": "unknown", "version": "fixture-v1"},
        {"name": "balanced_block", "version": "unknown"},
    ),
)
def test_unregistered_algorithm_version_fails_closed(algorithm: object) -> None:
    store = InMemoryArtifactStore()
    plan, _, seed_material = _plan(store, algorithm=algorithm)

    with pytest.raises(InvalidAssignmentAlgorithmError):
        ConcealedAssignmentService(
            seal_assignment_plan(plan, store), store, seed_material, _registry()
        )


def test_seed_mismatch_and_unordered_or_unknown_input_fail_closed() -> None:
    store = InMemoryArtifactStore()
    plan, hashes, seed_material = _plan(store)
    sealed = seal_assignment_plan(plan, store)

    with pytest.raises(SeedCommitmentMismatchError):
        ConcealedAssignmentService(sealed, store, b"wrong-seed", _registry())
    service = ConcealedAssignmentService(sealed, store, seed_material, _registry())
    with pytest.raises(InvalidAssignmentAlgorithmError):
        service.assign(AssignmentRequest(ExperimentId.new(), RunId.new(), "0" * 64))
    with pytest.raises(InvalidAssignmentAlgorithmError):
        service.assign(AssignmentRequest(ExperimentId.new(), RunId.new(), hashes[0].upper()))


def test_resolution_requires_the_narrow_provisioning_capability() -> None:
    store = InMemoryArtifactStore()
    plan, hashes, seed_material = _plan(store)
    service = ConcealedAssignmentService(
        seal_assignment_plan(plan, store), store, seed_material, _registry()
    )
    assignment = service.assign(AssignmentRequest(ExperimentId.new(), RunId.new(), hashes[0]))

    with pytest.raises(AssignmentResolutionDeniedError):
        service.resolve_for_provisioning(assignment.id, object())

    resolution = service.provisioning_authority().resolve(assignment.id)
    assert resolution.assignment_id == assignment.id
    assert not hasattr(resolution, "treatment")
    assert "fixture" not in repr(resolution).casefold()


def test_fixture_algorithm_is_balanced_within_each_frozen_block() -> None:
    store = InMemoryArtifactStore()
    plan, hashes, seed_material = _plan(store)
    service = ConcealedAssignmentService(
        seal_assignment_plan(plan, store), store, seed_material, _registry()
    )
    assignments = tuple(
        service.assign(AssignmentRequest(ExperimentId.new(), RunId.new(), input_hash))
        for input_hash in hashes
    )

    assert service.provisioning_authority().verify_balance(assignments) is True


def test_seal_consumes_story_1_5_style_ordered_inputs_and_run_order_blocks() -> None:
    store = InMemoryArtifactStore()
    plan, _, seed_material = _plan(store)
    input_ids = ("input_0001", "input_0002", "input_0003", "input_0004")
    inputs = dict(plan.inputs)
    inputs["ordered_inputs"] = _frozen_input(
        store,
        [
            {"input_id": input_id, "task_identity": sha256(input_id.encode()).hexdigest()}
            for input_id in input_ids
        ],
    )
    inputs["blocks"] = _frozen_input(
        store,
        {"blocks": [{"block_id": "block_0001", "run_order": list(input_ids)}]},
    )
    plan = replace(plan, inputs=inputs)

    sealed = seal_assignment_plan(plan, store)
    service = ConcealedAssignmentService(sealed, store, seed_material, _registry())

    assert len(sealed.ordered_input_hashes) == len(input_ids)
    assert (
        service.assign(
            AssignmentRequest(ExperimentId.new(), RunId.new(), sealed.ordered_input_hashes[0])
        ).assignment_plan_hash
        == sealed.assignment_plan_hash
    )


def test_concurrent_duplicate_assignment_is_idempotent_and_deterministic() -> None:
    store = InMemoryArtifactStore()
    plan, hashes, seed_material = _plan(store)
    service = ConcealedAssignmentService(
        seal_assignment_plan(plan, store), store, seed_material, _registry()
    )
    request = AssignmentRequest(ExperimentId.new(), RunId.new(), hashes[0])
    barrier = Barrier(8)

    def assign() -> str:
        barrier.wait(timeout=2)
        return str(service.assign(request).id)

    with ThreadPoolExecutor(max_workers=8) as executor:
        assigned_ids = tuple(executor.map(lambda _index: assign(), range(8)))

    assert len(set(assigned_ids)) == 1
