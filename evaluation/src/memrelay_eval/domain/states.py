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


class ExposureClassification(StrEnum):
    UNEXPOSED = "unexposed"
    EXPOSED = "exposed"
    AMBIGUOUS = "ambiguous"
    UNKNOWN = "unknown"
    CONTRADICTORY = "contradictory"


class ExposurePhase(StrEnum):
    """The five evidence-backed boundaries that determine attempt exposure."""

    ASSIGNMENT_RESOLUTION = "assignment_resolution"
    MEMORY_PROVISION = "memory_provision"
    TASK_DELIVERY = "task_delivery"
    INFERENCE = "inference"
    ACCESS = "access"


class RetryRequestPurpose(StrEnum):
    RETRY_FAILURE = "retry_failure"
    BEST_OF_N = "best_of_n"
    REPEAT_UNTIL_SUCCESS = "repeat_until_success"
    FAVORABLE_SUBSTITUTION = "favorable_substitution"


class InternalRetrySubsystem(StrEnum):
    INSPECT = "inspect"
    SDK = "sdk"
    MEMRELAY = "memrelay"
    GRADER = "grader"


class ArtifactScope(StrEnum):
    EXPERIMENT = "experiment"
    RUN = "run"
    ATTEMPT = "attempt"


class InclusionStatus(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"


class LedgerIntentKind(StrEnum):
    CREATE_EXPERIMENT = "create_experiment"
    CREATE_RUN = "create_run"
    CREATE_ATTEMPT = "create_attempt"
    RUN_TRANSITION = "run_transition"
    ATTEMPT_TERMINAL = "attempt_terminal"
    ARTIFACT_LINK = "artifact_link"
    RETRY_LINEAGE = "retry_lineage"
    INCLUSION_DECISION = "inclusion_decision"
