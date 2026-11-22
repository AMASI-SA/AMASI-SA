"""Pinned R3 isolated rehearsal, conditional on the operator's prior UI fact.

The pinned predecessor receipt proves original verified=true/checks=3 for
exact N/source/release identity and final inactive guard. It does not include
an explicit Deployment Succeeded UI observation. The operator must confirm
that the new UI success appeared before its successful verification, in the
reviewed launcher before --base-ui-success-confirmed is set.
This program never manufactures that fact or repeats the preceding release.

Run with Python -B. Optional --diagnostic reads bounded host metadata before
the build. No shared checkout, dependency, output, backup or lease is changed.
The original guard may use its normal operation-lock file while reading status.
No public identity probe or production verify command is invoked here.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile


N = "24270ebdfdebb83282389308a2349cdb27e2b8e4"
A = "108024c00b7f367fd1d6b15874d7a73d55de2620"
A_TREE = "1aa73c0a2024533f7a67ad32d64c540521b70cd8"
# Assign only after fresh CI and the reviewed intent-only B exist.
B = "5bb05036d8095a87c6635c0a431c2c19d24d8f78"
B_TREE = "8f915a681de43fed9da1ec98685a56f87a4c8bcb"
RELEASE_ID = "rg5-e4e41b98edc9f49b8e74adad2a905d362b7113b5b1a1fa83436a4c1ba8c4b918"
ARTIFACT = "dcaa1f2b805c44427be8df2f5b466b85d7860bade43a3799009156300397300b"
PROOF = "30bcd91b489c6cb585c4b560937b4cd22f21b02f8ce78bab6e417b17b224eac3"
INTENT_SHA256 = "3a56ff073fcb3b7e362500a3be1966e2c513194f4679d5bb81a139a338c866c6"
# Original live verification/closure proof; prior UI success is operator input.
BASE_RELEASE_VERIFICATION_EVIDENCE_COMMIT = "3be5f8df326ed27bec6976a344283e26a90510fd"
BASE_RELEASE_SOURCE = "4e6c8a61bd1566e061a4f312e797ce239b582475"
BASE_RELEASE_ID = "rg5-2e0e3797d6ff3b955a3a23fd31487bd92721e70f316f3bdf34cab0d94c5d630f"
PRODUCTION_REF = "refs/heads/hotfix/prod-snap-meta-final"
CANDIDATE_REF = "refs/heads/codex/operational-demand-latency-r3-20261122"
DIAGNOSTIC_PATH = "docs/operations/OPERATIONAL-DEMAND-LATENCY-20261122/MANUAL-HOST-LATENCY-DIAGNOSTIC.py"
DIAGNOSTIC_SHA256 = "0b608b157d3092a04f893637963ea864a8d9bfe98592fedb470488e4d7bcfc8a"
APP = Path("/app")
MINIMUM_FREE = 10 * 1024**3


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def show(label, value):
    print(label, json.dumps(value, sort_keys=True, allow_nan=False), flush=True)


def pinned():
    # First call in main: no /app, Git, guard, diagnostic, temporary directory
    # or network access can precede assignment of every reviewed pin.
    pending = [name for name, value in {
        "B": B, "B_TREE": B_TREE, "RELEASE_ID": RELEASE_ID,
        "ARTIFACT": ARTIFACT, "PROOF": PROOF, "INTENT_SHA256": INTENT_SHA256,
        "BASE_RELEASE_VERIFICATION_EVIDENCE_COMMIT": BASE_RELEASE_VERIFICATION_EVIDENCE_COMMIT,
    }.items() if value is None]
    require(not pending, "draft disabled: missing reviewed pins: " + ", ".join(pending))
    require(all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
                for value in (N, A, A_TREE, B, B_TREE, BASE_RELEASE_VERIFICATION_EVIDENCE_COMMIT)),
            "invalid commit/tree/evidence pins")
    require(isinstance(RELEASE_ID, str) and re.fullmatch(r"rg5-[0-9a-f]{64}", RELEASE_ID)
        and all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
                for value in (ARTIFACT, PROOF, INTENT_SHA256)),
        "invalid reviewed identity/artifact/proof/intent pins")
    require(B not in (N, A) and B_TREE != A_TREE,
            "B must be the separate reviewed intent-only commit")
    require("--base-ui-success-confirmed" in sys.argv[1:],
            "operator must first confirm Deployment Succeeded for previous PR2074")


def environment():
    rejected = (
        "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "GIT_DIR",
        "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_SHALLOW_FILE",
        "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
        "GIT_NAMESPACE", "GIT_CONFIG", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
        "GIT_CURL_VERBOSE",
    )
    # Git tracing can bypass captured stderr and write fetch URLs to a file.
    require(not any(name in os.environ for name in rejected)
        and not any(name.startswith("GIT_TRACE") for name in os.environ),
        "unexpected CI, Git routing or tracing environment")
    return dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0")


def run(label, argv, *, env, cwd=APP, timeout=120):
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, text=True,
            capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit("STOP: " + label + " could not complete") from None
    # Git errors may contain private remote URLs; never print argv or stderr.
    require(result.returncode == 0, label + " exit=" + str(result.returncode))
    return result.stdout.strip()


def git(*args, env, cwd=APP, timeout=120):
    return run("git " + args[0], ["git", "--no-optional-locks",
        "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
        env=env, cwd=cwd, timeout=timeout)


def real_directory(path):
    require(stat.S_ISDIR(path.lstat().st_mode) and path.resolve(strict=True) == path,
            "required directory is not a real canonical directory")


def shared_check(phase, env):
    require(git("rev-parse", "HEAD", env=env) == N,
            "shared checkout is no longer the pinned deployed N")
    require(not git("status", "--porcelain", "--untracked-files=no", env=env),
            "shared tracked checkout changed; isolated candidate retained")
    code = """
