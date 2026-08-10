"""Process-specific credential allowlists and non-secret canary conformance."""

from __future__ import annotations

import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ...domain.errors import ProcessBoundaryConformanceError, ProcessEnvironmentError


class CredentialDomain(StrEnum):
    NONE = "none"
    COPILOT = "copilot_subscription"
    OPENAI = "openai_api"


class ProcessRole(StrEnum):
    INSPECT_CONTROL = "inspect_control"
    COPILOT_WORKER = "copilot_worker"
    MEMRELAY_DAEMON = "memrelay_daemon"
    DIRECT_ENGINE_WORKER = "direct_engine_worker"
    MCP_CLIENT = "mcp_client"
    GRADER = "grader"
    JUDGE = "judge"
    COLLECTOR = "collector"
    ANALYSIS = "analysis"


_ROLE_DOMAINS: Mapping[ProcessRole, CredentialDomain] = {
    ProcessRole.INSPECT_CONTROL: CredentialDomain.NONE,
    ProcessRole.COPILOT_WORKER: CredentialDomain.COPILOT,
    ProcessRole.MEMRELAY_DAEMON: CredentialDomain.OPENAI,
    ProcessRole.DIRECT_ENGINE_WORKER: CredentialDomain.OPENAI,
    ProcessRole.MCP_CLIENT: CredentialDomain.NONE,
    ProcessRole.GRADER: CredentialDomain.NONE,
    ProcessRole.JUDGE: CredentialDomain.COPILOT,
    ProcessRole.COLLECTOR: CredentialDomain.NONE,
    ProcessRole.ANALYSIS: CredentialDomain.NONE,
}
_CREDENTIAL_VARIABLE_DOMAINS: Mapping[str, CredentialDomain] = {
    "COPILOT_AUTH_TOKEN": CredentialDomain.COPILOT,
    "COPILOT_GITHUB_TOKEN": CredentialDomain.COPILOT,
    "GH_TOKEN": CredentialDomain.COPILOT,
    "GITHUB_TOKEN": CredentialDomain.COPILOT,
    "OPENAI_API_KEY": CredentialDomain.OPENAI,
}
_DAEMON_CONFIGURATION_NAMES = frozenset({"OPENAI_BASE_URL"})
_RUNTIME_BASELINE_NAMES = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "COMSPEC",
        "HOME",
        "LANG",
        "LC_ALL",
        "TEMP",
        "TMP",
        "MEMRELAY_HOME",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
    }
)


@dataclass(frozen=True, slots=True)
class CredentialReference:
    """A credential variable's named delivery boundary; its value is never evidence."""

    variable_name: str
    domain: CredentialDomain
    target_role: ProcessRole

    def __post_init__(self) -> None:
        expected = _CREDENTIAL_VARIABLE_DOMAINS.get(self.variable_name)
        if expected is None or expected is not self.domain:
            raise ProcessEnvironmentError("credential_variable_not_allowlisted")
        if _ROLE_DOMAINS[self.target_role] is not self.domain:
            raise ProcessEnvironmentError("credential_target_role_not_authorized")


@dataclass(frozen=True, slots=True)
class SyntheticCanary:
    """Synthetic probe material retained only in the launched child environment."""

    canary_id: str
    variable_name: str
    domain: CredentialDomain
    value: str

    @classmethod
    def create(cls, variable_name: str, domain: CredentialDomain) -> SyntheticCanary:
        identifier = uuid.uuid4().hex
        return cls(identifier, variable_name, domain, f"synthetic-canary-{identifier}")


@dataclass(frozen=True, slots=True)
class ProcessBoundaryEvidence:
    """A value-free projection of one canary observation at a process boundary."""

    role: ProcessRole
    canary_id: str
    location: str
    verdict: str


def credential_domain_for(role: ProcessRole) -> CredentialDomain:
    """Return the sole credential domain permitted for a process role."""
    return _ROLE_DOMAINS[role]


