from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from memrelay_eval.domain.errors import AuthorityConflictError, SecretBoundaryViolationError
from memrelay_eval.domain.identity import (
    copilot_identity,
    framework_openai_identity,
    local_identity,
)
from memrelay_eval.domain.ids import AttemptId, CostEntryId
from memrelay_eval.evidence.costs import (
    CostRecord,
    IdentityEvidence,
    validate_cost_records,
    validate_identity_evidence,
)

NOW = datetime(2026, 8, 10, 20, 0, tzinfo=UTC)


def test_telemetry_disagreement_cannot_select_favorable_provider_or_cost_source() -> None:
    with pytest.raises(AuthorityConflictError) as error:
        validate_identity_evidence(
            (
                IdentityEvidence("telemetry", "span_1", copilot_identity()),
                IdentityEvidence("telemetry", "span_1", framework_openai_identity()),
            )
        )
    assert error.value.code == "authority_conflict"


@pytest.mark.parametrize(
    ("identity", "authority", "unit"),
    (
        (copilot_identity(), "native_provider", "cpu_second"),
        (framework_openai_identity(), "native_local_counter", "input_token"),
        (local_identity("local_cpu"), "native_provider", "cpu_second"),
    ),
)
def test_unknown_or_conflicting_cost_evidence_fails_closed(
    identity: object, authority: str, unit: str
) -> None:
    with pytest.raises(AuthorityConflictError):
        CostRecord(
            CostEntryId.new(),
            AttemptId.new(),
            identity,  # type: ignore[arg-type]
            authority,
            "native_1",
            sha256(b"native").hexdigest(),
            1,
            unit,
            "metered",
            NOW,
        )


def test_unicode_alias_source_and_secret_like_ref_are_never_preserved_as_cost_evidence() -> None:
    with pytest.raises(AuthorityConflictError):
        CostRecord(
            CostEntryId.new(),
            AttemptId.new(),
            local_identity("local_cpu"),
            "native_local_counter",
            "nаtive_usage",
            sha256(b"native").hexdigest(),
            1,
            "cpu_second",
            "metered",
            NOW,
        )


def test_cost_source_reference_with_secret_material_is_rejected_without_rendering_it() -> None:
    with pytest.raises(SecretBoundaryViolationError):
        CostRecord(
            CostEntryId.new(),
            AttemptId.new(),
            local_identity("local_cpu"),
            "native_local_counter",
            "sk-prohibited-secret",
            sha256(b"native").hexdigest(),
            1,
            "cpu_second",
            "metered",
            NOW,
        )


def test_conflicting_duplicate_native_quantity_evidence_cannot_be_collapsed() -> None:
    entry_id = CostEntryId.new()
    attempt_id = AttemptId.new()
    source_hash = sha256(b"native").hexdigest()
    common = (
        entry_id,
        attempt_id,
        local_identity("local_cpu"),
        "native_local_counter",
        "counter_1",
        source_hash,
        "cpu_second",
        "metered",
        NOW,
    )
    first = CostRecord(*common[:6], 1, *common[6:])
    conflicting = CostRecord(*common[:6], 2, *common[6:])
    with pytest.raises(AuthorityConflictError) as error:
        validate_cost_records((first, conflicting))
    assert "conflicting_duplicate_quantity_evidence" in error.value.fields
