#!/usr/bin/env python3
"""Fail CI when code bypasses the sealed accounting ledger V2 API."""
from __future__ import annotations

import ast
import sys
from pathlib import Path


MODULE = "accounting_ledger_v2"
PROTECTED_NAMES = frozenset({
    "GROUPS_COLLECTION",
    "GENERAL_LEDGER_COLLECTION",
    "AUDIT_COLLECTION",
    "SEQUENCES_COLLECTION",
})


class _AccessVisitor(ast.NodeVisitor):
    def __init__(self, path: Path):
        self.path = path
        self.aliases: set[str] = set()
        self.errors: list[str] = []

    def _error(self, node: ast.AST, message: str) -> None:
        self.errors.append(f"{self.path}:{getattr(node, 'lineno', 0)}: {message}")

    def visit_Import(self, node: ast.Import) -> None:
        for item in node.names:
            if item.name == MODULE:
                self.aliases.add(item.asname or MODULE)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module == MODULE:
            for item in node.names:
                if item.name in PROTECTED_NAMES or item.name.startswith("_"):
                    self._error(node, f"private V2 core import: {item.name}")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        if isinstance(node.value, ast.Name) and node.value.id in self.aliases:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.aliases.add(target.id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (
            isinstance(node.value, ast.Name)
            and node.value.id in self.aliases
            and (node.attr in PROTECTED_NAMES or node.attr.startswith("_"))
        ):
            self._error(node, f"private V2 core attribute: {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in self.aliases
        ):
            attr = node.args[1]
            if isinstance(attr, ast.Constant) and isinstance(attr.value, str):
                if attr.value in PROTECTED_NAMES or attr.value.startswith("_"):
                    self._error(node, f"reflective V2 core access: {attr.value}")
            else:
                self._error(node, "reflective V2 core access with dynamic attribute")
        self.generic_visit(node)


def _violations(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [f"{path}: unable to inspect: {exc}"]
    visitor = _AccessVisitor(path)
    visitor.visit(tree)
    return visitor.errors


def _candidate_files(root: Path):
    backend = root / "backend"
    for path in backend.rglob("*.py"):
        if path.name in {
            "accounting_ledger_v2.py",
            "check_accounting_ledger_v2_access.py",
        }:
            continue
        if "tests" in path.parts:
            continue
        yield path


def main(argv: list[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    root = Path(args[0]).resolve() if args else Path(__file__).resolve().parents[1]
    errors = [error for path in _candidate_files(root) for error in _violations(path)]
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("accounting ledger V2 access guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
