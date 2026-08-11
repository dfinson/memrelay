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


class StageKind(StrEnum):
    """The fixed, treatment-neutral study-stage progression.

    ``bootstrap`` and ``conformance`` precede enrollment; the four enrollable
    stages plus the deny-by-default cross-repository stage are the identities the
    non-interactive ``run`` command recognizes. Values match the stage strings
    already used by the reconciliation authority.
    """

    BOOTSTRAP = "bootstrap"
    CONFORMANCE = "conformance"
    INTEGRATION = "integration"
    PILOT = "pilot"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    CROSS_REPOSITORY = "cross-repo"


class StageState(StrEnum):
    """Append-only stage lifecycle; authority is split from process completion.

    ``authorized`` is reachable only through an independent authorization
    artifact, never through successful construction, reconciliation, or process
    completion. ``accepted`` and ``rejected`` are terminal; a rejected stage is
    never re-entered.
    """

    PLANNED = "planned"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    PAUSED = "paused"
    CLOSING = "closing"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class HistoryMode(StrEnum):
    """The protocol-level history regime; these modes are never poolable."""

    CONTROLLED = "controlled"
    DYNAMIC = "dynamic"


class EvaluationStratum(StrEnum):
    """The independently identified product and direct-engine strata."""

    PRODUCT = "product"
    DIRECT_ENGINE = "direct_engine"


class SequenceState(StrEnum):
    """Append-only dynamic-sequence lifecycle states."""

    ENROLLED = "enrolled"
    PROVISIONED = "provisioned"
    RUNNING = "running"
    TERMINAL = "terminal"
    CLEANED_UP = "cleaned_up"


class ProbeWriteDisposition(StrEnum):
    """The frozen, protocol-declared handling of a controlled-history probe write."""

    DISABLED = "disabled"
    DISCARDED = "discarded"
    RECORDED_SEPARATELY = "recorded_separately"


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


class GraderTerminalKind(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"


class LedgerIntentKind(StrEnum):
    CREATE_EXPERIMENT = "create_experiment"
    CREATE_RUN = "create_run"
    CREATE_ATTEMPT = "create_attempt"
    RUN_TRANSITION = "run_transition"
    ATTEMPT_TERMINAL = "attempt_terminal"
    ARTIFACT_LINK = "artifact_link"
    RETRY_LINEAGE = "retry_lineage"
    INCLUSION_DECISION = "inclusion_decision"
    AUTHORITY_CONFLICT = "authority_conflict"
    COST_LEDGER = "cost_ledger"
    MONETARY_VIEW = "monetary_view"
