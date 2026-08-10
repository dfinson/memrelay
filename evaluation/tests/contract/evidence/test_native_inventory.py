from __future__ import annotations

from memrelay_eval.adapters.fakes import InMemoryArtifactStore, InMemoryLedger, InMemoryTelemetry
from memrelay_eval.adapters.inspect.export import (
    persist_execution_evidence,
    reconcile_execution_evidence,
)
from memrelay_eval.adapters.inspect.task import NativeTerminalRecord
from memrelay_eval.domain.errors import ExecutionEvidenceConflictError, UnqualifiedEvidencePortError
from memrelay_eval.evidence.required import (
    REQUIRED_NATIVE_EVIDENCE_KINDS,
    require_unpaid_conformance_ports,
)


def test_native_inventory_retains_every_terminal_surface_as_independent_hashes() -> None:
    store = InMemoryArtifactStore()
    evidence = persist_execution_evidence(
        store,
        inspect_state="timed_out",
        eval_bytes=b"native inspect eval",
        inspect_export={
            "status": "timed_out",
            "usage": {"total_tokens": 12, "active_seconds": 5},
            "limits": {"token_limit": 15},
            "cleanup_time": {"seconds": 2},
        },
        native_terminal=NativeTerminalRecord(
            "timed_out",
            ("event-reference",),
            ("patch-reference",),
            {"total_tokens": 12, "active_seconds": 5},
        ),
    )

    assert set(evidence.inventory.artifacts) == REQUIRED_NATIVE_EVIDENCE_KINDS
    assert all(store.open_verified(ref) for ref in evidence.inventory.artifacts.values())
    reconcile_execution_evidence(
        evidence,
        {"status": "timed_out", "usage": {"total_tokens": 12, "active_seconds": 5}},
    )


def test_native_usage_disagreement_blocks_without_discarding_artifact_references() -> None:
    store = InMemoryArtifactStore()
    evidence = persist_execution_evidence(
        store,
        inspect_state="succeeded",
        eval_bytes=b"native inspect eval",
        inspect_export={"status": "succeeded", "usage": {"total_tokens": 12}},
        native_terminal=NativeTerminalRecord("succeeded", (), (), {"total_tokens": 11}),
    )

    try:
        reconcile_execution_evidence(
            evidence,
            {"status": "succeeded", "usage": {"total_tokens": 12}},
        )
    except ExecutionEvidenceConflictError:
        assert set(evidence.inventory.artifacts) == REQUIRED_NATIVE_EVIDENCE_KINDS
    else:
        raise AssertionError("native usage disagreement must block")


def test_only_unpaid_fake_ports_are_permitted_before_durable_adapters() -> None:
    require_unpaid_conformance_ports(InMemoryArtifactStore(), InMemoryLedger(), InMemoryTelemetry())

    class UnqualifiedPort:
        provenance = "durable"
        eligible_for_paid_or_study = True

    try:
        require_unpaid_conformance_ports(UnqualifiedPort())
    except UnqualifiedEvidencePortError as error:
        assert str(error) == UnqualifiedEvidencePortError.code
    else:
        raise AssertionError("durable adapter must not be admitted by Story 2.10")
