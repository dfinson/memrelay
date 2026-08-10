from __future__ import annotations

import base64
import io
import json
import time
import zipfile

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.adapters.inspect.export import persist_execution_evidence
from memrelay_eval.adapters.inspect.task import NativeTerminalRecord
from memrelay_eval.domain.errors import SecretBoundaryViolationError
from memrelay_eval.evidence.required import REQUIRED_NATIVE_EVIDENCE_KINDS
from memrelay_eval.evidence.secret_scan import (
    SecretBoundaryScanner,
    require_secret_boundary_clear,
    scan_secret_boundaries,
)


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


def test_base64_scanning_is_bounded_and_scales_linearly_through_public_api() -> None:
    def elapsed(value: str) -> float:
        started = time.perf_counter()
        assert scan_secret_boundaries({"events": value}) == ()
        return time.perf_counter() - started

    short = elapsed("A" * 10_000)
    long = elapsed("A" * 50_000)
    adversarial = elapsed(("AAAA " * 10_000) + "!")

    assert long < 2.0
    assert adversarial < 2.0
    assert long <= (short * 8) + 0.05


@pytest.mark.parametrize(
    "value",
    (
        "SK-" + ("x" * 24),
        "GHP_" + ("A" * 24),
        "\uff53\uff4b\uff0d" + ("x" * 24),
        "\u0455\u03ba-" + ("x" * 24),
        "G\u04bb\u03c1_" + ("A" * 24),
    ),
)
def test_unicode_and_case_variants_are_detected_in_plaintext_and_json(value: str) -> None:
    plain = scan_secret_boundaries({"events": value})
    document = scan_secret_boundaries({"export": json.dumps({"error": value}, ensure_ascii=False)})
    structured = scan_secret_boundaries({"export": {"error": value}})

    assert plain
    assert document
    assert structured
    assert value not in repr(plain)
    assert value not in repr(document)


def test_unicode_and_case_variants_are_detected_after_base64_and_archive_decoding() -> None:
    uppercase = "SK-" + ("x" * 24)
    fullwidth = "\uff53\uff4b\uff0d" + ("x" * 24)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("evidence.log", fullwidth)

    encoded_findings = scan_secret_boundaries(
        {"events": base64.b64encode(uppercase.encode()).decode()}
    )
    archive_findings = scan_secret_boundaries({"logs": archive.getvalue()})

    assert any(item.detector == "openai_key" for item in encoded_findings)
    assert any(item.detector == "openai_key" for item in archive_findings)


def test_unicode_projection_does_not_promote_short_or_prose_prefixes_to_secrets() -> None:
    assert (
        scan_secret_boundaries(
            {
                "events": (
                    "SK-short GHP_documentation fullwidth \uff53\uff4b\uff0dexample "
                    "and ordinary skeleton discussion"
                )
            }
        )
        == ()
    )


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


def test_failure_code_secret_omits_only_offending_values_and_preserves_native_bundle() -> None:
    store = InMemoryArtifactStore()
    value = _synthetic_openai_value()

    with pytest.raises(SecretBoundaryViolationError) as raised:
        persist_execution_evidence(
            store,
            inspect_state="failed",
            eval_bytes=b"partial evidence",
            inspect_export={"status": "failed"},
            native_terminal=NativeTerminalRecord(
                "failed",
                ("event-reference",),
                ("patch-reference",),
                {"total_tokens": 7},
                value,
            ),
        )

    assert len(raised.value.evidence_refs) == len(REQUIRED_NATIVE_EVIDENCE_KINDS) + 1
    persisted = tuple(store.open_verified(ref) for ref in raised.value.evidence_refs)
    assert b"partial evidence" in persisted
    assert any(b"event-reference" in item for item in persisted)
    assert any(b"patch-reference" in item for item in persisted)
    assert any(b"total_tokens" in item for item in persisted)
    assert all(value.encode() not in item for item in persisted)
    assert any(b'"status":"omitted"' in item for item in persisted)


def test_multiple_offending_fields_are_independently_omitted_without_secret_persistence() -> None:
    store = InMemoryArtifactStore()
    first = "SK-" + ("a" * 24)
    second = "GHP_" + ("B" * 24)

    with pytest.raises(SecretBoundaryViolationError) as raised:
        persist_execution_evidence(
            store,
            inspect_state="failed",
            eval_bytes=b"safe eval",
            inspect_export={"status": "failed", "error": first},
            native_terminal=NativeTerminalRecord(
                "failed",
                ("event-reference",),
                (),
                {},
                second,
            ),
        )

    persisted = tuple(store.open_verified(ref) for ref in raised.value.evidence_refs)
    assert len(raised.value.findings) >= 2
    assert all(first.encode() not in item and second.encode() not in item for item in persisted)
    assert sum(b'"status":"omitted"' in item for item in persisted) >= 2


def test_finding_persistence_failure_never_passes_secret_bytes_to_store() -> None:
    value = _synthetic_openai_value()

    class FailingFindingStore(InMemoryArtifactStore):
        def __init__(self) -> None:
            super().__init__()
            self.attempted: list[bytes] = []

        def put_bytes(self, data: bytes, *, media_type: str, classification: str):
            self.attempted.append(bytes(data))
            if classification == "secret_boundary_finding":
                raise OSError("synthetic finding persistence failure")
            return super().put_bytes(
                data,
                media_type=media_type,
                classification=classification,
            )

    store = FailingFindingStore()
    with pytest.raises(OSError, match="synthetic finding persistence failure"):
        persist_execution_evidence(
            store,
            inspect_state="failed",
            eval_bytes=b"safe eval",
            inspect_export={"status": "failed", "error": value},
            native_terminal=NativeTerminalRecord("failed", (), (), {}, "native_failed"),
        )

    assert len(store.attempted) == len(REQUIRED_NATIVE_EVIDENCE_KINDS) + 1
    assert all(value.encode() not in item for item in store.attempted)


def test_shared_aggregate_budget_fails_closed_after_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import memrelay_eval.evidence.secret_scan as secret_scan

    monkeypatch.setattr(secret_scan, "_MAX_SCAN_BYTES_TOTAL", 100)
    scanner = SecretBoundaryScanner()
    assert scanner.scan({"first": "!" * 60}) == ()
    exhausted = scanner.scan({"second": "?" * 60})
    unscanned = scanner.scan({"third": _synthetic_openai_value()})

    assert exhausted[0].detector == "scan_aggregate_size_exceeded"
    assert unscanned[0].detector == "scan_aggregate_size_exceeded"
