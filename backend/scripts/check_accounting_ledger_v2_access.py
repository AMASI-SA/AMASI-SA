#!/usr/bin/env python3
"""Fail CI when production code bypasses the Accounting V2 storage core."""
from __future__ import annotations

import ast
import sys
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
CORE = BACKEND / "accounting_ledger_v2.py"
SELF = Path(__file__).resolve()
COLLECTIONS = frozenset({
    "accounting_journal_groups_v2",
    "accounting_general_ledger_v2",
    "accounting_audit_log_v2",
    "accounting_ledger_sequences_v2",
})
COLLECTION_CONSTANTS = frozenset({
    "GROUPS_COLLECTION",
    "GENERAL_LEDGER_COLLECTION",
    "AUDIT_COLLECTION",
    "SEQUENCES_COLLECTION",
})


def _protected_name(name: str) -> bool:
    return name in COLLECTION_CONSTANTS or name.startswith("_")


def _production_modules() -> list[Path]:
    modules: list[Path] = []
    for path in BACKEND.rglob("*.py"):
        resolved = path.resolve()
        if resolved in {CORE.resolve(), SELF} or "tests" in path.parts:
            continue
        modules.append(path)
    return sorted(modules)


def _violations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    try:
        relative = path.relative_to(BACKEND.parent).as_posix()
    except ValueError:
        relative = path.as_posix()
    errors: list[str] = []
    for collection in sorted(COLLECTIONS):
        if collection in source:
            errors.append(f"{relative}: raw V2 collection literal {collection!r}")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        errors.append(f"{relative}:{exc.lineno}: cannot parse module")
        return errors
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] == "accounting_ledger_v2":
                    module_aliases.add(alias.asname or alias.name.split(".", 1)[0])
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.rsplit(".", 1)[-1] == "accounting_ledger_v2"
        ):
            for alias in node.names:
                if alias.name == "*" or _protected_name(alias.name):
                    errors.append(
                        f"{relative}:{node.lineno}: imports protected V2 core name "
                        f"{alias.name!r}"
                    )

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and isinstance(node.value, ast.Name)
                and node.value.id in module_aliases
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id not in module_aliases:
                        module_aliases.add(target.id)
                        changed = True

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id in module_aliases
            and _protected_name(node.attr)
        ):
            errors.append(
                f"{relative}:{node.lineno}: accesses protected V2 core name {node.attr!r}"
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"getattr", "vars"}
            and node.args
            and isinstance(node.args[0], ast.Name)
            and node.args[0].id in module_aliases
        ):
            if node.func.id == "getattr" and len(node.args) > 1:
                attribute = node.args[1]
                if isinstance(attribute, ast.Constant) and isinstance(attribute.value, str):
                    detail = repr(attribute.value)
                else:
                    detail = "a dynamic name"
            else:
                detail = "the module namespace"
            errors.append(
                f"{relative}:{node.lineno}: reflective V2 core access is forbidden: {detail}"
            )
    return errors


def main() -> int:
    errors = [error for path in _production_modules() for error in _violations(path)]
    if errors:
        print("Accounting V2 raw-access guard failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Accounting V2 raw-access guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
