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
import stat
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


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from frontend_build_identity import (  # noqa: E402
    CLIENT_ENV_ALLOWLIST,
    FrontendBuildIdentityError,
    RETIREMENT_SERVICE_WORKER_BYTES,
    RETIREMENT_SERVICE_WORKER_SHA256,
    read_frontend_build_identity,
    read_frontend_reproducibility_proof,
    validate_frontend_reproducibility_proof,
)

try:  # noqa: E402 - backend is intentionally added to sys.path above.
    from release_protocol_v5 import (
        CRITICAL_FILES as RUNTIME_CRITICAL_FILES,
        exact_json_equal,
        validate_runtime_release_identity,
    )
except ImportError:  # The v5 module may be absent on an older checked-out guard.
    validate_runtime_release_identity = None
    exact_json_equal = None
    RUNTIME_CRITICAL_FILES = (
        "server.py",
        "integrations/qoyod_manual/routes.py",
        "integrations/qoyod_manual/send.py",
    )


PRODUCTION_BRANCH = "hotfix/prod-snap-meta-final"
PRODUCTION_ORIGIN = "https://mezansalla.com"
SPA_SHELL_PATH = "/snapchat-accounts"
STANDARD_SERVICE_WORKER_PATHS = ("/sw.js", "/service-worker.js")
PROTOCOL_VERSION = 5
BOOT_CLOCK_SKEW = timedelta(minutes=5)
GIT_DIR = REPO_ROOT / ".git"
LEASE_DIR = GIT_DIR / "mezan-production-release.lock"
LEASE_PATH = LEASE_DIR / "lease.json"
IDENTITY_PATH = REPO_ROOT / "backend" / "release_identity.json"
RELEASE_INTENT_RELATIVE_PATH = "release/release-intent-v5.json"
RELEASE_INTENT_PATH = REPO_ROOT / RELEASE_INTENT_RELATIVE_PATH
FRONTEND_BUILD_META_PATH = REPO_ROOT / "frontend" / "build" / "build-meta.json"
CRITICAL_FILES = tuple(
    f"backend/{relative}" for relative in RUNTIME_CRITICAL_FILES
)
_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_RELEASE_ID_V5 = re.compile(r"^rg5-[0-9a-f]{64}$")
EXPECTED_CLIENT_ENVIRONMENT = {
    "REACT_APP_BACKEND_URL": "https://mezansalla.com",
}
_RELEASE_INTENT_KEYS = frozenset({
    "schema_version",
    "kind",
    "protocol_version",
    "source_git_sha",
    "branch",
    "frontend_source",
    "client_environment",
    "frontend_build",
    "frontend_reproducibility",
    "critical_file_hashes",
    "runtime_identity",
})


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


def _frontend_reproducibility_proof(
    frontend_build: dict[str, Any],
) -> dict[str, Any]:
    try:
        return read_frontend_reproducibility_proof(
            frontend_build=frontend_build
        )
    except FrontendBuildIdentityError as exc:
        raise ReleaseGuardError(
            f"frontend reproducibility proof failed: {exc}"
        ) from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ReleaseGuardError(f"cannot read {path}: {exc}") from exc


def _validated_runtime_identity(payload: Any) -> dict[str, Any]:
    """Validate the deterministic identity materialized by the build adapter."""
    if validate_runtime_release_identity is None:
        raise ReleaseGuardError(
            "release protocol v5 validator is unavailable; update the workspace"
        )
    if not isinstance(payload, dict):
        raise ReleaseGuardError("runtime release identity is not a JSON object")
    try:
        normalized = validate_runtime_release_identity(
            payload,
            backend_root=BACKEND_ROOT,
        )
    except Exception as exc:
        raise ReleaseGuardError(
            f"runtime release identity validation failed: {exc}"
        ) from exc
    if not isinstance(normalized, dict):
        raise ReleaseGuardError(
            "runtime release identity validator returned an invalid payload"
        )
    return normalized


