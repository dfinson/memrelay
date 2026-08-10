"""Frozen provider, credential, resource, and ledger identity authority."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType

from .errors import AuthorityConflictError

PROVIDER_IDENTITY_SCHEMA_VERSION = "1.0.0"
_SAFE_CODE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """One canonical authority tuple; fields answer distinct questions."""

    service_name: str
    provider: str
    credential_domain: str
    cost_source: str
    resource_identity: str
    operation: str
    logical_ledger: str
    source_provider_label: str
    schema_version: str = PROVIDER_IDENTITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != PROVIDER_IDENTITY_SCHEMA_VERSION:
            raise AuthorityConflictError("authority_conflict", ("unknown_identity_schema_version",))
        fields = (
            self.service_name,
            self.provider,
            self.credential_domain,
            self.cost_source,
            self.resource_identity,
            self.operation,
            self.logical_ledger,
            self.source_provider_label,
        )
        if any(
            not isinstance(value, str) or not value.isascii() or not _SAFE_CODE.fullmatch(value)
            for value in fields
        ):
            raise AuthorityConflictError("authority_conflict", ("invalid_identity_field",))
        expected = _IDENTITY_MATRIX.get(
            (
                self.service_name,
                self.provider,
                self.credential_domain,
                self.cost_source,
                self.resource_identity,
                self.operation,
                self.logical_ledger,
            )
        )
        if expected != self.source_provider_label:
            raise AuthorityConflictError(
                "authority_conflict", ("provider_credential_resource_mismatch",)
            )

    def to_record(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "service_name": self.service_name,
            "provider": self.provider,
            "credential_domain": self.credential_domain,
            "cost_source": self.cost_source,
            "resource_identity": self.resource_identity,
            "operation": self.operation,
            "logical_ledger": self.logical_ledger,
            "source_provider_label": self.source_provider_label,
        }


_IDENTITY_ROWS = (
    (
        "memrelay.eval.copilot.worker",
        "github_copilot",
        "copilot_subscription",
        "copilot_subscription_usage",
        "github_copilot_sdk_session",
        "copilot_inference",
        "copilot_subscription",
        "github_copilot_sdk",
    ),
    (
        "memrelay.eval.framework.daemon",
        "openai",
        "framework_openai_api",
        "openai_api_metered",
        "framework_openai_client",
        "framework_inference",
        "framework_openai",
        "openai",
    ),
    (
        "memrelay.eval.local.resource",
        "local",
        "none",
        "local_resource",
        "local_embedding",
        "local_embedding",
        "local_resources",
        "local",
    ),
    (
        "memrelay.eval.local.resource",
        "local",
        "none",
        "local_resource",
        "local_cpu",
        "local_resource",
        "local_resources",
        "local",
    ),
    (
        "memrelay.eval.local.resource",
        "local",
        "none",
        "local_resource",
        "local_memory",
        "local_resource",
        "local_resources",
        "local",
    ),
    (
        "memrelay.eval.local.resource",
        "local",
        "none",
        "local_resource",
        "local_disk",
        "local_resource",
        "local_resources",
        "local",
    ),
    (
        "memrelay.eval.local.resource",
        "local",
        "none",
        "local_resource",
        "local_collector",
        "local_resource",
        "local_resources",
        "local",
    ),
    (
        "memrelay.eval.local.resource",
        "local",
        "none",
        "local_resource",
        "local_storage",
        "local_resource",
        "local_resources",
        "local",
    ),
    (
        "memrelay.eval.local.control",
        "local",
        "none",
        "local_resource",
        "local_control",
        "local_control",
        "local_resources",
        "local",
    ),
)
_IDENTITY_MATRIX = MappingProxyType({row[:-1]: row[-1] for row in _IDENTITY_ROWS})
IDENTITY_COMPATIBILITY_MATRIX = tuple(
    {
        "service_name": row[0],
        "provider": row[1],
        "credential_domain": row[2],
        "cost_source": row[3],
        "resource_identity": row[4],
        "operation": row[5],
        "logical_ledger": row[6],
        "source_provider_label": row[7],
    }
    for row in _IDENTITY_ROWS
)
_SOURCE_PROVIDER_ALIASES = MappingProxyType(
    {
        "github_copilot_sdk": "github_copilot",
        "openai": "openai",
        "local": "local",
    }
)


def source_provider_to_canonical(value: str) -> str:
    """Map only exact frozen source labels; aliases never cross an authority boundary."""
    try:
        return _SOURCE_PROVIDER_ALIASES[value]
    except KeyError as error:
        raise AuthorityConflictError(
            "authority_conflict", ("unknown_source_provider_label",)
        ) from error


def copilot_identity() -> ProviderIdentity:
    return ProviderIdentity(*_IDENTITY_ROWS[0][:-1], _IDENTITY_ROWS[0][-1])


def framework_openai_identity() -> ProviderIdentity:
    return ProviderIdentity(*_IDENTITY_ROWS[1][:-1], _IDENTITY_ROWS[1][-1])


def local_identity(resource_identity: str = "local_control") -> ProviderIdentity:
    for row in _IDENTITY_ROWS[2:]:
        if row[4] == resource_identity:
            return ProviderIdentity(*row[:-1], row[-1])
    raise AuthorityConflictError("authority_conflict", ("unknown_local_resource_identity",))


def identity_for_span_class(span_class: str) -> ProviderIdentity:
    """Return the sole authority tuple for a required span class."""
    if span_class in {"copilot.session", "copilot.model_request", "judge.adjudication"}:
        return copilot_identity()
    if span_class in {
        "daemon.dispatch",
        "memory.write",
        "memory.retrieval",
        "framework.extraction",
    }:
        return framework_openai_identity()
    if span_class == "framework.embedding":
        return local_identity("local_embedding")
    return local_identity()
