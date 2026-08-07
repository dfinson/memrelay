from __future__ import annotations

import ast
import sys
from pathlib import Path

from memrelay_eval.adapters.process.environment import ProcessRole


def test_all_authoritative_process_roles_are_explicit() -> None:
    assert set(ProcessRole) == {
        ProcessRole.INSPECT_CONTROL,
        ProcessRole.COPILOT_WORKER,
        ProcessRole.MEMRELAY_DAEMON,
        ProcessRole.MCP_CLIENT,
        ProcessRole.GRADER,
        ProcessRole.JUDGE,
        ProcessRole.COLLECTOR,
        ProcessRole.ANALYSIS,
    }


def test_process_adapter_uses_only_stdlib_and_evaluator_domain_contracts() -> None:
    root = Path(__file__).parents[2] / "src" / "memrelay_eval" / "adapters" / "process"
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    violations: list[str] = []
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] not in stdlib:
                        violations.append(f"{source.name}: {alias.name}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and node.module
                and node.module.split(".")[0] not in stdlib
            ):
                violations.append(f"{source.name}: {node.module}")
    assert violations == []
