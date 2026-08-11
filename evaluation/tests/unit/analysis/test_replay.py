from __future__ import annotations

import os
import socket
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.analysis.replay import (
    ANALYSIS_ABSOLUTE_TOLERANCE,
    GRADER_CONTINUOUS_TOLERANCE,
    NetworkAccessDenied,
    ReplayOutputs,
    ReproductionBundle,
    allocate_stochastic_rerun,
    compare_replay_outputs,
    execute_sealed_replay,
    network_off,
    replay_offline,
)
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.cli.main import main
from memrelay_eval.domain.errors import ReproductionError
from memrelay_eval.domain.ids import AttemptId, ProtocolId, RunId


def _hash(value: bytes = b"") -> str:
    return sha256(value).hexdigest()


def _outputs(*, numeric: float = 1.0, score: float = 1.0) -> dict[str, object]:
    return {
        "analysis": {
            "categories": {"eligible": 2, "missing": 1},
            "figures": {"primary": _hash(b"figure")},
            "numeric": {"estimate": numeric},
        },
        "grader": {
            "binary": {"passed": True},
            "continuous": {"score": score},
            "tests": {"native": "passed", "hidden": "passed"},
        },
        "normalized_evidence": {"workspace": _hash(b"workspace")},
    }


def _bundle(cas_root: Path) -> ReproductionBundle:
    input_directory = cas_root / "inputs"
    input_directory.mkdir(parents=True)
    files = {
        "analysis.json": (b"retained analysis source", "analysis_source"),
        "grader.json": (b"retained grader contract", "grader_contract"),
        "evidence.json": (b"retained evidence source", "evidence_source"),
        "replay_runner.py": (
            (
                "import os\n"
                "def rebuild(bundle, roots):\n"
                "    assert 'OPENAI_API_KEY' not in os.environ\n"
                f"    return {_outputs()!r}\n"
            ).encode(),
            "replay_runner",
        ),
    }
    for name, (contents, _) in files.items():
        (input_directory / name).write_bytes(contents)
    document = {
        "artifact_type": "reproduction_bundle",
        "expected": _outputs(),
        "inputs": [
            {
                "derivation_sha256": _hash(f"{role}-derivation".encode()),
                "path": f"inputs/{name}",
                "role": role,
                "root": "cas",
                "sha256": _hash(contents),
                "source_sha256": _hash(f"{role}-source".encode()),
            }
            for name, (contents, role) in files.items()
        ],
        "normalization": {"paths": "relative_posix", "timestamps": "omitted"},
        "runner_path": "inputs/replay_runner.py",
        "runtime": {
            "duckdb": "1.5.5",
            "lock_sha256": _hash(b"lock"),
            "pyarrow": "25.0.0",
            "python": "3.13",
        },
        "schema_version": "1.0.0",
    }
    document["bundle_id"] = canonical_digest(document)
    return ReproductionBundle.parse(canonical_bytes(document))


def test_offline_replay_reproduces_exact_categories_hashes_and_tolerated_numerics(
    tmp_path: Path,
) -> None:
    bundle = _bundle(tmp_path / "cas")
    actual = ReplayOutputs.from_document(
        _outputs(numeric=1.0 + ANALYSIS_ABSOLUTE_TOLERANCE, score=1.0 + GRADER_CONTINUOUS_TOLERANCE)
    )

    comparison = replay_offline(bundle, cas_root=tmp_path / "cas", rebuilder=lambda _: actual)

    assert comparison.matches is True
    assert comparison.mismatches == ()


def test_replay_mismatch_identifies_source_derivation_and_downstream_impact(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "cas")
    actual = ReplayOutputs.from_document(_outputs(numeric=1.0001))

    comparison = compare_replay_outputs(bundle, actual)

    assert comparison.matches is False
    mismatch = comparison.mismatches[0]
    assert mismatch.field == "numeric.estimate"
    assert mismatch.source_sha256 == _hash(b"analysis_source-source")
    assert mismatch.derivation_sha256 == _hash(b"analysis_source-derivation")
    assert mismatch.downstream_impacts == ("analysis",)


def test_replay_rejects_tampered_retained_input_before_rebuilding(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "cas")
    (tmp_path / "cas" / "inputs" / "analysis.json").write_bytes(b"corrupt")

    with pytest.raises(ReproductionError, match="reproduction source hash mismatch") as raised:
        replay_offline(
            bundle,
            cas_root=tmp_path / "cas",
            rebuilder=lambda _: (_ for _ in ()).throw(AssertionError("must not rebuild")),
        )

    assert raised.value.code == "reproduction_source_hash_mismatch"


def test_sealed_replay_executes_the_verified_runner_inside_network_off(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "cas")

    comparison = execute_sealed_replay(bundle, cas_root=tmp_path / "cas")

    assert comparison.matches is True


def test_offline_replay_command_executes_only_the_bundle_bound_runner(tmp_path: Path) -> None:
    cas_root = tmp_path / "cas"
    bundle = _bundle(cas_root)
    bundle_path = tmp_path / "reproduction-bundle.json"
    bundle_path.write_bytes(bundle.bytes())

    exit_code = main(
        (
            "reproduce-offline",
            "--bundle",
            str(bundle_path),
            "--cas-root",
            str(cas_root),
            "--output-root",
            str(tmp_path / "output"),
        )
    )

    assert exit_code == 0
    assert list((tmp_path / "output" / "reproductions").rglob("reproduction-comparison.json"))


def test_grader_mismatches_are_attributed_to_the_grader_contract(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "cas")
    document = _outputs()
    document["grader"] = {
        "binary": {"passed": False},
        "continuous": {"score": 1.0},
        "tests": {"hidden": "passed", "native": "passed"},
    }

    comparison = compare_replay_outputs(bundle, ReplayOutputs.from_document(document))

    mismatch = comparison.mismatches[0]
    assert mismatch.field == "grader.binary.passed"
    assert mismatch.source_sha256 == _hash(b"grader_contract-source")


def test_network_off_strips_credentials_and_denies_dns_and_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-replay")

    with network_off():
        assert "OPENAI_API_KEY" not in os.environ
        with pytest.raises(NetworkAccessDenied):
            socket.getaddrinfo("example.com", 443)
        with pytest.raises(NetworkAccessDenied):
            socket.create_connection(("example.com", 443))
        with pytest.raises(NetworkAccessDenied):
            subprocess.run(("python", "--version"), check=True)
        with pytest.raises(NetworkAccessDenied):
            os.system("echo must-not-run")


def test_stochastic_rerun_receives_new_ids_and_cannot_write_confirmatory_root(
    tmp_path: Path,
) -> None:
    original = tmp_path / "confirmatory"
    original.mkdir()
    protocol = ProtocolId.new()
    run = RunId.new()
    attempt = AttemptId.new()

    identity = allocate_stochastic_rerun(
        original_protocol_id=str(protocol),
        original_run_id=str(run),
        original_attempt_id=str(attempt),
        conclusion_class="indeterminate",
        output_root=tmp_path / "reruns",
        original_evidence_root=original,
    )

    assert identity.protocol_id != protocol
    assert identity.run_id != run
    assert identity.attempt_id != attempt
    assert (identity.output_directory / "lineage.json").is_file()
    with pytest.raises(ReproductionError, match="stochastic output overlaps"):
        allocate_stochastic_rerun(
            original_protocol_id=str(protocol),
            original_run_id=str(run),
            original_attempt_id=str(attempt),
            conclusion_class="indeterminate",
            output_root=original,
            original_evidence_root=original,
        )
