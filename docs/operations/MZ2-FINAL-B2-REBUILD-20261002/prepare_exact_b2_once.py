import json
import subprocess
import sys

DEPLOYMENT = "78dcf31af73581ceba3677c464c657a0b9b2c4fc"
TREE = "806e935c8ecc3268f98096cd2b477050d235c99e"
SOURCE = "79f307f6ff69ba658958ebcdce03341b39a55939"
ROLLBACK = "83363097d48e034dc7140a60c290efc684e1ffde"
BRANCH = "hotfix/prod-snap-meta-final"
RELEASE_ID = "rg5-8f098823ae66cfa6c93a7d8effc3a87b0515796f8ef49eba4e4bb94e53696ff6"
ACTOR = "codex-mz2-final-b2-deploy-20261002"

def run(*args):
    return subprocess.check_output(args, text=True).strip()

def require(ok, message):
    if not ok:
        raise SystemExit("STOP: " + message)

guard = [sys.executable, "-B", "scripts/production_release_guard.py"]
require(run("git", "rev-parse", "--show-toplevel") == "/app", "Wrong repository.")
require(run("git", "rev-parse", "HEAD") == DEPLOYMENT, "Unexpected HEAD.")
require(run("git", "rev-parse", "HEAD^{tree}") == TREE, "Unexpected TREE.")
require(run("git", "branch", "--show-current") == BRANCH, "Unexpected branch.")
require(not run("git", "status", "--porcelain"), "Checkout is not clean.")
before = json.loads(run(*guard, "status"))
require(before.get("active") is False, "An existing lease is active; do not retry.")

# The repository guard is the only code creating a lease. Invoke it once.
prepared = json.loads(run(*guard, "prepare", "--actor", ACTOR))
expected = {
    "protocol_version": 5,
    "deployment_git_sha": DEPLOYMENT,
    "source_git_sha": SOURCE,
    "source_base_git_sha": ROLLBACK,
    "branch": BRANCH,
    "actor": ACTOR,
    "release_id": RELEASE_ID,
}
for key, value in expected.items():
    require(prepared.get(key) == value,
            "Prepared identity mismatch: " + key + "; inspect status, do not retry.")
after = json.loads(run(*guard, "status"))
require(after.get("active") is True, "Prepared lease is not active; do not retry.")
for key, value in expected.items():
    require(after.get(key) == value,
            "Lease changed: " + key + "; inspect status, do not retry.")

print(json.dumps({
    "result": "PREPARED_NOT_PUBLISHED",
    **expected,
    "TREE": TREE,
    "prepared_at": prepared.get("prepared_at"),
    "release_guard_active": True,
    "prepare_invocations": 1,
    "frontend_artifact_tree_sha256": prepared["frontend_build"]["artifact_tree_sha256"],
    "build_meta_sha256": prepared["frontend_build"]["build_meta"]["sha256"],
    "rollback_sha": ROLLBACK,
    "prepublish": "NOT_EXECUTED",
    "publish": "NOT_EXECUTED",
    "deploy": "NOT_EXECUTED",
    "opening": "NO",
    "inventory_initialization": "NO",
    "activation": "NO",
    "production_financial_writes_by_this_script": 0
}, indent=2))
