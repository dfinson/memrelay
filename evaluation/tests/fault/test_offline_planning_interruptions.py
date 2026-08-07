"""Fault and interruption tests for offline planning.

Validates AC-1-3: invalid catalog, ineligible fixture, freeze failure,
assignment failure, interruption handling.
"""

from __future__ import annotations

import shutil
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


@pytest.fixture(autouse=True)
def isolated_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run planning against a copied catalog so tests never alter repository inputs."""
    source_catalog = CATALOG_PATH
    catalog_root = tmp_path / "catalog"
    shutil.copytree(source_catalog.parent, catalog_root)
    monkeypatch.setitem(globals(), "CATALOG_PATH", catalog_root / "catalog.yaml")


def planning_output_dir() -> Path:
    output_dir = CATALOG_PATH.parent / "generated"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


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
        output_dir = planning_output_dir()

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

    @pytest.mark.parametrize(
        "payload",
        (
            b'{"note":"credential store confirmed provider access"}',
            b'{"note":"repository access was requested"}',
        ),
    )
    def test_prohibited_prose_fails(self, payload: bytes) -> None:
        with pytest.raises(RedactionViolationError):
            redaction_scan(payload)

    def test_innocuous_substrings_pass(self) -> None:
        redaction_scan(b'{"status":"decoded successfully"}')

    @pytest.mark.parametrize(
        "payload",
        (
            '{"note":"cred\u200bential store"}',
            '{"note":"secr\u200cet value"}',
            '{"note":"pro\u2060vider payload"}',
            '{"note":"cred\ufe0fential store"}',
        ),
    )
    def test_default_ignorables_cannot_split_prohibited_json_terms(self, payload: str) -> None:
        with pytest.raises(RedactionViolationError):
            redaction_scan(payload.encode("utf-8"))

    @pytest.mark.parametrize(
        "payload",
        (
            "credential\u200d store",
            "secr\ufeffet material",
        ),
    )
    def test_default_ignorables_cannot_split_prohibited_plaintext_terms(self, payload: str) -> None:
        with pytest.raises(RedactionViolationError):
            redaction_scan(payload.encode("utf-8"))

    @pytest.mark.parametrize(
        "payload",
        (
            '{"note":"cr\u0435dential store"}',
            '{"note":"pr\u043evider payload"}',
            '{"note":"s\u0435cret material"}',
            '{"note":"us\u0435r identity"}',
        ),
    )
    def test_confusable_prohibited_json_terms_fail_closed(self, payload: str) -> None:
        with pytest.raises(RedactionViolationError):
            redaction_scan(payload.encode("utf-8"))

    def test_benign_non_ascii_text_passes_without_transliteration(self) -> None:
        redaction_scan('{"note":"café Δ"}'.encode())


class TestEvidenceLabels:
    """Only implementation/conformance labels are accepted."""

    def test_valid_labels_accepted(self) -> None:
        assert validate_evidence_label("unpaid_conformance") == "unpaid_conformance"
        assert validate_evidence_label("implementation_evidence") == "implementation_evidence"

    def test_prohibited_labels_rejected(self) -> None:
        for label in ("study", "included", "efficacy", "safety", "economics", "release_fitness"):
            with pytest.raises(EvidenceLabelError):
                validate_evidence_label(label)

    @pytest.mark.parametrize("label", ("Efficacy", "EFFICACY", " efficacy "))
    def test_prohibited_label_spoofs_are_rejected(self, label: str) -> None:
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

    def test_planning_rejects_casefolded_prohibited_label(self) -> None:
        with pytest.raises(EvidenceLabelError):
            plan_offline(
                catalog_path=CATALOG_PATH,
                output_dir=planning_output_dir(),
                manifest_path=CATALOG_PATH.parent / "plan-manifest.json",
                evidence_classification="EFFICACY",
            )


class TestPlanningPublication:
    """Planning outputs fail closed and preserve prior complete publications."""

    def test_out_of_tree_output_is_rejected_with_typed_manifest(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "command-manifest.json"
        result = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=tmp_path / "outside-output",
            manifest_path=manifest_path,
        )
        assert result.terminal_status == "failed"
        assert result.error_code == "invalid_output_dir_layout"
        assert manifest_path.exists()

    def test_out_of_tree_lock_is_rejected_with_typed_manifest(self, tmp_path: Path) -> None:
        manifest_path = tmp_path / "command-manifest.json"
        result = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=planning_output_dir(),
            lock_path=tmp_path / "outside-lock.json",
            manifest_path=manifest_path,
        )
        assert result.terminal_status == "failed"
        assert result.error_code == "invalid_lock_path_layout"
        assert manifest_path.exists()

    def test_interrupting_planned_manifest_publish_preserves_prior_bytes(
        self, tmp_path: Path
    ) -> None:
        output_dir = planning_output_dir()
        command_manifest = tmp_path / "command-manifest.json"
        initial = plan_offline(
            catalog_path=CATALOG_PATH,
            output_dir=output_dir,
            manifest_path=command_manifest,
        )
        assert initial.terminal_status == "succeeded"
        planned_path = output_dir / "planned-run-manifest.json"
        prior_bytes = planned_path.read_bytes()

        from memrelay_eval.orchestration import planning

        writer = planning._write_bytes_durable
        interrupted_once = False

        def interrupt_planned_manifest(path: Path, data: bytes) -> None:
            nonlocal interrupted_once
            if path == planned_path and not interrupted_once:
                interrupted_once = True
                raise KeyboardInterrupt
            writer(path, data)

        with patch(
            "memrelay_eval.orchestration.planning._write_bytes_durable",
            side_effect=interrupt_planned_manifest,
        ):
            interrupted = plan_offline(
                catalog_path=CATALOG_PATH,
                output_dir=output_dir,
                manifest_path=command_manifest,
            )

        assert interrupted.terminal_status == "interrupted"
        assert planned_path.read_bytes() == prior_bytes
        assert b'"terminal_status":"interrupted"' in command_manifest.read_bytes()
