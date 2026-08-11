"""Frozen, blinded pilot planning and non-confirmatory exit control."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from memrelay_eval.canonical import canonical_bytes, canonical_digest, verify_digest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageId
from memrelay_eval.domain.states import StageKind
from memrelay_eval.orchestration.stages import StageEntryBundle

PILOT_TASK_COUNT = 16
PILOT_ASSIGNMENT_COUNT = 128
PILOT_ASSIGNMENTS_PER_TASK = PILOT_ASSIGNMENT_COUNT // PILOT_TASK_COUNT
PILOT_EVIDENCE_CLASSIFICATION = "non-confirmatory"
PILOT_REQUIRED_GATES = frozenset(
    {"panel", "blinding", "security", "governance", "grading", "evidence", "causal"}
)
_SHA256_LENGTH = 64


def _require_sha256(value: object, code: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != _SHA256_LENGTH
        or not value.isascii()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise StageControlError(code)
    return value


@dataclass(frozen=True, slots=True)
class PilotTask:
    """One opaque task with exactly eight assignment units.

    Sessions are retained as execution evidence but never become inferential units.
    """

    task_id: str
    assignment_unit_ids: tuple[str, ...]
    session_ids_by_unit: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        units = tuple(self.assignment_unit_ids)
        if (
            not self.task_id
            or len(units) != PILOT_ASSIGNMENTS_PER_TASK
            or len(set(units)) != len(units)
            or any(not isinstance(unit, str) or not unit for unit in units)
            or set(self.session_ids_by_unit) != set(units)
        ):
            raise StageControlError("pilot_task_assignment_shape_invalid")
        sessions = {unit: tuple(value) for unit, value in self.session_ids_by_unit.items()}
        if any(
            len(value) not in {2, 3}
            or len(set(value)) != len(value)
            or any(not isinstance(item, str) or not item for item in value)
            for value in sessions.values()
        ):
            raise StageControlError("pilot_sequence_session_shape_invalid")
        object.__setattr__(self, "assignment_unit_ids", units)
        object.__setattr__(self, "session_ids_by_unit", MappingProxyType(sessions))

    def to_document(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "assignment_unit_ids": list(self.assignment_unit_ids),
            "session_ids_by_unit": {
                unit: list(self.session_ids_by_unit[unit]) for unit in self.assignment_unit_ids
            },
        }


@dataclass(frozen=True, slots=True)
class PilotContracts:
    """Every frozen pre-outcome pilot contract, represented by its immutable digest."""

    assignment_seed_sha256: str
    assignment_blocks_sha256: str
    holdout_sha256: str
    blinding_transform_sha256: str
    panel_rubric_sha256: str
    evidence_matrix_sha256: str
    limits_sha256: str
    analysis_sha256: str
    price_table_sha256: str
    model_lock_sha256: str
    catalog_sha256: str
    configuration_sha256: str

    def __post_init__(self) -> None:
        for value in self.to_document().values():
            _require_sha256(value, "pilot_contract_hash_invalid")

    def to_document(self) -> dict[str, str]:
        return {
            "assignment_seed_sha256": self.assignment_seed_sha256,
            "assignment_blocks_sha256": self.assignment_blocks_sha256,
            "holdout_sha256": self.holdout_sha256,
            "blinding_transform_sha256": self.blinding_transform_sha256,
            "panel_rubric_sha256": self.panel_rubric_sha256,
            "evidence_matrix_sha256": self.evidence_matrix_sha256,
            "limits_sha256": self.limits_sha256,
            "analysis_sha256": self.analysis_sha256,
            "price_table_sha256": self.price_table_sha256,
            "model_lock_sha256": self.model_lock_sha256,
            "catalog_sha256": self.catalog_sha256,
            "configuration_sha256": self.configuration_sha256,
        }


@dataclass(frozen=True, slots=True)
class PilotBudget:
    """The hard paid-call envelope frozen with the pilot plan."""

    ai_credit_cap: float
    usd_cap: float
    task_class_active_seconds: Mapping[str, int]
    task_agent_token_cap: int = 32_000_000
    framework_input_token_cap: int = 6_000_000
    framework_output_token_cap: int = 2_000_000
    elapsed_days_cap: int = 5
    concurrency_cap: int = 2

    def __post_init__(self) -> None:
        active_caps = dict(self.task_class_active_seconds)
        if (
            self.task_agent_token_cap != 32_000_000
            or self.framework_input_token_cap != 6_000_000
            or self.framework_output_token_cap != 2_000_000
            or self.elapsed_days_cap != 5
            or self.concurrency_cap != 2
            or not isinstance(self.ai_credit_cap, (int, float))
            or not isinstance(self.usd_cap, (int, float))
            or isinstance(self.ai_credit_cap, bool)
            or isinstance(self.usd_cap, bool)
            or not math.isfinite(self.ai_credit_cap)
            or not math.isfinite(self.usd_cap)
            or self.ai_credit_cap <= 0
            or self.usd_cap <= 0
            or not active_caps
            or any(
                not task_class
                or not isinstance(seconds, int)
                or isinstance(seconds, bool)
                or seconds <= 0
                for task_class, seconds in active_caps.items()
            )
        ):
            raise StageControlError("pilot_budget_envelope_invalid")
        object.__setattr__(self, "task_class_active_seconds", MappingProxyType(active_caps))

    def to_document(self) -> dict[str, object]:
        return {
            "task_agent_token_cap": self.task_agent_token_cap,
            "ai_credit_cap": self.ai_credit_cap,
            "framework_input_token_cap": self.framework_input_token_cap,
            "framework_output_token_cap": self.framework_output_token_cap,
            "usd_cap": self.usd_cap,
            "task_class_active_seconds": dict(sorted(self.task_class_active_seconds.items())),
            "elapsed_days_cap": self.elapsed_days_cap,
            "concurrency_cap": self.concurrency_cap,
        }


@dataclass(frozen=True, slots=True)
class FrozenPilotPlan:
    """The immutable 128-unit plan that must exist before pilot enrollment."""

    stage_id: StageId
    protocol_id: ProtocolId
    tasks: tuple[PilotTask, ...]
    contracts: PilotContracts
    budget: PilotBudget
    evidence_classification: str = PILOT_EVIDENCE_CLASSIFICATION

    def __post_init__(self) -> None:
        tasks = tuple(sorted(self.tasks, key=lambda item: item.task_id))
        task_ids = tuple(item.task_id for item in tasks)
        unit_ids = tuple(unit for task in tasks for unit in task.assignment_unit_ids)
        if (
            len(tasks) != PILOT_TASK_COUNT
            or len(set(task_ids)) != PILOT_TASK_COUNT
            or len(unit_ids) != PILOT_ASSIGNMENT_COUNT
            or len(set(unit_ids)) != PILOT_ASSIGNMENT_COUNT
        ):
            raise StageControlError("pilot_assignment_cardinality_invalid")
        if self.evidence_classification != PILOT_EVIDENCE_CLASSIFICATION:
            raise StageControlError("pilot_evidence_must_be_non_confirmatory")
        object.__setattr__(self, "tasks", tasks)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "frozen_blinded_pilot_plan",
            "stage_id": str(self.stage_id),
            "protocol_id": str(self.protocol_id),
            "tasks": [task.to_document() for task in self.tasks],
            "contracts": self.contracts.to_document(),
            "budget": self.budget.to_document(),
            "evidence_classification": self.evidence_classification,
            "assignment_count": PILOT_ASSIGNMENT_COUNT,
            "task_count": PILOT_TASK_COUNT,
        }

    def bytes(self) -> bytes:
        return canonical_bytes({**self.to_document(), "digest": self.digest})


def authorize_pilot_plan(entry: StageEntryBundle, plan: FrozenPilotPlan) -> None:
    """Bind the pilot plan to the stage entry locks before any assignment starts."""

    if (
        entry.stage_kind is not StageKind.PILOT
        or entry.stage_id != plan.stage_id
        or entry.protocol_id != plan.protocol_id
    ):
        raise StageControlError("pilot_plan_stage_scope_mismatch")
    expected_locks = {
        "catalog_sha256": plan.contracts.catalog_sha256,
        "environment_sha256": plan.contracts.configuration_sha256,
        "model_lock_sha256": plan.contracts.model_lock_sha256,
        "price_table_sha256": plan.contracts.price_table_sha256,
        "limits_sha256": plan.contracts.limits_sha256,
    }
    if any(entry.locks[name] != value for name, value in expected_locks.items()):
        raise StageControlError("pilot_plan_lock_mismatch")


def load_pilot_plan(data: bytes) -> FrozenPilotPlan:
    """Load only canonical sealed pilot plans, preserving the frozen assignment."""

    try:
        import json

        document = json.loads(data.decode("utf-8"))
        if not isinstance(document, dict) or not verify_digest(document):
            raise ValueError
        if document.get("artifact_type") != "frozen_blinded_pilot_plan":
            raise ValueError
        tasks = tuple(
            PilotTask(
                task_id=str(item["task_id"]),
                assignment_unit_ids=tuple(str(value) for value in item["assignment_unit_ids"]),
                session_ids_by_unit={
                    str(key): tuple(str(value) for value in values)
                    for key, values in dict(item["session_ids_by_unit"]).items()
                },
            )
            for item in document["tasks"]
        )
        plan = FrozenPilotPlan(
            stage_id=StageId(str(document["stage_id"])),
            protocol_id=ProtocolId(str(document["protocol_id"])),
            tasks=tasks,
            contracts=PilotContracts(**dict(document["contracts"])),
            budget=PilotBudget(**dict(document["budget"])),
            evidence_classification=str(document["evidence_classification"]),
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, StageControlError) as error:
        raise StageControlError("pilot_plan_corrupt") from error
    if plan.bytes() != data:
        raise StageControlError("pilot_plan_corrupt")
    return plan


@dataclass(frozen=True, slots=True)
class PilotEvidenceCompleteness:
    """Mandatory evidence remains non-compensatory even above the aggregate threshold."""

    item_present: Mapping[str, bool]
    mandatory_item_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        items = dict(self.item_present)
        mandatory = tuple(sorted(set(self.mandatory_item_ids)))
        if (
            not items
            or not mandatory
            or not set(mandatory).issubset(items)
            or any(not item for item in items)
            or any(not isinstance(present, bool) for present in items.values())
        ):
            raise StageControlError("pilot_evidence_completeness_invalid")
        object.__setattr__(self, "item_present", MappingProxyType(items))
        object.__setattr__(self, "mandatory_item_ids", mandatory)

    @property
    def proportion(self) -> float:
        return sum(self.item_present.values()) / len(self.item_present)

    @property
    def mandatory_complete(self) -> bool:
        return all(self.item_present[item] for item in self.mandatory_item_ids)


@dataclass(frozen=True, slots=True)
class PilotPanelMetrics:
    reliability: float | None
    calibration_mae: float | None
    leakage_auc_upper_95: float | None

    def __post_init__(self) -> None:
        values = (self.reliability, self.calibration_mae, self.leakage_auc_upper_95)
        if any(
            value is not None
            and (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
                or value > 1
            )
            for value in values
        ):
            raise StageControlError("pilot_panel_metrics_invalid")

    @property
    def passed(self) -> bool:
        return (
            self.reliability is not None
            and self.reliability >= 0.70
            and self.calibration_mae is not None
            and self.calibration_mae <= 0.10
            and self.leakage_auc_upper_95 is not None
            and self.leakage_auc_upper_95 <= 0.60
        )


@dataclass(frozen=True, slots=True)
class FrozenPowerPublication:
    """The whole registered cell set, never a favorable result subset."""

    protocol_sha256: str
    result_sha256: str
    independent_spot_check_sha256: str
    registered_cell_ids: tuple[str, ...]
    cells: Mapping[str, int]

    def __post_init__(self) -> None:
        for value in (
            self.protocol_sha256,
            self.result_sha256,
            self.independent_spot_check_sha256,
        ):
            _require_sha256(value, "pilot_power_publication_invalid")
        cells = dict(self.cells)
        registered_cells = tuple(sorted(set(self.registered_cell_ids)))
        if (
            not cells
            or not registered_cells
            or set(cells) != set(registered_cells)
            or any(
                not cell
                or not isinstance(trials, int)
                or isinstance(trials, bool)
                or trials < 10_000
                for cell, trials in cells.items()
            )
        ):
            raise StageControlError("pilot_power_publication_incomplete")
        object.__setattr__(self, "cells", MappingProxyType(cells))
        object.__setattr__(self, "registered_cell_ids", registered_cells)

    def to_document(self) -> dict[str, object]:
        return {
            "protocol_sha256": self.protocol_sha256,
            "result_sha256": self.result_sha256,
            "independent_spot_check_sha256": self.independent_spot_check_sha256,
            "registered_cell_ids": list(self.registered_cell_ids),
            "cells": dict(sorted(self.cells.items())),
        }


@dataclass(frozen=True, slots=True)
class PilotExitEvidence:
    plan_sha256: str
    completeness: PilotEvidenceCompleteness
    panel: PilotPanelMetrics
    required_gate_statuses: Mapping[str, str]
    variance_sha256: str
    icc_sha256: str
    attrition_sha256: str
    harm_sha256: str
    power: FrozenPowerPublication

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "pilot_exit_lineage_invalid")
        for value in (
            self.variance_sha256,
            self.icc_sha256,
            self.attrition_sha256,
            self.harm_sha256,
        ):
            _require_sha256(value, "pilot_publication_missing")
        statuses = dict(self.required_gate_statuses)
        if set(statuses) != PILOT_REQUIRED_GATES or any(
            value not in {"passed", "failed", "missing"} for value in statuses.values()
        ):
            raise StageControlError("pilot_required_gate_status_invalid")
        object.__setattr__(self, "required_gate_statuses", MappingProxyType(statuses))

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "plan_sha256": self.plan_sha256,
            "completeness": {
                "item_present": dict(sorted(self.completeness.item_present.items())),
                "mandatory_item_ids": list(self.completeness.mandatory_item_ids),
            },
            "panel": {
                "reliability": self.panel.reliability,
                "calibration_mae": self.panel.calibration_mae,
                "leakage_auc_upper_95": self.panel.leakage_auc_upper_95,
            },
            "required_gate_statuses": dict(sorted(self.required_gate_statuses.items())),
            "variance_sha256": self.variance_sha256,
            "icc_sha256": self.icc_sha256,
            "attrition_sha256": self.attrition_sha256,
            "harm_sha256": self.harm_sha256,
            "power": self.power.to_document(),
        }

    def bytes(self) -> bytes:
        return canonical_bytes({**self.to_document(), "digest": self.digest})


def load_pilot_exit_evidence(data: bytes) -> PilotExitEvidence:
    """Load canonical, sealed pilot exit evidence without decoding efficacy data."""

    try:
        import json

        document = json.loads(data.decode("utf-8"))
        if not isinstance(document, dict) or not verify_digest(document):
            raise ValueError
        completeness_document = dict(document["completeness"])
        panel_document = dict(document["panel"])
        power_document = dict(document["power"])
        evidence = PilotExitEvidence(
            plan_sha256=str(document["plan_sha256"]),
            completeness=PilotEvidenceCompleteness(
                item_present=dict(completeness_document["item_present"]),
                mandatory_item_ids=tuple(completeness_document["mandatory_item_ids"]),
            ),
            panel=PilotPanelMetrics(
                reliability=panel_document["reliability"],
                calibration_mae=panel_document["calibration_mae"],
                leakage_auc_upper_95=panel_document["leakage_auc_upper_95"],
            ),
            required_gate_statuses=dict(document["required_gate_statuses"]),
            variance_sha256=str(document["variance_sha256"]),
            icc_sha256=str(document["icc_sha256"]),
            attrition_sha256=str(document["attrition_sha256"]),
            harm_sha256=str(document["harm_sha256"]),
            power=FrozenPowerPublication(
                protocol_sha256=str(power_document["protocol_sha256"]),
                result_sha256=str(power_document["result_sha256"]),
                independent_spot_check_sha256=str(power_document["independent_spot_check_sha256"]),
                registered_cell_ids=tuple(power_document["registered_cell_ids"]),
                cells=dict(power_document["cells"]),
            ),
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, StageControlError) as error:
        raise StageControlError("pilot_exit_evidence_corrupt") from error
    if evidence.bytes() != data:
        raise StageControlError("pilot_exit_evidence_corrupt")
    return evidence


@dataclass(frozen=True, slots=True)
class PilotExitDecision:
    """A non-confirmatory terminal decision with a whole-stage repair requirement."""

    plan_sha256: str
    exit_evidence_sha256: str
    status: str
    failure_codes: tuple[str, ...]
    evidence_classification: str = PILOT_EVIDENCE_CLASSIFICATION

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "pilot_exit_lineage_invalid")
        _require_sha256(self.exit_evidence_sha256, "pilot_exit_lineage_invalid")
        if (
            self.status not in {"accepted", "rejected"}
            or (self.status == "accepted" and self.failure_codes)
            or (self.status == "rejected" and not self.failure_codes)
        ):
            raise StageControlError("pilot_exit_status_invalid")
        if self.evidence_classification != PILOT_EVIDENCE_CLASSIFICATION:
            raise StageControlError("pilot_evidence_must_be_non_confirmatory")
        object.__setattr__(self, "failure_codes", tuple(sorted(set(self.failure_codes))))

    @property
    def confirmation_eligible(self) -> bool:
        return False

    @property
    def fresh_stage_required(self) -> bool:
        return self.status == "rejected"

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "blinded_pilot_exit",
            "plan_sha256": self.plan_sha256,
            "exit_evidence_sha256": self.exit_evidence_sha256,
            "status": self.status,
            "failure_codes": list(self.failure_codes),
            "evidence_classification": self.evidence_classification,
            "confirmation_eligible": False,
            "fresh_stage_required": self.fresh_stage_required,
            "no_favorable_subset": True,
            "threshold_weakening_forbidden": True,
        }

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def bytes(self) -> bytes:
        return canonical_bytes({**self.to_document(), "digest": self.digest})


def evaluate_pilot_exit(plan: FrozenPilotPlan, evidence: PilotExitEvidence) -> PilotExitDecision:
    """Gate the entire frozen pilot without access to decoded efficacy outcomes."""

    if evidence.plan_sha256 != plan.digest:
        raise StageControlError("pilot_exit_plan_mismatch")
    failures: list[str] = []
    if not evidence.completeness.mandatory_complete:
        failures.append("pilot_mandatory_evidence_missing")
    if evidence.completeness.proportion < 0.98:
        failures.append("pilot_evidence_completeness_below_98_percent")
    if not evidence.panel.passed:
        failures.append("pilot_panel_or_blinding_gate_failed")
    failures.extend(
        f"pilot_{name}_gate_{status}"
        for name, status in evidence.required_gate_statuses.items()
        if status != "passed"
    )
    return PilotExitDecision(
        plan_sha256=plan.digest,
        exit_evidence_sha256=evidence.digest,
        status="rejected" if failures else "accepted",
        failure_codes=tuple(failures),
    )


def require_fresh_pilot_stage(
    plan: FrozenPilotPlan, decision: PilotExitDecision, replacement_stage_id: StageId
) -> None:
    """Forbid resuming, replacing, or selectively rerunning a rejected pilot."""

    if decision.plan_sha256 != plan.digest:
        raise StageControlError("pilot_exit_plan_mismatch")
    if not decision.fresh_stage_required:
        raise StageControlError("pilot_rerun_not_authorized")
    if replacement_stage_id == plan.stage_id:
        raise StageControlError("pilot_fresh_stage_id_required")
