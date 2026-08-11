from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from memrelay_eval.adapters.artifacts.filesystem import FilesystemArtifactStore
from memrelay_eval.adapters.fakes import InMemoryLedger
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.cli.main import main
from memrelay_eval.domain.entities import ArtifactManifest, AttemptTerminal
from memrelay_eval.domain.errors import (
    LedgerIntentConflictError,
    ReconciliationError,
    TerminalDecisionConflictError,
)
from memrelay_eval.domain.ids import (
    AssignmentId,
    AttemptId,
    ExperimentId,
    IntentId,
    ProtocolId,
    RetentionPolicyId,
    RunId,
)
from memrelay_eval.domain.intents import (
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    IntentAck,
    IntentMetadata,
    IntentRejection,
    RunTransitionIntent,
)
from memrelay_eval.domain.states import (
    ArtifactScope,
    AttemptTerminalKind,
    EvaluationStratum,
    HistoryMode,
    InclusionStatus,
    RunState,
)
from memrelay_eval.evidence.reconcile import (
    EvidencePresence,
    EvidenceRecord,
    ReconciliationBlocker,
    ReconciliationInput,
    ReconciliationService,
    bind_evidence_projection,
    reconciliation_authority_from_input,
)
from memrelay_eval.evidence.required import (
    EvidenceKind,
    EvidenceMatrixKey,
    RequiredEvidenceProducerPolicy,
    producer_identity_for_authority,
    required_evidence_matrix,
    required_evidence_producer_policy,
)
from memrelay_eval.ledger import SqliteLedger

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
RETENTION = RetentionPolicyId("ret_" + "f" * 32)


def _accepted(value: IntentAck | IntentRejection) -> IntentAck:
    assert isinstance(value, IntentAck)
    return value


def _metadata(
    attempt_id: AttemptId | None = None,
    state: RunState | None = None,
    digest: str | None = None,
) -> IntentMetadata:
    return IntentMetadata(
        IntentId.new(),
        NOW,
        source_attempt_id=attempt_id,
        expected_prior_state=state,
        expected_prior_digest=digest,
        reason_code="lifecycle_advanced",
    )


def _seed_scored(
    ledger: SqliteLedger,
    *,
    terminal_kind: AttemptTerminalKind = AttemptTerminalKind.SUCCEEDED,
    record_terminal: bool = True,
) -> tuple[RunId, AttemptId]:
    experiment = ExperimentId("exp_" + "1" * 32)
    run = RunId("run_" + "2" * 32)
    attempt = AttemptId("attempt_" + "3" * 32)
    _accepted(
        ledger.submit_intent(CreateExperimentIntent(_metadata(), experiment, ProtocolId.new()))
    )
    _accepted(
        ledger.submit_intent(CreateRunIntent(_metadata(), run, experiment, AssignmentId.new()))
    )
    _accepted(ledger.submit_intent(CreateAttemptIntent(_metadata(), attempt, run)))
    digest: str | None = None
    for previous, next_state in (
        (RunState.PLANNED, RunState.ASSIGNED),
        (RunState.ASSIGNED, RunState.PROVISIONED),
        (RunState.PROVISIONED, RunState.RUNNING),
        (RunState.RUNNING, RunState.EXPORTED),
        (RunState.EXPORTED, RunState.SCORED),
    ):
        acknowledgement = _accepted(
            ledger.submit_intent(
                RunTransitionIntent(
                    _metadata(attempt, previous, digest),
                    run,
                    previous,
                    next_state,
                )
            )
        )
        digest = acknowledgement.canonical_payload_digest
    if record_terminal:
        ledger.append_attempt_terminal(
            AttemptTerminal(
                attempt,
                run,
                terminal_kind,
                NOW,
                "typed_terminal_recorded",
            )
        )
    return run, attempt


def _frozen_hashes() -> dict[str, str]:
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


