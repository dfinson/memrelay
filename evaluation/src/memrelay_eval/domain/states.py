"""Frozen evaluator state vocabularies."""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    PLANNED = "planned"
    ASSIGNED = "assigned"
    PROVISIONED = "provisioned"
    RUNNING = "running"
    EXPORTED = "exported"
    SCORED = "scored"
    RECONCILED = "reconciled"
    INCLUDED = "included"
    EXCLUDED = "excluded"


class AttemptTerminalKind(StrEnum):
    SUCCEEDED = "succeeded"
    AGENT_FAILED = "agent_failed"
    TIMED_OUT = "timed_out"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    QUOTA_EXHAUSTED = "quota_exhausted"
    GRADER_FAILED = "grader_failed"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    INFRASTRUCTURE_FAILED_PRE_EXPOSURE = "infrastructure_failed_pre_exposure"
    INFRASTRUCTURE_FAILED_POST_EXPOSURE = "infrastructure_failed_post_exposure"
    CANCELLED_BY_CIRCUIT_BREAKER = "cancelled_by_circuit_breaker"


class ArtifactScope(StrEnum):
    EXPERIMENT = "experiment"
    RUN = "run"
    ATTEMPT = "attempt"


class InclusionStatus(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
