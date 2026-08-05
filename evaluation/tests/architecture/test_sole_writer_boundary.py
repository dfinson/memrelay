from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).parents[2] / "src" / "memrelay_eval"


def imports_in(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_only_the_ledger_repository_imports_sqlite3() -> None:
    sqlite_importers = [
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in SOURCE_ROOT.rglob("*.py")
        if any(name == "sqlite3" or name.startswith("sqlite3.") for name in imports_in(path))
    ]
    assert sqlite_importers == ["ledger/repository.py"]


def test_worker_attempt_modules_have_no_sqlite_or_store_handle_boundary_escape() -> None:
    for name in ("worker.py", "attempt.py"):
        path = SOURCE_ROOT / "orchestration" / name
        source = path.read_text(encoding="utf-8").lower()
        assert "sqlite" not in source
        assert "database_path" not in source
        assert "connection" not in source


def test_analysis_cannot_open_or_mutate_operational_sqlite() -> None:
    analysis_root = SOURCE_ROOT / "analysis"
    violations: list[str] = []
    for path in analysis_root.rglob("*.py") if analysis_root.exists() else ():
        source = path.read_text(encoding="utf-8")
        imports = imports_in(path)
        if any(name == "sqlite3" or name.startswith("sqlite3.") for name in imports):
            violations.append(f"{path.name}: sqlite import")
        if any(
            name == "memrelay_eval.ledger" or name.startswith("memrelay_eval.ledger.")
            for name in imports
        ):
            violations.append(f"{path.name}: ledger import")
        if ".connect(" in source and "sqlite" in source.lower():
            violations.append(f"{path.name}: sqlite connection")
        if ".execute(" in source and any(
            token in source.lower() for token in ("insert ", "update ", "delete ")
        ):
            violations.append(f"{path.name}: sqlite mutation")
    assert violations == []