import json
from pathlib import Path
from scripts import production_release_guard as guard
if guard.REPO_ROOT.resolve() != Path('/app'):
    raise SystemExit(2)
state = guard.status()
value = {'active': state.get('active'),
         'incomplete': state.get('incomplete_lease_directory', False),
         'exactly_inactive': state == {'active': False}}
if not all(type(item) is bool for item in value.values()):
    raise SystemExit(3)
print(json.dumps(value))
"""
    state = json.loads(run("original guard status within five seconds",
        [sys.executable, "-B", "-c", code], env=env, timeout=5))
    require(isinstance(state, dict) and set(state) == {
        "active", "incomplete", "exactly_inactive"}
        and all(type(value) is bool for value in state.values()),
        "original guard returned an invalid status")
    show("SHARED_CHECK", {"phase": phase, "deployment_git_sha": N,
         "tracked_clean": True, **state})
    require(state == {"active": False, "incomplete": False, "exactly_inactive": True},
            "original shared guard is not fully inactive")


def remote_check(phase, env):
    lines = git("ls-remote", "--exit-code", "origin", PRODUCTION_REF,
                CANDIDATE_REF, env=env).splitlines()
    require(len(lines) == 2, "remote release references are incomplete")
    refs = {}
    for line in lines:
        parts = line.split()
        require(len(parts) == 2 and re.fullmatch(r"[0-9a-f]{40}", parts[0]),
                "remote release reference is invalid")
        require(parts[1] not in refs, "remote release reference is duplicated")
        refs[parts[1]] = parts[0]
    require(refs == {PRODUCTION_REF: N, CANDIDATE_REF: B},
            "remote production or candidate reference changed")
    show("REMOTE_CHECK", {"phase": phase, "production": N, "candidate": B})


def digest(path):
    require(stat.S_ISREG(path.lstat().st_mode), "pinned input is not a regular file")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_intent(repo):
    path = repo / "release/release-intent-v5.json"
    require(digest(path) == INTENT_SHA256, "tracked intent bytes differ from reviewed intent")
    intent = json.loads(path.read_text())
    require(intent["protocol_version"] == 5 and intent["source_base_git_sha"] == N
        and intent["source_git_sha"] == A
        and intent["runtime_identity"]["release_id"] == RELEASE_ID
        and intent["frontend_build"]["artifact_tree_sha256"] == ARTIFACT
        and intent["frontend_reproducibility"]["proof_file"]["sha256"] == PROOF,
        "tracked intent differs from reviewed source, identity, artifact or proof")
    return intent


def original_candidate_check(repo, env):
    code = """
import json, os
from scripts import emergent_deployment_adapter as adapter
intent = adapter.load_release_intent()
git = adapter.verify_release_intent_git(intent)
print(json.dumps({'source_base': intent['source_base_git_sha'],
    'source': intent['source_git_sha'], 'deployment': git['deployment_git_sha'],
    'release_id': intent['runtime_identity']['release_id'],
    'artifact_tree': intent['frontend_build']['artifact_tree_sha256'],
    'proof_sha256': intent['frontend_reproducibility']['proof_file']['sha256'],
    'cache_root': adapter._toolchain_cache_home(dict(os.environ))}))
