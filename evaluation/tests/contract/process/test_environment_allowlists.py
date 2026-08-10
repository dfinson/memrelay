from __future__ import annotations

import pytest
from memrelay_eval.adapters.process.environment import (
    CredentialDomain,
    CredentialReference,
    ProcessRole,
    SyntheticCanary,
    build_process_environment,
    credential_domain_for,
    inject_synthetic_canaries,
    verify_canary_conformance,
)
from memrelay_eval.domain.errors import ProcessBoundaryConformanceError, ProcessEnvironmentError

_REFERENCES = (
    CredentialReference("GITHUB_TOKEN", CredentialDomain.COPILOT, ProcessRole.COPILOT_WORKER),
    CredentialReference("COPILOT_AUTH_TOKEN", CredentialDomain.COPILOT, ProcessRole.JUDGE),
    CredentialReference("OPENAI_API_KEY", CredentialDomain.OPENAI, ProcessRole.MEMRELAY_DAEMON),
    CredentialReference(
        "OPENAI_API_KEY", CredentialDomain.OPENAI, ProcessRole.DIRECT_ENGINE_WORKER
    ),
)
_VALUES = {
    "GITHUB_TOKEN": "host-copilot-auth",
    "COPILOT_AUTH_TOKEN": "judge-copilot-auth",
    "OPENAI_API_KEY": "framework-openai-key",
}


@pytest.mark.parametrize("role", tuple(ProcessRole))
def test_role_environment_uses_minimal_role_allowlist(role: ProcessRole) -> None:
    references = tuple(reference for reference in _REFERENCES if reference.target_role is role)
    values = {reference.variable_name: _VALUES[reference.variable_name] for reference in references}
    environment = build_process_environment(
        role,
        runtime_environment={"PATH": "safe-path", "MEMRELAY_HOME": "attempt-home"},
        credential_references=references,
        credential_values=values,
    )

    assert set(environment) >= {"PATH", "MEMRELAY_HOME"}
    expected = {
        ProcessRole.COPILOT_WORKER: {"GITHUB_TOKEN"},
        ProcessRole.JUDGE: {"COPILOT_AUTH_TOKEN"},
        ProcessRole.MEMRELAY_DAEMON: {"OPENAI_API_KEY"},
        ProcessRole.DIRECT_ENGINE_WORKER: {"OPENAI_API_KEY"},
    }.get(role, set())
    assert set(environment).intersection(_VALUES) == expected
    assert credential_domain_for(role) in CredentialDomain


def test_ambient_and_unknown_key_bearing_variables_are_denied() -> None:
    with pytest.raises(ProcessEnvironmentError):
        build_process_environment(
            ProcessRole.GRADER,
            runtime_environment={"PATH": "safe", "OPENAI_API_KEY": "ambient-secret"},
        )
    with pytest.raises(ProcessEnvironmentError):
        build_process_environment(
            ProcessRole.GRADER,
            runtime_environment={"PATH": "safe", "UNDECLARED_AMBIENT": "leak"},
        )
    with pytest.raises(ProcessEnvironmentError):
        CredentialReference(
            "UNRECOGNIZED_API_KEY", CredentialDomain.OPENAI, ProcessRole.MEMRELAY_DAEMON
        )


@pytest.mark.parametrize("role", tuple(ProcessRole))
def test_subprocess_canary_conformance_retains_only_non_secret_evidence(role: ProcessRole) -> None:
    canaries = (
        SyntheticCanary.create("GITHUB_TOKEN", CredentialDomain.COPILOT),
        SyntheticCanary.create("OPENAI_API_KEY", CredentialDomain.OPENAI),
    )
    environment = inject_synthetic_canaries(role, {"PATH": "safe-path"}, canaries)
    evidence = verify_canary_conformance(role, environment, canaries)

    assert {item.verdict for item in evidence}.issubset(
        {"authorized_observed", "prohibited_absent"}
    )
    rendered = repr(evidence)
    assert all(canary.value not in rendered for canary in canaries)


def test_observed_prohibited_canary_fails_without_echoing_value() -> None:
    canary = SyntheticCanary.create("OPENAI_API_KEY", CredentialDomain.OPENAI)
    with pytest.raises(ProcessBoundaryConformanceError) as raised:
        verify_canary_conformance(
            ProcessRole.MCP_CLIENT,
            {"PATH": "safe-path", canary.variable_name: canary.value},
            (canary,),
        )

    assert canary.value not in repr(raised.value.evidence)
    assert raised.value.evidence[0].verdict == "prohibited_observed"
