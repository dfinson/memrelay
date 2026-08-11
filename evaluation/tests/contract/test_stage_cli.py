from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from memrelay_eval.cli.main import main
from memrelay_eval.domain.ids import ProtocolId, StageAuthorizationId, StageId
from memrelay_eval.domain.policies import STAGE_ENTRY_LOCK_FIELDS
from memrelay_eval.domain.states import StageKind, StageState
from memrelay_eval.orchestration.stages import (
    StageAuthorization,
    StageEntryBundle,
    StageExitBundle,
)

HASH_A = "a" * 64

CI_MARKERS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "JENKINS_URL",
    "TF_BUILD",
    "TEAMCITY_VERSION",
    "CIRCLECI",
)
AMBIENT_MARKERS = (
    "MEMRELAY_EVAL_STAGE",
    "MEMRELAY_EVAL_TOPOLOGY",
    "MEMRELAY_EVAL_FALLBACK",
    "MEMRELAY_EVAL_AUTHORIZATION",
    "MEMRELAY_EVAL_ENTRY_BUNDLE",
    "MEMRELAY_EVAL_PREDECESSOR_EXIT",
    "MEMRELAY_EVAL_PAID",
    "MEMRELAY_EVAL_AUTO_PROMOTE",
    "MEMRELAY_EVAL_PROMOTE",
)

_SCHEMA_ROOT = Path(__file__).resolve().parents[2] / "schemas"


def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for marker in (*CI_MARKERS, *AMBIENT_MARKERS):
        monkeypatch.delenv(marker, raising=False)


def _entry_locks(preceding: str) -> dict[str, str]:
    locks = dict.fromkeys(STAGE_ENTRY_LOCK_FIELDS, HASH_A)
    locks["preceding_exit_sha256"] = preceding
    return locks


def _accepted_predecessor() -> StageExitBundle:
    return StageExitBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.CONFORMANCE,
        protocol_id=ProtocolId.new(),
        entry_bundle_sha256="b" * 64,
        preceding_exit_sha256="c" * 64,
        status=StageState.ACCEPTED,
        reconciliation_sha256="d" * 64,
        inclusion_decision_sha256="e" * 64,
        authorization_id=StageAuthorizationId.new(),
    )


def _seal_stage_inputs(
    tmp_path: Path,
    *,
    predecessor: StageExitBundle | None = None,
    authorizer_role: str = "operator",
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
) -> dict[str, str]:
    now = datetime.now(UTC)
    predecessor = predecessor or _accepted_predecessor()
    stage_id = StageId.new()
    protocol_id = ProtocolId.new()
    entry = StageEntryBundle(
        stage_id=stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=protocol_id,
        predecessor_stage_kind=StageKind.CONFORMANCE,
        locks=_entry_locks(predecessor.digest),
    )
    authorization = StageAuthorization(
        authorization_id=StageAuthorizationId.new(),
        stage_id=stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=protocol_id,
        entry_bundle_sha256=entry.digest,
        envelope_sha256=entry.envelope_sha256,
        authorizer_id="operator-1",
        authorizer_role=authorizer_role,
        valid_from=valid_from or now - timedelta(hours=1),
        valid_until=valid_until or now + timedelta(hours=1),
        paid_execution=True,
    )
    entry_path = tmp_path / "entry.json"
    predecessor_path = tmp_path / "predecessor.json"
    authorization_path = tmp_path / "authorization.json"
    entry_path.write_bytes(entry.bytes())
    predecessor_path.write_bytes(predecessor.bytes())
    authorization_path.write_bytes(authorization.bytes())
    return {
        "entry": str(entry_path),
        "predecessor": str(predecessor_path),
        "authorization": str(authorization_path),
        "output_root": str(tmp_path / "artifacts"),
    }


def _run(paths: dict[str, str], stage: str = "integration") -> int:
    return main(
        [
            "run",
            "--stage",
            stage,
            "--entry-bundle",
            paths["entry"],
            "--predecessor-exit",
            paths["predecessor"],
            "--authorization",
            paths["authorization"],
            "--output-root",
            paths["output_root"],
        ]
    )


