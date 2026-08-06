"""Copilot adapter composition kept outside CLI and orchestration boundaries."""

from __future__ import annotations

from memrelay_eval.adapters.copilot.client import CopilotSdkClient, bootstrap_runtime
from memrelay_eval.adapters.copilot.session import qualify_native_catalog
from memrelay_eval.application.copilot_catalog import eligible_models

__all__ = (
    "CopilotSdkClient",
    "bootstrap_runtime",
    "eligible_models",
    "qualify_native_catalog",
)
