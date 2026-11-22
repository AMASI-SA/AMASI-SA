#!/usr/bin/env python3
"""DISABLED draft for an owner-run isolated PR2080 host rehearsal.

Do not publish this as an executable release instruction. Identity pins are
frozen, but exact-B acceptance and the actual completed predecessor release and
released-handoff receipt are still required before enabling it. No argument or
environment variable can enable it.
It never prepares a lease, merges, stages /app, or publishes. It retains every
temporary result on success and failure; it never cleans another task's files.

Derived from reviewed PR2074 isolated-rehearsal pattern at evidence commit
3be5f8df326ed27bec6976a344283e26a90510fd. This draft itself has not run on a host.
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
N = "02cd99e5b2b68c95341c87590abcf572c885e697"
A = "dd3a9ea29fce28e2fa87332cff4a6d46f776e0d3"
A_TREE = "ee385c9c71a19833e08b5e5d7470d94fe4b646f4"
B = "9a500a21013a08f750009be656362481986eb208"
B_TREE = "2cbd2842ed2506642a1321a47e35f69a766fcf89"
RELEASE_ID = "rg5-b2aa207d2f63107c64d9c0b66ce5cc567aa5ebb074753cf889227de4c448e008"
INTENT_SHA256 = "0e0c7a6167dae598450358db6a1399b080cb7c69e176d28c5c31a0585ed287dc"
RELEASE_REF = "refs/heads/codex/operational-balance-server-r3-20261122"

# Remains None until the actual PR2076 completed-release and released-handoff
# record for N is available and reviewed. No separate candidate approval or
# next-PR/source-specific authorization record is required or fabricated.
# A future pin binds the existing JSON receipt by local_filename (beside this
# helper, outside /app), evidence_git_sha, repository_path, sha256, and
# claim_paths. The final mapping must follow that receipt's actual schema.
# The pinned launcher retrieves those exact bytes before starting this file.
# Completion is never inferred from CI, an inactive guard, or PR merge alone.
PREVIOUS_RELEASE_COMPLETION_PIN = None

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


def require(condition, message):
    if not condition:
        raise Stop(message)


def validate_pins():
    missing = [name for name, value in {
        "B": B, "B_TREE": B_TREE, "RELEASE_ID": RELEASE_ID,
        "INTENT_SHA256": INTENT_SHA256, "RELEASE_REF": RELEASE_REF,
        "PREVIOUS_RELEASE_COMPLETION_PIN": PREVIOUS_RELEASE_COMPLETION_PIN,
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


def pinned_record(pin, label, claims):
    require(isinstance(pin, dict) and set(pin) == {
        "local_filename", "evidence_git_sha", "repository_path", "sha256", "claim_paths"
    }, label + " pin is not fully reviewed")
    require(isinstance(pin["evidence_git_sha"], str) and SHA.fullmatch(pin["evidence_git_sha"])
            and isinstance(pin["sha256"], str) and SHA256.fullmatch(pin["sha256"]), label + " identity is invalid")
    require(isinstance(pin["repository_path"], str) and pin["repository_path"].startswith("docs/operations/")
            and ".." not in Path(pin["repository_path"]).parts, label + " repository path is invalid")
    require(isinstance(pin["local_filename"], str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.json", pin["local_filename"]),
            label + " local evidence filename is invalid")
    path = Path(__file__).resolve(strict=True).parent / pin["local_filename"]
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_file()
            and APP not in path.parents, label + " evidence must be a real file outside /app")
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == pin["sha256"], label + " evidence bytes differ")
    record = json.loads(raw)
    paths = pin["claim_paths"]
    require(isinstance(record, dict) and isinstance(paths, dict) and set(paths) == set(claims),
            label + " claim mapping is incomplete")
    for claim, expected in claims.items():
        keys = paths[claim]
        require(isinstance(keys, list) and keys and all(isinstance(key, str) and key for key in keys),
                label + " claim path is invalid")
        value = record
        for key in keys:
            require(isinstance(value, dict) and key in value, label + " required claim is missing: " + claim)
            value = value[key]
        require(type(value) is type(expected) and value == expected,
                label + " required claim is not satisfied: " + claim)
    return {"evidence_git_sha": pin["evidence_git_sha"], "sha256": pin["sha256"]}


def main():
    # This is deliberately the first operation. In the delivered draft it stops
    # before reading /app, reading evidence, status, networking, or /tmp creation.
    validate_pins()
    completion = pinned_record(PREVIOUS_RELEASE_COMPLETION_PIN, "previous completion", {
        "eligible_for_next_publication": True,
        "deployment_git_sha": N,
        "explicit_new_deployment_succeeded": True,
        "original_live_verifier_passed": True,
        "original_live_verifier_checks": 3,
        "final_guard_active": False,
    })
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
            str(APP / "scripts/production_release_guard.py"), "status"], timeout=10))
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
    require(shutil.disk_usage("/tmp").free >= 10 * 1024**3, "less than 10 GiB free in /tmp")
    work = Path(tempfile.mkdtemp(prefix="mezan-pr2080-rehearsal-", dir="/tmp"))
    repo, log = work / "repo", work / "adapter.log"
    report_path = work / "result.json"
    report = {"kind": "pr2080_owner_isolated_rehearsal_v1", "status": "started",
              "N": N, "A": A, "B": B, "source_tree": A_TREE, "candidate_tree": B_TREE,
              "release_id": RELEASE_ID, "intent_sha256": INTENT_SHA256,
              "predecessor_completion_evidence": completion,
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
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
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
