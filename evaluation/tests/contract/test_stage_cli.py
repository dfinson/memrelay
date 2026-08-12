from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.cli.main import main
from memrelay_eval.domain.ids import ProtocolId, ScenarioId, StageAuthorizationId, StageId
from memrelay_eval.domain.policies import STAGE_ENTRY_LOCK_FIELDS
from memrelay_eval.domain.states import StageKind, StageState
from memrelay_eval.evidence.conformance import (
    REQUIRED_PROOF_IDS,
    ConformanceContext,
    ConformanceProbe,
    ProofRegistry,
    bootstrap_receipt_bytes,
    build_bootstrap_receipt,
    build_conformance_report,
    observed_probe_result,
    report_bytes,
)
from memrelay_eval.orchestration.limits import IntegrationStageLimits
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


def _observed_receipts(locks: dict[str, str]) -> tuple[object, ...]:
    registry = ProofRegistry(
        tuple(
            ConformanceProbe(
                proof_id,
                f"test/{proof_id}",
                lambda _, proof_id=proof_id: observed_probe_result(
                    input_documents={"proof": proof_id},
                    output_documents={"observation": proof_id},
                ),
            )
            for proof_id in REQUIRED_PROOF_IDS
        )
    )
    return registry.execute(
        ConformanceContext(
            mode="unpaid_ci",
            evaluation_root=Path(__file__).parents[2],
            run_root=Path(__file__).parent,
            stage_locks=locks,
            bootstrap_receipt={},
        )
    )


def _seal_stage_inputs(
    tmp_path: Path,
    *,
    predecessor: StageExitBundle | None = None,
    authorizer_role: str = "operator",
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    integration_inputs: bool = False,
) -> dict[str, str]:
    now = datetime.now(UTC)
    predecessor = predecessor or _accepted_predecessor()
    limits = (
        IntegrationStageLimits(ai_credit_cap=100.0, usd_cap=10.0, per_run_tool_call_cap=60)
        if integration_inputs
        else None
    )
    stage_id = StageId.new()
    protocol_id = ProtocolId.new()
    entry = StageEntryBundle(
        stage_id=stage_id,
        stage_kind=StageKind.INTEGRATION,
        protocol_id=protocol_id,
        predecessor_stage_kind=StageKind.CONFORMANCE,
        locks={
            **_entry_locks(predecessor.digest),
            **({"limits_sha256": limits.digest} if limits is not None else {}),
        },
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
    bootstrap = bootstrap_receipt_bytes(
        build_bootstrap_receipt(
            mode="unpaid_ci",
            runtime_lock={"lock_sha256": entry.locks["runtime_lock_sha256"]},
            input_hashes={"runtime": HASH_A},
            output_hashes={"telemetry": HASH_A},
            environment_sha256=entry.locks["environment_sha256"],
            protocol_sha256=entry.locks["protocol_sha256"],
        )
    )
    conformance = build_conformance_report(
        mode="unpaid_ci",
        stage_locks=entry.locks,
        proof_receipts=_observed_receipts(entry.locks),  # type: ignore[arg-type]
        input_hashes={"catalog_to_report_sha256": HASH_A},
        bootstrap_receipt_sha256=sha256(bootstrap).hexdigest(),
    )
    conformance_path = tmp_path / "conformance-report.json"
    bootstrap_path = tmp_path / "bootstrap-receipt.json"
    conformance_path.write_bytes(report_bytes(conformance))
    bootstrap_path.write_bytes(bootstrap)
    paths = {
        "entry": str(entry_path),
        "predecessor": str(predecessor_path),
        "authorization": str(authorization_path),
        "conformance": str(conformance_path),
        "bootstrap": str(bootstrap_path),
        "output_root": str(tmp_path / "artifacts"),
    }
    if limits is not None:
        scenarios_path = tmp_path / "integration-scenarios.json"
        limits_path = tmp_path / "integration-limits.json"
        scenarios_path.write_bytes(
            canonical_bytes(
                {
                    "scenario_ids": [
                        str(ScenarioId.from_digest(canonical_digest({"scenario": index})))
                        for index in range(8)
                    ]
                }
            )
        )
        limits_path.write_bytes(
            canonical_bytes(
                {
                    "ai_credit_cap": limits.ai_credit_cap,
                    "usd_cap": limits.usd_cap,
                    "per_run_tool_call_cap": limits.per_run_tool_call_cap,
                }
            )
        )
        paths["integration_scenarios"] = str(scenarios_path)
        paths["integration_limits"] = str(limits_path)
    return paths


def _run(paths: dict[str, str], stage: str = "integration", *extra: str) -> int:
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
            "--conformance-report",
            paths["conformance"],
            "--bootstrap-receipt",
            paths["bootstrap"],
            "--output-root",
            paths["output_root"],
            *extra,
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
    paths = _seal_stage_inputs(tmp_path, integration_inputs=True)

    exit_code = _run(
        paths,
        "integration",
        "--integration-scenarios",
        paths["integration_scenarios"],
        "--integration-limits",
        paths["integration_limits"],
    )

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
        "integration_limits",
        "integration_scenarios",
        "predecessor_exit",
        "stage_entry_bundle",
    }
    assert set(document["output_hashes"]) == {
        "integration_plan",
        "stage_authorization",
        "stage_entry_bundle",
    }
    plans = list((Path(paths["output_root"]) / "stage-plans").glob("integration-*.json"))
    assert len(plans) == 1
    assert len(json.loads(plans[0].read_text(encoding="utf-8"))["runs"]) == 32
    _validate_manifest(document)


