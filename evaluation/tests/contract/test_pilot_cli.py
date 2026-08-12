from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from memrelay_eval.analysis.gates import CategoricalGateDecision
from memrelay_eval.cli.main import main
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
from memrelay_eval.orchestration.pilot import (
    FrozenPilotPlan,
    FrozenPowerPublication,
    PilotBudget,
    PilotCategoricalGateEvidence,
    PilotContracts,
    PilotEvidenceCompleteness,
    PilotExitEvidence,
    PilotPanelEvidence,
    PilotTask,
)
from memrelay_eval.orchestration.stages import StageAuthorization, StageEntryBundle, StageExitBundle
from memrelay_eval.scoring.blinding import LEAKAGE_AUC_UPPER_BOUND
from memrelay_eval.scoring.calibration import AGREEMENT_THRESHOLD, HUMAN_CALIBRATION_MAE_THRESHOLD
from memrelay_eval.scoring.reliability import CriterionAgreement, GateDecision, PanelGateEvidence
from memrelay_eval.scoring.rubric import JUDGE_CRITERIA

HASH = "a" * 64
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


def _conformance_authorities(root: Path, locks: dict[str, str]) -> tuple[Path, Path]:
    bootstrap = bootstrap_receipt_bytes(
        build_bootstrap_receipt(
            mode="unpaid_ci",
            runtime_lock={"lock_sha256": locks["runtime_lock_sha256"]},
            input_hashes={"runtime": HASH},
            output_hashes={"telemetry": HASH},
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
        input_hashes={"catalog_to_report_sha256": HASH},
        bootstrap_receipt_sha256=sha256(bootstrap).hexdigest(),
    )
    report_path = root / "conformance-report.json"
    bootstrap_path = root / "bootstrap-receipt.json"
    report_path.write_bytes(report_bytes(report))
    bootstrap_path.write_bytes(bootstrap)
    return report_path, bootstrap_path


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
    gates = {
        "agreement": GateDecision("passed", AGREEMENT_THRESHOLD, AGREEMENT_THRESHOLD, "minimum"),
        "human_calibration": GateDecision(
            "passed", HUMAN_CALIBRATION_MAE_THRESHOLD, HUMAN_CALIBRATION_MAE_THRESHOLD, "maximum"
        ),
        "leakage": GateDecision(
            "passed", LEAKAGE_AUC_UPPER_BOUND, LEAKAGE_AUC_UPPER_BOUND, "maximum"
        ),
    }
    panel = PilotPanelEvidence(
        plan.stage_id,
        plan.protocol_id,
        plan.contracts.model_lock_sha256,
        plan.contracts.evidence_matrix_sha256,
        PanelGateEvidence(
            HASH,
            HASH,
            HASH,
            "diverse",
            {
                criterion: CriterionAgreement("icc", gates["agreement"])
                for criterion in JUDGE_CRITERIA
            },
            gates,
            {"judge": 0.0},
            dict.fromkeys(JUDGE_CRITERIA, 0.0),
            (),
        ),
    )
    categorical = tuple(
        PilotCategoricalGateEvidence(
            gate,
            plan.stage_id,
            plan.protocol_id,
            plan.contracts.model_lock_sha256,
            plan.contracts.evidence_matrix_sha256,
            CategoricalGateDecision(f"{plan.stage_id}:{gate}", "pass", (), (), HASH, (), False),
        )
        for gate in ("security", "governance", "grading", "evidence", "causal")
    )
    return PilotExitEvidence(
        plan.digest,
        PilotEvidenceCompleteness({"mandatory": True, "optional": True}, ("mandatory",)),
        panel,
        categorical,
        HASH,
        HASH,
        HASH,
        HASH,
        FrozenPowerPublication(HASH, HASH, HASH, ("registered",), {"registered": 10_000}),
    )


def test_pilot_entry_requires_sealed_128_unit_plan(monkeypatch, tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    for marker in CI_MARKERS:
        monkeypatch.delenv(marker, raising=False)
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
    entry = StageEntryBundle(stage_id, StageKind.PILOT, protocol_id, StageKind.INTEGRATION, locks)
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
    report_path, bootstrap_path = _conformance_authorities(tmp_path, entry.locks)

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
                "--conformance-report",
                str(report_path),
                "--bootstrap-receipt",
                str(bootstrap_path),
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
                "--conformance-report",
                str(report_path),
                "--bootstrap-receipt",
                str(bootstrap_path),
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
