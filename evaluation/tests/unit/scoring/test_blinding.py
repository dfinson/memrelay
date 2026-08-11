from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.domain.entities import ArtifactManifest
from memrelay_eval.domain.errors import BlindingConformanceError, UnqualifiedEvidencePortError
from memrelay_eval.domain.ids import AttemptId, RetentionPolicyId, RunId
from memrelay_eval.domain.states import ArtifactScope
from memrelay_eval.scoring.blinding import (
    BlindingPolicy,
    FrozenLeakageProtocol,
    LeakageCandidate,
    detect_direct_leaks,
    evaluate_leakage_classifier,
    generate_blinded_view,
    generate_sentinel_corpus,
    require_blinding_conformance,
    sentinel_corpus_sha256,
    write_blinded_view_manifest,
)


def _source(store: InMemoryArtifactStore):
    return store.put_bytes(
        json.dumps(
            {
                "requirements": "Preserve behavior.",
                "code": {"file": "src/feature.py", "content": "def feature(): return True"},
                "patch": "diff --git a/src/feature.py b/src/feature.py",
                "tests": ["tests/test_feature.py"],
                "artifact_locations": {
                    "patch": "C:\\memrelay\\attempts\\trial-1\\patch.diff",
                    "tests": "/workspace/memrelay/tests/test_feature.py",
                },
                "assignment_record": {"arm_name": "control"},
                "provider": {"name": "copilot"},
                "tool_timing": {"tool_name": "shell", "duration_ms": 5},
            }
        ).encode(),
        media_type="application/json",
        classification="synthetic",
    )


def _manifest(view):
    return ArtifactManifest(
        artifact_id=view.view_artifact.artifact_id,
        kind="blinded_evidence_view",
        sha256=view.sha256,
        size_bytes=view.view_artifact.size_bytes,
        media_type="application/json",
        created_at=datetime(2026, 8, 10, tzinfo=UTC),
        producer_component="blinding_test",
        producer_version="1.0.0",
        classification="unpaid_conformance",
        contains_secrets=False,
        source_artifact_ids=(view.source_artifact.artifact_id, view.policy_artifact.artifact_id),
        retention_policy_id=RetentionPolicyId.new(),
        encryption=None,
        scope=ArtifactScope.ATTEMPT,
        run_id=RunId.new(),
        attempt_id=AttemptId.new(),
    )


def test_blinded_view_is_canonical_repeatable_and_preserves_judgment_evidence() -> None:
    store = InMemoryArtifactStore()
    source = _source(store)
    source_bytes = store.open_verified(source)
    policy = BlindingPolicy(treatment_aliases=("control", "treatment-a"))

    first = generate_blinded_view(store, source, policy)
    second = generate_blinded_view(store, source, policy)
    document = json.loads(first.bytes)

    assert first.bytes == second.bytes
    assert first.sha256 == second.sha256
    assert store.open_verified(source) == source_bytes
    assert document["evidence"]["requirements"] == "Preserve behavior."
    assert document["evidence"]["code"]["file"] == "src/feature.py"
    assert document["evidence"]["patch"].startswith("diff --git")
    assert document["evidence"]["tests"] == ["tests/test_feature.py"]
    assert set(document["artifact_locations"]) == {"patch", "tests"}
    assert all(
        value.startswith("artifact://blinded/") for value in document["artifact_locations"].values()
    )
    assert "assignment_record" not in first.bytes.decode()
    assert "copilot" not in first.bytes.decode()
    assert "duration_ms" not in first.bytes.decode()


def test_blinded_view_redacts_registered_aliases_and_detects_raw_direct_leaks() -> None:
    policy = BlindingPolicy(treatment_aliases=("control",))
    assert detect_direct_leaks({"treatment_code": "control"}, policy) == ("assignment",)

    store = InMemoryArtifactStore()
    source = store.put_bytes(
        b'{"requirements":"control","artifact_locations":{}}',
        media_type="application/json",
        classification="synthetic",
    )
    assert b"control" not in generate_blinded_view(store, source, policy).bytes


def test_blinded_view_writes_only_matching_inherited_manifest() -> None:
    store = InMemoryArtifactStore()
    view = generate_blinded_view(store, _source(store), BlindingPolicy())

    assert write_blinded_view_manifest(store, view, _manifest(view))
    bad = _manifest(view)
    with pytest.raises(BlindingConformanceError, match="manifest provenance mismatch"):
        write_blinded_view_manifest(store, view, replace(bad, kind="other"))


def test_blinded_view_rejects_a_non_conformance_artifact_port() -> None:
    class PaidArtifactStore(InMemoryArtifactStore):
        provenance = "durable"
        eligible_for_paid_or_study = True

    store = PaidArtifactStore()
    source = _source(store)

    with pytest.raises(UnqualifiedEvidencePortError):
        generate_blinded_view(store, source, BlindingPolicy())


def test_sentinel_corpus_and_leakage_protocol_are_reproducible() -> None:
    corpus = generate_sentinel_corpus(17, 4)
    assert corpus == generate_sentinel_corpus(17, 4)
    assert sentinel_corpus_sha256(corpus) == sentinel_corpus_sha256(corpus)
    training_ids = tuple(f"train-{arm}-{index}" for arm in range(2) for index in range(20))
    evaluation_ids = tuple(f"eval-{arm}-{index}" for arm in range(2) for index in range(100))

    protocol = FrozenLeakageProtocol(
        seed=17,
        sentinel_corpus_sha256=sentinel_corpus_sha256(corpus),
        training_ids=training_ids,
        evaluation_ids=evaluation_ids,
    )
    conformance = evaluate_leakage_classifier(
        tuple(
            LeakageCandidate(candidate_id, b"same feature", arm)
            for prefix, arm, count in (
                ("train", 0, 20),
                ("train", 1, 20),
                ("eval", 0, 100),
                ("eval", 1, 100),
            )
            for candidate_id in (f"{prefix}-{arm}-{index}" for index in range(count))
        ),
        protocol,
    )

    assert conformance.auc == 0.5
    assert conformance.upper_auc_95 <= 0.60
    require_blinding_conformance((), conformance)


def test_classifier_upper_bound_and_direct_leaks_fail_closed() -> None:
    protocol = FrozenLeakageProtocol(
        seed=1,
        sentinel_corpus_sha256="0" * 64,
        training_ids=("train-zero", "train-one"),
        evaluation_ids=("eval-zero", "eval-one"),
    )
    conformance = evaluate_leakage_classifier(
        (
            LeakageCandidate("train-zero", b"zero only", 0),
            LeakageCandidate("train-one", b"one only", 1),
            LeakageCandidate("eval-zero", b"zero only", 0),
            LeakageCandidate("eval-one", b"one only", 1),
        ),
        protocol,
    )

    assert conformance.upper_auc_95 > 0.60
    with pytest.raises(BlindingConformanceError, match="classifier auc upper bound"):
        require_blinding_conformance((), conformance)
    with pytest.raises(BlindingConformanceError, match="direct blinding leak"):
        require_blinding_conformance(("assignment",), conformance)
