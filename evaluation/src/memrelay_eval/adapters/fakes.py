"""Deterministic in-memory adapters for unpaid conformance only."""

from __future__ import annotations

from datetime import UTC, datetime
from threading import Lock
from types import MappingProxyType

from memrelay_eval.domain.entities import (
    ArtifactLink,
    ArtifactManifest,
    ArtifactRef,
    AttemptTerminal,
    DynamicSequenceCleanup,
    DynamicSequenceTerminal,
    ExposureRecord,
    InclusionDecision,
    InternalRetryRecord,
    RetryAuthorization,
    RunTransition,
    TelemetryObservation,
)
from memrelay_eval.domain.errors import (
    ArtifactIntegrityError,
    AttemptTerminalAlreadyRecordedError,
    IneligibleEvidenceError,
    LedgerIntentConflictError,
)
from memrelay_eval.domain.ids import AttemptId, ExperimentId, IntentId, RunId
from memrelay_eval.domain.intents import (
    ArtifactLinkIntent,
    AttemptTerminalIntent,
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    InclusionDecisionIntent,
    IntentAck,
    IntentRejection,
    LedgerIntentType,
    RetryLineageIntent,
    RunTransitionIntent,
    delivery_payload_digest,
)
from memrelay_eval.domain.states import (
    AttemptTerminalKind,
    InclusionStatus,
    InternalRetrySubsystem,
    RunState,
)
from memrelay_eval.evidence.manifest import manifest_bytes

_REDACTED_TERMS = (
    "prompt",
    "code",
    "repo",
    "repository",
    "user",
    "credential",
    "secret",
    "provider",
    "treatment",
    "arm",
)


class InMemoryArtifactStore:
    """Content-addressed fake storage with deterministic byte verification."""

    provenance = "unpaid_conformance"
    eligible_for_paid_or_study = False

    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}
        self._manifests: dict[str, ArtifactManifest] = {}

    def put_bytes(self, data: bytes, *, media_type: str, classification: str) -> ArtifactRef:
        del media_type, classification
        artifact = ArtifactRef.from_bytes(bytes(data))
        self._blobs.setdefault(artifact.sha256, bytes(data))
        return artifact

    def open_verified(self, artifact: ArtifactRef) -> bytes:
        data = self._blobs.get(artifact.sha256)
        if data is None or ArtifactRef.from_bytes(data) != artifact:
            raise ArtifactIntegrityError("artifact is missing or corrupt")
        return bytes(data)

    def write_manifest(self, manifest: ArtifactManifest) -> ArtifactRef:
        payload = self._blobs.get(manifest.sha256)
        if payload is None or len(payload) != manifest.size_bytes:
            raise ArtifactIntegrityError("manifest does not match a stored immutable artifact")
        canonical = manifest_bytes(manifest)
        reference = self.put_bytes(
            canonical, media_type="application/json", classification=manifest.classification
        )
        self._manifests[reference.sha256] = manifest
        return reference