def _claims(
    kind: EvidenceKind,
    run: RunId,
    attempt: AttemptId,
    hashes: dict[str, str],
) -> dict[str, str]:
    claims: dict[str, str] = {}
    if kind is EvidenceKind.ASSIGNMENT:
        claims["hash.assignment"] = hashes["assignment"]
    elif kind is EvidenceKind.LIFECYCLE:
        claims.update({"run_id": str(run), "attempt_id": str(attempt), "retry_lineage": "none"})
    elif kind in {EvidenceKind.INSPECT_JSON, EvidenceKind.SDK_TERMINAL}:
        claims["terminal_state"] = "succeeded"
    elif kind is EvidenceKind.CONFIGURATION:
        claims["hash.configuration"] = hashes["configuration"]
    elif kind is EvidenceKind.MODEL_LOCK:
        claims.update({"model_id": "model_" + "7" * 32, "hash.model": hashes["model"]})
    elif kind is EvidenceKind.PARITY:
        claims.update(
            {
                "hash.parity": hashes["parity"],
                "environment_fingerprint": hashes["environment_fingerprint"],
                "causal_validity_status": "valid",
            }
        )
    elif kind is EvidenceKind.WORKSPACE_PATCH:
        claims["tamper_status"] = "verified"
    elif kind is EvidenceKind.TREATMENT:
        claims["contamination_status"] = "isolated"
    elif kind is EvidenceKind.GRADING:
        claims.update(
            {
                "selection_status": "canonical",
                "consistency_status": "consistent",
            }
        )
    elif kind is EvidenceKind.PANEL:
        claims["consistency_status"] = "consistent"
    elif kind is EvidenceKind.CLEANUP:
        claims["cleanup_status"] = "completed"
    elif kind is EvidenceKind.TRANSITIONS:
        claims["hash.transitions"] = hashes["transitions"]
    elif kind is EvidenceKind.REFERENCED_HASHES:
        claims = {f"hash.{name}": value for name, value in hashes.items()}
    return claims


def _record(
    store: FilesystemArtifactStore,
    kind: EvidenceKind,
    authority: str,
    run: RunId,
    attempt: AttemptId,
    hashes: dict[str, str],
    *,
    suffix: str = "",
    payload: bytes | None = None,
    claims: dict[str, str] | None = None,
) -> EvidenceRecord:
    producer = producer_identity_for_authority(authority)
    status = (
        EvidencePresence.UNAVAILABLE
        if kind in {EvidenceKind.COST_COPILOT, EvidenceKind.COST_FRAMEWORK, EvidenceKind.COST_LOCAL}
        else EvidencePresence.PRESENT
    )
    payload = payload if payload is not None else f"{kind.value}:{suffix}".encode()
    artifact = store.put_bytes(
        payload,
        media_type="application/json",
        classification="reconciliation_fixture",
    )
    manifest_ref = store.write_manifest(
        ArtifactManifest(
            artifact_id=artifact.artifact_id,
            kind=kind.value,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            media_type="application/json",
            created_at=NOW,
            producer_component=producer.component,
            producer_version=producer.version,
            classification="reconciliation_fixture",
            contains_secrets=False,
            source_artifact_ids=(),
            retention_policy_id=RETENTION,
            encryption=None,
            scope=ArtifactScope.ATTEMPT,
            run_id=run,
            attempt_id=attempt,
        )
    )
    return bind_evidence_projection(
        store,
        EvidenceRecord(
            kind,
            authority,
            status,
            artifact,
            manifest_ref,
            _claims(kind, run, attempt, hashes) if claims is None else claims,
            unavailable_reason="native_source_not_exposed"
            if status is EvidencePresence.UNAVAILABLE
            else None,
        ),
    )


def _request(store: FilesystemArtifactStore, run: RunId, attempt: AttemptId) -> ReconciliationInput:
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
    hashes = _frozen_hashes()
    records = tuple(
        _record(
            store,
            kind,
            matrix.requirements[kind].authorities[0],
            run,
            attempt,
            hashes,
        )
        for kind in matrix.primary_kinds
    )
    return ReconciliationInput(
        run,
        attempt,
        key,
        {
            "experiment_id": "exp_" + "1" * 32,
            "run_id": str(run),
            "attempt_id": str(attempt),
            "task_id": "task_" + "4" * 32,
            "replicate_id": "replicate_" + "5" * 32,
            "history_id": "history_" + "6" * 32,
            "sequence_id": "sequence_" + "7" * 32,
            "repository_id": "repository_" + "8" * 32,
            "model_id": "model_" + "7" * 32,
            "assignment_id": "assignment_" + "9" * 32,
        },
        hashes,
        NOW,
        NOW,
        NOW,
        sha256(b"protocol").hexdigest(),
        sha256(b"runtime").hexdigest(),
        RETENTION,
        records,
    )


