"""Local Collector verification and an OTLP-only telemetry adapter."""

from __future__ import annotations

import hashlib
import importlib.metadata
import os
import subprocess
import tarfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import Lock

import yaml

from memrelay_eval.canonical import canonical_bytes, canonical_digest
from memrelay_eval.domain.entities import ArtifactRef
from memrelay_eval.domain.errors import TelemetryConformanceError
from memrelay_eval.domain.ports import ArtifactStorePort

from .semantics import (
    GENAI_DEVELOPMENT_FIELD_MAP,
    GENAI_MAP_VERSION,
    TELEMETRY_SCHEMA_VERSION,
    TelemetrySpan,
)

COLLECTOR_VERSION = "0.158.0"
COLLECTOR_ARCHIVE_NAME = "otelcol-contrib_0.158.0_windows_amd64.tar.gz"
COLLECTOR_ARCHIVE_SHA256 = "4314abde3c8acc67af58bb8d7611aa991fd80abe4a412695167f956d9fff3005"
_REQUIRED_DISTRIBUTIONS = {
    "opentelemetry-api": "1.44.0",
    "opentelemetry-sdk": "1.44.0",
    "opentelemetry-exporter-otlp": "1.44.0",
    "openinference-semantic-conventions": "0.1.31",
}
_CONDITIONAL_OPENAI_INSTRUMENTATION = "openinference-instrumentation-openai"
_CONDITIONAL_OPENAI_INSTRUMENTATION_VERSION = "0.1.53"


@dataclass(frozen=True, slots=True)
class CollectorArchive:
    path: Path
    sha256: str
    version: str = COLLECTOR_VERSION


def verify_collector_archive(path: Path) -> CollectorArchive:
    """Accept only the frozen Windows archive and retain no permissive fallback."""

    if path.name != COLLECTOR_ARCHIVE_NAME or path.is_symlink() or not path.is_file():
        raise TelemetryConformanceError("collector_archive_missing_or_mismatched")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != COLLECTOR_ARCHIVE_SHA256:
        raise TelemetryConformanceError("collector_archive_digest_mismatch")
    return CollectorArchive(path=path, sha256=digest)


def persist_collector_verification(
    store: ArtifactStorePort, archive: CollectorArchive
) -> ArtifactRef:
    """Preserve a value-safe archive verification record through the immutable CAS."""

    return store.put_bytes(
        canonical_bytes(
            {
                "collector_version": archive.version,
                "archive_name": COLLECTOR_ARCHIVE_NAME,
                "archive_sha256": archive.sha256,
            }
        ),
        media_type="application/json",
        classification="collector_verification",
    )


@dataclass(frozen=True, slots=True)
class TelemetryBootstrapVerification:
    """Value-safe proof required before a runtime bootstrap can succeed."""

    collector: CollectorArchive
    collector_config_sha256: str
    semantic_map_sha256: str
    dependency_versions: dict[str, str]
    evidence: ArtifactRef