def test_run_replay_is_idempotent_single_append_only_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_environment(monkeypatch)
    paths = _seal_stage_inputs(tmp_path, integration_inputs=True)

    command = (
        "--integration-scenarios",
        paths["integration_scenarios"],
        "--integration-limits",
        paths["integration_limits"],
    )
    assert _run(paths, "integration", *command) == 0
    first = _sole_manifest(paths)
    assert _run(paths, "integration", *command) == 0
    second = _sole_manifest(paths)

    assert first == second


@pytest.mark.parametrize(
    "arguments",
    (
        (),
        ("--integration-scenarios", "integration_scenarios"),
        ("--integration-limits", "integration_limits"),
    ),
)
def test_integration_run_requires_complete_plan_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
) -> None:
    _clear_environment(monkeypatch)
    paths = _seal_stage_inputs(tmp_path, integration_inputs=True)
    command = tuple(
        paths[value] if value in {"integration_scenarios", "integration_limits"} else value
        for value in arguments
    )

    assert _run(paths, "integration", *command) == 2

    manifest = _sole_manifest(paths)
    assert json.loads(capsys.readouterr().out.strip()) == manifest
    assert manifest["terminal_status"] == "refused"
    assert manifest["error_code"] in {
        "integration_plan_required",
        "integration_limits_missing",
        "integration_scenario_plan_missing",
    }


def test_installed_cli_refuses_missing_integration_plan_authority(tmp_path: Path) -> None:
    paths = _seal_stage_inputs(tmp_path)
    executable = shutil.which("memrelay-eval")
    assert executable is not None
    command = (
        executable,
        "run",
        "--stage",
        "integration",
        "--entry-bundle",
        paths["entry"],
        "--predecessor-exit",
        paths["predecessor"],
        "--authorization",
        paths["authorization"],
        "--conformance-report",
        paths["conformance"],
        "--bootstrap-receipt",
        paths["bootstrap"],
        "--output-root",
        paths["output_root"],
    )

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {*CI_MARKERS, *AMBIENT_MARKERS}
    }
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 2
    manifest = json.loads(completed.stdout)
    assert manifest["error_code"] == "integration_plan_required"
    assert manifest["terminal_status"] == "refused"


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


def test_missing_conformance_report_refuses_before_integration_enrollment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear_environment(monkeypatch)
    paths = _seal_stage_inputs(tmp_path)

    exit_code = main(
        [
            "run",
            "--stage",
            "integration",
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

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "conformance_report_missing"


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


def test_manifest_publish_failure_still_emits_the_manifest_before_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed on-disk publish must never silently swallow the terminal manifest.

    Regression test for a Copilot automated-review finding: the manifest was
    previously printed only *after* ``_write_immutable_stage_manifest``
    succeeded, so a write failure (disk full, permissions, a genuine publish
    conflict) propagated as an unhandled exception with no manifest ever
    reaching stdout, violating the "always emits exactly one manifest on every
    terminal path" contract.
    """

    import memrelay_eval.cli.commands as commands_module
    from memrelay_eval.domain.errors import StageControlError

    def _boom(path: Path, data: bytes) -> None:
        if path.parent.name == "commands":
            raise StageControlError("stage_command_manifest_publish_failed", (str(path),))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    monkeypatch.setattr(commands_module, "_write_immutable_stage_manifest", _boom)
    _clear_environment(monkeypatch)
    paths = _seal_stage_inputs(tmp_path, integration_inputs=True)

    with pytest.raises(StageControlError) as failure:
        _run(
            paths,
            "integration",
            "--integration-scenarios",
            paths["integration_scenarios"],
            "--integration-limits",
            paths["integration_limits"],
        )

    assert failure.value.code == "stage_command_manifest_publish_failed"
    printed = json.loads(capsys.readouterr().out.strip())
    assert printed["command"] == "run"
    assert printed["terminal_status"] == "succeeded"
    _validate_manifest(printed)
