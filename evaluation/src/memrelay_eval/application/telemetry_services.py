"""Application composition for verified local telemetry bootstrap."""

from __future__ import annotations

from pathlib import Path

from memrelay_eval.adapters.artifacts.filesystem import FilesystemArtifactStore
from memrelay_eval.adapters.telemetry.otel import (
    COLLECTOR_ARCHIVE_NAME,
    TelemetryBootstrapVerification,
    verify_telemetry_bootstrap,
)

DEFAULT_COLLECTOR_ARCHIVE_NAME = COLLECTOR_ARCHIVE_NAME


def verify_local_telemetry_bootstrap(
    evaluation_root: Path, archive_path: Path
) -> TelemetryBootstrapVerification:
    """Compose durable bootstrap evidence without exposing adapter imports to the CLI."""

    return verify_telemetry_bootstrap(
        FilesystemArtifactStore(evaluation_root / "artifacts" / "telemetry"),
        archive_path=archive_path,
        collector_config_path=evaluation_root / "collector" / "collector.yaml",
        semantic_map_path=evaluation_root / "collector" / "semantic-map.yaml",
    )
