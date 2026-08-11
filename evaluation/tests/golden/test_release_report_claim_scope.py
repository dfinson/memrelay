from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from memrelay_eval.evidence.release_map import ReleaseEvidence, map_release_evidence
from tests.unit.evidence.test_release_evidence_map import _fixture, _scope, _statement


def test_release_map_terminal_status_golden() -> None:
    observation_scope = _scope(evidence_id="RELEASE-CONTINUOUS", gate_id="GATE-OBSERVATION")
    observation = ReleaseEvidence(
        observation_scope,
        "observation_sentinel",
        "OBSERVATION-REPLAY-DELIVERY",
        ("causal treatment effect",),
        "passed",
        "passed",
    )
    blocked_statement = _statement(
        scope=_scope(evidence_id="EV-ROUNDTRIP-MCP"),
        statement="CL-PIPELINE-SEAM",
    )
    result = map_release_evidence(
        (_fixture(), observation),
        (
            replace(_statement(), statement_id="statement-1"),
            replace(
                _statement(
                scope=observation_scope,
                statement_kind="observation_qualification",
                statement="OBSERVATION-REPLAY-DELIVERY",
                ),
                statement_id="statement-2",
            ),
            replace(blocked_statement, statement_id="statement-3"),
        ),
    )
    expected = json.loads(
        (Path(__file__).with_name("release-evidence-map.expected.json")).read_text("utf-8")
    )

    assert {
        "schema_version": result.to_document()["schema_version"],
        "artifact_type": result.to_document()["artifact_type"],
        "decision_statuses": [item.terminal_status for item in result.decisions],
    } == expected
