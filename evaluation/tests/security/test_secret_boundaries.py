from __future__ import annotations

import base64
import io
import zipfile

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.adapters.inspect.export import persist_execution_evidence
from memrelay_eval.adapters.inspect.task import NativeTerminalRecord
from memrelay_eval.domain.errors import SecretBoundaryViolationError
from memrelay_eval.evidence.secret_scan import require_secret_boundary_clear, scan_secret_boundaries


def _synthetic_openai_value() -> str:
    return "sk-" + ("x" * 24)


@pytest.mark.parametrize(
    "surface",
    (
        "events",
        "patches",
        "usage",
        "logs",
        "manifests",
        "exports",
        "errors",
        "subprocess_environment",
        "temporary_files",
        "cleanup_evidence",
    ),
)
def test_secret_surfaces_fail_closed_without_rendering_matched_value(surface: str) -> None:
    value = _synthetic_openai_value()
    with pytest.raises(SecretBoundaryViolationError) as raised:
        require_secret_boundary_clear({surface: {"payload": value}})

    assert value not in str(raised.value)
    assert value not in repr(raised.value.findings)
    assert raised.value.findings[0].location.startswith(surface)


def test_encoded_and_archived_secrets_are_detected() -> None:
    value = _synthetic_openai_value()
    encoded = base64.b64encode(value.encode()).decode()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("evidence.log", value)

    encoded_findings = scan_secret_boundaries({"events": encoded})
    archive_findings = scan_secret_boundaries({"logs": archive.getvalue()})

    assert any(item.detector == "openai_key" for item in encoded_findings)
    assert any(item.detector == "openai_key" for item in archive_findings)
    assert value not in repr(encoded_findings)
    assert value not in repr(archive_findings)


def test_whitespace_wrapped_base64_secret_is_detected() -> None:
    value = _synthetic_openai_value()
    encoded = base64.b64encode(value.encode()).decode()
    wrapped = " ".join(encoded[index : index + 12] for index in range(0, len(encoded), 12))

    findings = scan_secret_boundaries({"events": wrapped})

    assert any(item.detector == "openai_key" for item in findings)
    assert value not in repr(findings)


def test_secret_mapping_keys_and_oversized_archive_members_are_non_secret_findings() -> None:
    value = _synthetic_openai_value()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        output.writestr("large-evidence.log", ("x" * (4 * 1024 * 1024)) + value)

    key_findings = scan_secret_boundaries({"artifact": {value: "ordinary value"}})
    archive_findings = scan_secret_boundaries({"logs": archive.getvalue()})

    assert key_findings[0].detector == "openai_key"
    assert any(item.detector == "scan_size_exceeded" for item in archive_findings)
    assert value not in repr(key_findings)
    assert value not in repr(archive_findings)


def test_safe_text_and_treatment_neutral_surfaces_do_not_trigger() -> None:
    assert (
        scan_secret_boundaries(
            {
                "logs": "Documentation refers to a token without containing a credential.",
                "agent_visible.prompt": "Complete the synthetic task.",
            }
        )
        == ()
    )


def test_secret_export_is_replaced_by_a_redacted_finding_artifact() -> None:
    store = InMemoryArtifactStore()
    value = _synthetic_openai_value()

    with pytest.raises(SecretBoundaryViolationError) as raised:
        persist_execution_evidence(
            store,
            inspect_state="failed",
            eval_bytes=b"partial evidence",
            inspect_export={"status": "failed", "error": value},
            native_terminal=NativeTerminalRecord("failed", (), (), {}, "native_failed"),
        )

    assert len(raised.value.evidence_refs) == 1
    assert value not in store.open_verified(raised.value.evidence_refs[0]).decode()
