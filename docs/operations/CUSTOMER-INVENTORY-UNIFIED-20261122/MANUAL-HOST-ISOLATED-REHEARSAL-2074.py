import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from scripts import production_release_guard as guard

J = "06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35"
A = "4e6c8a61bd1566e061a4f312e797ce239b582475"
B = "447e83bfb95d2627877d46c7baa528e454a2e143"
TREE = "6a906db6cf4fd3dda4b550483a0ef96067c6f1ed"
RELEASE_ID = "rg5-2e0e3797d6ff3b955a3a23fd31487bd92721e70f316f3bdf34cab0d94c5d630f"
APP = Path("/app")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0")


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
    if guard.REPO_ROOT.resolve() != APP:
        raise SystemExit("STOP: original /app guard required")
    state = guard.status()
    print("SHARED_GUARD", json.dumps({
        "active": state.get("active"),
        "incomplete": state.get("incomplete_lease_directory", False),
    }), flush=True)
    if state != {"active": False}:
        raise SystemExit("STOP: shared guard is no longer inactive")
    if git("rev-parse", "HEAD") != J or git(
        "status", "--porcelain", "--untracked-files=no"
    ):
        raise SystemExit("STOP: shared checkout changed; candidate retained")


if any(name in os.environ for name in (
    "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "GIT_DIR",
    "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)):
    raise SystemExit("STOP: unexpected CI or Git routing environment")
shared_check()
refs = dict(line.split()[::-1] for line in git(
    "ls-remote", "--exit-code", "origin",
    "refs/heads/hotfix/prod-snap-meta-final",
    "refs/heads/codex/customer-inventory-unified-r2-20261122",
).splitlines())
if refs.get("refs/heads/hotfix/prod-snap-meta-final") != J or refs.get(
    "refs/heads/codex/customer-inventory-unified-r2-20261122"
) != B:
    raise SystemExit("STOP: remote release references changed")
if shutil.disk_usage("/tmp").free < 10 * 1024**3:
    raise SystemExit("STOP: less than 10 GiB free in /tmp")

work = Path(tempfile.mkdtemp(prefix="mezan-pr2074-", dir="/tmp"))
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
    remote, B,
], cwd=repo, timeout=300)
del remote
for commit in (J, A, B):
    git("cat-file", "-e", commit + "^{commit}", cwd=repo)
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
if identity != intent["runtime_identity"] or identity.get("release_id") != RELEASE_ID:
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
print("DONE: isolated rehearsal only; no merge, shared output change, lease or publish", flush=True)
