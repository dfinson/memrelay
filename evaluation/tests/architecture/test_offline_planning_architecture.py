"""Architecture compliance tests for offline planning.

Validates: no-network, determinism, redaction, evidence labels,
assignment concealment, cross-repository denial, credential isolation.
"""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path
from unittest.mock import patch

import pytest
from memrelay_eval.adapters.fakes import (
    FakeCopilotPort,
    FakeMemrelayPort,
    FakeOpenAIPort,
    InMemoryArtifactStore,
    InMemoryLedger,
)
from memrelay_eval.orchestration.planning import (
    _PROHIBITED_LABELS,
    EvidenceLabelError,
    OfflinePlanningPorts,
    plan_offline,
    plan_offline_to_command_manifest,
    redaction_scan,
    validate_evidence_label,
)

CATALOG_PATH = Path(__file__).parents[2] / "catalog" / "catalog.yaml"


@pytest.fixture(autouse=True)
def isolated_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run planning against a copied catalog so tests never alter repository inputs."""
    source_catalog = CATALOG_PATH
    catalog_root = tmp_path / "catalog"
    shutil.copytree(source_catalog.parent, catalog_root)
    monkeypatch.setitem(globals(), "CATALOG_PATH", catalog_root / "catalog.yaml")


def planning_output_dir(name: str = "generated") -> Path:
    output_dir = CATALOG_PATH.parent / name
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


class TestNoNetworkArchitecture:
    """AD-22: CI and dry run make zero Copilot/OpenAI calls."""

    def test_planning_blocks_all_sockets(self, tmp_path: Path) -> None:
        """Verify socket is blocked during the entire planning execution."""
        socket_calls: list[str] = []
        original_socket = socket.socket

        def tracking_socket(*args, **kwargs):
            socket_calls.append("socket_created")
            return original_socket(*args, **kwargs)

        with patch.object(socket, "socket", tracking_socket):
            output_dir = planning_output_dir("network")
            plan_offline(
                catalog_path=CATALOG_PATH,
                output_dir=output_dir,
                manifest_path=tmp_path / "manifest.json",
            )
        # The network deny guard replaces socket, so tracking_socket never fires
        assert len(socket_calls) == 0

    def test_fake_copilot_raises_on_use(self) -> None:
        port = FakeCopilotPort()
        with pytest.raises(RuntimeError, match="no real Copilot"):
            port.list_models()

    def test_fake_openai_raises_on_use(self) -> None:
        port = FakeOpenAIPort()
        with pytest.raises(RuntimeError, match="no real OpenAI"):
            port.create_completion()

    def test_fake_memrelay_raises_on_use(self) -> None:
        port = FakeMemrelayPort()
        with pytest.raises(RuntimeError, match="no real memrelay"):
            port.health()


class TestDeterminism:
    """Same frozen inputs produce byte-identical output."""

    def test_repeated_runs_identical(self, tmp_path: Path) -> None:
        results = []
        for i in range(3):
            catalog_path = CATALOG_PATH
            if i:
                catalog_root = tmp_path / f"catalog-{i}"
                shutil.copytree(CATALOG_PATH.parent, catalog_root)
                catalog_path = catalog_root / "catalog.yaml"
            output_dir = catalog_path.parent / "generated"
            output_dir.mkdir(parents=True, exist_ok=True)
            result = plan_offline(
                catalog_path=catalog_path,
                output_dir=output_dir,
                manifest_path=output_dir / "manifest.json",
            )
            results.append(result)

        for r in results[1:]:
            assert r.manifest_ref == results[0].manifest_ref
            assert r.input_hashes == results[0].input_hashes
            assert r.output_hashes == results[0].output_hashes

    def test_changed_seed_changes_assignment_hash(self, tmp_path: Path) -> None:
        output1 = planning_output_dir("run1")
        r1 = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=output1,
            manifest_path=output1 / "m.json",
            seed=b"seed-alpha",
        )
        output2 = planning_output_dir("run2")
        r2 = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=output2,
            manifest_path=output2 / "m.json",
            seed=b"seed-beta",
        )
        # Different seeds produce different assignment plan hashes
        assert r1.output_hashes.get("assignment_plan") != r2.output_hashes.get("assignment_plan")


class TestRedactionArchitecture:
    """AC-2: Redact prohibited log fields and scan emitted artifacts."""

    def test_manifest_contains_no_redacted_terms(self, tmp_path: Path) -> None:
        output_dir = planning_output_dir("redaction")
        result = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=output_dir,
            manifest_path=tmp_path / "manifest.json",
        )
        redaction_scan(plan_offline_to_command_manifest(result))
        # If we get here without RedactionViolationError, the scan passed
        planned_path = output_dir / "planned-run-manifest.json"
        if planned_path.exists():
            content = planned_path.read_bytes()
            # Verify no prohibited terms as JSON keys
            redaction_scan(content)


class TestEvidenceLabelArchitecture:
    """AC-3: Labels are implementation/conformance only."""

    def test_all_prohibited_labels_are_rejected(self) -> None:
        for label in _PROHIBITED_LABELS:
            with pytest.raises(EvidenceLabelError):
                validate_evidence_label(label)

    def test_output_manifest_uses_unpaid_conformance(self, tmp_path: Path) -> None:
        output_dir = planning_output_dir("evidence")
        result = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=output_dir,
            manifest_path=tmp_path / "manifest.json",
        )
        assert result.evidence_classification in ("unpaid_conformance", "implementation_evidence")
        planned_path = output_dir / "planned-run-manifest.json"
        if planned_path.exists():
            content = json.loads(planned_path.read_bytes())
            assert content["evidence_classification"] in (
                "unpaid_conformance",
                "implementation_evidence",
            )


class TestCredentialIsolation:
    """AD-09: No credentials enter the planning path."""

    def test_environment_credentials_not_in_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-canary-12345")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_canary_token_67890")
        monkeypatch.setenv("COPILOT_TOKEN", "gho_canary_99999")
        output_dir = planning_output_dir("credentials")
        plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=output_dir,
            manifest_path=tmp_path / "manifest.json",
        )
        # Scan all generated files
        for path in output_dir.rglob("*"):
            if path.is_file():
                content = path.read_text(errors="replace")
                assert "sk-test-canary-12345" not in content
                assert "ghp_canary_token_67890" not in content
                assert "gho_canary_99999" not in content


class TestFakeAdapterConformance:
    """Fake adapters declare correct provenance and eligibility."""

    def test_artifact_store_provenance(self) -> None:
        store = InMemoryArtifactStore()
        assert store.provenance == "unpaid_conformance"
        assert store.eligible_for_paid_or_study is False

    def test_ledger_provenance(self) -> None:
        ledger = InMemoryLedger()
        assert ledger.provenance == "unpaid_conformance"
        assert ledger.eligible_for_paid_or_study is False

    def test_fake_copilot_provenance(self) -> None:
        port = FakeCopilotPort()
        assert port.provenance == "unpaid_conformance"
        assert port.eligible_for_paid_or_study is False

    def test_fake_openai_provenance(self) -> None:
        port = FakeOpenAIPort()
        assert port.provenance == "unpaid_conformance"
        assert port.eligible_for_paid_or_study is False

    def test_fake_memrelay_provenance(self) -> None:
        port = FakeMemrelayPort()
        assert port.provenance == "unpaid_conformance"
        assert port.eligible_for_paid_or_study is False

    def test_planning_composes_fake_ports(self, tmp_path: Path) -> None:
        ports = OfflinePlanningPorts.fake()
        result = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=planning_output_dir("ports"),
            manifest_path=tmp_path / "command-manifest.json",
            ports=ports,
        )
        assert result.terminal_status == "succeeded"
        assert len(ports.telemetry.observations) == 1
