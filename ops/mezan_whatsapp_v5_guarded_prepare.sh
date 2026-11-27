#!/usr/bin/env bash
set -euo pipefail

cd /app
BRANCH="hotfix/prod-snap-meta-final"
EXPECTED_ORIGINAL_A="8d8654815751da1eb3f1b91314f418b8f872be09"
EXPECTED_SOURCE_P="d4a623657fd3cb47c769095e5886b2f52dabaa3c"
EXPECTED_B_HEAD="6d2300d43a8a3a4ee856b4740cc514c9506fce71"
EXPECTED_DEPLOYMENT_M="53e52767a1bf2b4efd4ec796e8c59de9425c8839"
EXPECTED_J="19ea1404010b9c5e5135e5dbb4b40657157ff2b1"
EXPECTED_RELEASE_ID="rg5-e039d675b02789f0164e5595e19de3b63e49ab1815970a631edd620f8d239d82"

fail() { echo "RELEASE_STOP: $*" >&2; exit 2; }

# Do not overwrite, stash or reset the owner's local Emergent changes.
test "$(git branch --show-current)" = "$BRANCH" || fail "wrong branch"
test -z "$(git status --porcelain --untracked-files=no)" || fail "tracked worktree has local changes; preserve them"
git fetch --no-tags origin "$BRANCH"
test "$(git rev-parse HEAD)" = "$(git rev-parse FETCH_HEAD)" || fail "local /app does not match remote production"
test "${AMASI_WHATSAPP_CATALOG_ENABLED:-0}" = "0" || fail "catalog feature must be OFF"
test "${AMASI_WHATSAPP_ORDER_STAGE_ENABLED:-0}" = "0" || fail "order stage feature must be OFF"

export EXPECTED_ORIGINAL_A EXPECTED_SOURCE_P EXPECTED_B_HEAD EXPECTED_DEPLOYMENT_M EXPECTED_J EXPECTED_RELEASE_ID
python3 - <<'PY'
import json, os, subprocess, urllib.request
from pathlib import Path

def fail(message):
    raise SystemExit("RELEASE_STOP: " + message)

def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()

def git_ok(*args):
    return subprocess.run(["git", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

repo = "AMASI-SA/AMASI-SA"
a = os.environ["EXPECTED_ORIGINAL_A"]
p = os.environ["EXPECTED_SOURCE_P"]
b = os.environ["EXPECTED_B_HEAD"]
m = os.environ["EXPECTED_DEPLOYMENT_M"]
j = os.environ["EXPECTED_J"]
release_id = os.environ["EXPECTED_RELEASE_ID"]

def gh(path):
    req = urllib.request.Request(
        "https://api.github.com/repos/" + repo + "/" + path,
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "mezan-v5-guarded-operator"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)

if git("rev-parse", "HEAD") != m:
    fail("worktree is not exact reviewed V5 merge commit")
if git("rev-list", "--parents", "-n", "1", p).split() != [
    p, "54a655ac88c1f67c280728cc723eddd069ac1741", a
]:
    fail("source P parents changed")
if git("rev-list", "--parents", "-n", "1", m).split() != [m, p, b]:
    fail("deployment B merge parents changed")
if git("rev-parse", p + "^{tree}") != git("rev-parse", a + "^{tree}"):
    fail("reviewed source A and merged source P differ")
if not git_ok("merge-base", "--is-ancestor", j, p):
    fail("previous reviewed intent J is not ancestor of source P")
if not git_ok("merge-base", "--is-ancestor", b, m):
    fail("reviewed intent B is not ancestor of deployment M")
if git("diff", "--name-only", p, m) != "release/release-intent-v5.json":
    fail("deployment contains changes other than reviewed V5 intent")

for number, expected_head in ((2177, a), (2186, b)):
    pr = gh("pulls/" + str(number))
    if not pr.get("merged") or pr.get("head", {}).get("sha") != expected_head:
        fail("PR " + str(number) + " not merged at approved head")
if gh("pulls/2186").get("merge_commit_sha") != m:
    fail("PR #2186 merge commit differs from intended V5 deployment")

def workflow_runs(sha):
    return [
        run for run in gh("actions/runs?head_sha=" + sha + "&per_page=100").get("workflow_runs", [])
        if run.get("head_sha") == sha
    ]

source_runs = [r for r in workflow_runs(a) if r.get("event") == "pull_request"]
if len(source_runs) != 26 or any(r.get("conclusion") != "success" for r in source_runs):
    fail("original 26 WhatsApp source tests are not all green")

b_runs = [r for r in workflow_runs(b) if r.get("event") == "pull_request"]
if len(b_runs) != 8:
    fail("reviewed B workflow count differs")
expected = {
    "Mezan Release Readiness", "Security Gate", "CodeQL",
    "Operational Balance Web", "Qoyod Payment Freshness",
    "Snapchat Direct Active Publication", "Snapchat Settings Management V2",
}
for name in expected:
    if not any(r["name"] == name and r.get("conclusion") == "success" for r in b_runs):
        fail("required B workflow not green: " + name)
if not any(r["name"] == "TikTok Creator Release Candidate" and r.get("conclusion") == "skipped" for r in b_runs):
    fail("unexpected B workflow disposition")

push_runs = [r for r in workflow_runs(m) if r.get("event") == "push"]
for name in ("Mezan Release Readiness", "Security Gate", "CodeQL"):
    if not any(r["name"] == name and r.get("conclusion") == "success" for r in push_runs):
        fail("production merge push gate not green: " + name)

readiness = next(r for r in b_runs if r["name"] == "Mezan Release Readiness")
jobs = gh("actions/runs/" + str(readiness["id"]) + "/jobs?per_page=100")["jobs"]
for node in ("20.20.2", "24.19.0"):
    name = "Emergent Host Node " + node + " clean-clone adapter rehearsal"
    if not any(job.get("name") == name and job.get("conclusion") == "success" for job in jobs):
        fail("reviewed Node rehearsal not green: " + node)

intent = json.loads(Path("release/release-intent-v5.json").read_text(encoding="utf-8"))
if not (
    intent.get("schema_version") == 2 and
    intent.get("protocol_version") == 5 and
    intent.get("source_git_sha") == p and
    intent.get("source_base_git_sha") == j and
    intent.get("branch") == "hotfix/prod-snap-meta-final" and
    intent.get("runtime_identity", {}).get("release_id") == release_id
):
    fail("reviewed V5 release identity or source binding differs")
print("REVIEWED_V5_SOURCE_INTENT_CI_PASS")
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
