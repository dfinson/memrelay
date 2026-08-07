import ast
from pathlib import Path

import pytest

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "memrelay_eval"
_PROHIBITED_ASSIGNMENT_MODULES = frozenset(
    {
        "memrelay_eval.orchestration.assignment",
        "memrelay_eval.domain.assignment",
    }
)


def _module_for_path(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = ("memrelay_eval", *relative.parts)
    return ".".join(parts[:-1])


def _resolve_from_module(node: ast.ImportFrom, current_package: str) -> str:
    if node.level == 0:
        return node.module or ""

    package_parts = current_package.split(".")
    parent_parts = package_parts[: len(package_parts) - node.level + 1]
    return ".".join((*parent_parts, *(node.module or "").split("."))).strip(".")


def _is_prohibited(module: str) -> bool:
    return any(
        module == prohibited or module.startswith(f"{prohibited}.")
        for prohibited in _PROHIBITED_ASSIGNMENT_MODULES
    )


def _literal_module(call: ast.Call) -> str | None:
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return None


def _contains_prohibited_assignment_import(tree: ast.AST, current_package: str) -> bool:
    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()
    builtins_aliases: set[str] = set()
    builtin_import_aliases: set[str] = {"__import__"}
    imported_modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_modules.add(alias.name)
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or alias.name)
                if alias.name == "builtins":
                    builtins_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_from_module(node, current_package)
            imported_modules.add(module)
            for alias in node.names:
                imported_modules.add(f"{module}.{alias.name}".strip("."))
                if module == "importlib" and alias.name == "import_module":
                    import_module_aliases.add(alias.asname or alias.name)
                if module == "builtins" and alias.name == "__import__":
                    builtin_import_aliases.add(alias.asname or alias.name)

    if any(_is_prohibited(module) for module in imported_modules):
        return True

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        module = _literal_module(node)
        if module is None:
            continue
        if (
            isinstance(node.func, ast.Name)
            and (node.func.id in builtin_import_aliases or node.func.id in import_module_aliases)
            and _is_prohibited(module)
        ):
            return True
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"__import__", "import_module"}
            and isinstance(node.func.value, ast.Name)
            and (
                node.func.value.id in builtins_aliases
                or (node.func.attr == "import_module" and node.func.value.id in importlib_aliases)
            )
            and _is_prohibited(module)
        ):
            return True
    return False


def _assignment_boundary_violations(source_root: Path) -> list[str]:
    violations: list[str] = []
    for package in ("catalog", "scoring", "cli"):
        package_root = source_root / package
        if not package_root.exists():
            continue
        for path in package_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if _contains_prohibited_assignment_import(tree, _module_for_path(path, source_root)):
                violations.append(path.relative_to(source_root).as_posix())
    return violations


@pytest.mark.parametrize(
    ("source", "path"),
    (
        (
            "import memrelay_eval.orchestration.assignment",
            "catalog/mutation.py",
        ),
        (
            "from memrelay_eval.orchestration.assignment import ConcealedAssignmentService",
            "catalog/mutation.py",
        ),
        (
            "from memrelay_eval.orchestration import assignment",
            "catalog/mutation.py",
        ),
        (
            "from ..orchestration.assignment import ConcealedAssignmentService",
            "catalog/mutation.py",
        ),
        (
            "from ..orchestration import assignment as concealed_assignment",
            "catalog/mutation.py",
        ),
        (
            "from ..orchestration.assignment import ConcealedAssignmentService",
            "catalog/__init__.py",
        ),
        (
            "from ..orchestration import assignment as concealed_assignment",
            "catalog/__init__.py",
        ),
        (
            "from memrelay_eval.orchestration import (\n    assignment as concealed_assignment,\n)",
            "catalog/mutation.py",
        ),
        (
            "from importlib import import_module as load\n"
            'load("memrelay_eval.orchestration.assignment")',
            "scoring/mutation.py",
        ),
        (
            "import importlib as imports\n"
            'imports.import_module("memrelay_eval.orchestration.assignment")',
            "cli/mutation.py",
        ),
        (
            'from builtins import __import__ as load\nload("memrelay_eval.domain.assignment")',
            "catalog/mutation.py",
        ),
    ),
)
def test_assignment_boundary_detects_planted_import_bypasses(
    tmp_path: Path, source: str, path: str
) -> None:
    source_root = tmp_path / "memrelay_eval"
    target = source_root / path
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")

    assert _assignment_boundary_violations(source_root) == [path]


@pytest.mark.parametrize(
    ("path", "expected_package"),
    (
        ("catalog/mutation.py", "memrelay_eval.catalog"),
        ("catalog/__init__.py", "memrelay_eval.catalog"),
        ("catalog/nested/mutation.py", "memrelay_eval.catalog.nested"),
        ("catalog/nested/__init__.py", "memrelay_eval.catalog.nested"),
    ),
)
def test_assignment_boundary_resolves_regular_modules_and_package_initializers(
    tmp_path: Path, path: str, expected_package: str
) -> None:
    source_root = tmp_path / "memrelay_eval"
    target = source_root / path
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")

    assert _module_for_path(target, source_root) == expected_package


def test_catalog_scoring_and_cli_cannot_import_provisioning_assignment_resolution() -> None:
    assert _assignment_boundary_violations(SOURCE_ROOT) == []
