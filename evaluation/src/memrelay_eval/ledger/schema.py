"""Append-only, additive SQLite schema migrations for the evaluator ledger."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    statements: tuple[str, ...]

    @property
    def digest(self) -> str:
        return sha256("\n".join(self.statements).encode("utf-8")).hexdigest()


MIGRATIONS = (
    Migration(
        version=1,
        statements=(
            """
            CREATE TABLE IF NOT EXISTS intent_receipts (
                intent_id TEXT PRIMARY KEY,
                payload_digest TEXT NOT NULL,
                kind TEXT NOT NULL,
                outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'rejected')),
                reason_code TEXT,
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS ledger_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                intent_id TEXT NOT NULL UNIQUE REFERENCES intent_receipts(intent_id),
                payload_digest TEXT NOT NULL,
                kind TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rejected_intents (
                intent_id TEXT PRIMARY KEY REFERENCES intent_receipts(intent_id),
                payload_digest TEXT NOT NULL,
                kind TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS experiments (
                experiment_id TEXT PRIMARY KEY,
                protocol_id TEXT NOT NULL,
                intent_id TEXT NOT NULL UNIQUE REFERENCES intent_receipts(intent_id),
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
                assignment_id TEXT NOT NULL,
                intent_id TEXT NOT NULL UNIQUE REFERENCES intent_receipts(intent_id),
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                intent_id TEXT NOT NULL UNIQUE REFERENCES intent_receipts(intent_id),
                occurred_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS run_transitions (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                intent_id TEXT NOT NULL UNIQUE REFERENCES intent_receipts(intent_id),
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                previous_state TEXT NOT NULL,
                next_state TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                monotonic_ns INTEGER,
                source_attempt_id TEXT REFERENCES attempts(attempt_id),
                expected_prior_digest TEXT,
                reason_code TEXT NOT NULL,
                event_digest TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS attempt_terminals (
                attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
                intent_id TEXT NOT NULL UNIQUE REFERENCES intent_receipts(intent_id),
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                classification TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                monotonic_ns INTEGER,
                source_attempt_id TEXT REFERENCES attempts(attempt_id),
                reason_code TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS artifact_links (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                intent_id TEXT NOT NULL UNIQUE REFERENCES intent_receipts(intent_id),
                artifact_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                purpose TEXT NOT NULL,
                experiment_id TEXT REFERENCES experiments(experiment_id),
                run_id TEXT REFERENCES runs(run_id),
                attempt_id TEXT REFERENCES attempts(attempt_id),
                occurred_at TEXT NOT NULL,
                reason_code TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS retry_links (
                previous_attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
                retry_attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(attempt_id),
                intent_id TEXT NOT NULL UNIQUE REFERENCES intent_receipts(intent_id),
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                occurred_at TEXT NOT NULL,
                monotonic_ns INTEGER,
                reason_code TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS inclusion_decisions (
                inclusion_id TEXT PRIMARY KEY,
                intent_id TEXT NOT NULL UNIQUE REFERENCES intent_receipts(intent_id),
                run_id TEXT NOT NULL UNIQUE REFERENCES runs(run_id),
                status TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                reconciliation_sha256 TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                monotonic_ns INTEGER
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS intent_evidence_refs (
                intent_id TEXT NOT NULL REFERENCES intent_receipts(intent_id),
                ordinal INTEGER NOT NULL,
                artifact_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                PRIMARY KEY (intent_id, ordinal)
            )
            """,
            (
                "CREATE INDEX IF NOT EXISTS run_transitions_by_run "
                "ON run_transitions(run_id, sequence)"
            ),
            "CREATE INDEX IF NOT EXISTS attempts_by_run ON attempts(run_id)",
            "CREATE INDEX IF NOT EXISTS artifact_links_by_run ON artifact_links(run_id, sequence)",
        ),
    ),
    Migration(
        version=2,
        statements=(
            """
            CREATE TABLE IF NOT EXISTS attempt_terminal_evidence_refs (
                attempt_id TEXT NOT NULL REFERENCES attempt_terminals(attempt_id),
                ordinal INTEGER NOT NULL,
                artifact_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                PRIMARY KEY (attempt_id, ordinal)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS retry_authorizations (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                assignment_id TEXT NOT NULL,
                parent_attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                retry_attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(attempt_id),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS retry_authorization_evidence_refs (
                run_id TEXT NOT NULL REFERENCES retry_authorizations(run_id),
                evidence_scope TEXT NOT NULL
                    CHECK (evidence_scope IN ('exposure', 'isolation')),
                ordinal INTEGER NOT NULL,
                artifact_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                PRIMARY KEY (run_id, evidence_scope, ordinal)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS internal_retries (
                attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                subsystem TEXT NOT NULL,
                retry_number INTEGER NOT NULL CHECK (retry_number >= 1),
                created_at TEXT NOT NULL,
                PRIMARY KEY (attempt_id, subsystem, retry_number)
            )
            """,
            (
                "CREATE INDEX IF NOT EXISTS internal_retries_by_attempt_subsystem "
                "ON internal_retries(attempt_id, subsystem, retry_number)"
            ),
        ),
    ),
    Migration(
        version=3,
        statements=(
            """
            CREATE TABLE IF NOT EXISTS attempt_execution_claims (
                attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                claimed_at TEXT NOT NULL
            )
            """,
        ),
    ),
)


def apply_migrations(connection: object, *, applied_at: str) -> None:
    """Apply only known additive migrations and journal each exact migration hash."""

    execute = connection.execute
    execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migration_journal (
            version INTEGER PRIMARY KEY,
            migration_sha256 TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    for migration in MIGRATIONS:
        row = execute(
            "SELECT migration_sha256 FROM schema_migration_journal WHERE version = ?",
            (migration.version,),
        ).fetchone()
        if row is not None:
            if row[0] != migration.digest:
                raise RuntimeError(
                    f"ledger migration {migration.version} hash differs from its "
                    "append-only journal"
                )
            continue
        execute("BEGIN IMMEDIATE")
        try:
            for statement in migration.statements:
                execute(statement)
            execute(
                """
                INSERT INTO schema_migration_journal (version, migration_sha256, applied_at)
                VALUES (?, ?, ?)
                """,
                (migration.version, migration.digest, applied_at),
            )
            execute("COMMIT")
        except BaseException:
            execute("ROLLBACK")
            raise
