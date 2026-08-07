from __future__ import annotations

import ast
import sys
from pathlib import Path

STDLIB = set(sys.stdlib_module_names) | {"__future__"}
DOMAIN_ROOT = Path(__file__).parents[2] / "src" / "memrelay_eval" / "domain"
_DOMAIN_SHARED_PURE_MODULES = frozenset({"memrelay_eval.canonical"})


def test_domain_imports_only_stdlib_or_domain_owned_modules() -> None:
    violations: list[str] = []
    for source_path in sorted(DOMAIN_ROOT.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".", maxsplit=1)[0] not in STDLIB:
                        violations.append(f"{source_path.name}: {alias.name}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module
                and node.module.split(".", maxsplit=1)[0] not in STDLIB
                and node.module not in _DOMAIN_SHARED_PURE_MODULES
            ):
                violations.append(f"{source_path.name}: {node.module}")
    assert violations == []