def verify_telemetry_bootstrap(
    store: ArtifactStorePort,
    *,
    archive_path: Path,
    collector_config_path: Path,
    semantic_map_path: Path,
    version_provider: Callable[[str], str] = importlib.metadata.version,
    archive_verifier: Callable[[Path], CollectorArchive] = verify_collector_archive,
) -> TelemetryBootstrapVerification:
    """Fail closed on telemetry substrate drift and retain its immutable CAS evidence."""

    collector = archive_verifier(archive_path)
    config = _read_yaml_mapping(collector_config_path, "collector_config")
    semantic_map = _read_yaml_mapping(semantic_map_path, "semantic_map")
    _require_collector_config(config)
    _require_semantic_map(semantic_map)
    versions: dict[str, str] = {}
    for distribution, expected in _REQUIRED_DISTRIBUTIONS.items():
        try:
            actual = version_provider(distribution)
        except importlib.metadata.PackageNotFoundError as error:
            raise TelemetryConformanceError(
                "telemetry_dependency_missing", (distribution,)
            ) from error
        if actual != expected:
            raise TelemetryConformanceError(
                "telemetry_dependency_version_mismatch", (distribution,)
            )
        versions[distribution] = actual
    try:
        conditional_version = version_provider(_CONDITIONAL_OPENAI_INSTRUMENTATION)
    except importlib.metadata.PackageNotFoundError:
        conditional_version = None
    if conditional_version is not None:
        if conditional_version != _CONDITIONAL_OPENAI_INSTRUMENTATION_VERSION:
            raise TelemetryConformanceError(
                "telemetry_dependency_version_mismatch",
                (_CONDITIONAL_OPENAI_INSTRUMENTATION,),
            )
        versions[_CONDITIONAL_OPENAI_INSTRUMENTATION] = conditional_version
    config_sha256 = canonical_digest(config)
    semantic_map_sha256 = canonical_digest(semantic_map)
    evidence = store.put_bytes(
        canonical_bytes(
            {
                "schema_version": TELEMETRY_SCHEMA_VERSION,
                "collector_version": collector.version,
                "collector_archive_sha256": collector.sha256,
                "collector_config_sha256": config_sha256,
                "semantic_map_sha256": semantic_map_sha256,
                "dependency_versions": dict(sorted(versions.items())),
            }
        ),
        media_type="application/json",
        classification="telemetry_bootstrap_verification",
    )
    return TelemetryBootstrapVerification(
        collector=collector,
        collector_config_sha256=config_sha256,
        semantic_map_sha256=semantic_map_sha256,
        dependency_versions=versions,
        evidence=evidence,
    )


def _read_yaml_mapping(path: Path, field: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise TelemetryConformanceError("telemetry_configuration_missing_or_unsafe", (field,))
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise TelemetryConformanceError("telemetry_configuration_invalid", (field,)) from error
    if not isinstance(loaded, dict):
        raise TelemetryConformanceError("telemetry_configuration_invalid", (field,))
    return loaded


def _require_collector_config(config: dict[str, object]) -> None:
    expected = {
        "receivers": {"otlp": {"protocols": {"grpc": {}, "http": {}}}},
        "processors": {"batch": {}},
        "exporters": {"file": {"path": "${env:MEMRELAY_EVAL_OTLP_EXPORT_PATH}"}},
        "service": {
            "pipelines": {
                "traces": {
                    "receivers": ["otlp"],
                    "processors": ["batch"],
                    "exporters": ["file"],
                }
            }
        },
    }
    if config != expected:
        raise TelemetryConformanceError("collector_configuration_mismatch")


def _require_semantic_map(semantic_map: dict[str, object]) -> None:
    expected = {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "genai_map_version": GENAI_MAP_VERSION,
        "source": "otel-genai-development",
        "mapping": GENAI_DEVELOPMENT_FIELD_MAP,
    }
    if semantic_map != expected:
        raise TelemetryConformanceError("semantic_map_configuration_mismatch")


def extract_verified_collector(archive: CollectorArchive, destination: Path) -> Path:
    """Extract only the expected executable after the immutable archive verification."""

    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive.path, "r:gz") as bundle:
        members = bundle.getmembers()
        if any(
            member.name.startswith(("/", "\\")) or ".." in Path(member.name).parts
            for member in members
        ):
            raise TelemetryConformanceError("collector_archive_unsafe_member")
        matches = [member for member in members if Path(member.name).name == "otelcol-contrib.exe"]
        if len(matches) != 1 or not matches[0].isfile():
            raise TelemetryConformanceError("collector_executable_missing")
        source = bundle.extractfile(matches[0])
        if source is None:
            raise TelemetryConformanceError("collector_executable_missing")
        target = destination / "otelcol-contrib.exe"
        target.write_bytes(source.read())
    return target


