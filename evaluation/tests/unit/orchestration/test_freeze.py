from __future__ import annotations

import json
import socket
from collections.abc import Mapping
from dataclasses import replace

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.canonical import attach_digest, canonical_bytes
from memrelay_eval.domain.entities import EnrollmentPlanLineage, FrozenArtifactInput
from memrelay_eval.domain.environment import EnvironmentFingerprint
from memrelay_eval.domain.errors import (
    CatalogEligibilityBindingError,
    DomainError,
    EnrollmentLineageError,
    EnvironmentStratumChangedError,
    FrozenInputMutationError,
    InvalidConfigurationError,
)
from memrelay_eval.domain.ids import AssignmentId, AttemptId, EnrollmentPlanId
from memrelay_eval.orchestration.blocks import (
    build_environment_blocks,
    require_same_environment_stratum,
)
from memrelay_eval.orchestration.configuration import (
    persist_effective_configuration,
    resolve_effective_configuration,
)
from memrelay_eval.orchestration.freeze import (
    AssignedEnrollmentPlans,
    EnrollmentFreezeRequest,
    assert_assigned_plan_unchanged,
    freeze_enrollment_inputs,
    require_successor_lineage,
)


def _source_input(
    store: InMemoryArtifactStore, artifact_type: str, provenance: str
) -> FrozenArtifactInput:
    payload = canonical_bytes(
        attach_digest(
            {
                "artifact_type": artifact_type,
                "schema_version": "1.0.0",
                "revision": "fixture-v1",
            }
        )
    )
    return FrozenArtifactInput(
        store.put_bytes(payload, media_type="application/json", classification="synthetic"),
        schema_version="1.0.0",
        provenance=provenance,
    )


def _eligibility_evaluation(
    task_identity: str, *, disposition: str = "eligible"
) -> dict[str, object]:
    codes = [] if disposition == "eligible" else ["CANARY_CONTAMINATION"]
    return attach_digest(
        {
            "schema_version": "1.0.0",
            "catalog_id": "cat_" + "a" * 32,
            "scenario_id": "scenario_" + task_identity[:32],
            "disposition": disposition,
            "codes": codes,
            "evidence_refs": ["fixture_" + "b" * 32],
            "reviewer_roles": ["evaluation-scientist"],
            "scenario_data_classification": "synthetic",
            "fixture_sha256": {},
            "study_validity_ref": "studyvalidity_" + "c" * 32,
            "study_validity_snapshot": {},
            "unpaid_conformance": True,
        }
    )


def _catalog_input(
    store: InMemoryArtifactStore, evaluations: dict[str, Mapping[str, object]]
) -> FrozenArtifactInput:
    document = attach_digest(
        {
            "schema_version": "1.0.0",
            "catalog_id": "cat_" + "a" * 32,
            "tasks": [
                {
                    "digest": task_identity,
                    "eligibility_evaluation": evaluation,
                }
                for task_identity, evaluation in evaluations.items()
            ],
        }
    )
    payload = canonical_bytes(document)
    return FrozenArtifactInput(
        store.put_bytes(payload, media_type="application/json", classification="synthetic"),
        schema_version="1.0.0",
        provenance="catalog_compiler",
    )


def _environment(*, power_mode: str = "ac") -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        os_name="Windows",
        os_build="22631",
        cpu={"architecture": "x86_64", "logical_cores": 8},
        memory={"total_bytes": 17179869184},
        storage_class="local_ssd",
        power_mode=power_mode,
        python_version="3.13.0",
        runtime_version="cpython-3.13.0",
        process_limits={"max_workers": 1, "wall_seconds": 600},
        network_policy={"mode": "deny"},
        background_load_policy={"mode": "idle_only"},
    )


