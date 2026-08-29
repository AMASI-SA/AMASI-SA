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


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from frontend_build_identity import (  # noqa: E402
    FrontendBuildIdentityError,
    read_frontend_build_identity,
)


PRODUCTION_BRANCH = "hotfix/prod-snap-meta-final"
PRODUCTION_ORIGIN = "https://mezansalla.com"
SPA_SHELL_PATH = "/snapchat-accounts"
PROTOCOL_VERSION = 3
BOOT_CLOCK_SKEW = timedelta(minutes=5)
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


def _frontend_build_identity(expected_git_sha: str) -> dict[str, Any]:
    try:
        return read_frontend_build_identity(
            expected_git_sha=expected_git_sha,
            require_git_source=True,
        )
    except FrontendBuildIdentityError as exc:
        raise ReleaseGuardError(f"frontend build proof failed: {exc}") from exc


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
        frontend_build = _frontend_build_identity(local_sha)
        payload = {
            "release_id": str(uuid4()),
            "git_sha": local_sha,
            "branch": branch,
            "actor": actor,
            "prepared_at": _utc_now(),
            "protocol_version": PROTOCOL_VERSION,
            "critical_file_hashes": _critical_hashes(),
            "frontend_build": frontend_build,
        }
        if (
            _run_git("rev-parse", "HEAD") != local_sha
            or _frontend_build_identity(local_sha) != frontend_build
        ):
            raise ReleaseGuardError(
                "frontend source or build changed while preparing release"
            )
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
    frontend_build = _frontend_build_identity(expected_sha)
    if (
        identity != lease
        or identity.get("critical_file_hashes") != _critical_hashes()
        or identity.get("frontend_build") != frontend_build
    ):
        raise ReleaseGuardError(
            "release identity, critical files, or frontend build changed after prepare"
        )
    if _frontend_build_identity(expected_sha) != frontend_build:
        raise ReleaseGuardError(
            "frontend source or build changed during prepublish verification"
        )
    _assert_active_lease_unchanged(lease)
    return {
        "ready_to_publish": True,
        "git_sha": expected_sha,
        "release_id": expected_release_id,
        "protocol_version": PROTOCOL_VERSION,
    }


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ReleaseGuardError(
            f"production verification refused redirect: {req.full_url} -> {newurl}"
        )


def _urlopen_no_redirect(request: urllib.request.Request, timeout: int):
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def _validated_production_origin(base_url: str) -> str:
    raw = str(base_url or "").strip()
    parsed = urllib.parse.urlsplit(raw)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "mezansalla.com"
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
    ):
        raise ReleaseGuardError(
            f"verify origin must be exactly {PRODUCTION_ORIGIN}"
        )
    return PRODUCTION_ORIGIN


def _release_check_url(
    origin: str,
    relative_path: str,
    *,
    cache_bust: bool,
) -> str:
    if origin != PRODUCTION_ORIGIN:
        raise ReleaseGuardError("production verification origin is not pinned")
    relative_path = str(relative_path or "")
    parsed_path = urllib.parse.urlsplit(relative_path)
    path_parts = Path(parsed_path.path).parts
    if (
        not parsed_path.path.startswith("/")
        or parsed_path.scheme
        or parsed_path.netloc
        or parsed_path.query
        or parsed_path.fragment
        or "\\" in relative_path
        or ".." in path_parts
    ):
        raise ReleaseGuardError(
            f"production verification path is invalid: {relative_path}"
        )
    query = ""
    if cache_bust:
        query = urllib.parse.urlencode([
            ("release_check", secrets.token_hex(16)),
        ])
        if len(urllib.parse.parse_qsl(query, keep_blank_values=True)) != 1:
            raise ReleaseGuardError("release check query is invalid")
    url = urllib.parse.urlunsplit(("https", "mezansalla.com", parsed_path.path, query, ""))
    parsed_url = urllib.parse.urlsplit(url)
    release_keys = [
        name for name, _value in urllib.parse.parse_qsl(
            parsed_url.query, keep_blank_values=True
        ) if name == "release_check"
    ]
    if len(release_keys) != (1 if cache_bust else 0):
        raise ReleaseGuardError("release_check query must occur exactly once")
    return url


def _response_headers(response: Any) -> dict[str, str]:
    return {
        str(name).lower(): str(value).strip()
        for name, value in response.headers.items()
    }


