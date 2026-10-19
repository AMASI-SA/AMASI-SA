"""Run Security Gate contracts for the checked-out source, not the PR merge ref.

The pull_request workflow can include a later base-branch test while checkout
uses the PR head. Only the known BUILD37 contract has a source applicability
rule; every core/other requested test remains mandatory. Never import runtime
modules to decide scope (the mobile module installs operational guards).
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys


CORE_TESTS = (
    "backend/tests/test_browser_security.py",
    "backend/tests/test_email_otp_security_v1.py",
    "backend/tests/test_auth_session_revocation_v1.py",
    "backend/tests/test_login_security_v1.py",
    "backend/tests/test_mfa_security_v1.py",
    "backend/tests/test_passkey_security_v1.py",
    "backend/tests/test_progressive_login_security_v1.py",
    "backend/tests/test_customer_intelligence_instagram_provisioning.py",
    "backend/tests/test_customer_intelligence_instagram_ingress.py",
    "backend/tests/test_meta_instagram_webhook_deadline.py",
    "backend/tests/test_instagram_production_safe_diagnostics.py",
)
BUILD37_TEST = "backend/tests/test_build37_preparation_file_permission.py"
MOBILE_SOURCE = "backend/mobile_app_request_context.py"
PREPARATION_ROUTE = "/api/preparation-file-safety-v1"


def has_legacy_preparation_binding(root: Path) -> bool:
    """Recognize only the explicit pre-BUILD37 binding; ambiguity fails closed."""
    module = ast.parse((root / MOBILE_SOURCE).read_text(encoding="utf-8"))
    bindings = []
    for node in module.body:
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        elif isinstance(node, ast.Assign):
            targets = node.targets
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == "MOBILE_ROUTE_PERMISSIONS" for t in targets):
            if not isinstance(node.value, (ast.Tuple, ast.List)):
                raise ValueError("Unknown mobile permission table; security scope cannot be established")
            for entry in node.value.elts:
                if not isinstance(entry, (ast.Tuple, ast.List)) or len(entry.elts) != 2:
                    raise ValueError("Unknown mobile permission entry; security scope cannot be established")
                route, permissions = entry.elts
                if not isinstance(route, ast.Constant) or not isinstance(route.value, str):
                    raise ValueError("Non-literal mobile route; security scope cannot be established")
                if route.value == PREPARATION_ROUTE:
                    bindings.append(permissions)
    if len(bindings) != 1:
        raise ValueError("Missing or ambiguous preparation permission binding")
    permissions = bindings[0]
    if not (
        isinstance(permissions, ast.Call)
        and isinstance(permissions.func, ast.Name)
        and permissions.func.id == "_permissions"
        and not permissions.keywords
        and all(isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in permissions.args)
    ):
        raise ValueError("Unknown preparation permission expression")
    return [arg.value for arg in permissions.args] == ["app.page.my_products"]


def select_tests(root: Path, requested: list[str]) -> tuple[list[str], str]:
    legacy = has_legacy_preparation_binding(root)
    build37_required = (root / BUILD37_TEST).exists() or not legacy
    # Core contracts cannot disappear from the job by shortening workflow args.
    selected = list(dict.fromkeys((*CORE_TESTS, *requested)))
    if build37_required:
        if BUILD37_TEST not in selected:
            selected.append(BUILD37_TEST)
        scope = "REQUIRED: source contains the test or a non-legacy preparation binding"
    else:
        selected = [path for path in selected if path != BUILD37_TEST]
        scope = "NOT_APPLICABLE: test absent and explicit pre-BUILD37 binding proven"
    for path in selected:
        if not path.startswith("backend/tests/test_") or not path.endswith(".py") or ".." in Path(path).parts:
            raise ValueError(f"Unexpected Security Gate test path: {path}")
        if not (root / path).is_file():
            raise ValueError(f"Required Security Gate test is missing: {path}")
    return selected, scope


def run(root: Path, requested: list[str]) -> int:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    expected = os.environ.get("SECURITY_SOURCE_SHA")
    if expected and expected != head:
        raise ValueError("Security checkout differs from the expected source SHA")
    selected, scope = select_tests(root, requested)
    print(json.dumps({"source_git_sha": head, "build37_scope": scope, "required_tests": selected}), flush=True)
    # Propagate collection, assertion and execution failures unchanged; no retry.
    return subprocess.run([sys.executable, "-m", "pytest", "-q", *selected], cwd=root).returncode


def main() -> int:
    try:
        return run(Path(__file__).resolve().parents[1], sys.argv[1:])
    except (OSError, SyntaxError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Security scope FAIL CLOSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
