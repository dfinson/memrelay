from __future__ import annotations

import pytest
from memrelay_eval.adapters.process.environment import (
    CredentialDomain,
    CredentialReference,
    ProcessRole,
    SyntheticCanary,
    build_process_environment,
    inject_synthetic_canaries,
    verify_canary_conformance,
)
from memrelay_eval.domain.errors import ProcessBoundaryConformanceError
from memrelay_eval.domain.identity import copilot_identity, framework_openai_identity, local_identity
from memrelay_eval.evidence.costs import environment_identity_projection
from memrelay_eval.evidence.secret_scan import SecretBoundaryViolationError


@pytest.mark.parametrize(
    ("role", "identity"),
    (
        (ProcessRole.COPILOT_WORKER, copilot_identity()),
        (ProcessRole.MEMRELAY_DAEMON, framework_openai_identity()),
        (ProcessRole.COLLECTOR, local_identity("local_collector")),
        (ProcessRole.EVIDENCE, local_identity("local_storage")),
    ),
)
def test_role_projections_never_persist_credential_values(
    role: ProcessRole, identity: object
) -> None:
    references = (
        (CredentialReference("GITHUB_TOKEN", CredentialDomain.COPILOT, role),)
        if role is ProcessRole.COPILOT_WORKER
        else (
            (CredentialReference("OPENAI_API_KEY", CredentialDomain.OPENAI, role),)
            if role is ProcessRole.MEMRELAY_DAEMON
            else ()
        )
    )
    values = {reference.variable_name: "synthetic-canary-" + "a" * 32 for reference in references}
    environment = build_process_environment(
        role,
        runtime_environment={"PATH": "safe"},
        credential_references=references,
        credential_values=values,
    )
    projection = environment_identity_projection(environment, identity)  # type: ignore[arg-type]
    assert "synthetic-canary" not in repr(projection)
    assert set(projection["environment_names"]).intersection(values) == set(values)


def test_reused_and_substituted_credentials_are_rejected_at_the_child_boundary() -> None:
    canary = SyntheticCanary.create("OPENAI_API_KEY", CredentialDomain.OPENAI)
    canary_environment = {"PATH": "safe", "OPENAI_API_KEY": canary.value}
    with pytest.raises(ProcessBoundaryConformanceError) as error:
        verify_canary_conformance(
            ProcessRole.COLLECTOR,
            canary_environment,
            (canary,),
        )
    assert "synthetic-canary" not in repr(error.value)
    with pytest.raises(SecretBoundaryViolationError):
        environment_identity_projection(
            {"PATH": "safe", "LOG": "synthetic-canary-" + "c" * 32},
            local_identity("local_collector"),
        )