"""
    value = json.loads(run("original candidate validation",
        [sys.executable, "-B", "-c", code], cwd=repo, env=env))
    expected = {"source_base": N, "source": A, "deployment": B,
        "release_id": RELEASE_ID, "artifact_tree": ARTIFACT, "proof_sha256": PROOF}
    require(isinstance(value, dict) and set(value) == set(expected) | {"cache_root"}
        and all(value[key] == item for key, item in expected.items()),
        "original candidate proof differs from reviewed pins")
    show("ORIGINAL_INTENT_CHECK", expected)
    cache = Path(value["cache_root"])
    require(cache.is_absolute(), "toolchain cache path is not absolute")
    cache = cache.resolve(strict=False)
    require(not any(cache == root or root in cache.parents for root in (APP, repo)),
            "toolchain cache must be outside /app and the isolated candidate")
    existing = cache
    while not existing.exists():
        existing = existing.parent
    real_directory(existing)
    tmp_free, cache_free = shutil.disk_usage("/tmp").free, shutil.disk_usage(existing).free
    require(min(tmp_free, cache_free) >= MINIMUM_FREE,
            "less than 10 GiB free on /tmp or the actual toolchain-cache filesystem")
    show("BUILD_SPACE", {"tmp_free_bytes": tmp_free, "cache_free_bytes": cache_free,
         "cache_root": str(cache)})
    return cache


def optional_diagnostic(repo, env, requested):
    if not requested:
        show("OPTIONAL_DIAGNOSTIC", {"status": "not_requested"})
        return
    script = repo / DIAGNOSTIC_PATH
    try:
        if (not stat.S_ISREG(script.lstat().st_mode)
                or hashlib.sha256(script.read_bytes()).hexdigest() != DIAGNOSTIC_SHA256):
            show("OPTIONAL_DIAGNOSTIC", {"status": "unavailable", "reason": "sha256_mismatch"})
            return
        result = subprocess.run([sys.executable, "-B", str(script)], cwd=APP,
            env=env, text=True, capture_output=True, stdin=subprocess.DEVNULL, timeout=45)
        if result.returncode:
            show("OPTIONAL_DIAGNOSTIC", {"status": "unavailable", "exit": result.returncode})
            return
        # The pinned diagnostic owns detailed metric allowlisting. Accept only
        # its three JSON records; never forward arbitrary stdout or stderr.
        labels = {"READ_ONLY_DIAGNOSTIC", "HOST_LOG_SAMPLES", "HOST_JOB_METADATA"}
        records, complete = {}, False
        if len(result.stdout) > 131072:
            raise ValueError()
        for line in result.stdout.splitlines():
            if line == "DONE: read-only metadata; no application request, job, source, index, lease or financial write":
                complete = True
                continue
            label, raw = line.split(" ", 1)
            if label not in labels or label in records:
                raise ValueError()
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("production_scope_verified") is True:
                raise ValueError()
            json.dumps(value, allow_nan=False)
            records[label] = value
        if set(records) != labels or not complete:
            raise ValueError()
        if records["READ_ONLY_DIAGNOSTIC"].get("deployment_git_sha") != N:
            raise ValueError()
        for label in ("READ_ONLY_DIAGNOSTIC", "HOST_LOG_SAMPLES", "HOST_JOB_METADATA"):
            show(label, records[label])
        show("OPTIONAL_DIAGNOSTIC", {"status": "completed", "production_scope_verified": False})
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError):
        show("OPTIONAL_DIAGNOSTIC", {"status": "unavailable", "reason": "bounded_read_failed"})


def adapter_proof(log):
    proofs = []
    text = log.read_text(errors="replace")
    for match in re.finditer(r"(?m)^\{", text):
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("built") is True:
            proofs.append(payload)
    require(len(proofs) == 1, "original adapter success proof is missing or ambiguous")
    proof = proofs[0]
    boundary = proof.get("package_boundary", {})
    require(proof.get("protocol_version") == 5 and proof.get("source_git_sha") == A
        and proof.get("release_id") == RELEASE_ID
        and proof.get("frontend_artifact_tree_sha256") == ARTIFACT
        and boundary.get("kind") == "mezan_release_package_boundary_proof_v1"
        and boundary.get("source_git_sha") == A and boundary.get("release_id") == RELEASE_ID
        and boundary.get("frontend", {}).get("isolated_runtime_artifact_verified") is True
        and boundary.get("frontend", {}).get("content_type_contract") == "application/json"
        and boundary.get("frontend", {}).get("artifact_tree_sha256") == ARTIFACT
        and boundary.get("backend", {}).get("isolated_verified") is True
        and boundary.get("backend", {}).get("health_verified") is True
        and boundary.get("backend", {}).get("sibling_frontend_present") is False
        and boundary.get("backend", {}).get("git_directory_present") is False,
        "original adapter proof does not match the reviewed release")


def main():
    pinned()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--base-ui-success-confirmed", action="store_true")
    args = parser.parse_args()
    show("BASE_VERIFICATION_EVIDENCE", {
        "deployment_git_sha": N, "source_git_sha": BASE_RELEASE_SOURCE,
        "release_id": BASE_RELEASE_ID,
        "evidence_commit": BASE_RELEASE_VERIFICATION_EVIDENCE_COMMIT,
        "ui_success_confirmed_by_operator": args.base_ui_success_confirmed,
        "ui_success_observed_by_assistant": False,
        "current_production_probe_performed": False,
    })
    env = environment()
    for path in (APP, APP / ".git", Path("/tmp")):
        real_directory(path)
    require(git("rev-parse", "--show-toplevel", env=env) == str(APP)
        and git("rev-parse", "--absolute-git-dir", env=env) == str(APP / ".git"),
        "shared Git directories differ from /app")
    shared_check("before", env)
    remote_check("before", env)
    require(shutil.disk_usage("/tmp").free >= MINIMUM_FREE, "less than 10 GiB free in /tmp")
    work = Path(tempfile.mkdtemp(prefix="mezan-pr2076-", dir="/tmp"))
    repo, log = work / "repo", work / "adapter.log"
    show("REHEARSAL_DIRECTORY", {"directory": str(work), "build_log": str(log)})
    git("clone", "--local", "--no-hardlinks", "--no-checkout", str(APP), str(repo),
        cwd=work, env=env, timeout=300)
    # Original origin is used only in memory: no URL, header or credential is
    # copied into the candidate's config, FETCH_HEAD, console or build log.
    remote = git("remote", "get-url", "origin", env=env)
    git("fetch", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance",
        remote, B, cwd=repo, env=env, timeout=300)
    del remote
    for commit in (N, A, B):
        git("cat-file", "-e", commit + "^{commit}", cwd=repo, env=env)
    require(git("rev-parse", A + "^{tree}", cwd=repo, env=env) == A_TREE,
            "source A tree differs from the reviewed source")
    git("checkout", "--detach", B, cwd=repo, env=env)
    require(git("rev-parse", "HEAD", cwd=repo, env=env) == B
        and git("rev-parse", "HEAD^{tree}", cwd=repo, env=env) == B_TREE
        and not git("status", "--porcelain", "--untracked-files=no", cwd=repo, env=env),
        "isolated candidate does not match clean reviewed B")
    intent = check_intent(repo)
    cache = original_candidate_check(repo, env)
    optional_diagnostic(repo, env, args.diagnostic)
    shared_check("before_build", env)
    remote_check("before_build", env)
    build_env = dict(env, REACT_APP_BACKEND_URL="https://mezansalla.com",
                     TMPDIR=str(work), XDG_CACHE_HOME=str(cache))
    print("BUILD_RUNNING: original package adapter in isolated /tmp clone", flush=True)
    with log.open("x", encoding="utf-8") as output:
        result = subprocess.run(["yarn", "build"], cwd=repo / "frontend", env=build_env,
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT)
    show("ADAPTER_EXIT", {"code": result.returncode, "build_log": str(log)})
    require(result.returncode == 0, "original adapter failed; isolated log retained, no raw tail printed")
    require(check_intent(repo) == intent, "intent changed during the isolated build")
    identity = json.loads((repo / "backend/release_identity.json").read_text())
    require(identity == intent["runtime_identity"], "materialized identity differs from reviewed intent")
    require(digest(repo / "frontend/.release/reproducible-build.json") == PROOF,
            "materialized reproducibility proof differs from reviewed proof")
    require(git("rev-parse", "HEAD", cwd=repo, env=env) == B
        and not git("status", "--porcelain", "--untracked-files=no", cwd=repo, env=env),
        "isolated source changed during the build")
    adapter_proof(log)
    shared_check("final", env)
    remote_check("final", env)
    show("REHEARSAL_SUCCESS", {"built": True, "source_base": N, "source": A,
        "deployment": B, "release_id": RELEASE_ID, "artifact_tree": ARTIFACT,
        "proof_sha256": PROOF, "package_boundaries_verified": True,
        "base_verification_evidence_commit": BASE_RELEASE_VERIFICATION_EVIDENCE_COMMIT,
        "base_ui_success_confirmed_by_operator": args.base_ui_success_confirmed,
        "shared_tracked_clean": True, "guard_active": False, "directory": str(work),
        "build_log": str(log)})
    print("DONE: isolated rehearsal only; no shared source/output/dependency change, lease or publish", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Do not expose subprocess errors, environment values or connection data.
        raise SystemExit("STOP: rehearsal could not complete (" + type(error).__name__
                         + "); any created isolated directory and log are retained") from None
