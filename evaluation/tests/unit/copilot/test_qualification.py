from __future__ import annotations

import asyncio

import pytest

from memrelay_eval.adapters.copilot.session import QualificationSession, qualify_models
from memrelay_eval.domain.entities import (
    NativeModel,
    QualificationCaps,
    QualificationTaskResult,
    QualificationUsage,
)
from memrelay_eval.domain.errors import QualificationLimitError


def native(identifier: str) -> NativeModel:
    return NativeModel(identifier, "family", {}, "high", "large")


async def result(_: QualificationSession) -> QualificationTaskResult:
    return QualificationTaskResult(True, 1.0, QualificationUsage(1, 1, 10, 1, 2))


def test_qualification_runs_exactly_eight_sessions_per_model() -> None:
    calls: list[QualificationSession] = []

    async def execute(session: QualificationSession) -> QualificationTaskResult:
        calls.append(session)
        return await result(session)

    qualifications, usage = asyncio.run(
        qualify_models(
            (native("a"), native("b")),
            QualificationCaps(16, 16, 160, 16, 32),
            execute,
        )
    )

    assert len(calls) == 16
    assert all(len(qualification.task_results) == 8 for qualification in qualifications)
    assert usage.sessions == 16


def test_qualification_rejects_unbounded_or_wrong_session_envelope() -> None:
    with pytest.raises(QualificationLimitError, match="eight sessions"):
        asyncio.run(
            qualify_models((native("a"),), QualificationCaps(9, 9, 90, 9, 18), result)
        )