def _record_authority(ledger: SqliteLedger, request: ReconciliationInput) -> None:
    assert ledger.record_reconciliation_authority(reconciliation_authority_from_input(request))


def test_reconciliation_is_cas_first_idempotent_and_appends_one_inclusion(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(ledger)
    request = _request(store, run, attempt)
    _record_authority(ledger, request)
    service = ReconciliationService(store, ledger)

    first = service.reconcile(request)
    repeated = service.reconcile(replace(request, evidence=tuple(reversed(request.evidence))))

    assert first.decision.status is InclusionStatus.INCLUDED
    assert repeated.decision_idempotent
    assert repeated.report.reconciliation_sha256 == first.report.reconciliation_sha256
    assert repeated.report_ref == first.report_ref
    assert ledger.inclusion_for(run) == first.decision
    assert ledger.run_state_snapshot(run).state is RunState.INCLUDED
    assert store.open_verified(first.report_ref)
    assert store.read_manifest(first.report_ref).kind == "reconciliation_report"
    ledger.close()


def test_reconciliation_binds_the_authoritative_attempt_terminal_before_deciding(
    tmp_path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(
        ledger,
        terminal_kind=AttemptTerminalKind.TIMED_OUT,
    )
    request = _request(store, run, attempt)
    _record_authority(ledger, request)

    with pytest.raises(ReconciliationError) as mismatch:
        ReconciliationService(store, ledger).reconcile(request)

    assert mismatch.value.code == "authoritative_attempt_terminal_classification_mismatch"
    ledger.close()


def test_reconciliation_requires_the_sealed_terminal_timestamp_to_match_the_ledger(
    tmp_path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(ledger)
    request = replace(
        _request(store, run, attempt),
        terminal_at=NOW + timedelta(seconds=1),
        reconciled_at=NOW + timedelta(seconds=1),
    )
    _record_authority(ledger, request)

    with pytest.raises(ReconciliationError) as mismatch:
        ReconciliationService(store, ledger).reconcile(request)

    assert mismatch.value.code == "authoritative_attempt_terminal_timestamp_mismatch"
    ledger.close()


def test_reconciliation_requires_an_authoritative_attempt_terminal(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(ledger, record_terminal=False)
    request = _request(store, run, attempt)
    _record_authority(ledger, request)

    with pytest.raises(ReconciliationError) as missing:
        ReconciliationService(store, ledger).reconcile(request)

    assert missing.value.code == "authoritative_attempt_terminal_missing"
    ledger.close()


def test_reconciliation_fails_closed_without_a_controlled_authority(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(ledger)

    with pytest.raises(ReconciliationError) as missing:
        ReconciliationService(store, ledger).reconcile(_request(store, run, attempt))

    assert missing.value.code == "reconciliation_authority_missing"
    ledger.close()


def test_reconciliation_authority_seals_every_input_controlled_condition(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(ledger)
    request = _request(store, run, attempt)
    _record_authority(ledger, request)
    hashes = dict(request.frozen_hashes)
    hashes["fixture"] = sha256(b"different-fixture").hexdigest()
    identities = dict(request.identities)
    identities["history_id"] = "history_" + "0" * 32
    variants = (
        replace(
            request,
            matrix_key=replace(request.matrix_key, history_mode=HistoryMode.DYNAMIC),
        ),
        replace(
            request,
            matrix_key=replace(request.matrix_key, stratum=EvaluationStratum.DIRECT_ENGINE),
        ),
        replace(
            request,
            matrix_key=replace(request.matrix_key, provider_path="direct_engine"),
        ),
        replace(request, matrix_key=replace(request.matrix_key, panel_required=True)),
        replace(request, identities=identities),
        replace(request, frozen_hashes=hashes),
        replace(request, reconciled_at=NOW + timedelta(seconds=1)),
        replace(request, protocol_sha256=sha256(b"other-protocol").hexdigest()),
        replace(request, runtime_lock_sha256=sha256(b"other-runtime").hexdigest()),
    )

    for variant in variants:
        with pytest.raises(ReconciliationError) as mismatch:
            ReconciliationService(store, ledger).reconcile(variant)
        assert mismatch.value.code == "reconciliation_authority_mismatch"

    ledger.close()


@pytest.mark.parametrize("source_policy", ("matrix", "producer"))
def test_reconciliation_rejects_a_mutated_policy_after_authority_sealing(
    tmp_path, monkeypatch, source_policy: str
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(ledger)
    request = _request(store, run, attempt)
    _record_authority(ledger, request)

    if source_policy == "matrix":
        matrix = required_evidence_matrix(request.matrix_key)
        requirements = dict(matrix.requirements)
        requirement = requirements[EvidenceKind.MEMRELAY_LOGS]
        requirements[EvidenceKind.MEMRELAY_LOGS] = replace(requirement, authorities=("ledger",))
        monkeypatch.setattr(
            "memrelay_eval.evidence.reconcile.required_evidence_matrix",
            lambda _key: replace(matrix, requirements=requirements),
        )
    else:
        policy = required_evidence_producer_policy()
        authorities = dict(policy.authorities)
        authority = next(iter(authorities))
        identity = next(iter(authorities[authority]))
        authorities[authority] = frozenset({replace(identity, version="mutated-producer-version")})
        monkeypatch.setattr(
            "memrelay_eval.evidence.reconcile.required_evidence_producer_policy",
            lambda: RequiredEvidenceProducerPolicy(authorities),
        )

    with pytest.raises(ReconciliationError) as mismatch:
        ReconciliationService(store, ledger).reconcile(request)

    assert mismatch.value.code == "reconciliation_authority_policy_mismatch"
    ledger.close()


def test_reconciliation_authority_is_durable_and_immutable(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger_path = tmp_path / "ledger.sqlite"
    ledger = SqliteLedger.open_control(ledger_path)
    run, attempt = _seed_scored(ledger)
    authority = reconciliation_authority_from_input(_request(store, run, attempt))

    assert ledger.record_reconciliation_authority(authority)
    assert not ledger.record_reconciliation_authority(authority)
    with pytest.raises(LedgerIntentConflictError):
        ledger.record_reconciliation_authority(
            replace(authority, matrix_key=replace(authority.matrix_key, stage="primary"))
        )
    ledger.close()

    reopened = SqliteLedger.open_control(ledger_path)
    assert reopened.reconciliation_authority_for(run, attempt) == authority
    reopened.close()


def test_fake_ledger_keeps_reconciliation_authority_sole_writer(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = InMemoryLedger()
    run, attempt = _seed_scored(ledger)  # type: ignore[arg-type]
    authority = reconciliation_authority_from_input(_request(store, run, attempt))

    assert ledger.record_reconciliation_authority(authority)
    assert not ledger.record_reconciliation_authority(authority)
    with pytest.raises(LedgerIntentConflictError):
        ledger.record_reconciliation_authority(
            replace(authority, matrix_key=replace(authority.matrix_key, stage="primary"))
        )
    assert ledger.reconciliation_authority_for(run, attempt) == authority


def test_reconciliation_report_redacts_secret_positive_evidence_before_storage(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(ledger)
    request = _request(store, run, attempt)
    secret = "sk-proj-abcdefghijklmnopqrstuvwx"
    records = list(request.evidence)
    index = next(
        index for index, record in enumerate(records) if record.kind is EvidenceKind.MEMRELAY_LOGS
    )
    records[index] = _record(
        store,
        EvidenceKind.MEMRELAY_LOGS,
        required_evidence_matrix(request.matrix_key)
        .requirements[EvidenceKind.MEMRELAY_LOGS]
        .authorities[0],
        run,
        attempt,
        dict(request.frozen_hashes),
        payload=secret.encode(),
    )

    request = replace(request, evidence=tuple(records))
    _record_authority(ledger, request)
    result = ReconciliationService(store, ledger).reconcile(request)
    report_bytes = store.open_verified(result.report_ref)

    assert ReconciliationBlocker.CREDENTIAL_LEAK in result.report.blockers
    assert secret.encode() not in report_bytes
    assert store.read_manifest(result.report_ref).contains_secrets is False
    ledger.close()


def test_reconciliation_report_schema_validates_without_external_references(tmp_path) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(ledger)
    request = _request(store, run, attempt)
    _record_authority(ledger, request)
    result = ReconciliationService(store, ledger).reconcile(request)
    schema_path = Path(__file__).parents[3] / "schemas" / "reconciliation-report.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(result.report.to_document())

    ledger.close()


def test_changed_evidence_preserves_a_new_report_but_cannot_replace_terminal_decision(
    tmp_path,
) -> None:
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    ledger = SqliteLedger.open_control(tmp_path / "ledger.sqlite")
    run, attempt = _seed_scored(ledger)
    request = _request(store, run, attempt)
    _record_authority(ledger, request)
    service = ReconciliationService(store, ledger)
    first = service.reconcile(request)
    changed_records = list(request.evidence)
    index = next(
        index
        for index, record in enumerate(changed_records)
        if record.kind is EvidenceKind.MEMRELAY_LOGS
    )
    changed_records[index] = _record(
        store,
        EvidenceKind.MEMRELAY_LOGS,
        required_evidence_matrix(request.matrix_key)
        .requirements[EvidenceKind.MEMRELAY_LOGS]
        .authorities[0],
        run,
        attempt,
        dict(request.frozen_hashes),
        suffix="changed",
    )

    with pytest.raises(TerminalDecisionConflictError) as raised:
        service.reconcile(replace(request, evidence=tuple(changed_records)))

    assert raised.value.report_ref is not None
    assert raised.value.report_manifest_ref is not None
    assert raised.value.report_ref != first.report_ref
    report_bytes = store.open_verified(raised.value.report_ref)
    report = json.loads(report_bytes)
    assert ReconciliationBlocker.AUTHORITY_CONFLICT.value in report["blockers"]
    assert b"changed" not in report_bytes
    assert ledger.inclusion_for(run) == first.decision
    assert ledger.run_state_snapshot(run).state is RunState.INCLUDED
    ledger.close()


def test_reconcile_cli_writes_a_typed_terminal_command_manifest(tmp_path, capsys) -> None:
    root = tmp_path / "artifacts"
    store = FilesystemArtifactStore(root)
    ledger_path = root / "ledger.sqlite"
    ledger = SqliteLedger.open_control(ledger_path)
    run, attempt = _seed_scored(ledger)
    request = _request(store, run, attempt)
    _record_authority(ledger, request)
    ledger.close()
    input_path = tmp_path / "integration.input.json"
    output_path = tmp_path / "reconcile-command-manifest.json"
    input_path.write_bytes(canonical_bytes(request.to_document()))

    exit_code = main(
        [
            "reconcile",
            "--stage",
            "integration",
            "--input",
            str(input_path),
            "--artifacts-root",
            str(root),
            "--ledger",
            str(ledger_path),
            "--manifest",
            str(output_path),
        ]
    )

    manifest = json.loads(output_path.read_text(encoding="utf-8"))
    stdout_manifest = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert manifest == stdout_manifest
    assert manifest["terminal_status"] == "succeeded"
    assert manifest["runtime_lock_sha256"] == request.runtime_lock_sha256
    assert manifest["protocol_sha256"] == request.protocol_sha256
    assert manifest["output_hashes"]["reconciliation_sha256"]


def test_reconcile_cli_exits_nonzero_after_persisting_an_excluded_decision(
    tmp_path, capsys
) -> None:
    root = tmp_path / "artifacts"
    store = FilesystemArtifactStore(root)
    ledger_path = root / "ledger.sqlite"
    ledger = SqliteLedger.open_control(ledger_path)
    run, attempt = _seed_scored(ledger)
    request = _request(store, run, attempt)
    missing_evidence = tuple(
        record for record in request.evidence if record.kind is not EvidenceKind.WORKSPACE_PATCH
    )
    request = replace(request, evidence=missing_evidence)
    _record_authority(ledger, request)
    ledger.close()
    input_path = tmp_path / "missing-evidence.input.json"
    output_path = tmp_path / "excluded-command-manifest.json"
    input_path.write_bytes(canonical_bytes(request.to_document()))

    exit_code = main(
        [
            "reconcile",
            "--stage",
            "integration",
            "--input",
            str(input_path),
            "--artifacts-root",
            str(root),
            "--ledger",
            str(ledger_path),
            "--manifest",
            str(output_path),
        ]
    )

    manifest = json.loads(output_path.read_text(encoding="utf-8"))
    assert json.loads(capsys.readouterr().out) == manifest
    assert exit_code == 2
    assert manifest["terminal_status"] == "failed"
    assert manifest["error_code"] == "reconciliation_excluded_missing_primary_evidence"
    reopened = SqliteLedger.open_control(ledger_path)
    assert reopened.inclusion_for(run) is not None
    assert reopened.inclusion_for(run).status is InclusionStatus.EXCLUDED  # type: ignore[union-attr]
    reopened.close()
