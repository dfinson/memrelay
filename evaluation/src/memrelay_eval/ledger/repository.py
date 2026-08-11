"""Single-control-process SQLite WAL implementation of the thin ledger port."""

from __future__ import annotations

import os
import re
import sqlite3
import threading
import weakref
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

if os.name == "nt":
    import msvcrt
else:
    import fcntl

from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.domain.entities import (
    ArtifactLink,
    ArtifactRef,
    Attempt,
    AttemptTerminal,
    CostLedgerLink,
    InclusionDecision,
    InternalRetryRecord,
    MonetaryViewLink,
    RetryAuthorization,
    RunTransition,
)
from memrelay_eval.domain.errors import (
    AttemptTerminalAlreadyRecordedError,
    LedgerDirectWriteError,
    LedgerIntentConflictError,
    LedgerOwnershipError,
)
from memrelay_eval.domain.ids import (
    ArtifactId,
    AssignmentId,
    AttemptId,
    CostEntryId,
    ExperimentId,
    IntentId,
    ProtocolId,
    RunId,
)
from memrelay_eval.domain.intents import (
    ArtifactLinkIntent,
    AttemptTerminalIntent,
    AuthorityConflictIntent,
    CostLedgerIntent,
    CreateAttemptIntent,
    CreateExperimentIntent,
    CreateRunIntent,
    InclusionDecisionIntent,
    IntentAck,
    IntentMetadata,
    IntentRejection,
    LedgerEvent,
    LedgerIntent,
    LedgerIntentType,
    MonetaryViewIntent,
    RejectedIntentEvidence,
    RetryLineageIntent,
    RunTransitionIntent,
    delivery_payload_digest,
)
from memrelay_eval.domain.policies import validate_run_transition
from memrelay_eval.domain.states import (
    AttemptTerminalKind,
    InclusionStatus,
    InternalRetrySubsystem,
    LedgerIntentKind,
    RunState,
)

from .schema import apply_migrations

_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_REASON_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_METADATA_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_PROHIBITED_METADATA_TERMS = frozenset(
    {
        "prompt",
        "patch",
        "trace",
        "grader",
        "inspect",
        "provider",
        "credential",
        "repository",
        "repo",
        "payload",
        "event",
        "body",
        "secret",
        "token",
        "password",
    }
)


class LedgerFaultInjectedError(RuntimeError):
    """A test-only fault occurred after durable commit and before the acknowledgement."""


class _RejectIntent(Exception):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code


class _ControlOwnershipLease:
    """Kernel-backed control ownership lease that is released when its process exits."""

    def __init__(self, database_path: Path) -> None:
        self._handle = database_path.with_name(f"{database_path.name}.owner").open("a+b")
        try:
            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write(b"\0")
                self._handle.flush()
            self._handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._handle.close()
            raise LedgerOwnershipError(
                "a control process already owns this ledger database"
            ) from error

    def release(self) -> None:
        if self._handle.closed:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()

    def close_in_forked_child(self) -> None:
        """Drop only the child descriptor; do not unlock the parent's shared lease."""

        if not self._handle.closed:
            self._handle.close()


