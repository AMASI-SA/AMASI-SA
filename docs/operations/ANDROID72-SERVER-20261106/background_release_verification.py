"""Detached lifecycle only; the original official verification helper is unchanged."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path("/app")
JOB = Path("/root/android72-r3-background-verification")
ACTOR = "android72-cloud-copy-r3-20261106"
DEPLOYMENT = "049fba51c13b902cd12808e03e87aca203649fb7"
RELEASE = "rg5-7aa41b494eaaed8eda944b8a7f0c6fdc5ba6659ee55f36cfb80bc901638bc450"
HELPER = "dc4490bcca2764a487c23a7bc1443cf27554d7c3:docs/operations/ANDROID72-SERVER-20261106/verify_cloud_copy_release.py"
HELPER_HASH = "4e10d40d109bec80961d84541f5c4ab1596dbbcea5568ab10adc9af66ab55122"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def status():
    result = JOB / "result.json"
    report = {"job_directory": str(JOB)}
    if result.exists():
        report.update(json.loads(result.read_text(encoding="utf-8")))
    elif (JOB / "job.json").exists():
        job = json.loads((JOB / "job.json").read_text(encoding="utf-8"))
        report.update(job)
        try:
            args = Path("/proc", str(job["pid"]), "cmdline").read_bytes().split(b"\0")
            alive = os.fsencode(str(JOB / "controller.py")) in args
        except OSError:
            alive = False
        report["state"] = "running" if alive else "stopped_without_final_result"
    else:
        report["state"] = "not_started_or_setup_incomplete"
    log = JOB / "progress.log"
    if log.exists():
        report["last_output"] = log.read_text(encoding="utf-8", errors="replace")[-3000:]
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return report


def start():
    os.chdir(ROOT.resolve(strict=True))
    if JOB.exists():
        require(JOB.is_dir() and not JOB.is_symlink(), "Unsafe existing job directory")
        return status()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            args = process.joinpath("cmdline").read_bytes().decode(errors="replace").split("\0")
        except OSError:
            continue
        require(not (any(a.endswith("production_release_guard.py") for a in args) and "verify" in args),
                "An official verification is already running; leave it alone")
    guard = [sys.executable, "-B", "scripts/production_release_guard.py", "status"]
    lease = json.loads(subprocess.check_output(guard, text=True, timeout=8))
    require(lease.get("active") is True and lease.get("actor") == ACTOR and
            lease.get("deployment_git_sha") == DEPLOYMENT and lease.get("release_id") == RELEASE,
            "Expected own release lease is absent; do not change other leases")
    payload = subprocess.check_output(["git", "show", HELPER])
    require(hashlib.sha256(payload).hexdigest() == HELPER_HASH, "Official helper hash mismatch")
    JOB.mkdir(mode=0o700)
    (JOB / "official_verify.py").write_bytes(payload)
    (JOB / "controller.py").write_bytes(Path(__file__).read_bytes())
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    with (JOB / "progress.log").open("ab") as stream:
        process = subprocess.Popen([sys.executable, "-B", str(JOB / "controller.py"), "worker"],
                                   cwd=ROOT, stdin=subprocess.DEVNULL, stdout=stream,
                                   stderr=subprocess.STDOUT, start_new_session=True, env=environment)
    record = {"state": "started", "pid": process.pid,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "status_command": "python -B " + str(JOB / "controller.py") + " status"}
    (JOB / "job.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2), flush=True)


def worker():
    payload = (JOB / "official_verify.py").read_bytes()
    require(hashlib.sha256(payload).hexdigest() == HELPER_HASH, "Official helper hash mismatch")
    code = subprocess.call([sys.executable, "-B", str(JOB / "official_verify.py")], cwd=ROOT)
    result = {"state": "completed" if code == 0 else "failed", "helper_exit_code": code,
              "finished_at": datetime.now(timezone.utc).isoformat()}
    temporary = JOB / "result.json.tmp"
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, JOB / "result.json")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    require(action in {"start", "worker", "status"}, "Unknown action")
    {"start": start, "worker": worker, "status": status}[action]()
