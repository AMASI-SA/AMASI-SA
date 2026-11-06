"""Owner-run Preview preparation only; never clicks Publish or writes business data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

DEPLOYMENT = "049fba51c13b902cd12808e03e87aca203649fb7"
SOURCE = "befcff8363a47e7b91890b75f9c70ab2c81cb7c2"
RELEASE = "rg5-7aa41b494eaaed8eda944b8a7f0c6fdc5ba6659ee55f36cfb80bc901638bc450"
ACTOR = "android72-cloud-copy-r3-20261106"
BRANCH = "hotfix/prod-snap-meta-final"
ROOT = Path("/app")


def main():
    root = ROOT.resolve(strict=True)
    os.chdir(root)
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
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
    saved = Path(tempfile.mkdtemp(prefix="android72-cloud-copy-r3-"))
    for path in targets:
        destination = saved / path.relative_to(root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(destination))
    log = saved / "frontend-build.log"
    print("Build log and preserved bytecode:", saved, flush=True)
    print("BUILD_RUNNING_DO_NOT_PUBLISH", flush=True)
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(["yarn", "--cwd", "frontend", "build"],
                                   stdout=stream, stderr=subprocess.STDOUT, env=environment)
        while True:
            try:
                code = process.wait(timeout=15)
                break
            except subprocess.TimeoutExpired:
                print("Governed build is running; log:", log, flush=True)
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
