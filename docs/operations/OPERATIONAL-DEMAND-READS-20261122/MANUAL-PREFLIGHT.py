import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request

def run(*args):
    try:
        p = subprocess.run(
            args, cwd="/app", text=True, capture_output=True, timeout=20,
            stdin=subprocess.DEVNULL,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
    except subprocess.TimeoutExpired:
        raise SystemExit("STOP: command timed out: " + args[0])
    if p.returncode:
        raise SystemExit("STOP: command failed: " + args[0] + " exit=" + str(p.returncode))
    return p.stdout.strip()

try:
    guard = json.loads(run(sys.executable, "-B", "scripts/production_release_guard.py", "status"))
except json.JSONDecodeError:
    raise SystemExit("STOP: Release Guard did not return valid JSON.")

fields = ("active", "actor", "prepared_at", "release_id", "source_git_sha",
          "deployment_git_sha", "incomplete_lease_directory")
print("RELEASE_GUARD", json.dumps({k: guard[k] for k in fields if k in guard}), flush=True)
if guard.get("active") is not False or guard.get("incomplete_lease_directory"):
    raise SystemExit("STOP: existing or incomplete release needs review; nothing changed.")

print("UTC", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
print("HEAD", run("git", "rev-parse", "HEAD"))
print("BRANCH", run("git", "branch", "--show-current"))
print("TRACKED_CHANGES", run("git", "status", "--porcelain", "--untracked-files=no") or "clean")
print("UNTRACKED_COUNT", len(run("git", "ls-files", "--others", "--exclude-standard").splitlines()))
print("REMOTE_REFS", run(
    "git", "ls-remote", "origin",
    "refs/heads/hotfix/prod-snap-meta-final",
    "refs/heads/codex/operational-demand-release-20261122",
    "refs/heads/codex/operational-demand-intent-20261122",
), sep="\n", flush=True)

for path in ("/api/health", "/build-meta.json", "/build-meta.json?preflight=" + str(int(time.time()))):
    info = {"path": path}
    try:
        request = urllib.request.Request("https://mezansalla.com" + path, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=15) as response:
            info.update(http_status=response.status, content_type=response.headers.get("Content-Type"))
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("response_too_large")
        data = json.loads(raw)
        info["body_sha256"] = hashlib.sha256(raw).hexdigest()
        release = data.get("release") or data
        for key in ("ok", "git_sha", "source_git_sha", "release_id", "verified_identity_available",
                    "critical_file_hashes_match", "frontend_build_verified", "boot_started_at"):
            if key in release:
                info[key] = release[key]
        if "ok" in data:
            info["ok"] = data["ok"]
    except Exception as error:
        info["error_type"] = type(error).__name__
        if hasattr(error, "code"):
            info["http_status"] = error.code
    print("LIVE_CHECK", json.dumps(info), flush=True)

print("PREFLIGHT_ONLY_DONE: no merge, checkout, lease, build or publish performed.")
