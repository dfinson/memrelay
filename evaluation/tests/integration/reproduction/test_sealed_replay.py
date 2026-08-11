from __future__ import annotations

import json
import sys
from pathlib import Path

from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.analysis.replay import (
    ReplayOutputs,
    ReproductionBundle,
    _execute_retained_grader,
    build_grader_replay_descriptor,
    rebuild_retained_outputs,
)
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.cli.main import main
from memrelay_eval.domain.entities import GraderResult
from memrelay_eval.domain.states import GraderTerminalKind
from memrelay_eval.evidence.parquet import ParquetMaterializer
from tests.integration.analysis.test_parquet_materialization import _record
from tests.unit.scoring.test_deterministic_grader import _contract, _snapshot


def test_seal_reproduction_bundle_rebuilds_real_retained_parquet_and_evidence(
    tmp_path: Path,
) -> None:
    store, record = _record(tmp_path)
    dataset = ParquetMaterializer(store, tmp_path / "parquet").materialize((record,))
    manifest = json.loads((dataset.directory / "dataset-manifest.json").read_bytes())
    runtime_lock = tmp_path / "runtime.lock"
    runtime_lock.write_text("frozen local wheel inventory", encoding="utf-8")
    grader_store = InMemoryArtifactStore()
    snapshot = _snapshot(grader_store)
    script = (
        "import json\n"
        "print(json.dumps({'schema_version':'1.0.0','native_tests':True,'hidden_tests':True,"
        "'continuous_score':1.0,'objective_components':{'fixture':1.0}},"
        "sort_keys=True,separators=(',',':')))\n"
    )
    contract = _contract(grader_store, snapshot, script)
    result_artifact = grader_store.put_bytes(
        b"retained grader result", media_type="application/json", classification="grader_result"
    )
    expected_result = GraderResult(
        snapshot_sha256=snapshot.canonical_sha256 or "0" * 64,
        contract_sha256=canonical_digest(contract.to_record()),
        terminal=GraderTerminalKind.PASSED,
        binary_passed=True,
        test_outcomes={"hidden": True, "native": True},
        continuous_score=1.0,
        objective_components={"fixture": 1.0},
        raw_output_artifact=None,
        result_artifact=result_artifact,
    )
    grader_replay = build_grader_replay_descriptor(
        snapshot=snapshot,
        contract=contract,
        expected_result=expected_result,
        artifact_store=grader_store,
    )
    queries_path = tmp_path / "queries.json"
    grader_path = tmp_path / "grader-result.json"
    evidence_path = tmp_path / "normalized-evidence.json"
    queries_path.write_bytes(
        canonical_bytes(
            [
                {
                    "name": "eligible",
                    "table": "eligible_outcomes",
                    "columns": ["endpoint_id", "numeric_value"],
                    "equals": [],
                    "numeric_column": "numeric_value",
                    "figure_columns": ["endpoint_id", "numeric_value"],
                }
            ]
        )
    )
    grader_path.write_bytes(canonical_bytes(grader_replay))
    evidence_path.write_bytes(
        canonical_bytes(
            {
                "created_at": "2026-08-11T00:00:00Z",
                "workspace_path": "workspace\\result.json",
                "value": "retained",
            }
        )
    )
    bundle_root = tmp_path / "bundle"
    assert (
        main(
            [
                "seal-reproduction-bundle",
                "--parquet-root",
                str(dataset.directory.parent),
                "--dataset-version",
                dataset.dataset_version,
                "--queries",
                str(queries_path),
                "--grader-result",
                str(grader_path),
                "--normalized-evidence",
                str(evidence_path),
                "--protocol-sha256",
                manifest["protocol_sha256"][0],
                "--runtime-lock",
                str(runtime_lock),
                "--output-root",
                str(bundle_root),
            ]
        )
        == 0
    )
    bundle = ReproductionBundle.parse((bundle_root / "reproduction-bundle.json").read_bytes())

    rebuilt = ReplayOutputs.from_document(
        rebuild_retained_outputs(bundle.bytes(), {"cas": str(bundle_root)})
    )
    assert rebuilt.categories == bundle.expected.categories
    assert rebuilt.numeric == bundle.expected.numeric
    assert rebuilt.figures == bundle.expected.figures
    assert rebuilt.normalized_evidence == bundle.expected.normalized_evidence
    assert rebuilt.grader_binary == {}
    assert bundle.protocol_sha256 == manifest["protocol_sha256"][0]
    assert (bundle_root / "reproduction-bundle.json").is_file()
    if sys.platform == "linux":
        actual_grader = _execute_retained_grader(grader_replay)
        assert actual_grader == {
            "binary_passed": True,
            "continuous_score": 1.0,
            "tests": {"hidden": True, "native": True},
        }
        changed_contract = _contract(
            grader_store,
            snapshot,
            script.replace("'native_tests':True", "'native_tests':False"),
        )
        changed_descriptor = build_grader_replay_descriptor(
            snapshot=snapshot,
            contract=changed_contract,
            expected_result=expected_result,
            artifact_store=grader_store,
        )
        changed_grader = _execute_retained_grader(changed_descriptor)
        assert changed_grader["binary_passed"] is False
