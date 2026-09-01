from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SERVER = BACKEND / "server.py"


def _server_tree() -> ast.Module:
    return ast.parse(SERVER.read_text(encoding="utf-8"))


def _imports(tree: ast.Module, module: str) -> list[ast.alias]:
    return [
        alias
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == module
        for alias in node.names
    ]


def _calls(tree: ast.Module, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == name
    ]


def _name(node: ast.AST) -> str | None:
    return node.id if isinstance(node, ast.Name) else None


def test_diagnostics_imports_use_distinct_explicit_aliases() -> None:
    tree = _server_tree()
    runtime_imports = _imports(tree, "runtime_diagnostics_routes")
    order_imports = _imports(tree, "diagnostics_routes")

    assert [(item.name, item.asname) for item in runtime_imports] == [
        ("attach_diagnostics_routes", "attach_runtime_diagnostics_routes")
    ]
    assert [(item.name, item.asname) for item in order_imports] == [
        ("attach_diagnostics_routes", "attach_order_diagnostics_routes")
    ]

    all_route_imports = runtime_imports + order_imports
    assert all(item.asname is not None for item in all_route_imports)
    assert "attach_diagnostics_routes" not in {
        item.asname or item.name for item in all_route_imports
    }


def test_runtime_and_order_calls_use_their_matching_contracts() -> None:
    tree = _server_tree()
    runtime_calls = _calls(tree, "attach_runtime_diagnostics_routes")
    order_calls = _calls(tree, "attach_order_diagnostics_routes")

    assert len(runtime_calls) == 1
    assert len(order_calls) == 1

    runtime_call = runtime_calls[0]
    assert [_name(arg) for arg in runtime_call.args] == ["app"]
    assert {item.arg for item in runtime_call.keywords} == {
        "mongo_client",
        "state",
    }
    assert runtime_call.lineno < order_calls[0].lineno

    order_call = order_calls[0]
    assert [_name(arg) for arg in order_call.args] == ["api", "db"]
    assert order_call.keywords == []


def test_aliases_cannot_be_shadowed_by_later_server_bindings() -> None:
    tree = _server_tree()
    protected = {
        "attach_runtime_diagnostics_routes",
        "attach_order_diagnostics_routes",
    }
    stored_names = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    declared_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }

    assert protected.isdisjoint(stored_names)
    assert protected.isdisjoint(declared_names)


def test_route_implementations_keep_the_expected_signatures() -> None:
    sys.path.insert(0, str(BACKEND))
    try:
        runtime_module = importlib.import_module("runtime_diagnostics_routes")
        order_module = importlib.import_module("diagnostics_routes")
    finally:
        sys.path.remove(str(BACKEND))

    runtime_signature = inspect.signature(
        runtime_module.attach_diagnostics_routes
    )
    assert list(runtime_signature.parameters) == [
        "app",
        "mongo_client",
        "state",
    ]
    keyword_only = inspect.Parameter.KEYWORD_ONLY
    assert runtime_signature.parameters["mongo_client"].kind is keyword_only
    assert runtime_signature.parameters["state"].kind is keyword_only

    order_signature = inspect.signature(order_module.attach_diagnostics_routes)
    assert list(order_signature.parameters) == ["parent_router", "db"]
    positional = inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert order_signature.parameters["parent_router"].kind is positional
    assert order_signature.parameters["db"].kind is positional
