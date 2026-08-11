from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import (
    ArtifactManifest,
    Attempt,
    AttemptTerminal,
    RetryAuthorization,
)
from memrelay_eval.domain.errors import ReconciliationError
from memrelay_eval.domain.ids import AssignmentId, AttemptId, RetentionPolicyId, RunId
from memrelay_eval.domain.states import (
    ArtifactScope,
    AttemptTerminalKind,
    EvaluationStratum,
    HistoryMode,
)
from memrelay_eval.evidence.projection import EvidenceProjection
from memrelay_eval.evidence.reconcile import (
    AuthoritativeReconciliationState,
    EvidencePresence,
    EvidenceRecord,
    ReconciliationBlocker,
    ReconciliationInput,
    bind_evidence_projection,
    reconcile_required_evidence,
)
from memrelay_eval.evidence.required import (
    EvidenceKind,
    EvidenceMatrixKey,
    RequirementMode,
    producer_identity_for_authority,
    required_evidence_matrix,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
RUN = RunId("run_" + "a" * 32)
ATTEMPT = AttemptId("attempt_" + "b" * 32)
RETENTION = RetentionPolicyId("ret_" + "c" * 32)


def _hashes() -> dict[str, str]:
    return {
        name: sha256(name.encode()).hexdigest()
        for name in (
            "assignment",
            "catalog",
            "fixture",
            "workspace",
            "prompt",
            "tool_policy",
            "budget",
            "grader",
            "configuration",
            "model",
            "parity",
            "environment_fingerprint",
            "transitions",
        )
    }


def _claims(kind: EvidenceKind, hashes: dict[str, str]) -> dict[str, str]:
    if kind is EvidenceKind.ASSIGNMENT:
        return {"hash.assignment": hashes["assignment"]}
    if kind is EvidenceKind.LIFECYCLE:
        return {"run_id": str(RUN), "attempt_id": str(ATTEMPT), "retry_lineage": "none"}
    if kind in {EvidenceKind.INSPECT_JSON, EvidenceKind.SDK_TERMINAL}:
        return {"terminal_state": "succeeded"}
    if kind is EvidenceKind.CONFIGURATION:
        return {"hash.configuration": hashes["configuration"]}
    if kind is EvidenceKind.MODEL_LOCK:
        return {"model_id": "model_" + "1" * 32, "hash.model": hashes["model"]}
    if kind is EvidenceKind.PARITY:
        return {
            "hash.parity": hashes["parity"],
            "environment_fingerprint": hashes["environment_fingerprint"],
            "causal_validity_status": "valid",
        }
    if kind is EvidenceKind.WORKSPACE_PATCH:
        return {"tamper_status": "verified"}
    if kind is EvidenceKind.TREATMENT:
        return {"contamination_status": "isolated"}
    if kind is EvidenceKind.GRADING:
        return {
            "selection_status": "canonical",
            "consistency_status": "consistent",
        }
    if kind is EvidenceKind.PANEL:
        return {"consistency_status": "consistent"}
    if kind is EvidenceKind.CLEANUP:
        return {"cleanup_status": "completed"}
    if kind is EvidenceKind.TRANSITIONS:
        return {"hash.transitions": hashes["transitions"]}
    if kind is EvidenceKind.REFERENCED_HASHES:
        return {f"hash.{name}": value for name, value in hashes.items()}
    return {}


def _record(
    store: InMemoryArtifactStore,
    kind: EvidenceKind,
    authority: str,
    hashes: dict[str, str],
    *,
    status: EvidencePresence = EvidencePresence.PRESENT,
    claims: dict[str, str] | None = None,
    payload: bytes | None = None,
    blockers: tuple[ReconciliationBlocker, ...] = (),
) -> EvidenceRecord:
    producer = producer_identity_for_authority(authority)
    artifact = store.put_bytes(
        payload if payload is not None else kind.value.encode(),
        media_type="application/json",
        classification="fault_fixture",
    )
    manifest = store.write_manifest(
        ArtifactManifest(
            artifact_id=artifact.artifact_id,
            kind=kind.value,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            media_type="application/json",
            created_at=NOW,
            producer_component=producer.component,
            producer_version=producer.version,
            classification="fault_fixture",
            contains_secrets=False,
            source_artifact_ids=(),
            retention_policy_id=RETENTION,
            encryption=None,
            scope=ArtifactScope.ATTEMPT,
            run_id=RUN,
            attempt_id=ATTEMPT,
        )
    )
    return bind_evidence_projection(
        store,
        EvidenceRecord(
            kind,
            authority,
            status,
            artifact,
            manifest,
            _claims(kind, hashes) if claims is None else claims,
            unavailable_reason="native_source_not_exposed"
            if status is EvidencePresence.UNAVAILABLE
            else None,
            declared_blockers=blockers,
        ),
    )


def _request() -> tuple[InMemoryArtifactStore, ReconciliationInput]:
    store = InMemoryArtifactStore()
    hashes = _hashes()
    key = EvidenceMatrixKey(
        "integration",
        EvaluationStratum.PRODUCT,
        HistoryMode.CONTROLLED,
        "terminal",
        AttemptTerminalKind.SUCCEEDED,
        "copilot_task_agent",
        True,
        False,
        False,
    )
    matrix = required_evidence_matrix(key)
    evidence = tuple(
        _record(
            store,
            kind,
            matrix.requirements[kind].authorities[0],
            hashes,
            status=EvidencePresence.UNAVAILABLE
            if kind
            in {EvidenceKind.COST_COPILOT, EvidenceKind.COST_FRAMEWORK, EvidenceKind.COST_LOCAL}
            else EvidencePresence.PRESENT,
        )
        for kind in matrix.primary_kinds
    )
    return store, ReconciliationInput(
        RUN,
        ATTEMPT,
        key,
        {
            "experiment_id": "exp_" + "1" * 32,
            "run_id": str(RUN),
            "attempt_id": str(ATTEMPT),
            "task_id": "task_" + "2" * 32,
            "replicate_id": "replicate_" + "3" * 32,
            "history_id": "history_" + "4" * 32,
            "sequence_id": "sequence_" + "5" * 32,
            "repository_id": "repository_" + "6" * 32,
            "model_id": "model_" + "1" * 32,
            "assignment_id": "assignment_" + "7" * 32,
        },
        hashes,
        NOW,
        NOW,
        NOW,
        sha256(b"protocol").hexdigest(),
        sha256(b"runtime").hexdigest(),
        RETENTION,
        evidence,
    )


def _report_blockers(request: ReconciliationInput, store: InMemoryArtifactStore) -> set[str]:
    projected = replace(request, evidence=_bound_records(store, request.evidence))
    return {item.value for item in reconcile_required_evidence(projected, store).blockers}


def _bound_records(
    store: InMemoryArtifactStore, records: tuple[EvidenceRecord, ...] | list[EvidenceRecord]
) -> tuple[EvidenceRecord, ...]:
    return tuple(bind_evidence_projection(store, record) for record in records)


def test_missing_primary_cost_is_not_zero_or_permitted_unavailable() -> None:
    store, request = _request()
    records = tuple(
        record for record in request.evidence if record.kind is not EvidenceKind.COST_LOCAL
    )

    blockers = _report_blockers(replace(request, evidence=records), store)

    assert ReconciliationBlocker.MISSING.value in blockers
    assert ReconciliationBlocker.UNAVAILABLE_DISALLOWED.value not in blockers


def test_declared_categorical_blockers_are_preserved_but_not_authoritative() -> None:
    store, request = _request()

    report = reconcile_required_evidence(
        replace(request, declared_blockers=(ReconciliationBlocker.TAMPER,)),
        store,
    )

    assert ReconciliationBlocker.TAMPER not in report.blockers
    assert report.to_document()["input"]["declared_blockers"] == ["tamper"]


def test_evidence_projection_binds_claims_blockers_and_verified_raw_bytes() -> None:
    store, request = _request()
    records = _replace_claim(
        list(request.evidence), EvidenceKind.WORKSPACE_PATCH, "tamper_status", "tampered"
    )

    self_asserted = reconcile_required_evidence(replace(request, evidence=tuple(records)), store)

    assert ReconciliationBlocker.EVIDENCE_PROJECTION_BINDING_CONFLICT in self_asserted.blockers
    assert ReconciliationBlocker.TAMPER not in self_asserted.blockers

    bounded_records = _bound_records(store, records)
    bounded = reconcile_required_evidence(
        replace(request, evidence=bounded_records),
        store,
    )

    assert ReconciliationBlocker.TAMPER in bounded.blockers
    projection = next(
        record.projection
        for record in bounded_records
        if record.kind is EvidenceKind.WORKSPACE_PATCH
    )
    assert projection is not None
    store._blobs[projection.sha256] = b"arbitrary projection bytes"  # type: ignore[attr-defined]
    tampered = reconcile_required_evidence(
        replace(request, evidence=bounded_records),
        store,
    )

    assert ReconciliationBlocker.EVIDENCE_PROJECTION_ABSENT_OR_CORRUPT in tampered.blockers


@pytest.mark.parametrize(
    ("target", "mutate"),
    [
        (
            ReconciliationBlocker.TERMINAL_CONFLICT,
            lambda records, _store, _request: _replace_claim(
                records, EvidenceKind.SDK_TERMINAL, "terminal_state", "timed_out"
            ),
        ),
        (
            ReconciliationBlocker.IDENTITY_CONFLICT,
            lambda records, _store, _request: _replace_claim(
                records, EvidenceKind.LIFECYCLE, "run_id", "run_" + "d" * 32
            ),
        ),
        (
            ReconciliationBlocker.CLEANUP_FAILURE,
            lambda records, _store, _request: _replace_claim(
                records, EvidenceKind.CLEANUP, "cleanup_status", "failed"
            ),
        ),
        (
            ReconciliationBlocker.ENVIRONMENT_FINGERPRINT_DRIFT,
            lambda records, _store, _request: _replace_claim(
                records, EvidenceKind.PARITY, "environment_fingerprint", "e" * 64
            ),
        ),
        (
            ReconciliationBlocker.UNAVAILABLE_DISALLOWED,
            lambda records, _store, _request: _replace_status(
                records, EvidenceKind.GRADING, EvidencePresence.UNAVAILABLE
            ),
        ),
        (
            ReconciliationBlocker.FAVORABLE_SUBSTITUTION,
            lambda records, _store, _request: _replace_claim(
                records,
                EvidenceKind.GRADING,
                "selection_status",
                "favorable_substitution",
            ),
        ),
        (
            ReconciliationBlocker.CONTAMINATION,
            lambda records, _store, _request: _replace_claim(
                records, EvidenceKind.TREATMENT, "contamination_status", "contaminated"
            ),
        ),
        (
            ReconciliationBlocker.TAMPER,
            lambda records, _store, _request: _replace_claim(
                records, EvidenceKind.WORKSPACE_PATCH, "tamper_status", "tampered"
            ),
        ),
        (
            ReconciliationBlocker.GRADING_CONFLICT,
            lambda records, _store, _request: _replace_claim(
                records, EvidenceKind.GRADING, "consistency_status", "conflict"
            ),
        ),
        (
            ReconciliationBlocker.CAUSAL_VALIDITY_CONFLICT,
            lambda records, _store, _request: _replace_claim(
                records, EvidenceKind.PARITY, "causal_validity_status", "conflict"
            ),
        ),
    ],
)
def test_categorical_faults_dominate_all_complete_primary_records(target, mutate) -> None:
    store, request = _request()
    records = mutate(list(request.evidence), store, request)

    report = reconcile_required_evidence(
        replace(request, evidence=_bound_records(store, records)), store
    )

    assert target in report.blockers
    assert report.inclusion_status.value == "excluded"


def test_corrupt_cas_stale_manifest_and_credential_evidence_fail_closed() -> None:
    store, request = _request()
    records = list(request.evidence)
    inspect_index = next(
        index for index, record in enumerate(records) if record.kind is EvidenceKind.INSPECT_EVAL
    )
    corrupt = records[inspect_index]
    assert corrupt.artifact is not None
    store._blobs[corrupt.artifact.sha256] = b"changed bytes"  # type: ignore[attr-defined]
    corrupt_report = reconcile_required_evidence(replace(request, evidence=tuple(records)), store)
    assert ReconciliationBlocker.CAS_OR_DIGEST_INVALID in corrupt_report.blockers
    assert corrupt_report.primary_present == corrupt_report.primary_required - 1
    assert corrupt_report.to_document()["primary_complete"] is False

    store, request = _request()
    records = list(request.evidence)
    current = records[inspect_index]
    replacement = _record(
        store,
        EvidenceKind.MEMRELAY_LOGS,
        "memrelay",
        dict(request.frozen_hashes),
    )
    records[inspect_index] = replace(current, manifest=replacement.manifest)
    stale_blockers = _report_blockers(replace(request, evidence=tuple(records)), store)
    assert ReconciliationBlocker.STALE_MANIFEST.value in stale_blockers

    store, request = _request()
    matrix = required_evidence_matrix(request.matrix_key)
    records = list(request.evidence)
    records[inspect_index] = _record(
        store,
        EvidenceKind.INSPECT_EVAL,
        matrix.requirements[EvidenceKind.INSPECT_EVAL].authorities[0],
        dict(request.frozen_hashes),
        payload=b"sk-proj-abcdefghijklmnopqrstuvwx",
    )
    secret_blockers = _report_blockers(replace(request, evidence=tuple(records)), store)
    assert ReconciliationBlocker.CREDENTIAL_LEAK.value in secret_blockers


def test_unavailable_reason_credentials_never_persist_in_evidence_projections() -> None:
    store, request = _request()
    record = next(record for record in request.evidence if record.kind is EvidenceKind.COST_LOCAL)
    secret = "sk-proj-abcdefghijklmnopqrstuvwx"
    before = set(store._blobs)  # type: ignore[attr-defined]

    with pytest.raises(ReconciliationError) as rejected:
        bind_evidence_projection(
            store,
            replace(record, unavailable_reason=secret, projection=None),
        )

    assert rejected.value.code == "evidence_projection_secret_boundary_violation"
    assert set(store._blobs) == before  # type: ignore[attr-defined]

    unsafe_projection = EvidenceProjection(
        kind=record.kind,
        authority=record.authority,
        status=record.status,
        artifact=record.artifact,
        manifest=record.manifest,
        claims=record.claims,
        unavailable_reason=secret,
        declared_blockers=record.declared_blockers,
    )
    unsafe_reference = store.put_bytes(
        canonical_bytes(unsafe_projection.to_document()),
        media_type="application/json",
        classification="test_unsafe_evidence_projection",
    )
    report = reconcile_required_evidence(
        replace(
            request,
            evidence=tuple(
                replace(item, unavailable_reason=secret, projection=unsafe_reference)
                if item.kind is record.kind
                else item
                for item in request.evidence
            ),
        ),
        store,
    )

    assert ReconciliationBlocker.CREDENTIAL_LEAK in report.blockers
    assert secret not in canonical_bytes(report.to_document()).decode()


def test_manifest_producer_identity_is_bound_to_the_matrix_authority() -> None:
    store, request = _request()
    records = list(request.evidence)
    index = next(
        index for index, record in enumerate(records) if record.kind is EvidenceKind.INSPECT_EVAL
    )
    record = records[index]
    assert record.artifact is not None
    manifest = store.read_manifest(record.artifact)
    records[index] = replace(
        record,
        manifest=store.write_manifest(replace(manifest, producer_component="arbitrary_producer")),
    )

    report = reconcile_required_evidence(
        replace(request, evidence=_bound_records(store, records)), store
    )

    assert ReconciliationBlocker.MANIFEST_PRODUCER_CONFLICT in report.blockers
    assert report.primary_present == report.primary_required - 1
    assert report.to_document()["primary_complete"] is False


def test_unavailable_permitted_evidence_is_counted_only_after_manifest_verification() -> None:
    store, request = _request()
    record = next(record for record in request.evidence if record.kind is EvidenceKind.COST_LOCAL)
    assert record.artifact is not None
    store._blobs[record.artifact.sha256] = b"changed bytes"  # type: ignore[attr-defined]

    report = reconcile_required_evidence(request, store)

    assert ReconciliationBlocker.CAS_OR_DIGEST_INVALID in report.blockers
    assert report.primary_present == report.primary_required - 1
    assert report.to_document()["primary_complete"] is False


def test_manifest_kind_and_attempt_scope_must_bind_to_each_evidence_record() -> None:
    mutations = (
        ("kind", EvidenceKind.MEMRELAY_LOGS.value),
        ("run_id", RunId("run_" + "d" * 32)),
        ("attempt_id", AttemptId("attempt_" + "e" * 32)),
    )
    for field, value in mutations:
        store, request = _request()
        records = list(request.evidence)
        index = next(
            index
            for index, record in enumerate(records)
            if record.kind is EvidenceKind.INSPECT_EVAL
        )
        record = records[index]
        assert record.artifact is not None
        manifest = store.read_manifest(record.artifact)
        records[index] = replace(
            record,
            manifest=store.write_manifest(replace(manifest, **{field: value})),
        )

        blockers = _report_blockers(replace(request, evidence=tuple(records)), store)

        assert ReconciliationBlocker.MANIFEST_BINDING_CONFLICT.value in blockers


def test_conditional_adjudication_does_not_change_primary_completeness() -> None:
    store, request = _request()
    key = replace(request.matrix_key, panel_required=True, adjudication_required=False)
    matrix = required_evidence_matrix(key)
    records = list(request.evidence)
    for kind in (EvidenceKind.PANEL, EvidenceKind.CALIBRATION, EvidenceKind.ADJUDICATION):
        records.append(
            _record(
                store,
                kind,
                matrix.requirements[kind].authorities[0],
                dict(request.frozen_hashes),
            )
        )

    report = reconcile_required_evidence(
        replace(request, matrix_key=key, evidence=_bound_records(store, records)),
        store,
    )

    assert (
        matrix.requirements[EvidenceKind.ADJUDICATION].mode
        is RequirementMode.CONDITIONALLY_REQUIRED
    )
    assert report.primary_required == len(matrix.primary_kinds)
    assert report.primary_present == report.primary_required
    assert ReconciliationBlocker.MISSING not in report.blockers


def test_authoritative_lineage_derives_hidden_retry_and_replay_blockers() -> None:
    store, request = _request()
    current = AttemptTerminal(
        ATTEMPT,
        RUN,
        AttemptTerminalKind.SUCCEEDED,
        NOW,
        "terminal_recorded",
    )
    prior = AttemptTerminal(
        AttemptId("attempt_" + "d" * 32),
        RUN,
        AttemptTerminalKind.AGENT_FAILED,
        NOW,
        "terminal_recorded",
    )
    hidden_retry = reconcile_required_evidence(
        request,
        store,
        AuthoritativeReconciliationState(
            current,
            (prior, current),
            ((prior.attempt_id, current.attempt_id),),
            (),
        ),
    )
    replay = reconcile_required_evidence(
        request,
        store,
        AuthoritativeReconciliationState(current, (prior, current), (), ()),
    )

    assert ReconciliationBlocker.HIDDEN_RETRY in hidden_retry.blockers
    assert ReconciliationBlocker.REPLAY in replay.blockers


def test_authoritative_lineage_rejects_superseded_parent_and_unfinished_child() -> None:
    store, request = _request()
    parent = AttemptTerminal(
        ATTEMPT,
        RUN,
        AttemptTerminalKind.SUCCEEDED,
        NOW,
        "terminal_recorded",
    )
    child = Attempt(AttemptId("attempt_" + "f" * 32), RUN)
    authorization = RetryAuthorization(
        RUN,
        AssignmentId("assignment_" + "9" * 32),
        AssignmentId("assignment_" + "9" * 32),
        ATTEMPT,
        child,
        parent,
        (),
        (),
    )

    report = reconcile_required_evidence(
        request,
        store,
        AuthoritativeReconciliationState(
            parent,
            (parent,),
            ((ATTEMPT, child.id),),
            (authorization,),
        ),
    )

    assert ReconciliationBlocker.REPLAY in report.blockers
    assert report.inclusion_status.value == "excluded"


def test_authoritative_lineage_rejects_a_superseded_parent_after_child_terminal() -> None:
    store, request = _request()
    parent = AttemptTerminal(
        ATTEMPT,
        RUN,
        AttemptTerminalKind.SUCCEEDED,
        NOW,
        "terminal_recorded",
    )
    child = Attempt(AttemptId("attempt_" + "f" * 32), RUN)
    child_terminal = AttemptTerminal(
        child.id,
        RUN,
        AttemptTerminalKind.SUCCEEDED,
        NOW,
        "terminal_recorded",
    )
    authorization = RetryAuthorization(
        RUN,
        AssignmentId("assignment_" + "9" * 32),
        AssignmentId("assignment_" + "9" * 32),
        ATTEMPT,
        child,
        parent,
        (),
        (),
    )

    report = reconcile_required_evidence(
        request,
        store,
        AuthoritativeReconciliationState(
            parent,
            (parent, child_terminal),
            ((ATTEMPT, child.id),),
            (authorization,),
        ),
    )

    assert ReconciliationBlocker.REPLAY in report.blockers
    assert report.inclusion_status.value == "excluded"


def test_ambiguous_categorical_claim_fails_closed() -> None:
    store, request = _request()
    records = _replace_claim(
        list(request.evidence),
        EvidenceKind.WORKSPACE_PATCH,
        "tamper_status",
        "unknown",
    )

    report = reconcile_required_evidence(
        replace(request, evidence=_bound_records(store, records)), store
    )

    assert ReconciliationBlocker.BLOCKER_EVIDENCE_INVALID in report.blockers
    assert report.inclusion_status.value == "excluded"


def test_duplicate_partial_and_malformed_evidence_remain_visible_and_block() -> None:
    store, request = _request()
    duplicate = tuple(request.evidence) + (
        next(record for record in request.evidence if record.kind is EvidenceKind.INSPECT_EVAL),
    )
    duplicate_blockers = _report_blockers(replace(request, evidence=duplicate), store)
    assert ReconciliationBlocker.DUPLICATE.value in duplicate_blockers

    partial_records = _replace_status(
        list(request.evidence), EvidenceKind.INSPECT_EVAL, EvidencePresence.PARTIAL
    )
    partial_blockers = _report_blockers(replace(request, evidence=tuple(partial_records)), store)
    assert ReconciliationBlocker.PARTIAL.value in partial_blockers

    malformed_records = _replace_status(
        list(request.evidence), EvidenceKind.INSPECT_EVAL, EvidencePresence.MALFORMED
    )
    malformed_blockers = _report_blockers(
        replace(request, evidence=tuple(malformed_records)),
        store,
    )
    assert ReconciliationBlocker.MALFORMED.value in malformed_blockers


def _replace_claim(
    records: list[EvidenceRecord], kind: EvidenceKind, key: str, value: str
) -> list[EvidenceRecord]:
    index = next(index for index, record in enumerate(records) if record.kind is kind)
    claims = dict(records[index].claims)
    claims[key] = value
    records[index] = replace(records[index], claims=claims)
    return records


def _replace_status(
    records: list[EvidenceRecord], kind: EvidenceKind, status: EvidencePresence
) -> list[EvidenceRecord]:
    index = next(index for index, record in enumerate(records) if record.kind is kind)
    records[index] = replace(
        records[index],
        status=status,
        unavailable_reason="source_unavailable" if status is EvidencePresence.UNAVAILABLE else None,
    )
    return records


def _replace_blockers(
    records: list[EvidenceRecord],
    kind: EvidenceKind,
    blockers: tuple[ReconciliationBlocker, ...],
) -> list[EvidenceRecord]:
    index = next(index for index, record in enumerate(records) if record.kind is kind)
    records[index] = replace(records[index], declared_blockers=blockers)
    return records