class SqliteLedger:
    """Durable append-only ledger owned exclusively by the Inspect control process.

    The object opens one private SQLite connection.  Worker-facing code receives only
    typed intents and an intent sink; it never receives this repository or its path.
    """

    provenance = "durable_control_ledger"
    eligible_for_paid_or_study = True
    __ownership_lock = threading.RLock()
    __owned_paths: set[Path] = set()
    __instances: weakref.WeakSet = weakref.WeakSet()

    def __init__(
        self,
        database_path: Path | str,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> None:
        self.__owner_pid = os.getpid()
        self.__lock = threading.RLock()
        self.__fault_injector = fault_injector
        self.__closed = False
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.__registry_path = path.resolve()
        with self.__ownership_lock:
            if self.__registry_path in self.__owned_paths:
                raise LedgerOwnershipError(
                    "a control-owned connection already exists for this ledger database"
                )
            self.__owned_paths.add(self.__registry_path)
        try:
            self.__lease = _ControlOwnershipLease(self.__registry_path)
            self.__connection = sqlite3.connect(
                path,
                isolation_level=None,
                check_same_thread=False,
            )
            self.__connection.row_factory = sqlite3.Row
            self._configure()
            apply_migrations(self.__connection, applied_at=_utc_z(datetime.now(UTC)))
            self.__instances.add(self)
        except BaseException:
            with self.__ownership_lock:
                self.__owned_paths.discard(self.__registry_path)
            with suppress(AttributeError, OSError):
                self.__lease.release()
            with suppress(AttributeError, sqlite3.Error):
                self.__connection.close()
            self.__closed = True
            raise

    @classmethod
    def open_control(
        cls,
        database_path: Path | str,
        *,
        fault_injector: Callable[[str], None] | None = None,
    ) -> SqliteLedger:
        """Construct the repository at the control composition root only."""

        return cls(database_path, fault_injector=fault_injector)

    def _configure(self) -> None:
        self.__connection.execute("PRAGMA journal_mode = WAL")
        self.__connection.execute("PRAGMA foreign_keys = ON")
        self.__connection.execute("PRAGMA synchronous = FULL")
        self.__connection.execute("PRAGMA busy_timeout = 5000")
        self.__connection.execute("PRAGMA trusted_schema = OFF")

    def close(self) -> None:
        with self.__lock:
            if not self.__closed:
                try:
                    self.__connection.close()
                finally:
                    self.__closed = True
                    self.__lease.release()
                    with self.__ownership_lock:
                        self.__owned_paths.discard(self.__registry_path)
                        self.__instances.discard(self)

    @classmethod
    def _after_fork_in_child(cls) -> None:
        """Close inherited control-only state before forked worker code can execute."""

        for ledger in list(cls.__instances):
            ledger._close_in_forked_child()
        cls.__owned_paths.clear()
        cls.__instances = weakref.WeakSet()
        cls.__ownership_lock = threading.RLock()

    def _close_in_forked_child(self) -> None:
        self.__closed = True
        with suppress(AttributeError, sqlite3.Error):
            self.__connection.close()
        with suppress(AttributeError, OSError):
            self.__lease.close_in_forked_child()

    def __enter__(self) -> SqliteLedger:
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def submit_intent(self, intent: LedgerIntentType) -> IntentAck | IntentRejection:
        """Validate and append one intent atomically, or retain a thin rejection."""

        self._ensure_open()
        digest, preflight_rejection = delivery_payload_digest(intent)
        occurred_at = (
            _utc_z(intent.metadata.occurred_at)
            if _is_utc(intent.metadata.occurred_at)
            else _utc_z(datetime.now(UTC))
        )
        with self.__lock:
            self._fault("before_begin")
            self.__connection.execute("BEGIN IMMEDIATE")
            try:
                prior = self.__connection.execute(
                    """
                    SELECT payload_digest, kind, outcome, reason_code
                    FROM intent_receipts WHERE intent_id = ?
                    """,
                    (str(intent.intent_id),),
                ).fetchone()
                if prior is not None:
                    result = self._repeat_result(intent, digest, prior)
                    self.__connection.execute("COMMIT")
                    return result

                if preflight_rejection is not None:
                    self.__connection.execute("COMMIT")
                    result = self._persist_rejection(
                        intent,
                        digest,
                        preflight_rejection,
                        occurred_at,
                    )
                    return result

                self._validate_common(intent)
                self._insert_receipt(intent, digest, "accepted", None, occurred_at)
                self._apply(intent, digest, occurred_at)
                self._append_evidence_refs(intent)
                self._fault("after_append")
                self.__connection.execute(
                    """
                    INSERT INTO ledger_events (intent_id, payload_digest, kind, occurred_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(intent.intent_id), digest, intent.kind.value, occurred_at),
                )
                self._fault("before_commit")
                self.__connection.execute("COMMIT")
                result: IntentAck | IntentRejection = IntentAck(
                    intent.intent_id, digest, intent.kind
                )
            except _RejectIntent as rejected:
                with suppress(sqlite3.Error):
                    self.__connection.execute("ROLLBACK")
                result = self._persist_rejection(intent, digest, rejected.reason_code, occurred_at)
            except sqlite3.IntegrityError:
                with suppress(sqlite3.Error):
                    self.__connection.execute("ROLLBACK")
                result = self._persist_rejection(intent, digest, "integrity_violation", occurred_at)
            except BaseException:
                with suppress(sqlite3.Error):
                    self.__connection.execute("ROLLBACK")
                raise
        self._fault("after_commit_before_ack")
        return result

    def reject_intent(self, intent: LedgerIntentType, reason_code: str) -> IntentRejection:
        """Retain a control-bound rejection without permitting the requested append."""

        self._ensure_open()
        if not _safe_code(reason_code):
            raise ValueError("rejection reason codes must use the stable safe vocabulary")
        digest, preflight_rejection = delivery_payload_digest(intent)
        rejection_reason = preflight_rejection or reason_code
        occurred_at = (
            _utc_z(intent.metadata.occurred_at)
            if _is_utc(intent.metadata.occurred_at)
            else _utc_z(datetime.now(UTC))
        )
        with self.__lock:
            self.__connection.execute("BEGIN IMMEDIATE")
            try:
                prior = self.__connection.execute(
                    """
                    SELECT payload_digest, kind, outcome, reason_code
                    FROM intent_receipts WHERE intent_id = ?
                    """,
                    (str(intent.intent_id),),
                ).fetchone()
                if prior is not None:
                    result = self._repeat_result(intent, digest, prior)
                    self.__connection.execute("COMMIT")
                    if isinstance(result, IntentRejection):
                        return result
                    raise LedgerIntentConflictError(
                        "control rejection conflicts with an already accepted intent"
                    )
                self.__connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self.__connection.execute("ROLLBACK")
                raise
            return self._persist_rejection(intent, digest, rejection_reason, occurred_at)

    def cost_ledger_entries_for(self, attempt_id: AttemptId) -> tuple[CostLedgerLink, ...]:
        """Return immutable artifact links grouped only by their recorded logical ledger."""

        self._ensure_open()
        with self.__lock:
            rows = self.__connection.execute(
                """
                SELECT cost_entry_id, run_id, attempt_id, logical_ledger,
                       artifact_id, artifact_sha256, size_bytes
                FROM cost_ledger_entries
                WHERE attempt_id = ?
                ORDER BY sequence
                """,
                (str(attempt_id),),
            ).fetchall()
        return tuple(
            CostLedgerLink(
                CostEntryId(row["cost_entry_id"]),
                RunId(row["run_id"]),
                AttemptId(row["attempt_id"]),
                row["logical_ledger"],
                ArtifactRef(
                    ArtifactId(row["artifact_id"]),
                    row["artifact_sha256"],
                    row["size_bytes"],
                ),
            )
            for row in rows
        )

    def monetary_views_for(self, attempt_id: AttemptId) -> tuple[MonetaryViewLink, ...]:
        """Return append-only repricing views without choosing a mutable latest revision."""

        self._ensure_open()
        with self.__lock:
            rows = self.__connection.execute(
                """
                SELECT monetary_view_id, run_id, attempt_id, category,
                       artifact_id, artifact_sha256, size_bytes,
                       price_table_artifact_id, price_table_sha256, price_table_size_bytes
                FROM monetary_views
                WHERE attempt_id = ?
                ORDER BY sequence
                """,
                (str(attempt_id),),
            ).fetchall()
        return tuple(
            MonetaryViewLink(
                row["monetary_view_id"],
                RunId(row["run_id"]),
                AttemptId(row["attempt_id"]),
                row["category"],
                ArtifactRef(
                    ArtifactId(row["artifact_id"]),
                    row["artifact_sha256"],
                    row["size_bytes"],
                ),
                ArtifactRef(
                    ArtifactId(row["price_table_artifact_id"]),
                    row["price_table_sha256"],
                    row["price_table_size_bytes"],
                ),
            )
            for row in rows
        )

    def _persist_rejection(
        self, intent: LedgerIntent, digest: str, reason_code: str, occurred_at: str
    ) -> IntentRejection:
        self.__connection.execute("BEGIN IMMEDIATE")
        try:
            self._insert_receipt(intent, digest, "rejected", reason_code, occurred_at)
            self.__connection.execute(
                """
                INSERT INTO rejected_intents
                    (intent_id, payload_digest, kind, reason_code, occurred_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(intent.intent_id),
                    digest,
                    intent.kind.value,
                    reason_code,
                    occurred_at,
                ),
            )
            self._fault("before_commit")
            self.__connection.execute("COMMIT")
        except BaseException:
            with suppress(sqlite3.Error):
                self.__connection.execute("ROLLBACK")
            raise
        return IntentRejection(intent.intent_id, digest, intent.kind, reason_code)

    def _repeat_result(
        self, intent: LedgerIntent, digest: str, prior: sqlite3.Row
    ) -> IntentAck | IntentRejection:
        if prior["payload_digest"] != digest:
            raise LedgerIntentConflictError(
                "intent ID was reused with a different canonical payload digest"
            )
        kind = LedgerIntentKind(prior["kind"])
        if prior["outcome"] == "accepted":
            return IntentAck(intent.intent_id, digest, kind, idempotent=True)
        return IntentRejection(
            intent.intent_id,
            digest,
            kind,
            cast(str, prior["reason_code"]),
            idempotent=True,
        )

    def _insert_receipt(
        self,
        intent: LedgerIntent,
        digest: str,
        outcome: Literal["accepted", "rejected"],
        reason_code: str | None,
        occurred_at: str,
    ) -> None:
        self.__connection.execute(
            """
            INSERT INTO intent_receipts
                (intent_id, payload_digest, kind, outcome, reason_code, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(intent.intent_id), digest, intent.kind.value, outcome, reason_code, occurred_at),
        )

    def _append_evidence_refs(self, intent: LedgerIntent) -> None:
        for ordinal, reference in enumerate(intent.metadata.evidence_refs):
            self.__connection.execute(
                """
                INSERT INTO intent_evidence_refs
                    (intent_id, ordinal, artifact_id, artifact_sha256, size_bytes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(intent.intent_id),
                    ordinal,
                    str(reference.artifact_id),
                    reference.sha256,
                    reference.size_bytes,
                ),
            )

    def _apply(self, intent: LedgerIntentType, digest: str, occurred_at: str) -> None:
        if isinstance(intent, CreateExperimentIntent):
            self.__connection.execute(
                """
                INSERT INTO experiments (experiment_id, protocol_id, intent_id, occurred_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    str(intent.experiment_id),
                    str(intent.protocol_id),
                    str(intent.intent_id),
                    occurred_at,
                ),
            )
            return
        if isinstance(intent, CreateRunIntent):
            self._require_experiment(intent.experiment_id)
            self.__connection.execute(
                """
                INSERT INTO runs (run_id, experiment_id, assignment_id, intent_id, occurred_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(intent.run_id),
                    str(intent.experiment_id),
                    str(intent.assignment_id),
                    str(intent.intent_id),
                    occurred_at,
                ),
            )
            return
        if isinstance(intent, CreateAttemptIntent):
            self._require_run(intent.run_id)
            if intent.metadata.source_attempt_id is not None:
                raise _RejectIntent("initial_attempt_must_not_have_predecessor")
            existing = self.__connection.execute(
                "SELECT 1 FROM attempts WHERE run_id = ? LIMIT 1", (str(intent.run_id),)
            ).fetchone()
            if existing is not None:
                raise _RejectIntent("unlinked_attempt_creation")
            self.__connection.execute(
                """
                INSERT INTO attempts (attempt_id, run_id, intent_id, occurred_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(intent.attempt_id), str(intent.run_id), str(intent.intent_id), occurred_at),
            )
            return
        if isinstance(intent, RunTransitionIntent):
            self._append_transition_intent(intent, digest, occurred_at)
            return
        if isinstance(intent, AttemptTerminalIntent):
            self._append_terminal_intent(intent, occurred_at)
            return
        if isinstance(intent, ArtifactLinkIntent):
            self._append_artifact_link_intent(intent, occurred_at)
            return
        if isinstance(intent, RetryLineageIntent):
            self._append_retry_intent(intent, occurred_at)
            return
        if isinstance(intent, InclusionDecisionIntent):
            self._append_inclusion_intent(intent, occurred_at)
            return
        if isinstance(intent, AuthorityConflictIntent):
            self._require_attempt_for_run(intent.attempt_id, intent.run_id)
            self.__connection.execute(
                """
                INSERT INTO authority_conflicts
                    (intent_id, run_id, attempt_id, conflict_fields, occurred_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(intent.intent_id),
                    str(intent.run_id),
                    str(intent.attempt_id),
                    ",".join(intent.conflict_fields),
                    occurred_at,
                ),
            )
            return
        if isinstance(intent, CostLedgerIntent):
            self._require_attempt_for_run(intent.attempt_id, intent.run_id)
            self._require_source_attempt_for_run(intent.metadata.source_attempt_id, intent.run_id)
            if intent.metadata.source_attempt_id != intent.attempt_id:
                raise _RejectIntent("cost_attempt_source_mismatch")
            if intent.artifact_ref not in intent.metadata.evidence_refs:
                raise _RejectIntent("cost_artifact_not_evidence")
            if intent.source_evidence_ref not in intent.metadata.evidence_refs:
                raise _RejectIntent("cost_source_not_evidence")
            self.__connection.execute(
                """
                INSERT INTO cost_ledger_entries (
                    intent_id, cost_entry_id, run_id, attempt_id, logical_ledger,
                    artifact_id, artifact_sha256, size_bytes, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(intent.intent_id),
                    str(intent.cost_entry_id),
                    str(intent.run_id),
                    str(intent.attempt_id),
                    intent.logical_ledger,
                    str(intent.artifact_ref.artifact_id),
                    intent.artifact_ref.sha256,
                    intent.artifact_ref.size_bytes,
                    occurred_at,
                ),
            )
            return
        if isinstance(intent, MonetaryViewIntent):
            self._require_attempt_for_run(intent.attempt_id, intent.run_id)
            self._require_source_attempt_for_run(intent.metadata.source_attempt_id, intent.run_id)
            if intent.metadata.source_attempt_id != intent.attempt_id:
                raise _RejectIntent("monetary_view_attempt_source_mismatch")
            if any(
                reference not in intent.metadata.evidence_refs
                for reference in (
                    intent.artifact_ref,
                    intent.price_table_ref,
                    intent.quantity_artifact_ref,
                )
            ):
                raise _RejectIntent("monetary_view_artifact_not_evidence")
            self.__connection.execute(
                """
                INSERT INTO monetary_views (
                    intent_id, monetary_view_id, run_id, attempt_id, category,
                    artifact_id, artifact_sha256, size_bytes,
                    price_table_artifact_id, price_table_sha256, price_table_size_bytes,
                    quantity_artifact_id, quantity_sha256, quantity_size_bytes, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(intent.intent_id),
                    str(intent.monetary_view_id),
                    str(intent.run_id),
                    str(intent.attempt_id),
                    intent.category,
                    str(intent.artifact_ref.artifact_id),
                    intent.artifact_ref.sha256,
                    intent.artifact_ref.size_bytes,
                    str(intent.price_table_ref.artifact_id),
                    intent.price_table_ref.sha256,
                    intent.price_table_ref.size_bytes,
                    str(intent.quantity_artifact_ref.artifact_id),
                    intent.quantity_artifact_ref.sha256,
                    intent.quantity_artifact_ref.size_bytes,
                    occurred_at,
                ),
            )
            return
        raise _RejectIntent("unknown_intent_kind")

    def _append_transition_intent(
        self, intent: RunTransitionIntent, digest: str, occurred_at: str
    ) -> None:
        self._require_run(intent.run_id)
        if not isinstance(intent.previous, RunState) or not isinstance(intent.next_state, RunState):
            raise _RejectIntent("invalid_run_state")
        try:
            validate_run_transition(intent.previous, intent.next_state)
        except ValueError as error:
            raise _RejectIntent("invalid_lifecycle_transition") from error
        state, prior_digest = self._run_state_and_digest(intent.run_id)
        if state is not intent.previous:
            raise _RejectIntent("stale_prior_state")
        if intent.metadata.expected_prior_state is not intent.previous:
            raise _RejectIntent("missing_or_stale_expected_state")
        if intent.metadata.expected_prior_digest != prior_digest:
            raise _RejectIntent("stale_prior_digest")
        self._require_source_attempt_for_run(intent.metadata.source_attempt_id, intent.run_id)
        if intent.next_state in (RunState.INCLUDED, RunState.EXCLUDED):
            inclusion = self.__connection.execute(
                "SELECT status FROM inclusion_decisions WHERE run_id = ?",
                (str(intent.run_id),),
            ).fetchone()
            if inclusion is None:
                raise _RejectIntent("missing_inclusion_decision")
            if inclusion["status"] != intent.next_state.value:
                raise _RejectIntent("inclusion_transition_mismatch")
        self.__connection.execute(
            """
            INSERT INTO run_transitions (
                intent_id, run_id, previous_state, next_state, occurred_at, monotonic_ns,
                source_attempt_id, expected_prior_digest, reason_code, event_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(intent.intent_id),
                str(intent.run_id),
                intent.previous.value,
                intent.next_state.value,
                occurred_at,
                intent.metadata.monotonic_ns,
                _optional_id(intent.metadata.source_attempt_id),
                intent.metadata.expected_prior_digest,
                intent.metadata.reason_code,
                digest,
            ),
        )

    def _append_terminal_intent(self, intent: AttemptTerminalIntent, occurred_at: str) -> None:
        self._require_attempt_for_run(intent.attempt_id, intent.run_id)
        if not isinstance(intent.classification, AttemptTerminalKind):
            raise _RejectIntent("invalid_attempt_terminal")
        self._require_source_attempt_for_run(intent.metadata.source_attempt_id, intent.run_id)
        if intent.classification is AttemptTerminalKind.INFRASTRUCTURE_FAILED_PRE_EXPOSURE:
            if (
                intent.metadata.source_attempt_id != intent.attempt_id
                or intent.pre_exposure_evidence is None
                or intent.pre_exposure_evidence not in intent.metadata.evidence_refs
            ):
                raise _RejectIntent("unverified_pre_exposure_failure")
        elif intent.pre_exposure_evidence is not None:
            raise _RejectIntent("unexpected_pre_exposure_evidence")
        existing = self.__connection.execute(
            "SELECT 1 FROM attempt_terminals WHERE attempt_id = ?", (str(intent.attempt_id),)
        ).fetchone()
        if existing is not None:
            raise _RejectIntent("attempt_already_terminal")
        self._insert_terminal_record(
            intent.attempt_id,
            str(intent.intent_id),
            intent.run_id,
            intent.classification,
            occurred_at,
            intent.metadata.monotonic_ns,
            intent.metadata.source_attempt_id,
            intent.metadata.reason_code,
            intent.metadata.evidence_refs,
        )

    def _insert_terminal_record(
        self,
        attempt_id: AttemptId,
        receipt_id: str,
        run_id: RunId,
        classification: AttemptTerminalKind,
        occurred_at: str,
        monotonic_ns: int | None,
        source_attempt_id: AttemptId | None,
        reason_code: str,
        evidence_refs: Sequence[ArtifactRef],
    ) -> None:
        self.__connection.execute(
            """
            INSERT INTO attempt_terminals (
                attempt_id, intent_id, run_id, classification, occurred_at, monotonic_ns,
                source_attempt_id, reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(attempt_id),
                receipt_id,
                str(run_id),
                classification.value,
                occurred_at,
                monotonic_ns,
                _optional_id(source_attempt_id),
                reason_code,
            ),
        )
        self._append_terminal_evidence_refs(attempt_id, evidence_refs)

    def _append_terminal_evidence_refs(
        self, attempt_id: AttemptId, references: Sequence[ArtifactRef]
    ) -> None:
        for ordinal, reference in enumerate(references):
            self.__connection.execute(
                """
                INSERT INTO attempt_terminal_evidence_refs
                    (attempt_id, ordinal, artifact_id, artifact_sha256, size_bytes)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(attempt_id),
                    ordinal,
                    str(reference.artifact_id),
                    reference.sha256,
                    reference.size_bytes,
                ),
            )

    def _append_artifact_link_intent(self, intent: ArtifactLinkIntent, occurred_at: str) -> None:
        link = intent.link
        if not isinstance(link, ArtifactLink) or not _safe_code(link.purpose):
            raise _RejectIntent("unsafe_artifact_link")
        if link.experiment_id is not None:
            self._require_experiment(link.experiment_id)
        if link.run_id is not None:
            self._require_run(link.run_id)
            self._require_source_attempt_for_run(intent.metadata.source_attempt_id, link.run_id)
            if link.experiment_id is not None:
                run = self.__connection.execute(
                    "SELECT experiment_id FROM runs WHERE run_id = ?",
                    (str(link.run_id),),
                ).fetchone()
                if run["experiment_id"] != str(link.experiment_id):
                    raise _RejectIntent("artifact_experiment_run_mismatch")
        if link.attempt_id is not None:
            if link.run_id is None:
                raise _RejectIntent("invalid_artifact_owner")
            self._require_attempt_for_run(link.attempt_id, link.run_id)
        self.__connection.execute(
            """
            INSERT INTO artifact_links (
                intent_id, artifact_id, artifact_sha256, size_bytes, purpose, experiment_id,
                run_id, attempt_id, occurred_at, reason_code
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(intent.intent_id),
                str(link.artifact_ref.artifact_id),
                link.artifact_ref.sha256,
                link.artifact_ref.size_bytes,
                link.purpose,
                _optional_id(link.experiment_id),
                _optional_id(link.run_id),
                _optional_id(link.attempt_id),
                occurred_at,
                intent.metadata.reason_code,
            ),
        )

    def _append_retry_intent(self, intent: RetryLineageIntent, occurred_at: str) -> None:
        del intent, occurred_at
        raise _RejectIntent("retry_authorization_control_only")

    def _append_inclusion_intent(self, intent: InclusionDecisionIntent, occurred_at: str) -> None:
        decision = intent.decision
        if not isinstance(decision, InclusionDecision):
            raise _RejectIntent("invalid_inclusion_decision")
        if (
            decision.reason != intent.metadata.reason_code
            or not _safe_code(decision.reason)
            or not isinstance(decision.status, InclusionStatus)
            or not _DIGEST.fullmatch(decision.reconciliation_sha256)
        ):
            raise _RejectIntent("unsafe_inclusion_decision")
        if _utc_z(decision.occurred_at) != occurred_at:
            raise _RejectIntent("inclusion_timestamp_mismatch")
        self._require_run(decision.run_id)
        state, prior_digest = self._run_state_and_digest(decision.run_id)
        if state is not RunState.RECONCILED:
            raise _RejectIntent("inclusion_before_reconciliation")
        if intent.metadata.expected_prior_state is not RunState.RECONCILED:
            raise _RejectIntent("missing_or_stale_expected_state")
        if intent.metadata.expected_prior_digest != prior_digest:
            raise _RejectIntent("stale_prior_digest")
        self.__connection.execute(
            """
            INSERT INTO inclusion_decisions (
                inclusion_id, intent_id, run_id, status, reason_code, reconciliation_sha256,
                occurred_at, monotonic_ns
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(decision.id),
                str(intent.intent_id),
                str(decision.run_id),
                decision.status.value,
                decision.reason,
                decision.reconciliation_sha256,
                occurred_at,
                intent.metadata.monotonic_ns,
            ),
        )

    def _validate_common(self, intent: LedgerIntent) -> None:
        if not isinstance(intent.metadata, IntentMetadata) or not isinstance(
            intent.metadata.intent_id, IntentId
        ):
            raise _RejectIntent("invalid_intent_metadata")
        metadata = intent.metadata
        if not _is_utc(metadata.occurred_at):
            raise _RejectIntent("non_utc_timestamp")
        if metadata.monotonic_ns is not None and (
            isinstance(metadata.monotonic_ns, bool)
            or not isinstance(metadata.monotonic_ns, int)
            or metadata.monotonic_ns < 0
        ):
            raise _RejectIntent("invalid_monotonic_time")
        if metadata.expected_prior_digest is not None and not _DIGEST.fullmatch(
            metadata.expected_prior_digest
        ):
            raise _RejectIntent("invalid_expected_digest")
        if metadata.expected_prior_state is not None and not isinstance(
            metadata.expected_prior_state, RunState
        ):
            raise _RejectIntent("invalid_expected_state")
        if not _safe_code(metadata.reason_code):
            raise _RejectIntent("invalid_reason_code")
        if len(metadata.evidence_refs) > 16 or any(
            not isinstance(reference, ArtifactRef) for reference in metadata.evidence_refs
        ):
            raise _RejectIntent("invalid_evidence_refs")
        if (
            len(metadata.safe_metadata) > 16
            or not metadata.has_only_small_scalars()
            or any(
                not isinstance(key, str)
                or not _METADATA_KEY.fullmatch(key)
                or any(term in key for term in _PROHIBITED_METADATA_TERMS)
                for key in metadata.safe_metadata
            )
        ):
            raise _RejectIntent("thin_ledger_violation")
        if metadata.source_attempt_id is not None and not isinstance(
            metadata.source_attempt_id, AttemptId
        ):
            raise _RejectIntent("invalid_source_attempt")
        self._validate_intent_identities(intent)

    @staticmethod
    def _validate_intent_identities(intent: LedgerIntent) -> None:
        if isinstance(intent, CreateExperimentIntent):
            valid = isinstance(intent.experiment_id, ExperimentId) and isinstance(
                intent.protocol_id, ProtocolId
            )
        elif isinstance(intent, CreateRunIntent):
            valid = (
                isinstance(intent.run_id, RunId)
                and isinstance(intent.experiment_id, ExperimentId)
                and isinstance(intent.assignment_id, AssignmentId)
            )
        elif isinstance(intent, CreateAttemptIntent):
            valid = isinstance(intent.attempt_id, AttemptId) and isinstance(intent.run_id, RunId)
        elif isinstance(intent, RunTransitionIntent):
            valid = isinstance(intent.run_id, RunId)
        elif isinstance(intent, AttemptTerminalIntent):
            valid = (
                isinstance(intent.attempt_id, AttemptId)
                and isinstance(intent.run_id, RunId)
                and (
                    intent.pre_exposure_evidence is None
                    or isinstance(intent.pre_exposure_evidence, ArtifactRef)
                )
            )
        elif isinstance(intent, ArtifactLinkIntent):
            valid = isinstance(intent.link, ArtifactLink) and isinstance(
                intent.link.artifact_ref, ArtifactRef
            )
        elif isinstance(intent, RetryLineageIntent):
            valid = (
                isinstance(intent.run_id, RunId)
                and isinstance(intent.previous_attempt_id, AttemptId)
                and isinstance(intent.retry_attempt_id, AttemptId)
            )
        elif isinstance(intent, InclusionDecisionIntent):
            valid = isinstance(intent.decision, InclusionDecision)
        elif isinstance(intent, AuthorityConflictIntent):
            valid = isinstance(intent.run_id, RunId) and isinstance(intent.attempt_id, AttemptId)
        elif isinstance(intent, CostLedgerIntent):
            valid = (
                isinstance(intent.cost_entry_id, CostEntryId)
                and isinstance(intent.run_id, RunId)
                and isinstance(intent.attempt_id, AttemptId)
                and intent.logical_ledger
                in {"copilot_subscription", "framework_openai", "local_resources"}
                and isinstance(intent.artifact_ref, ArtifactRef)
            )
        else:
            valid = False
        if not valid:
            raise _RejectIntent("invalid_opaque_identity")

    def _require_experiment(self, experiment_id: ExperimentId) -> None:
        if (
            self.__connection.execute(
                "SELECT 1 FROM experiments WHERE experiment_id = ?", (str(experiment_id),)
            ).fetchone()
            is None
        ):
            raise _RejectIntent("unknown_experiment")

    def _require_run(self, run_id: RunId) -> None:
        if (
            self.__connection.execute(
                "SELECT 1 FROM runs WHERE run_id = ?", (str(run_id),)
            ).fetchone()
            is None
        ):
            raise _RejectIntent("unknown_run")

    def _require_attempt_for_run(self, attempt_id: AttemptId, run_id: RunId) -> None:
        row = self.__connection.execute(
            "SELECT run_id FROM attempts WHERE attempt_id = ?", (str(attempt_id),)
        ).fetchone()
        if row is None:
            raise _RejectIntent("unknown_attempt")
        if row["run_id"] != str(run_id):
            raise _RejectIntent("attempt_run_mismatch")

    def _require_source_attempt_for_run(
        self, source_attempt_id: AttemptId | None, run_id: RunId
    ) -> None:
        if source_attempt_id is not None:
            self._require_attempt_for_run(source_attempt_id, run_id)

    def _run_state_and_digest(self, run_id: RunId) -> tuple[RunState, str | None]:
        row = self.__connection.execute(
            """
            SELECT next_state, event_digest FROM run_transitions
            WHERE run_id = ? ORDER BY sequence DESC LIMIT 1
            """,
            (str(run_id),),
        ).fetchone()
        if row is None:
            return RunState.PLANNED, None
        return RunState(row["next_state"]), row["event_digest"]

    def _fault(self, boundary: str) -> None:
        if self.__fault_injector is not None:
            self.__fault_injector(boundary)

    def _ensure_open(self) -> None:
        if self.__closed:
            raise RuntimeError("ledger is closed")
        if os.getpid() != self.__owner_pid:
            raise RuntimeError("ledger connection may only be used by its owning control process")

    def append_transition(self, transition: RunTransition) -> None:
        _, digest = self._run_state_and_digest(transition.run_id)
        result = self.submit_intent(
            RunTransitionIntent(
                IntentMetadata(
                    IntentId.new(),
                    transition.occurred_at,
                    expected_prior_state=transition.previous,
                    expected_prior_digest=digest,
                    reason_code="legacy_append",
                ),
                transition.run_id,
                transition.previous,
                transition.next_state,
            )
        )
        self._raise_direct_rejection(result)

    def append_attempt_terminal(self, terminal: AttemptTerminal) -> None:
        """Atomically retain one immutable terminal record for a known attempt."""

        self._ensure_open()
        occurred_at = _utc_z(terminal.occurred_at)
        with self.__lock:
            self.__connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_attempt_for_run(terminal.attempt_id, terminal.run_id)
                if self.attempt_terminal_for(terminal.attempt_id) is not None:
                    raise AttemptTerminalAlreadyRecordedError(
                        AttemptTerminalAlreadyRecordedError.code
                    )
                receipt_id = f"terminal:{terminal.attempt_id}"
                digest = _direct_record_digest(
                    "attempt_terminal",
                    {
                        "attempt_id": str(terminal.attempt_id),
                        "run_id": str(terminal.run_id),
                        "classification": terminal.classification.value,
                        "occurred_at": occurred_at,
                        "reason": terminal.reason,
                    },
                )
                self.__connection.execute(
                    """
                    INSERT INTO intent_receipts
                        (intent_id, payload_digest, kind, outcome, reason_code, occurred_at)
                    VALUES (?, ?, ?, 'accepted', NULL, ?)
                    """,
                    (receipt_id, digest, LedgerIntentKind.ATTEMPT_TERMINAL.value, occurred_at),
                )
                self._insert_terminal_record(
                    terminal.attempt_id,
                    receipt_id,
                    terminal.run_id,
                    terminal.classification,
                    occurred_at,
                    None,
                    terminal.attempt_id,
                    terminal.reason,
                    terminal.evidence_refs,
                )
                self.__connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self.__connection.execute("ROLLBACK")
                raise

    def attempt_terminal_for(self, attempt_id: AttemptId) -> AttemptTerminal | None:
        self._ensure_open()
        with self.__lock:
            return self._attempt_terminal_for_locked(attempt_id)

    def claim_attempt_execution(self, attempt_id: AttemptId, run_id: RunId) -> bool:
        """Atomically reserve a nonterminal attempt before its scheduler can run."""

        self._ensure_open()
        with self.__lock:
            self.__connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_attempt_for_run(attempt_id, run_id)
                if self._attempt_terminal_for_locked(attempt_id) is not None:
                    self.__connection.execute("COMMIT")
                    return False
                existing = self.__connection.execute(
                    "SELECT 1 FROM attempt_execution_claims WHERE attempt_id = ?",
                    (str(attempt_id),),
                ).fetchone()
                if existing is not None:
                    self.__connection.execute("COMMIT")
                    return False
                self.__connection.execute(
                    """
                    INSERT INTO attempt_execution_claims (attempt_id, run_id, claimed_at)
                    VALUES (?, ?, ?)
                    """,
                    (str(attempt_id), str(run_id), _utc_z(datetime.now(UTC))),
                )
                self.__connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self.__connection.execute("ROLLBACK")
                raise
        return True

    def _attempt_terminal_for_locked(self, attempt_id: AttemptId) -> AttemptTerminal | None:
        row = self.__connection.execute(
            """
            SELECT run_id, classification, occurred_at, reason_code
            FROM attempt_terminals WHERE attempt_id = ?
            """,
            (str(attempt_id),),
        ).fetchone()
        if row is None:
            return None
        evidence_rows = self.__connection.execute(
            """
            SELECT artifact_id, artifact_sha256, size_bytes
            FROM attempt_terminal_evidence_refs
            WHERE attempt_id = ? ORDER BY ordinal
            """,
            (str(attempt_id),),
        ).fetchall()
        return AttemptTerminal(
            attempt_id,
            RunId(row["run_id"]),
            AttemptTerminalKind(row["classification"]),
            _parse_utc(row["occurred_at"]),
            row["reason_code"],
            tuple(
                ArtifactRef(
                    ArtifactId(item["artifact_id"]),
                    item["artifact_sha256"],
                    item["size_bytes"],
                )
                for item in evidence_rows
            ),
        )

    def reserve_internal_retry(
        self,
        attempt_id: AttemptId,
        subsystem: InternalRetrySubsystem,
        maximum_retries: int,
    ) -> InternalRetryRecord | None:
        """Allocate a retry ordinal exactly once within one attempt/subsystem budget."""

        self._ensure_open()
        if maximum_retries < 0:
            raise ValueError("maximum_retries must not be negative")
        if not isinstance(subsystem, InternalRetrySubsystem):
            raise ValueError("subsystem must use the frozen internal retry vocabulary")
        with self.__lock:
            self.__connection.execute("BEGIN IMMEDIATE")
            try:
                if (
                    self.__connection.execute(
                        "SELECT 1 FROM attempts WHERE attempt_id = ?", (str(attempt_id),)
                    ).fetchone()
                    is None
                ):
                    raise LedgerDirectWriteError("unknown_attempt")
                row = self.__connection.execute(
                    """
                    SELECT COALESCE(MAX(retry_number), 0) AS latest
                    FROM internal_retries WHERE attempt_id = ? AND subsystem = ?
                    """,
                    (str(attempt_id), subsystem.value),
                ).fetchone()
                retry_number = int(row["latest"]) + 1
                if retry_number > maximum_retries:
                    self.__connection.execute("COMMIT")
                    return None
                self.__connection.execute(
                    """
                    INSERT INTO internal_retries
                        (attempt_id, subsystem, retry_number, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (str(attempt_id), subsystem.value, retry_number, _utc_z(datetime.now(UTC))),
                )
                self._fault("before_internal_retry_commit")
                self.__connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self.__connection.execute("ROLLBACK")
                raise
        return InternalRetryRecord(attempt_id, subsystem, retry_number)

    def internal_retries_for(
        self, attempt_id: AttemptId, subsystem: InternalRetrySubsystem
    ) -> tuple[InternalRetryRecord, ...]:
        self._ensure_open()
        with self.__lock:
            rows = self.__connection.execute(
                """
                SELECT retry_number FROM internal_retries
                WHERE attempt_id = ? AND subsystem = ? ORDER BY retry_number
                """,
                (str(attempt_id), subsystem.value),
            ).fetchall()
        return tuple(
            InternalRetryRecord(attempt_id, subsystem, row["retry_number"]) for row in rows
        )

    def append_retry_authorization_once(self, authorization: RetryAuthorization) -> bool:
        """Atomically make the sole retry attempt for a run durable."""

        self._ensure_open()
        with self.__lock:
            self.__connection.execute("BEGIN IMMEDIATE")
            try:
                if (
                    self.__connection.execute(
                        "SELECT 1 FROM retry_authorizations WHERE run_id = ?",
                        (str(authorization.run_id),),
                    ).fetchone()
                    is not None
                ):
                    self.__connection.execute("COMMIT")
                    return False
                run = self.__connection.execute(
                    "SELECT assignment_id FROM runs WHERE run_id = ?", (str(authorization.run_id),)
                ).fetchone()
                if run is None:
                    raise LedgerDirectWriteError("unknown_run")
                if run["assignment_id"] != str(authorization.assignment_id):
                    raise LedgerDirectWriteError("retry_assignment_mismatch")
                self._require_attempt_for_run(authorization.parent_attempt_id, authorization.run_id)
                if (
                    self._attempt_terminal_for_locked(authorization.parent_attempt_id)
                    != authorization.parent_terminal
                ):
                    raise LedgerDirectWriteError("retry_terminal_not_authoritative")
                if (
                    self.__connection.execute(
                        "SELECT 1 FROM attempts WHERE attempt_id = ?",
                        (str(authorization.attempt.id),),
                    ).fetchone()
                    is not None
                ):
                    raise LedgerDirectWriteError("retry_attempt_already_exists")
                occurred_at = _utc_z(datetime.now(UTC))
                receipt_id = f"retry:{authorization.attempt.id}"
                digest = _direct_record_digest(
                    "retry_authorization",
                    {
                        "run_id": str(authorization.run_id),
                        "assignment_id": str(authorization.assignment_id),
                        "parent_attempt_id": str(authorization.parent_attempt_id),
                        "retry_attempt_id": str(authorization.attempt.id),
                    },
                )
                self.__connection.execute(
                    """
                    INSERT INTO intent_receipts
                        (intent_id, payload_digest, kind, outcome, reason_code, occurred_at)
                    VALUES (?, ?, ?, 'accepted', NULL, ?)
                    """,
                    (receipt_id, digest, LedgerIntentKind.RETRY_LINEAGE.value, occurred_at),
                )
                self.__connection.execute(
                    """
                    INSERT INTO attempts (attempt_id, run_id, intent_id, occurred_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(authorization.attempt.id),
                        str(authorization.run_id),
                        receipt_id,
                        occurred_at,
                    ),
                )
                self.__connection.execute(
                    """
                    INSERT INTO retry_authorizations (
                        run_id, assignment_id, parent_attempt_id, retry_attempt_id, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(authorization.run_id),
                        str(authorization.assignment_id),
                        str(authorization.parent_attempt_id),
                        str(authorization.attempt.id),
                        occurred_at,
                    ),
                )
                self.__connection.execute(
                    """
                    INSERT INTO retry_links (
                        previous_attempt_id, retry_attempt_id, intent_id, run_id, occurred_at,
                        monotonic_ns, reason_code
                    ) VALUES (?, ?, ?, ?, ?, NULL, 'retry_authorized')
                    """,
                    (
                        str(authorization.parent_attempt_id),
                        str(authorization.attempt.id),
                        receipt_id,
                        str(authorization.run_id),
                        occurred_at,
                    ),
                )
                self._append_retry_authorization_evidence(
                    authorization.run_id, "exposure", authorization.exposure_evidence_refs
                )
                self._append_retry_authorization_evidence(
                    authorization.run_id, "isolation", authorization.isolation_evidence_refs
                )
                self._fault("before_retry_authorization_commit")
                self.__connection.execute("COMMIT")
            except BaseException:
                with suppress(sqlite3.Error):
                    self.__connection.execute("ROLLBACK")
                raise
        return True

    def _append_retry_authorization_evidence(
        self,
        run_id: RunId,
        evidence_scope: Literal["exposure", "isolation"],
        references: Sequence[ArtifactRef],
    ) -> None:
        for ordinal, reference in enumerate(references):
            self.__connection.execute(
                """
                INSERT INTO retry_authorization_evidence_refs (
                    run_id, evidence_scope, ordinal, artifact_id, artifact_sha256, size_bytes
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(run_id),
                    evidence_scope,
                    ordinal,
                    str(reference.artifact_id),
                    reference.sha256,
                    reference.size_bytes,
                ),
            )

    def append_artifact_link(self, link: ArtifactLink) -> None:
        result = self.submit_intent(
            ArtifactLinkIntent(
                IntentMetadata(IntentId.new(), datetime.now(UTC), reason_code="legacy_append"),
                link,
            )
        )
        self._raise_direct_rejection(result)

    def append_inclusion(self, decision: InclusionDecision) -> None:
        state, digest = self._run_state_and_digest(decision.run_id)
        result = self.submit_intent(
            InclusionDecisionIntent(
                IntentMetadata(
                    IntentId.new(),
                    decision.occurred_at,
                    expected_prior_state=state,
                    expected_prior_digest=digest,
                    reason_code=decision.reason,
                ),
                decision,
            )
        )
        self._raise_direct_rejection(result)

    @staticmethod
    def _raise_direct_rejection(result: IntentAck | IntentRejection) -> None:
        if isinstance(result, IntentRejection):
            raise LedgerDirectWriteError(result.reason_code)

    def history(self, run_id: RunId) -> Sequence[RunTransition]:
        self._ensure_open()
        with self.__lock:
            rows = self.__connection.execute(
                """
                SELECT previous_state, next_state, occurred_at
                FROM run_transitions WHERE run_id = ? ORDER BY sequence
                """,
                (str(run_id),),
            ).fetchall()
        return tuple(
            RunTransition(
                run_id,
                RunState(row["previous_state"]),
                RunState(row["next_state"]),
                _parse_utc(row["occurred_at"]),
            )
            for row in rows
        )

    def attempt_terminals(self, run_id: RunId) -> tuple[AttemptTerminal, ...]:
        self._ensure_open()
        with self.__lock:
            rows = self.__connection.execute(
                """
                SELECT attempt_id, classification, occurred_at, reason_code
                FROM attempt_terminals WHERE run_id = ? ORDER BY occurred_at, attempt_id
                """,
                (str(run_id),),
            ).fetchall()
        return tuple(
            AttemptTerminal(
                AttemptId(row["attempt_id"]),
                run_id,
                AttemptTerminalKind(row["classification"]),
                _parse_utc(row["occurred_at"]),
                row["reason_code"],
            )
            for row in rows
        )

    def retry_lineage(self, run_id: RunId) -> tuple[tuple[AttemptId, AttemptId], ...]:
        self._ensure_open()
        with self.__lock:
            rows = self.__connection.execute(
                """
                SELECT previous_attempt_id, retry_attempt_id
                FROM retry_links WHERE run_id = ? ORDER BY occurred_at, previous_attempt_id
                """,
                (str(run_id),),
            ).fetchall()
        return tuple((AttemptId(row[0]), AttemptId(row[1])) for row in rows)

    def retry_authorizations_for(self, run_id: RunId) -> tuple[RetryAuthorization, ...]:
        self._ensure_open()
        with self.__lock:
            rows = self.__connection.execute(
                """
                SELECT assignment_id, parent_attempt_id, retry_attempt_id
                FROM retry_authorizations WHERE run_id = ?
                """,
                (str(run_id),),
            ).fetchall()
            authorizations: list[RetryAuthorization] = []
            for row in rows:
                parent_attempt_id = AttemptId(row["parent_attempt_id"])
                parent_terminal = self._attempt_terminal_for_locked(parent_attempt_id)
                if parent_terminal is None:
                    raise RuntimeError("retry authorization has no authoritative parent terminal")
                evidence = self.__connection.execute(
                    """
                    SELECT evidence_scope, artifact_id, artifact_sha256, size_bytes
                    FROM retry_authorization_evidence_refs
                    WHERE run_id = ? ORDER BY evidence_scope, ordinal
                    """,
                    (str(run_id),),
                ).fetchall()
                evidence_by_scope: dict[str, list[ArtifactRef]] = {
                    "exposure": [],
                    "isolation": [],
                }
                for reference in evidence:
                    evidence_by_scope[reference["evidence_scope"]].append(
                        ArtifactRef(
                            ArtifactId(reference["artifact_id"]),
                            reference["artifact_sha256"],
                            reference["size_bytes"],
                        )
                    )
                assignment_id = AssignmentId(row["assignment_id"])
                authorizations.append(
                    RetryAuthorization(
                        run_id=run_id,
                        assignment_id=assignment_id,
                        parent_assignment_id=assignment_id,
                        parent_attempt_id=parent_attempt_id,
                        attempt=Attempt(AttemptId(row["retry_attempt_id"]), run_id),
                        parent_terminal=parent_terminal,
                        exposure_evidence_refs=tuple(evidence_by_scope["exposure"]),
                        isolation_evidence_refs=tuple(evidence_by_scope["isolation"]),
                    )
                )
        return tuple(authorizations)

    def logical_history(self) -> tuple[LedgerEvent, ...]:
        self._ensure_open()
        with self.__lock:
            rows = self.__connection.execute(
                """
                SELECT sequence, intent_id, payload_digest, kind, occurred_at
                FROM ledger_events ORDER BY sequence
                """
            ).fetchall()
        return tuple(
            LedgerEvent(
                row["sequence"],
                IntentId(row["intent_id"]),
                row["payload_digest"],
                LedgerIntentKind(row["kind"]),
                _parse_utc(row["occurred_at"]),
            )
            for row in rows
        )

    def rejected_intent_evidence(self) -> tuple[RejectedIntentEvidence, ...]:
        self._ensure_open()
        with self.__lock:
            rows = self.__connection.execute(
                """
                SELECT intent_id, payload_digest, kind, reason_code, occurred_at
                FROM rejected_intents ORDER BY occurred_at, intent_id
                """
            ).fetchall()
        return tuple(
            RejectedIntentEvidence(
                IntentId(row["intent_id"]),
                row["payload_digest"],
                LedgerIntentKind(row["kind"]),
                row["reason_code"],
                _parse_utc(row["occurred_at"]),
            )
            for row in rows
        )

    def canonical_history(self) -> bytes:
        """Return a stable logical append stream suitable for reopen comparisons."""

        return canonical_bytes(
            {
                "events": [
                    {
                        "sequence": event.sequence,
                        "intent_id": str(event.intent_id),
                        "payload_digest": event.canonical_payload_digest,
                        "kind": event.kind.value,
                        "occurred_at": _utc_z(event.occurred_at),
                    }
                    for event in self.logical_history()
                ],
                "rejections": [
                    {
                        "intent_id": str(rejection.intent_id),
                        "payload_digest": rejection.canonical_payload_digest,
                        "kind": rejection.kind.value,
                        "reason_code": rejection.reason_code,
                        "occurred_at": _utc_z(rejection.occurred_at),
                    }
                    for rejection in self.rejected_intent_evidence()
                ],
            }
        )

    @property
    def schema_version(self) -> int:
        self._ensure_open()
        with self.__lock:
            row = self.__connection.execute(
                "SELECT MAX(version) AS version FROM schema_migration_journal"
            ).fetchone()
        return int(row["version"] or 0)

    @property
    def migration_journal(self) -> tuple[tuple[int, str], ...]:
        self._ensure_open()
        with self.__lock:
            rows = self.__connection.execute(
                """
                SELECT version, migration_sha256
                FROM schema_migration_journal ORDER BY version
                """
            ).fetchall()
        return tuple((int(row["version"]), row["migration_sha256"]) for row in rows)

    def integrity_check(self) -> str:
        self._ensure_open()
        with self.__lock:
            return cast(str, self.__connection.execute("PRAGMA integrity_check").fetchone()[0])

    def checkpoint(self) -> None:
        """Checkpoint WAL under the control writer for recovery-boundary tests."""

        self._ensure_open()
        with self.__lock:
            self._fault("before_wal_checkpoint")
            self.__connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._fault("after_wal_checkpoint")

    def sqlite_settings(self) -> dict[str, object]:
        self._ensure_open()
        with self.__lock:
            journal_mode = self.__connection.execute("PRAGMA journal_mode").fetchone()[0]
            foreign_keys = self.__connection.execute("PRAGMA foreign_keys").fetchone()[0]
        return {"journal_mode": journal_mode, "foreign_keys": bool(foreign_keys)}


if os.name != "nt" and hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=SqliteLedger._after_fork_in_child)


def _is_utc(value: object) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == UTC.utcoffset(None)
    )


def _utc_z(value: datetime) -> str:
    if not _is_utc(value):
        raise _RejectIntent("non_utc_timestamp")
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _optional_id(value: object | None) -> str | None:
    return str(value) if value is not None else None


def _safe_code(value: object) -> bool:
    return isinstance(value, str) and _REASON_CODE.fullmatch(value) is not None


def _direct_record_digest(kind: str, payload: dict[str, str]) -> str:
    return sha256(canonical_bytes({"kind": kind, **payload})).hexdigest()
