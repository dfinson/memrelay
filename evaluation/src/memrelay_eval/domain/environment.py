"""Immutable environment fingerprint records for assignment blocking."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from memrelay_eval.canonical import attach_digest, canonical_bytes

from .entities import (
    EnvironmentStratum,
    FrozenArtifactInput,
)
from .errors import InvalidConfigurationError
from .ids import EnvironmentStratumId
from .policies import require_no_secret_values
from .ports import ArtifactStorePort

ENVIRONMENT_FINGERPRINT_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class EnvironmentFingerprint:
    """All host dimensions that may otherwise confound an assignment block."""

    os_name: str
    os_build: str
    cpu: Mapping[str, object]
    memory: Mapping[str, object]
    storage_class: str
    power_mode: str
    python_version: str
    runtime_version: str
    process_limits: Mapping[str, object]
    network_policy: Mapping[str, object]
    background_load_policy: Mapping[str, object]

    def __post_init__(self) -> None:
        scalar_values = (
            self.os_name,
            self.os_build,
            self.storage_class,
            self.power_mode,
            self.python_version,
            self.runtime_version,
        )
        if any(not isinstance(value, str) or not value for value in scalar_values):
            raise InvalidConfigurationError()
        mappings = (
            self.cpu,
            self.memory,
            self.process_limits,
            self.network_policy,
            self.background_load_policy,
        )
        if any(not isinstance(value, Mapping) or not value for value in mappings):
            raise InvalidConfigurationError()
        for value in mappings:
            require_no_secret_values(value)
        object.__setattr__(self, "cpu", MappingProxyType(dict(self.cpu)))
        object.__setattr__(self, "memory", MappingProxyType(dict(self.memory)))
        object.__setattr__(self, "process_limits", MappingProxyType(dict(self.process_limits)))
        object.__setattr__(self, "network_policy", MappingProxyType(dict(self.network_policy)))
        object.__setattr__(
            self,
            "background_load_policy",
            MappingProxyType(dict(self.background_load_policy)),
        )

    def to_document(self) -> dict[str, object]:
        return attach_digest(
            {
                "artifact_type": "environment_fingerprint",
                "schema_version": ENVIRONMENT_FINGERPRINT_SCHEMA_VERSION,
                "os": {"name": self.os_name, "build": self.os_build},
                "cpu": dict(self.cpu),
                "memory": dict(self.memory),
                "storage_class": self.storage_class,
                "power_mode": self.power_mode,
                "python_version": self.python_version,
                "runtime_version": self.runtime_version,
                "process_limits": dict(self.process_limits),
                "network_policy": dict(self.network_policy),
                "background_load_policy": dict(self.background_load_policy),
            }
        )

    @property
    def digest(self) -> str:
        return str(self.to_document()["digest"])

    @property
    def stratum(self) -> EnvironmentStratum:
        return EnvironmentStratum(EnvironmentStratumId.from_digest(self.digest), self.digest)


def persist_environment_fingerprint(
    fingerprint: EnvironmentFingerprint,
    artifact_store: ArtifactStorePort,
    *,
    provenance: str = "captured_environment",
) -> FrozenArtifactInput:
    """Persist canonical non-secret fingerprint evidence through the sole store port."""
    if not provenance:
        raise InvalidConfigurationError()
    artifact = artifact_store.put_bytes(
        canonical_bytes(fingerprint.to_document()),
        media_type="application/json",
        classification="environment_fingerprint",
    )
    return FrozenArtifactInput(
        artifact,
        schema_version=ENVIRONMENT_FINGERPRINT_SCHEMA_VERSION,
        provenance=provenance,
    )
