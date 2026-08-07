"""Explicit, redacted evaluator configuration resolution."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from memrelay_eval.canonical import attach_digest, canonical_bytes
from memrelay_eval.domain.entities import EffectiveConfigurationArtifact
from memrelay_eval.domain.errors import (
    AmbiguousConfigurationKeyError,
    InvalidConfigurationError,
    SecretConfigurationError,
    UnknownConfigurationKeyError,
)
from memrelay_eval.domain.ids import ConfigurationId
from memrelay_eval.domain.policies import require_no_secret_values
from memrelay_eval.domain.ports import ArtifactStorePort

EFFECTIVE_CONFIGURATION_SCHEMA_VERSION = "1.0.0"
CONFIGURATION_PRECEDENCE = ("cli", "protocol_stage", "evaluator_file", "safe_default")
SAFE_DEFAULTS: Mapping[str, object] = MappingProxyType(
    {
        "stage": "conformance",
        "network_policy": {"mode": "deny"},
        "timeout_seconds": 600,
        "max_concurrency": 1,
        "credential_references": [],
    }
)
_KNOWN_KEYS = frozenset(
    {
        "stage",
        "protocol_version",
        "configuration_version",
        "network_policy",
        "timeout_seconds",
        "max_concurrency",
        "limits",
        "thresholds",
        "threshold",
        "endpoints",
        "endpoint",
        "stage_rules",
        "stage_rule",
        "model_selection",
        "assignment_algorithm",
        "credential_references",
    }
)
_CREDENTIAL_VARIABLE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_TARGET_PROCESS = re.compile(r"^[a-z][a-z0-9_]{2,127}$")
_CREDENTIAL_TARGET_PROCESSES = frozenset(
    {"copilot_sdk_worker", "memrelay_framework_daemon", "agentic_judge_worker"}
)


@dataclass(frozen=True, slots=True)
class CredentialReference:
    """A named environment injection point, deliberately without a value."""

    variable_name: str
    target_process: str

    def __post_init__(self) -> None:
        if not _CREDENTIAL_VARIABLE.fullmatch(self.variable_name):
            raise InvalidConfigurationError()
        if (
            not _TARGET_PROCESS.fullmatch(self.target_process)
            or self.target_process not in _CREDENTIAL_TARGET_PROCESSES
        ):
            raise InvalidConfigurationError()

    def to_marker(self) -> dict[str, object]:
        return {
            "kind": "credential_reference",
            "redacted": True,
            "variable_name": self.variable_name,
            "target_process": self.target_process,
        }


@dataclass(frozen=True, slots=True)
class EffectiveConfiguration:
    """A resolved non-secret configuration with one provenance entry per field."""

    values: Mapping[str, object]
    provenance: Mapping[str, str]

    def __post_init__(self) -> None:
        if set(self.values) != set(self.provenance):
            raise InvalidConfigurationError()
        if not self.values:
            raise InvalidConfigurationError()
        object.__setattr__(self, "values", MappingProxyType(dict(self.values)))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))

    def to_document(self) -> dict[str, object]:
        fields: dict[str, object] = {}
        for key in sorted(self.values):
            value = self.values[key]
            fields[key] = {
                "value": _redacted_value(key, value),
                "provenance": self.provenance[key],
            }
        return attach_digest(
            {
                "artifact_type": "effective_configuration",
                "schema_version": EFFECTIVE_CONFIGURATION_SCHEMA_VERSION,
                "fields": fields,
            }
        )

    @property
    def digest(self) -> str:
        return str(self.to_document()["digest"])


def load_evaluator_toml(path: Path) -> dict[str, object]:
    """Load only the explicit evaluator TOML layer; environment is never consulted."""
    try:
        with path.open("rb") as source:
            decoded = tomllib.load(source)
    except tomllib.TOMLDecodeError as error:
        if "overwrite" in str(error).casefold():
            raise AmbiguousConfigurationKeyError() from error
        raise InvalidConfigurationError() from error
    except OSError as error:
        raise InvalidConfigurationError() from error
    if not isinstance(decoded, dict):
        raise InvalidConfigurationError()
    return dict(decoded)


def resolve_effective_configuration(
    *,
    cli: Mapping[str, object] | None = None,
    protocol_stage: Mapping[str, object] | None = None,
    evaluator_file: Mapping[str, object] | None = None,
    safe_defaults: Mapping[str, object] | None = None,
) -> EffectiveConfiguration:
    """Resolve exactly CLI > protocol/stage > TOML > safe defaults."""
    defaults = dict(SAFE_DEFAULTS if safe_defaults is None else safe_defaults)
    sources = (
        ("cli", cli or {}, True),
        ("protocol_stage", protocol_stage or {}, False),
        ("evaluator_file", evaluator_file or {}, False),
        ("safe_default", defaults, False),
    )
    normalized = {
        name: _normalize_source(values, allow_none_as_absent=allow_none_as_absent)
        for name, values, allow_none_as_absent in sources
    }
    values: dict[str, object] = {}
    provenance: dict[str, str] = {}
    for key in sorted(_KNOWN_KEYS):
        for source in CONFIGURATION_PRECEDENCE:
            if key in normalized[source]:
                values[key] = normalized[source][key]
                provenance[key] = source
                break
    if not values:
        raise InvalidConfigurationError()
    return EffectiveConfiguration(values, provenance)


def resolve_configuration(
    *,
    cli: Mapping[str, object] | None = None,
    protocol_stage: Mapping[str, object] | None = None,
    evaluator_file: Mapping[str, object] | None = None,
    safe_defaults: Mapping[str, object] | None = None,
) -> EffectiveConfiguration:
    """Compatibility name for the explicit configuration resolver."""
    return resolve_effective_configuration(
        cli=cli,
        protocol_stage=protocol_stage,
        evaluator_file=evaluator_file,
        safe_defaults=safe_defaults,
    )


def persist_effective_configuration(
    configuration: EffectiveConfiguration, artifact_store: ArtifactStorePort
) -> EffectiveConfigurationArtifact:
    """Write canonical redacted bytes through the evaluator's sole artifact port."""
    require_no_secret_values(
        {
            key: value
            for key, value in configuration.values.items()
            if key != "credential_references"
        }
    )
    document = configuration.to_document()
    artifact = artifact_store.put_bytes(
        canonical_bytes(document),
        media_type="application/json",
        classification="effective_configuration",
    )
    return EffectiveConfigurationArtifact(
        ConfigurationId.from_digest(configuration.digest),
        artifact,
        configuration.digest,
    )


