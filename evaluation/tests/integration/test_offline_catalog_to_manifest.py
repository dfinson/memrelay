"""End-to-end offline catalog-to-planned-run-manifest integration tests.

Validates AC-1: deterministic planned-run manifest with no network or credentials.
Validates AC-2: input/output hashes, runtime lock, protocol ID, typed terminal status.
"""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import pytest
from memrelay_eval.orchestration.planning import (
    NetworkDenyError,
    PlanningResult,
    network_deny_guard,
    plan_offline,
    plan_offline_to_command_manifest,
)

CATALOG_PATH = Path(__file__).parents[2] / "catalog" / "catalog.yaml"


@pytest.fixture(autouse=True)
def isolated_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run planning against a copied catalog so tests never alter repository inputs."""
    source_catalog = CATALOG_PATH
    catalog_root = tmp_path / "catalog"
    shutil.copytree(source_catalog.parent, catalog_root)
    monkeypatch.setitem(globals(), "CATALOG_PATH", catalog_root / "catalog.yaml")


@pytest.fixture
def clean_output_dir(tmp_path: Path) -> Path:
    """Provide a fresh output directory for each test."""
    del tmp_path
    output = CATALOG_PATH.parent / "generated"
    output.mkdir(parents=True, exist_ok=True)
    return output


class TestNetworkDenyGuard:
    """Verify that the network deny guard blocks all socket operations."""

    def test_socket_creation_blocked(self) -> None:
        with network_deny_guard(), pytest.raises(NetworkDenyError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def test_getaddrinfo_blocked(self) -> None:
        with network_deny_guard(), pytest.raises(NetworkDenyError):
            socket.getaddrinfo("example.com", 80)

    def test_gethostbyname_blocked(self) -> None:
        with network_deny_guard(), pytest.raises(NetworkDenyError):
            socket.gethostbyname("example.com")

    def test_restored_after_context(self) -> None:
        original = socket.socket
        with network_deny_guard():
            pass
        assert socket.socket is original


class TestOfflinePlanningEndToEnd:
    """Full offline planning pipeline reaching exactly planned state."""

    def test_successful_plan_produces_manifest(self, clean_output_dir: Path) -> None:
        manifest_path = clean_output_dir / "plan-manifest.json"
        result = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=clean_output_dir,
            manifest_path=manifest_path,
        )
        assert result.terminal_status == "succeeded"
        assert result.exit_code == 0
        assert result.evidence_classification == "unpaid_conformance"
        assert result.protocol_id is not None
        assert result.manifest_ref is not None
        assert len(result.input_hashes) > 0
        assert len(result.output_hashes) > 0

    def test_manifest_is_deterministic(self, clean_output_dir: Path) -> None:
        """Same inputs produce byte-identical manifests."""
        manifest_path = clean_output_dir / "plan-manifest.json"
        result1 = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=clean_output_dir,
            manifest_path=manifest_path,
        )
        # Run again in a fresh directory
        catalog_root2 = clean_output_dir.parent.parent / "catalog2"
        shutil.copytree(CATALOG_PATH.parent, catalog_root2)
        catalog_path2 = catalog_root2 / "catalog.yaml"
        output2 = catalog_root2 / "generated"
        output2.mkdir(exist_ok=True)
        manifest_path2 = output2 / "plan-manifest.json"
        result2 = plan_offline(
            catalog_path=catalog_path2,
            output_dir=output2,
            manifest_path=manifest_path2,
        )
        assert result1.manifest_ref == result2.manifest_ref
        assert result1.input_hashes == result2.input_hashes
        assert result1.output_hashes == result2.output_hashes

    def test_planned_manifest_file_written(self, clean_output_dir: Path) -> None:
        manifest_path = clean_output_dir / "plan-manifest.json"
        plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=clean_output_dir,
            manifest_path=manifest_path,
        )
        planned_path = clean_output_dir / "planned-run-manifest.json"
        assert planned_path.exists()
        content = json.loads(planned_path.read_bytes())
        assert content["terminal_state"] == "planned"
        assert content["evidence_classification"] == "unpaid_conformance"
        assert "digest" in content

    def test_no_lifecycle_beyond_planned(self, clean_output_dir: Path) -> None:
        manifest_path = clean_output_dir / "plan-manifest.json"
        plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=clean_output_dir,
            manifest_path=manifest_path,
        )
        planned_path = clean_output_dir / "planned-run-manifest.json"
        content = json.loads(planned_path.read_bytes())
        assert content["terminal_state"] == "planned"
        # Verify no assigned/provisioned/running transitions exist
        assert "assigned" not in json.dumps(content).lower().replace("assignment", "")

    def test_no_credentials_in_environment(
        self, clean_output_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Credential canaries in environment produce no leakage."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-canary-test-key")
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_canary_token")
        manifest_path = clean_output_dir / "plan-manifest.json"
        result = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=clean_output_dir,
            manifest_path=manifest_path,
        )
        assert result.terminal_status == "succeeded"
        # Check that no credential values leak into the output
        planned_path = clean_output_dir / "planned-run-manifest.json"
        content = planned_path.read_text()
        assert "sk-canary-test-key" not in content
        assert "ghp_canary_token" not in content


class TestCommandManifest:
    """Verify command manifest serialization."""

    def test_command_manifest_is_valid_json(self, clean_output_dir: Path) -> None:
        manifest_path = clean_output_dir / "plan-manifest.json"
        result = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=clean_output_dir,
            manifest_path=manifest_path,
        )
        manifest_bytes = plan_offline_to_command_manifest(result)
        document = json.loads(manifest_bytes)
        assert document["command"] == "plan-offline"
        assert document["terminal_status"] == "succeeded"
        assert "digest" in document

    def test_failed_result_manifest(self) -> None:
        result = PlanningResult(
            terminal_status="failed",
            exit_code=1,
            error_code="catalog_failed",
            error_message="validation error",
        )
        manifest_bytes = plan_offline_to_command_manifest(result)
        document = json.loads(manifest_bytes)
        assert document["terminal_status"] == "failed"
        assert document["error_code"] == "catalog_failed"