def _request(store: InMemoryArtifactStore, **changes: object) -> EnrollmentFreezeRequest:
    environment = _environment()
    task_identities = ("a" * 64, "b" * 64)
    evaluations = {
        task_identity: _eligibility_evaluation(task_identity) for task_identity in task_identities
    }
    config = persist_effective_configuration(
        resolve_effective_configuration(
            safe_defaults={
                "stage": "conformance",
                "network_policy": {"mode": "deny"},
                "timeout_seconds": 600,
            }
        ),
        store,
    )
    request = EnrollmentFreezeRequest(
        catalog=_catalog_input(store, evaluations),
        protocol=_source_input(store, "protocol_lock", "protocol_author"),
        effective_configuration=config,
        environment=environment,
        native_model_catalog=_source_input(store, "native_model_catalog", "fixture_catalog"),
        assignment_algorithm={"name": "balanced_block", "version": "1.0.0"},
        seed_commitment="a" * 64,
        blocks=build_environment_blocks(
            environment,
            [{"block_id": "block_0001", "run_order": ["input_0001", "input_0002"]}],
        ),
        ordered_inputs=(
            {"input_id": "input_0001", "task_identity": task_identities[0]},
            {"input_id": "input_0002", "task_identity": task_identities[1]},
        ),
        price_tables={
            "framework": {
                "model": "gpt-4.1-mini-2025-04-14",
                "input_microusd_per_million": 400000,
            }
        },
        eligibility_dispositions=tuple(
            {
                "task_identity": task_identity,
                "eligibility_evaluation": evaluation,
            }
            for task_identity, evaluation in evaluations.items()
        ),
    )
    return replace(request, **changes)


def test_freeze_is_deterministic_canonical_and_treatment_neutral() -> None:
    store = InMemoryArtifactStore()
    request = _request(store)
    first = freeze_enrollment_inputs(request, store)
    second = freeze_enrollment_inputs(request, store)
    payload = store.open_verified(first.artifact)
    document = json.loads(payload)

    assert first == second
    assert first.id == EnrollmentPlanId.from_digest(first.document_digest)
    assert payload == canonical_bytes(document)
    assert document["parity_hash"] == first.parity_inputs_digest
    assert set(document["parity_hash_inputs"]) == set(first.inputs)
    assert "treatment" not in payload.decode("utf-8").casefold()
    assert first.unpaid_conformance is True


def test_reordered_ordered_inputs_change_plan_and_parity_identity() -> None:
    first_store = InMemoryArtifactStore()
    first = freeze_enrollment_inputs(_request(first_store), first_store)
    second_store = InMemoryArtifactStore()
    original = _request(second_store)
    second = freeze_enrollment_inputs(
        replace(original, ordered_inputs=tuple(reversed(original.ordered_inputs))),
        second_store,
    )

    assert first.id != second.id
    assert first.parity_inputs_digest != second.parity_inputs_digest


def test_environment_fingerprint_has_all_dimensions_and_changed_stratum_blocks() -> None:
    original = _environment()
    changed = _environment(power_mode="battery")
    blocks = build_environment_blocks(original, [{"block_id": "block_0001"}])

    document = original.to_document()
    assert {
        "os",
        "cpu",
        "memory",
        "storage_class",
        "power_mode",
        "python_version",
        "runtime_version",
        "process_limits",
        "network_policy",
        "background_load_policy",
    }.issubset(document)
    assert original.stratum.id != changed.stratum.id
    with pytest.raises(EnvironmentStratumChangedError):
        require_same_environment_stratum(blocks, changed)
    store = InMemoryArtifactStore()
    with pytest.raises(EnvironmentStratumChangedError):
        freeze_enrollment_inputs(
            _request(store, environment=changed, blocks=blocks),
            store,
        )


def test_fixture_model_catalog_and_price_table_freeze_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("network access is not permitted while freezing fixtures")

    monkeypatch.setattr(socket, "create_connection", no_network)
    store = InMemoryArtifactStore()

    plan = freeze_enrollment_inputs(_request(store), store)

    assert plan.unpaid_conformance is True
    assert plan.inputs["native_model_catalog"].provenance == "fixture_catalog"
    assert plan.inputs["price_tables"].provenance == "fixture_or_observed_price_table"


def test_eligibility_bindings_are_verified_against_catalog_task_identities() -> None:
    store = InMemoryArtifactStore()
    request = _request(store)

    plan = freeze_enrollment_inputs(request, store)

    assert plan.inputs["eligibility_dispositions"].provenance == "story_1_4_disposition"


