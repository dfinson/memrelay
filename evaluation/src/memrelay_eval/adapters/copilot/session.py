"""Finite nonstudy qualification through the official runtime port."""

from __future__ import annotations

import importlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from memrelay_eval.domain.entities import (
    ModelQualification,
    NativeModel,
    QualificationCaps,
    QualificationTaskResult,
    QualificationUsage,
)
from memrelay_eval.domain.errors import ConformancePauseError, QualificationLimitError


@dataclass(frozen=True, slots=True)
class QualificationSession:
    """An arm-blind, nonstudy session request with remaining aggregate limits."""

    native_model_id: str
    task_index: int
    remaining_caps: QualificationCaps


QualificationExecutor = Callable[[QualificationSession], Awaitable[QualificationTaskResult]]

_QUALIFICATION_PROMPTS = (
    ("Return only the result of 17 + 25.", "42"),
    ("Return only the result of 81 - 36.", "45"),
    ("Return only the result of 9 * 7.", "63"),
    ("Return only the integer quotient of 144 divided by 12.", "12"),
    ("Return only the next value after 34 in this sequence: 2, 5, 10, 17, 26, 34.", "45"),
    ("Return only the number of letters in the word 'conformance'.", "11"),
    ("Return only the result of 15 squared.", "225"),
    ("Return only the result of (8 + 4) * 3.", "36"),
)


async def qualify_models(
    models: Sequence[NativeModel],
    caps: QualificationCaps,
    execute: QualificationExecutor,
) -> tuple[tuple[ModelQualification, ...], QualificationUsage]:
    """Run exactly eight sessions per eligible model, with no retry path."""

    planned_sessions = len(models) * 8
    if caps.session_limit != planned_sessions:
        raise QualificationLimitError(
            "qualification_session_envelope_invalid",
            "qualification cap must equal exactly eight sessions per eligible model",
        )
    usage = QualificationUsage()
    results: list[ModelQualification] = []
    for model in models:
        task_results: list[QualificationTaskResult] = []
        for task_index in range(8):
            _require_capacity(caps, usage)
            result = await execute(
                QualificationSession(
                    native_model_id=model.native_id,
                    task_index=task_index,
                    remaining_caps=_remaining_caps(caps, usage),
                )
            )
            next_usage = usage.plus(result.usage)
            _require_within_caps(caps, next_usage)
            usage = next_usage
            task_results.append(result)
        results.append(ModelQualification(model.native_id, tuple(task_results)))
    return tuple(results), usage


async def qualify_native_catalog(
    catalog_models: Sequence[NativeModel],
    caps: QualificationCaps,
) -> tuple[tuple[ModelQualification, ...], QualificationUsage]:
    """Explicitly run the fixed nonstudy suite with direct official-SDK sessions."""

    models = eligible_models_from_native(catalog_models)
    executor = _CopilotQualificationExecutor({model.native_id: model for model in models})
    return await qualify_models(models, caps, executor)


def eligible_models_from_native(models: Sequence[NativeModel]) -> tuple[NativeModel, ...]:
    """Apply the same native capability gate without accepting public model aliases."""

    return tuple(
        model
        for model in models
        if all(
            model.capabilities.get(capability) not in {None, False, "unavailable"}
            for capability in ("tools", "permissions", "context", "events", "cancellation", "sessions")
        )
    )


class _CopilotQualificationExecutor:
    """One direct SDK session per task; neither Inspect nor alternate providers participate."""

    def __init__(self, models: dict[str, NativeModel]) -> None:
        self._models = models

    async def __call__(self, session: QualificationSession) -> QualificationTaskResult:
        model = self._models[session.native_model_id]
        client = _official_client()
        await client.start()
        started = time.monotonic()
        try:
            kwargs: dict[str, object] = {"model": model.native_id}
            if isinstance(model.reasoning_effort, str) and model.reasoning_effort != "unavailable":
                kwargs["reasoning_effort"] = model.reasoning_effort
            if isinstance(model.context_tier, str) and model.context_tier != "unavailable":
                kwargs["context_tier"] = model.context_tier
            sdk_session = await client.create_session(**kwargs)
            prompt, expected = _QUALIFICATION_PROMPTS[session.task_index]
            event = await sdk_session.send_and_wait(prompt, timeout=session.remaining_caps.wall_seconds_limit)
            await sdk_session.disconnect()
            elapsed = time.monotonic() - started
            return QualificationTaskResult(
                executable_passed=_event_contains_answer(event, expected),
                protected_check_fraction=float(_event_contains_answer(event, expected)),
                usage=_native_usage(event, elapsed),
            )
        finally:
            await client.stop()


