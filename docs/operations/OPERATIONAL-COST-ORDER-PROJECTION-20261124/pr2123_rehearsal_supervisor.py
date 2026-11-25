#!/usr/bin/env python3
"""Launch one PR2123 rehearsal worker; external exact-D CI/review are prerequisites.

Usage: python3 -I -B pr2123_rehearsal_supervisor.py IMMUTABLE_EVIDENCE_COMMIT
This launcher does not establish those external gates or alter release lifecycle.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import urllib.request


PREFIX = "mezan-pr2123-supervisor-"
HELPER = "pr2123_guarded_rehearsal.py"
ENVELOPE = "pr2123-reviewed-envelope.json"
SUPERVISOR = "pr2123_rehearsal_supervisor.py"
BASE = "https://raw.githubusercontent.com/AMASI-SA/AMASI-SA/{commit}/docs/operations/OPERATIONAL-COST-ORDER-PROJECTION-20261124/"
PINS = {
    HELPER: (43667, "0e19197c46ca81293bdad2658ca03b039488afc24eb6dce0112b9ff36281b008"),
    ENVELOPE: (1363, "d7e18b6704d659d3a8fd82f9422255607d1cb16db33fc519f407bbc88b1d5db1"),
}


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, data):
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def record(path, value):
    write_new(path, (json.dumps(value, indent=2) + "\n").encode())


def check_bytes(name, data):
    length, sha256 = PINS[name]
    if len(data) != length or hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError("PIN_MISMATCH: " + name)


def read_pinned(stage, name):
    path = stage / name
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("NON_REGULAR_INPUT: " + name)
        data = stream.read(PINS[name][0] + 1)
    check_bytes(name, data)
    return data


def child_env():
    env = os.environ.copy()
    for key in ("PYTHONOPTIMIZE", "PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def log_digest(path):
    if not path.exists():
        return None
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return {"file": path.name, "bytes": size, "sha256": digest.hexdigest()}


def worker(stage):
    info = stage.lstat()
    if (stage.parent != Path("/tmp") or not stage.name.startswith(PREFIX)
            or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700 or stage.resolve() != stage):
        raise ValueError("UNSAFE_OWNED_STAGE")
    # Exclusive receipt also refuses a second worker invocation in this stage.
    record(stage / "worker-start.json", {"started_at": now(), "worker_pid": os.getpid(),
           "cwd": "/app", "input_pins": PINS, "automatic_retry": False})
    code, error, helper_pid = None, None, None
    out_path, err_path = stage / "stdout.log", stage / "stderr.log"
    try:
        helper_bytes = read_pinned(stage, HELPER)
        read_pinned(stage, ENVELOPE)
        compile(helper_bytes, str(stage / HELPER), "exec", dont_inherit=True, optimize=0)
        with out_path.open("xb") as out, err_path.open("xb") as err:
            process = subprocess.Popen(
                [sys.executable, "-B", str(stage / HELPER), str(stage / ENVELOPE)],
                cwd="/app", env=child_env(), stdin=subprocess.DEVNULL,
                stdout=out, stderr=err, close_fds=True)
            helper_pid = process.pid
            code = process.wait()
            for stream in (out, err):
                stream.flush()
                os.fsync(stream.fileno())
    except Exception as exc:
        error = type(exc).__name__ + ": " + str(exc)
    record(stage / "worker-exit.json", {"finished_at": now(), "worker_pid": os.getpid(),
           "helper_pid": helper_pid, "helper_returncode": code,
           "worker_error": error, "stdout": log_digest(out_path), "stderr": log_digest(err_path),
           "automatic_retry": False, "cleanup_or_rollback": False})
    if error is not None or code is None:
        return 2
    return code if code >= 0 else 128 - code


def launch(commit):
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        raise ValueError("IMMUTABLE_40_HEX_EVIDENCE_COMMIT_REQUIRED")
    stage = Path(tempfile.mkdtemp(prefix=PREFIX, dir="/tmp"))
    print(json.dumps({"status": "STAGING", "stage": str(stage)}), flush=True)
    opener = urllib.request.build_opener(NoRedirect())
    downloaded = []
    for name, (length, sha256) in PINS.items():
        url = BASE.format(commit=commit) + name
        # At most expected length + 1 bytes; no redirect, retry or alternate URL.
        with opener.open(url, timeout=30) as response:
            if response.status != 200:
                raise ValueError("DOWNLOAD_STATUS: " + str(response.status))
            data = response.read(length + 1)
        check_bytes(name, data)
        write_new(stage / name, data)
        downloaded.append({"file": name, "url": url, "bytes": length, "sha256": sha256})
    compile(read_pinned(stage, HELPER), str(stage / HELPER), "exec", dont_inherit=True, optimize=0)
    supervisor_bytes = Path(__file__).read_bytes()
    write_new(stage / SUPERVISOR, supervisor_bytes)
    record(stage / "verified-inputs.json", {"verified_at": now(), "evidence_commit": commit,
           "files": downloaded, "helper_compile_only": True,
           "supervisor_sha256": hashlib.sha256(supervisor_bytes).hexdigest(),
           "external_gates": "Caller must satisfy exact-D CI and independent final review before invocation."})
    with (stage / "supervisor.log").open("xb") as supervisor_log:
        process = subprocess.Popen(
            [sys.executable, "-B", str(stage / SUPERVISOR), "--worker", str(stage)],
            cwd=str(stage), env=child_env(), stdin=subprocess.DEVNULL,
            stdout=supervisor_log, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True)
    launched = {"status": "STARTED_NOT_RESULT", "worker_pid": process.pid,
                "stage": str(stage), "stdout": str(stage / "stdout.log"),
                "stderr": str(stage / "stderr.log"), "exit_receipt": str(stage / "worker-exit.json")}
    print(json.dumps(launched), flush=True)
    record(stage / "launch.json", launched)
    return 0


def main():
    os.umask(0o077)
    if sys.flags.optimize:
        raise ValueError("PYTHON_OPTIMIZATION_NOT_ALLOWED")
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        return worker(Path(sys.argv[2]))
    if len(sys.argv) != 2:
        raise ValueError("ONE_IMMUTABLE_EVIDENCE_COMMIT_REQUIRED")
    return launch(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