def _normalize_source(
    values: Mapping[str, object], *, allow_none_as_absent: bool
) -> dict[str, object]:
    if not isinstance(values, Mapping):
        raise InvalidConfigurationError()
    normalized: dict[str, object] = {}
    for key, value in values.items():
        if not isinstance(key, str):
            raise AmbiguousConfigurationKeyError()
        if "." in key or key.strip() != key or not key:
            raise AmbiguousConfigurationKeyError()
        if key not in _KNOWN_KEYS:
            raise UnknownConfigurationKeyError()
        if value is None and allow_none_as_absent:
            continue
        if value is None:
            raise InvalidConfigurationError()
        if key in normalized:
            raise AmbiguousConfigurationKeyError()
        normalized[key] = _validate_value(key, value)
    return normalized


def _validate_value(key: str, value: object) -> object:
    if key in {"stage", "protocol_version", "configuration_version"}:
        if not isinstance(value, str) or not value:
            raise InvalidConfigurationError()
    elif key == "timeout_seconds" or key == "max_concurrency":
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise InvalidConfigurationError()
    elif key == "credential_references":
        return tuple(_credential_reference(item) for item in _sequence(value))
    elif key == "endpoint":
        if not isinstance(value, str) or not value or "@" in value:
            raise InvalidConfigurationError()
    elif key == "threshold":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise InvalidConfigurationError()
    elif key in {
        "network_policy",
        "limits",
        "thresholds",
        "endpoints",
        "stage_rules",
        "stage_rule",
        "model_selection",
        "assignment_algorithm",
    }:
        if not isinstance(value, Mapping) or not value:
            raise InvalidConfigurationError()
    else:
        raise UnknownConfigurationKeyError()
    try:
        require_no_secret_values({key: value})
        _reject_mutable_path_values(value)
        canonical_bytes(value)
    except SecretConfigurationError:
        raise
    except Exception as error:
        raise InvalidConfigurationError() from error
    return _immutable_value(value)


def _credential_reference(value: object) -> CredentialReference:
    if not isinstance(value, Mapping) or set(value) != {"variable_name", "target_process"}:
        raise InvalidConfigurationError()
    variable_name = value["variable_name"]
    target_process = value["target_process"]
    if not isinstance(variable_name, str) or not isinstance(target_process, str):
        raise InvalidConfigurationError()
    return CredentialReference(variable_name, target_process)


def _sequence(value: object) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise InvalidConfigurationError()
    return value


def _immutable_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _immutable_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_immutable_value(item) for item in value)
    return value


def _redacted_value(key: str, value: object) -> object:
    if key == "credential_references":
        return [reference.to_marker() for reference in _sequence(value)]
    if isinstance(value, Mapping):
        return {name: _redacted_value(name, item) for name, item in value.items()}
    if isinstance(value, tuple):
        return [_redacted_value(key, item) for item in value]
    return value


def _reject_mutable_path_values(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if (
                not isinstance(key, str)
                or key == "path"
                or key.endswith("_path")
                or key.endswith("_root")
            ):
                raise InvalidConfigurationError()
            _reject_mutable_path_values(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            _reject_mutable_path_values(nested)
