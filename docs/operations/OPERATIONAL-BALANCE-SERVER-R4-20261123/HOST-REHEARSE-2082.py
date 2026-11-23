#!/usr/bin/env python3
"""Owner-run isolated PR2082 host rehearsal; disabled until exact-B acceptance.

Preserves current Mezan86 source and adds the reviewed operational reversal and
account-history source. It never creates/closes a lease, stages /app, merges or
publishes. Every owned temporary result is retained on success and failure.
Fresh original inactive status and exact current Production/candidate refs are
required before and after the original adapter. A prior release's publication
or verification is neither asserted nor replayed by this helper.

Derived from the reviewed PR2080 draft at evidence34ab7a708b8da7a92160ef93850acee4a1425db8;
the obsolete PR2076 completion-receipt gate does not apply to the current-base
isolated rehearsal. AGENTS requires the original fresh inactive guard. No lease
or final publication authority is inferred from this non-publishing rehearsal.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

ENABLED = False
N = "44122bcc986bd1dff1532b9ad62c0268e899a179"
A = "aec203382b387c3812509084e77c2922aa860877"
A_TREE = "f133f04fd5759c57f5cce195e35ae03ba53ef6da"
B = "0bd6a3fadd33d6354ebdc8dec814bcb80b0d7218"
B_TREE = "4fa54935f420e2a546c2237c0fb7b0ef9ef8367d"
RELEASE_ID = "rg5-12e0f132b3ee688815cd2ec401bd34c43706d193492edacc7be6c5ca6e6ade8a"
INTENT_SHA256 = "59da5ee6a86f3bfa5a1b8446c6aacb8e180144bc15ccaca3e414e1ec23b151ab"
RELEASE_REF = "refs/heads/codex/operational-balance-server-r4-20261123"

APP = Path("/app")
PRODUCTION_BRANCH = "hotfix/prod-snap-meta-final"
INTENT_PATH = "release/release-intent-v5.json"
GUARD_FILES = {
    "scripts/production_release_guard.py": "4e56f1f60f5f79931658b8f95a0aeb2269471025ca84f7e5b651cd284806349a",
    "backend/frontend_build_identity.py": "7896dfa2c9b4fea8c426b47c5b75e331380c0a4a3f947028aa4f5afa101f9d87",
    "backend/release_protocol_v5.py": "1961ac18a5363ec425ceec4f98f96f611d378b09e19380d4756bd6776987d8ef",
}
SHA = re.compile(r"[0-9a-f]{40}")
SHA256 = re.compile(r"[0-9a-f]{64}")


class Stop(RuntimeError):
    pass


def interrupted(signum, _frame):
    raise Stop("isolated rehearsal interrupted by " + signal.Signals(signum).name)


def stop_owned_build(process):
    # Repeated terminal termination signals must not interrupt bounded cleanup.
    for signum in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(signum, signal.SIG_IGN)
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    # A leader can exit before a descendant that ignored SIGTERM. Finish the
    # entire still-owned group, not only the Popen leader, before leaving.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=10)


def require(condition, message):
    if not condition:
        raise Stop(message)


def validate_pins():
    missing = [name for name, value in {
        "B": B, "B_TREE": B_TREE, "RELEASE_ID": RELEASE_ID,
        "INTENT_SHA256": INTENT_SHA256, "RELEASE_REF": RELEASE_REF,
    }.items() if value is None]
    require(ENABLED is True and not missing,
            "DISABLED_DRAFT: required pins or enabled state are not ready; no host action attempted")
    require(not sys.argv[1:], "arguments and runtime pin overrides are not supported")
    require(all(isinstance(value, str) and SHA.fullmatch(value)
                for value in (N, A, A_TREE, B, B_TREE)), "invalid immutable Git pins")
    require(isinstance(RELEASE_ID, str) and re.fullmatch(r"rg5-[0-9a-f]{64}", RELEASE_ID), "invalid runtime release pin")
    require(isinstance(INTENT_SHA256, str) and SHA256.fullmatch(INTENT_SHA256), "invalid intent bytes pin")
    require(isinstance(RELEASE_REF, str) and re.fullmatch(r"refs/heads/[A-Za-z0-9._/-]+", RELEASE_REF)
            and ".." not in RELEASE_REF, "invalid candidate ref pin")


def main():
    # This is deliberately the first operation. In the delivered draft it stops
    # before reading /app, reading evidence, status, networking, or /tmp creation.
    validate_pins()
    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    require(not any(name.startswith("GIT_") and name not in {
        "GIT_TERMINAL_PROMPT", "GIT_OPTIONAL_LOCKS"
    } for name in os.environ), "unexpected Git routing or override environment")
    require(not any(name in os.environ for name in (
        "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "MEZAN_RELEASE_BOOTSTRAP_INTENT",
        "NODE_OPTIONS", "NODE_PATH",
    )), "unexpected CI, bootstrap, or Node override environment")
    require(APP.resolve(strict=True) == APP and (APP / ".git").is_dir()
            and not (APP / ".git").is_symlink(), "original /app checkout required")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
               GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")

    def run(label, argv, cwd=APP, timeout=120):
        try:
            result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                                    stdin=subprocess.DEVNULL, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            raise Stop(label + " did not complete; no shared state was repaired") from None
        require(result.returncode == 0, label + " exit=" + str(result.returncode))
        # No raw argv, stderr, remote URL, environment, or lease is printed.
        return result.stdout

    def git(*args, cwd=APP):
        return run("git " + args[0], ["git", "--no-optional-locks", *args], cwd).decode("utf-8").strip()

    def shared_check():
        require(git("branch", "--show-current") == PRODUCTION_BRANCH, "shared branch changed")
        require(git("rev-parse", "HEAD") == N, "shared HEAD changed from N")
        require(not git("status", "--porcelain=v1", "--untracked-files=no"), "shared tracked source is dirty")
        for relative, expected in GUARD_FILES.items():
            path = APP / relative
            require(path.resolve(strict=True) == path and path.is_file()
                    and hashlib.sha256(path.read_bytes()).hexdigest() == expected,
                    "original shared guard or dependencies changed")
        state = json.loads(run("original shared guard status", [sys.executable, "-I", "-B",
            str(APP / "scripts/production_release_guard.py"), "status"], timeout=30))
        require(isinstance(state, dict) and set(state) == {"active"} and state["active"] is False,
                "original shared guard is not unambiguously inactive")
        print("SHARED_GUARD", json.dumps({"active": False}), flush=True)

    def remote_check():
        rows = git("ls-remote", "--exit-code", "origin",
                   "refs/heads/" + PRODUCTION_BRANCH, RELEASE_REF).splitlines()
        refs = {}
        for row in rows:
            parts = row.split()
            require(len(parts) == 2 and SHA.fullmatch(parts[0]) and parts[1] not in refs,
                    "remote reference response is ambiguous")
            refs[parts[1]] = parts[0]
        require(refs == {"refs/heads/" + PRODUCTION_BRANCH: N, RELEASE_REF: B},
                "remote production or candidate reference changed")

    shared_check()
    remote_check()
    free = shutil.disk_usage("/tmp").free
    print("REHEARSAL_DISK", json.dumps({"tmp_free_bytes": free,
          "minimum_free_bytes": 10 * 1024**3, "app_free_bytes": shutil.disk_usage(APP).free}), flush=True)
    require(free >= 10 * 1024**3, "less than 10 GiB free in /tmp; no files were removed")
    work = Path(tempfile.mkdtemp(prefix="mezan-pr2082-rehearsal-", dir="/tmp"))
    repo, log = work / "repo", work / "adapter.log"
    report_path = work / "result.json"
    report = {"kind": "pr2082_owner_isolated_rehearsal_v1", "status": "started",
              "N": N, "A": A, "B": B, "source_tree": A_TREE, "candidate_tree": B_TREE,
              "release_id": RELEASE_ID, "intent_sha256": INTENT_SHA256,
              "started_at": datetime.now(timezone.utc).isoformat(),
              "lease_created": False, "shared_source_or_outputs_changed_by_helper": False,
              "merge_or_publish_performed": False}
    print("REHEARSAL_DIRECTORY", str(work), flush=True)
    print("BUILD_LOG", str(log), flush=True)

    def save_report():
        report_path.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    save_report()
    try:
        run("isolated clone", ["git", "clone", "--local", "--no-hardlinks", "--no-checkout",
                               str(APP), str(repo)], cwd=work, timeout=300)
        remote = git("remote", "get-url", "origin")
        run("fetch exact B in isolated clone", ["git", "--no-optional-locks", "fetch", "--no-tags",
            "--no-write-fetch-head", "--no-auto-maintenance", remote, B], cwd=repo, timeout=300)
        del remote
        for commit in (N, A, B):
            git("cat-file", "-e", commit + "^{commit}", cwd=repo)
        require(git("rev-list", "--parents", "-n", "1", A, cwd=repo).split() == [A, N],
                "source A is not the reviewed direct child of N")
        require(git("rev-list", "--parents", "-n", "1", B, cwd=repo).split() == [B, A],
                "intent B is not the reviewed direct child of A")
        require(git("rev-parse", A + "^{tree}", cwd=repo) == A_TREE, "source A tree differs")
        require(git("diff", "--name-only", A, B, cwd=repo).splitlines() == [INTENT_PATH],
                "B changes more than the intent")
        git("checkout", "--detach", B, cwd=repo)
        require(git("rev-parse", "HEAD^{tree}", cwd=repo) == B_TREE, "candidate B tree differs")
        require(not git("status", "--porcelain=v1", "--untracked-files=all", cwd=repo),
                "isolated checkout is not clean")
        require(hashlib.sha256((repo / INTENT_PATH).read_bytes()).hexdigest() == INTENT_SHA256,
                "tracked intent bytes differ")
        check = "\n".join([
            "import json, runpy",
            "adapter = runpy.run_path('scripts/emergent_deployment_adapter.py')",
            "intent = adapter['load_release_intent']()",
            "adapter['verify_release_intent_git'](intent)",
            f"expected = ({A!r}, {N!r}, {RELEASE_ID!r}, {PRODUCTION_BRANCH!r})",
            "actual = (intent['source_git_sha'], intent['source_base_git_sha'], intent['runtime_identity']['release_id'], intent['branch'])",
            "if actual != expected:",
            "    raise SystemExit('candidate identity differs from the reviewed pins')",
            "print(json.dumps({'validated': True}))",
        ])
        run("original candidate intent validation", [sys.executable, "-I", "-B", "-c", check], cwd=repo)
        shared_check()
        remote_check()
        build_env = dict(env, REACT_APP_BACKEND_URL="https://mezansalla.com",
                         TMPDIR=str(work), XDG_CACHE_HOME=str(work / "cache"))
        print("BUILD_RUNNING: original configured package adapter in isolated /tmp clone", flush=True)
        with log.open("wb") as output:
            process = subprocess.Popen(["yarn", "build"], cwd=repo / "frontend", env=build_env,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                adapter_exit = process.wait(timeout=3600)
            except BaseException:
                # Only this helper's isolated process group, never a host service
                # or another task's guard/build process, is stopped on interruption.
                stop_owned_build(process)
                raise
        report["adapter_exit"] = adapter_exit
        require(adapter_exit == 0, "original adapter failed; complete log and candidate retained")
        intent = json.loads((repo / INTENT_PATH).read_bytes())
        identity = json.loads((repo / "backend/release_identity.json").read_bytes())
        require(identity == intent["runtime_identity"] and identity.get("release_id") == RELEASE_ID,
                "materialized identity differs from reviewed intent")
        require(not git("status", "--porcelain=v1", "--untracked-files=all", cwd=repo),
                "isolated tracked or unexpected untracked source changed")
        text = log.read_text(encoding="utf-8", errors="replace")
        proofs = []
        for match in re.finditer(r"(?m)^\{", text):
            try:
                value, _ = json.JSONDecoder().raw_decode(text[match.start():])
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("built") is True:
                proofs.append(value)
        require(len(proofs) == 1, "original adapter success proof missing or ambiguous")
        proof = proofs[0]
        boundary = proof.get("package_boundary", {})
        require(
            proof.get("protocol_version") == 5 and proof.get("source_git_sha") == A
            and proof.get("release_id") == RELEASE_ID
            and proof.get("frontend_artifact_tree_sha256") == intent["frontend_build"]["artifact_tree_sha256"]
            and boundary.get("kind") == "mezan_release_package_boundary_proof_v1"
            and boundary.get("source_git_sha") == A and boundary.get("release_id") == RELEASE_ID
            and boundary.get("frontend", {}).get("isolated_runtime_artifact_verified") is True
            and boundary.get("frontend", {}).get("content_type_contract") == "application/json"
            and boundary.get("frontend", {}).get("artifact_tree_sha256") == intent["frontend_build"]["artifact_tree_sha256"]
            and boundary.get("backend", {}).get("isolated_verified") is True
            and boundary.get("backend", {}).get("health_verified") is True
            and boundary.get("backend", {}).get("sibling_frontend_present") is False
            and boundary.get("backend", {}).get("git_directory_present") is False,
            "original adapter proof does not match the reviewed release")
        shared_check()
        remote_check()
        report.update(status="isolated_rehearsal_passed", built=True,
                      frontend_artifact_tree_sha256=proof["frontend_artifact_tree_sha256"],
                      package_boundaries_verified=True)
        print("REHEARSAL_RESULT", json.dumps(report, sort_keys=True), flush=True)
    except BaseException as exc:
        report.update(status="failed_or_interrupted", built=False,
                      failure_type=type(exc).__name__)
        if isinstance(exc, Stop):
            report["failure"] = str(exc)
        raise
    finally:
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save_report()
        print("RETAINED_RESULT", str(report_path), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Stop as exc:
        print("STOP:", str(exc), flush=True)
        raise SystemExit(2) from None
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Exception strings may contain sensitive paths or a private remote URL.
        print("STOP: operation could not be verified; failure_type=" + type(exc).__name__, flush=True)
        raise SystemExit(2) from None
