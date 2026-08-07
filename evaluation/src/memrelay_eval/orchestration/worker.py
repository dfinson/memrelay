"""Worker-facing intent sink; workers receive no durable-store capabilities."""

from __future__ import annotations

from typing import Protocol

from memrelay_eval.domain.intents import IntentAck, IntentRejection, LedgerIntentType


class WorkerIntentSink(Protocol):
    """Narrow process-boundary contract supplied to an isolated attempt worker."""

    def emit(self, intent: LedgerIntentType) -> IntentAck | IntentRejection: ...


class WorkerIntentEmitter:
    """Small worker helper that forwards opaque immutable intents to the control process."""

    def __init__(self, sink: WorkerIntentSink) -> None:
        self._sink = sink

    def emit(self, intent: LedgerIntentType) -> IntentAck | IntentRejection:
        return self._sink.emit(intent)
