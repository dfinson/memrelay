"""Immutable 32-run integration-stage planning, recovery, and exit control."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from types import MappingProxyType

from memrelay_eval.canonical import canonical_bytes, canonical_digest, verify_digest
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import AttemptId, ProtocolId, RunId, ScenarioId, StageId
from memrelay_eval.domain.states import StageKind
from memrelay_eval.orchestration.limits import IntegrationStageLimits
from memrelay_eval.orchestration.stages import (
    StageAuthorization,
    StageEntryBundle,
    StageExitBundle,
    authorize_stage_entry,
)

INTEGRATION_SCENARIO_COUNT = 8
INTEGRATION_CONDITION_COUNT = 2
INTEGRATION_REPEAT_COUNT = 2
INTEGRATION_RUN_COUNT = 32
INTEGRATION_MODEL_ROLE = "R-COP-M0"
INTEGRATION_EVIDENCE_CLASSIFICATION = "infrastructure_conformance"
INTEGRATION_REQUIRED_SUMMARIES = frozenset(
    {"run", "reconciliation", "backup", "parity", "cost", "fault"}
)
INTEGRATION_REQUIRED_CATEGORICAL_GATES = frozenset(
    {"security", "governance", "grading", "evidence", "causal"}
)
_SHA256 = frozenset("0123456789abcdef")


def _require_sha256(value: object, code: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256 for character in value)
    ):
        raise StageControlError(code)
    return value


@dataclass(frozen=True, slots=True)
class IntegrationRun:
    """One opaque assigned integration run; condition slots never reveal treatment."""

    run_id: RunId
    scenario_id: ScenarioId
    model_role: str
    condition_slot: int
    repeat: int
    order_index: int
    concurrency_lane: int

    def __post_init__(self) -> None:
        if (
            self.model_role != INTEGRATION_MODEL_ROLE
            or self.condition_slot not in range(INTEGRATION_CONDITION_COUNT)
            or self.repeat not in range(INTEGRATION_REPEAT_COUNT)
            or self.order_index not in range(INTEGRATION_RUN_COUNT)
            or self.concurrency_lane not in {0, 1}
        ):
            raise StageControlError("integration_run_shape_invalid")

    def to_document(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "scenario_id": str(self.scenario_id),
            "model_role": self.model_role,
            "condition_slot": self.condition_slot,
            "repeat": self.repeat,
            "order_index": self.order_index,
            "concurrency_lane": self.concurrency_lane,
        }


@dataclass(frozen=True, slots=True)
class FrozenIntegrationPlan:
    """Sealed, opaque 32-run envelope with balanced order and bounded concurrency."""

    stage_id: StageId
    protocol_id: ProtocolId
    entry_bundle_sha256: str
    conformance_exit_sha256: str
    limits_sha256: str
    runs: tuple[IntegrationRun, ...]
    evidence_classification: str = INTEGRATION_EVIDENCE_CLASSIFICATION

    def __post_init__(self) -> None:
        for value in (
            self.entry_bundle_sha256,
            self.conformance_exit_sha256,
            self.limits_sha256,
        ):
            _require_sha256(value, "integration_plan_lineage_invalid")
        runs = tuple(sorted(self.runs, key=lambda item: item.order_index))
        expected_cells = {
            (scenario_id, condition_slot, repeat)
            for scenario_id in {item.scenario_id for item in runs}
            for condition_slot in range(INTEGRATION_CONDITION_COUNT)
            for repeat in range(INTEGRATION_REPEAT_COUNT)
        }
        observed_cells = {(item.scenario_id, item.condition_slot, item.repeat) for item in runs}
        if (
            self.evidence_classification != INTEGRATION_EVIDENCE_CLASSIFICATION
            or len(runs) != INTEGRATION_RUN_COUNT
            or len({item.scenario_id for item in runs}) != INTEGRATION_SCENARIO_COUNT
            or len({item.run_id for item in runs}) != INTEGRATION_RUN_COUNT
            or len({item.order_index for item in runs}) != INTEGRATION_RUN_COUNT
            or tuple(item.order_index for item in runs) != tuple(range(INTEGRATION_RUN_COUNT))
            or observed_cells != expected_cells
            or len(observed_cells) != INTEGRATION_RUN_COUNT
            or any(item.model_role != INTEGRATION_MODEL_ROLE for item in runs)
            or any(
                {runs[index].condition_slot, runs[index + 1].condition_slot} != {0, 1}
                for index in range(0, INTEGRATION_RUN_COUNT, 2)
            )
            or any(
                {runs[index].concurrency_lane, runs[index + 1].concurrency_lane} != {0, 1}
                for index in range(0, INTEGRATION_RUN_COUNT, 2)
            )
        ):
            raise StageControlError("integration_enrollment_envelope_invalid")
        object.__setattr__(self, "runs", runs)

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "frozen_integration_plan",
            "stage_id": str(self.stage_id),
            "protocol_id": str(self.protocol_id),
            "entry_bundle_sha256": self.entry_bundle_sha256,
            "conformance_exit_sha256": self.conformance_exit_sha256,
            "limits_sha256": self.limits_sha256,
            "model_role": INTEGRATION_MODEL_ROLE,
            "scenario_count": INTEGRATION_SCENARIO_COUNT,
            "condition_count": INTEGRATION_CONDITION_COUNT,
            "repeat_count": INTEGRATION_REPEAT_COUNT,
            "run_count": INTEGRATION_RUN_COUNT,
            "runs": [run.to_document() for run in self.runs],
            "evidence_classification": self.evidence_classification,
        }

    def bytes(self) -> bytes:
        return canonical_bytes({**self.to_document(), "digest": self.digest})


def seal_integration_plan(
    *,
    entry_bundle: StageEntryBundle,
    conformance_exit: StageExitBundle,
    authorization: StageAuthorization,
    scenario_ids: Sequence[ScenarioId],
    limits: IntegrationStageLimits,
    now: datetime,
) -> FrozenIntegrationPlan:
    """Authorize and seal the exact fixed envelope before any start receipt exists."""

    authorize_stage_entry(
        stage_kind=StageKind.INTEGRATION,
        entry_bundle=entry_bundle,
        predecessor_exit=conformance_exit,
        authorization=authorization,
        now=now,
    )
    if (
        conformance_exit.stage_kind is not StageKind.CONFORMANCE
        or not conformance_exit.is_accepted_and_complete
    ):
        raise StageControlError("integration_conformance_exit_not_accepted")
    if entry_bundle.locks["limits_sha256"] != limits.digest:
        raise StageControlError("integration_limits_lock_mismatch")
    scenarios = tuple(sorted(set(scenario_ids), key=str))
    if len(scenarios) != INTEGRATION_SCENARIO_COUNT:
        raise StageControlError("integration_scenario_envelope_invalid")

    pairs: list[tuple[ScenarioId, int, int]] = [
        (scenario_id, repeat, pair_rank)
        for scenario_id in scenarios
        for repeat in range(INTEGRATION_REPEAT_COUNT)
        for pair_rank in (0,)
    ]
    pairs.sort(
        key=lambda item: canonical_digest(
            {
                "stage_id": str(entry_bundle.stage_id),
                "scenario_id": str(item[0]),
                "repeat": item[1],
            }
        )
    )
    runs: list[IntegrationRun] = []
    for pair_index, (scenario_id, repeat, _) in enumerate(pairs):
        first_slot = (
            int(
                canonical_digest(
                    {
                        "stage_id": str(entry_bundle.stage_id),
                        "scenario_id": str(scenario_id),
                        "repeat": repeat,
                        "schedule": "integration-32-v1",
                    }
                )[0],
                16,
            )
            % 2
        )
        for offset, condition_slot in enumerate((first_slot, 1 - first_slot)):
            run_id = RunId.from_digest(
                canonical_digest(
                    {
                        "stage_id": str(entry_bundle.stage_id),
                        "scenario_id": str(scenario_id),
                        "model_role": INTEGRATION_MODEL_ROLE,
                        "condition_slot": condition_slot,
                        "repeat": repeat,
                    }
                )
            )
            runs.append(
                IntegrationRun(
                    run_id=run_id,
                    scenario_id=scenario_id,
                    model_role=INTEGRATION_MODEL_ROLE,
                    condition_slot=condition_slot,
                    repeat=repeat,
                    order_index=pair_index * 2 + offset,
                    concurrency_lane=offset,
                )
            )
    return FrozenIntegrationPlan(
        stage_id=entry_bundle.stage_id,
        protocol_id=entry_bundle.protocol_id,
        entry_bundle_sha256=entry_bundle.digest,
        conformance_exit_sha256=conformance_exit.digest,
        limits_sha256=limits.digest,
        runs=tuple(runs),
    )


def authorize_integration_plan(entry: StageEntryBundle, plan: FrozenIntegrationPlan) -> None:
    """Require the presealed plan to bind the exact integration entry locks."""

    if (
        entry.stage_kind is not StageKind.INTEGRATION
        or entry.stage_id != plan.stage_id
        or entry.protocol_id != plan.protocol_id
        or entry.digest != plan.entry_bundle_sha256
        or entry.locks["preceding_exit_sha256"] != plan.conformance_exit_sha256
        or entry.locks["limits_sha256"] != plan.limits_sha256
    ):
        raise StageControlError("integration_plan_lock_mismatch")


def load_integration_plan(data: bytes) -> FrozenIntegrationPlan:
    """Load only exact canonical, digest-bound integration plans."""

    try:
        document = json.loads(data.decode("utf-8"))
        if not isinstance(document, dict) or not verify_digest(document):
            raise ValueError
        runs = tuple(
            IntegrationRun(
                run_id=RunId(str(value["run_id"])),
                scenario_id=ScenarioId(str(value["scenario_id"])),
                model_role=str(value["model_role"]),
                condition_slot=int(value["condition_slot"]),
                repeat=int(value["repeat"]),
                order_index=int(value["order_index"]),
                concurrency_lane=int(value["concurrency_lane"]),
            )
            for value in document["runs"]
        )
        plan = FrozenIntegrationPlan(
            stage_id=StageId(str(document["stage_id"])),
            protocol_id=ProtocolId(str(document["protocol_id"])),
            entry_bundle_sha256=str(document["entry_bundle_sha256"]),
            conformance_exit_sha256=str(document["conformance_exit_sha256"]),
            limits_sha256=str(document["limits_sha256"]),
            runs=runs,
            evidence_classification=str(document["evidence_classification"]),
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, StageControlError) as error:
        raise StageControlError("integration_plan_corrupt") from error
    if plan.bytes() != data:
        raise StageControlError("integration_plan_corrupt")
    return plan


@dataclass(frozen=True, slots=True)
class IntegrationAttemptEvidence:
    """One terminal attempt receipt retained even when a permitted retry exists."""

    run_id: RunId
    attempt_number: int
    terminal_kind: str
    exposure: str
    infrastructure_complete: bool
    reconciled: bool
    evidence_refs: tuple[str, ...]
    categorical_blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.attempt_number not in {0, 1}
            or self.exposure not in {"unexposed", "exposed", "ambiguous", "unknown"}
            or not self.terminal_kind
            or not isinstance(self.infrastructure_complete, bool)
            or not isinstance(self.reconciled, bool)
            or not self.evidence_refs
            or len(set(self.evidence_refs)) != len(self.evidence_refs)
            or any(not value for value in self.evidence_refs)
            or any(not value for value in self.categorical_blockers)
        ):
            raise StageControlError("integration_attempt_evidence_invalid")
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(
            self, "categorical_blockers", tuple(sorted(set(self.categorical_blockers)))
        )

    @property
    def is_authorized_retry_source(self) -> bool:
        return (
            self.attempt_number == 0
            and self.terminal_kind == "infrastructure_failed_pre_exposure"
            and self.exposure == "unexposed"
        )

    def to_document(self) -> dict[str, object]:
        return {
            "run_id": str(self.run_id),
            "attempt_number": self.attempt_number,
            "terminal_kind": self.terminal_kind,
            "exposure": self.exposure,
            "infrastructure_complete": self.infrastructure_complete,
            "reconciled": self.reconciled,
            "evidence_refs": list(self.evidence_refs),
            "categorical_blockers": list(self.categorical_blockers),
        }


@dataclass(frozen=True, slots=True)
class IntegrationExitEvidence:
    """The complete reconciled, outcome-blind exit evidence for all retained attempts."""

    plan_sha256: str
    attempts: tuple[IntegrationAttemptEvidence, ...]
    summary_sha256: Mapping[str, str]
    categorical_gate_statuses: Mapping[str, str]

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "integration_exit_lineage_invalid")
        summaries = dict(self.summary_sha256)
        if set(summaries) != INTEGRATION_REQUIRED_SUMMARIES:
            raise StageControlError("integration_exit_summary_incomplete")
        for value in summaries.values():
            _require_sha256(value, "integration_exit_summary_invalid")
        statuses = dict(self.categorical_gate_statuses)
        if set(statuses) != INTEGRATION_REQUIRED_CATEGORICAL_GATES or any(
            value not in {"pass", "blocked"} for value in statuses.values()
        ):
            raise StageControlError("integration_categorical_gate_set_invalid")
        attempts = tuple(
            sorted(
                self.attempts,
                key=lambda value: (str(value.run_id), value.attempt_number),
            )
        )
        if len({(item.run_id, item.attempt_number) for item in attempts}) != len(attempts):
            raise StageControlError("integration_duplicate_attempt_receipt")
        object.__setattr__(self, "attempts", attempts)
        object.__setattr__(
            self,
            "summary_sha256",
            MappingProxyType(dict(sorted(summaries.items()))),
        )
        object.__setattr__(
            self,
            "categorical_gate_statuses",
            MappingProxyType(dict(sorted(statuses.items()))),
        )

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "plan_sha256": self.plan_sha256,
            "attempts": [item.to_document() for item in self.attempts],
            "summary_sha256": dict(self.summary_sha256),
            "categorical_gate_statuses": dict(self.categorical_gate_statuses),
        }

    def bytes(self) -> bytes:
        return canonical_bytes({**self.to_document(), "digest": self.digest})


def load_integration_exit_evidence(data: bytes) -> IntegrationExitEvidence:
    """Load exact canonical terminal evidence before evaluating the immutable exit."""

    try:
        document = json.loads(data.decode("utf-8"))
        if not isinstance(document, dict) or not verify_digest(document):
            raise ValueError
        attempts = tuple(
            IntegrationAttemptEvidence(
                run_id=RunId(str(value["run_id"])),
                attempt_number=int(value["attempt_number"]),
                terminal_kind=str(value["terminal_kind"]),
                exposure=str(value["exposure"]),
                infrastructure_complete=value["infrastructure_complete"],
                reconciled=value["reconciled"],
                evidence_refs=tuple(str(item) for item in value["evidence_refs"]),
                categorical_blockers=tuple(str(item) for item in value["categorical_blockers"]),
            )
            for value in document["attempts"]
        )
        evidence = IntegrationExitEvidence(
            plan_sha256=str(document["plan_sha256"]),
            attempts=attempts,
            summary_sha256=dict(document["summary_sha256"]),
            categorical_gate_statuses=dict(document["categorical_gate_statuses"]),
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, StageControlError) as error:
        raise StageControlError("integration_exit_evidence_corrupt") from error
    if evidence.bytes() != data:
        raise StageControlError("integration_exit_evidence_corrupt")
    return evidence


@dataclass(frozen=True, slots=True)
class IntegrationExitDecision:
    """Typed terminal integration decision; it never contains efficacy results."""

    plan_sha256: str
    exit_evidence_sha256: str
    status: str
    infrastructure_complete_count: int
    failure_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.plan_sha256, "integration_exit_lineage_invalid")
        _require_sha256(self.exit_evidence_sha256, "integration_exit_lineage_invalid")
        if (
            self.status not in {"accepted", "rejected"}
            or not 0 <= self.infrastructure_complete_count <= INTEGRATION_RUN_COUNT
            or (self.status == "accepted" and self.failure_codes)
            or (self.status == "rejected" and not self.failure_codes)
        ):
            raise StageControlError("integration_exit_status_invalid")
        object.__setattr__(self, "failure_codes", tuple(sorted(set(self.failure_codes))))

    @property
    def fresh_stage_required(self) -> bool:
        return self.status == "rejected"

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_document())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "1.0.0",
            "artifact_type": "integration_exit",
            "plan_sha256": self.plan_sha256,
            "exit_evidence_sha256": self.exit_evidence_sha256,
            "status": self.status,
            "infrastructure_complete_count": self.infrastructure_complete_count,
            "infrastructure_complete_threshold": 30,
            "assigned_run_denominator": INTEGRATION_RUN_COUNT,
            "failure_codes": list(self.failure_codes),
            "evidence_classification": INTEGRATION_EVIDENCE_CLASSIFICATION,
            "no_favorable_subset": True,
            "fresh_stage_required": self.fresh_stage_required,
        }

    def bytes(self) -> bytes:
        return canonical_bytes({**self.to_document(), "digest": self.digest})


def evaluate_integration_exit(
    plan: FrozenIntegrationPlan, evidence: IntegrationExitEvidence
) -> IntegrationExitDecision:
    """Evaluate all fixed assigned runs using only infrastructure and evidence status."""

    if evidence.plan_sha256 != plan.digest:
        raise StageControlError("integration_exit_plan_mismatch")
    expected_run_ids = {run.run_id for run in plan.runs}
    grouped: dict[RunId, list[IntegrationAttemptEvidence]] = {}
    for attempt in evidence.attempts:
        grouped.setdefault(attempt.run_id, []).append(attempt)
    failures: list[str] = []
    if set(grouped) != expected_run_ids:
        failures.append("integration_assigned_run_receipts_incomplete")
    final_attempts: list[IntegrationAttemptEvidence] = []
    for run_id in expected_run_ids:
        attempts = sorted(grouped.get(run_id, ()), key=lambda item: item.attempt_number)
        if not attempts or attempts[0].attempt_number != 0:
            failures.append("integration_assigned_run_receipts_incomplete")
            continue
        if len(attempts) > 2 or any(
            attempt.attempt_number != index for index, attempt in enumerate(attempts)
        ):
            failures.append("integration_retry_receipt_invalid")
            continue
        if len(attempts) == 2 and not attempts[0].is_authorized_retry_source:
            failures.append("integration_retry_not_authorized")
        if any(not attempt.reconciled for attempt in attempts):
            failures.append("integration_terminal_evidence_incomplete")
        final_attempts.append(attempts[-1])
    complete_count = sum(
        attempt.infrastructure_complete and attempt.reconciled for attempt in final_attempts
    )
    if len(final_attempts) != INTEGRATION_RUN_COUNT:
        failures.append("integration_attempts_not_terminal")
    if complete_count < 30:
        failures.append("integration_infrastructure_complete_below_30")
    if any(value != "pass" for value in evidence.categorical_gate_statuses.values()) or any(
        attempt.categorical_blockers for attempt in evidence.attempts
    ):
        failures.append("integration_categorical_blockers_present")
    return IntegrationExitDecision(
        plan_sha256=plan.digest,
        exit_evidence_sha256=evidence.digest,
        status="rejected" if failures else "accepted",
        infrastructure_complete_count=complete_count,
        failure_codes=tuple(failures),
    )


def require_fresh_integration_stage(
    plan: FrozenIntegrationPlan,
    decision: IntegrationExitDecision,
    replacement: FrozenIntegrationPlan,
) -> None:
    """Reject any attempt to salvage a rejected 32-run stage with a subset or regrade."""

    if decision.plan_sha256 != plan.digest:
        raise StageControlError("integration_exit_plan_mismatch")
    if not decision.fresh_stage_required:
        raise StageControlError("integration_rerun_not_authorized")
    if replacement.stage_id == plan.stage_id:
        raise StageControlError("integration_fresh_stage_id_required")
    if replacement.protocol_id == plan.protocol_id:
        raise StageControlError("integration_fresh_protocol_id_required")
    if replacement.digest == plan.digest:
        raise StageControlError("integration_fresh_plan_required")


class IntegrationReceiptJournal:
    """Sole-writer append-only receipt authority for start, terminal, and resume control."""

    def __init__(self, plan: FrozenIntegrationPlan, receipt_root: Path) -> None:
        self._plan = plan
        self._lock = RLock()
        self._attempts: dict[RunId, list[IntegrationAttemptEvidence]] = {
            run.run_id: [] for run in plan.runs
        }
        self._active: dict[RunId, AttemptId] = {}
        self._receipt_root = (
            Path(receipt_root) / "integration-receipts" / str(plan.stage_id) / plan.digest
        )
        self._sequence = 0
        self._recover_or_initialize()

    def start(self, run_id: RunId, *, retry: bool = False) -> AttemptId:
        """Write a start receipt once; ordinary resume never reopens a started run."""

        with self._lock:
            attempts = self._attempts.get(run_id)
            if attempts is None:
                raise StageControlError("integration_run_unknown")
            if run_id in self._active:
                return self._active[run_id]
            if not retry and attempts:
                raise StageControlError("integration_run_already_started")
            if retry and (len(attempts) != 1 or not attempts[0].is_authorized_retry_source):
                raise StageControlError("integration_retry_not_authorized")
            if len(attempts) >= 2:
                raise StageControlError("integration_retry_already_used")
            if not retry:
                run = self._run(run_id)
                preceding = self._plan.runs[: run.order_index]
                if any(
                    not self._attempts[earlier.run_id] and earlier.run_id not in self._active
                    for earlier in preceding
                ):
                    raise StageControlError("integration_schedule_order_violation")
                active_lanes = {
                    self._run(active_run_id).concurrency_lane for active_run_id in self._active
                }
                if len(self._active) >= 2 or run.concurrency_lane in active_lanes:
                    raise StageControlError("integration_concurrency_cap_reached")
            attempt_id = AttemptId.from_digest(
                canonical_digest(
                    {
                        "plan_sha256": self._plan.digest,
                        "run_id": str(run_id),
                        "attempt_number": len(attempts),
                    }
                )
            )
            self._append_receipt(
                {
                    "kind": "start",
                    "run_id": str(run_id),
                    "attempt_id": str(attempt_id),
                    "attempt_number": len(attempts),
                    "retry": retry,
                }
            )
            self._active[run_id] = attempt_id
            return attempt_id

    def terminal(self, attempt_id: AttemptId, evidence: IntegrationAttemptEvidence) -> None:
        """Retain exactly one immutable terminal receipt for an active attempt."""

        with self._lock:
            active = self._active.get(evidence.run_id)
            if active != attempt_id:
                raise StageControlError("integration_terminal_receipt_conflict")
            attempts = self._attempts[evidence.run_id]
            if evidence.attempt_number != len(attempts):
                raise StageControlError("integration_terminal_receipt_conflict")
            self._append_receipt(
                {
                    "kind": "terminal",
                    "run_id": str(evidence.run_id),
                    "attempt_id": str(attempt_id),
                    "attempt": evidence.to_document(),
                }
            )
            attempts.append(evidence)
            del self._active[evidence.run_id]

    def resume_candidates(self) -> tuple[RunId, ...]:
        """Return only never-started planned runs; active and terminal evidence remains fixed."""

        with self._lock:
            return tuple(
                run.run_id
                for run in self._plan.runs
                if not self._attempts[run.run_id] and run.run_id not in self._active
            )

    def evidence(
        self,
        summary_sha256: Mapping[str, str],
        categorical_gate_statuses: Mapping[str, str],
    ) -> IntegrationExitEvidence:
        with self._lock:
            if self._active:
                raise StageControlError("integration_attempts_not_terminal")
            return IntegrationExitEvidence(
                plan_sha256=self._plan.digest,
                attempts=tuple(
                    attempt for attempts in self._attempts.values() for attempt in attempts
                ),
                summary_sha256=summary_sha256,
                categorical_gate_statuses=categorical_gate_statuses,
            )

    def _run(self, run_id: RunId) -> IntegrationRun:
        for run in self._plan.runs:
            if run.run_id == run_id:
                return run
        raise StageControlError("integration_run_unknown")

    def _recover_or_initialize(self) -> None:
        self._receipt_root.mkdir(parents=True, exist_ok=True)
        paths = tuple(sorted(self._receipt_root.glob("*.json")))
        if not paths:
            self._append_receipt({"kind": "planned", "plan_sha256": self._plan.digest})
            return
        for sequence, path in enumerate(paths):
            receipt = self._load_receipt(path)
            if receipt["sequence"] != sequence:
                raise StageControlError("integration_receipt_journal_invalid")
            self._replay_receipt(receipt, sequence)
        self._sequence = len(paths)

    def _append_receipt(self, payload: Mapping[str, object]) -> None:
        document = {
            "schema_version": "1.0.0",
            "artifact_type": "integration_receipt",
            "plan_sha256": self._plan.digest,
            "sequence": self._sequence,
            **dict(payload),
        }
        data = canonical_bytes({**document, "digest": canonical_digest(document)})
        path = self._receipt_root / f"{self._sequence:08d}-{canonical_digest(document)}.json"
        staged = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.staged")
        try:
            with staged.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(staged, path)
            except FileExistsError:
                if not path.is_file() or path.read_bytes() != data:
                    raise StageControlError("integration_receipt_journal_conflict") from None
        finally:
            if staged.exists():
                staged.unlink()
        self._sequence += 1

    def _load_receipt(self, path: Path) -> dict[str, object]:
        try:
            data = path.read_bytes()
            document = json.loads(data.decode("utf-8"))
            if (
                not isinstance(document, dict)
                or not verify_digest(document)
                or canonical_bytes(document) != data
                or document.get("schema_version") != "1.0.0"
                or document.get("artifact_type") != "integration_receipt"
                or document.get("plan_sha256") != self._plan.digest
                or not isinstance(document.get("sequence"), int)
            ):
                raise ValueError
            return document
        except (OSError, UnicodeDecodeError, ValueError) as error:
            raise StageControlError("integration_receipt_journal_invalid") from error

    def _replay_receipt(self, receipt: Mapping[str, object], sequence: int) -> None:
        kind = receipt.get("kind")
        if sequence == 0:
            if kind != "planned" or receipt != {
                "schema_version": "1.0.0",
                "artifact_type": "integration_receipt",
                "plan_sha256": self._plan.digest,
                "sequence": 0,
                "kind": "planned",
                "digest": receipt.get("digest"),
            }:
                raise StageControlError("integration_receipt_journal_invalid")
            return
        if kind == "start":
            self._replay_start(receipt)
            return
        if kind == "terminal":
            self._replay_terminal(receipt)
            return
        raise StageControlError("integration_receipt_journal_invalid")

    def _replay_start(self, receipt: Mapping[str, object]) -> None:
        try:
            run_id = RunId(str(receipt["run_id"]))
            attempt_id = AttemptId(str(receipt["attempt_id"]))
            attempt_number = receipt["attempt_number"]
            retry = receipt["retry"]
        except (KeyError, TypeError, ValueError) as error:
            raise StageControlError("integration_receipt_journal_invalid") from error
        attempts = self._attempts.get(run_id)
        if (
            attempts is None
            or not isinstance(attempt_number, int)
            or not isinstance(retry, bool)
            or run_id in self._active
            or attempt_number != len(attempts)
            or (retry and (len(attempts) != 1 or not attempts[0].is_authorized_retry_source))
            or (not retry and attempts)
            or len(attempts) >= 2
        ):
            raise StageControlError("integration_receipt_journal_invalid")
        if not retry:
            run = self._run(run_id)
            preceding = self._plan.runs[: run.order_index]
            active_lanes = {
                self._run(active_run_id).concurrency_lane for active_run_id in self._active
            }
            if (
                any(
                    not self._attempts[earlier.run_id] and earlier.run_id not in self._active
                    for earlier in preceding
                )
                or len(self._active) >= 2
                or run.concurrency_lane in active_lanes
            ):
                raise StageControlError("integration_receipt_journal_invalid")
        expected = AttemptId.from_digest(
            canonical_digest(
                {
                    "plan_sha256": self._plan.digest,
                    "run_id": str(run_id),
                    "attempt_number": attempt_number,
                }
            )
        )
        if attempt_id != expected:
            raise StageControlError("integration_receipt_journal_invalid")
        self._active[run_id] = attempt_id

    def _replay_terminal(self, receipt: Mapping[str, object]) -> None:
        try:
            run_id = RunId(str(receipt["run_id"]))
            attempt_id = AttemptId(str(receipt["attempt_id"]))
            raw = receipt["attempt"]
            if not isinstance(raw, Mapping):
                raise ValueError
            evidence = IntegrationAttemptEvidence(
                run_id=RunId(str(raw["run_id"])),
                attempt_number=int(raw["attempt_number"]),
                terminal_kind=str(raw["terminal_kind"]),
                exposure=str(raw["exposure"]),
                infrastructure_complete=raw["infrastructure_complete"],
                reconciled=raw["reconciled"],
                evidence_refs=tuple(str(value) for value in raw["evidence_refs"]),
                categorical_blockers=tuple(str(value) for value in raw["categorical_blockers"]),
            )
        except (KeyError, TypeError, ValueError, StageControlError) as error:
            raise StageControlError("integration_receipt_journal_invalid") from error
        active = self._active.get(run_id)
        attempts = self._attempts.get(run_id)
        if (
            active != attempt_id
            or attempts is None
            or evidence.run_id != run_id
            or evidence.attempt_number != len(attempts)
        ):
            raise StageControlError("integration_receipt_journal_invalid")
        attempts.append(evidence)
        del self._active[run_id]


class IntegrationExitStore:
    """Atomically seal exactly one integration exit, prohibiting post-rejection regrade."""

    def __init__(self, root: Path) -> None:
        self._root = Path(root) / "integration-exits"

    def gate(
        self, plan: FrozenIntegrationPlan, evidence: IntegrationExitEvidence
    ) -> tuple[IntegrationExitDecision, Path, str]:
        decision = evaluate_integration_exit(plan, evidence)
        path = self._root / f"{plan.stage_id}.json"
        return decision, path, self._write_once(path, decision.bytes())

    @staticmethod
    def _write_once(path: Path, data: bytes) -> str:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if not path.is_file() or path.read_bytes() != data:
                raise StageControlError("integration_stage_mutation_regrade_prohibited")
            return "reused"
        staged = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.staged")
        try:
            with staged.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(staged, path)
            except FileExistsError:
                if not path.is_file() or path.read_bytes() != data:
                    raise StageControlError(
                        "integration_stage_mutation_regrade_prohibited"
                    ) from None
                return "reused"
        finally:
            if staged.exists():
                staged.unlink()
        return "sealed"