@pytest.mark.parametrize("mutation", ("digest", "disposition", "codes"))
def test_forged_eligibility_binding_is_rejected(mutation: str) -> None:
    store = InMemoryArtifactStore()
    request = _request(store)
    binding = dict(request.eligibility_dispositions[0])
    evaluation = dict(binding["eligibility_evaluation"])
    if mutation == "digest":
        evaluation["digest"] = "0" * 64
    elif mutation == "disposition":
        evaluation["disposition"] = "rejected"
    else:
        evaluation["codes"] = ["CANARY_CONTAMINATION"]
    binding["eligibility_evaluation"] = evaluation
    forged_bindings = (binding, request.eligibility_dispositions[1])

    with pytest.raises(CatalogEligibilityBindingError):
        freeze_enrollment_inputs(replace(request, eligibility_dispositions=forged_bindings), store)


def test_catalog_mismatch_or_tampered_catalog_digest_is_rejected() -> None:
    store = InMemoryArtifactStore()
    request = _request(store)
    identities = [item["task_identity"] for item in request.ordered_inputs]
    revised_catalog = _catalog_input(
        store,
        {
            identities[0]: _eligibility_evaluation(identities[0], disposition="rejected"),
            identities[1]: _eligibility_evaluation(identities[1]),
        },
    )

    with pytest.raises(CatalogEligibilityBindingError):
        freeze_enrollment_inputs(replace(request, catalog=revised_catalog), store)

    tampered = attach_digest({"schema_version": "1.0.0", "catalog_id": "catalog", "tasks": []})
    tampered["digest"] = "0" * 64
    malformed_catalog = FrozenArtifactInput(
        store.put_bytes(
            canonical_bytes(tampered),
            media_type="application/json",
            classification="synthetic",
        ),
        schema_version="1.0.0",
        provenance="catalog_compiler",
    )
    with pytest.raises(CatalogEligibilityBindingError):
        freeze_enrollment_inputs(replace(request, catalog=malformed_catalog), store)


@pytest.mark.parametrize(
    "input_name",
    (
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
    ),
)
def test_every_frozen_input_mutation_is_rejected_after_assignment(input_name: str) -> None:
    store = InMemoryArtifactStore()
    plan = freeze_enrollment_inputs(_request(store), store)
    replacement = _source_input(store, f"replacement_{input_name}", "replacement")
    changed_inputs = dict(plan.inputs)
    changed_inputs[input_name] = replacement
    candidate = replace(
        plan,
        id=EnrollmentPlanId.from_digest("c" * 64),
        document_digest="c" * 64,
        inputs=changed_inputs,
    )
    assignments = AssignedEnrollmentPlans()
    assignment_id = AssignmentId.new()
    assignments.bind(assignment_id, plan)

    with pytest.raises(FrozenInputMutationError) as error:
        assignments.verify(assignment_id, candidate)

    assert error.value.field == input_name


def test_same_identity_cannot_mask_a_forged_assigned_plan_mutation() -> None:
    store = InMemoryArtifactStore()
    plan = freeze_enrollment_inputs(_request(store), store)
    changed_inputs = dict(plan.inputs)
    changed_inputs["seed_commitment"] = _source_input(store, "seed_commitment", "replacement")
    forged = replace(plan, inputs=changed_inputs)

    with pytest.raises(FrozenInputMutationError) as error:
        assert_assigned_plan_unchanged(plan, forged)

    assert error.value.field == "seed_commitment"


def test_price_token_metrics_are_not_misclassified_as_credentials() -> None:
    store = InMemoryArtifactStore()
    request = _request(
        store,
        price_tables={
            "framework": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "microusd_per_token": 4,
            }
        },
    )

    assert freeze_enrollment_inputs(request, store).inputs["price_tables"].provenance == (
        "fixture_or_observed_price_table"
    )


