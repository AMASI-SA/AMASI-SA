"""Pinned original-workspace rehearsal; never prepares or publishes a release."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time

PINS_REVIEWED = False
SOURCE = "7944a567fef519b3a1f9eb933fc463fbdd0c8575"
BASE = "77e4acf33e8f5432d97bda5d7208983f0fadc599"
INTENT_COMMIT = "ece0b24b0978c5064c4e605b577ad2722158f56a"
MERGE = "UNPINNED"
TREE = "b5aba4c6ec2755fb27fe084c2b74f87f9d8fddf6"
INTENT_SHA256 = "a7d098c77d88b64250a937b58664085bd235a45ab994c34c37161d11c1fe260b"
RELEASE_ID = "rg5-d2259549478abdd0e0879d65d1de0defce94f7545b1f62f473799779c9151c1c"
EXPECTED_PRESERVED_COUNT = 120
ROOT = Path("/app")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", REACT_APP_BACKEND_URL="https://mezansalla.com")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((json.dumps(value, sort_keys=True, indent=2) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


class RequestedSignal(BaseException):
    pass


def main():
    require(PINS_REVIEWED and len(MERGE) == 40, "Deployment pins have not been reviewed")
    require(ROOT.is_dir() and not ROOT.is_symlink(), "Unexpected workspace root")
    require(not any(ENV.get(k) for k in ("GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE")),
            "Unexpected GitHub dispatcher markers")
    def terminate_requested(signum, _frame):
        raise SystemExit(f"Runner interrupted by signal {signum}")
    signal.signal(signal.SIGTERM, terminate_requested)
    evidence = Path(tempfile.mkdtemp(prefix="mezan-pr1923-original-"))
    print(json.dumps({"evidence_directory": str(evidence), "source": SOURCE, "deployment": MERGE}), flush=True)
    sequence = 0

    def run(argv, label, cwd=ROOT, timeout=1800):
        nonlocal sequence
        sequence += 1
        prefix = evidence / f"{sequence:02d}-{label}"
        started = time.time()
        process = None
        failure = None
        code = None
        termination = None
        cleanup_error = None
        deferred_signals = []

        def defer(signum, _frame):
            if signum not in deferred_signals:
                deferred_signals.append(signum)

        handlers = {sig: signal.signal(sig, defer) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            with prefix.with_suffix(".stdout").open("wb") as stdout, prefix.with_suffix(".stderr").open("wb") as stderr:
                try:
                    if deferred_signals:
                        raise RequestedSignal()
                    process = subprocess.Popen(argv, cwd=cwd, env=ENV, stdout=stdout, stderr=stderr,
                                               start_new_session=True)
                    deadline = time.monotonic() + timeout
                    while True:
                        if deferred_signals:
                            raise RequestedSignal()
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(argv, timeout)
                        try:
                            code = process.wait(timeout=min(0.25, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
                except BaseException as exc:
                    failure = exc
                    termination = (signal.Signals(deferred_signals[0]).name
                                   if isinstance(exc, RequestedSignal) else type(exc).__name__)
                    try:
                        if process is not None:
                            try:
                                os.killpg(process.pid, signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                            try:
                                process.wait(timeout=10)
                            except subprocess.TimeoutExpired:
                                pass
                            # This is the private group of the current invocation.
                            # Stop descendants even when Yarn has already exited.
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            code = process.wait(timeout=10)
                    except BaseException as cleanup_exc:
                        cleanup_error = type(cleanup_exc).__name__
                    finally:
                        if process is not None:
                            code = process.poll()
                stdout.flush()
                stderr.flush()
                os.fsync(stdout.fileno())
                os.fsync(stderr.fileno())
            output = prefix.with_suffix(".stdout").read_bytes()
            errors = prefix.with_suffix(".stderr").read_bytes()
            receipt = {"argv": argv, "cwd": str(cwd), "exit_code": code, "termination": termination,
                       "cleanup_error": cleanup_error,
                       "signals_observed_before_receipt": [signal.Signals(sig).name for sig in deferred_signals],
                       "elapsed_seconds": round(time.time() - started, 3),
                       "stdout_sha256": sha(output), "stderr_sha256": sha(errors)}
            write_json(prefix.with_suffix(".json"), receipt)
            print(json.dumps({"step": label, "exit_code": code, "termination": termination,
                              "cleanup_error": cleanup_error,
                              "elapsed_seconds": receipt["elapsed_seconds"]}), flush=True)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
        if deferred_signals:
            signum = deferred_signals[0]
            if callable(handlers[signum]):
                handlers[signum](signum, None)
            raise SystemExit(128 + signum)
        if failure is not None:
            raise RuntimeError(f"Step interrupted: {label}; retained evidence: {prefix}") from failure
        require(code == 0, f"Step failed: {label}; retained evidence: {prefix}")
        return output.decode("utf-8", errors="replace")

    def git(*args):
        return run(["git", *args], "git-" + args[0]).strip()

    def inactive():
        value = json.loads(run([sys.executable, "-B", "scripts/production_release_guard.py", "status"], "guard-status"))
        require(value.get("active") is False and not value.get("incomplete_lease_directory"),
                "Shared release is active or incomplete")

    def inventory():
        paths = [p for p in run(["git", "ls-files", "--others", "--exclude-standard", "-z"], "preserved-paths").split("\0") if p]
        records = []
        for relative in sorted(paths):
            path = ROOT / relative
            info = path.lstat()
            if stat.S_ISREG(info.st_mode):
                content, kind = path.read_bytes(), "file"
            elif stat.S_ISLNK(info.st_mode):
                content, kind = os.readlink(path).encode(), "symlink"
            else:
                raise RuntimeError("Unsupported preserved entry type")
            records.append({"path": relative, "kind": kind, "mode": stat.S_IMODE(info.st_mode),
                            "bytes": len(content), "sha256": sha(content)})
        return records

    def clean_tracked():
        require(git("status", "--porcelain", "--untracked-files=no") == "", "Tracked workspace changed")

    inactive()
    require(git("branch", "--show-current") == "hotfix/prod-snap-meta-final", "Unexpected branch")
    require(git("rev-parse", "HEAD") == BASE, "Previous deployment does not match completed base")
    clean_tracked()
    original = inventory()
    require(len(original) == EXPECTED_PRESERVED_COUNT, "Preserved entry count changed; inspect before release")
    (evidence / "preserved-before.json").write_text(json.dumps(original, sort_keys=True, indent=2) + "\n")
    generated = ("frontend/build", "frontend/.release", "frontend/node_modules", "backend/release_identity.json")
    require(not any(r["path"] == p or r["path"].startswith(p + "/") for r in original for p in generated),
            "A preserved entry overlaps adapter cleanup")
    git("fetch", "origin", "hotfix/prod-snap-meta-final")
    require(git("rev-parse", "origin/hotfix/prod-snap-meta-final") == MERGE, "Production branch moved")
    require(git("rev-parse", MERGE + "^{tree}") == TREE, "Deployment tree mismatch")
    require(git("show", "-s", "--format=%P", MERGE).split() == [BASE, INTENT_COMMIT], "Merge parent mismatch")
    git("merge-base", "--is-ancestor", SOURCE, MERGE)
    require(git("diff", "--name-only", SOURCE, MERGE).splitlines() == ["release/release-intent-v5.json"],
            "Source-to-deployment changes more than intent")
    incoming = git("ls-tree", "-r", "--name-only", MERGE).splitlines()
    require(not any(r["path"] == p or r["path"].startswith(p + "/") or p.startswith(r["path"] + "/")
                    for r in original for p in incoming), "Preserved entry collides with tracked destination")
    inactive()
    clean_tracked()
    require(git("rev-parse", "HEAD") == BASE, "Workspace head changed before fast-forward")
    git("merge", "--ff-only", MERGE)
    require(inventory() == original, "Preservation changed during fast-forward")
    require(sha((ROOT / "release/release-intent-v5.json").read_bytes()) == INTENT_SHA256, "Intent bytes mismatch")
    validation = "from scripts.emergent_deployment_adapter import load_release_intent,verify_release_intent_git; i=load_release_intent(); verify_release_intent_git(i); print(i['source_git_sha'])"
    run([sys.executable, "-B", "-c", validation], "python-source-preflight")
    js_validation = "const x=require('./frontend/scripts/release-intent.cjs').loadReviewedReleaseIntent({intentPath:'/app/release/release-intent-v5.json',frontendRoot:'/app/frontend'}); console.log(x.source_git_sha)"
    run(["node", "-e", js_validation], "frontend-source-preflight")
    host_node = run(["node", "--version"], "host-node").strip()
    host_yarn = run(["yarn", "--version"], "host-yarn").strip()
    require(host_yarn == "1.22.22", "Unexpected host Yarn")
    build_log = run(["yarn", "build"], "actual-host-yarn-build", cwd=ROOT / "frontend")
    start = build_log.rfind('{\n  "built": true,')
    require(start >= 0, "Adapter completion JSON absent")
    built, _ = json.JSONDecoder().raw_decode(build_log[start:])
    require(built.get("built") is True and built.get("source_git_sha") == SOURCE
            and built.get("release_id") == RELEASE_ID, "Adapter identity mismatch")
    intent = json.loads((ROOT / "release/release-intent-v5.json").read_text())
    identity = json.loads((ROOT / "backend/release_identity.json").read_text())
    require(identity == intent["runtime_identity"], "Generated runtime identity mismatch")
    require(intent["source_git_sha"] == SOURCE and intent["source_base_git_sha"] == BASE, "Intent lineage mismatch")
    require(git("rev-parse", "HEAD") == MERGE and git("rev-parse", "HEAD^{tree}") == TREE, "Build changed head/tree")
    clean_tracked()
    after = inventory()
    require(after == original, "Preserved entries changed or unexpected entries appeared")
    require(git("ls-files", "--", "backend/release_identity.json", "frontend/build", "frontend/.release") == "",
            "Generated output became tracked")
    run(["git", "check-ignore", "backend/release_identity.json", "frontend/build/build-meta.json",
         "frontend/.release/reproducible-build.json"], "generated-output-ignored")
    require(sha((ROOT / "release/release-intent-v5.json").read_bytes()) == INTENT_SHA256, "Intent changed during build")
    git("fetch", "origin", "hotfix/prod-snap-meta-final")
    require(git("rev-parse", "origin/hotfix/prod-snap-meta-final") == MERGE, "Production advanced during build")
    inactive()
    summary = {"rehearsal_passed": True, "source_git_sha": SOURCE, "source_base_git_sha": BASE,
               "deployment_git_sha": MERGE, "tree": TREE, "intent_sha256": INTENT_SHA256,
               "release_id": RELEASE_ID, "host_node": host_node, "host_yarn": host_yarn,
               "preserved_count": len(original), "preserved_manifest_sha256": sha(json.dumps(original, sort_keys=True).encode()),
               "preserved_unchanged": True, "build_result": built, "lease_created": False, "published": False}
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "build_result"}, indent=2), flush=True)


if __name__ == "__main__":
    main()
