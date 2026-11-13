"""Read-only dependency/import inventory. Never imports application modules."""
import ast
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def audit():
    imports, dynamic, startup, missing = [], [], [], []
    tracked = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"], cwd=ROOT, text=True
    ).splitlines()
    for name in tracked:
        if not name.startswith(("backend/", "frontend/", "scripts/")):
            continue
        path = ROOT / name
        if not path.is_file():
            missing.append(name)
            continue
        if path.suffix != ".py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=name)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                imports.extend({"file": name, "line": node.lineno, "module": m} for m in modules)
            if isinstance(node, ast.Call):
                call = ast.unparse(node.func)
                if any(t in call for t in ("import_module", "__import__", "entry_points", "load_entry_point", "spec_from_file_location", "exec_module", "run_module", "run_path")) or call in ("exec", "eval"):
                    dynamic.append({"file": name, "line": node.lineno, "call": call})
                if call.endswith((".on_event", ".add_event_handler")):
                    startup.append({"file": name, "line": node.lineno, "call": call})
    intent = json.loads((ROOT / "release/release-intent-v5.json").read_text())
    source = intent["frontend_source"]["files"]
    mismatches = [r["path"] for r in source if not (ROOT / "frontend" / r["path"]).is_file() or hashlib.sha256((ROOT / "frontend" / r["path"]).read_bytes()).hexdigest() != r["sha256"]]
    return {"source_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(), "imports": imports, "dynamic_load_sites": dynamic, "startup_registration_sites": startup, "missing_build_scope_files": missing, "frontend_intent_source_count": len(source), "frontend_intent_content_mismatches": mismatches, "limit": "Static evidence only. Real imports, distribution entry points and network-isolated runtime still required."}


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
