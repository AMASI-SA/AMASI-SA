"""Regression test for is_cod scope in Plan-B manual send."""

import ast
from pathlib import Path


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "integrations"
    / "qoyod_manual"
    / "send.py"
)


def test_run_all_steps_receives_is_cod_explicitly():
    source = SOURCE_PATH.read_text()
    tree = ast.parse(source)

    run_all_steps = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_run_all_steps"
    )

    all_args = {
        arg.arg
        for arg in (
            run_all_steps.args.args
            + run_all_steps.args.kwonlyargs
        )
    }

    assert "is_cod" in all_args

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_run_all_steps"
    ]

    assert len(calls) == 1

    passed_keywords = {
        keyword.arg for keyword in calls[0].keywords
    }
    assert "is_cod" in passed_keywords
