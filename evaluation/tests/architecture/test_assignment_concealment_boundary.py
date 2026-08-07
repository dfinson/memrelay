import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "memrelay_eval"


def _contains_prohibited_assignment_import(tree: ast.AST, prohibited: set[str]) -> bool:
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    return any(
        module == forbidden or module.startswith(f"{forbidden}.")
        for module in imported
        for forbidden in prohibited
    )


@pytest.mark.parametrize(
    "source",
    (
        "from memrelay_eval.orchestration.assignment import ConcealedAssignmentService",
        "import memrelay_eval.orchestration.assignment",
        "import memrelay_eval.domain.assignment as assignment",
    ),
)
def test_assignment_boundary_detects_all_import_forms(source: str) -> None:
    prohibited = {
        "memrelay_eval.orchestration.assignment",
        "memrelay_eval.domain.assignment",
    }

    assert _contains_prohibited_assignment_import(ast.parse(source), prohibited)


def test_catalog_scoring_and_cli_cannot_import_provisioning_assignment_resolution() -> None:
    prohibited = {
        "memrelay_eval.orchestration.assignment",
        "memrelay_eval.domain.assignment",
    }
    violations: list[str] = []
    for package in ("catalog", "scoring", "cli"):
        package_root = SOURCE_ROOT / package
        if not package_root.exists():
            continue
        for path in package_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if _contains_prohibited_assignment_import(tree, prohibited):
                violations.append(path.relative_to(SOURCE_ROOT).as_posix())

    assert violations == []
