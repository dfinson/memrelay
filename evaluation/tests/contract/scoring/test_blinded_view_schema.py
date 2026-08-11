from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.scoring.blinding import BlindingPolicy, generate_blinded_view


def test_blinded_view_matches_the_versioned_schema() -> None:
    store = InMemoryArtifactStore()
    source = store.put_bytes(
        (
            b'{"requirements":"Keep behavior.",'
            b'"artifact_locations":{"patch":"C:\\\\memrelay\\\\patch.diff"}}'
        ),
        media_type="application/json",
        classification="synthetic",
    )
    view = generate_blinded_view(store, source, BlindingPolicy())
    schema_path = Path(__file__).parents[3] / "schemas" / "blinded-view.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(json.loads(view.bytes))
