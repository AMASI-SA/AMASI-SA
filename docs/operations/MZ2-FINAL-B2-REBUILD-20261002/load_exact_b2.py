import json
import subprocess
import sys

B2 = "d6c0553ae6a99a85d52b03871b7bf64024180514"
TREE = "806e935c8ecc3268f98096cd2b477050d235c99e"
BASE = "83363097d48e034dc7140a60c290efc684e1ffde"
OLD_B = "e030b737ca50adb03f37b06dab2a5624d79474fa"
REF = "refs/heads/codex/mz2-final-b2-rebuild-20261002"
PROD = "refs/heads/hotfix/prod-snap-meta-final"

def run(*args):
    return subprocess.check_output(args, text=True, timeout=120).strip()

def require(ok, message):
    if not ok:
        raise SystemExit("STOP: " + message)

def guard():
    result = json.loads(run(
        sys.executable, "-B", "scripts/production_release_guard.py", "status"
    ))
    require(result.get("active") is False, "Guard active or unavailable.")
    return result

require(run("git", "rev-parse", "--show-toplevel") == "/app",
        "Wrong repository directory.")
require(not run("git", "status", "--porcelain"),
        "Checkout is not clean; preserve all changes.")
require(run("git", "rev-parse", "HEAD") in (BASE, OLD_B, B2),
        "Unexpected starting HEAD.")
guard()

run("git", "fetch", "origin", "--prune")
require(run("git", "ls-remote", "origin", PROD) == BASE + "\t" + PROD,
        "Production changed; reassessment required.")
require(run("git", "ls-remote", "origin", REF) == B2 + "\t" + REF,
        "Remote candidate mismatch.")
run("git", "fetch", "origin", REF)
require(run("git", "rev-parse", "FETCH_HEAD") == B2,
        "Fetched candidate mismatch.")
require(run("git", "rev-parse", B2 + "^{tree}") == TREE,
        "Candidate TREE mismatch.")
guard()
require(not run("git", "status", "--porcelain"),
        "Checkout changed during verification.")

run("git", "checkout", "--detach", "--no-overwrite-ignore", B2)
require(run("git", "rev-parse", "HEAD") == B2, "Final HEAD mismatch.")
require(run("git", "rev-parse", "HEAD^{tree}") == TREE, "Final TREE mismatch.")
require(not run("git", "status", "--porcelain"), "Final checkout not clean.")
release_guard = guard()
require(run("git", "ls-remote", "origin", PROD) == BASE + "\t" + PROD,
        "Production changed during verification.")

print(json.dumps({
    "result": "EXACT_B2_LOADED",
    "HEAD": B2,
    "TREE": TREE,
    "status": "clean",
    "checkout": "detached HEAD",
    "release_guard": release_guard,
    "prepare": "NOT_EXECUTED",
    "prepublish": "NOT_EXECUTED",
    "lease": "NOT_CREATED",
    "merge": "NOT_EXECUTED",
    "deploy": "NOT_EXECUTED",
    "production_financial_writes_by_this_script": 0
}, indent=2))
