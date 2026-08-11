from __future__ import annotations

import ast
from pathlib import Path


def _forbidden_imports(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return {
        module
        for module in modules
        if module.startswith("memrelay_eval.adapters")
        or module
        in {
            "memrelay_eval.orchestration.assignment",
            "memrelay_eval.domain.assignment",
        }
    }


def test_outcome_normalization_has_no_assignment_or_adapter_imports() -> None:
    source = Path(__file__).parents[3] / "src" / "memrelay_eval" / "scoring" / "outcomes.py"
    assert _forbidden_imports(source.read_text(encoding="utf-8")) == set()


def test_outcome_boundary_rejects_assignment_and_cross_adapter_imports() -> None:
    assert _forbidden_imports("from memrelay_eval.adapters.grader import executable") == {
        "memrelay_eval.adapters.grader"
    }
    assert _forbidden_imports("import memrelay_eval.orchestration.assignment") == {
        "memrelay_eval.orchestration.assignment"
    }
