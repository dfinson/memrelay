from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from memrelay_eval.analysis.audits import (
    AuditPolicy,
    TaskAudit,
    TaskAuditDisposition,
    materialize_task_audits,
)
from memrelay_eval.analysis.gates import (
    CategoricalEvent,
    CategoricalGatePolicy,
    claim_status_after_categorical_gate,
    decide_categorical_overrides,
)
from memrelay_eval.domain.errors import SafetyAnalysisError

_HASH = "a" * 64


def _audit_policy() -> AuditPolicy:
    return AuditPolicy(
        policy_id="audit-policy-v1",
        duplicate_threshold_sha256=_HASH,
        cutoff_policy_sha256="b" * 64,
        grader_policy_sha256="c" * 64,
    )


def _audit(policy: AuditPolicy, kind: str, outcome: str) -> TaskAudit:
    return TaskAudit(
        task_id="task-1",
        kind=kind,
        outcome=outcome,
        policy_sha256=policy.sha256,
        evidence_sha256=("d" * 64,),
        disposition_id=f"{kind}-evidence",
    )


def test_compromised_task_is_quarantined_not_selectively_repaired() -> None:
    policy = _audit_policy()
    all_passing = tuple(
        _audit(policy, kind, "pass")
        for kind in sorted(
            {
                "necessity",
                "shortcut",
                "duplicate",
                "canary",
                "holdout",
                "cutoff",
                "baseline_stability",
                "gold_stability",
            }
        )
    )
    prior = TaskAuditDisposition(
        task_id="task-1",
        status="quarantined",
        reasons=("CANARY_CONTAMINATION",),
        policy_sha256=policy.sha256,
        audit_evidence_sha256=("e" * 64,),
    )

    disposition = materialize_task_audits(
        policy=policy,
        audits=all_passing,
        prior_dispositions=(prior,),
    )[0]

    assert disposition.status == "quarantined"
    assert disposition.reasons == ("CANARY_CONTAMINATION", "SELECTIVE_REPAIR_FORBIDDEN")
    assert disposition.audit_evidence_sha256 == ("d" * 64, "e" * 64)
    assert disposition.prior_disposition_digest == prior.digest


def test_missing_or_failed_audit_remains_governed_evidence() -> None:
    policy = _audit_policy()
    audits = (
        _audit(policy, "necessity", "pass"),
        _audit(policy, "canary", "fail"),
        _audit(policy, "shortcut", "missing"),
    )

    disposition = materialize_task_audits(policy=policy, audits=audits)[0]

    assert disposition.status == "quarantined"
    assert "CANARY_CONTAMINATION" in disposition.reasons
    assert "MISSING_SHORTCUT_AUDIT" in disposition.reasons
    assert "MISSING_DUPLICATE_AUDIT" in disposition.reasons


def test_confirmed_categorical_event_blocks_favorable_aggregate_claim() -> None:
    policy = CategoricalGatePolicy(policy_id="gate-policy-v1", policy_document_sha256=_HASH)
    decisions = decide_categorical_overrides(
        policy=policy,
        events=(
            CategoricalEvent(
                event_id="event-1",
                kind="treatment_contamination",
                scope_id="primary-stage",
                affected_claim_ids=("claim-reliability",),
                evidence_sha256=("b" * 64,),
                policy_sha256=policy.sha256,
                confirmed=True,
            ),
        ),
    )

    decision = decisions[0]
    assert decision.status == "blocked"
    assert decision.bounded_language_required is True
    assert decision.blocking_event_ids == ("event-1",)
    assert (
        claim_status_after_categorical_gate(aggregate_favorable=True, decision=decision)
        == "blocked"
    )


def test_authority_conflict_and_duplicate_audits_fail_closed() -> None:
    policy = _audit_policy()
    audit = _audit(policy, "canary", "pass")
    with pytest.raises(SafetyAnalysisError, match="duplicate task audit authority"):
        materialize_task_audits(policy=policy, audits=(audit, audit))

    gate_policy = CategoricalGatePolicy(policy_id="gate-policy-v1", policy_document_sha256=_HASH)
    with pytest.raises(SafetyAnalysisError, match="categorical event authority conflict"):
        decide_categorical_overrides(
            policy=gate_policy,
            events=(
                CategoricalEvent(
                    event_id="event-1",
                    kind="hash_mismatch",
                    scope_id="primary-stage",
                    affected_claim_ids=("claim-reliability",),
                    evidence_sha256=("b" * 64,),
                    policy_sha256="c" * 64,
                    confirmed=True,
                ),
            ),
        )


def test_invalid_categorical_decision_never_allows_a_claim() -> None:
    with pytest.raises(SafetyAnalysisError, match="categorical gate decision invalid"):
        from memrelay_eval.analysis.gates import CategoricalGateDecision

        CategoricalGateDecision(
            scope_id="primary-stage",
            status="invalid",
            blocking_event_ids=(),
            affected_claim_ids=(),
            policy_sha256=_HASH,
            evidence_sha256=(),
            bounded_language_required=False,
        )


def test_audit_and_categorical_decisions_match_versioned_schemas() -> None:
    policy = _audit_policy()
    audit_disposition = materialize_task_audits(
        policy=policy,
        audits=tuple(
            _audit(policy, kind, "pass")
            for kind in {
                "necessity",
                "shortcut",
                "duplicate",
                "canary",
                "holdout",
                "cutoff",
                "baseline_stability",
                "gold_stability",
            }
        ),
    )[0]
    gate_policy = CategoricalGatePolicy(policy_id="gate-policy-v1", policy_document_sha256=_HASH)
    gate_decision = decide_categorical_overrides(
        policy=gate_policy,
        events=(
            CategoricalEvent(
                event_id="event-1",
                kind="hash_mismatch",
                scope_id="primary-stage",
                affected_claim_ids=("claim-reliability",),
                evidence_sha256=("b" * 64,),
                policy_sha256=gate_policy.sha256,
                confirmed=True,
            ),
        ),
    )[0]
    schemas = Path(__file__).parents[3] / "schemas"

    jsonschema.validate(
        audit_disposition.to_document(),
        json.loads((schemas / "task-audit-disposition.schema.json").read_text("utf-8")),
    )
    jsonschema.validate(
        gate_decision.to_document(),
        json.loads((schemas / "categorical-gate-decision.schema.json").read_text("utf-8")),
    )