class InMemoryLedger:
    """Append-only fake ledger that cannot authorize paid or study inclusion."""

    provenance = "unpaid_conformance"
    eligible_for_paid_or_study = False

    def __init__(self) -> None:
        self._transitions: list[RunTransition] = []
        self._attempt_terminals: list[AttemptTerminal] = []
        self._dynamic_sequence_terminals: list[DynamicSequenceTerminal] = []
        self._dynamic_sequence_cleanup: list[DynamicSequenceCleanup] = []
        self._exposure_records: list[ExposureRecord] = []
        self._exposure_lock = Lock()
        self._internal_retries: list[InternalRetryRecord] = []
        self._retry_authorizations: list[RetryAuthorization] = []
        self._artifact_links: list[ArtifactLink] = []
        self._inclusions: list[InclusionDecision] = []
        self._internal_retry_lock = Lock()
        self._retry_authorization_lock = Lock()
        self._experiments: set[ExperimentId] = set()
        self._runs: dict[RunId, ExperimentId] = {}
        self._attempts: dict[AttemptId, RunId] = {}
        self._terminals: dict[AttemptId, AttemptTerminal] = {}
        self._retry_links: list[tuple[AttemptId, AttemptId]] = []
        self._intent_results: dict[IntentId, IntentAck | IntentRejection] = {}
        self._transition_digests: dict[RunId, str | None] = {}

    def append_transition(self, transition: RunTransition) -> None:
        history = self.history(transition.run_id)
        if not history and transition.previous.value != "planned":
            raise ValueError("authoritative run history must begin at planned")
        if history and history[-1].next_state != transition.previous:
            raise ValueError("transition does not continue this run's authoritative history")
        self._transitions.append(transition)

    def append_attempt_terminal(self, terminal: AttemptTerminal) -> None:
        if any(item.attempt_id == terminal.attempt_id for item in self._attempt_terminals):
            raise AttemptTerminalAlreadyRecordedError(AttemptTerminalAlreadyRecordedError.code)
        self._attempt_terminals.append(terminal)

    def attempt_terminal_for(self, attempt_id: AttemptId) -> AttemptTerminal | None:
        return next(
            (item for item in self._attempt_terminals if item.attempt_id == attempt_id),
            None,
        )

    def append_dynamic_sequence_terminal(self, terminal: DynamicSequenceTerminal) -> None:
        if any(
            item.sequence_id == terminal.sequence_id for item in self._dynamic_sequence_terminals
        ):
            raise AttemptTerminalAlreadyRecordedError("dynamic_sequence_terminal_already_recorded")
        self._dynamic_sequence_terminals.append(terminal)

    def append_dynamic_sequence_cleanup(self, cleanup: DynamicSequenceCleanup) -> None:
        if any(item.sequence_id == cleanup.sequence_id for item in self._dynamic_sequence_cleanup):
            raise AttemptTerminalAlreadyRecordedError("dynamic_sequence_cleanup_already_recorded")
        if not any(
            item.sequence_id == cleanup.sequence_id for item in self._dynamic_sequence_terminals
        ):
            raise ValueError("dynamic sequence cleanup requires an authoritative terminal")
        self._dynamic_sequence_cleanup.append(cleanup)

    def append_exposure_record(self, record: ExposureRecord) -> None:
        with self._exposure_lock:
            if any(item.attempt_id == record.attempt_id for item in self._exposure_records):
                from memrelay_eval.domain.errors import ExposureAlreadyRecordedError

                raise ExposureAlreadyRecordedError(ExposureAlreadyRecordedError.code)
            self._exposure_records.append(record)

    def reserve_internal_retry(
        self,
        attempt_id: AttemptId,
        subsystem: InternalRetrySubsystem,
        maximum_retries: int,
    ) -> InternalRetryRecord | None:
        with self._internal_retry_lock:
            retries = tuple(
                item
                for item in self._internal_retries
                if item.attempt_id == attempt_id and item.subsystem is subsystem
            )
            if len(retries) >= maximum_retries:
                return None
            record = InternalRetryRecord(attempt_id, subsystem, len(retries) + 1)
            self._internal_retries.append(record)
            return record

    def internal_retries_for(
        self, attempt_id: AttemptId, subsystem: InternalRetrySubsystem
    ) -> tuple[InternalRetryRecord, ...]:
        return tuple(
            item
            for item in self._internal_retries
            if item.attempt_id == attempt_id and item.subsystem is subsystem
        )

    def append_retry_authorization_once(self, authorization: RetryAuthorization) -> bool:
        with self._retry_authorization_lock:
            if self.retry_authorizations_for(authorization.run_id):
                return False
            self._retry_authorizations.append(authorization)
            return True

    def append_artifact_link(self, link: ArtifactLink) -> None:
        self._artifact_links.append(link)

    def append_inclusion(self, decision: InclusionDecision) -> None:
        if decision.status is InclusionStatus.INCLUDED:
            raise IneligibleEvidenceError("unpaid conformance evidence cannot support inclusion")
        self._inclusions.append(decision)

    def submit_intent(self, intent: LedgerIntentType) -> IntentAck | IntentRejection:
        digest, preflight_rejection = delivery_payload_digest(intent)
        prior = self._intent_results.get(intent.intent_id)
        if prior is not None:
            if prior.canonical_payload_digest != digest:
                raise LedgerIntentConflictError(
                    "intent ID was reused with a different canonical payload digest"
                )
            if isinstance(prior, IntentAck):
                return IntentAck(
                    prior.intent_id,
                    prior.canonical_payload_digest,
                    prior.kind,
                    idempotent=True,
                )
            return IntentRejection(
                prior.intent_id,
                prior.canonical_payload_digest,
                prior.kind,
                prior.reason_code,
                idempotent=True,
            )

        if preflight_rejection is not None:
            return self._reject_with_digest(intent, digest, preflight_rejection)

        if isinstance(intent, CreateExperimentIntent):
            if intent.experiment_id in self._experiments:
                return self.reject_intent(intent, "duplicate_experiment")
            self._experiments.add(intent.experiment_id)
            return self._ack(intent)
        if isinstance(intent, CreateRunIntent):
            if intent.experiment_id not in self._experiments:
                return self.reject_intent(intent, "unknown_experiment")
            if intent.run_id in self._runs:
                return self.reject_intent(intent, "duplicate_run")
            self._runs[intent.run_id] = intent.experiment_id
            return self._ack(intent)
        if isinstance(intent, CreateAttemptIntent):
            if intent.run_id not in self._runs:
                return self.reject_intent(intent, "unknown_run")
            if intent.metadata.source_attempt_id is not None:
                return self.reject_intent(intent, "initial_attempt_must_not_have_predecessor")
            if any(run_id == intent.run_id for run_id in self._attempts.values()):
                return self.reject_intent(intent, "unlinked_attempt_creation")
            self._attempts[intent.attempt_id] = intent.run_id
            return self._ack(intent)
        if isinstance(intent, RunTransitionIntent):
            return self._submit_transition(intent)
        if isinstance(intent, AttemptTerminalIntent):
            return self._submit_terminal(intent)
        if isinstance(intent, ArtifactLinkIntent):
            return self._submit_artifact_link(intent)
        if isinstance(intent, RetryLineageIntent):
            return self._submit_retry(intent)
        if isinstance(intent, InclusionDecisionIntent):
            return self._submit_inclusion(intent)
        return self.reject_intent(intent, "unknown_intent_kind")

    def reject_intent(self, intent: LedgerIntentType, reason_code: str) -> IntentRejection:
        digest, preflight_rejection = delivery_payload_digest(intent)
        return self._reject_with_digest(intent, digest, preflight_rejection or reason_code)

    def _reject_with_digest(
        self, intent: LedgerIntentType, digest: str, reason_code: str
    ) -> IntentRejection:
        prior = self._intent_results.get(intent.intent_id)
        if prior is not None:
            if prior.canonical_payload_digest != digest:
                raise LedgerIntentConflictError(
                    "intent ID was reused with a different canonical payload digest"
                )
            if isinstance(prior, IntentRejection):
                return IntentRejection(
                    prior.intent_id,
                    prior.canonical_payload_digest,
                    prior.kind,
                    prior.reason_code,
                    idempotent=True,
                )
            raise LedgerIntentConflictError("control rejection conflicts with an accepted intent")
        result = IntentRejection(
            intent.intent_id,
            digest,
            intent.kind,
            reason_code,
        )
        self._intent_results[intent.intent_id] = result
        return result

    def _ack(self, intent: LedgerIntentType) -> IntentAck:
        digest, preflight_rejection = delivery_payload_digest(intent)
        if preflight_rejection is not None:
            raise ValueError("malformed intent cannot be acknowledged")
        result = IntentAck(intent.intent_id, digest, intent.kind)
        self._intent_results[intent.intent_id] = result
        return result

    def _submit_transition(self, intent: RunTransitionIntent) -> IntentAck | IntentRejection:
        if intent.run_id not in self._runs:
            return self.reject_intent(intent, "unknown_run")
        history = self.history(intent.run_id)
        current = history[-1].next_state if history else RunState.PLANNED
        digest = self._transition_digests.get(intent.run_id)
        if (
            current is not intent.previous
            or intent.metadata.expected_prior_state is not current
            or intent.metadata.expected_prior_digest != digest
        ):
            return self.reject_intent(intent, "stale_prior_state")
        if self._attempts.get(intent.metadata.source_attempt_id) != intent.run_id:
            return self.reject_intent(intent, "invalid_source_attempt")
        if intent.next_state in (RunState.INCLUDED, RunState.EXCLUDED):
            decision = next(
                (item for item in self._inclusions if item.run_id == intent.run_id),
                None,
            )
            if decision is None:
                return self.reject_intent(intent, "missing_inclusion_decision")
            if decision.status.value != intent.next_state.value:
                return self.reject_intent(intent, "inclusion_transition_mismatch")
        try:
            self.append_transition(
                RunTransition(
                    intent.run_id, intent.previous, intent.next_state, intent.metadata.occurred_at
                )
            )
        except ValueError:
            return self.reject_intent(intent, "invalid_lifecycle_transition")
        result = self._ack(intent)
        self._transition_digests[intent.run_id] = result.canonical_payload_digest
        return result

    def _submit_terminal(self, intent: AttemptTerminalIntent) -> IntentAck | IntentRejection:
        if self._attempts.get(intent.attempt_id) != intent.run_id:
            return self.reject_intent(intent, "unknown_attempt")
        if self._attempts.get(intent.metadata.source_attempt_id) != intent.run_id:
            return self.reject_intent(intent, "invalid_source_attempt")
        if intent.attempt_id in self._terminals:
            return self.reject_intent(intent, "attempt_already_terminal")
        if intent.classification is AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE and (
            intent.metadata.source_attempt_id != intent.attempt_id
            or intent.pre_exposure_evidence is None
            or intent.pre_exposure_evidence not in intent.metadata.evidence_refs
        ):
            return self.reject_intent(intent, "unverified_pre_exposure_failure")
        if (
            intent.classification is not AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE
            and intent.pre_exposure_evidence is not None
        ):
            return self.reject_intent(intent, "unexpected_pre_exposure_evidence")
        self._terminals[intent.attempt_id] = AttemptTerminal(
            intent.attempt_id,
            intent.run_id,
            intent.classification,
            intent.metadata.occurred_at,
            intent.metadata.reason_code,
            intent.metadata.evidence_refs,
        )
        return self._ack(intent)

    def _submit_artifact_link(self, intent: ArtifactLinkIntent) -> IntentAck | IntentRejection:
        link = intent.link
        if link.run_id is None:
            if link.experiment_id is None or link.experiment_id not in self._experiments:
                return self.reject_intent(intent, "invalid_artifact_owner")
            self.append_artifact_link(link)
            return self._ack(intent)
        if link.run_id not in self._runs:
            return self.reject_intent(intent, "unknown_run")
        if link.experiment_id is not None and self._runs[link.run_id] != link.experiment_id:
            return self.reject_intent(intent, "artifact_experiment_run_mismatch")
        if self._attempts.get(intent.metadata.source_attempt_id) != link.run_id:
            return self.reject_intent(intent, "invalid_source_attempt")
        if link.attempt_id is not None and self._attempts.get(link.attempt_id) != link.run_id:
            return self.reject_intent(intent, "attempt_run_mismatch")
        self.append_artifact_link(link)
        return self._ack(intent)

    def _submit_retry(self, intent: RetryLineageIntent) -> IntentAck | IntentRejection:
        return self.reject_intent(intent, "retry_authorization_control_only")

    def _submit_inclusion(self, intent: InclusionDecisionIntent) -> IntentAck | IntentRejection:
        decision = intent.decision
        history = self.history(decision.run_id)
        current = history[-1].next_state if history else RunState.PLANNED
        digest = self._transition_digests.get(decision.run_id)
        if (
            current is not RunState.RECONCILED
            or intent.metadata.expected_prior_state is not RunState.RECONCILED
            or intent.metadata.expected_prior_digest != digest
        ):
            return self.reject_intent(intent, "inclusion_before_reconciliation")
        if decision.status is InclusionStatus.INCLUDED:
            return self.reject_intent(intent, "unpaid_inclusion_forbidden")
        self._inclusions.append(decision)
        return self._ack(intent)

    def history(self, run_id: RunId) -> tuple[RunTransition, ...]:
        return tuple(item for item in self._transitions if item.run_id == run_id)

    def retry_authorizations_for(self, run_id: RunId) -> tuple[RetryAuthorization, ...]:
        return tuple(item for item in self._retry_authorizations if item.run_id == run_id)

    @property
    def artifact_links(self) -> tuple[ArtifactLink, ...]:
        return tuple(self._artifact_links)

    @property
    def attempt_terminals(self) -> tuple[AttemptTerminal, ...]:
        return tuple(self._attempt_terminals)

    @property
    def dynamic_sequence_terminals(self) -> tuple[DynamicSequenceTerminal, ...]:
        return tuple(self._dynamic_sequence_terminals)

    @property
    def dynamic_sequence_cleanup(self) -> tuple[DynamicSequenceCleanup, ...]:
        return tuple(self._dynamic_sequence_cleanup)

    @property
    def exposure_records(self) -> tuple[ExposureRecord, ...]:
        return tuple(self._exposure_records)

    @property
    def internal_retries(self) -> tuple[InternalRetryRecord, ...]:
        return tuple(self._internal_retries)

    @property
    def retry_authorizations(self) -> tuple[RetryAuthorization, ...]:
        return tuple(self._retry_authorizations)

    @property
    def inclusions(self) -> tuple[InclusionDecision, ...]:
        return tuple(self._inclusions)


