from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from memrelay_eval.domain.governance import (
    DenialEvidence,
    DenyByDefaultRepositoryAuthorization,
    EvaluationStage,
    GovernanceDenialReason,
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

SCHEMA_PATH = Path(__file__).parents[2] / "schemas" / "repository-authorization.schema.json"
DENIAL_FIELDS = {"request_id", "decision", "policy_version", "reason"}


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def assert_privacy_minimized_denial_schema(schema: dict[str, Any]) -> None:
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == DENIAL_FIELDS
    assert set(schema["properties"]) == DENIAL_FIELDS
    assert schema["properties"]["request_id"] == {
        "type": "string",
        "pattern": "^govrequest_[a-f0-9]{32}$",
    }
    assert schema["properties"]["decision"] == {"const": "denied"}
    assert schema["properties"]["policy_version"] == {
        "type": "string",
        "pattern": "^policy_[a-f0-9]{32}$",
    }
    assert set(schema["properties"]["reason"]["enum"]) == {
        reason.value for reason in GovernanceDenialReason
    }


def assert_matches_denial_schema(payload: dict[str, str], schema: dict[str, Any]) -> None:
    assert set(payload) == set(schema["required"])
    properties = schema["properties"]
    for field in ("request_id", "policy_version"):
        assert isinstance(payload[field], str)
        assert re.fullmatch(properties[field]["pattern"], payload[field])
    assert payload["decision"] == properties["decision"]["const"]
    assert payload["reason"] in properties["reason"]["enum"]


def repository_mismatch_evidence() -> DenialEvidence:
    request = RepositoryAccessRequest(
        request_id=GovernanceRequestId.new(),
        task_repository_id=RepositoryId.new(),
        requested_repository_id=RepositoryId.new(),
        principal_id=PrincipalId.new(),
        authorization_id=AuthorizationId.new(),
        authorization_version=AuthorizationVersionId.new(),
        purpose_id=PurposeId.new(),
        purpose_version=PurposeVersionId.new(),
        policy_version=PolicyVersionId.new(),
        valid_from=datetime.fromtimestamp(0, tz=UTC),
        valid_until=datetime.fromtimestamp(60, tz=UTC),
        revocation_state=RevocationState.ACTIVE,
        stage=EvaluationStage.ORDINARY,
    )
    result = DenyByDefaultRepositoryAuthorization().authorize(
        request, datetime.fromtimestamp(1, tz=UTC)
    )
    return DenialEvidence.from_result(request, result)


def test_repository_authorization_schema_is_versioned_and_privacy_minimized() -> None:
    assert_privacy_minimized_denial_schema(load_schema())


def test_real_denial_evidence_matches_the_declared_schema_contract() -> None:
    assert_matches_denial_schema(repository_mismatch_evidence().to_dict(), load_schema())


def test_schema_privacy_drift_is_rejected() -> None:
    schema = load_schema()
    schema["additionalProperties"] = True

    with pytest.raises(AssertionError):
        assert_privacy_minimized_denial_schema(schema)
