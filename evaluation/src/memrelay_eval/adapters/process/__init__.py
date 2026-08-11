"""Disposable process adapters with credential-isolated environments."""

from .environment import (
    CredentialDomain,
    CredentialReference,
    ProcessRole,
    SyntheticCanary,
    build_process_environment,
    inject_synthetic_canaries,
    verify_canary_conformance,
)
from .judge import IsolatedJudgeProcessRuntime
from .launcher import DisposableProcessLauncher, ProcessLaunchRequest, ProcessRunReport

__all__ = [
    "CredentialDomain",
    "CredentialReference",
    "DisposableProcessLauncher",
    "IsolatedJudgeProcessRuntime",
    "ProcessLaunchRequest",
    "ProcessRole",
    "ProcessRunReport",
    "SyntheticCanary",
    "build_process_environment",
    "inject_synthetic_canaries",
    "verify_canary_conformance",
]
