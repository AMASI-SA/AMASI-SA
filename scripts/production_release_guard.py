#!/usr/bin/env python3
"""Fail-closed production release lease and deployed-SHA verifier."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


PRODUCTION_BRANCH = "hotfix/prod-snap-meta-final"
PROTOCOL_VERSION = 2
BOOT_CLOCK_SKEW = timedelta(minutes=5)
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
_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


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


def _utc_datetime() -> datetime:
    return datetime.now(timezone.utc)


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


@contextmanager
def _release_operation_lock():
    """Serialize every lease lifecycle operation across local processes."""
    lock_path = LEASE_DIR.parent / f".{LEASE_DIR.name}.operation.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise ReleaseGuardError(
            f"cannot open production release operation lock: {exc}"
        ) from exc
    with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def prepare(actor: str) -> dict[str, Any]:
    if not GIT_DIR.is_dir():
        raise ReleaseGuardError(f"{REPO_ROOT} is not a git worktree")
    with _release_operation_lock():
        return _prepare_locked(actor)


def _prepare_locked(actor: str) -> dict[str, Any]:
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
            "release_id": str(uuid4()),
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
    with _release_operation_lock():
        if not LEASE_PATH.exists():
            return {"active": False}
        return {"active": True, **_read_json(LEASE_PATH)}


def prepublish() -> dict[str, Any]:
    with _release_operation_lock():
        return _prepublish_locked()


def _prepublish_locked() -> dict[str, Any]:
    if not LEASE_PATH.exists():
        raise ReleaseGuardError("no prepared production release lease")
    lease = _read_json(LEASE_PATH)
    expected_sha, expected_release_id = _validated_release_lease(lease)
    branch = _run_git("branch", "--show-current")
    if branch != PRODUCTION_BRANCH:
        raise ReleaseGuardError(f"wrong branch {branch!r}")
    dirty = _run_git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise ReleaseGuardError("tracked worktree changed after prepare:\n" + dirty)
    _run_git("fetch", "origin", PRODUCTION_BRANCH)
    local_sha = _run_git("rev-parse", "HEAD")
    remote_sha = _run_git("rev-parse", f"origin/{PRODUCTION_BRANCH}")
    if not (local_sha == remote_sha == expected_sha):
        raise ReleaseGuardError(
            "release SHA changed after prepare: "
            f"lease={expected_sha} local={local_sha} remote={remote_sha}"
        )
    identity = _read_json(IDENTITY_PATH)
    if identity != lease or identity.get("critical_file_hashes") != _critical_hashes():
        raise ReleaseGuardError("release identity or critical files changed after prepare")
    _assert_active_lease_unchanged(lease)
    return {
        "ready_to_publish": True,
        "git_sha": expected_sha,
        "release_id": expected_release_id,
        "protocol_version": PROTOCOL_VERSION,
    }


def _health_payload(base_url: str) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api/health"
    url += "?" + urllib.parse.urlencode({
        "release_check": secrets.token_hex(16),
    })
    request = urllib.request.Request(
        url,
        headers={
            "Cache-Control": "no-cache",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; MezanReleaseGuard/1.0)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ReleaseGuardError(f"production health check failed: {exc}") from exc


def _aware_timestamp(value: Any, label: str) -> tuple[str, datetime]:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseGuardError(f"{label} is missing")
    raw = value.strip()
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseGuardError(f"{label} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReleaseGuardError(f"{label} must include a timezone")
    return raw, parsed


def _uuid4(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseGuardError(f"{label} is invalid")
    raw = value.strip()
    try:
        parsed = UUID(raw)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ReleaseGuardError(f"{label} is invalid") from exc
    if parsed.version != 4 or str(parsed) != raw:
        raise ReleaseGuardError(f"{label} is invalid")
    return raw


def _validated_release_lease(lease: dict[str, Any]) -> tuple[str, str]:
    """Validate the exact v2 lease identity before any release mutation."""
    try:
        protocol_version = int(lease.get("protocol_version") or 0)
    except (TypeError, ValueError) as exc:
        raise ReleaseGuardError("prepared release protocol is invalid") from exc
    if protocol_version != PROTOCOL_VERSION:
        raise ReleaseGuardError(
            "prepared release protocol is unsupported; abort the existing "
            "lease with its original guard, update the workspace, then "
            f"prepare again with protocol v{PROTOCOL_VERSION}"
        )
    expected_sha = str(lease.get("git_sha") or "").strip().lower()
    if not _FULL_GIT_SHA.fullmatch(expected_sha):
        raise ReleaseGuardError("prepared release SHA is invalid")
    expected_release_id = _uuid4(
        lease.get("release_id"), "prepared release id"
    )
    return expected_sha, expected_release_id


def _assert_active_lease_unchanged(
    expected_lease: dict[str, Any],
) -> tuple[str, str]:
    """Re-read and fence the active lease immediately before final action."""
    if not LEASE_PATH.exists():
        raise ReleaseGuardError("production release lease disappeared")
    active_lease = _read_json(LEASE_PATH)
    expected_sha, expected_release_id = _validated_release_lease(
        expected_lease
    )
    active_sha, active_release_id = _validated_release_lease(active_lease)
    if (
        active_sha != expected_sha
        or active_release_id != expected_release_id
        or active_lease != expected_lease
    ):
        raise ReleaseGuardError(
            "active production release lease changed during operation: "
            f"expected={expected_release_id} active={active_release_id}"
        )
    return active_sha, active_release_id


def _remove_active_lease(expected_lease: dict[str, Any]) -> tuple[str, str]:
    """Delete only the exact lease observed under the operation mutex."""
    active_sha, active_release_id = _assert_active_lease_unchanged(
        expected_lease
    )
    shutil.rmtree(LEASE_DIR)
    return active_sha, active_release_id


def verify(base_url: str) -> dict[str, Any]:
    with _release_operation_lock():
        return _verify_locked(base_url)


def _verify_locked(base_url: str) -> dict[str, Any]:
    if not LEASE_PATH.exists():
        raise ReleaseGuardError("no prepared production release lease")
    lease = _read_json(LEASE_PATH)
    expected_sha, expected_release_id = _validated_release_lease(lease)
    identity_fields = (
        "release_id",
        "git_sha",
        "branch",
        "prepared_at",
        "protocol_version",
        "critical_file_hashes",
    )
    expected_identity = {field: lease.get(field) for field in identity_fields}
    expected_identity["release_id"] = expected_release_id
    _, prepared_at = _aware_timestamp(
        expected_identity["prepared_at"], "prepared release timestamp"
    )
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
        identity_mismatches = [
            field
            for field, expected in expected_identity.items()
            if release.get(field) != expected
        ]
        if identity_mismatches:
            raise ReleaseGuardError(
                "production release identity does not match prepared lease: "
                + ", ".join(identity_mismatches)
            )
        boot_started_at, boot_started_at_timestamp = _aware_timestamp(
            release.get("boot_started_at"), "production boot identity"
        )
        now = _utc_datetime()
        if boot_started_at_timestamp < prepared_at - BOOT_CLOCK_SKEW:
            raise ReleaseGuardError(
                "production boot identity predates prepared release"
            )
        if boot_started_at_timestamp > now + BOOT_CLOCK_SKEW:
            raise ReleaseGuardError("production boot identity is in the future")
        observations.append({
            "git_sha": actual_sha,
            "boot_started_at": boot_started_at,
        })
        if attempt < 2:
            time.sleep(2)
    boot_started_at_observations = list(dict.fromkeys(
        row["boot_started_at"] for row in observations
    ))
    _remove_active_lease(lease)
    return {
        "verified": True,
        "git_sha": actual_sha,
        "release_id": expected_release_id,
        "protocol_version": PROTOCOL_VERSION,
        "url": base_url.rstrip("/"),
        "checks": len(observations),
        "boot_started_at": observations[0]["boot_started_at"],
        "boot_started_at_observations": boot_started_at_observations,
    }


def abort(expected_sha: str, expected_release_id: str) -> dict[str, Any]:
    with _release_operation_lock():
        return _abort_locked(expected_sha, expected_release_id)


def _abort_locked(
    expected_sha: str, expected_release_id: str
) -> dict[str, Any]:
    if not LEASE_PATH.exists():
        raise ReleaseGuardError("no active production release lease")
    lease = _read_json(LEASE_PATH)
    actual_sha, actual_release_id = _validated_release_lease(lease)
    expected_sha = str(expected_sha or "").strip().lower()
    expected_release_id = _uuid4(
        expected_release_id, "expected release id"
    )
    if expected_sha != actual_sha:
        raise ReleaseGuardError(
            "lease SHA mismatch: "
            f"expected argument={expected_sha} active={actual_sha}"
        )
    if expected_release_id != actual_release_id:
        raise ReleaseGuardError(
            "lease release id mismatch: "
            f"expected argument={expected_release_id} active={actual_release_id}"
        )
    _remove_active_lease(lease)
    return {
        "aborted": True,
        "git_sha": actual_sha,
        "release_id": actual_release_id,
        "protocol_version": PROTOCOL_VERSION,
    }


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
    abort_parser.add_argument("--expected-release-id", required=True)
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
            result = abort(args.expected_sha, args.expected_release_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ReleaseGuardError as exc:
        print(f"RELEASE_GUARD_REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