class CollectorLifecycle:
    """Owns exactly one short-lived local Collector process per invocation."""

    def __init__(
        self,
        executable: Path,
        config: Path,
        *,
        starter: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self._executable = executable
        self._config = config
        self._starter = starter
        self._process: subprocess.Popen[bytes] | None = None

    def start(self) -> None:
        if (
            self._process is not None
            or not self._executable.is_file()
            or not self._config.is_file()
        ):
            raise TelemetryConformanceError("collector_start_invalid")
        environment = {
            "PATH": os.environ.get("SYSTEMROOT", ""),
            "MEMRELAY_EVAL_OTLP_EXPORT_PATH": os.environ.get("MEMRELAY_EVAL_OTLP_EXPORT_PATH", ""),
        }
        self._process = self._starter(
            [str(self._executable), "--config", str(self._config)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )

    def shutdown(self, timeout_seconds: float) -> bool:
        if self._process is None or timeout_seconds < 0:
            raise TelemetryConformanceError("collector_shutdown_invalid")
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
            return False
        return self._process.returncode == 0


class OtelTelemetry:
    """Adapter-only OpenTelemetry emission; lifecycle truth remains outside telemetry."""

    provenance = "collector_otlp_pending_conformance"
    eligible_for_paid_or_study = False

    def __init__(
        self,
        endpoint: str,
        *,
        exporter: Callable[[Sequence[TelemetrySpan], float], bool] | None = None,
    ) -> None:
        if not endpoint.startswith("http://127.0.0.1:") and not endpoint.startswith(
            "http://localhost:"
        ):
            raise TelemetryConformanceError("collector_endpoint_must_be_local")
        self._endpoint = endpoint
        self._spans: list[TelemetrySpan] = []
        self._lock = Lock()
        self._exported_count = 0
        self._exporter = exporter or self._make_otlp_exporter(endpoint)

    def emit_span(self, span: TelemetrySpan) -> None:
        """Retain the canonical projection before exporting through the local topology."""

        with self._lock:
            self._spans.append(span)

    @property
    def spans(self) -> tuple[TelemetrySpan, ...]:
        with self._lock:
            return tuple(self._spans)

    def flush(self, timeout_seconds: float) -> dict[str, object]:
        if timeout_seconds < 0:
            raise TelemetryConformanceError("telemetry_flush_timeout_invalid")
        with self._lock:
            pending = tuple(self._spans[self._exported_count :])
            if pending and not self._exporter(pending, timeout_seconds):
                raise TelemetryConformanceError("telemetry_export_failed")
            self._exported_count = len(self._spans)
        return {
            "flushed": len(pending),
            "endpoint": self._endpoint,
            "provenance": self.provenance,
        }

    @staticmethod
    def _make_otlp_exporter(endpoint: str) -> Callable[[Sequence[TelemetrySpan], float], bool]:
        """Load pinned OpenTelemetry types only at the concrete adapter boundary."""

        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import SimpleSpanProcessor
            from opentelemetry.trace import Link, SpanContext, TraceFlags
        except ImportError as error:
            raise TelemetryConformanceError("otel_dependencies_unavailable") from error

        provider = TracerProvider()
        provider.add_span_processor(
            SimpleSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
        )
        tracer = provider.get_tracer("memrelay.eval.telemetry", "1.0.0")

        def export(spans: Sequence[TelemetrySpan], timeout_seconds: float) -> bool:
            for semantic_span in spans:
                links = [
                    Link(
                        SpanContext(
                            trace_id=_opaque_trace_id(link.correlation_id),
                            span_id=_opaque_span_id(link.span_id),
                            is_remote=True,
                            trace_flags=TraceFlags(TraceFlags.SAMPLED),
                        ),
                        {"memrelay.eval.opaque_correlation_id": link.correlation_id},
                    )
                    for link in semantic_span.links
                ]
                span = tracer.start_span(semantic_span.span_class.value, links=links)
                for key, value in semantic_span.context.attributes().items():
                    span.set_attribute(key, value)
                for key, value in semantic_span.attributes.items():
                    span.set_attribute(f"memrelay.eval.{key}", value)
                span.end(end_time=int(semantic_span.ended_at.timestamp() * 1_000_000_000))
            return provider.force_flush(timeout_millis=int(timeout_seconds * 1000))

        return export


def _opaque_trace_id(value: str) -> int:
    return int(sha256(f"trace:{value}".encode()).hexdigest()[:32], 16)


def _opaque_span_id(value: str) -> int:
    return int(sha256(f"span:{value}".encode()).hexdigest()[:16], 16)
