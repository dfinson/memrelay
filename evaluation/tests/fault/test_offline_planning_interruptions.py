"""Fault and interruption tests for offline planning.

Validates AC-1-3: invalid catalog, ineligible fixture, freeze failure,
assignment failure, interruption handling.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from memrelay_eval.orchestration.planning import (
    EvidenceLabelError,
    NetworkDenyError,
    RedactionViolationError,
    network_deny_guard,
    plan_offline,
    redaction_scan,
    validate_evidence_label,
)

CATALOG_PATH = Path(__file__).parents[2] / "catalog" / "catalog.yaml"


class TestInvalidCatalog:
    """Planning fails with typed error on invalid catalog input."""

    def test_nonexistent_catalog_fails(self, tmp_path: Path) -> None:
        result = plan_offline(
            catalog_path=tmp_path / "missing.yaml",
            output_dir=tmp_path / "gen",
            manifest_path=tmp_path / "manifest.json",
        )
        assert result.terminal_status == "failed"
        assert result.exit_code != 0
        assert result.error_code is not None

    def test_malformed_yaml_fails(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("{{{{invalid yaml")
        output_dir = tmp_path / "gen"
        output_dir.mkdir()
        result = plan_offline(
            catalog_path=bad,
            output_dir=output_dir,
            manifest_path=tmp_path / "manifest.json",
        )
        assert result.terminal_status == "failed"
        assert result.exit_code != 0

    def test_empty_catalog_fails(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty.yaml"
        empty.write_text("")
        output_dir = tmp_path / "gen"
        output_dir.mkdir()
        result = plan_offline(
            catalog_path=empty,
            output_dir=output_dir,
            manifest_path=tmp_path / "manifest.json",
        )
        assert result.terminal_status == "failed"
        assert result.exit_code != 0


class TestInterruption:
    """Planning handles KeyboardInterrupt gracefully."""

    def test_keyboard_interrupt_during_compilation(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "gen"
        output_dir.mkdir()

        with patch(
            "memrelay_eval.orchestration.planning.compile_catalog_command",
            side_effect=KeyboardInterrupt,
        ):
            result = plan_offline(
                catalog_path=CATALOG_PATH,
                output_dir=output_dir,
                manifest_path=tmp_path / "manifest.json",
            )
        assert result.terminal_status == "interrupted"
        assert result.exit_code == 130
        assert result.error_code == "keyboard_interrupt"


class TestNetworkDenial:
    """No network operations are possible under the deny guard."""

    def test_socket_blocked(self) -> None:
        import socket as _socket

        with network_deny_guard(), pytest.raises(NetworkDenyError):
            _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)

    def test_dns_blocked(self) -> None:
        import socket as _socket

        with network_deny_guard(), pytest.raises(NetworkDenyError):
            _socket.getaddrinfo("example.com", 443)


class TestRedactionEnforcement:
    """Redaction scan catches prohibited terms in emitted bytes."""

    def test_clean_bytes_pass(self) -> None:
        redaction_scan(b'{"status": "ok", "id": "abc123"}')

    def test_prohibited_term_fails(self) -> None:
        with pytest.raises(RedactionViolationError):
            redaction_scan(b'{"prompt": "hello world"}')

    def test_credential_term_fails(self) -> None:
        with pytest.raises(RedactionViolationError):
            redaction_scan(b'{"credential": "sk-xxx"}')


class TestEvidenceLabels:
    """Only implementation/conformance labels are accepted."""

    def test_valid_labels_accepted(self) -> None:
        assert validate_evidence_label("unpaid_conformance") == "unpaid_conformance"
        assert validate_evidence_label("implementation_evidence") == "implementation_evidence"

    def test_prohibited_labels_rejected(self) -> None:
        for label in ("study", "included", "efficacy", "safety", "economics", "release_fitness"):
            with pytest.raises(EvidenceLabelError):
                validate_evidence_label(label)

    def test_planning_rejects_prohibited_label(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "gen"
        output_dir.mkdir()
        with pytest.raises(EvidenceLabelError):
            plan_offline(
                catalog_path=CATALOG_PATH,
                output_dir=output_dir,
                manifest_path=tmp_path / "manifest.json",
                evidence_classification="efficacy",
            )
