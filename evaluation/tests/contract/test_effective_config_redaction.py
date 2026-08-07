from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.errors import SecretConfigurationError
from memrelay_eval.orchestration.configuration import (
    persist_effective_configuration,
    resolve_effective_configuration,
)


def test_effective_configuration_schema_and_secret_canary_boundary() -> None:
    secret = "canary-secret-value-never-persisted"
    configuration = resolve_effective_configuration(
        cli={
            "credential_references": [
                {
                    "variable_name": "GITHUB_TOKEN",
                    "target_process": "copilot_sdk_worker",
                }
            ]
        }
    )
    store = InMemoryArtifactStore()
    stored = persist_effective_configuration(configuration, store)
    payload = store.open_verified(stored.artifact)
    document = json.loads(payload)
    schema_path = Path(__file__).parents[2] / "schemas" / "effective-config.schema.json"

    jsonschema.Draft202012Validator(json.loads(schema_path.read_text(encoding="utf-8"))).validate(
        document
    )
    assert secret.encode("utf-8") not in payload
    assert payload == canonical_bytes(document)
    assert "value" not in document["fields"]["credential_references"]["value"][0]

    with pytest.raises(SecretConfigurationError) as error:
        resolve_effective_configuration(evaluator_file={"stage_rules": {"token": secret}})
    assert secret not in str(error.value)
