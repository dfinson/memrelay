from __future__ import annotations

import ast
from pathlib import Path

EVALUATION_ROOT = Path(__file__).parents[2]
SOURCE_ROOT = EVALUATION_ROOT / "src" / "memrelay_eval"


def test_no_cross_repository_adapter_is_composed_in_evaluator_v1() -> None:
    adapter_root = SOURCE_ROOT / "adapters"
    source = "\n".join(path.read_text(encoding="utf-8") for path in adapter_root.glob("*.py"))

    assert "CrossRepository" not in source
    assert not (adapter_root / "workspace").exists()
    assert not (adapter_root / "clone.py").exists()


def test_orchestration_and_cli_do_not_import_repository_adapters() -> None:
    violations: list[str] = []
    for source_path in (
        SOURCE_ROOT / "orchestration" / "control.py",
        SOURCE_ROOT / "orchestration" / "stages.py",
        SOURCE_ROOT / "cli" / "commands.py",
        SOURCE_ROOT / "cli" / "main.py",
    ):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and ".adapters" in node.module:
                violations.append(f"{source_path.name}: {node.module}")
    assert violations == []
