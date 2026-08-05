"""Native Copilot catalog capture and deterministic selection."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from statistics import median

from memrelay_eval.domain.entities import (
    LockedModel,
    ModelQualification,
    NativeModel,
    NativeModelCatalog,
)
from memrelay_eval.domain.errors import ConformancePauseError

UNAVAILABLE = "unavailable"
_CATALOG_FIELDS = (
    "tools",
    "permissions",
    "context",
    "events",
    "cancellation",
    "sessions",
)


@dataclass(frozen=True, slots=True)
class CatalogArchive:
    """The exact response bytes and a stable capability-only projection."""

    catalog: NativeModelCatalog
    raw_bytes: bytes
    projection_bytes: bytes


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """Model roles selected mechanically from one frozen catalog."""

    m0: LockedModel
    m1: LockedModel | None
    m2: LockedModel | None
    judges: tuple[LockedModel, ...]
    omissions: Mapping[str, str]


def archive_native_catalog(raw_bytes: bytes, response: object) -> CatalogArchive:
    """Archive exact SDK bytes without replacing missing native fields."""

    if not raw_bytes:
        raise ConformancePauseError("catalog_raw_missing", "native catalog bytes are required")
    models = tuple(_native_model(item) for item in _model_items(response))
    projection = [
        {
            "native_id": model.native_id,
            "family": model.family,
            "capabilities": dict(model.capabilities),
            "reasoning_effort": model.reasoning_effort,
            "context_tier": model.context_tier,
        }
        for model in sorted(models, key=lambda model: model.native_id)
    ]
    projection_bytes = json.dumps(
        projection, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return CatalogArchive(
        NativeModelCatalog(
            raw_sha256=sha256(raw_bytes).hexdigest(),
            projection_sha256=sha256(projection_bytes).hexdigest(),
            models=models,
        ),
        bytes(raw_bytes),
        projection_bytes,
    )


def eligible_models(
    catalog: NativeModelCatalog, required_capabilities: Sequence[str] = _CATALOG_FIELDS
) -> tuple[NativeModel, ...]:
    """Return models only when every required native capability is explicitly present."""

    return tuple(
        model
        for model in catalog.models
        if all(_is_available(model.capabilities.get(capability, UNAVAILABLE)) for capability in required_capabilities)
    )


def select_models(
    catalog: NativeModelCatalog,
    qualifications: Sequence[ModelQualification],
) -> ModelSelection:
    """Apply the frozen selection and diversity algorithm without substitutions."""

    by_id = {model.native_id: model for model in catalog.models}
    if not qualifications:
        raise ConformancePauseError("no_qualified_models", "no eligible model completed qualification")
    if any(qualification.native_id not in by_id for qualification in qualifications):
        raise ConformancePauseError(
            "qualification_catalog_mismatch",
            "qualification includes a model absent from the archived native catalog",
        )
    ranked = tuple(
        sorted(
            qualifications,
            key=lambda result: (
                -result.executable_passes,
                -result.protected_check_fraction,
                result.median_active_seconds,
                result.native_id,
            ),
        )
    )
    m0_result = ranked[0]
    m0 = _locked("M0", by_id[m0_result.native_id])
    m1_candidate = next(
        (
            item
            for item in ranked
            if by_id[item.native_id].family != UNAVAILABLE
            and by_id[item.native_id].family != by_id[m0.native_id].family
        ),
        None,
    )
    qualifying_m2 = [
        item
        for item in ranked
        if item.executable_passes / 8 >= m0_result.executable_passes / 8 - 0.05
    ]
    m2_candidate = min(
        qualifying_m2,
        key=lambda item: (item.median_credits, item.native_id),
        default=None,
    )
    selected_ids = {m0.native_id}
    if m1_candidate is not None:
        selected_ids.add(m1_candidate.native_id)
    judges = _select_judges(ranked, by_id, selected_ids)
    omissions: dict[str, str] = {}
    if m1_candidate is None:
        omissions["M1"] = "no_distinct_reported_family"
    if m2_candidate is None:
        omissions["M2"] = "no_model_within_executable_score_tolerance"
    if len(judges) < 3:
        omissions["judges"] = "fewer_than_three_distinct_eligible_native_models"
    return ModelSelection(
        m0=m0,
        m1=_locked("M1", by_id[m1_candidate.native_id]) if m1_candidate else None,
        m2=_locked("M2", by_id[m2_candidate.native_id]) if m2_candidate else None,
        judges=tuple(_locked("judge", by_id[item.native_id]) for item in judges),
        omissions=omissions,
    )


def qualification_summary(results: Sequence[ModelQualification]) -> dict[str, object]:
    """Return non-sensitive, aggregate qualification evidence for the model lock."""

    return {
        result.native_id: {
            "executable_passes": result.executable_passes,
            "protected_check_fraction": result.protected_check_fraction,
            "median_active_seconds": median(
                task.usage.active_seconds for task in result.task_results
            ),
            "median_credits": median(task.usage.credits for task in result.task_results),
            "usage": {
                "sessions": result.usage.sessions,
                "credits": result.usage.credits,
                "tokens": result.usage.tokens,
                "active_seconds": result.usage.active_seconds,
                "wall_seconds": result.usage.wall_seconds,
            },
        }
        for result in results
    }


def _model_items(response: object) -> Sequence[object]:
    if isinstance(response, Mapping):
        models = response.get("models")
        if isinstance(models, Sequence) and not isinstance(models, (str, bytes)):
            return models
    if isinstance(response, Sequence) and not isinstance(response, (str, bytes)):
        return response
    raise ConformancePauseError(
        "catalog_shape_invalid", "native catalog response does not contain a models sequence"
    )


def _native_model(value: object) -> NativeModel:
    if not isinstance(value, Mapping) or not isinstance(value.get("id"), str):
        raise ConformancePauseError(
            "catalog_model_invalid", "every native catalog model must expose its native id"
        )
    capabilities = value.get("capabilities")
    source_capabilities = capabilities if isinstance(capabilities, Mapping) else {}
    return NativeModel(
        native_id=value["id"],
        family=_value_or_unavailable(value, "family"),
        capabilities={
            field: source_capabilities.get(field, value.get(field, UNAVAILABLE))
            for field in _CATALOG_FIELDS
        },
        reasoning_effort=_value_or_unavailable(value, "reasoning_effort"),
        context_tier=_value_or_unavailable(value, "context_tier"),
    )


def _value_or_unavailable(value: Mapping[str, object], key: str) -> object:
    result = value.get(key, UNAVAILABLE)
    return UNAVAILABLE if result is None else result


def _is_available(value: object) -> bool:
    if value == UNAVAILABLE or value is None or value is False:
        return False
    return not isinstance(value, (int, float)) or value > 0


def _locked(role: str, model: NativeModel) -> LockedModel:
    return LockedModel(
        role=role,
        native_id=model.native_id,
        family=model.family,
        capabilities=model.capabilities,
        reasoning_effort=model.reasoning_effort,
        context_tier=model.context_tier,
    )


def _select_judges(
    ranked: Sequence[ModelQualification],
    models: Mapping[str, NativeModel],
    excluded_ids: set[str],
) -> tuple[ModelQualification, ...]:
    """Prefer unique reported families and IDs while excluding the task generator."""

    selected: list[ModelQualification] = []
    families: set[str] = set()
    for result in ranked:
        model = models[result.native_id]
        if result.native_id in excluded_ids or model.family in families:
            continue
        selected.append(result)
        families.add(model.family)
        if len(selected) == 3:
            return tuple(selected)
    for result in ranked:
        if result.native_id not in excluded_ids and result not in selected:
            selected.append(result)
        if len(selected) == 3:
            break
    return tuple(selected)
