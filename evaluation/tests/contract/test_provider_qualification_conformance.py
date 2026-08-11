"""Provider qualification must cross explicit paid authority and adapter boundaries."""

from __future__ import annotations

from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from memrelay_eval.canonical import canonical_bytes
from memrelay_eval.cli.commands import conformance
from memrelay_eval.domain.errors import StageControlError
from memrelay_eval.domain.ids import ProtocolId, StageAuthorizationId, StageId
from memrelay_eval.domain.policies import STAGE_ENTRY_LOCK_FIELDS
from memrelay_eval.domain.states import StageKind
from memrelay_eval.evidence.conformance import (
    build_bootstrap_receipt,
    observed_probe_result,
    provider_proof_registry,
)
from memrelay_eval.orchestration.stages import StageAuthorization, StageEntryBundle

HASH_A = "a" * 64


def _authority(tmp_path: Path, *, paid: bool = True) -> tuple[Namespace, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    locks = dict.fromkeys(STAGE_ENTRY_LOCK_FIELDS, HASH_A)
    entry = StageEntryBundle(
        stage_id=StageId.new(),
        stage_kind=StageKind.INTEGRATION,
        protocol_id=ProtocolId.new(),
        predecessor_stage_kind=StageKind.CONFORMANCE,
        locks=locks,
    )
    authorization = StageAuthorization(
        authorization_id=StageAuthorizationId.new(),
        stage_id=entry.stage_id,
        stage_kind=entry.stage_kind,
        protocol_id=entry.protocol_id,
        entry_bundle_sha256=entry.digest,
        envelope_sha256=entry.envelope_sha256,
        authorizer_id="independent-operator",
        authorizer_role="operator",
        valid_from=datetime.now(UTC) - timedelta(minutes=1),
        valid_until=datetime.now(UTC) + timedelta(minutes=1),
        paid_execution=paid,
    )
    entry_path = tmp_path / "entry.json"
    authorization_path = tmp_path / "authorization.json"
    stage_locks_path = tmp_path / "locks.json"
    bootstrap_path = tmp_path / "bootstrap.json"
    entry_path.write_bytes(entry.bytes())
    authorization_path.write_bytes(authorization.bytes())
    stage_locks_path.write_bytes(canonical_bytes(locks))
    from memrelay_eval.evidence.conformance import bootstrap_receipt_bytes

    bootstrap_path.write_bytes(
        bootstrap_receipt_bytes(
            build_bootstrap_receipt(
                mode="provider_qualification",
                runtime_lock={"lock_sha256": HASH_A},
                input_hashes={"runtime": HASH_A},
                output_hashes={"telemetry": HASH_A},
                environment_sha256=HASH_A,
                protocol_sha256=HASH_A,
            )
        )
    )
    return (
        Namespace(
            mode="provider_qualification",
            catalog=str(Path(__file__).parents[2] / "catalog" / "catalog.yaml"),
            stage_locks=str(stage_locks_path),
            bootstrap_receipt=str(bootstrap_path),
            entry_bundle=str(entry_path),
            authorization=str(authorization_path),
            output_root=str(tmp_path / "artifacts"),
        ),
        entry_path,
        authorization_path,
    )


def test_provider_qualification_requires_paid_sealed_authority_before_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _, _ = _authority(tmp_path, paid=False)
    called: list[str] = []

    with pytest.raises(StageControlError) as failure:
        conformance(
            args,
            provider_probe=lambda proof_id, _: called.append(proof_id),  # type: ignore[return-value]
        )
    assert failure.value.code == "provider_qualification_paid_authorization_required"
    assert called == []

    args, _, _ = _authority(tmp_path / "ci")
    monkeypatch.setenv("CI", "1")
    with pytest.raises(StageControlError) as failure:
        conformance(
            args,
            provider_probe=lambda proof_id, _: called.append(proof_id),  # type: ignore[return-value]
        )
    assert failure.value.code == "paid_execution_forbidden_in_ci"
    assert called == []


def test_provider_registry_uses_controlled_adapter_only_for_provider_proofs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args, _, _ = _authority(tmp_path)
    adapter_calls: list[str] = []

    monkeypatch.setattr(
        "memrelay_eval.evidence.conformance.pytest_probe",
        lambda proof_id: (
            lambda _: observed_probe_result(
                input_documents={"local_contract": proof_id},
                output_documents={"local_observed": proof_id},
            )
        ),
    )

    def fake_provider(proof_id: str, _):
        adapter_calls.append(proof_id)
        return observed_probe_result(
            input_documents={"provider_contract": proof_id},
            output_documents={"provider_observed": proof_id},
        )

    assert conformance(args, proof_registry=provider_proof_registry(fake_provider)) == 0
    assert adapter_calls == sorted(
        {
            "AUTH-COPILOT-SUBSCRIPTION",
            "MODEL-CATALOG-SNAPSHOT",
            "MODEL-SELECTION-PIN",
        }
    )
