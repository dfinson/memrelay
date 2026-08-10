from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest
from memrelay_eval.domain.errors import AuthorityConflictError
from memrelay_eval.domain.identity import (
    IDENTITY_COMPATIBILITY_MATRIX,
    ProviderIdentity,
    copilot_identity,
    framework_openai_identity,
    local_identity,
    source_provider_to_canonical,
)
from memrelay_eval.evidence.costs import CostRecord, IdentityEvidence, validate_identity_evidence


@pytest.mark.parametrize("row", IDENTITY_COMPATIBILITY_MATRIX)
def test_every_frozen_provider_credential_cost_resource_row_is_valid(row: dict[str, str]) -> None:
    identity = ProviderIdentity(**row)
    schema_path = Path(__file__).parents[3] / "schemas" / "provider-identity.schema.json"
    jsonschema.Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(
        identity.to_record()
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        pytest.param("credential_domain", "framework_openai_api", id="credential"),
        pytest.param("cost_source", "openai_api_metered", id="cost-source"),
        pytest.param("resource_identity", "framework_openai_client", id="resource"),
        pytest.param("provider", "openai", id="provider"),
    ),
)
def test_cross_authority_combinations_fail_closed(field: str, value: str) -> None:
    with pytest.raises(AuthorityConflictError, match="authority_conflict"):
        replace(copilot_identity(), **{field: value})


def test_alias_case_and_unicode_confusion_are_not_canonicalized() -> None:
    assert source_provider_to_canonical("github_copilot_sdk") == "github_copilot"
    for value in ("GitHub_Copilot_SDK", "github-copilot-sdk", "github_cop\u043epilot_sdk"):
        with pytest.raises(AuthorityConflictError, match="authority_conflict"):
            source_provider_to_canonical(value)


def test_cost_and_telemetry_evidence_preserve_source_disagreement_without_substitution() -> None:
    with pytest.raises(AuthorityConflictError) as conflict:
        validate_identity_evidence(
            (
                IdentityEvidence("telemetry", "span_opaque", copilot_identity()),
                IdentityEvidence("telemetry", "span_opaque", framework_openai_identity()),
            )
        )
    assert conflict.value.code == "authority_conflict"
    cost = CostRecord(
        "cost_opaque", "attempt_" + "1" * 32, local_identity("local_cpu"), "usage_opaque"
    )
    assert cost.identity.logical_ledger == "local_resources"
