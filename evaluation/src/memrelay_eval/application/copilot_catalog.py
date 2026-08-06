"""Catalog adapter composition required by evaluator lock persistence."""

from __future__ import annotations

from memrelay_eval.adapters.copilot.catalog import (
    CatalogArchive,
    ModelSelection,
    eligible_models,
    qualification_summary,
    select_models,
)

__all__ = (
    "CatalogArchive",
    "ModelSelection",
    "eligible_models",
    "qualification_summary",
    "select_models",
)
