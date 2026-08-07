from __future__ import annotations

import json
import socket
from hashlib import sha256

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.domain.entities import EnrollmentPlan, FrozenArtifactInput
from memrelay_eval.domain.errors import DurableConformanceRequiredError
from memrelay_eval.domain.ids import EnrollmentPlanId, ExperimentId, RunId
from memrelay_eval.orchestration.assignment import (
    AssignmentAlgorithmRegistry,
    AssignmentRequest,
    ConcealedAssignmentService,
    FixtureBalancedBlockAlgorithm,
    seal_assignment_plan,
)
from memrelay_eval.orchestration.exposure import ExposureRecorder


def _input(store: InMemoryArtifactStore, value: object) -> FrozenArtifactInput:
    import memrelay_eval.canonical as canonical

    artifact = store.put_bytes(
        canonical.canonical_bytes(canonical.attach_digest({"value": value})),
        media_type="application/json",
        classification="synthetic",
    )
    return FrozenArtifactInput(artifact, "1.0.0", "fixture")


def _service() -> tuple[ConcealedAssignmentService, str]:
    store = InMemoryArtifactStore()
    seed = b"contract-seed"
    input_hash = sha256(b"contract-input").hexdigest()
    inputs = {
        name: _input(store, {"fixture": name})
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
            "assignment_algorithm": _input(
                store, {"name": "balanced_block", "version": "fixture-v1"}
            ),
            "seed_commitment": _input(store, sha256(seed).hexdigest()),
            "blocks": _input(
                store,
                {
                    "blocks": [
                        {"block_id": "block_0001", "ordered_input_hashes": [input_hash]}
                    ]
                },
            ),
            "ordered_inputs": _input(store, [{"input_hash": input_hash}]),
        }
    )
    import memrelay_eval.canonical as canonical

    document = canonical.attach_digest({"artifact_type": "pre_enrollment_plan", "fixture": True})
    artifact = store.put_bytes(
        canonical.canonical_bytes(document),
        media_type="application/json",
        classification="synthetic",
    )
    plan = EnrollmentPlan(
        EnrollmentPlanId.from_digest(str(document["digest"])),
        artifact,
        str(document["digest"]),
        inputs,
        sha256(b"parity").hexdigest(),
        unpaid_conformance=True,
    )
    registry = AssignmentAlgorithmRegistry(
        (FixtureBalancedBlockAlgorithm("balanced_block", "fixture-v1", slot_count=2),)
    )
    service = ConcealedAssignmentService(seal_assignment_plan(plan, store), store, seed, registry)
    return service, input_hash


def test_ordinary_assignment_manifest_is_redacted_and_json_serializable() -> None:
    service, input_hash = _service()
    assignment = service.assign(AssignmentRequest(ExperimentId.new(), RunId.new(), input_hash))

    payload = json.dumps(service.ordinary_manifest(assignment), sort_keys=True)
    forbidden = ("treatment", "control", "variant", "fixture-v1", "resolution", "slot")
    assert all(term not in payload.casefold() for term in forbidden)
    assert assignment.id in payload


def test_fixture_ports_make_no_network_calls_and_never_authorize_study_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", no_network)
    service, input_hash = _service()
    assignment = service.assign(AssignmentRequest(ExperimentId.new(), RunId.new(), input_hash))
    with pytest.raises(DurableConformanceRequiredError):
        service.require_durable_execution()
    with pytest.raises(DurableConformanceRequiredError):
        ExposureRecorder(InMemoryLedger(), InMemoryTelemetry()).require_durable_execution()
    assert assignment.id
