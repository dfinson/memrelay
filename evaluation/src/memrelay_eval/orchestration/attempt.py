"""Attempt terminal and internal retry recording without run-state mutation."""

from __future__ import annotations

from datetime import UTC, datetime

from memrelay_eval.domain.entities import (
    AttemptTerminal,
    InternalRetryPolicy,
    InternalRetryRecord,
    TelemetryObservation,
)
from memrelay_eval.domain.errors import (
    AttemptExecutionClaimDeniedError,
    AttemptTerminalAlreadyRecordedError,
    InternalRetryLimitExceededError,
)
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.domain.intents import IntentAck, IntentRejection, LedgerIntentType
from memrelay_eval.domain.ports import LedgerPort, TelemetryPort
from memrelay_eval.domain.states import InternalRetrySubsystem

from .worker import WorkerIntentEmitter


class AttemptTerminalRecorder:
    """Append one immutable terminal record per attempt, separate from run lifecycle."""

    def __init__(self, ledger: LedgerPort, telemetry: TelemetryPort) -> None:
        self._ledger = ledger
        self._telemetry = telemetry
        self._terminals: dict[AttemptId, AttemptTerminal] = {}

    def append(self, terminal: AttemptTerminal) -> None:
        if (
            terminal.attempt_id in self._terminals
            or self._ledger.attempt_terminal_for(terminal.attempt_id) is not None
        ):
            raise AttemptTerminalAlreadyRecordedError(AttemptTerminalAlreadyRecordedError.code)
        self._ledger.append_attempt_terminal(terminal)
        self._telemetry.finish_attempt(terminal)
        self._terminals[terminal.attempt_id] = terminal

    def terminal_for(self, attempt_id: AttemptId) -> AttemptTerminal | None:
        return self._terminals.get(attempt_id) or self._ledger.attempt_terminal_for(attempt_id)

    def claim_execution(self, attempt_id: AttemptId, run_id: object) -> None:
        """Reserve an open attempt before any scheduler or task side effect."""

        if not isinstance(run_id, RunId) or not self._ledger.claim_attempt_execution(
            attempt_id, run_id
        ):
            raise AttemptExecutionClaimDeniedError(AttemptExecutionClaimDeniedError.code)


class InternalRetryRecorder:
    """Records bounded retries separately for Inspect, SDK, memrelay, and grader."""

    def __init__(
        self,
        attempt_id: AttemptId,
        policies: tuple[InternalRetryPolicy, ...],
        ledger: LedgerPort,
        telemetry: TelemetryPort,
    ) -> None:
        self._attempt_id = attempt_id
        self._policies = {policy.subsystem: policy for policy in policies}
        if len(self._policies) != len(policies):
            raise ValueError("internal retry policies must not duplicate a subsystem")
        self._ledger = ledger
        self._telemetry = telemetry
        self._records: list[InternalRetryRecord] = []

    def record(self, subsystem: InternalRetrySubsystem) -> InternalRetryRecord:
        policy = self._policies.get(subsystem)
        if policy is None:
            raise InternalRetryLimitExceededError(InternalRetryLimitExceededError.code)
        record = self._ledger.reserve_internal_retry(
            self._attempt_id, subsystem, policy.maximum_retries
        )
        if record is None:
            raise InternalRetryLimitExceededError(InternalRetryLimitExceededError.code)
        self._telemetry.emit(
            TelemetryObservation(
                "internal_retry",
                datetime(1970, 1, 1, tzinfo=UTC),
                {"retry_number": record.retry_number},
            )
        )
        self._records.append(record)
        return record

    @property
    def records(self) -> tuple[InternalRetryRecord, ...]:
        return tuple(self._records)


def emit_attempt_intent(
    emitter: WorkerIntentEmitter, intent: LedgerIntentType
) -> IntentAck | IntentRejection:
    """Forward an immutable attempt lifecycle intent across the control boundary."""

    return emitter.emit(intent)
