"""Shared schema-in-prompt + robust JSON-parse helpers for LLM strategies.

Both the local (Ollama) and litellm strategies drive structured output the same
provider-agnostic way: embed the requested ``response_model`` JSON schema in the
prompt and parse a single JSON object back out of the model's raw text, tolerating
code fences and stray prose. These helpers are factored out of ``local.py`` so the
two strategies share one proven implementation rather than drifting copies; the
behaviour is pinned by ``tests/unit/test_local_llm.py``.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


def _schema_instruction(response_model: type[BaseModel]) -> str:
    schema = json.dumps(response_model.model_json_schema(), indent=2)
    return (
        "Respond with a SINGLE JSON object and nothing else — no prose, no code "
        "fences, no explanation. The object MUST validate against this JSON "
        f"schema:\n{schema}"
    )


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Drop the opening fence line (``` or ```json) and the trailing fence.
    without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if "```" in without_open:
        without_open = without_open.rsplit("```", 1)[0]
    return without_open.strip()


def _loads_json_object(raw: str) -> dict[str, Any]:
    """Best-effort parse of a JSON object out of a raw model response."""
    candidate = _strip_code_fences(raw)
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError):
        # Fall back to the outermost {...} span if the model added stray text.
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object found in model response") from None
        parsed = json.loads(candidate[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("model response JSON was not an object")
    return parsed
