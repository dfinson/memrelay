"""Model-stage exit authority must reject incomplete or fabricated evidence."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.cli.main import main
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageAuthorizationId, StageId
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
from memrelay_eval.orchestration.limits import PrimaryModelStageLimits
from memrelay_eval.orchestration.stages import (
    StageAuthorization,
    StageEntryBundle,
    StageExitBundle,
    conclude_primary_stage,
    load_primary_stage_plan,
)

from tests.unit.orchestration.test_primary_secondary_plans import (
    _analysis_authority,
    _exit_evidence,
    _model_lock,
    _primary_plan,
    _secondary_inputs,
)


def _conformance_authorities(root: Path, locks: dict[str, str]) -> tuple[Path, Path]:
    bootstrap = bootstrap_receipt_bytes(
        build_bootstrap_receipt(
            mode="unpaid_ci",
            runtime_lock={"lock_sha256": locks["runtime_lock_sha256"]},
            input_hashes={"runtime": "a" * 64},
            output_hashes={"telemetry": "a" * 64},
            environment_sha256=locks["environment_sha256"],
            protocol_sha256=locks["protocol_sha256"],
        )
    )
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
    receipts = registry.execute(
        ConformanceContext(
            mode="unpaid_ci",
            evaluation_root=Path(__file__).parents[2],
            run_root=root,
            stage_locks=locks,
            bootstrap_receipt={},
        )
    )
    report = build_conformance_report(
        mode="unpaid_ci",
        stage_locks=locks,
        proof_receipts=receipts,
        input_hashes={"catalog_to_report_sha256": "a" * 64},
        bootstrap_receipt_sha256=sha256(bootstrap).hexdigest(),
    )
    report_path = root / "conformance-report.json"
    bootstrap_path = root / "bootstrap-receipt.json"
    report_path.write_bytes(report_bytes(report))
    bootstrap_path.write_bytes(bootstrap)
    return report_path, bootstrap_path


def test_primary_exit_requires_typed_complete_claim_authority() -> None:
    plan, _pilot_exit, _entry = _primary_plan()

    with pytest.raises(StageControlError, match="primary claim authority invalid"):
        conclude_primary_stage(
            plan=plan,
            terminal_unit_ids=tuple(unit.unit_id for unit in plan.units),
            reconciliation_sha256="f" * 64,
            exit_evidence_sha256=_exit_evidence(),
            analysis_authority=SimpleNamespace(),  # type: ignore[arg-type]
        )

    authority = _analysis_authority()
    with pytest.raises(StageControlError, match="primary claim family incomplete"):
        replace(authority, required_claim_ids=("claim-1",))


def test_primary_plan_rejects_overflow_or_missing_complete_itt_units() -> None:
    plan, _pilot_exit, _entry = _primary_plan()

    with pytest.raises(StageControlError, match="primary enrollment envelope invalid"):
        replace(plan, units=(*plan.units, plan.units[0]))
    with pytest.raises(StageControlError, match="primary enrollment envelope invalid"):
        replace(plan, units=plan.units[:-1])


def test_primary_cli_publishes_immutable_exact_512_unit_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    for name in ("CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)
    now = datetime.now(UTC)
    pilot_exit = StageExitBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.PILOT,
        protocol_id=ProtocolId.new(),
        entry_bundle_sha256="a" * 64,
        preceding_exit_sha256="b" * 64,
        status=StageState.ACCEPTED,
        reconciliation_sha256="c" * 64,
        inclusion_decision_sha256="d" * 64,
        authorization_id=StageAuthorizationId.new(),
    )
    limits = PrimaryModelStageLimits(
        ai_credit_cap=10.0, usd_cap=20.0, task_class_active_seconds={"default": 60.0}
    )
    locks = dict.fromkeys(STAGE_ENTRY_LOCK_FIELDS, "e" * 64)
    locks["preceding_exit_sha256"] = pilot_exit.digest
    locks["limits_sha256"] = limits.digest
    entry = StageEntryBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.PRIMARY,
        protocol_id=ProtocolId.new(),
        predecessor_stage_kind=StageKind.PILOT,
        locks=locks,
    )
    authorization = StageAuthorization(
        authorization_id=StageAuthorizationId.new(),
        stage_id=entry.stage_id,
        stage_kind=StageKind.PRIMARY,
        protocol_id=entry.protocol_id,
        entry_bundle_sha256=entry.digest,
        envelope_sha256=entry.envelope_sha256,
        authorizer_id="operator-1",
        authorizer_role="operator",
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(minutes=1),
        paid_execution=True,
    )
    paths = {
        "entry": tmp_path / "entry.json",
        "exit": tmp_path / "pilot.json",
        "authorization": tmp_path / "authorization.json",
        "tasks": tmp_path / "tasks.json",
        "limits": tmp_path / "limits.json",
    }
    paths["entry"].write_bytes(entry.bytes())
    paths["exit"].write_bytes(pilot_exit.bytes())
    paths["authorization"].write_bytes(authorization.bytes())
    paths["tasks"].write_bytes(
        canonical_bytes(
            {
                "task_families": {
                    f"F{family}": [f"task-{family}-{number}" for number in range(4)]
                    for family in range(1, 9)
                }
            }
        )
    )
    paths["limits"].write_bytes(
        canonical_bytes(
            {
                "ai_credit_cap": 10.0,
                "usd_cap": 20.0,
                "task_class_active_seconds": {"default": 60.0},
            }
        )
    )
    report_path, bootstrap_path = _conformance_authorities(tmp_path, entry.locks)

    assert (
        main(
            [
                "run",
                "--stage",
                "primary",
                "--entry-bundle",
                str(paths["entry"]),
                "--predecessor-exit",
                str(paths["exit"]),
                "--authorization",
                str(paths["authorization"]),
                "--conformance-report",
                str(report_path),
                "--bootstrap-receipt",
                str(bootstrap_path),
                "--task-plan",
                str(paths["tasks"]),
                "--limits",
                str(paths["limits"]),
                "--output-root",
                str(tmp_path / "output"),
            ]
        )
        == 0
    )
    plans = list((tmp_path / "output" / "stage-plans").glob("primary-*.json"))
    assert len(plans) == 1
    assert len(load_primary_stage_plan(plans[0].read_bytes()).units) == 512


def test_secondary_cli_publishes_separate_96_unit_role_strata(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    for name in ("CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(name, raising=False)
    plan, pilot_exit, primary_entry = _primary_plan()
    conclusion = conclude_primary_stage(
        plan=plan,
        terminal_unit_ids=tuple(unit.unit_id for unit in plan.units),
        reconciliation_sha256="f" * 64,
        exit_evidence_sha256=_exit_evidence(),
        analysis_authority=_analysis_authority(),
    )
    primary_exit = StageExitBundle(
        stage_id=primary_entry.stage_id,
        stage_kind=StageKind.PRIMARY,
        protocol_id=primary_entry.protocol_id,
        entry_bundle_sha256=primary_entry.digest,
        preceding_exit_sha256=pilot_exit.digest,
        status=StageState.ACCEPTED,
        reconciliation_sha256=conclusion.reconciliation_sha256,
        inclusion_decision_sha256="9" * 64,
        authorization_id=StageAuthorizationId.new(),
    )
    lock = _model_lock(("M0", "M1", "M2"), {})
    entries, authorizations, _limits = _secondary_inputs(primary_exit, lock["lock_sha256"])
    now = datetime.now(UTC)
    paths = {
        "plan": tmp_path / "primary-plan.json",
        "conclusion": tmp_path / "primary-conclusion.json",
        "exit": tmp_path / "primary-exit.json",
        "lock": tmp_path / "model-lock.json",
        "tasks": tmp_path / "tasks.json",
        "limits": tmp_path / "limits.json",
    }
    paths["plan"].write_bytes(plan.bytes())
    paths["conclusion"].write_bytes(conclusion.bytes())
    paths["exit"].write_bytes(primary_exit.bytes())
    paths["lock"].write_bytes(canonical_bytes(lock))
    paths["tasks"].write_bytes(
        canonical_bytes(
            {
                "task_families": {
                    family: list(task_ids[:2]) for family, task_ids in plan.task_families.items()
                }
            }
        )
    )
    paths["limits"].write_bytes(
        canonical_bytes(
            {
                "ai_credit_cap": 10.0,
                "usd_cap": 20.0,
                "task_class_active_seconds": {"default": 60.0},
            }
        )
    )
    report_path, bootstrap_path = _conformance_authorities(tmp_path, entries["M1"].locks)
    command = [
        "run",
        "--stage",
        "secondary",
        "--entry-bundle",
        str(tmp_path / "entry-M1.json"),
        "--predecessor-exit",
        str(paths["exit"]),
        "--authorization",
        str(tmp_path / "authorization-M1.json"),
        "--conformance-report",
        str(report_path),
        "--bootstrap-receipt",
        str(bootstrap_path),
        "--task-plan",
        str(paths["tasks"]),
        "--limits",
        str(paths["limits"]),
        "--primary-plan",
        str(paths["plan"]),
        "--primary-conclusion",
        str(paths["conclusion"]),
        "--model-lock",
        str(paths["lock"]),
        "--output-root",
        str(tmp_path / "output"),
    ]
    for role in ("M1", "M2"):
        entry_path = tmp_path / f"entry-{role}.json"
        authorization_path = tmp_path / f"authorization-{role}.json"
        entry_path.write_bytes(entries[role].bytes())
        authorization_path.write_bytes(
            replace(
                authorizations[role],
                valid_from=now - timedelta(minutes=1),
                valid_until=now + timedelta(minutes=1),
            ).bytes()
        )
        command.extend(["--secondary-entry", f"{role}:{entry_path}"])
        command.extend(["--secondary-authorization", f"{role}:{authorization_path}"])

    assert main(command) == 0
    plans = list((tmp_path / "output" / "stage-plans").glob("secondary-*.json"))
    assert len(plans) == 1
    document = json.loads(plans[0].read_text(encoding="utf-8"))
    assert len(document["role_plans"]) == 2
    assert all(len(role["units"]) == 96 for role in document["role_plans"])
