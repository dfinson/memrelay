"""Fail-closed evaluator stage boundary checks."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from memrelay_eval.domain.entities import NativeModelCatalog, RuntimeIdentity
from memrelay_eval.domain.errors import ConformancePauseError
from memrelay_eval.domain.governance import (
    EvaluationStage,
    RepositoryAccessRequest,
    RevocationState,
)
from memrelay_eval.domain.ids import (
    AuthorizationId,
    AuthorizationVersionId,
    GovernanceRequestId,
    PolicyVersionId,
    PrincipalId,
    PurposeId,
    PurposeVersionId,
    RepositoryId,
)
from memrelay_eval.orchestration.control import CrossRepositoryAdmissionController


def refuse_cross_repository_stage() -> None:
    """Refuse the unavailable v1 stage before any repository identity is resolved."""

    now = datetime.now(UTC)
    repository_id = RepositoryId.new()
    request = RepositoryAccessRequest(
        request_id=GovernanceRequestId.new(),
        task_repository_id=repository_id,
        requested_repository_id=repository_id,
        principal_id=PrincipalId.new(),
        authorization_id=AuthorizationId.new(),
        authorization_version=AuthorizationVersionId.new(),
        purpose_id=PurposeId.new(),
        purpose_version=PurposeVersionId.new(),
        policy_version=PolicyVersionId.new(),
        valid_from=now,
        valid_until=now + timedelta(days=365),
        revocation_state=RevocationState.ACTIVE,
        stage=EvaluationStage.CROSS_REPOSITORY,
    )
    CrossRepositoryAdmissionController().authorize_at_entry(request, now)
def verify_stage_locks(
    runtime_lock: Mapping[str, object],
    model_lock: Mapping[str, object],
    current_runtime: RuntimeIdentity,
    current_catalog: NativeModelCatalog,
) -> None:
    """Raise a typed pause for any runtime, catalog, or selected-model drift."""

    expected_runtime = runtime_lock.get("runtime")
    if not isinstance(expected_runtime, Mapping):
        raise ConformancePauseError("runtime_lock_invalid", "runtime lock has no runtime identity")
    for key, actual in {
        "sdk_version": current_runtime.sdk_version,
        "wheel_filename": current_runtime.wheel_filename,
        "wheel_sha256": current_runtime.wheel_sha256,
        "runtime_version": current_runtime.runtime_version,
        "runtime_sha256": current_runtime.runtime_sha256,
        "transport": current_runtime.transport,
        "auth_mode": current_runtime.auth_mode,
        "subscription_identity_sha256": current_runtime.subscription_identity_sha256,
    }.items():
        if expected_runtime.get(key) != actual:
            raise ConformancePauseError("runtime_drift", f"locked runtime field changed: {key}")
    if model_lock.get("runtime_lock_sha256") != runtime_lock.get("lock_sha256"):
        raise ConformancePauseError(
            "runtime_model_link_drift", "model lock is not linked to runtime lock"
        )
    if model_lock.get("catalog_raw_sha256") != current_catalog.raw_sha256:
        raise ConformancePauseError("catalog_drift", "native model catalog bytes changed")
    if model_lock.get("catalog_projection_sha256") != current_catalog.projection_sha256:
        raise ConformancePauseError("catalog_projection_drift", "native model capabilities changed")
    catalog_by_id = {model.native_id: model for model in current_catalog.models}
    selected = model_lock.get("selected_models")
    if not isinstance(selected, list):
        raise ConformancePauseError("model_lock_invalid", "model lock has no selected models")
    for pin in selected:
        if not isinstance(pin, Mapping) or not isinstance(pin.get("native_id"), str):
            raise ConformancePauseError("model_lock_invalid", "selected model pin is malformed")
        current = catalog_by_id.get(pin["native_id"])
        if current is None:
            raise ConformancePauseError("model_unavailable", "locked native model disappeared")
        for key, actual in {
            "family": current.family,
            "capabilities": dict(current.capabilities),
            "reasoning_effort": current.reasoning_effort,
            "context_tier": current.context_tier,
        }.items():
            if pin.get(key) != actual:
                raise ConformancePauseError(
                    "model_capability_drift", f"locked model field changed: {key}"
                )
