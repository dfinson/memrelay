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
    InclusionDecision,
    InternalRetryRecord,
    RetryAuthorization,
    RunTransition,
    TelemetryObservation,
)
from memrelay_eval.domain.errors import ArtifactIntegrityError, IneligibleEvidenceError
from memrelay_eval.domain.ids import AttemptId, RunId
from memrelay_eval.domain.states import InclusionStatus, InternalRetrySubsystem
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
        self._internal_retries: list[InternalRetryRecord] = []
        self._retry_authorizations: list[RetryAuthorization] = []
        self._artifact_links: list[ArtifactLink] = []
        self._inclusions: list[InclusionDecision] = []
        self._internal_retry_lock = Lock()
        self._retry_authorization_lock = Lock()

    def append_transition(self, transition: RunTransition) -> None:
        history = self.history(transition.run_id)
        if not history and transition.previous.value != "planned":
            raise ValueError("authoritative run history must begin at planned")
        if history and history[-1].next_state != transition.previous:
            raise ValueError("transition does not continue this run's authoritative history")
        self._transitions.append(transition)

    def append_attempt_terminal(self, terminal: AttemptTerminal) -> None:
        if any(item.attempt_id == terminal.attempt_id for item in self._attempt_terminals):
            raise ValueError("an attempt terminal record already exists")
        self._attempt_terminals.append(terminal)

    def attempt_terminal_for(self, attempt_id: AttemptId) -> AttemptTerminal | None:
        return next(
            (item for item in self._attempt_terminals if item.attempt_id == attempt_id),
            None,
        )

    def append_internal_retry(self, record: InternalRetryRecord) -> None:
        self._internal_retries.append(record)

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

    def append_retry_authorization(self, authorization: RetryAuthorization) -> None:
        if self.retry_authorizations_for(authorization.run_id):
            raise IneligibleEvidenceError("retry_already_authorized_for_run")
        self._retry_authorizations.append(authorization)

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
