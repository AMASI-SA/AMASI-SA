#!/usr/bin/env python3
"""Construct exact candidate factories under stronger zero-I/O preflight guards.

This is not a server, authentication substitute, export, import or lifespan test.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE_SHA = "9a851854132bd71be922c4aae24b4cc20a546e41"
SOURCE_TREE = "436199d239a75910d94d441e9eb47408aea80080"
RUNTIME_MANIFEST_SHA = "4742718a69563fdd8fd4129e884bd268870f37da8c997935463cfdfa3bcb372d"

CHILD = r'''
import contextlib
import json
import os
import re
import sys
from private_guard import require_installed
from private_common import SOURCE_SHA, load_config, RuntimeBlocked
require_installed()
config = load_config(os.environ["MEZAN_PRIVATE_RUNTIME_CONFIG"])
attempts = {"network": 0, "filesystem_mutation": 0}
sink = open(os.devnull, "w")
def no_io(event, args):
    if event in {"socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo", "socket.bind"}:
        attempts["network"] += 1
        raise PermissionError("factory_preflight_network_forbidden")
    mutation = event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename", "os.link",
        "os.symlink", "os.chmod", "os.chown", "os.truncate", "os.utime"}
    if event == "open":
        mutation = bool(args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    if mutation:
        attempts["filesystem_mutation"] += 1
        raise PermissionError("factory_preflight_write_forbidden")
sys.addaudithook(no_io)
receipt = {"ok": False, "gate": "factory_construction_only"}
try:
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        from private_app import create_app
        from private_http import AUTH_METHODS, FEE_PATHS, GET_SUFFIXES, GET_TEMPLATES, PREFIX
        app = create_app(config)
        expected = {("GET", PREFIX + suffix) for suffix in GET_SUFFIXES | GET_TEMPLATES}
        expected |= {("GET", path) for path in FEE_PATHS}
        expected |= {(method, path) for path, method in AUTH_METHODS.items()}
        expected.add(("DELETE", PREFIX + "/workspace/read-demand/{key}"))
        actual = {(method, route.path) for route in app.routes for method in route.methods}
        assert actual == expected and len(actual) == 41
        app.build_middleware_stack()
        assert attempts == {"network": 0, "filesystem_mutation": 0}
    receipt.update(ok=True, source_commit=SOURCE_SHA, mounted_routes=41,
        product_get_routes=34, fee_get_routes=2, auth_routes=4, demand_release_routes=1,
        lifespan_started=False, served_requests=0)
except BaseException as error:
    receipt["error_type"] = type(error).__name__
    if isinstance(error, RuntimeBlocked) and re.fullmatch(r"[a-z_]{1,120}", str(error)):
        receipt["code"] = str(error)
    missing = getattr(error, "name", None)
    if isinstance(missing, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", missing):
        receipt["missing_module"] = missing
finally:
    sink.close()
receipt["io_attempts"] = attempts
print(json.dumps(receipt, sort_keys=True))
raise SystemExit(0 if receipt["ok"] else 1)
'''

def main():
    if len(sys.argv) != 4:
        raise ValueError("Three absolute private paths required")
    source, tooling, task_root = map(Path, sys.argv[1:])
    assert all(path.is_absolute() and path.is_dir() and not path.is_symlink()
        for path in (source, tooling, task_root))
    for command, expected in [(["rev-parse", "HEAD"], SOURCE_SHA),
        (["rev-parse", "HEAD^{tree}"], SOURCE_TREE),
        (["status", "--porcelain", "--untracked-files=no"], "")]:
        value = subprocess.check_output(["git", "-C", str(source), *command],
            text=True, timeout=15).strip()
        assert value == expected
    manifest_raw = (tooling / "sha256-manifest.json").read_bytes()
    assert hashlib.sha256(manifest_raw).hexdigest() == RUNTIME_MANIFEST_SHA
    for item in json.loads(manifest_raw)["files"]:
        assert Path(item["path"]).name == item["path"]
        raw = (tooling / item["path"]).read_bytes()
        assert len(raw) == item["bytes"]
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
    sys.path.insert(0, str(tooling))
    from private_common import explicit_environment, load_config
    with tempfile.TemporaryDirectory(prefix="factory-preflight-", dir=task_root) as name:
        work = Path(name)
        export = work / "empty-export"
        export.mkdir(mode=0o700)
        config_path = work / "runtime.json"
        value = {"source_root": str(source), "export_directory": str(export),
            "manifest_sha256": "0" * 64, "database_name": "mezan_pr2095_20261125_factoryprobe",
            "expected_owner_id": "inert-preflight-owner",
            "preview_origin": "https://salla-analytics.preview.emergentagent.com"}
        fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
        config = load_config(config_path)
        result = subprocess.run([sys.executable, "-B", "-c", CHILD], cwd=work,
            env=explicit_environment(config, "api"), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=45)
        try:
            receipt = json.loads(result.stdout)
        except (ValueError, TypeError):
            receipt = {"ok": False, "gate": "factory_construction_only",
                "code": "child_failed_before_metadata_receipt", "exit_code": result.returncode}
        print(json.dumps(receipt, sort_keys=True))
        return 0 if result.returncode == 0 and receipt.get("ok") is True else 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(json.dumps({"ok": False, "gate": "factory_preflight_bootstrap",
            "error_type": type(error).__name__}, sort_keys=True))
        raise SystemExit(1)

