from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from memrelay_eval.adapters.telemetry.otel import CollectorLifecycle, verify_collector_archive
from memrelay_eval.domain.errors import TelemetryConformanceError


class _TimeoutProcess:
    returncode = None

    def terminate(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired("collector", timeout)
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def test_collector_archive_rejects_missing_and_wrong_hash(tmp_path: Path) -> None:
    with pytest.raises(TelemetryConformanceError) as missing:
        verify_collector_archive(tmp_path / "missing.tar.gz")
    assert missing.value.code == "collector_archive_missing_or_mismatched"
    archive = tmp_path / "otelcol-contrib_0.158.0_windows_amd64.tar.gz"
    archive.write_bytes(b"not the frozen collector")
    with pytest.raises(TelemetryConformanceError) as mismatch:
        verify_collector_archive(archive)
    assert mismatch.value.code == "collector_archive_digest_mismatch"


def test_collector_timeout_is_a_visible_shutdown_failure(tmp_path: Path) -> None:
    executable = tmp_path / "otelcol-contrib.exe"
    executable.write_bytes(b"fake")
    config = tmp_path / "collector.yaml"
    config.write_text("receivers: {}\n", encoding="utf-8")

    def start(*args: object, **kwargs: object) -> _TimeoutProcess:
        del args, kwargs
        return _TimeoutProcess()

    lifecycle = CollectorLifecycle(executable, config, starter=start)  # type: ignore[arg-type]
    lifecycle.start()
    assert lifecycle.shutdown(0) is False
