"""Bounded, read-only inspection of the known Mezan Preview host."""
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.dont_write_bytecode = True
HOST = "agent-env-f5e6b93a-68a2-4155-84ad-e55b4fa936d3"
APP = Path("/app")
BASE = Path("/root/mezan-preview-supplier-r2-20261026")
BUNDLE = BASE / "bundle"
READY = APP / ".worktrees/mz2-uat-1128-runtime-20260921/ready.json"
NODE = Path("/root/.cache/mezan-release-toolchains/v1/node-v22.23.2-yarn-1.22.22-linux-arm64/node/bin/node")
LIMIT = 1048576
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")


def emit(kind, **values):
    print(json.dumps(dict(kind=kind, **values), sort_keys=True), flush=True)


def run(argv, timeout=8, env=ENV):
    try:
        result = subprocess.run(argv, cwd=str(APP), env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout, check=False)
        return result.returncode, result.stdout[:LIMIT].decode("utf-8", "replace"), None
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, "", type(error).__name__


def read_regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > LIMIT:
        raise ValueError("not a bounded regular file")
    with path.open("rb") as stream:
        value = stream.read(LIMIT + 1)
    if len(value) > LIMIT:
        raise ValueError("file exceeded limit")
    return value


def file_info(path):
    result = {"path": str(path)}
    try:
        info = path.lstat()
        result.update(present=True, uid=info.st_uid, gid=info.st_gid,
                      mode=oct(stat.S_IMODE(info.st_mode)), bytes=info.st_size,
                      symlink=stat.S_ISLNK(info.st_mode))
        if stat.S_ISREG(info.st_mode) and info.st_size <= LIMIT:
            result["sha256"] = hashlib.sha256(read_regular(path)).hexdigest()
    except OSError as error:
        result.update(present=False, error=type(error).__name__)
    except ValueError as error:
        result["error"] = type(error).__name__
    return result


def safe_path(value):
    return value if isinstance(value, str) and len(value) <= 400 and re.fullmatch(r"/[A-Za-z0-9_./+-]+", value) else None


def main():
    if socket.gethostname() != HOST or not APP.is_dir():
        emit("STOP", reason="unexpected_host_or_missing_app")
        return 1
    routing = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")
    if any(name in os.environ for name in routing):
        emit("STOP", reason="unexpected_git_routing")
        return 1
    code, output, error = run([sys.executable, "-B", str(APP / "scripts/production_release_guard.py"), "status"])
    try:
        guard = json.loads(output) if code == 0 else {}
        if not isinstance(guard, dict):
            raise ValueError()
    except (ValueError, TypeError):
        guard, error = {}, "InvalidGuardJSON"
    hcode, head, herror = run(["git", "--no-optional-locks", "-C", str(APP), "rev-parse", "HEAD"])
    lease = guard.get("lease") if isinstance(guard.get("lease"), dict) else guard
    emit("release", active=guard.get("active"), actor=lease.get("actor"),
         deployment_git_sha=lease.get("deployment_git_sha"),
         head=head.strip() if re.fullmatch(r"[0-9a-f]{40}\s*", head) else None,
         guard_exit=code, guard_error=error, head_exit=hcode, head_error=herror,
         inspection_only=True)
    code, output, error = run(["supervisorctl", "status", "frontend", "backend"])
    statuses = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in ("frontend", "backend") and parts[1] in ("STOPPED", "STARTING", "RUNNING", "BACKOFF", "STOPPING", "EXITED", "FATAL", "UNKNOWN"):
            statuses[parts[0]] = parts[1]
    emit("supervisor", services=statuses, exit=code, error=error)
    configs = []
    for path in sorted(Path("/etc/supervisor/conf.d").glob("*.conf"))[:12]:
        try:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(read_regular(path).decode("utf-8"))
            for name in ("frontend", "backend"):
                section = "program:" + name
                if parser.has_section(section):
                    command = shlex.split(parser.get(section, "command", fallback=""))
                    first = command[0] if command else ""
                    configs.append({"path": str(path), "program": name,
                                    "directory": safe_path(parser.get(section, "directory", fallback="")),
                                    "executable": first if re.fullmatch(r"[A-Za-z0-9_./+-]{1,400}", first) else None})
        except (OSError, ValueError, UnicodeError, configparser.Error) as error:
            configs.append({"path": str(path), "error": type(error).__name__})
    emit("supervisor_configs", configs=configs)
    emit("known_files", files=[file_info(path) for path in (
        BASE / "serve.py", BUNDLE / "manifest.json", BUNDLE / "adapter.sha256",
        BUNDLE / "preview_password_runtime.py", READY, BUNDLE / "frontend/build/build-meta.json")])
    ready_result = {}
    try:
        ready = json.loads(read_regular(READY))
        ready_result["node"] = safe_path(ready.get("node"))
        ready_result["node_exists"] = bool(ready_result["node"] and Path(ready_result["node"]).is_file())
        value = ready.get("env", {}).get("REACT_APP_BACKEND_URL")
        origin = urllib.parse.urlsplit(value) if isinstance(value, str) else None
        ready_result["api_origin_present"] = isinstance(value, str)
        ready_result["api_same_origin"] = value == ""
        ready_result["api_origin"] = (origin.scheme + "://" + origin.netloc) if origin and origin.scheme in ("http", "https") and origin.hostname and not origin.username and not origin.password and not origin.query and not origin.fragment and origin.path in ("", "/") else None
    except (OSError, ValueError, TypeError, AttributeError) as error:
        ready_result["error"] = type(error).__name__
    emit("ready_allowlist", **ready_result)
    node_info = file_info(NODE)
    if NODE.is_file() and not NODE.is_symlink():
        code, output, error = run([str(NODE), "--version"], timeout=3, env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        node_info.update(version=output.strip() if re.fullmatch(r"v\d+\.\d+\.\d+\s*", output) else None, exit=code, error=error)
    emit("cached_node", **node_info)
    disks = []
    for path in (APP, Path("/tmp")):
        try:
            usage = shutil.disk_usage(path)
            disks.append({"path": str(path), "free_bytes": usage.free, "total_bytes": usage.total})
        except OSError as error:
            disks.append({"path": str(path), "error": type(error).__name__})
    emit("disk", disks=disks)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, message, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    # Defer frontend requests until its server-side proxy target is verified.
    for port in (8001,):
        for route in ("/api/live", "/api/auth/me"):
            result = {"port": port, "route": route}
            try:
                request = urllib.request.Request("http://127.0.0.1:" + str(port) + route, method="GET", headers={"Accept": "application/json"})
                try:
                    response = opener.open(request, timeout=2)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    result.update(status=response.code, mime=response.headers.get_content_type())
            except (OSError, ValueError, urllib.error.URLError) as error:
                result["error"] = type(error).__name__
            emit("local_http", **result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
