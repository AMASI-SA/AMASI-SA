"""Rehearse pinned PR2079 after its ordinary merge; never modify /app."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

J = "02cd99e5b2b68c95341c87590abcf572c885e697"
A = "d6443504c7d16eba00142526478abf10de336f99"
B = 'd5036d9609b7155ee137d07ff802bbce5be265c5'
D = None
TREE = '83fc2223913c996c19f4fa676da9cfa8c54ebb49'
RELEASE_ID = 'rg5-b26fcb18b80fb02cb6b77c0781819c2467f4a6a20d7d2350516febf22957fa11'
ARTIFACT = 'd37d10537c6b55f4f0e4a4392ed40eca5bef962ea6cabaf822acc6a5e47a9d99'
ACTOR = "codex-tabby-weekly-fees-2079-20261122"
BRANCH = "hotfix/prod-snap-meta-final"
TASK_BRANCH = "codex/tabby-weekly-fee-estimates-r3-20261122"
APP = Path("/app")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0")
guard = None


def run(label, argv, cwd=APP, timeout=120, env=ENV):
    try:
        result = subprocess.run(
            argv, cwd=cwd, env=env, text=True, capture_output=True,
            stdin=subprocess.DEVNULL, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit("STOP: " + label + " could not complete") from None
    if result.returncode:
        # Git errors can contain private remote URLs. Do not print argv/stderr.
        raise SystemExit("STOP: " + label + " exit=" + str(result.returncode))
    return result.stdout.strip()


def git(*args, cwd=APP):
    return run("git " + args[0], ["git", "--no-optional-locks", *args], cwd)


def shared_check():
    if (guard.REPO_ROOT.resolve() != APP
            or Path(guard.__file__).resolve() != APP / "scripts/production_release_guard.py"):
        raise SystemExit("STOP: original /app guard required")
    state = guard.status()
    print("SHARED_GUARD", json.dumps({
        "active": state.get("active"),
        "incomplete": state.get("incomplete_lease_directory", False),
    }), flush=True)
    if state != {"active": False}:
        raise SystemExit("STOP: shared guard is no longer inactive")
    if git("branch", "--show-current") != BRANCH or git("rev-parse", "HEAD") != J or git(
        "status", "--porcelain", "--untracked-files=no"
    ):
        raise SystemExit("STOP: shared checkout changed; candidate retained")


def configured():
    if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
               for value in (J, A, B, D, TREE)):
        raise SystemExit("STOP: disabled release bundle; final commit/tree pins are not configured")
    if not isinstance(RELEASE_ID, str) or not re.fullmatch(r"rg5-[0-9a-f]{64}", RELEASE_ID):
        raise SystemExit("STOP: disabled release bundle; final release identity is not configured")
    if not isinstance(ARTIFACT, str) or not re.fullmatch(r"[0-9a-f]{64}", ARTIFACT):
        raise SystemExit("STOP: disabled release bundle; final artifact pin is not configured")


def owned_directory(path):
    if (not path.is_dir() or path.is_symlink() or path.absolute() != path.resolve()
            or path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077):
        raise SystemExit("STOP: receipt/rehearsal directory is not a real private owned directory")


def remote_check():
    refs = dict(line.split()[::-1] for line in git(
        "ls-remote", "--exit-code", "origin", "refs/heads/" + BRANCH,
        "refs/heads/" + TASK_BRANCH,
    ).splitlines())
    if refs.get("refs/heads/" + BRANCH) != D or refs.get("refs/heads/" + TASK_BRANCH) != B:
        raise SystemExit("STOP: remote release references changed")


def main():
    global guard
    configured()
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    receipt = parser.parse_args().receipt
    owned_directory(receipt.parent)
    if (receipt.parent.parent != Path("/tmp")
            or not receipt.parent.name.startswith("mezan-pr2079-deploy-")
            or receipt.name != "rehearsal-receipt.json" or os.path.lexists(receipt)):
        raise SystemExit("STOP: receipt must be a new path in this launcher's private directory")
    if Path.cwd() != APP or APP.is_symlink() or APP.resolve() != APP:
        raise SystemExit("STOP: original real /app working directory required")
    sys.path.insert(0, str(APP))
    from scripts import production_release_guard as original_guard
    guard = original_guard
    if any(os.environ.get(name) for name in (
        "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "GIT_DIR",
        "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT", "GIT_CONFIG", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
        "GIT_TRACE", "GIT_TRACE_PACKET", "GIT_TRACE_CURL", "GIT_CURL_VERBOSE",
        "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF",
    )):
        raise SystemExit("STOP: unexpected CI or Git routing environment")
    shared_check()
    remote_check()
    if shutil.disk_usage("/tmp").free < 10 * 1024**3:
        raise SystemExit("STOP: less than 10 GiB free in /tmp")

    work = Path(tempfile.mkdtemp(prefix="mezan-pr2079-rehearsal-", dir="/tmp"))
    repo = work / "repo"
    log = work / "adapter.log"
    print("REHEARSAL_DIRECTORY", str(work), flush=True)
    print("BUILD_LOG", str(log), flush=True)
    run("isolated clone", [
        "git", "clone", "--local", "--no-hardlinks", "--no-checkout",
        str(APP), str(repo),
    ], cwd=work, timeout=300)

    # Keep the remote URL only in memory, never in config or FETCH_HEAD.
    remote = git("remote", "get-url", "origin")
    run("fetch reviewed B", [
        "git", "--no-optional-locks", "fetch", "--no-tags",
        "--no-write-fetch-head", "--no-auto-maintenance",
        remote, B, D,
    ], cwd=repo, timeout=300)
    del remote
    for commit in (J, A, B, D):
        git("cat-file", "-e", commit + "^{commit}", cwd=repo)
    if git("rev-parse", D + "^{tree}", cwd=repo) != TREE:
        raise SystemExit("STOP: reviewed deployment tree differs from B")
    if git("rev-list", "--parents", "-n", "1", D, cwd=repo).split() != [D, J, B]:
        raise SystemExit("STOP: deployment is not the reviewed ordinary merge")
    git("checkout", "--detach", B, cwd=repo)
    if git("rev-parse", "HEAD^{tree}", cwd=repo) != TREE:
        raise SystemExit("STOP: candidate tree does not match reviewed B")
    if git("status", "--porcelain", "--untracked-files=no", cwd=repo):
        raise SystemExit("STOP: isolated tracked source is not clean")

    check = (
        "import json; from scripts import emergent_deployment_adapter as a; "
        "i=a.load_release_intent(); a.verify_release_intent_git(i); "
        f"assert i['source_git_sha'] == {A!r}; "
        f"assert i['source_base_git_sha'] == {J!r}; "
        f"assert i['runtime_identity']['release_id'] == {RELEASE_ID!r}; "
        f"assert i['frontend_build']['artifact_tree_sha256'] == {ARTIFACT!r}; "
        "print(json.dumps({'source':i['source_git_sha'], "
        "'release_id':i['runtime_identity']['release_id']}))"
    )
    print("ORIGINAL_INTENT_CHECK", run(
        "original candidate validation", [sys.executable, "-B", "-c", check],
        cwd=repo,
    ), flush=True)
    shared_check()
    build_env = dict(ENV, REACT_APP_BACKEND_URL="https://mezansalla.com", TMPDIR=str(work))
    print("BUILD_RUNNING: original package adapter in isolated /tmp clone", flush=True)
    with log.open("w") as output:
        result = subprocess.run(
            ["yarn", "build"], cwd=repo / "frontend", env=build_env,
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
        )
    print("ADAPTER_EXIT", result.returncode, flush=True)
    if result.returncode:
        with log.open(errors="replace") as output:
            tail = output.read()[-6000:]
        print(tail, flush=True)
        raise SystemExit("STOP: adapter failed; /app was not modified")

    intent = json.loads((repo / "release/release-intent-v5.json").read_text())
    identity = json.loads((repo / "backend/release_identity.json").read_text())
    if (identity != intent["runtime_identity"] or identity.get("release_id") != RELEASE_ID
            or intent["frontend_build"]["artifact_tree_sha256"] != ARTIFACT):
        raise SystemExit("STOP: materialized identity differs from reviewed intent")
    if git("status", "--porcelain", "--untracked-files=no", cwd=repo):
        raise SystemExit("STOP: isolated tracked source changed during build")
    text = log.read_text(errors="replace")
    proofs = []
    for match in re.finditer(r"(?m)^\{", text):
        try:
            payload, _ = json.JSONDecoder().raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("built") is True:
            proofs.append(payload)
    if len(proofs) != 1:
        raise SystemExit("STOP: original adapter success proof is missing or ambiguous")
    proof = proofs[0]
    boundary = proof.get("package_boundary", {})
    if not (
        proof.get("protocol_version") == 5
        and proof.get("source_git_sha") == A
        and proof.get("release_id") == identity["release_id"]
        and proof.get("frontend_artifact_tree_sha256")
            == intent["frontend_build"]["artifact_tree_sha256"]
        and boundary.get("frontend", {}).get("isolated_runtime_artifact_verified") is True
        and boundary.get("kind") == "mezan_release_package_boundary_proof_v1"
        and boundary.get("source_git_sha") == A
        and boundary.get("release_id") == identity["release_id"]
        and boundary.get("frontend", {}).get("content_type_contract") == "application/json"
        and boundary.get("frontend", {}).get("artifact_tree_sha256")
            == intent["frontend_build"]["artifact_tree_sha256"]
        and boundary.get("backend", {}).get("isolated_verified") is True
        and boundary.get("backend", {}).get("health_verified") is True
        and boundary.get("backend", {}).get("sibling_frontend_present") is False
        and boundary.get("backend", {}).get("git_directory_present") is False
    ):
        raise SystemExit("STOP: original adapter proof does not match reviewed release")
    print("REHEARSAL_RESULT", json.dumps({
        "built": True, "source": A, "release_id": identity["release_id"],
        "artifact_tree": proof["frontend_artifact_tree_sha256"],
        "package_boundaries_verified": True, "directory": str(work),
    }), flush=True)
    shared_check()
    remote_check()
    record = {"kind": "mezan_pr2079_rehearsal_v1", "actor": ACTOR,
              "base": J, "source": A, "intent_commit": B, "deployment": D,
              "tree": TREE, "release_id": RELEASE_ID, "artifact_tree": ARTIFACT,
              "directory": str(work), "candidate": str(repo),
              "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest()}
    owned_directory(receipt.parent)
    with os.fdopen(os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o400), "w") as stream:
        json.dump(record, stream, sort_keys=True)
        stream.write("\n")
    print("REHEARSAL_RECEIPT", str(receipt), flush=True)
    print("DONE: isolated rehearsal only; no shared output change, lease or publish", flush=True)


if __name__ == "__main__":
    main()
