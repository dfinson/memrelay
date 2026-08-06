"""Conservative authorization for the sole permitted retry path."""

from __future__ import annotations

from memrelay_eval.domain.entities import (
    Attempt,
    AttemptTerminal,
    ExposureDecision,
    FreshIsolationAttestation,
    Protocol,
    RetryAuthorization,
    Run,
)
from memrelay_eval.domain.errors import RetryDeniedError
from memrelay_eval.domain.ids import AttemptId
from memrelay_eval.domain.policies import retry_eligibility_denial_code
from memrelay_eval.domain.ports import LedgerPort
from memrelay_eval.domain.states import RetryRequestPurpose


class RetryAuthorizer:
    """Applies AD-18 without creating a run lifecycle transition."""

    def __init__(self, ledger: LedgerPort) -> None:
        self._ledger = ledger

    def authorize(
        self,
        protocol: Protocol,
        run: Run,
        parent_attempt: Attempt,
        parent_terminal: AttemptTerminal,
        *,
        exposure: ExposureDecision | None,
        isolation: FreshIsolationAttestation | None,
        purpose: RetryRequestPurpose = RetryRequestPurpose.RETRY_FAILURE,
    ) -> RetryAuthorization:
        if purpose is not RetryRequestPurpose.RETRY_FAILURE:
            raise RetryDeniedError("retry_favorable_substitution_forbidden")
        if (
            parent_attempt.run_id != run.id
            or parent_terminal.attempt_id != parent_attempt.id
            or parent_terminal.run_id != run.id
        ):
            raise RetryDeniedError("retry_lineage_mismatch")
        if self._ledger.attempt_terminal_for(parent_attempt.id) != parent_terminal:
            raise RetryDeniedError("retry_terminal_not_authoritative")
        if denial_code := retry_eligibility_denial_code(
            protocol, parent_terminal, exposure, isolation
        ):
            raise RetryDeniedError(denial_code)
        authorization = RetryAuthorization(
            run_id=run.id,
            assignment_id=run.assignment_id,
            parent_assignment_id=run.assignment_id,
            parent_attempt_id=parent_attempt.id,
            attempt=Attempt(AttemptId.new(), run.id),
            parent_terminal=parent_terminal,
            exposure_evidence_refs=exposure.evidence_refs,
            isolation_evidence_refs=isolation.evidence_refs,
        )
        if not self._ledger.append_retry_authorization_once(authorization):
            raise RetryDeniedError("retry_already_authorized_for_run")
        return authorization
