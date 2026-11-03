#!/usr/bin/env python3
"""Read-only, credential-free prior-release identity and host lease diagnostic.

No database, production, hosting, order, provider, or release-lease writes.
"""
import json
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path("/app")
EXPECTED_SHA = "7d1be020b9559ea294b6ea3f7511f37ee5e6a847"
EXPECTED_RELEASE_ID = "rg5-b3ee097a92d53974c278005cdf322c01be4b03d669f687326e2c7d61e0bd38ad"


def command(*argv):
    return subprocess.check_output(argv, cwd=ROOT, text=True, timeout=20).strip()


class DenyRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Unexpected redirect; refusing to follow")


def main():
    if not ROOT.is_dir():
        raise RuntimeError("Expected /app worktree is missing")
    branch = command("git", "branch", "--show-current")
    host_sha = command("git", "rev-parse", "HEAD")
    guard = command("python3", "-B", "scripts/production_release_guard.py", "status")
    lease = json.loads(guard)
    if not isinstance(lease, dict):
        raise RuntimeError("Release lease status is not a JSON object")
    query = secrets.token_hex(12)
    url = f"https://mezansalla.com/api/health?release_check={query}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "MezanV5ReadOnlyIdentityDiagnostic/1.0",
    })
    with urllib.request.build_opener(DenyRedirect).open(req, timeout=20) as response:
        health = json.loads(response.read().decode("utf-8"))
    release = health.get("release") or {}
    if not isinstance(release, dict):
        raise RuntimeError("Health release object missing or invalid")
    observed = {
        "health_ok": health.get("ok") is True,
        "verified_identity_available": release.get("verified_identity_available") is True,
        "backend_runtime_source_verified": release.get("backend_runtime_source_verified") is True,
        "release_control_source_bound": release.get("release_control_source_bound") is True,
        "frontend_build_verified": release.get("frontend_build_verified") is True,
        "live_deployment_git_sha": release.get("deployment_git_sha"),
        "live_source_git_sha": release.get("source_git_sha"),
        "live_release_id": release.get("release_id"),
    }
    report = {
        "scope": "READ_ONLY_NO_RELEASE_OR_DATA_WRITES",
        "host_branch": branch,
        "host_git_sha": host_sha,
        "expected_deployment_git_sha": EXPECTED_SHA,
        "expected_release_id": EXPECTED_RELEASE_ID,
        "lease_active": lease.get("active"),
        "lease_incomplete": bool(lease.get("incomplete_lease_directory")),
        "lease_deployment_git_sha": lease.get("deployment_git_sha") if lease.get("active") else None,
        "live": observed,
    }
    safe_match = (
        branch == "hotfix/prod-snap-meta-final" and
        host_sha == EXPECTED_SHA and
        lease.get("active") is False and
        not lease.get("incomplete_lease_directory") and
        all(observed[k] for k in (
            "health_ok", "verified_identity_available", "backend_runtime_source_verified",
            "release_control_source_bound", "frontend_build_verified"
        )) and
        observed["live_deployment_git_sha"] == EXPECTED_SHA and
        observed["live_release_id"] == EXPECTED_RELEASE_ID
    )
    report["ready_for_next_intent_only"] = safe_match
    if not safe_match:
        report["action"] = (
            "STOP: do not build or merge next release. Determine which exact release "
            "is live; if previous J still needs deployment, complete its governed "
            "Emergent Cloud Publish and release-guard verification first."
        )
    else:
        report["action"] = "Previous J is live and host lease is clear; reviewed next intent may be prepared."
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if safe_match else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, urllib.error.URLError) as exc:
        print("READ_ONLY_DIAGNOSTIC_STOP: " + type(exc).__name__ + ": " + str(exc)[:260], file=sys.stderr)
        raise SystemExit(3)
