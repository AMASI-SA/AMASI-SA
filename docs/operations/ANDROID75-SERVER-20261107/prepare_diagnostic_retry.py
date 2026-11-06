"""Owner-run Preview preparation only; never clicks Publish or writes business data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

DEPLOYMENT = "3bd31d56a93ce30f43d514008f545eb2ca3563c3"
SOURCE = "ebb69057d25d6578ae0b28eb4e6aab1ab71f44e3"
RELEASE = "rg5-03f70cc373f6f22c5b7b904ac6de8278d736978c30b6ce74a8094d4dba9ff96c"
ACTOR = "android75-selected-groups-20261107"
BRANCH = "hotfix/prod-snap-meta-final"
ROOT = Path("/app")


def main():
    root = ROOT.resolve(strict=True)
    os.chdir(root)
    runtime_cache = Path(tempfile.mkdtemp(prefix="android75-build-python-cache-"))
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                   "PYTHONPYCACHEPREFIX": str(runtime_cache),
                   "REACT_APP_BACKEND_URL": "https://mezansalla.com"}
    guard = [sys.executable, "-B", "scripts/production_release_guard.py"]

    def out(*args):
        return subprocess.check_output(args, text=True, env=environment).strip()

    def run(*args):
        subprocess.run(args, check=True, env=environment)

    def inactive():
        state = json.loads(out(*guard, "status"))
        if state.get("active") is not False:
            raise RuntimeError("Another release is active; no lease will be cleared")

    def require(condition, message):
        if not condition:
            raise RuntimeError(message)

    inactive()
    require(out("git", "branch", "--show-current") == BRANCH, "Wrong branch")
    require(not out("git", "status", "--porcelain", "--untracked-files=no"),
            "Tracked changes need inspection; nothing will be overwritten")
    run("git", "fetch", "origin", BRANCH)
    require(out("git", "rev-parse", "origin/" + BRANCH) == DEPLOYMENT,
            "Production source advanced; stop and obtain a fresh reviewed plan")
    run("git", "merge-base", "--is-ancestor", "HEAD", DEPLOYMENT)
    inactive()
    preview = subprocess.run(["supervisorctl", "status", "backend"],
                             capture_output=True, text=True, env=environment)
    print(preview.stdout.strip(), flush=True)
    fields = preview.stdout.split()
    require(len(fields) >= 2 and fields[0] == "backend", "Inspect Preview service status")
    if fields[1] in {"RUNNING", "STARTING", "BACKOFF"}:
        run("supervisorctl", "stop", "backend")
    else:
        require(fields[1] in {"STOPPED", "EXITED", "FATAL"}, "Unexpected Preview state")

    run("git", "merge", "--ff-only", "origin/" + BRANCH)
    require(out("git", "rev-parse", "HEAD") == DEPLOYMENT, "Unexpected HEAD")
    inactive()
    backend = root / "backend"
    require(backend.is_dir() and not backend.is_symlink(), "Unsafe Backend directory")
    caches = []
    for path in sorted(backend.rglob("__pycache__"), key=lambda p: len(p.parts)):
        if not any(parent in caches for parent in path.parents):
            caches.append(path)
    loose = [p for pattern in ("*.pyc", "*.pyo") for p in backend.rglob(pattern)
             if not any(parent in caches for parent in p.parents)]
    targets = caches + loose
    for path in targets:
        require(not path.is_symlink() and path.resolve().is_relative_to(backend.resolve()),
                "Unsafe bytecode path; nothing will be removed")
        require(not out("git", "ls-files", "--", path.relative_to(root).as_posix()),
                "Tracked bytecode needs inspection")
    saved = Path(tempfile.mkdtemp(prefix="android75-selected-groups-"))
    for path in targets:
        destination = saved / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))
    log = saved / "frontend-build.log"
    print("Build log and preserved bytecode:", saved, flush=True)
    print("BUILD_RUNNING_DO_NOT_PUBLISH", flush=True)
    command = ["yarn", "--cwd", "frontend", "build"]
    trace = saved / "private-open-trace.log"
    if shutil.which("strace"):
        command = ["strace", "-f", "-qq", "-e", "trace=openat", "-o", str(trace), *command]
    observed = set()
    ticks = 0
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, env=environment)
        while True:
            try:
                code = process.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                ticks += 1
                for entry in Path("/proc").iterdir():
                    if not entry.name.isdigit() or entry.name in observed:
                        continue
                    try:
                        argv = entry.joinpath("cmdline").read_bytes().split(b"\0")
                        if not argv or b"python" not in Path(os.fsdecode(argv[0])).name.encode():
                            continue
                        script = next((Path(os.fsdecode(arg)).name for arg in argv[1:] if arg.endswith(b".py")), None)
                        if script not in {"emergent_deployment_adapter.py", "frontend_release_toolchain.py", "verify_frontend_build.py"}:
                            continue
                        observed.add(entry.name)
                        values = dict(item.split(b"=", 1) for item in entry.joinpath("environ").read_bytes().split(b"\0") if b"=" in item)
                        print(json.dumps({"build_python_pid": int(entry.name), "script": script,
                            "no_bytecode_flag": b"-B" in argv,
                            "no_bytecode_env": values.get(b"PYTHONDONTWRITEBYTECODE") == b"1"}), flush=True)
                    except OSError:
                        pass
                if ticks % 30 == 0:
                    print("Governed build is running; log:", log, flush=True)
    if trace.exists():
        writes = [line for line in trace.read_text(errors="replace").splitlines()
                  if "/app/backend/" in line and ".pyc" in line and "O_CREAT" in line]
        print(json.dumps({"traced_backend_bytecode_writes": writes[-20:]}, indent=2), flush=True)
    if code:
        print(log.read_text(encoding="utf-8", errors="replace")[-6000:], flush=True)
        raise RuntimeError("Build failed; no release lease was prepared")

    identity = json.loads((backend / "release_identity.json").read_text(encoding="utf-8"))
    require(identity.get("source_git_sha") == SOURCE and identity.get("release_id") == RELEASE,
            "Generated identity differs from the reviewed plan")
    inactive()
    prepared = json.loads(out(*guard, "prepare", "--actor", ACTOR))
    require((prepared.get("source_git_sha"), prepared.get("deployment_git_sha"),
             prepared.get("release_id")) == (SOURCE, DEPLOYMENT, RELEASE),
            "Prepared identity differs; do not publish or clear a different lease")
    print(json.dumps({key: prepared.get(key) for key in
                      ("source_git_sha", "deployment_git_sha", "release_id", "prepared_at")},
                     indent=2), flush=True)
    run(*guard, "prepublish")
    print("FINAL_READY_TO_PUBLISH=true", flush=True)


if __name__ == "__main__":
    main()