def build_process_environment(
    role: ProcessRole,
    *,
    runtime_environment: Mapping[str, str],
    credential_references: Sequence[CredentialReference] = (),
    credential_values: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build an environment from declared safe runtime names, never ``os.environ``."""
    environment: dict[str, str] = {}
    for name, value in runtime_environment.items():
        _validate_runtime_name(role, name, value)
        environment[name] = value

    references = tuple(credential_references)
    names = {reference.variable_name for reference in references}
    if len(names) != len(references):
        raise ProcessEnvironmentError("credential_reference_duplicated")
    values = credential_values or {}
    if set(values) != names:
        raise ProcessEnvironmentError("credential_value_reference_mismatch")

    allowed_domain = credential_domain_for(role)
    for reference in references:
        if reference.target_role is not role or reference.domain is not allowed_domain:
            raise ProcessEnvironmentError("credential_cross_boundary_denied")
        value = values[reference.variable_name]
        if not isinstance(value, str) or not value:
            raise ProcessEnvironmentError("credential_value_invalid")
        environment[reference.variable_name] = value
    return environment


def inject_synthetic_canaries(
    role: ProcessRole,
    environment: Mapping[str, str],
    canaries: Sequence[SyntheticCanary],
) -> dict[str, str]:
    """Add only authorized synthetic canaries to a disposable child environment."""
    result = dict(environment)
    allowed_domain = credential_domain_for(role)
    for canary in canaries:
        if canary.domain is allowed_domain:
            result[canary.variable_name] = canary.value
    return result


def verify_canary_conformance(
    role: ProcessRole,
    environment: Mapping[str, str],
    canaries: Sequence[SyntheticCanary],
    *,
    probe: Callable[[Mapping[str, str], Sequence[str]], set[str]] | None = None,
) -> tuple[ProcessBoundaryEvidence, ...]:
    """Probe a child process and fail closed if an unauthorized canary is observable."""
    names = tuple(canary.variable_name for canary in canaries)
    observed = (probe or _subprocess_probe)(environment, names)
    evidence: list[ProcessBoundaryEvidence] = []
    violations = False
    allowed_domain = credential_domain_for(role)
    for canary in canaries:
        visible = canary.variable_name in observed
        authorized = canary.domain is allowed_domain
        if visible and authorized:
            verdict = "authorized_observed"
        elif not visible and not authorized:
            verdict = "prohibited_absent"
        elif visible:
            verdict = "prohibited_observed"
            violations = True
        else:
            verdict = "authorized_missing"
            violations = True
        evidence.append(ProcessBoundaryEvidence(role, canary.canary_id, "environment", verdict))
    if violations:
        raise ProcessBoundaryConformanceError(tuple(evidence))
    return tuple(evidence)


def _validate_runtime_name(role: ProcessRole, name: str, value: str) -> None:
    if not isinstance(value, str) or not name:
        raise ProcessEnvironmentError("runtime_environment_invalid")
    if name in _CREDENTIAL_VARIABLE_DOMAINS or _looks_secret_bearing(name):
        raise ProcessEnvironmentError("runtime_credential_denied")
    if name in _DAEMON_CONFIGURATION_NAMES:
        if role not in {ProcessRole.MEMRELAY_DAEMON, ProcessRole.DIRECT_ENGINE_WORKER}:
            raise ProcessEnvironmentError("daemon_configuration_cross_boundary_denied")
        return
    if name not in _RUNTIME_BASELINE_NAMES:
        raise ProcessEnvironmentError("runtime_variable_not_allowlisted")


def _looks_secret_bearing(name: str) -> bool:
    lowered = name.lower()
    return any(
        fragment in lowered for fragment in ("token", "secret", "password", "api_key", "credential")
    )


def _subprocess_probe(environment: Mapping[str, str], names: Sequence[str]) -> set[str]:
    """Return only observable variable names from an inert Python child."""
    result = subprocess.run(
        (
            sys.executable,
            "-c",
            (
                "import os, sys; "
                "print('\\n'.join(name for name in sys.argv[1:] if name in os.environ))"
            ),
            *names,
        ),
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        raise ProcessBoundaryConformanceError(())
    return set(result.stdout.splitlines())