def _official_client() -> Any:
    try:
        return importlib.import_module("copilot").CopilotClient(
            use_logged_in_user=True,
            github_token=None,
        )
    except (ImportError, AttributeError) as exc:
        raise ConformancePauseError(
            "sdk_unavailable", "github-copilot-sdk 1.0.8 is required for live qualification"
        ) from exc


def _event_contains_answer(event: object, expected: str) -> bool:
    if event is None or not hasattr(event, "to_dict"):
        return False
    return _contains_exact_answer(event.to_dict(), expected)


def _contains_exact_answer(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return value.strip() == expected
    if isinstance(value, dict):
        return any(_contains_exact_answer(item, expected) for item in value.values())
    if isinstance(value, list):
        return any(_contains_exact_answer(item, expected) for item in value)
    return False


def _native_usage(event: object, elapsed: float) -> QualificationUsage:
    """Reject absent native metering rather than manufacturing zero-cost evidence."""

    if event is None or not hasattr(event, "to_dict"):
        raise ConformancePauseError("qualification_usage_unavailable", "native usage event is missing")
    data = event.to_dict()
    if not isinstance(data, dict):
        raise ConformancePauseError("qualification_usage_unavailable", "native usage event is invalid")
    usage = _find_usage_mapping(data)
    if usage is None:
        raise ConformancePauseError(
            "qualification_usage_unavailable", "native SDK did not expose qualification usage"
        )
    credits = usage.get("ai_credits", usage.get("credits"))
    tokens = usage.get("total_tokens")
    if not isinstance(credits, (int, float)) or not isinstance(tokens, int):
        raise ConformancePauseError(
            "qualification_usage_unavailable", "native SDK usage lacks credits or tokens"
        )
    return QualificationUsage(
        sessions=1,
        credits=float(credits),
        tokens=tokens,
        active_seconds=elapsed,
        wall_seconds=elapsed,
    )


def _find_usage_mapping(value: object) -> dict[str, object] | None:
    if isinstance(value, dict):
        usage = value.get("usage")
        if isinstance(usage, dict):
            return usage
        for item in value.values():
            found = _find_usage_mapping(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _find_usage_mapping(item)
            if found is not None:
                return found
    return None


def _remaining_caps(caps: QualificationCaps, usage: QualificationUsage) -> QualificationCaps:
    return QualificationCaps(
        session_limit=caps.session_limit - usage.sessions,
        credit_limit=float(caps.credit_limit) - usage.credits,
        token_limit=int(caps.token_limit) - usage.tokens,
        active_seconds_limit=float(caps.active_seconds_limit) - usage.active_seconds,
        wall_seconds_limit=float(caps.wall_seconds_limit) - usage.wall_seconds,
    )


def _require_capacity(caps: QualificationCaps, usage: QualificationUsage) -> None:
    if usage.sessions >= caps.session_limit:
        raise QualificationLimitError("qualification_session_cap_reached", "session cap reached")
    if (
        usage.credits >= caps.credit_limit
        or usage.tokens >= caps.token_limit
        or usage.active_seconds >= caps.active_seconds_limit
        or usage.wall_seconds >= caps.wall_seconds_limit
    ):
        raise QualificationLimitError(
            "qualification_cap_reached", "qualification aggregate cap reached before a new session"
        )


def _require_within_caps(caps: QualificationCaps, usage: QualificationUsage) -> None:
    if (
        usage.sessions > caps.session_limit
        or usage.credits > caps.credit_limit
        or usage.tokens > caps.token_limit
        or usage.active_seconds > caps.active_seconds_limit
        or usage.wall_seconds > caps.wall_seconds_limit
    ):
        raise QualificationLimitError(
            "qualification_cap_exceeded",
            "runtime returned usage beyond the frozen qualification envelope",
        )