def _read_runtime_identity() -> dict[str, Any]:
    return _validated_runtime_identity(_read_json(IDENTITY_PATH))


def _read_reviewed_runtime_identity() -> dict[str, Any]:
    intent = _read_json(RELEASE_INTENT_PATH)
    if set(intent) != _RELEASE_INTENT_KEYS:
        raise ReleaseGuardError("reviewed release intent fields are invalid")
    if (
        type(intent.get("schema_version")) is not int
        or intent.get("schema_version") != 1
        or intent.get("kind") != "mezan_emergent_release_intent_v1"
        or type(intent.get("protocol_version")) is not int
        or intent.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise ReleaseGuardError("reviewed release intent contract is invalid")
    identity = _validated_runtime_identity(intent.get("runtime_identity"))
    client_environment = _validated_reviewed_client_environment(
        intent.get("client_environment")
    )
    mirrored = {
        "source_git_sha": identity["source_git_sha"],
        "branch": identity["branch"],
        "frontend_build": identity["frontend_build"],
        "frontend_reproducibility": identity["frontend_reproducibility"],
        "critical_file_hashes": identity["critical_file_hashes"],
    }
    mismatches = [
        field for field, expected in mirrored.items()
        if not exact_json_equal(intent.get(field), expected)
    ]
    if mismatches:
        raise ReleaseGuardError(
            "reviewed release intent does not match runtime identity: "
            + ", ".join(mismatches)
        )
    proof_values = (
        (identity.get("frontend_build") or {})
        .get("environment", {})
        .get("values")
    )
    expected_proof_values = {
        name: {
            "present": record["present"],
            "sha256": (
                hashlib.sha256(record["value"].encode("utf-8")).hexdigest()
                if record["present"]
                else None
            ),
        }
        for name, record in client_environment.items()
    }
    if not exact_json_equal(proof_values, expected_proof_values):
        raise ReleaseGuardError(
            "reviewed client environment does not match frontend build proof"
        )
    retained_metadata = _read_json(FRONTEND_BUILD_META_PATH)
    if not exact_json_equal(
        retained_metadata.get("source"),
        intent.get("frontend_source"),
    ):
        raise ReleaseGuardError(
            "reviewed frontend source does not match retained build metadata"
        )
    return identity


def _validated_reviewed_client_environment(
    value: Any,
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(CLIENT_ENV_ALLOWLIST):
        raise ReleaseGuardError(
            "reviewed client environment does not match the allowlist"
        )
    result: dict[str, dict[str, Any]] = {}
    for name in CLIENT_ENV_ALLOWLIST:
        record = value.get(name)
        if not isinstance(record, dict) or set(record) != {"present", "value"}:
            raise ReleaseGuardError(
                f"reviewed client environment record is invalid: {name}"
            )
        present = record.get("present")
        raw = record.get("value")
        if not isinstance(present, bool):
            raise ReleaseGuardError(
                f"reviewed client environment presence is invalid: {name}"
            )
        if not present:
            raise ReleaseGuardError(
                f"required reviewed client environment is absent: {name}"
            )
        if not isinstance(raw, str) or not raw or len(raw) > 2048:
            raise ReleaseGuardError(
                f"reviewed client environment value is invalid: {name}"
            )
        if raw != EXPECTED_CLIENT_ENVIRONMENT[name]:
            raise ReleaseGuardError(
                f"reviewed client environment value is not approved: {name}"
            )
        parsed = urllib.parse.urlsplit(raw)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ReleaseGuardError(
                f"reviewed client environment must be a public HTTP origin: {name}"
            )
        result[name] = {"present": True, "value": raw}
    return result


def _runtime_identity_from_lease(lease: dict[str, Any]) -> dict[str, Any]:
    identity = lease.get("runtime_identity")
    if not isinstance(identity, dict):
        raise ReleaseGuardError("prepared runtime release identity is missing")
    normalized = _validated_runtime_identity(identity)
    expected_aliases = {
        "release_id": normalized["release_id"],
        "source_git_sha": normalized["source_git_sha"],
        "git_sha": normalized["source_git_sha"],
        "branch": normalized["branch"],
        "protocol_version": normalized["protocol_version"],
        "critical_file_hashes": normalized["critical_file_hashes"],
        "frontend_build": normalized["frontend_build"],
        "frontend_reproducibility": normalized[
            "frontend_reproducibility"
        ],
    }
    mismatches = [
        key for key, value in expected_aliases.items()
        if not exact_json_equal(lease.get(key), value)
    ]
    if mismatches:
        raise ReleaseGuardError(
            "prepared lease does not match runtime release identity: "
            + ", ".join(mismatches)
        )
    return normalized


def _assert_local_identity_binding(
    identity: dict[str, Any],
    *,
    expected_git_sha: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    if identity.get("source_git_sha") != expected_git_sha:
        raise ReleaseGuardError(
            "runtime release identity source SHA does not match reviewed "
            f"HEAD: identity={identity.get('source_git_sha')} "
            f"head={expected_git_sha}"
        )
    frontend_build = _frontend_build_identity(expected_git_sha)
    frontend_reproducibility = _frontend_reproducibility_proof(
        frontend_build
    )
    critical_hashes = _critical_hashes()
    if not all((
        exact_json_equal(identity.get("frontend_build"), frontend_build),
        exact_json_equal(
            identity.get("frontend_reproducibility"),
            frontend_reproducibility,
        ),
        exact_json_equal(identity.get("critical_file_hashes"), critical_hashes),
    )):
        raise ReleaseGuardError(
            "runtime release identity, critical files, or frontend build "
            "does not match the local governed package"
        )
    return frontend_build, frontend_reproducibility, critical_hashes


def _assert_reviewed_source_relation(
    *,
    source_git_sha: str,
    deployment_git_sha: str,
) -> None:
    """Allow the deployment commit to differ only by the reviewed v5 intent."""
    if source_git_sha == deployment_git_sha:
        raise ReleaseGuardError(
            "protocol v5 requires a separate tracked intent-only deployment commit"
        )
    try:
        _run_git(
            "merge-base",
            "--is-ancestor",
            source_git_sha,
            deployment_git_sha,
        )
    except ReleaseGuardError as exc:
        raise ReleaseGuardError(
            "runtime identity source SHA is not an ancestor of deployment HEAD"
        ) from exc
    changed = {
        row.strip()
        for row in _run_git(
            "diff", "--name-only", source_git_sha, deployment_git_sha
        ).splitlines()
        if row.strip()
    }
    expected_change = {RELEASE_INTENT_RELATIVE_PATH}
    if changed != expected_change:
        raise ReleaseGuardError(
            "deployment HEAD must differ from runtime identity source by the "
            "reviewed release intent only; found: "
            + (", ".join(sorted(changed)) or "no tracked change")
        )
    tracked = _run_git(
        "ls-files", "--error-unmatch", "--", RELEASE_INTENT_RELATIVE_PATH
    )
    if tracked.strip() != RELEASE_INTENT_RELATIVE_PATH:
        raise ReleaseGuardError("reviewed release intent is not tracked")
    deployment_blob = _run_git(
        "rev-parse", f"{deployment_git_sha}:{RELEASE_INTENT_RELATIVE_PATH}"
    )
    local_blob = _run_git("hash-object", RELEASE_INTENT_RELATIVE_PATH)
    if deployment_blob != local_blob:
        raise ReleaseGuardError(
            "reviewed release intent bytes differ from deployment commit"
        )


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


def _create_or_recover_lease_dir() -> None:
    """Create the lease dir or recover only an interrupted pre-lease write."""
    try:
        LEASE_DIR.mkdir(mode=0o700)
        return
    except FileExistsError:
        pass
    try:
        info = LEASE_DIR.lstat()
    except OSError as exc:
        raise ReleaseGuardError(
            f"cannot inspect existing production release lease directory: {exc}"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ReleaseGuardError(
            "production release lease path is not a real directory"
        )
    if LEASE_PATH.exists():
        lease = _read_json(LEASE_PATH)
        raise ReleaseGuardError(
            "another production release is active: "
            + json.dumps(lease, ensure_ascii=False)
        )
    allowed = {LEASE_PATH.name + ".tmp"}
    entries = list(LEASE_DIR.iterdir())
    if any(
        entry.name not in allowed
        or entry.is_symlink()
        or not entry.is_file()
        for entry in entries
    ):
        raise ReleaseGuardError(
            "incomplete production release lease directory contains "
            "unexpected entries"
        )
    shutil.rmtree(LEASE_DIR)
    LEASE_DIR.mkdir(mode=0o700)


def _prepare_locked(actor: str) -> dict[str, Any]:
    _create_or_recover_lease_dir()

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
        runtime_identity = _read_runtime_identity()
        reviewed_identity = _read_reviewed_runtime_identity()
        if not exact_json_equal(runtime_identity, reviewed_identity):
            raise ReleaseGuardError(
                "materialized runtime identity does not match reviewed release intent"
            )
        if runtime_identity.get("branch") != branch:
            raise ReleaseGuardError(
                "runtime release identity branch does not match production branch"
            )
        source_git_sha = runtime_identity["source_git_sha"]
        _assert_reviewed_source_relation(
            source_git_sha=source_git_sha,
            deployment_git_sha=local_sha,
        )
        frontend_build, frontend_reproducibility, critical_hashes = (
            _assert_local_identity_binding(
                runtime_identity,
                expected_git_sha=source_git_sha,
            )
        )
        payload = {
            "release_id": runtime_identity["release_id"],
            "source_git_sha": source_git_sha,
            "git_sha": source_git_sha,
            "deployment_git_sha": local_sha,
            "branch": branch,
            "actor": actor,
            "prepared_at": _utc_now(),
            "protocol_version": PROTOCOL_VERSION,
            "critical_file_hashes": critical_hashes,
            "frontend_build": frontend_build,
            "frontend_reproducibility": frontend_reproducibility,
            "runtime_identity": runtime_identity,
        }
        if (
            _run_git("rev-parse", "HEAD") != local_sha
            or not exact_json_equal(_read_runtime_identity(), runtime_identity)
            or not exact_json_equal(
                _read_reviewed_runtime_identity(),
                reviewed_identity,
            )
            or not exact_json_equal(
                _frontend_build_identity(source_git_sha),
                frontend_build,
            )
            or not exact_json_equal(
                _frontend_reproducibility_proof(frontend_build),
                frontend_reproducibility,
            )
            or not exact_json_equal(_critical_hashes(), critical_hashes)
        ):
            raise ReleaseGuardError(
                "frontend source, critical files, or build changed while "
                "preparing release"
            )
        _atomic_json(LEASE_PATH, payload)
        return payload
    except Exception:
        shutil.rmtree(LEASE_DIR, ignore_errors=True)
        raise


def status() -> dict[str, Any]:
    with _release_operation_lock():
        if not LEASE_PATH.exists():
            incomplete = LEASE_DIR.exists()
            return {
                "active": False,
                **(
                    {"incomplete_lease_directory": True}
                    if incomplete
                    else {}
                ),
            }
        return {"active": True, **_read_json(LEASE_PATH)}


def prepublish() -> dict[str, Any]:
    with _release_operation_lock():
        return _prepublish_locked()


def _prepublish_locked() -> dict[str, Any]:
    if not LEASE_PATH.exists():
        raise ReleaseGuardError("no prepared production release lease")
    lease = _read_json(LEASE_PATH)
    expected_source_sha, expected_deployment_sha, expected_release_id = (
        _validated_release_lease(lease)
    )
    branch = _run_git("branch", "--show-current")
    if branch != PRODUCTION_BRANCH:
        raise ReleaseGuardError(f"wrong branch {branch!r}")
    dirty = _run_git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise ReleaseGuardError("tracked worktree changed after prepare:\n" + dirty)
    _run_git("fetch", "origin", PRODUCTION_BRANCH)
    local_sha = _run_git("rev-parse", "HEAD")
    remote_sha = _run_git("rev-parse", f"origin/{PRODUCTION_BRANCH}")
    if not (local_sha == remote_sha == expected_deployment_sha):
        raise ReleaseGuardError(
            "release SHA changed after prepare: "
            f"lease={expected_deployment_sha} local={local_sha} remote={remote_sha}"
        )
    _assert_reviewed_source_relation(
        source_git_sha=expected_source_sha,
        deployment_git_sha=expected_deployment_sha,
    )
    prepared_identity = _runtime_identity_from_lease(lease)
    runtime_identity = _read_runtime_identity()
    reviewed_identity = _read_reviewed_runtime_identity()
    frontend_build, frontend_reproducibility, critical_hashes = (
        _assert_local_identity_binding(
            runtime_identity,
            expected_git_sha=expected_source_sha,
        )
    )
    if not (
        exact_json_equal(runtime_identity, prepared_identity)
        and exact_json_equal(reviewed_identity, prepared_identity)
    ):
        raise ReleaseGuardError(
            "runtime release identity or reviewed intent changed after prepare"
        )
    if not all((
        exact_json_equal(_read_runtime_identity(), runtime_identity),
        exact_json_equal(_read_reviewed_runtime_identity(), reviewed_identity),
        exact_json_equal(
            _frontend_build_identity(expected_source_sha),
            frontend_build,
        ),
        exact_json_equal(
            _frontend_reproducibility_proof(frontend_build),
            frontend_reproducibility,
        ),
        exact_json_equal(_critical_hashes(), critical_hashes),
    )):
        raise ReleaseGuardError(
            "frontend source, critical files, or build changed during "
            "prepublish verification"
        )
    _assert_active_lease_unchanged(lease)
    return {
        "ready_to_publish": True,
        "source_git_sha": expected_source_sha,
        "git_sha": expected_source_sha,
        "deployment_git_sha": expected_deployment_sha,
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
    url = urllib.parse.urlunsplit(
        ("https", "mezansalla.com", parsed_path.path, query, "")
    )
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
    allowed_statuses: frozenset[int] = frozenset({200}),
    service_worker_script: bool = False,
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
    if service_worker_script:
        headers["Service-Worker"] = "script"
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with _urlopen_no_redirect(request, timeout=timeout) as response:
            status = int(response.getcode())
            final_url = str(response.geturl())
            body = response.read()
            response_headers = _response_headers(response)
    except ReleaseGuardError:
        raise
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        final_url = str(exc.geturl())
        body = exc.read()
        response_headers = _response_headers(exc)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ReleaseGuardError(
            f"production check failed for {relative_path}: {exc}"
        ) from exc
    if status not in allowed_statuses:
        raise ReleaseGuardError(
            f"production check returned HTTP {status}: {relative_path}"
        )
    if final_url != url:
        raise ReleaseGuardError(
            f"production check changed URL: expected={url} actual={final_url}"
        )
    return {
        "url": url,
        "status": status,
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


def _assert_build_meta_response(
    response: dict[str, Any],
    *,
    label: str,
) -> None:
    if response.get("status") != 200:
        raise ReleaseGuardError(
            f"production frontend build metadata returned non-200: {label}"
        )
    content_type = str(
        (response.get("headers") or {}).get("content-type") or ""
    ).lower()
    media_type = content_type.split(";", 1)[0].strip()
    if media_type != "application/json":
        raise ReleaseGuardError(
            "production frontend build metadata MIME is invalid: "
            f"{label}={content_type or 'missing'}"
        )
    try:
        parsed = json.loads(response["body"].decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseGuardError(
            f"production frontend build metadata is invalid JSON: {label}"
        ) from exc
    if not isinstance(parsed, dict):
        raise ReleaseGuardError(
            f"production frontend build metadata is not a JSON object: {label}"
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


def _assert_service_worker_headers(
    response: dict[str, Any], relative_path: str
) -> dict[str, Any]:
    headers = response["headers"]
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {
        "application/javascript",
        "application/x-javascript",
        "text/javascript",
    }:
        raise ReleaseGuardError(
            f"production service worker MIME is invalid: {relative_path}"
        )
    if headers.get("x-content-type-options", "").lower() != "nosniff":
        raise ReleaseGuardError(
            "production service worker must declare X-Content-Type-Options "
            f"nosniff: {relative_path}"
        )
    cache_control = headers.get("cache-control", "")
    directives = {
        part.strip().lower() for part in cache_control.split(",") if part.strip()
    }
    if not {"no-cache", "no-store", "must-revalidate"}.issubset(
        directives
    ):
        raise ReleaseGuardError(
            "production service worker cache policy must include no-cache, "
            f"no-store, must-revalidate: {relative_path}"
        )
    if "immutable" in directives:
        raise ReleaseGuardError(
            f"production service worker must not be immutable: {relative_path}"
        )
    for directive in directives:
        if directive.startswith(("max-age=", "s-maxage=")):
            try:
                max_age = int(directive.split("=", 1)[1].strip('"'))
            except ValueError as exc:
                raise ReleaseGuardError(
                    f"production service worker cache age is invalid: {relative_path}"
                ) from exc
            if max_age > 0:
                raise ReleaseGuardError(
                    f"production service worker cache age is positive: {relative_path}"
                )
    if "max-age=0" not in directives:
        raise ReleaseGuardError(
            f"production service worker must declare max-age=0: {relative_path}"
        )
    age = headers.get("age")
    if age is not None:
        try:
            if int(age) != 0:
                raise ReleaseGuardError(
                    f"production service worker Age is non-zero: {relative_path}"
                )
        except ValueError as exc:
            raise ReleaseGuardError(
                f"production service worker Age is invalid: {relative_path}"
            ) from exc
    cf_cache_status = headers.get("cf-cache-status", "").upper()
    if cf_cache_status in {"HIT", "STALE", "UPDATING"}:
        raise ReleaseGuardError(
            "production service worker was served from an unsafe Cloudflare "
            f"cache state: {relative_path}={cf_cache_status}"
        )
    return {
        "path": relative_path,
        "state": "retirement_payload_present",
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
    for relative_path in STANDARD_SERVICE_WORKER_PATHS:
        record = records_by_request_path.get(relative_path)
        if (
            record is None
            or record["bytes"] != RETIREMENT_SERVICE_WORKER_BYTES
            or record["sha256"] != RETIREMENT_SERVICE_WORKER_SHA256
        ):
            raise ReleaseGuardError(
                "prepared frontend retirement service worker is missing or "
                f"invalid: {relative_path}"
            )
    checked_requests = []
    shell_cache = []
    service_workers = []
    for relative_path, record in records_by_request_path.items():
        accept_values = (
            ("application/json", "*/*")
            if relative_path == "/build-meta.json"
            else ("*/*",)
        )
        for accept in accept_values:
            for cache_bust in (False, True):
                response = _fetch_production(
                    origin,
                    relative_path,
                    cache_bust=cache_bust,
                    accept=accept,
                    timeout=30,
                    service_worker_script=(
                        relative_path in STANDARD_SERVICE_WORKER_PATHS
                    ),
                )
                label = relative_path + (
                    "?release_check" if cache_bust else ""
                )
                if relative_path == "/build-meta.json":
                    label += f" [accept={accept}]"
                    _assert_build_meta_response(
                        response,
                        label=label,
                    )
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
                if relative_path in STANDARD_SERVICE_WORKER_PATHS:
                    service_worker = _assert_service_worker_headers(
                        response, relative_path
                    )
                    if not cache_bust:
                        service_workers.append(service_worker)

    return {
        "artifact_tree_sha256": expected.get("artifact_tree_sha256"),
        "checked_requests": checked_requests,
        "shell_cache": shell_cache,
        "service_workers": service_workers,
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


def _release_id_v5(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseGuardError(f"{label} is invalid")
    raw = value.strip()
    if not _RELEASE_ID_V5.fullmatch(raw):
        raise ReleaseGuardError(f"{label} is invalid")
    return raw


def _validated_release_lease(
    lease: dict[str, Any],
) -> tuple[str, str, str]:
    """Validate the exact v5 lease identity before any release mutation."""
    protocol_version = lease.get("protocol_version")
    if type(protocol_version) is not int:
        raise ReleaseGuardError("prepared release protocol is invalid")
    if protocol_version != PROTOCOL_VERSION:
        raise ReleaseGuardError(
            "prepared release protocol is unsupported; abort the existing "
            "lease with its original guard, update the workspace, then "
            f"prepare again with protocol v{PROTOCOL_VERSION}"
        )
    expected_sha = lease.get("source_git_sha")
    if not isinstance(expected_sha, str) or not _FULL_GIT_SHA.fullmatch(
        expected_sha
    ):
        raise ReleaseGuardError("prepared release SHA is invalid")
    if lease.get("git_sha") != expected_sha:
        raise ReleaseGuardError(
            "prepared release git_sha alias does not match source_git_sha"
        )
    deployment_sha = lease.get("deployment_git_sha")
    if not isinstance(deployment_sha, str) or not _FULL_GIT_SHA.fullmatch(
        deployment_sha
    ):
        raise ReleaseGuardError("prepared deployment SHA is invalid")
    expected_release_id = _release_id_v5(
        lease.get("release_id"), "prepared release id"
    )
    if not isinstance(lease.get("frontend_build"), dict):
        raise ReleaseGuardError("prepared frontend build proof is missing")
    if not isinstance(lease.get("frontend_reproducibility"), dict):
        raise ReleaseGuardError(
            "prepared frontend reproducibility proof is missing"
        )
    runtime_identity = _runtime_identity_from_lease(lease)
    if (
        runtime_identity["source_git_sha"] != expected_sha
        or runtime_identity["release_id"] != expected_release_id
    ):
        raise ReleaseGuardError(
            "prepared lease source or release id does not match runtime identity"
        )
    try:
        validate_frontend_reproducibility_proof(
            frontend_build=lease["frontend_build"],
            proof=lease["frontend_reproducibility"],
        )
    except FrontendBuildIdentityError as exc:
        raise ReleaseGuardError(
            f"prepared frontend reproducibility proof is invalid: {exc}"
        ) from exc
    return expected_sha, deployment_sha, expected_release_id


def _assert_active_lease_unchanged(
    expected_lease: dict[str, Any],
) -> tuple[str, str, str]:
    """Re-read and fence the active lease immediately before final action."""
    if not LEASE_PATH.exists():
        raise ReleaseGuardError("production release lease disappeared")
    active_lease = _read_json(LEASE_PATH)
    (
        expected_source_sha,
        expected_deployment_sha,
        expected_release_id,
    ) = _validated_release_lease(expected_lease)
    active_source_sha, active_deployment_sha, active_release_id = (
        _validated_release_lease(active_lease)
    )
    if (
        active_source_sha != expected_source_sha
        or active_deployment_sha != expected_deployment_sha
        or active_release_id != expected_release_id
        or not exact_json_equal(active_lease, expected_lease)
    ):
        raise ReleaseGuardError(
            "active production release lease changed during operation: "
            f"expected={expected_release_id} active={active_release_id}"
        )
    return active_source_sha, active_deployment_sha, active_release_id


def _remove_active_lease(
    expected_lease: dict[str, Any],
) -> tuple[str, str, str]:
    """Delete only the exact lease observed under the operation mutex."""
    active_source_sha, active_deployment_sha, active_release_id = (
        _assert_active_lease_unchanged(expected_lease)
    )
    shutil.rmtree(LEASE_DIR)
    return active_source_sha, active_deployment_sha, active_release_id


def verify(base_url: str) -> dict[str, Any]:
    with _release_operation_lock():
        return _verify_locked(base_url)


def _verify_locked(base_url: str) -> dict[str, Any]:
    if not LEASE_PATH.exists():
        raise ReleaseGuardError("no prepared production release lease")
    lease = _read_json(LEASE_PATH)
    origin = _validated_production_origin(base_url)
    expected_sha, expected_deployment_sha, expected_release_id = (
        _validated_release_lease(lease)
    )
    expected_runtime_identity = _runtime_identity_from_lease(lease)
    expected_health_identity = {
        "identity_kind": expected_runtime_identity["kind"],
        "identity_schema_version": expected_runtime_identity[
            "schema_version"
        ],
        **{
            field: expected_runtime_identity[field]
            for field in (
                "release_id",
                "source_git_sha",
                "branch",
                "protocol_version",
                "critical_file_hashes",
                "frontend_build",
                "frontend_reproducibility",
            )
        },
    }
    _, prepared_at = _aware_timestamp(
        lease.get("prepared_at"), "prepared release timestamp"
    )
    observations = []
    for attempt in range(3):
        health = _health_payload(origin)
        release = health.get("release") or {}
        actual_sha = release.get("source_git_sha")
        if health.get("ok") is not True:
            raise ReleaseGuardError("production health is not ok")
        if release.get("verified_identity_available") is not True:
            raise ReleaseGuardError("production has no verified release identity")
        if not isinstance(actual_sha, str) or actual_sha != expected_sha:
            raise ReleaseGuardError(
                f"deployed SHA mismatch: expected={expected_sha} actual={actual_sha}"
            )
        if release.get("git_sha") != expected_sha:
            raise ReleaseGuardError(
                "deployed git_sha alias does not match source_git_sha"
            )
        if release.get("critical_file_hashes_match") is not True:
            raise ReleaseGuardError("production critical file hashes do not match")
        if release.get("frontend_build_verified") is not True:
            raise ReleaseGuardError("production frontend build proof does not match")
        identity_mismatches = [
            field
            for field, expected in expected_health_identity.items()
            if not exact_json_equal(release.get(field), expected)
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
            expected_runtime_identity["frontend_build"],
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
        "source_git_sha": actual_sha,
        "git_sha": actual_sha,
        "deployment_git_sha": expected_deployment_sha,
        "release_id": expected_release_id,
        "protocol_version": PROTOCOL_VERSION,
        "url": origin,
        "checks": len(observations),
        "boot_started_at": observations[0]["boot_started_at"],
        "boot_started_at_observations": boot_started_at_observations,
        "frontend_artifact_tree_sha256": expected_runtime_identity[
            "frontend_build"
        ]["artifact_tree_sha256"],
        "frontend_checked_requests": observations[0]["frontend"][
            "checked_requests"
        ],
        "frontend_shell_cache": observations[0]["frontend"]["shell_cache"],
        "frontend_service_workers": observations[0]["frontend"][
            "service_workers"
        ],
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
    actual_source_sha, actual_deployment_sha, actual_release_id = (
        _validated_release_lease(lease)
    )
    expected_sha = str(expected_sha or "").strip().lower()
    expected_release_id = _release_id_v5(
        expected_release_id, "expected release id"
    )
    if expected_sha != actual_deployment_sha:
        raise ReleaseGuardError(
            "lease SHA mismatch: "
            f"expected argument={expected_sha} active={actual_deployment_sha}"
        )
    if expected_release_id != actual_release_id:
        raise ReleaseGuardError(
            "lease release id mismatch: "
            f"expected argument={expected_release_id} active={actual_release_id}"
        )
    _remove_active_lease(lease)
    return {
        "aborted": True,
        "source_git_sha": actual_source_sha,
        "git_sha": actual_source_sha,
        "deployment_git_sha": actual_deployment_sha,
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
