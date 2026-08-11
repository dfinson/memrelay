from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jsonschema
import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.adapters.memrelay.observation import (
    ObservationQualificationService,
    build_observation_identity,
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
from memrelay_eval.domain.errors import ObservationQualificationError
from memrelay_eval.domain.identity import identity_for_span_class
from memrelay_eval.domain.observation import (
    ObservationBoundary,
    ObservationContract,
    ObservationEvidence,
    ObservationPath,
    ObservationSentinel,
    SentinelBoundaryRecord,
    assess_observation,
    generate_sentinels,
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
        (span,), path=contract.path, collector_shutdown_verified=True
    )
    assert telemetry.complete
    assert telemetry.records[0].sentinel_id == sentinel.identifier
    assert telemetry.records[0].restart_epoch == 1


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


def test_observation_conformance_cli_persists_path_bound_decision_and_manifests(
    tmp_path: Path,
) -> None:
    contract = _contract()
    evidence = _evidence(contract)
    input_path = tmp_path / "observation-input.json"
    input_path.write_bytes(
        canonical_bytes(
            {
                "contract": contract.to_document(),
                "evidence": evidence.to_document(),
                "decided_at": (_STARTED_AT + timedelta(minutes=2))
                .isoformat(timespec="microseconds")
                .replace("+00:00", "Z"),
            }
        )
    )
    output_root = tmp_path / "artifacts"

    assert (
        main(
            [
                "observation-conformance",
                "--input",
                str(input_path),
                "--output-root",
                str(output_root),
            ]
        )
        == 0
    )

    decision_path = next(output_root.glob("observation-qualification/replay/*/decision-*.json"))
    decision = json.loads(decision_path.read_text(encoding="utf-8"))
    assert decision["qualified"] is True
    assert decision["contract"]["identity"] == contract.identity.to_document()
    qualification_manifest_path = next(
        output_root.glob("observation-qualification/replay/*/manifest-*.json")
    )
    qualification_manifest = json.loads(qualification_manifest_path.read_text(encoding="utf-8"))
    assert qualification_manifest["protocol_version"] == contract.identity.protocol_version
    command_manifest_path = next(output_root.glob("commands/observation-conformance-*.json"))
    command_manifest = json.loads(command_manifest_path.read_text(encoding="utf-8"))
    assert command_manifest["runtime_lock_sha256"] == contract.identity.runtime_lock_sha256
    assert command_manifest["protocol_sha256"] == canonical_digest(
        {
            "protocol_version": contract.identity.protocol_version,
            "conformance_sha256": contract.identity.conformance_sha256,
        }
    )
