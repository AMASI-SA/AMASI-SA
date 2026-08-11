#!/usr/bin/env python3
"""Fail-closed production release lease and deployed-SHA verifier."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PRODUCTION_BRANCH = "hotfix/prod-snap-meta-final"
PROTOCOL_VERSION = 1
REPO_ROOT = Path(__file__).resolve().parents[1]
GIT_DIR = REPO_ROOT / ".git"
LEASE_DIR = GIT_DIR / "mezan-production-release.lock"
LEASE_PATH = LEASE_DIR / "lease.json"
IDENTITY_PATH = REPO_ROOT / "backend" / "release_identity.json"
CRITICAL_FILES = (
    "backend/server.py",
    "backend/integrations/qoyod_manual/routes.py",
    "backend/integrations/qoyod_manual/send.py",
)


class ReleaseGuardError(RuntimeError):
    pass


def _run_git(*args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode:
        raise ReleaseGuardError(
            f"git {' '.join(args)} failed: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _critical_hashes() -> dict[str, str]:
    return {
        relative.removeprefix("backend/"): _sha256(REPO_ROOT / relative)
        for relative in CRITICAL_FILES
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReleaseGuardError(f"cannot read {path}: {exc}") from exc


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def prepare(actor: str) -> dict[str, Any]:
    if not GIT_DIR.is_dir():
        raise ReleaseGuardError(f"{REPO_ROOT} is not a git worktree")
    try:
        LEASE_DIR.mkdir(mode=0o700)
    except FileExistsError as exc:
        lease = _read_json(LEASE_PATH) if LEASE_PATH.exists() else {}
        raise ReleaseGuardError(
            "another production release is active: "
            + json.dumps(lease, ensure_ascii=False)
        ) from exc

    try:
        branch = _run_git("branch", "--show-current")
        if branch != PRODUCTION_BRANCH:
            raise ReleaseGuardError(
                f"wrong branch {branch!r}; expected {PRODUCTION_BRANCH!r}"
            )
        dirty = _run_git("status", "--porcelain", "--untracked-files=no")
        if dirty:
            raise ReleaseGuardError(
                "tracked worktree changes exist; commit/push them first:\n"
                + dirty
            )
        _run_git("fetch", "origin", PRODUCTION_BRANCH)
        local_sha = _run_git("rev-parse", "HEAD")
        remote_sha = _run_git(
            "rev-parse", f"origin/{PRODUCTION_BRANCH}"
        )
        if local_sha != remote_sha:
            raise ReleaseGuardError(
                "workspace does not match GitHub production branch: "
                f"local={local_sha} remote={remote_sha}"
            )
        payload = {
            "git_sha": local_sha,
            "branch": branch,
            "actor": actor,
            "prepared_at": _utc_now(),
            "protocol_version": PROTOCOL_VERSION,
            "critical_file_hashes": _critical_hashes(),
        }
        _atomic_json(IDENTITY_PATH, payload)
        _atomic_json(LEASE_PATH, payload)
        return payload
    except Exception:
        shutil.rmtree(LEASE_DIR, ignore_errors=True)
        raise


def status() -> dict[str, Any]:
    if not LEASE_PATH.exists():
        return {"active": False}
    return {"active": True, **_read_json(LEASE_PATH)}


def prepublish() -> dict[str, Any]:
    if not LEASE_PATH.exists():
        raise ReleaseGuardError("no prepared production release lease")
    lease = _read_json(LEASE_PATH)
    branch = _run_git("branch", "--show-current")
    if branch != PRODUCTION_BRANCH:
        raise ReleaseGuardError(f"wrong branch {branch!r}")
    dirty = _run_git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise ReleaseGuardError("tracked worktree changed after prepare:\n" + dirty)
    _run_git("fetch", "origin", PRODUCTION_BRANCH)
    local_sha = _run_git("rev-parse", "HEAD")
    remote_sha = _run_git("rev-parse", f"origin/{PRODUCTION_BRANCH}")
    expected_sha = str(lease.get("git_sha") or "")
    if not (local_sha == remote_sha == expected_sha):
        raise ReleaseGuardError(
            "release SHA changed after prepare: "
            f"lease={expected_sha} local={local_sha} remote={remote_sha}"
        )
    identity = _read_json(IDENTITY_PATH)
    if identity != lease or identity.get("critical_file_hashes") != _critical_hashes():
        raise ReleaseGuardError("release identity or critical files changed after prepare")
    return {"ready_to_publish": True, "git_sha": expected_sha}


def _health_payload(base_url: str) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/health"
    url += "?" + urllib.parse.urlencode({"release_check": int(time.time())})
    request = urllib.request.Request(
        url,
        headers={"Cache-Control": "no-cache", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ReleaseGuardError(f"production health check failed: {exc}") from exc


def verify(base_url: str) -> dict[str, Any]:
    if not LEASE_PATH.exists():
        raise ReleaseGuardError("no prepared production release lease")
    lease = _read_json(LEASE_PATH)
    expected_sha = str(lease.get("git_sha") or "")
    observations = []
    for attempt in range(3):
        health = _health_payload(base_url)
        release = health.get("release") or {}
        actual_sha = str(release.get("git_sha") or "")
        if health.get("ok") is not True:
            raise ReleaseGuardError("production health is not ok")
        if not release.get("verified_identity_available"):
            raise ReleaseGuardError("production has no verified release identity")
        if actual_sha != expected_sha:
            raise ReleaseGuardError(
                f"deployed SHA mismatch: expected={expected_sha} actual={actual_sha}"
            )
        if release.get("critical_file_hashes_match") is not True:
            raise ReleaseGuardError("production critical file hashes do not match")
        observations.append({
            "git_sha": actual_sha,
            "boot_started_at": release.get("boot_started_at"),
        })
        if attempt < 2:
            time.sleep(2)
    if len({row["boot_started_at"] for row in observations}) != 1:
        raise ReleaseGuardError("production boot identity changed during verification")
    shutil.rmtree(LEASE_DIR)
    return {
        "verified": True,
        "git_sha": actual_sha,
        "url": base_url.rstrip("/"),
        "checks": len(observations),
        "boot_started_at": observations[0]["boot_started_at"],
    }


def abort(expected_sha: str) -> dict[str, Any]:
    if not LEASE_PATH.exists():
        raise ReleaseGuardError("no active production release lease")
    lease = _read_json(LEASE_PATH)
    actual = str(lease.get("git_sha") or "")
    if expected_sha != actual:
        raise ReleaseGuardError(
            f"lease SHA mismatch: expected argument={expected_sha} active={actual}"
        )
    shutil.rmtree(LEASE_DIR)
    return {"aborted": True, "git_sha": actual}


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument(
        "--actor", default=os.environ.get("USER") or "unknown"
    )
    sub.add_parser("status")
    sub.add_parser("prepublish")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--url", default="https://mezansalla.com")
    abort_parser = sub.add_parser("abort")
    abort_parser.add_argument("--expected-sha", required=True)
    args = parser.parse_args()

    try:
        if args.command == "prepare":
            result = prepare(args.actor)
        elif args.command == "status":
            result = status()
        elif args.command == "prepublish":
            result = prepublish()
        elif args.command == "verify":
            result = verify(args.url)
        else:
            result = abort(args.expected_sha)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ReleaseGuardError as exc:
        print(f"RELEASE_GUARD_REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
