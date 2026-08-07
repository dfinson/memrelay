"""Immutable environment fingerprint records for assignment blocking."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from memrelay_eval.canonical import attach_digest, canonical_bytes, canonical_digest

from .entities import (
    EnvironmentStratum,
    FrozenArtifactInput,
    NativeModel,
    RuntimeIdentity,
)
from .errors import (
    AgentParityMismatchError,
    EnvironmentStratumChangedError,
    InvalidConfigurationError,
)
from .ids import EnvironmentStratumId, ProtocolId
from .policies import require_no_secret_values, require_treatment_neutral
from .ports import ArtifactStorePort

ENVIRONMENT_FINGERPRINT_SCHEMA_VERSION = "1.0.0"
AGENT_PARITY_SCHEMA_VERSION = "1.0.0"
_SHA256 = re.compile(r"^[a-f0-9]{64}$")


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


@dataclass(frozen=True, slots=True)
class PromptByteHashes:
    """Hashes exact prompt components without retaining prompt text."""

    common_bytes_sha256: str
    allowed_delta_bytes_sha256: str

    def __post_init__(self) -> None:
        _require_sha256(self.common_bytes_sha256)
        _require_sha256(self.allowed_delta_bytes_sha256)

    @classmethod
    def from_bytes(cls, common: bytes, allowed_delta: bytes = b"") -> PromptByteHashes:
        if not isinstance(common, bytes) or not isinstance(allowed_delta, bytes):
            raise InvalidConfigurationError()
        from hashlib import sha256

        return cls(sha256(common).hexdigest(), sha256(allowed_delta).hexdigest())

    def to_document(self) -> dict[str, str]:
        return {
            "common_bytes_sha256": self.common_bytes_sha256,
            "allowed_delta_bytes_sha256": self.allowed_delta_bytes_sha256,
        }

    def neutral_document(self) -> dict[str, str]:
        return {"common_bytes_sha256": self.common_bytes_sha256}


@dataclass(frozen=True, slots=True)
class ProtocolDeltaAllowance:
    """Opaque protocol evidence for the only two intervention-specific deltas."""

    protocol_projection_sha256: str
    system_prompt_delta_sha256: str
    user_prompt_delta_sha256: str
    access_delta_sha256: str

    def __post_init__(self) -> None:
        for digest in (
            self.protocol_projection_sha256,
            self.system_prompt_delta_sha256,
            self.user_prompt_delta_sha256,
            self.access_delta_sha256,
        ):
            _require_sha256(digest)


@dataclass(frozen=True, slots=True)
class AgentEnvironmentParityRecord:
    """Canonical pre-exposure execution substrate record for one opaque attempt."""

    runtime: RuntimeIdentity
    runtime_lock_sha256: str
    model_lock_sha256: str
    model: NativeModel
    system_prompt: PromptByteHashes
    user_prompt: PromptByteHashes
    tool_schemas: Mapping[str, object]
    permission_policy: Mapping[str, object]
    network_policy: Mapping[str, object]
    limits: Mapping[str, object]
    timeout_seconds: float
    workspace_layout: Mapping[str, object]
    built_in_memory_enabled: bool
    cross_session_store_enabled: bool
    retry_policy: Mapping[str, object]
    effective_configuration_digest: str
    environment_fingerprint_digest: str
    environment_stratum: EnvironmentStratum
    enrollment_parity_inputs_digest: str
    access_delta_sha256: str

    def __post_init__(self) -> None:
        for digest in (
            self.runtime_lock_sha256,
            self.model_lock_sha256,
            self.effective_configuration_digest,
            self.environment_fingerprint_digest,
            self.enrollment_parity_inputs_digest,
            self.access_delta_sha256,
        ):
            _require_sha256(digest)
        if self.environment_stratum.fingerprint_digest != self.environment_fingerprint_digest:
            raise InvalidConfigurationError()
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise InvalidConfigurationError()
        if not isinstance(self.built_in_memory_enabled, bool) or not isinstance(
            self.cross_session_store_enabled, bool
        ):
            raise InvalidConfigurationError()
        for name in (
            "tool_schemas",
            "permission_policy",
            "network_policy",
            "limits",
            "workspace_layout",
            "retry_policy",
        ):
            object.__setattr__(self, name, _freeze_mapping(getattr(self, name)))

    def to_document(self) -> dict[str, object]:
        return attach_digest(self._document(include_allowed_deltas=True))

    @property
    def digest(self) -> str:
        return str(self.to_document()["digest"])

    @property
    def neutral_digest(self) -> str:
        return canonical_digest(self._document(include_allowed_deltas=False))

    def neutral_document(self) -> dict[str, object]:
        return attach_digest(self._document(include_allowed_deltas=False))

    def _document(self, *, include_allowed_deltas: bool) -> dict[str, object]:
        system_prompt = (
            self.system_prompt.to_document()
            if include_allowed_deltas
            else self.system_prompt.neutral_document()
        )
        user_prompt = (
            self.user_prompt.to_document()
            if include_allowed_deltas
            else self.user_prompt.neutral_document()
        )
        document: dict[str, object] = {
            "artifact_type": "agent_environment_parity",
            "schema_version": AGENT_PARITY_SCHEMA_VERSION,
            "runtime": {
                "sdk_version": self.runtime.sdk_version,
                "wheel_filename": self.runtime.wheel_filename,
                "wheel_sha256": self.runtime.wheel_sha256,
                "runtime_version": self.runtime.runtime_version,
                "runtime_sha256": self.runtime.runtime_sha256,
                "transport": self.runtime.transport,
                "auth_mode": self.runtime.auth_mode,
                "subscription_identity_sha256": self.runtime.subscription_identity_sha256,
            },
            "runtime_lock_sha256": self.runtime_lock_sha256,
            "model_lock_sha256": self.model_lock_sha256,
            "model": {
                "native_id": self.model.native_id,
                "family": self.model.family,
                "capabilities": _thaw_value(self.model.capabilities),
                "reasoning_effort": self.model.reasoning_effort,
                "context_tier": self.model.context_tier,
            },
            "prompts": {"system": system_prompt, "user": user_prompt},
            "tool_schemas": _thaw_value(self.tool_schemas),
            "permission_policy": _thaw_value(self.permission_policy),
            "network_policy": _thaw_value(self.network_policy),
            "limits": _thaw_value(self.limits),
            "timeout_seconds": self.timeout_seconds,
            "workspace_layout": _thaw_value(self.workspace_layout),
            "built_in_memory_enabled": self.built_in_memory_enabled,
            "cross_session_store_enabled": self.cross_session_store_enabled,
            "retry_policy": _thaw_value(self.retry_policy),
            "effective_configuration_digest": self.effective_configuration_digest,
            "environment": {
                "fingerprint_sha256": self.environment_fingerprint_digest,
                "stratum_id": str(self.environment_stratum.id),
            },
            "enrollment_parity_inputs_digest": self.enrollment_parity_inputs_digest,
        }
        if include_allowed_deltas:
            document["access_delta_sha256"] = self.access_delta_sha256
        return document


@dataclass(frozen=True, slots=True)
class EnvironmentStratumLink:
    """An explicit protocol-to-host linkage that makes pooling checks possible."""

    protocol_id: ProtocolId
    stratum: EnvironmentStratum


def link_environment_stratum(
    protocol_id: ProtocolId, fingerprint: EnvironmentFingerprint
) -> EnvironmentStratumLink:
    """Bind a protocol to the exact host stratum used for its preflight."""
    return EnvironmentStratumLink(protocol_id, fingerprint.stratum)


def require_single_environment_stratum(
    links: Sequence[EnvironmentStratumLink],
) -> EnvironmentStratum:
    """Reject ordinary aggregation across different host fingerprints."""
    if not links:
        raise InvalidConfigurationError()
    strata = {link.stratum for link in links}
    if len(strata) != 1:
        raise EnvironmentStratumChangedError(EnvironmentStratumChangedError.code)
    return next(iter(strata))


def require_declared_delta(
    record: AgentEnvironmentParityRecord, allowance: ProtocolDeltaAllowance
) -> tuple[str, ...]:
    """Return stable mismatch fields when an opaque protocol has not declared a delta."""
    differences: list[str] = []
    if record.system_prompt.allowed_delta_bytes_sha256 != allowance.system_prompt_delta_sha256:
        differences.append("system_prompt_delta")
    if record.user_prompt.allowed_delta_bytes_sha256 != allowance.user_prompt_delta_sha256:
        differences.append("user_prompt_delta")
    if record.access_delta_sha256 != allowance.access_delta_sha256:
        differences.append("access_delta")
    return tuple(differences)


def verify_agent_environment_parity(
    left: AgentEnvironmentParityRecord,
    left_allowance: ProtocolDeltaAllowance,
    right: AgentEnvironmentParityRecord,
    right_allowance: ProtocolDeltaAllowance,
) -> tuple[str, ...]:
    """Return every pre-exposure mismatch without normalizing or substituting inputs."""
    differences = list(require_declared_delta(left, left_allowance))
    differences.extend(require_declared_delta(right, right_allowance))
    if left_allowance.protocol_projection_sha256 != right_allowance.protocol_projection_sha256:
        differences.append("protocol_delta_projection")

    left_document = left._document(include_allowed_deltas=False)
    right_document = right._document(include_allowed_deltas=False)
    for field in sorted(set(left_document) | set(right_document)):
        if left_document.get(field) != right_document.get(field):
            differences.append(field)
    return tuple(dict.fromkeys(differences))


def require_agent_environment_parity(
    left: AgentEnvironmentParityRecord,
    left_allowance: ProtocolDeltaAllowance,
    right: AgentEnvironmentParityRecord,
    right_allowance: ProtocolDeltaAllowance,
) -> None:
    """Raise the typed denial used before task delivery or inference."""
    if differences := verify_agent_environment_parity(left, left_allowance, right, right_allowance):
        raise AgentParityMismatchError("agent_environment_parity_mismatch", differences)


def _require_sha256(value: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise InvalidConfigurationError()


def _freeze_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not value:
        raise InvalidConfigurationError()
    require_no_secret_values(value)
    require_treatment_neutral(value)
    try:
        canonical_bytes(value)
    except Exception as error:
        raise InvalidConfigurationError() from error
    return MappingProxyType({key: _freeze_value(nested) for key, nested in value.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise InvalidConfigurationError()
        return MappingProxyType({key: _freeze_value(nested) for key, nested in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(nested) for nested in value)
    return value


def _thaw_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_value(nested) for nested in value]
    return value
