from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.adapters.memrelay.observation import (
    ObservationQualificationService,
    build_observation_identity,
)
from memrelay_eval.adapters.memrelay.observation_runner import (
    ObservationTelemetryCollector,
    current_observation_identity,
    current_observation_semantic_map,
    current_observation_source_files,
    load_observation_product_configuration,
    run_actual_observation_composition,
)
from memrelay_eval.adapters.telemetry.observation import observation_telemetry_evidence
from memrelay_eval.adapters.telemetry.semantics import (
    GENAI_DEVELOPMENT_FIELD_MAP,
    OBSERVATION_SENTINEL_ATTRIBUTE_MAP,
    SpanClass,
    TelemetryContext,
    TelemetrySpan,
)
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.cli.main import main
from memrelay_eval.application.observation_services import verified_product_observation_evidence
from memrelay_eval.domain.errors import ObservationQualificationError
from memrelay_eval.domain.identity import identity_for_span_class
from memrelay_eval.domain.observation import (
    ObservationBoundary,
    ObservationContract,
    ObservationEvidence,
    ObservationFailureReason,
    ObservationPath,
    ObservationSentinel,
    SentinelBoundaryRecord,
    assess_observation,
    generate_sentinels,
    observation_evidence_from_document,
    require_new_protocol,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA = _REPO_ROOT / "evaluation" / "schemas" / "observation-qualification.schema.json"
_SOURCE_FILES = (
    _REPO_ROOT / "src" / "memrelay" / "daemon" / "session_discovery.py",
    _REPO_ROOT / "src" / "memrelay" / "daemon" / "runtime.py",
)
_STARTED_AT = datetime(2026, 8, 11, tzinfo=UTC)


def _identity(*, semantic_map: dict[str, object] | None = None):
    return build_observation_identity(
        source_files=_SOURCE_FILES,
        semantic_map=semantic_map
        or {
            "genai": GENAI_DEVELOPMENT_FIELD_MAP,
            "observation": OBSERVATION_SENTINEL_ATTRIBUTE_MAP,
        },
        configuration={"ingest": {"session_poll_interval": 2, "max_sessions": 2}},
        runtime_lock=b"synthetic-runtime-lock-v1",
    )


def _contract(path: ObservationPath = ObservationPath.REPLAY) -> ObservationContract:
    return ObservationContract(
        path=path,
        identity=_identity(),
        expected_sentinels=tuple(
            ObservationSentinel(f"sentinel_{sequence:032x}", sequence) for sequence in range(1, 4)
        ),
        window_started_at=_STARTED_AT,
        deadline_at=_STARTED_AT + timedelta(minutes=1),
    )


def _records(contract: ObservationContract) -> list[SentinelBoundaryRecord]:
    result: list[SentinelBoundaryRecord] = []
    for boundary in ObservationBoundary:
        for sentinel in contract.expected_sentinels:
            result.append(
                SentinelBoundaryRecord(
                    path=contract.path,
                    boundary=boundary,
                    sentinel_id=sentinel.identifier,
                    sequence=sentinel.sequence,
                    observed_at=_STARTED_AT + timedelta(seconds=sentinel.sequence),
                    restart_epoch=1 if sentinel.sequence == 3 else 0,
                )
            )
    # File-watch is intentionally allowed to produce an input duplicate; the spool is
    # the idempotent boundary and retains one accepted sentinel.
    result.insert(
        7,
        SentinelBoundaryRecord(
            path=contract.path,
            boundary=ObservationBoundary.PRE_IDEMPOTENCY,
            sentinel_id=contract.expected_sentinels[0].identifier,
            sequence=1,
            observed_at=_STARTED_AT + timedelta(seconds=1),
        ),
    )
    return result


def _evidence(contract: ObservationContract) -> ObservationEvidence:
    return ObservationEvidence(
        path=contract.path,
        conformance_sha256=contract.identity.conformance_sha256,
        records=tuple(_records(contract)),
        final_drain_completed=True,
        collector_shutdown_verified=True,
        reconciliation_completed=True,
    )


def _cli_contract(tmp_path: Path, path: ObservationPath) -> tuple[ObservationContract, Path, Path]:
    product_config = tmp_path / "product.toml"
    product_config.write_text(
        "\n".join(
            (
                "[ingest]",
                f'intake_source = "{path.value}"',
                "session_poll_interval = 0.001",
                "max_sessions = 3",
                "session_freshness_s = 30.0",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    runtime_lock = tmp_path / "runtime-lock.json"
    runtime_lock.write_bytes(b'{"runtime":"synthetic-observation-lock-v1"}')
    identity, _ = current_observation_identity(
        path=path,
        product_config_path=product_config,
        runtime_lock_path=runtime_lock,
        workspace=tmp_path / "identity-workspace",
    )
    contract = ObservationContract(
        path=path,
        identity=identity,
        expected_sentinels=tuple(
            ObservationSentinel(f"sentinel_{sequence:032x}", sequence) for sequence in range(1, 4)
        ),
        window_started_at=_STARTED_AT,
        deadline_at=_STARTED_AT + timedelta(minutes=1),
    )
    return contract, product_config, runtime_lock


def _actual_run_contract(
    tmp_path: Path, path: ObservationPath
) -> tuple[ObservationContract, Any, Path]:
    contract, product_config, runtime_lock = _cli_contract(tmp_path, path)
    identity, config = current_observation_identity(
        path=path,
        product_config_path=product_config,
        runtime_lock_path=runtime_lock,
        workspace=tmp_path / "actual-run-identity",
    )
    started_at = datetime.now(UTC)
    return (
        replace(
            contract,
            identity=identity,
            window_started_at=started_at,
            deadline_at=started_at + timedelta(minutes=1),
        ),
        config,
        tmp_path / "actual-run-workspace",
    )


class _FailingTailSource:
    async def __aenter__(self) -> _FailingTailSource:
        raise RuntimeError("tail source failed")

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _EmptyTailSource:
    async def __aenter__(self) -> _EmptyTailSource:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def __aiter__(self) -> Any:
        return self._records()

    async def _records(self) -> Any:
        if False:
            yield None


class _CollectorMutation(ObservationTelemetryCollector):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self._mode = mode

    def emit_span(self, span: TelemetrySpan) -> None:
        if self._mode == "drop":
            return
        if self._mode == "malformed":
            span = replace(span, attributes={"observation_path": ObservationPath.REPLAY.value})
        elif self._mode == "mixed_path":
            span = replace(
                span,
                attributes={
                    **span.attributes,
                    "observation_path": ObservationPath.FILE_WATCH.value,
                },
            )
        super().emit_span(span)


@pytest.mark.parametrize("path", [ObservationPath.REPLAY, ObservationPath.FILE_WATCH])
def test_path_scoped_sentinels_bind_real_source_identity_and_all_boundaries(
    path: ObservationPath,
) -> None:
    contract = _contract(path)
    evidence = _evidence(contract)
    service = ObservationQualificationService()
    decision = service.qualify(contract, evidence, decided_at=_STARTED_AT + timedelta(minutes=2))

    assert decision.qualified
    assert decision.assessment.delivery_numerator == 3
    assert decision.assessment.delivery_denominator == 3
    assert decision.assessment.pre_idempotency_duplicates == (
        contract.expected_sentinels[0].identifier,
    )
    assert decision.assessment.post_idempotency_duplicates == ()
    assert decision.assessment.completeness_claim().startswith("The named configured")
    assert contract.identity.protocol_version.endswith(contract.identity.conformance_sha256[:16])

    store = InMemoryArtifactStore()
    persisted = service.persist(store, decision)
    assert store.read_manifest(persisted.decision_ref).sha256 == persisted.decision_ref.sha256
    document = decision.to_document()
    jsonschema.Draft202012Validator(json.loads(_SCHEMA.read_text(encoding="utf-8"))).validate(
        document
    )
    rendered = json.dumps(document)
    assert str(_REPO_ROOT) not in rendered
    assert '"payload"' not in rendered
    assert persisted.qualification_manifest_ref.size_bytes > 0


def test_sentinel_generator_is_opaque_unique_and_nonsecret() -> None:
    tokens = iter(bytes([value]) * 16 for value in (1, 1, 2, 3))
    sentinels = generate_sentinels(3, token_bytes=lambda _: next(tokens))

    assert [item.sequence for item in sentinels] == [1, 2, 3]
    assert len({item.identifier for item in sentinels}) == 3
    assert all(item.identifier.startswith("sentinel_") for item in sentinels)


def test_prior_evidence_documents_remain_parseable_without_new_failure_inventory() -> None:
    evidence = _evidence(_contract())
    prior_document = evidence.to_document()
    prior_document.pop("evidence_failure_reasons")

    assert observation_evidence_from_document(prior_document) == evidence


def test_semantic_and_source_identity_drift_require_a_new_protocol_version(tmp_path: Path) -> None:
    first_source = tmp_path / "first.py"
    second_source = tmp_path / "second.py"
    first_source.write_text("first", encoding="utf-8")
    second_source.write_text("second", encoding="utf-8")
    common = {
        "configuration": {"ingest": {"intake_source": "replay"}},
        "runtime_lock": b"synthetic-runtime-lock-v1",
    }
    first = build_observation_identity(
        source_files=(first_source, second_source),
        semantic_map={"map": "one"},
        **common,
    )
    second_source.write_text("second changed", encoding="utf-8")
    source_changed = build_observation_identity(
        source_files=(first_source, second_source),
        semantic_map={"map": "one"},
        **common,
    )
    semantic_changed = build_observation_identity(
        source_files=(first_source, second_source),
        semantic_map={"map": "two"},
        **common,
    )

    assert first.conformance_sha256 != source_changed.conformance_sha256
    assert first.protocol_version != source_changed.protocol_version
    assert source_changed.conformance_sha256 != semantic_changed.conformance_sha256
    assert source_changed.protocol_version != semantic_changed.protocol_version

    with pytest.raises(ObservationQualificationError) as error:
        require_new_protocol(
            ObservationContract(
                path=ObservationPath.REPLAY,
                identity=first,
                expected_sentinels=_contract().expected_sentinels,
                window_started_at=_STARTED_AT,
                deadline_at=_STARTED_AT + timedelta(minutes=1),
            ),
            ObservationContract(
                path=ObservationPath.REPLAY,
                identity=source_changed,
                expected_sentinels=_contract().expected_sentinels,
                window_started_at=_STARTED_AT,
                deadline_at=_STARTED_AT + timedelta(minutes=1),
            ),
        )
    assert error.value.code == ObservationFailureReason.CONFORMANCE_IDENTITY_DRIFT.value


def test_telemetry_records_expose_only_opaque_sentinel_attributes() -> None:
    contract = _contract()
    sentinel = contract.expected_sentinels[0]
    span = TelemetrySpan(
        span_id="span-observation-1",
        span_class=SpanClass.DAEMON_DISPATCH,
        context=TelemetryContext(
            experiment_id="exp_" + "1" * 32,
            protocol_id="protocol_" + "2" * 32,
            run_id="run_" + "3" * 32,
            attempt_id="attempt_" + "4" * 32,
            scenario_id="scenario_" + "5" * 32,
            stratum_id="product",
            history_mode="controlled",
            identity=identity_for_span_class(SpanClass.DAEMON_DISPATCH.value),
            evidence_class="observation_sentinel",
            exposure_state="unexposed",
            environment_fingerprint_sha256="a" * 64,
        ),
        started_at=_STARTED_AT,
        ended_at=_STARTED_AT + timedelta(seconds=1),
        attributes={
            "sentinel_id": sentinel.identifier,
            "sentinel_sequence": sentinel.sequence,
            "observation_path": contract.path.value,
            "restart_epoch": 1,
        },
    )

    telemetry = observation_telemetry_evidence(
        (span,),
        path=contract.path,
        expected_sentinels=(sentinel,),
        collector_shutdown_verified=True,
    )
    assert telemetry.complete
    assert telemetry.records[0].sentinel_id == sentinel.identifier
    assert telemetry.records[0].restart_epoch == 1


def test_telemetry_reconciliation_scopes_order_to_the_target_path_and_rejects_bad_spans() -> None:
    contract = _contract()
    sentinel = contract.expected_sentinels[0]
    context = TelemetryContext(
        experiment_id="exp_" + "1" * 32,
        protocol_id="protocol_" + "2" * 32,
        run_id="run_" + "3" * 32,
        attempt_id="attempt_" + "4" * 32,
        scenario_id="scenario_" + "5" * 32,
        stratum_id="product",
        history_mode="controlled",
        identity=identity_for_span_class(SpanClass.DAEMON_DISPATCH.value),
        evidence_class="observation_sentinel",
        exposure_state="unexposed",
        environment_fingerprint_sha256="a" * 64,
    )
    target = TelemetrySpan(
        span_id="span-observation-target",
        span_class=SpanClass.DAEMON_DISPATCH,
        context=context,
        started_at=_STARTED_AT,
        ended_at=_STARTED_AT,
        attributes={
            "sentinel_id": sentinel.identifier,
            "sentinel_sequence": sentinel.sequence,
            "observation_path": ObservationPath.REPLAY.value,
            "restart_epoch": 1,
        },
    )
    other_path = replace(
        target,
        span_id="span-observation-other",
        attributes={
            **target.attributes,
            "observation_path": ObservationPath.FILE_WATCH.value,
        },
    )

    mixed = observation_telemetry_evidence(
        (other_path, target),
        path=ObservationPath.REPLAY,
        expected_sentinels=(sentinel,),
        collector_shutdown_verified=True,
    )
    malformed = observation_telemetry_evidence(
        (
            replace(
                target,
                span_id="span-observation-malformed",
                attributes={"observation_path": ObservationPath.REPLAY.value},
            ),
        ),
        path=ObservationPath.REPLAY,
        expected_sentinels=(sentinel,),
        collector_shutdown_verified=True,
    )

    assert [record.sentinel_id for record in mixed.records] == [sentinel.identifier]
    assert "TEL-OUT-OF-ORDER" not in mixed.failure_codes
    assert "TEL-OBSERVATION-RECONCILIATION" in mixed.failure_codes
    assert not mixed.complete
    assert malformed.records == ()
    assert "TEL-OBSERVATION-RECONCILIATION" in malformed.failure_codes
    assert not malformed.complete


def test_mixed_path_evidence_fails_without_a_completeness_claim() -> None:
    contract = _contract()
    assessment = assess_observation(
        contract,
        replace(_evidence(contract), path=ObservationPath.FILE_WATCH),
    )

    assert not assessment.qualified
    assert assessment.reason_code == "observation_path_mismatch"
    with pytest.raises(ObservationQualificationError) as error:
        assessment.completeness_claim()
    assert error.value.code == "observation_path_mismatch"


@pytest.mark.parametrize("path", [ObservationPath.REPLAY, ObservationPath.FILE_WATCH])
def test_observation_conformance_cli_executes_and_binds_actual_compositions(
    tmp_path: Path, path: ObservationPath
) -> None:
    contract, product_config, runtime_lock = _cli_contract(tmp_path, path)
    input_path = tmp_path / "observation-input.json"
    input_path.write_bytes(canonical_bytes({"contract": contract.to_document()}))
    output_root = tmp_path / "artifacts"

    assert (
        main(
            [
                "observation-conformance",
                "--input",
                str(input_path),
                "--product-config",
                str(product_config),
                "--runtime-lock",
                str(runtime_lock),
                "--output-root",
                str(output_root),
            ]
        )
        == 0
    )

    decision_path = next(
        output_root.glob(f"observation-qualification/{path.value}/*/decision-*.json")
    )
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["qualified"] is True
    assert decision["contract"]["identity"] == contract.identity.to_document()
    expected_boundaries = {
        boundary.value
        for boundary in ObservationBoundary
        if path is ObservationPath.FILE_WATCH or boundary is not ObservationBoundary.LIVE_TAIL
    }
    assert expected_boundaries == {
        record["boundary"] for record in decision["evidence"]["records"]
    }
    assert all(
        record["sentinel_id"] not in contract.expected_identifiers
        for record in decision["evidence"]["records"]
    )
    qualification_manifest_path = next(
        output_root.glob(f"observation-qualification/{path.value}/*/manifest-*.json")
    )
    qualification_manifest = json.loads(qualification_manifest_path.read_text(encoding="utf-8"))
    assert qualification_manifest["protocol_version"] == contract.identity.protocol_version
    assert "native_observation_receipt" in qualification_manifest["input_hashes"]
    receipt_path = next(
        output_root.glob(f"observation-qualification/{path.value}/*/native-receipt-*.json")
    )
    native_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    rendered_receipt = json.dumps(native_receipt)
    assert '"content"' not in rendered_receipt
    assert str(tmp_path) not in rendered_receipt
    expected_capture = "RunObserveCapture" if path is ObservationPath.REPLAY else "LiveTailCapture"
    assert native_receipt["capture_types"] == [expected_capture] * 6
    if path is ObservationPath.FILE_WATCH:
        assert set(native_receipt["live_tail_ids"]) == set(native_receipt["mcp_ids"])
        assert native_receipt["live_tail_failed"] is False
    assert native_receipt["sessions_observed"] == 6
    assert native_receipt["spool_ids"] == native_receipt["mcp_ids"]
    assert native_receipt["path"] == path.value
    command_manifest_path = next(output_root.glob("commands/observation-conformance-*.json"))
    command_manifest = json.loads(command_manifest_path.read_text(encoding="utf-8"))
    assert command_manifest["runtime_lock_sha256"] == contract.identity.runtime_lock_sha256
    assert command_manifest["protocol_sha256"] == canonical_digest(
        {
            "protocol_version": contract.identity.protocol_version,
            "conformance_sha256": contract.identity.conformance_sha256,
        }
    )


@pytest.mark.parametrize(
    ("tail_source", "reason"),
    [
        (_FailingTailSource, ObservationFailureReason.LIVE_TAIL_DELIVERY_FAILED),
        (_EmptyTailSource, ObservationFailureReason.LIVE_TAIL_DELIVERY_MISSING),
    ],
)
def test_file_watch_requires_retained_live_tail_delivery_even_when_replay_succeeds(
    tmp_path: Path,
    tail_source: type[object],
    reason: ObservationFailureReason,
) -> None:
    contract, config, workspace = _actual_run_contract(tmp_path, ObservationPath.FILE_WATCH)

    run = run_actual_observation_composition(
        contract=contract,
        config=config,
        workspace=workspace,
        tail_source_factory=lambda _ref: tail_source(),
    )
    evidence = verified_product_observation_evidence(
        contract,
        run,
        native_receipt_persisted=True,
    )
    decision = ObservationQualificationService().qualify(
        contract,
        evidence,
        decided_at=datetime.now(UTC),
    )

    assert run.receipt
    assert {record.sentinel_id for record in run.native_records if record.boundary is ObservationBoundary.SPOOL} == set(
        contract.expected_identifiers
    )
    assert not decision.qualified
    assert reason in decision.assessment.failure_reasons
    assert reason in evidence.evidence_failure_reasons
    assert decision.assessment.reason_code == reason.value
    assert not [
        record
        for record in evidence.records
        if record.boundary is ObservationBoundary.LIVE_TAIL
    ]


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("drop", ObservationFailureReason.TELEMETRY_DELIVERY_LOSS),
        ("malformed", ObservationFailureReason.TELEMETRY_MALFORMED),
        ("mixed_path", ObservationFailureReason.TELEMETRY_PATH_MISMATCH),
    ],
)
def test_actual_collector_delivery_faults_fail_observation_qualification(
    tmp_path: Path,
    mode: str,
    reason: ObservationFailureReason,
) -> None:
    contract, config, workspace = _actual_run_contract(tmp_path, ObservationPath.REPLAY)
    collector = _CollectorMutation(mode)

    run = run_actual_observation_composition(
        contract=contract,
        config=config,
        workspace=workspace,
        telemetry_collector_factory=lambda: collector,
    )
    evidence = verified_product_observation_evidence(
        contract,
        run,
        native_receipt_persisted=True,
    )
    decision = ObservationQualificationService().qualify(
        contract,
        evidence,
        decided_at=datetime.now(UTC),
    )

    assert run.collector_shutdown_verified
    assert not decision.qualified
    assert reason in evidence.evidence_failure_reasons
    assert reason in decision.assessment.failure_reasons
    assert decision.assessment.reason_code == reason.value


def test_actual_composition_preserves_real_source_times_and_rejects_pre_window_evidence(
    tmp_path: Path,
) -> None:
    contract, config, workspace = _actual_run_contract(tmp_path, ObservationPath.REPLAY)
    run = run_actual_observation_composition(contract=contract, config=config, workspace=workspace)
    evidence = verified_product_observation_evidence(
        contract,
        run,
        native_receipt_persisted=True,
    )
    qualified = ObservationQualificationService().qualify(
        contract,
        evidence,
        decided_at=datetime.now(UTC),
    )

    assert qualified.qualified
    assert {
        span.attributes["sentinel_id"] for span in run.telemetry_spans
    } == set(contract.expected_identifiers)
    assert all(
        span.attributes["observation_path"] == contract.path.value
        and contract.window_started_at <= span.ended_at <= contract.deadline_at
        for span in run.telemetry_spans
    )
    assert all(
        contract.window_started_at <= record.observed_at <= contract.deadline_at
        for record in evidence.records
    )
    retained = next(
        record for record in evidence.records if record.boundary is ObservationBoundary.SPOOL
    )
    pre_window = replace(
        evidence,
        records=tuple(
            replace(record, observed_at=contract.window_started_at - timedelta(microseconds=1))
            if record is retained
            else record
            for record in evidence.records
        ),
    )
    rejected = assess_observation(contract, pre_window)

    assert ObservationFailureReason.OUTSIDE_FROZEN_WINDOW in rejected.failure_reasons
    assert ObservationFailureReason.MISSING in rejected.failure_reasons


@pytest.mark.parametrize(
    "identity_part", ["source", "semantic_map", "configuration", "runtime_lock"]
)
def test_observation_conformance_cli_rejects_serialized_identity_drift(
    tmp_path: Path, identity_part: str
) -> None:
    contract, product_config, runtime_lock = _cli_contract(tmp_path, ObservationPath.REPLAY)
    source_files = current_observation_source_files(ObservationPath.REPLAY)
    semantic_map = current_observation_semantic_map()
    _, configuration = load_observation_product_configuration(
        path=ObservationPath.REPLAY,
        product_config_path=product_config,
        workspace=tmp_path / "configuration-workspace",
    )
    lock_bytes = runtime_lock.read_bytes()
    if identity_part == "source":
        stale_source = tmp_path / "stale-observation-source.py"
        stale_source.write_text("stale observation source", encoding="utf-8")
        source_files = (stale_source,)
    elif identity_part == "semantic_map":
        semantic_map = {"stale": "semantic-map"}
    elif identity_part == "configuration":
        configuration = {"stale": "configuration"}
    elif identity_part == "runtime_lock":
        lock_bytes = b'{"runtime":"stale-observation-lock-v1"}'
    stale_identity = build_observation_identity(
        source_files=source_files,
        semantic_map=semantic_map,
        configuration=configuration,
        runtime_lock=lock_bytes,
    )
    stale = replace(contract, identity=stale_identity)
    input_path = tmp_path / "stale-observation-input.json"
    input_path.write_bytes(canonical_bytes({"contract": stale.to_document()}))
    output_root = tmp_path / "artifacts"

    with pytest.raises(ObservationQualificationError) as error:
        main(
            [
                "observation-conformance",
                "--input",
                str(input_path),
                "--product-config",
                str(product_config),
                "--runtime-lock",
                str(runtime_lock),
                "--output-root",
                str(output_root),
            ]
        )

    assert error.value.code == ObservationFailureReason.CONFORMANCE_IDENTITY_DRIFT.value
    assert not list(output_root.glob("observation-qualification/**/decision-*.json"))
    command_manifest = json.loads(
        next(output_root.glob("commands/observation-conformance-*.json")).read_text(
            encoding="utf-8"
        )
    )
    expected_error = ObservationFailureReason.CONFORMANCE_IDENTITY_DRIFT.value
    assert command_manifest["error_code"] == expected_error
