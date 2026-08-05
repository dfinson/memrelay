from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from memrelay_eval.cli.main import build_parser
from memrelay_eval.domain.entities import ArtifactManifest, ArtifactRef
from memrelay_eval.domain.ids import RetentionPolicyId, RunId
from memrelay_eval.domain.states import ArtifactScope

EVALUATION_ROOT = Path(__file__).parents[2]
REPOSITORY_ROOT = EVALUATION_ROOT.parent


def test_cli_is_composed_from_the_evaluator_project() -> None:
    parser = build_parser()
    assert parser.prog == "memrelay-eval"
    assert parser.parse_args(["foundation"]).command == "foundation"


def test_product_metadata_does_not_include_evaluator() -> None:
    product_metadata = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "memrelay-eval" not in product_metadata
    assert "memrelay_eval" not in product_metadata


def test_manifest_schema_covers_version_1_fields_and_scope_rules() -> None:
    schema = json.loads(
        (EVALUATION_ROOT / "schemas" / "artifact-manifest.schema.json").read_text(encoding="utf-8")
    )
    fields = set(schema["required"])
    assert {
        "schema_version",
        "artifact_id",
        "kind",
        "sha256",
        "size_bytes",
        "media_type",
        "created_at",
        "producer",
        "classification",
        "contains_secrets",
        "source_artifact_ids",
        "retention_policy_id",
        "encryption",
        "scope",
        "attempt_id",
    } <= fields
    assert schema["properties"]["schema_version"]["const"] == "1.0.0"


def test_manifest_lineage_uses_domain_artifact_ids_matching_schema_pattern() -> None:
    source_artifact = ArtifactRef.from_bytes(b"source artifact")
    artifact = ArtifactRef.from_bytes(b"derived artifact")
    manifest = ArtifactManifest(
        artifact_id=artifact.artifact_id,
        kind="derived_fixture",
        sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        media_type="application/octet-stream",
        created_at=datetime.now(UTC),
        producer_component="contract_test",
        producer_version="1.0.0",
        classification="synthetic",
        contains_secrets=False,
        source_artifact_ids=(source_artifact.artifact_id,),
        retention_policy_id=RetentionPolicyId.new(),
        encryption=None,
        scope=ArtifactScope.RUN,
        run_id=RunId.new(),
    )
    schema = json.loads(
        (EVALUATION_ROOT / "schemas" / "artifact-manifest.schema.json").read_text(encoding="utf-8")
    )
    lineage_pattern = schema["properties"]["source_artifact_ids"]["items"]["pattern"]

    assert re.fullmatch(lineage_pattern, manifest.to_dict()["source_artifact_ids"][0])