def test_configuration_successor_requires_attempt_lineage_and_cannot_replace_assignment() -> None:
    store = InMemoryArtifactStore()
    plan = freeze_enrollment_inputs(_request(store), store)
    replacement_config = _source_input(store, "effective_configuration", "effective_configuration")
    replacement_inputs = dict(plan.inputs)
    replacement_inputs["effective_configuration"] = replacement_config
    successor = replace(
        plan,
        id=EnrollmentPlanId.from_digest("d" * 64),
        document_digest="d" * 64,
        inputs=replacement_inputs,
        lineage=EnrollmentPlanLineage(
            plan.id,
            "attempt_configuration_revision",
            AttemptId.new(),
        ),
    )

    require_successor_lineage(plan, successor)
    with pytest.raises(FrozenInputMutationError):
        assert_assigned_plan_unchanged(plan, successor)
    with pytest.raises(EnrollmentLineageError):
        require_successor_lineage(
            plan,
            replace(successor, lineage=None),
        )


def test_treatment_revealing_input_is_rejected_before_sealing() -> None:
    store = InMemoryArtifactStore()
    request = _request(store, assignment_algorithm={"treatment": "control"})

    with pytest.raises(DomainError):
        freeze_enrollment_inputs(request, store)


@pytest.mark.parametrize("path_key", ("Path", "PATH", "Workspace_Root", "ROOT_PATH"))
@pytest.mark.parametrize(
    "surface",
    ("catalog", "protocol", "effective_configuration", "native_model_catalog"),
)
def test_mixed_case_mutable_paths_are_rejected_from_all_verified_frozen_artifacts(
    path_key: str, surface: str
) -> None:
    store = InMemoryArtifactStore()
    request = _request(store)
    source = (
        request.effective_configuration.to_frozen_input()
        if surface == "effective_configuration"
        else getattr(request, surface)
    )
    document = json.loads(store.open_verified(source.artifact))
    document[path_key] = "mutable"
    document = attach_digest(document)
    replacement = FrozenArtifactInput(
        store.put_bytes(
            canonical_bytes(document),
            media_type="application/json",
            classification="synthetic",
        ),
        schema_version=source.schema_version,
        provenance=source.provenance,
    )
    if surface == "effective_configuration":
        request = replace(
            request,
            effective_configuration=replace(
                request.effective_configuration, artifact=replacement.artifact
            ),
        )
    else:
        request = replace(request, **{surface: replacement})

    with pytest.raises(CatalogEligibilityBindingError):
        freeze_enrollment_inputs(request, store)


@pytest.mark.parametrize("path_key", ("Path", "PATH", "Workspace_Root", "ROOT_PATH"))
@pytest.mark.parametrize(
    "surface",
    (
        "assignment_algorithm",
        "blocks",
        "ordered_inputs",
        "price_tables",
        "eligibility_dispositions",
    ),
)
def test_mixed_case_mutable_paths_are_rejected_across_frozen_input_surfaces(
    path_key: str, surface: str
) -> None:
    store = InMemoryArtifactStore()
    request = _request(store)
    if surface == "assignment_algorithm":
        request = replace(request, assignment_algorithm={path_key: "mutable"})
    elif surface == "blocks":
        request = replace(
            request,
            blocks=build_environment_blocks(
                request.environment, [{"block_id": "block_0001", path_key: "mutable"}]
            ),
        )
    elif surface == "ordered_inputs":
        request = replace(
            request,
            ordered_inputs=(
                {**request.ordered_inputs[0], path_key: "mutable"},
                request.ordered_inputs[1],
            ),
        )
    elif surface == "price_tables":
        request = replace(request, price_tables={path_key: "mutable"})
    else:
        binding = dict(request.eligibility_dispositions[0])
        evaluation = dict(binding["eligibility_evaluation"])
        evaluation[path_key] = "mutable"
        binding["eligibility_evaluation"] = attach_digest(evaluation)
        request = replace(
            request,
            eligibility_dispositions=(binding, request.eligibility_dispositions[1]),
        )

    with pytest.raises((CatalogEligibilityBindingError, InvalidConfigurationError)):
        freeze_enrollment_inputs(request, store)
