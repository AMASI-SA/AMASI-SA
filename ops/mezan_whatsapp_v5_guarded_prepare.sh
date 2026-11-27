#!/usr/bin/env bash
set -euo pipefail

cd /app
BRANCH="hotfix/prod-snap-meta-final"
EXPECTED_A="8d8654815751da1eb3f1b91314f418b8f872be09"
EXPECTED_J="19ea1404010b9c5e5135e5dbb4b40657157ff2b1"

fail() { echo "RELEASE_STOP: $*" >&2; exit 2; }

# Do not overwrite, stash or reset the owner's local Emergent changes.
test "$(git branch --show-current)" = "$BRANCH" || fail "wrong branch"
test -z "$(git status --porcelain --untracked-files=no)" || fail "tracked worktree has local changes; preserve them"
git fetch --no-tags origin "$BRANCH"
test "$(git rev-parse HEAD)" = "$(git rev-parse FETCH_HEAD)" || fail "local /app does not match remote production"
test "${AMASI_WHATSAPP_CATALOG_ENABLED:-0}" = "0" || fail "catalog feature must be OFF"
test "${AMASI_WHATSAPP_ORDER_STAGE_ENABLED:-0}" = "0" || fail "order stage feature must be OFF"

export EXPECTED_A EXPECTED_J
python3 - <<'PY'
import json, os, subprocess, urllib.request
from pathlib import Path

def fail(msg):
    raise SystemExit("RELEASE_STOP: " + msg)

repo = "AMASI-SA/AMASI-SA"
a, j = os.environ["EXPECTED_A"], os.environ["EXPECTED_J"]

def gh(path):
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/" + path,
        headers={"Accept":"application/vnd.github+json",
                 "X-GitHub-Api-Version":"2022-11-28",
                 "User-Agent":"mezan-v5-guarded-operator"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)

pr = gh("pulls/2177")
if pr.get("merged") is not True or pr.get("head",{}).get("sha") != a:
    fail("PR #2177 not merged at reviewed A")
runs = gh("actions/runs?head_sha=" + a + "&per_page=100")
checks = [r for r in runs.get("workflow_runs", [])
          if r.get("head_sha") == a and r.get("event") == "pull_request"]
if len(checks) != 26 or any(r.get("conclusion") != "success" for r in checks):
    fail("26 exact-head GitHub Actions checks have not all passed")

intent = json.loads(Path("release/release-intent-v5.json").read_text())
if not (intent.get("schema_version") == 2
        and intent.get("protocol_version") == 5
        and intent.get("source_git_sha") == a
        and intent.get("source_base_git_sha") == j
        and intent.get("branch") == "hotfix/prod-snap-meta-final"):
    fail("production intent-only B is missing or mismatched")

def git_ok(*args):
    return subprocess.run(["git", *args], check=False,
                          stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0

if not git_ok("merge-base", "--is-ancestor", j, a):
    fail("reviewed base J is not an ancestor of A")
if not git_ok("merge-base", "--is-ancestor", a, "HEAD"):
    fail("reviewed source A is not an ancestor of B")
if not git_ok("diff", "--quiet", a, "HEAD", "--", ".", ":(exclude)release/release-intent-v5.json"):
    fail("runtime or release-control changes exist after reviewed source A")
if git_ok("diff", "--quiet", a, "HEAD", "--", "release/release-intent-v5.json"):
    fail("intent-only B commit has not been produced")

# Deployment commit B also needs full protected release CI and host rehearsals.
b = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
b_runs = gh("actions/runs?head_sha=" + b + "&per_page=100")
b_success = {r.get("name"): r for r in b_runs.get("workflow_runs", [])
             if r.get("head_sha") == b and r.get("conclusion") == "success"}
required = {"Mezan Release Readiness", "CodeQL", "Security Gate"}
if not required.issubset(b_success):
    fail("final protected deployment commit B has not passed Release Readiness, CodeQL and Security Gate")
release_id = b_success["Mezan Release Readiness"]["id"]
release_jobs = gh("actions/runs/" + str(release_id) + "/jobs?per_page=100").get("jobs", [])
for node in ("20.20.2", "24.19.0"):
    if not any("Emergent Host Node " + node in job.get("name","")
               and "clean-clone adapter rehearsal" in job.get("name","")
               and job.get("conclusion") == "success" for job in release_jobs):
        fail("reviewed V5 clean-clone host rehearsal " + node + " has not passed")
print("REVIEWED_A_B_AND_CI_PASS")
PY

STATUS_JSON="$(python3 -B scripts/production_release_guard.py status)"
export STATUS_JSON
python3 - <<'PY'
import json, os
v = json.loads(os.environ["STATUS_JSON"])
assert v.get("active") is False, "RELEASE_STOP: another release lease is active"
print("RELEASE_LEASE_AVAILABLE")
PY

python3 -B scripts/production_release_guard.py prepare --actor "owner-approved-whatsapp-v5"
python3 -B scripts/production_release_guard.py prepublish
echo "PREPUBLISH_PASS: use Emergent > Re-publish changes exactly once."
