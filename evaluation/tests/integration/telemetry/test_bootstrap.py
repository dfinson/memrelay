from __future__ import annotations

from argparse import Namespace
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from memrelay_eval.adapters.fakes import InMemoryArtifactStore
from memrelay_eval.adapters.telemetry.otel import (
    COLLECTOR_ARCHIVE_NAME,
    CollectorArchive,
    verify_telemetry_bootstrap,
)
from memrelay_eval.adapters.telemetry.semantics import TELEMETRY_SCHEMA_VERSION
from memrelay_eval.cli.commands import bootstrap
from memrelay_eval.domain.errors import TelemetryConformanceError


def _write_config(root: Path) -> tuple[Path, Path]:
    collector = root / "collector.yaml"
    semantic_map = root / "semantic-map.yaml"
    collector.write_text(
        """
receivers:
  otlp:
    protocols:
      grpc: {}
      http: {}
processors:
  batch: {}
exporters:
  file:
    path: ${env:MEMRELAY_EVAL_OTLP_EXPORT_PATH}
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [batch]
      exporters: [file]
""".strip(),
        encoding="utf-8",
    )
    semantic_map.write_text(
        """
schema_version: "{TELEMETRY_SCHEMA_VERSION}"
genai_map_version: "memrelay.eval.genai-map/1.0.0"
source: "otel-genai-development"
mapping:
  gen_ai.operation.name: memrelay.eval.genai.operation
  gen_ai.request.model: memrelay.eval.genai.request_model
  gen_ai.response.model: memrelay.eval.genai.response_model
  gen_ai.usage.input_tokens: memrelay.eval.genai.input_tokens
  gen_ai.usage.output_tokens: memrelay.eval.genai.output_tokens
""".strip().format(TELEMETRY_SCHEMA_VERSION=TELEMETRY_SCHEMA_VERSION),
        encoding="utf-8",
    )
    return collector, semantic_map


def _versions(name: str) -> str:
    versions = {
        "opentelemetry-api": "1.44.0",
        "opentelemetry-sdk": "1.44.0",
        "opentelemetry-exporter-otlp": "1.44.0",
        "openinference-semantic-conventions": "0.1.31",
    }
    if name not in versions:
        from importlib.metadata import PackageNotFoundError

        raise PackageNotFoundError(name)
    return versions[name]


def test_bootstrap_verification_persists_safe_evidence_and_validates_config(tmp_path: Path) -> None:
    collector, semantic_map = _write_config(tmp_path)
    archive = tmp_path / COLLECTOR_ARCHIVE_NAME
    archive.write_bytes(b"fake archive")
    store = InMemoryArtifactStore()
    result = verify_telemetry_bootstrap(
        store,
        archive_path=archive,
        collector_config_path=collector,
        semantic_map_path=semantic_map,
        version_provider=_versions,
        archive_verifier=lambda path: CollectorArchive(path, "a" * 64),
    )
    assert result.evidence == store.put_bytes(
        store.open_verified(result.evidence),
        media_type="application/json",
        classification="telemetry_bootstrap_verification",
    )
    assert b"fake archive" not in store.open_verified(result.evidence)


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda collector, semantic: collector.unlink(),
            "telemetry_configuration_missing_or_unsafe",
        ),
        (
            lambda collector, semantic: collector.write_text("receivers: {}", encoding="utf-8"),
            "collector_configuration_mismatch",
        ),
        (
            lambda collector, semantic: semantic.write_text("mapping: {}", encoding="utf-8"),
            "semantic_map_configuration_mismatch",
        ),
    ],
)
def test_bootstrap_verification_fails_closed_on_config_drift(
    tmp_path: Path,
    mutate: Callable[[Path, Path], None],
    code: str,
) -> None:
    collector, semantic_map = _write_config(tmp_path)
    archive = tmp_path / COLLECTOR_ARCHIVE_NAME
    archive.write_bytes(b"fake archive")
    mutate(collector, semantic_map)
    with pytest.raises(TelemetryConformanceError) as error:
        verify_telemetry_bootstrap(
            InMemoryArtifactStore(),
            archive_path=archive,
            collector_config_path=collector,
            semantic_map_path=semantic_map,
            version_provider=_versions,
            archive_verifier=lambda path: CollectorArchive(path, "a" * 64),
        )
    assert error.value.code == code


def test_cli_bootstrap_runs_telemetry_before_runtime_and_retains_evidence(tmp_path: Path) -> None:
    root = tmp_path / "evaluation"
    (root / "collector").mkdir(parents=True)
    _write_config(root / "collector")
    (root / "uv.lock").write_text("", encoding="utf-8")
    archive = tmp_path / COLLECTOR_ARCHIVE_NAME
    archive.write_bytes(b"fake archive")
    calls: list[str] = []
    evidence = InMemoryArtifactStore().put_bytes(
        b"telemetry verification", media_type="application/json", classification="test"
    )

    def verify(*args: object) -> object:
        del args
        calls.append("telemetry")
        return SimpleNamespace(evidence=evidence)

    def runtime(*args: object) -> None:
        del args
        calls.append("runtime")

    result = bootstrap(
        Namespace(backup_root=str(tmp_path), collector_archive=str(archive)),
        evaluation_root=root,
        telemetry_verifier=verify,
        runtime_bootstrap=runtime,
        backup_root_validator=lambda path: None,
    )
    assert result == 0
    assert calls == ["telemetry", "runtime"]


def test_archive_tamper_and_version_mismatch_fail_before_evidence_persistence(
    tmp_path: Path,
) -> None:
    collector, semantic_map = _write_config(tmp_path)
    archive = tmp_path / COLLECTOR_ARCHIVE_NAME
    archive.write_bytes(b"tampered")
    with pytest.raises(TelemetryConformanceError) as archive_error:
        verify_telemetry_bootstrap(
            InMemoryArtifactStore(),
            archive_path=archive,
            collector_config_path=collector,
            semantic_map_path=semantic_map,
            version_provider=_versions,
        )
    assert archive_error.value.code == "collector_archive_digest_mismatch"

    with pytest.raises(TelemetryConformanceError) as version_error:
        verify_telemetry_bootstrap(
            InMemoryArtifactStore(),
            archive_path=archive,
            collector_config_path=collector,
            semantic_map_path=semantic_map,
            version_provider=lambda name: "9.9.9",
            archive_verifier=lambda path: CollectorArchive(path, "a" * 64),
        )
    assert version_error.value.code == "telemetry_dependency_version_mismatch"
