from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

import pytest

EVALUATION_ROOT = Path(__file__).parents[3]
SOURCE_ROOT = EVALUATION_ROOT / "src" / "memrelay_eval"
_FORBIDDEN_ROOTS = frozenset(
    {
        "http",
        "httpx",
        "github_copilot_sdk",
        "inspect",
        "openai",
        "requests",
        "socket",
        "urllib",
        "copilot",
    }
)


class _CatalogArchitectureVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.findings: list[str] = []
        self.json_modules: set[str] = set()
        self.json_dumps: set[str] = set()
        self.import_modules: set[str] = set()
        self.network_modules: set[str] = set()
        self.network_calls: set[str] = set()

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local_name = alias.asname or alias.name.split(".", 1)[0]
            root = alias.name.split(".", 1)[0]
            if root in _FORBIDDEN_ROOTS:
                self.findings.append(f"forbidden import {alias.name}")
            if alias.name == "json":
                self.json_modules.add(local_name)
            if alias.name == "importlib":
                self.import_modules.add(local_name)
            if root in {"socket", "urllib", "http"}:
                self.network_modules.add(local_name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        root = module.split(".", 1)[0]
        for alias in node.names:
            local_name = alias.asname or alias.name
            if root in _FORBIDDEN_ROOTS:
                self.findings.append(f"forbidden import {module}.{alias.name}")
            if module == "json" and alias.name == "dumps":
                self.json_dumps.add(local_name)
            if module == "importlib" and alias.name == "import_module":
                self.import_modules.add(local_name)
            if root in {"socket", "urllib", "http"}:
                self.network_calls.add(local_name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if (
            isinstance(node.value, ast.Attribute)
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id in self.network_modules
        ):
            self.network_calls.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in self.json_dumps:
                self.findings.append("forbidden json.dumps call")
            if node.func.id in self.network_calls:
                self.findings.append("forbidden network call")
            if node.func.id == "__import__" and _literal_module(node.args):
                self._forbid_dynamic_module(_literal_module(node.args))
            if node.func.id in self.import_modules and _literal_module(node.args):
                self._forbid_dynamic_module(_literal_module(node.args))
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            if node.func.attr == "dumps" and node.func.value.id in self.json_modules:
                self.findings.append("forbidden json.dumps call")
            if node.func.value.id in self.import_modules and node.func.attr == "import_module":
                self._forbid_dynamic_module(_literal_module(node.args))
            if node.func.value.id in self.network_modules:
                self.findings.append("forbidden network call")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        name = node.name.casefold()
        calls = tuple(item for item in ast.walk(node) if isinstance(item, ast.Call))
        uses_shared_canonicalizer = any(
            isinstance(call.func, ast.Name) and call.func.id == "canonical_bytes" for call in calls
        )
        uses_manual_sorting = any(
            isinstance(call.func, ast.Name) and call.func.id == "sorted" for call in calls
        )
        uses_sha256 = any(
            (
                isinstance(call.func, ast.Name)
                and call.func.id == "sha256"
                or isinstance(call.func, ast.Attribute)
                and call.func.attr == "sha256"
            )
            for call in calls
        )
        if "canonical" in name and uses_manual_sorting:
            self.findings.append("competing manually sorted canonicalizer")
        if "digest" in name and uses_sha256 and not uses_shared_canonicalizer:
            self.findings.append("digest implementation bypasses shared canonicalizer")
        self.generic_visit(node)

    def _forbid_dynamic_module(self, module: str | None) -> None:
        if module is not None and module.split(".", 1)[0] in _FORBIDDEN_ROOTS:
            self.findings.append(f"forbidden dynamic import {module}")


def _literal_module(args: list[ast.expr]) -> str | None:
    if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
        return args[0].value
    return None


def _findings(source: str) -> list[str]:
    visitor = _CatalogArchitectureVisitor()
    visitor.visit(ast.parse(source))
    return visitor.findings


def _python_files() -> Iterable[Path]:
    return SOURCE_ROOT.rglob("*.py")


def test_evaluator_has_one_shared_jcs_implementation_and_catalog_import_graph() -> None:
    files = {
        path.relative_to(SOURCE_ROOT).as_posix(): path.read_text(encoding="utf-8")
        for path in _python_files()
    }
    rfc8785_importers = [
        path
        for path, source in files.items()
        if any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and (
                (
                    isinstance(node, ast.Import)
                    and any(alias.name == "rfc8785" for alias in node.names)
                )
                or (isinstance(node, ast.ImportFrom) and node.module == "rfc8785")
            )
            for node in ast.walk(ast.parse(source))
        )
    ]

    assert rfc8785_importers == ["canonical.py"]
    assert all(
        not {
            "forbidden json.dumps call",
            "competing manually sorted canonicalizer",
            "digest implementation bypasses shared canonicalizer",
        }
        & set(_findings(source))
        for source in files.values()
    )
    for path, source in files.items():
        if not path.startswith("catalog/"):
            continue
        tree = ast.parse(source)
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        } | {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert not any(
            module.startswith("memrelay_eval.adapters")
            or module.startswith("memrelay_eval.evidence")
            for module in imports
        )


def test_catalog_ast_and_import_graph_reject_sdk_network_and_serializer_evasion() -> None:
    for path in (SOURCE_ROOT / "catalog").glob("*.py"):
        assert _findings(path.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "source",
    [
        "from json import dumps as encode\nencode({}, **{'sort_keys': True})",
        "import json as wire\nwire.dumps({})",
        "import socket as transport\nconnect = transport.create_connection\nconnect(('x', 1))",
        "from urllib.request import urlopen as fetch\nfetch('https://example.invalid')",
        "import importlib as loader\nloader.import_module('http.client')",
        "__import__('socket')",
        """
import hashlib

def canonical_identity(value):
    return repr(sorted(value)).encode()

def canonical_digest(value):
    return hashlib.sha256(repr(value).encode()).hexdigest()
""",
    ],
)
def test_architecture_analysis_catches_alias_and_dynamic_evasions(source: str) -> None:
    assert _findings(source)


def test_architecture_analysis_ignores_safe_strings_and_shared_canonical_calls() -> None:
    source = """
from memrelay_eval.canonical import canonical_bytes

description = "socket and json.dumps are documentation, not calls"
canonical_bytes({"safe": True})
"""

    assert _findings(source) == []