def _fetch_production(
    origin: str,
    relative_path: str,
    *,
    cache_bust: bool,
    accept: str,
    timeout: int,
) -> dict[str, Any]:
    url = _release_check_url(
        origin, relative_path, cache_bust=cache_bust
    )
    headers = {
        "Accept": accept,
        "Accept-Encoding": "identity",
        "User-Agent": "Mozilla/5.0 (compatible; MezanReleaseGuard/1.0)",
    }
    if cache_bust:
        headers["Cache-Control"] = "no-cache"
    request = urllib.request.Request(url, headers=headers)
    try:
        with _urlopen_no_redirect(request, timeout=timeout) as response:
            status = int(response.getcode())
            final_url = str(response.geturl())
            body = response.read()
            response_headers = _response_headers(response)
    except ReleaseGuardError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        raise ReleaseGuardError(
            f"production check failed for {relative_path}: {exc}"
        ) from exc
    if status != 200:
        raise ReleaseGuardError(
            f"production check returned HTTP {status}: {relative_path}"
        )
    if final_url != url:
        raise ReleaseGuardError(
            f"production check changed URL: expected={url} actual={final_url}"
        )
    return {
        "url": url,
        "body": body,
        "headers": response_headers,
    }


def _health_payload(origin: str) -> dict[str, Any]:
    try:
        response = _fetch_production(
            origin,
            "/api/health",
            cache_bust=True,
            accept="application/json",
            timeout=20,
        )
        return json.loads(response["body"].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseGuardError(f"production health check failed: {exc}") from exc


def _validated_public_record(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseGuardError(f"prepared {label} proof is invalid")
    relative = str(value.get("path") or "")
    pure = Path(relative)
    size = value.get("bytes")
    digest = str(value.get("sha256") or "")
    if (
        not relative
        or relative.startswith("/")
        or "\\" in relative
        or ".." in pure.parts
        or pure.as_posix() != relative
    ):
        raise ReleaseGuardError(f"prepared {label} path is invalid: {relative}")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ReleaseGuardError(f"prepared {label} byte count is invalid: {relative}")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ReleaseGuardError(f"prepared {label} SHA is invalid: {relative}")
    return {"path": relative, "bytes": size, "sha256": digest}


def _assert_public_bytes(
    response: dict[str, Any],
    *,
    expected: dict[str, Any],
    label: str,
) -> None:
    content = response["body"]
    actual_sha256 = hashlib.sha256(content).hexdigest()
    if actual_sha256 != expected["sha256"]:
        raise ReleaseGuardError(
            "deployed frontend SHA mismatch: "
            f"path={label} expected={expected['sha256']} actual={actual_sha256}"
        )
    if len(content) != expected["bytes"]:
        raise ReleaseGuardError(
            "deployed frontend byte count mismatch: "
            f"path={label} expected={expected['bytes']} actual={len(content)}"
        )


def _assert_html_shell_headers(
    response: dict[str, Any], relative_path: str
) -> dict[str, Any]:
    headers = response["headers"]
    content_type = headers.get("content-type", "").lower()
    if not content_type.startswith("text/html"):
        raise ReleaseGuardError(
            f"production SPA shell content type is invalid: {relative_path}"
        )
    cache_control = headers.get("cache-control", "")
    directives = {
        part.strip().lower() for part in cache_control.split(",") if part.strip()
    }
    if not {"no-cache", "no-store", "must-revalidate"}.issubset(directives):
        raise ReleaseGuardError(
            "production SPA shell cache policy must include "
            f"no-cache, no-store, must-revalidate: {relative_path}"
        )
    if "immutable" in directives:
        raise ReleaseGuardError(
            f"production SPA shell must not be immutable: {relative_path}"
        )
    for directive in directives:
        if directive.startswith(("max-age=", "s-maxage=")):
            try:
                max_age = int(directive.split("=", 1)[1].strip('"'))
            except ValueError as exc:
                raise ReleaseGuardError(
                    f"production SPA shell cache age is invalid: {relative_path}"
                ) from exc
            if max_age > 0:
                raise ReleaseGuardError(
                    f"production SPA shell cache age is positive: {relative_path}"
                )
    age = headers.get("age")
    if age is not None:
        try:
            if int(age) != 0:
                raise ReleaseGuardError(
                    f"production SPA shell Age is non-zero: {relative_path}"
                )
        except ValueError as exc:
            raise ReleaseGuardError(
                f"production SPA shell Age is invalid: {relative_path}"
            ) from exc
    cf_cache_status = headers.get("cf-cache-status", "").upper()
    if cf_cache_status in {"HIT", "STALE", "UPDATING"}:
        raise ReleaseGuardError(
            "production SPA shell was served from an unsafe Cloudflare cache "
            f"state: {relative_path}={cf_cache_status}"
        )
    return {
        "path": relative_path,
        "cache_control": cache_control,
        "etag": headers.get("etag"),
        "age": age,
        "cf_cache_status": headers.get("cf-cache-status"),
        "content_type": headers.get("content-type"),
    }


def _verify_public_frontend(
    origin: str,
    expected: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(expected, dict):
        raise ReleaseGuardError("prepared frontend build proof is missing")
    index = _validated_public_record(expected.get("index"), "frontend index")
    if index["path"] != "index.html":
        raise ReleaseGuardError("prepared frontend index path is invalid")
    build_meta = _validated_public_record(
        expected.get("build_meta"), "frontend build metadata"
    )
    if build_meta["path"] != "build-meta.json":
        raise ReleaseGuardError("prepared frontend build metadata path is invalid")
    raw_public_files = expected.get("public_files")
    if not isinstance(raw_public_files, list) or not raw_public_files:
        raise ReleaseGuardError("prepared frontend public file proof is missing")
    public_files = [
        _validated_public_record(value, "frontend public file")
        for value in raw_public_files
    ]
    paths = [record["path"] for record in public_files]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ReleaseGuardError(
            "prepared frontend public file paths must be sorted and unique"
        )
    if index not in public_files:
        raise ReleaseGuardError(
            "prepared frontend index is missing from public file proof"
        )

    records_by_request_path = {
        "/build-meta.json": build_meta,
        **{"/" + record["path"]: record for record in public_files},
        "/": index,
        SPA_SHELL_PATH: index,
    }
    checked_requests = []
    shell_cache = []
    for relative_path, record in records_by_request_path.items():
        for cache_bust in (False, True):
            response = _fetch_production(
                origin,
                relative_path,
                cache_bust=cache_bust,
                accept="*/*",
                timeout=30,
            )
            label = relative_path + ("?release_check" if cache_bust else "")
            _assert_public_bytes(response, expected=record, label=label)
            checked_requests.append(label)
            if not cache_bust and relative_path in {
                "/",
                "/index.html",
                SPA_SHELL_PATH,
            }:
                shell_cache.append(
                    _assert_html_shell_headers(response, relative_path)
                )
    return {
        "artifact_tree_sha256": expected.get("artifact_tree_sha256"),
        "checked_requests": checked_requests,
        "shell_cache": shell_cache,
    }


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
    """Validate the exact v3 lease identity before any release mutation."""
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
    if not isinstance(lease.get("frontend_build"), dict):
        raise ReleaseGuardError("prepared frontend build proof is missing")
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
    origin = _validated_production_origin(base_url)
    expected_sha, expected_release_id = _validated_release_lease(lease)
    identity_fields = (
        "release_id",
        "git_sha",
        "branch",
        "prepared_at",
        "protocol_version",
        "critical_file_hashes",
        "frontend_build",
    )
    expected_identity = {field: lease.get(field) for field in identity_fields}
    expected_identity["release_id"] = expected_release_id
    _, prepared_at = _aware_timestamp(
        expected_identity["prepared_at"], "prepared release timestamp"
    )
    observations = []
    for attempt in range(3):
        health = _health_payload(origin)
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
        if release.get("frontend_build_verified") is not True:
            raise ReleaseGuardError("production frontend build proof does not match")
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
        frontend_observation = _verify_public_frontend(
            origin,
            expected_identity["frontend_build"],
        )
        observations.append({
            "git_sha": actual_sha,
            "boot_started_at": boot_started_at,
            "frontend": frontend_observation,
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
        "url": origin,
        "checks": len(observations),
        "boot_started_at": observations[0]["boot_started_at"],
        "boot_started_at_observations": boot_started_at_observations,
        "frontend_artifact_tree_sha256": expected_identity[
            "frontend_build"
        ]["artifact_tree_sha256"],
        "frontend_checked_requests": observations[0]["frontend"][
            "checked_requests"
        ],
        "frontend_shell_cache": observations[0]["frontend"]["shell_cache"],
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
    verify_parser.add_argument("--url", default=PRODUCTION_ORIGIN)
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
