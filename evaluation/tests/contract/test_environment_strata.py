from __future__ import annotations

import pytest
from memrelay_eval.domain.environment import (
    EnvironmentFingerprint,
    link_environment_stratum,
    require_single_environment_stratum,
)
from memrelay_eval.domain.errors import EnvironmentStratumChangedError
from memrelay_eval.domain.ids import ProtocolId


def _fingerprint(*, power_mode: str = "ac") -> EnvironmentFingerprint:
    return EnvironmentFingerprint(
        os_name="Windows",
        os_build="22631",
        cpu={"architecture": "x86_64", "logical_cores": 8},
        memory={"total_bytes": 17179869184},
        storage_class="local_ssd",
        power_mode=power_mode,
        python_version="3.13.0",
        runtime_version="cpython-3.13.0",
        process_limits={"max_workers": 1, "wall_seconds": 600},
        network_policy={"mode": "deny"},
        background_load_policy={"mode": "idle_only"},
    )


def test_matching_host_records_remain_in_one_explicit_environment_stratum() -> None:
    protocol = ProtocolId.new()
    fingerprint = _fingerprint()

    stratum = require_single_environment_stratum(
        (
            link_environment_stratum(protocol, fingerprint),
            link_environment_stratum(protocol, fingerprint),
        )
    )

    assert stratum == fingerprint.stratum


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("os_build", "22632"),
        ("storage_class", "network_ssd"),
        ("power_mode", "battery"),
        ("python_version", "3.13.1"),
    ),
)
def test_changed_fingerprint_cannot_be_aggregated_without_stratification(
    field: str, value: str
) -> None:
    original = _fingerprint()
    values = {
        "os_name": original.os_name,
        "os_build": original.os_build,
        "cpu": original.cpu,
        "memory": original.memory,
        "storage_class": original.storage_class,
        "power_mode": original.power_mode,
        "python_version": original.python_version,
        "runtime_version": original.runtime_version,
        "process_limits": original.process_limits,
        "network_policy": original.network_policy,
        "background_load_policy": original.background_load_policy,
    }
    values[field] = value
    changed = EnvironmentFingerprint(**values)
    protocol = ProtocolId.new()

    with pytest.raises(EnvironmentStratumChangedError):
        require_single_environment_stratum(
            (
                link_environment_stratum(protocol, original),
                link_environment_stratum(protocol, changed),
            )
        )

    assert original.stratum != changed.stratum
