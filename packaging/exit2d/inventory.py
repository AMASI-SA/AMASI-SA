"""Offline source inventory; never imports Mezan, reads env, or opens Mongo.

This is discovery evidence, not proof of live callback reachability, enabled
features, task ownership enforcement, or a deployable independent runtime.
Only syntax and identifiers are emitted; source literals/credentials are not.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path


def identifier(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = identifier(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


class Inventory(ast.NodeVisitor):
    def __init__(self, path):
        self.path = path
        self.scope = []
        self.hooks = []
        self.launches = []

    def function(self, node):
        for decorator in node.decorator_list:
            if (isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "on_event"
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                    and decorator.args[0].value in {"startup", "shutdown"}):
                self.hooks.append({"path": self.path, "line": decorator.lineno,
                    "event": decorator.args[0].value,
                    "callback": ".".join([*self.scope, node.name]),
                    "registration": "decorator"})
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = function
    visit_AsyncFunctionDef = function

    def visit_ClassDef(self, node):
        self.scope.append(node.name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node):
        name = identifier(node.func) or ""
        if (isinstance(node.func, ast.Attribute) and node.func.attr == "append"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr in {"on_startup", "on_shutdown"}):
            callback = identifier(node.args[0]) if node.args else None
            self.hooks.append({"path": self.path, "line": node.lineno,
                "event": node.func.value.attr.removeprefix("on_"),
                "callback": ".".join([*self.scope, callback]) if callback else None,
                "registration": "append", "unresolved": callback is None})
        terminal = name.rsplit(".", 1)[-1]
        if (terminal in {"create_task", "ensure_future", "create_subprocess_exec",
                         "create_subprocess_shell", "add_event_handler"}
                or terminal.startswith("start_")
                or terminal.startswith("_start_")
                or terminal in {"_qoyod_worker_start", "_qoyod_plan_b_auto_start"}):
            self.launches.append({"path": self.path, "line": node.lineno,
                "scope": ".".join(self.scope), "call": name,
                "target": identifier(node.args[0].func)
                    if node.args and isinstance(node.args[0], ast.Call) else None})
        self.generic_visit(node)


def scan(root):
    root = Path(root)
    hooks, launches, hashes, errors = [], [], {}, []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if any(part in {"tests", "scripts", "__pycache__", ".venv", "venv"}
               for part in relative.parts) or path.name.startswith("test_"):
            continue
        label = relative.as_posix()
        data = path.read_bytes()
        try:
            tree = ast.parse(data, filename=label)
        except (SyntaxError, ValueError) as exc:
            # Do not expose source lines or exception messages.
            errors.append({"path": label, "error": type(exc).__name__})
            continue
        visitor = Inventory(label)
        visitor.visit(tree)
        hooks.extend(visitor.hooks)
        launches.extend(visitor.launches)
        if visitor.hooks or visitor.launches:
            hashes[label] = hashlib.sha256(data).hexdigest()
    return {"schema_version": 1, "mode": "offline_ast_discovery",
        "runtime_verified": False,
        "limitations": ["No import or control-flow reachability analysis",
            "Launch names are candidates, not proof of background execution",
            "Dynamic registration, aliases and custom task factories may be missed",
            "Source constants and environment values are intentionally omitted"],
        "hooks": sorted(hooks, key=lambda row: (row["path"], row["line"])),
        "launch_candidates": sorted(launches, key=lambda row: (row["path"], row["line"])),
        "source_sha256": hashes, "parse_errors": errors}


ROLES = {"web", "migration", "worker_sync", "worker_reports", "worker_accounting"}


def hook_key(row):
    return f"{row['path']}:{row['event']}:{row['callback']}"


def local_key(row):
    return f"{row['path']}:{row['scope']}:{row['call']}:{row['target'] or ''}"


def validate_plan(result, plan):
    """Check the proposed inventory coverage, never grant execution authority."""
    expected = {hook_key(row) for row in result["hooks"]}
    expected.update(local_key(row) for row in result["launch_candidates"]
        if row["path"] == "server.py" and row["scope"] == "_local_startup")
    seen, errors = set(), []
    for entry in plan.get("assignments", []):
        key = entry.get("key")
        if key in seen:
            errors.append({"code": "duplicate_assignment", "key": key})
        seen.add(key)
        if entry.get("owner") not in ROLES:
            errors.append({"code": "invalid_owner", "key": key})
    for key in sorted(expected - seen):
        errors.append({"code": "unclassified_entry", "key": key})
    for key in sorted(seen - expected, key=str):
        errors.append({"code": "stale_assignment", "key": key})
    if result["source_sha256"] != plan.get("source_sha256"):
        errors.append({"code": "source_drift"})
    if result["parse_errors"]:
        errors.append({"code": "parse_errors"})
    if any(row.get("unresolved") for row in result["hooks"]):
        errors.append({"code": "dynamic_hook_unresolved"})
    return {"inventory_coverage_passed": not errors, "runtime_verified": False,
        "deployment_authorized": False, "errors": errors,
        "scope": "Detected hooks and server._local_startup launch candidates only"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", type=Path,
        default=Path(__file__).resolve().parents[2] / "backend")
    parser.add_argument("--plan", type=Path, help="Validate an offline proposed ownership plan")
    args = parser.parse_args()
    if not args.backend.is_dir():
        parser.error("backend directory does not exist")
    result = scan(args.backend)
    if args.plan:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        result = validate_plan(result, plan)
        print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
        return 0 if result["inventory_coverage_passed"] else 1
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 1 if result["parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
