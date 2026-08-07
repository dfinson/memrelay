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
from .launcher import DisposableProcessLauncher, ProcessLaunchRequest, ProcessRunReport

__all__ = [
    "CredentialDomain",
    "CredentialReference",
    "DisposableProcessLauncher",
    "ProcessLaunchRequest",
    "ProcessRole",
    "ProcessRunReport",
    "SyntheticCanary",
    "build_process_environment",
    "inject_synthetic_canaries",
    "verify_canary_conformance",
]