class InMemoryTelemetry:
    """Redacting observation sink that is not lifecycle authority."""

    provenance = "unpaid_conformance"
    eligible_for_paid_or_study = False

    def __init__(self) -> None:
        self._observations: list[TelemetryObservation] = []
        self._terminals: list[AttemptTerminal] = []

    def emit(self, observation: TelemetryObservation) -> None:
        name = self._safe_name(observation.event_name)
        attributes = self._redact(observation.attributes)
        self._observations.append(TelemetryObservation(name, observation.occurred_at, attributes))

    def start_attempt(self, context: object) -> None:
        del context
        self.emit(TelemetryObservation("attempt_started", datetime(1970, 1, 1, tzinfo=UTC), {}))

    def finish_attempt(self, terminal: AttemptTerminal) -> None:
        self._terminals.append(terminal)
        self.emit(TelemetryObservation("attempt_finished", terminal.occurred_at, {}))

    @property
    def terminals(self) -> tuple[AttemptTerminal, ...]:
        return tuple(self._terminals)

    def flush(self, timeout_seconds: float) -> MappingProxyType:
        if timeout_seconds < 0:
            raise ValueError("timeout_seconds must not be negative")
        return MappingProxyType({"flushed": len(self._observations), "provenance": self.provenance})

    @property
    def observations(self) -> tuple[TelemetryObservation, ...]:
        return tuple(self._observations)

    @staticmethod
    def _safe_name(name: str) -> str:
        lowered = name.lower()
        return "redacted_event" if any(term in lowered for term in _REDACTED_TERMS) else name

    @staticmethod
    def _redact(attributes: object) -> dict[str, object]:
        if not isinstance(attributes, dict) and not hasattr(attributes, "items"):
            return {}
        safe: dict[str, object] = {}
        for key, value in attributes.items():
            if any(term in str(key).lower() for term in _REDACTED_TERMS):
                continue
            if isinstance(value, (bool, int, float)):
                safe[str(key)] = value
        return safe


class FakeCopilotPort:
    """No-op Copilot port that raises on any actual SDK operation."""

    provenance = "unpaid_conformance"
    eligible_for_paid_or_study = False

    def list_models(self) -> object:
        raise RuntimeError("FakeCopilotPort: no real Copilot SDK calls during offline planning")

    def run_session(self, session: object) -> object:
        raise RuntimeError("FakeCopilotPort: no real Copilot SDK calls during offline planning")


class FakeOpenAIPort:
    """No-op OpenAI port that raises on any actual API operation."""

    provenance = "unpaid_conformance"
    eligible_for_paid_or_study = False

    def create_completion(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("FakeOpenAIPort: no real OpenAI calls during offline planning")

    def create_embedding(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("FakeOpenAIPort: no real OpenAI calls during offline planning")


class FakeMemrelayPort:
    """No-op memrelay port that raises on any daemon or engine operation."""

    provenance = "unpaid_conformance"
    eligible_for_paid_or_study = False

    def note(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("FakeMemrelayPort: no real memrelay calls during offline planning")

    def search(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("FakeMemrelayPort: no real memrelay calls during offline planning")

    def health(self) -> object:
        raise RuntimeError("FakeMemrelayPort: no real memrelay calls during offline planning")
