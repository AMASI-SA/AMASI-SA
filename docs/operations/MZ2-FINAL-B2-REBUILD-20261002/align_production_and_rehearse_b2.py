import json
import subprocess
import sys
from pathlib import Path

B2 = "d6c0553ae6a99a85d52b03871b7bf64024180514"
TREE = "806e935c8ecc3268f98096cd2b477050d235c99e"
A2 = "79f307f6ff69ba658958ebcdce03341b39a55939"
BASE = "83363097d48e034dc7140a60c290efc684e1ffde"
MERGED = "78dcf31af73581ceba3677c464c657a0b9b2c4fc"
BRANCH = "hotfix/prod-snap-meta-final"
REF = "refs/heads/" + BRANCH
RELEASE_ID = "rg5-8f098823ae66cfa6c93a7d8effc3a87b0515796f8ef49eba4e4bb94e53696ff6"

def run(*args):
    return subprocess.check_output(args, text=True, timeout=120).strip()

def require(ok, message):
    if not ok:
        raise SystemExit("STOP: " + message)

def guard():
    result = json.loads(run(
        sys.executable, "-B", "scripts/production_release_guard.py", "status"
    ))
    require(result.get("active") is False, "Release Guard active/unavailable.")
    return result

require(run("git", "rev-parse", "--show-toplevel") == "/app", "Wrong repository.")
require(not run("git", "status", "--porcelain"), "Checkout not clean.")
require(run("git", "rev-parse", "HEAD") in (B2, MERGED), "Unexpected starting HEAD.")
current_branch = run("git", "branch", "--show-current")
require(current_branch in ("", BRANCH), "Unexpected checked-out branch.")
guard()

run("git", "fetch", "origin", BRANCH)
require(run("git", "rev-parse", "FETCH_HEAD") == MERGED, "Production moved.")
require(run("git", "rev-parse", "origin/" + BRANCH) == MERGED, "Remote ref mismatch.")
require(run("git", "rev-parse", MERGED + "^{tree}") == TREE, "Merged TREE mismatch.")
require(run("git", "rev-list", "--parents", "-n", "1", MERGED).split()
        == [MERGED, BASE, B2], "Merge ancestry mismatch.")
run("git", "diff", "--exit-code", B2, MERGED)
require(run("git", "diff", "--name-only", A2, MERGED)
        == "release/release-intent-v5.json", "A2 to deployment invariant failed.")
guard()
require(not run("git", "status", "--porcelain"), "Checkout changed.")

if current_branch == "":
    local = subprocess.run(
        ["git", "rev-parse", "--verify", REF],
        capture_output=True, text=True, timeout=30
    )
    require(local.returncode != 0 or local.stdout.strip() in (BASE, B2, MERGED),
            "Local Production branch has unrelated changes.")
    run("git", "fetch", "origin", REF + ":" + REF)
    require(run("git", "rev-parse", REF) == MERGED, "Local branch mismatch.")
    run("git", "checkout", "--no-overwrite-ignore", BRANCH)
else:
    run("git", "merge", "--ff-only", "origin/" + BRANCH)

require(run("git", "rev-parse", "HEAD") == MERGED, "Final HEAD mismatch.")
require(run("git", "rev-parse", "HEAD^{tree}") == TREE, "Final TREE mismatch.")
require(run("git", "branch", "--show-current") == BRANCH, "Final branch mismatch.")
require(not run("git", "status", "--porcelain"), "Final checkout not clean.")
intent = json.loads(Path("release/release-intent-v5.json").read_text())
require(intent["source_git_sha"] == A2, "Intent source mismatch.")
require(intent["source_base_git_sha"] == BASE, "Intent base mismatch.")
require(intent["runtime_identity"]["release_id"] == RELEASE_ID, "Intent identity mismatch.")
guard()

print("SOURCE_MATCHED; RUNNING_EXISTING_V5_REHEARSAL_ONCE; NO_LEASE", flush=True)
subprocess.run(["yarn", "build"], cwd="/app/frontend", check=True)
runtime = json.loads(Path("backend/release_identity.json").read_text())
require(runtime == intent["runtime_identity"], "Materialized identity mismatch.")
require(run("git", "rev-parse", "HEAD") == MERGED, "HEAD changed during build.")
require(run("git", "rev-parse", "HEAD^{tree}") == TREE, "TREE changed during build.")
require(not run("git", "status", "--porcelain"), "Build changed tracked/untracked source.")
require(run("git", "ls-remote", "origin", REF) == MERGED + "\t" + REF,
        "Production moved during build.")
status = guard()

print(json.dumps({
    "result": "PRODUCTION_SOURCE_MATCHED_AND_V5_REHEARSAL_PASS",
    "deployment_git_sha": MERGED,
    "approved_B2": B2,
    "TREE": TREE,
    "source_A2": A2,
    "branch": BRANCH,
    "status": "clean",
    "release_id": RELEASE_ID,
    "release_guard": status,
    "rollback_sha": BASE,
    "prepare": "NOT_EXECUTED",
    "prepublish": "NOT_EXECUTED",
    "lease": "NOT_CREATED",
    "deploy": "NOT_EXECUTED",
    "production_financial_writes_by_this_script": 0
}, indent=2))