def _sole_manifest(paths: dict[str, str]) -> dict[str, object]:
    commands = list((Path(paths["output_root"]) / "commands").glob("*.json"))
    assert len(commands) == 1
    return json.loads(commands[0].read_text(encoding="utf-8"))


def _validate_manifest(document: dict[str, object]) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((_SCHEMA_ROOT / "command-manifest.schema.json").read_text("utf-8"))
    jsonschema.validate(document, schema)


def test_authorized_integration_run_writes_complete_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear_environment(monkeypatch)
    paths = _seal_stage_inputs(tmp_path)

    exit_code = _run(paths)

    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out.strip())
    document = _sole_manifest(paths)
    assert printed == document
    assert document["command"] == "run"
    assert document["stage"] == "integration"
    assert document["terminal_status"] == "succeeded"
    assert document["exit_code"] == 0
    assert document["error_code"] is None
    assert document["runtime_lock_sha256"] == HASH_A
    assert document["protocol_sha256"] == HASH_A
    assert set(document["input_hashes"]) == {
        "authorization",
        "predecessor_exit",
        "stage_entry_bundle",
    }
    assert set(document["output_hashes"]) == {"stage_authorization", "stage_entry_bundle"}
    _validate_manifest(document)


def test_run_replay_is_idempotent_single_append_only_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_environment(monkeypatch)
    paths = _seal_stage_inputs(tmp_path)

    assert _run(paths) == 0
    first = _sole_manifest(paths)
    assert _run(paths) == 0
    second = _sole_manifest(paths)

    assert first == second


@pytest.mark.parametrize("stage", ("integration", "pilot", "primary", "secondary"))
def test_missing_authority_refuses_before_enrollment_with_typed_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    _clear_environment(monkeypatch)
    output_root = tmp_path / "artifacts"

    exit_code = main(["run", "--stage", stage, "--output-root", str(output_root)])

    assert exit_code == 2
    document = json.loads(capsys.readouterr().out.strip())
    assert document["terminal_status"] == "refused"
    assert document["error_code"] == "stage_inputs_incomplete"
    assert document["stage"] == stage
    _validate_manifest(document)


def test_rejected_predecessor_refuses_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear_environment(monkeypatch)
    rejected = StageExitBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.CONFORMANCE,
        protocol_id=ProtocolId.new(),
        entry_bundle_sha256="b" * 64,
        preceding_exit_sha256="c" * 64,
        status=StageState.REJECTED,
        reconciliation_sha256="d" * 64,
        inclusion_decision_sha256="e" * 64,
        authorization_id=StageAuthorizationId.new(),
    )
    paths = _seal_stage_inputs(tmp_path, predecessor=rejected)

    assert _run(paths) == 2
    document = json.loads(capsys.readouterr().out.strip())
    assert document["error_code"] == "predecessor_exit_rejected"
    assert document["terminal_status"] == "refused"


def test_paid_execution_is_refused_under_ci(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear_environment(monkeypatch)
    monkeypatch.setenv("CI", "true")
    paths = _seal_stage_inputs(tmp_path)

    assert _run(paths) == 2
    document = json.loads(capsys.readouterr().out.strip())
    assert document["error_code"] == "paid_execution_forbidden_in_ci"
    assert document["terminal_status"] == "refused"


@pytest.mark.parametrize("marker", AMBIENT_MARKERS)
def test_ambient_stage_configuration_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    marker: str,
) -> None:
    _clear_environment(monkeypatch)
    monkeypatch.setenv(marker, "1")
    paths = _seal_stage_inputs(tmp_path)

    assert _run(paths) == 2
    document = json.loads(capsys.readouterr().out.strip())
    assert document["error_code"] == "ambient_stage_configuration_forbidden"
    assert document["terminal_status"] == "refused"


def test_cross_repository_run_is_denied_before_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear_environment(monkeypatch)

    exit_code = main(["run", "--stage", "cross-repo", "--output-root", str(tmp_path)])

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "cross_repository_stage_disabled" in output
    assert re.search(r"repository_[a-f0-9]{32}", output) is None
    assert "credential" not in output
    # Deny-before-discovery emits no stage manifest and touches no artifact root.
    assert not (tmp_path / "commands").exists()
