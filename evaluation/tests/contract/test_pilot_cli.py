from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from memrelay_eval.cli.main import main
from memrelay_eval.domain.ids import ProtocolId, StageAuthorizationId, StageId
from memrelay_eval.domain.policies import STAGE_ENTRY_LOCK_FIELDS
from memrelay_eval.domain.states import StageKind, StageState
from memrelay_eval.orchestration.pilot import (
    FrozenPilotPlan,
    FrozenPowerPublication,
    PilotBudget,
    PilotContracts,
    PilotEvidenceCompleteness,
    PilotExitEvidence,
    PilotPanelMetrics,
    PilotTask,
)
from memrelay_eval.orchestration.stages import StageAuthorization, StageEntryBundle, StageExitBundle

HASH = "a" * 64


def _pilot_plan(stage_id: StageId, protocol_id: ProtocolId) -> FrozenPilotPlan:
    return FrozenPilotPlan(
        stage_id,
        protocol_id,
        tuple(
            PilotTask(
                f"task-{task}",
                tuple(f"unit-{task}-{unit}" for unit in range(8)),
                {
                    f"unit-{task}-{unit}": (f"session-{task}-{unit}-a", f"session-{task}-{unit}-b")
                    for unit in range(8)
                },
            )
            for task in range(16)
        ),
        PilotContracts(**dict.fromkeys(PilotContracts.__dataclass_fields__, HASH)),
        PilotBudget(1.0, 1.0, {"standard": 1}),
    )


def _exit_evidence(plan: FrozenPilotPlan) -> PilotExitEvidence:
    return PilotExitEvidence(
        plan.digest,
        PilotEvidenceCompleteness({"mandatory": True, "optional": True}, ("mandatory",)),
        PilotPanelMetrics(0.70, 0.10, 0.60),
        dict.fromkeys(
            ("panel", "blinding", "security", "governance", "grading", "evidence", "causal"),
            "passed",
        ),
        HASH,
        HASH,
        HASH,
        HASH,
        FrozenPowerPublication(HASH, HASH, HASH, ("registered",), {"registered": 10_000}),
    )


def test_pilot_entry_requires_sealed_128_unit_plan(
    monkeypatch, tmp_path, capsys
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv("CI", raising=False)
    predecessor = StageExitBundle(
        StageId.new(),
        StageKind.INTEGRATION,
        ProtocolId.new(),
        "b" * 64,
        "c" * 64,
        StageState.ACCEPTED,
        "d" * 64,
        "e" * 64,
        StageAuthorizationId.new(),
    )
    stage_id = StageId.new()
    protocol_id = ProtocolId.new()
    locks = dict.fromkeys(STAGE_ENTRY_LOCK_FIELDS, HASH)
    locks["preceding_exit_sha256"] = predecessor.digest
    entry = StageEntryBundle(
        stage_id, StageKind.PILOT, protocol_id, StageKind.INTEGRATION, locks
    )
    now = datetime.now(UTC)
    authorization = StageAuthorization(
        StageAuthorizationId.new(),
        stage_id,
        StageKind.PILOT,
        protocol_id,
        entry.digest,
        entry.envelope_sha256,
        "operator-1",
        "operator",
        now - timedelta(hours=1),
        now + timedelta(hours=1),
        True,
    )
    entry_path = tmp_path / "entry.json"
    predecessor_path = tmp_path / "predecessor.json"
    authorization_path = tmp_path / "authorization.json"
    entry_path.write_bytes(entry.bytes())
    predecessor_path.write_bytes(predecessor.bytes())
    authorization_path.write_bytes(authorization.bytes())

    assert (
        main(
            [
                "run",
                "--stage",
                "pilot",
                "--entry-bundle",
                str(entry_path),
                "--predecessor-exit",
                str(predecessor_path),
                "--authorization",
                str(authorization_path),
                "--output-root",
                str(tmp_path / "artifacts"),
            ]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().out)["error_code"] == "pilot_plan_required"

    plan_path = tmp_path / "pilot-plan.json"
    plan_path.write_bytes(_pilot_plan(stage_id, protocol_id).bytes())
    assert (
        main(
            [
                "run",
                "--stage",
                "pilot",
                "--entry-bundle",
                str(entry_path),
                "--predecessor-exit",
                str(predecessor_path),
                "--authorization",
                str(authorization_path),
                "--pilot-plan",
                str(plan_path),
                "--output-root",
                str(tmp_path / "artifacts"),
            ]
        )
        == 0
    )
    capsys.readouterr()

    pilot_plan = _pilot_plan(stage_id, protocol_id)
    plan_path.write_bytes(pilot_plan.bytes())
    evidence_path = tmp_path / "pilot-exit-evidence.json"
    evidence_path.write_bytes(_exit_evidence(pilot_plan).bytes())
    assert (
        main(
            [
                "pilot-gate",
                "--pilot-plan",
                str(plan_path),
                "--exit-evidence",
                str(evidence_path),
                "--output-root",
                str(tmp_path / "pilot-artifacts"),
            ]
        )
        == 0
    )
    decision = json.loads(capsys.readouterr().out)
    assert decision["evidence_classification"] == "non-confirmatory"
    assert decision["confirmation_eligible"] is False
