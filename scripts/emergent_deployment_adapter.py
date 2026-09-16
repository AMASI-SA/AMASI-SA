#!/usr/bin/env python3
"""Materialize a governed release inside the Emergent Cloud Build workspace.

Emergent invokes the frontend package build with its host Node installation.
That host is allowed to start this Python adapter only.  The adapter removes
all inherited frontend outputs and dependencies, provisions the repository's
pinned release toolchain, performs a frozen install and two clean builds, and
then materializes the deterministic Backend runtime identity in the same
Cloud Build workspace.

Nothing produced by a developer's ``/app`` session is an input.  The only
handoff is the reviewed, tracked release intent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Sequence

sys.dont_write_bytecode = True


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend"
BACKEND_ROOT = REPO_ROOT / "backend"
BUILD_ROOT = FRONTEND_ROOT / "build"
PROOF_PATH = FRONTEND_ROOT / ".release" / "reproducible-build.json"
IDENTITY_PATH = BACKEND_ROOT / "release_identity.json"
INTENT_PATH = REPO_ROOT / "release" / "release-intent-v5.json"
TOOLCHAIN_SCRIPT = REPO_ROOT / "scripts" / "frontend_release_toolchain.py"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_frontend_build.py"
BACKEND_REQUIREMENTS_RELATIVE = "requirements.txt"
REVIEWED_BACKEND_REQUIREMENTS_RELATIVE = "scripts/release_backend_requirements.lock"
INTENT_SCHEMA_VERSION = 2
INTENT_KIND = "mezan_emergent_release_intent_v1"
PROTOCOL_VERSION = 5
FULL_GIT_SHA_LENGTH = 40
CLIENT_ENV_ALLOWLIST = ("REACT_APP_BACKEND_URL",)
FRONTEND_PUBLIC_PACKAGE_PATHS = frozenset({".env"})
INTENT_KEYS = frozenset({
    "schema_version",
    "kind",
    "protocol_version",
    "source_git_sha",
    "source_base_git_sha",
    "branch",
    "frontend_source",
    "backend_runtime_source",
    "release_control_source",
    "client_environment",
    "frontend_build",
    "frontend_reproducibility",
    "critical_file_hashes",
    "runtime_identity",
})
EXPECTED_CLIENT_ENVIRONMENT = {
    "REACT_APP_BACKEND_URL": "https://mezansalla.com",
}
TOOLCHAIN_HOST_ENV_ALLOWLIST = (
    "CI",
    "ALL_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TEMP",
    "TMP",
    "TMPDIR",
    "TZ",
    "XDG_CACHE_HOME",
    "all_proxy",
    "https_proxy",
    "http_proxy",
    "no_proxy",
)

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from release_protocol_v5 import (  # noqa: E402
    BACKEND_RUNTIME_EXCLUDED_PREFIXES,
    BACKEND_RUNTIME_GENERATED_PATHS,
    BACKEND_RUNTIME_SOURCE_SCOPE,
    RELEASE_CONTROL_SOURCE_SCOPE,
    backend_runtime_source_path_included,
    build_source_manifest,
    exact_json_equal,
    git_source_tree_oid,
    source_manifest_summary,
    validate_backend_runtime_source_manifest,
    validate_release_control_source_manifest,
    validate_source_manifest,
    validate_source_path,
    validated_source_git_mode,
)


class DeploymentAdapterError(RuntimeError):
    """A fail-closed Cloud Build contract violation."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git_blob_oid(value: bytes) -> str:
    header = f"blob {len(value)}\0".encode("utf-8")
    return hashlib.sha1(header + value, usedforsecurity=False).hexdigest()


def _canonical_source_tree_sha256(records: Iterable[dict[str, Any]]) -> str:
    value = "".join(
        f"{row['git_blob']}\0{row['mode']}\0{row['sha256']}\0"
        f"{row['bytes']}\0{row['path']}\n"
        for row in records
    ).encode("utf-8")
    return _sha256_bytes(value)


def _canonical_file_tree_sha256(records: Iterable[dict[str, Any]]) -> str:
    value = "".join(
        f"{row['sha256']}\0{row['bytes']}\0{row['path']}\n"
        for row in records
    ).encode("utf-8")
    return _sha256_bytes(value)


def _full_git_sha(value: Any, label: str = "source_git_sha") -> str:
    if not isinstance(value, str):
        raise DeploymentAdapterError(f"{label} must be a full lowercase Git SHA")
    candidate = value
    if len(candidate) != FULL_GIT_SHA_LENGTH or any(
        character not in "0123456789abcdef" for character in candidate
    ):
        raise DeploymentAdapterError(f"{label} must be a full lowercase Git SHA")
    return candidate


def _relative_path(value: Any, label: str) -> str:
    candidate = str(value or "")
    pure = PurePosixPath(candidate)
    if (
        not candidate
        or "\\" in candidate
        or candidate.startswith("/")
        or pure.is_absolute()
        or ".." in pure.parts
        or pure.as_posix() != candidate
    ):
        raise DeploymentAdapterError(f"{label} path is invalid: {candidate!r}")
    return candidate


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeploymentAdapterError(f"cannot read {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise DeploymentAdapterError(f"{label} must be a JSON object")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    parent_info = path.parent.lstat()
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise DeploymentAdapterError(
            f"atomic JSON parent is not a real directory: {path.parent}"
        )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_regular_source_record(
    path: Path,
    *,
    relative: str,
    label: str,
) -> tuple[dict[str, Any], bytes, os.stat_result]:
    try:
        initial = path.lstat()
    except OSError as exc:
        raise DeploymentAdapterError(f"{label} is missing: {relative}") from exc
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise DeploymentAdapterError(f"{label} must be a regular file: {relative}")
    try:
        mode = validated_source_git_mode(
            initial.st_mode,
            label=label,
            path=relative,
        )
    except ValueError as exc:
        raise DeploymentAdapterError(str(exc)) from exc

    descriptor: int | None = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != initial.st_dev
            or opened.st_ino != initial.st_ino
        ):
            raise DeploymentAdapterError(
                f"{label} changed while it was being read: {relative}"
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            content = stream.read()
    except OSError as exc:
        raise DeploymentAdapterError(f"cannot read {label}: {relative}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)

    return (
        {
            "path": relative,
            "mode": mode,
            "git_blob": _git_blob_oid(content),
            "bytes": len(content),
            "sha256": _sha256_bytes(content),
        },
        content,
        initial,
    )


def _source_record_identity(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in ("mode", "git_blob", "bytes", "sha256")}


def _manifest_file_record(
    manifest: dict[str, Any],
    relative: str,
    *,
    label: str,
) -> dict[str, Any]:
    matches = [record for record in manifest["files"] if record["path"] == relative]
    if len(matches) != 1:
        raise DeploymentAdapterError(
            f"{label} is not bound exactly once in the reviewed intent: " f"{relative}"
        )
    return matches[0]


def _atomic_replace_existing_regular_file(
    path: Path,
    payload: bytes,
    *,
    original: os.stat_result,
) -> None:
    try:
        parent = path.parent
        parent_info = parent.lstat()
    except OSError as exc:
        raise DeploymentAdapterError(
            f"Backend requirements parent is unavailable: {parent}"
        ) from exc
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        raise DeploymentAdapterError(
            f"Backend requirements parent is not a real directory: {parent}"
        )

    descriptor, temporary_name = tempfile.mkstemp(
        dir=parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        current = path.lstat()
        if (
            stat.S_ISLNK(current.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or current.st_dev != original.st_dev
            or current.st_ino != original.st_ino
        ):
            raise DeploymentAdapterError(
                "Backend requirements changed before atomic materialization"
            )
        os.replace(temporary, path)
    except OSError as exc:
        raise DeploymentAdapterError(
            f"cannot atomically materialize Backend requirements: {exc}"
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _materialize_cloud_build_backend_requirements() -> dict[str, Any]:
    """Restore only a manifest-bound platform rewrite before full validation."""
    requirements_path = BACKEND_ROOT / BACKEND_REQUIREMENTS_RELATIVE
    reviewed_path = REPO_ROOT / REVIEWED_BACKEND_REQUIREMENTS_RELATIVE
    reviewed_actual, reviewed_bytes, _ = _read_regular_source_record(
        reviewed_path,
        relative=REVIEWED_BACKEND_REQUIREMENTS_RELATIVE,
        label="reviewed Backend requirements",
    )
    requirements_actual, _, requirements_info = _read_regular_source_record(
        requirements_path,
        relative=BACKEND_REQUIREMENTS_RELATIVE,
        label="Backend requirements",
    )

    if (REPO_ROOT / ".git").exists():
        actual_identity = _source_record_identity(requirements_actual)
        reviewed_identity = _source_record_identity(reviewed_actual)
        if actual_identity != reviewed_identity:
            raise DeploymentAdapterError(
                "reviewed Backend requirements differ from " "backend/requirements.txt"
            )
        return {
            "path": f"backend/{BACKEND_REQUIREMENTS_RELATIVE}",
            "restored": False,
            "bytes": requirements_actual["bytes"],
            "sha256": requirements_actual["sha256"],
        }

    payload = _load_json(INTENT_PATH, "tracked release intent")
    source_git_sha = _full_git_sha(payload.get("source_git_sha"))
    source_base_git_sha = _full_git_sha(
        payload.get("source_base_git_sha"),
        "source_base_git_sha",
    )
    try:
        backend_manifest = validate_source_manifest(
            payload.get("backend_runtime_source"),
            expected_scope=BACKEND_RUNTIME_SOURCE_SCOPE,
            label="Backend runtime source",
        )
        control_manifest = validate_release_control_source_manifest(
            payload.get("release_control_source"),
            repo_root=REPO_ROOT,
            source_git_sha=source_git_sha,
            source_base_git_sha=source_base_git_sha,
            allow_missing_github=True,
        )
    except (ValueError, TypeError) as exc:
        raise DeploymentAdapterError(
            "cannot materialize Backend requirements from reviewed source: " f"{exc}"
        ) from exc
    if (
        backend_manifest["source_git_sha"] != source_git_sha
        or backend_manifest["source_base_git_sha"] != source_base_git_sha
    ):
        raise DeploymentAdapterError(
            "Backend requirements manifest is not bound to intent provenance"
        )

    reviewed_expected = _manifest_file_record(
        control_manifest,
        REVIEWED_BACKEND_REQUIREMENTS_RELATIVE,
        label="reviewed Backend requirements",
    )
    if reviewed_expected["mode"] != "100644":
        raise DeploymentAdapterError(
            "reviewed Backend requirements must use regular source mode"
        )
    if reviewed_actual != reviewed_expected:
        raise DeploymentAdapterError(
            "reviewed Backend requirements changed after manifest validation"
        )

    restored = (
        _source_record_identity(requirements_actual)
        != _source_record_identity(reviewed_expected)
    )
    if restored:
        _atomic_replace_existing_regular_file(
            requirements_path,
            reviewed_bytes,
            original=requirements_info,
        )
        requirements_actual, _, _ = _read_regular_source_record(
            requirements_path,
            relative=BACKEND_REQUIREMENTS_RELATIVE,
            label="Backend requirements",
        )
        if (
            _source_record_identity(requirements_actual)
            != _source_record_identity(reviewed_expected)
        ):
            raise DeploymentAdapterError(
                "Backend requirements atomic materialization failed"
            )

    return {
        "path": f"backend/{BACKEND_REQUIREMENTS_RELATIVE}",
        "restored": restored,
        "bytes": reviewed_expected["bytes"],
        "sha256": reviewed_expected["sha256"],
    }


def _run(
    command: Sequence[str],
    *,
    cwd: Path = REPO_ROOT,
    capture: bool = False,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    normalized = [os.fspath(part) for part in command]
    result = subprocess.run(
        normalized,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        env=env,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise DeploymentAdapterError(
            f"command failed ({result.returncode}): {' '.join(normalized)}{suffix}"
        )
    return result


def _run_git_bytes(*args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", os.fspath(REPO_ROOT), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise DeploymentAdapterError(
            f"git {' '.join(args)} failed: {detail}"
        )
    return result.stdout


def _run_git_text(*args: str) -> str:
    try:
        return _run_git_bytes(*args).decode("utf-8").strip()
    except UnicodeError as exc:
        raise DeploymentAdapterError("Git returned non-UTF-8 release data") from exc


def _git_blob_contents(oids: Sequence[str]) -> dict[str, bytes]:
    """Read all already-approved Git blobs in one binary-safe batch."""
    unique = tuple(dict.fromkeys(oids))
    if not unique:
        return {}
    process = subprocess.run(
        ["git", "-C", os.fspath(REPO_ROOT), "cat-file", "--batch"],
        input=b"".join(oid.encode("ascii") + b"\n" for oid in unique),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.returncode:
        detail = process.stderr.decode("utf-8", "replace").strip()
        raise DeploymentAdapterError(f"git cat-file --batch failed: {detail}")
    raw = process.stdout
    offset = 0
    result: dict[str, bytes] = {}
    for expected_oid in unique:
        newline = raw.find(b"\n", offset)
        if newline < 0:
            raise DeploymentAdapterError("Git blob batch response is truncated")
        try:
            oid, object_type, raw_size = raw[offset:newline].decode("ascii").split(" ")
            size = int(raw_size)
        except (UnicodeError, ValueError) as exc:
            raise DeploymentAdapterError(
                "Git blob batch response header is malformed"
            ) from exc
        start = newline + 1
        end = start + size
        if (
            oid != expected_oid
            or object_type != "blob"
            or size < 0
            or end >= len(raw)
            or raw[end:end + 1] != b"\n"
        ):
            raise DeploymentAdapterError(
                f"Git blob batch response is invalid for {expected_oid}"
            )
        result[oid] = raw[start:end]
        offset = end + 1
    if offset != len(raw):
        raise DeploymentAdapterError("Git blob batch response has trailing data")
    return result


def _assert_freeze_git_state(
    *,
    source_git_sha: str,
    source_base_git_sha: str,
) -> None:
    if _run_git_text("rev-parse", "--show-object-format") != "sha1":
        raise DeploymentAdapterError("release manifests require Git SHA-1 objects")
    if _run_git_text("rev-parse", "HEAD") != source_git_sha:
        raise DeploymentAdapterError(
            "freeze source_git_sha must equal the current Git HEAD"
        )
    _run_git_bytes("cat-file", "-e", f"{source_git_sha}^{{commit}}")
    _run_git_bytes("cat-file", "-e", f"{source_base_git_sha}^{{commit}}")
    _run_git_bytes(
        "merge-base", "--is-ancestor", source_base_git_sha, source_git_sha
    )
    raw_status = _run_git_bytes(
        "status", "--porcelain=v1", "-z", "--untracked-files=all", "--",
        "backend", "frontend", ".github/workflows", ".github/actions",
        "scripts", "release/release-intent-v5.json",
    )
    dirty = [
        entry for entry in raw_status.split(b"\0")
        if entry and entry != b"?? backend/.env"
    ]
    if dirty:
        raise DeploymentAdapterError(
            "freeze requires a clean governed source worktree"
        )


def _git_changed_paths(older: str, newer: str) -> list[str]:
    raw = _run_git_bytes("diff", "--name-only", "-z", older, newer)
    try:
        return [part.decode("utf-8") for part in raw.split(b"\0") if part]
    except UnicodeError as exc:
        raise DeploymentAdapterError("Git diff contains a non-UTF-8 path") from exc


def _tracked_intent_at(commit: str) -> dict[str, Any]:
    try:
        raw = _run_git_bytes(
            "show", f"{commit}:release/release-intent-v5.json"
        )
        payload = json.loads(raw.decode("utf-8"))
    except (DeploymentAdapterError, UnicodeError, json.JSONDecodeError) as exc:
        raise DeploymentAdapterError(
            "cannot read the previously reviewed release intent from Git"
        ) from exc
    if not isinstance(payload, dict):
        raise DeploymentAdapterError("previous release intent is not an object")
    if payload.get("kind") != INTENT_KIND or payload.get("protocol_version") != 5:
        raise DeploymentAdapterError("previous release intent is not trusted v5")
    if payload.get("schema_version") not in {1, INTENT_SCHEMA_VERSION}:
        raise DeploymentAdapterError("previous release intent schema is unsupported")
    return payload


def resolve_candidate_source_base(production_head: str, source_git_sha: str) -> str:
    """Resolve the prior exact A/B pair across documentation-only staging.

    This selects J; it does not trust or materialize an intent. The workflow
    still validates J with its own adapter and all existing A/B checks remain.
    """
    head = _full_git_sha(production_head, "production_head")
    source = _full_git_sha(source_git_sha, "source_git_sha")
    _run_git_bytes("merge-base", "--is-ancestor", head, source)
    intent = _tracked_intent_at(head)
    previous_source = _full_git_sha(intent.get("source_git_sha"), "previous source")
    _run_git_bytes("merge-base", "--is-ancestor", previous_source, head)
    intent_path = "release/release-intent-v5.json"
    for candidate in _run_git_text("rev-list", "--first-parent", "--max-count=128", head).splitlines():
        paths = _git_changed_paths(previous_source, candidate)
        if paths == [intent_path]:
            # Includes full-history rejection of intent edits/reverts in J..A.
            _assert_candidate_source_transition(source_git_sha=source, source_base_git_sha=candidate)
            return candidate
        extra = [path for path in paths if path != intent_path]
        if not extra or any(path != "AGENTS.md" and not path.startswith("docs/") for path in extra):
            raise DeploymentAdapterError("production base contains unreviewed non-documentation changes")
        if _run_git_bytes("show", f"{candidate}:{intent_path}") != _run_git_bytes("show", f"{head}:{intent_path}"):
            raise DeploymentAdapterError("documentation staging changed the reviewed intent")
    raise DeploymentAdapterError("no exact prior source/intent pair within bounded first-parent history")


def _assert_candidate_source_transition(
    *,
    source_git_sha: str,
    source_base_git_sha: str,
) -> None:
    """Prove J is a prior B and A carries J's intent bytes unchanged."""
    if source_git_sha == source_base_git_sha:
        raise DeploymentAdapterError("source A must differ from source base J")
    previous_intent = _tracked_intent_at(source_base_git_sha)
    previous_source = _full_git_sha(
        previous_intent.get("source_git_sha"),
        "previous release source_git_sha",
    )
    _run_git_bytes(
        "merge-base", "--is-ancestor", previous_source, source_base_git_sha
    )
    if _git_changed_paths(previous_source, source_base_git_sha) != [
        "release/release-intent-v5.json"
    ]:
        raise DeploymentAdapterError(
            "previous deployment is not an exact source/intent A/B pair"
        )
    source_changes = _git_changed_paths(source_base_git_sha, source_git_sha)
    if (
        not source_changes
        or "release/release-intent-v5.json" in source_changes
        or _run_git_text(
            "rev-list", "--full-history", f"{source_base_git_sha}..{source_git_sha}",
            "--", "release/release-intent-v5.json",
        )
    ):
        raise DeploymentAdapterError(
            "source A must change governed source without changing intent"
        )
    if _run_git_bytes(
        "show", f"{source_git_sha}:release/release-intent-v5.json"
    ) != _run_git_bytes(
        "show", f"{source_base_git_sha}:release/release-intent-v5.json"
    ):
        raise DeploymentAdapterError(
            "source A does not carry source base J's exact intent bytes"
        )


def _git_scope_record_path(repo_relative: str, *, scope: str) -> str | None:
    try:
        validate_source_path(repo_relative, "Git release source")
    except ValueError as exc:
        raise DeploymentAdapterError(str(exc)) from exc
    if PurePosixPath(repo_relative).suffix.casefold() in {".pyc", ".pyo"}:
        raise DeploymentAdapterError(
            "Git release source contains prepackaged bytecode"
        )
    if scope == BACKEND_RUNTIME_SOURCE_SCOPE:
        if not repo_relative.startswith("backend/"):
            raise DeploymentAdapterError(
                f"Backend Git source escaped its root: {repo_relative}"
            )
        relative = repo_relative.removeprefix("backend/")
        try:
            included = backend_runtime_source_path_included(relative)
        except ValueError as exc:
            raise DeploymentAdapterError(str(exc)) from exc
        if relative in BACKEND_RUNTIME_GENERATED_PATHS:
            raise DeploymentAdapterError(
                f"generated Backend path must not be tracked: {repo_relative}"
            )
        if not included:
            return None
        return relative
    if scope == RELEASE_CONTROL_SOURCE_SCOPE:
        if not (
            repo_relative.startswith(".github/workflows/")
            or repo_relative.startswith(".github/actions/")
            or repo_relative.startswith("scripts/")
        ):
            raise DeploymentAdapterError(
                f"Release control Git source escaped its scope: {repo_relative}"
            )
        return repo_relative
    raise DeploymentAdapterError(f"unsupported release source scope: {scope}")


def _git_scope_records(commit: str, *, scope: str) -> list[dict[str, Any]]:
    pathspecs = (
        ("backend",)
        if scope == BACKEND_RUNTIME_SOURCE_SCOPE
        else (".github/workflows", ".github/actions", "scripts")
    )
    output = _run_git_bytes(
        "ls-tree", "-rz", "--full-tree", commit, "--", *pathspecs
    )
    entries: list[tuple[str, str, str, str]] = []
    for raw in output.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            mode, object_type, raw_oid = metadata.split(b" ", 2)
            repo_relative = raw_path.decode("utf-8")
            oid = raw_oid.decode("ascii")
            git_mode = mode.decode("ascii")
            git_type = object_type.decode("ascii")
        except (ValueError, UnicodeError) as exc:
            raise DeploymentAdapterError(
                "Git release source contains a malformed or non-UTF-8 entry"
            ) from exc
        relative = _git_scope_record_path(repo_relative, scope=scope)
        if git_type != "blob" or git_mode not in {"100644", "100755"}:
            raise DeploymentAdapterError(
                f"unsupported Git release source entry: {repo_relative}"
            )
        if relative is None:
            continue
        entries.append((repo_relative, relative, git_mode, oid))
    contents = _git_blob_contents([entry[3] for entry in entries])
    records: list[dict[str, Any]] = []
    for repo_relative, relative, git_mode, oid in entries:
        content = contents[oid]
        actual_oid = _git_blob_oid(content)
        if oid != actual_oid:
            raise DeploymentAdapterError(
                f"Git blob identity mismatch for release source: {repo_relative}"
            )
        records.append({
            "path": relative,
            "mode": git_mode,
            "git_blob": oid,
            "bytes": len(content),
            "sha256": _sha256_bytes(content),
        })
    return sorted(records, key=lambda row: row["path"])


def _git_source_root_tree_oid(commit: str, *, scope: str) -> str:
    return _full_git_sha(
        _run_git_text("rev-parse", f"{commit}^{{tree}}"),
        f"{scope} source_root_tree_oid",
    )


def _git_scope_tree_oid(commit: str, *, scope: str) -> str | None:
    if scope != BACKEND_RUNTIME_SOURCE_SCOPE:
        return None
    return _full_git_sha(
        _run_git_text("rev-parse", f"{commit}:backend"),
        "backend_runtime_source scope_tree_oid",
    )


def _git_source_manifest(
    *,
    scope: str,
    source_git_sha: str,
    source_base_git_sha: str,
) -> dict[str, Any]:
    try:
        current = _git_scope_records(source_git_sha, scope=scope)
        baseline = _git_scope_records(source_base_git_sha, scope=scope)
        current_scope_oid = _git_scope_tree_oid(source_git_sha, scope=scope)
        base_scope_oid = _git_scope_tree_oid(source_base_git_sha, scope=scope)
        return build_source_manifest(
            scope=scope,
            source_git_sha=source_git_sha,
            source_base_git_sha=source_base_git_sha,
            source_root_tree_oid=_git_source_root_tree_oid(
                source_git_sha, scope=scope
            ),
            source_base_root_tree_oid=_git_source_root_tree_oid(
                source_base_git_sha, scope=scope
            ),
            scope_tree_oid=current_scope_oid or git_source_tree_oid(current),
            source_base_scope_tree_oid=(
                base_scope_oid or git_source_tree_oid(baseline)
            ),
            files=current,
            base_files=baseline,
            label=(
                "Backend runtime source"
                if scope == BACKEND_RUNTIME_SOURCE_SCOPE
                else "Release control source"
            ),
        )
    except ValueError as exc:
        raise DeploymentAdapterError(
            f"cannot construct {scope} manifest: {exc}"
        ) from exc


def verify_release_intent_git(
    intent: dict[str, Any],
    *,
    deployment_git_sha: str | None = None,
) -> dict[str, Any]:
    """Re-derive J-to-A manifests and prove current commit is intent-only B."""
    source_git_sha = _full_git_sha(intent.get("source_git_sha"))
    source_base_git_sha = _full_git_sha(
        intent.get("source_base_git_sha"), "source_base_git_sha"
    )
    deployed = _full_git_sha(
        deployment_git_sha or _run_git_text("rev-parse", "HEAD"),
        "deployment_git_sha",
    )
    for commit in (source_base_git_sha, source_git_sha, deployed):
        _run_git_bytes("cat-file", "-e", f"{commit}^{{commit}}")
    _run_git_bytes(
        "merge-base", "--is-ancestor", source_base_git_sha, source_git_sha
    )
    _run_git_bytes(
        "merge-base", "--is-ancestor", source_git_sha, deployed
    )
    if deployed == source_git_sha or _git_changed_paths(
        source_git_sha, deployed
    ) != ["release/release-intent-v5.json"]:
        raise DeploymentAdapterError(
            "deployment commit is not exact intent-only B over source A"
        )
    backend_source = _git_source_manifest(
        scope=BACKEND_RUNTIME_SOURCE_SCOPE,
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
    )
    control_source = _git_source_manifest(
        scope=RELEASE_CONTROL_SOURCE_SCOPE,
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
    )
    if not exact_json_equal(intent.get("backend_runtime_source"), backend_source):
        raise DeploymentAdapterError(
            "release intent Backend runtime manifest differs from Git J-to-A"
        )
    if not exact_json_equal(intent.get("release_control_source"), control_source):
        raise DeploymentAdapterError(
            "release intent control manifest differs from Git J-to-A"
        )
    return {
        "source_git_sha": source_git_sha,
        "source_base_git_sha": source_base_git_sha,
        "deployment_git_sha": deployed,
        "backend_runtime_source": backend_source,
        "release_control_source": control_source,
    }


def _version(command: str) -> str:
    try:
        return _run([command, "--version"], capture=True).stdout.strip()
    except (OSError, DeploymentAdapterError):
        return "unavailable"


def cloud_build_evidence() -> dict[str, Any]:
    """Return sanitized facts which are safe to emit into a build log."""
    return {
        "schema_version": 1,
        "kind": "emergent_cloud_build_evidence_v1",
        "working_directory": os.fspath(Path.cwd().resolve()),
        "repository_root": os.fspath(REPO_ROOT),
        "frontend_root": os.fspath(FRONTEND_ROOT),
        "backend_root": os.fspath(BACKEND_ROOT),
        "declared_frontend_package_root": "frontend",
        "declared_frontend_static_root": "frontend/build",
        "declared_backend_package_root": "backend",
        "source_roots_declared_co_parented": (
            FRONTEND_ROOT.parent == BACKEND_ROOT.parent
        ),
        "platform_snapshot_workspace_shared_observed": False,
        "git_directory_present": (REPO_ROOT / ".git").exists(),
        "host_node": _version("node"),
        "host_yarn": _version("yarn"),
        "host_python": platform.python_version(),
        "system": platform.system(),
        "architecture": platform.machine(),
        "outer_install_command_observed": False,
        "outer_install_command": None,
        "outer_build_command_observed": False,
        "configured_package_build_command": "cd frontend && yarn build",
        "governed_install_command": (
            "governed-toolchain exec -- yarn --cwd frontend install "
            "--frozen-lockfile --non-interactive"
        ),
        "governed_build_command": (
            "governed-toolchain exec -- yarn --cwd frontend build:release"
        ),
    }


def _remove_path(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def clean_generated_state(*, remove_dependencies: bool) -> None:
    for path in (BUILD_ROOT, PROOF_PATH.parent, IDENTITY_PATH):
        _remove_path(path)
    if remove_dependencies:
        _remove_path(FRONTEND_ROOT / "node_modules")


def _validated_frontend_source(value: Any) -> dict[str, Any]:
    _require_real_directory(FRONTEND_ROOT, "Frontend source root")
    if not isinstance(value, dict):
        raise DeploymentAdapterError("release intent frontend_source is missing")
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise DeploymentAdapterError("release intent frontend source files are missing")
    file_count = value.get("file_count")
    if type(file_count) is not int or file_count <= 0:
        raise DeploymentAdapterError("release intent frontend source count is invalid")
    records: list[dict[str, Any]] = []
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise DeploymentAdapterError("release intent frontend source record is invalid")
        relative = _relative_path(raw.get("path"), "frontend source")
        mode = str(raw.get("mode") or "")
        blob = str(raw.get("git_blob") or "")
        digest = str(raw.get("sha256") or "")
        size = raw.get("bytes")
        if mode not in {"100644", "100755"}:
            raise DeploymentAdapterError(f"invalid frontend source mode: {relative}")
        if len(blob) != 40 or any(character not in "0123456789abcdef" for character in blob):
            raise DeploymentAdapterError(f"invalid frontend source Git blob: {relative}")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise DeploymentAdapterError(f"invalid frontend source SHA256: {relative}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise DeploymentAdapterError(f"invalid frontend source byte count: {relative}")
        absolute = FRONTEND_ROOT / relative
        try:
            info = absolute.lstat()
        except OSError as exc:
            raise DeploymentAdapterError(
                f"reviewed frontend source is missing: {relative}"
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise DeploymentAdapterError(
                f"reviewed frontend source is not a regular file: {relative}"
            )
        content = absolute.read_bytes()
        try:
            actual_mode = validated_source_git_mode(
                info.st_mode,
                label="frontend source",
                path=relative,
            )
        except ValueError as exc:
            raise DeploymentAdapterError(str(exc)) from exc
        actual = {
            "path": relative,
            "mode": actual_mode,
            "git_blob": _git_blob_oid(content),
            "bytes": len(content),
            "sha256": _sha256_bytes(content),
        }
        expected = {
            "path": relative,
            "mode": mode,
            "git_blob": blob,
            "bytes": size,
            "sha256": digest,
        }
        if actual != expected:
            raise DeploymentAdapterError(
                f"frontend source differs from reviewed intent: {relative}"
            )
        records.append(expected)
    if records != sorted(records, key=lambda row: row["path"]):
        raise DeploymentAdapterError("release intent frontend source is not sorted")
    if len({row["path"] for row in records}) != len(records):
        raise DeploymentAdapterError("release intent frontend source has duplicate paths")
    expected = {
        "scope": "git_head_frontend_tree_v1",
        "git_tree_oid": str(value.get("git_tree_oid") or ""),
        "file_count": len(records),
        "files": records,
        "tree_sha256": _canonical_source_tree_sha256(records),
    }
    _full_git_sha(expected["git_tree_oid"], "frontend_source.git_tree_oid")
    if value != expected:
        raise DeploymentAdapterError("release intent frontend_source proof is invalid")
    return expected


def _critical_hashes() -> dict[str, str]:
    from release_identity import CRITICAL_FILES

    result: dict[str, str] = {}
    for relative in CRITICAL_FILES:
        path = BACKEND_ROOT / relative
        if path.is_symlink() or not path.is_file():
            raise DeploymentAdapterError(
                f"critical Backend source is missing or not regular: {relative}"
            )
        result[relative] = _sha256(path)
    return result


def _reviewed_client_environment(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != set(CLIENT_ENV_ALLOWLIST):
        raise DeploymentAdapterError(
            "release intent client_environment does not match the allowlist"
        )
    result: dict[str, dict[str, Any]] = {}
    for name in CLIENT_ENV_ALLOWLIST:
        record = value.get(name)
        if not isinstance(record, dict) or set(record) != {"present", "value"}:
            raise DeploymentAdapterError(
                f"release intent client environment record is invalid: {name}"
            )
        present = record.get("present")
        raw = record.get("value")
        if not isinstance(present, bool):
            raise DeploymentAdapterError(
                f"release intent client environment presence is invalid: {name}"
            )
        if not present:
            raise DeploymentAdapterError(
                f"required release client environment is absent: {name}"
            )
        if not isinstance(raw, str) or not raw or len(raw) > 2048:
            raise DeploymentAdapterError(
                f"release intent client environment value is invalid: {name}"
            )
        if raw != EXPECTED_CLIENT_ENVIRONMENT[name]:
            raise DeploymentAdapterError(
                f"release intent client environment value is not approved: {name}"
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
            raise DeploymentAdapterError(
                f"release intent client environment must be a public HTTP origin: {name}"
            )
        result[name] = {"present": True, "value": raw}
    return result


def _assert_client_environment_binding(
    *,
    reviewed: dict[str, dict[str, Any]],
    frontend_build: dict[str, Any],
) -> None:
    proof_values = (
        (frontend_build.get("environment") or {}).get("values") or {}
    )
    expected: dict[str, dict[str, Any]] = {}
    for name, record in reviewed.items():
        raw = record["value"]
        expected[name] = {
            "present": record["present"],
            "sha256": (
                _sha256_bytes(raw.encode("utf-8"))
                if record["present"]
                else None
            ),
        }
    if not exact_json_equal(proof_values, expected):
        raise DeploymentAdapterError(
            "reviewed client environment does not match frontend build proof"
        )


def _validate_runtime_identity(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DeploymentAdapterError("release intent runtime_identity is missing")
    try:
        from release_protocol_v5 import validate_runtime_release_identity

        return validate_runtime_release_identity(
            payload,
            backend_root=BACKEND_ROOT,
            allow_derived_bytecode=False,
        )
    except (ImportError, ValueError, TypeError) as exc:
        raise DeploymentAdapterError(f"runtime release identity is invalid: {exc}") from exc


def _load_and_validate_release_intent(
    path: Path,
    *,
    verify_deployment_git: bool,
) -> dict[str, Any]:
    payload = _load_json(path, "tracked release intent")
    if set(payload) != INTENT_KEYS:
        raise DeploymentAdapterError("release intent fields are not canonical")
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != INTENT_SCHEMA_VERSION
    ):
        raise DeploymentAdapterError("unsupported release intent schema_version")
    if payload.get("kind") != INTENT_KIND:
        raise DeploymentAdapterError("unsupported release intent kind")
    if (
        type(payload.get("protocol_version")) is not int
        or payload.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise DeploymentAdapterError("release intent is not protocol v5")
    source_git_sha = _full_git_sha(payload.get("source_git_sha"))
    source_base_git_sha = _full_git_sha(
        payload.get("source_base_git_sha"), "source_base_git_sha"
    )
    frontend_source = _validated_frontend_source(payload.get("frontend_source"))
    client_environment = _reviewed_client_environment(
        payload.get("client_environment")
    )
    critical = payload.get("critical_file_hashes")
    if not exact_json_equal(critical, _critical_hashes()):
        raise DeploymentAdapterError("release intent critical Backend hashes are stale")
    try:
        backend_runtime_source = validate_backend_runtime_source_manifest(
            payload.get("backend_runtime_source"),
            backend_root=BACKEND_ROOT,
            source_git_sha=source_git_sha,
            source_base_git_sha=source_base_git_sha,
            allow_derived_bytecode=False,
        )
        release_control_source = validate_release_control_source_manifest(
            payload.get("release_control_source"),
            repo_root=REPO_ROOT,
            source_git_sha=source_git_sha,
            source_base_git_sha=source_base_git_sha,
            allow_missing_github=True,
        )
        release_control_summary = source_manifest_summary(
            release_control_source,
            expected_scope=RELEASE_CONTROL_SOURCE_SCOPE,
            label="Release control source",
        )
    except (ValueError, TypeError) as exc:
        raise DeploymentAdapterError(
            f"release source manifests are invalid: {exc}"
        ) from exc
    runtime_identity = _validate_runtime_identity(payload.get("runtime_identity"))
    if runtime_identity.get("source_git_sha") != source_git_sha:
        raise DeploymentAdapterError("runtime identity source SHA differs from intent")
    if runtime_identity.get("source_base_git_sha") != source_base_git_sha:
        raise DeploymentAdapterError(
            "runtime identity source base SHA differs from intent"
        )
    if payload.get("branch") != runtime_identity.get("branch"):
        raise DeploymentAdapterError("runtime identity branch differs from intent")
    for name in ("frontend_build", "frontend_reproducibility", "critical_file_hashes"):
        if not exact_json_equal(payload.get(name), runtime_identity.get(name)):
            raise DeploymentAdapterError(f"runtime identity {name} differs from intent")
    if not exact_json_equal(
        runtime_identity.get("backend_runtime_source"), backend_runtime_source
    ):
        raise DeploymentAdapterError(
            "runtime identity Backend runtime source differs from intent"
        )
    if not exact_json_equal(
        runtime_identity.get("release_control_source"), release_control_summary
    ):
        raise DeploymentAdapterError(
            "runtime identity release control summary differs from intent"
        )
    if not exact_json_equal(payload["frontend_build"].get("source"), {
        "scope": frontend_source["scope"],
        "git_tree_oid": frontend_source["git_tree_oid"],
        "file_count": frontend_source["file_count"],
        "tree_sha256": frontend_source["tree_sha256"],
    }):
        raise DeploymentAdapterError("frontend build source summary differs from intent")
    _assert_client_environment_binding(
        reviewed=client_environment,
        frontend_build=payload["frontend_build"],
    )
    if verify_deployment_git and (REPO_ROOT / ".git").exists():
        verify_release_intent_git(payload)
    return payload


def load_release_intent(path: Path = INTENT_PATH) -> dict[str, Any]:
    """Load the tracked deployment intent and require exact intent-only B."""
    return _load_and_validate_release_intent(
        path, verify_deployment_git=True
    )


def load_candidate_release_intent(
    path: Path,
    *,
    source_git_sha: str,
    source_base_git_sha: str,
) -> dict[str, Any]:
    """Validate an external freeze artifact while HEAD is source commit A."""
    candidate_path = _freeze_output_path(path)
    expected_source = _full_git_sha(source_git_sha)
    expected_base = _full_git_sha(
        source_base_git_sha, "source_base_git_sha"
    )
    _assert_freeze_git_state(
        source_git_sha=expected_source,
        source_base_git_sha=expected_base,
    )
    _assert_candidate_source_transition(
        source_git_sha=expected_source,
        source_base_git_sha=expected_base,
    )
    payload = _load_and_validate_release_intent(
        candidate_path,
        verify_deployment_git=False,
    )
    if (
        payload["source_git_sha"] != expected_source
        or payload["source_base_git_sha"] != expected_base
    ):
        raise DeploymentAdapterError(
            "candidate intent provenance differs from expected A/J"
        )
    expected_backend = _git_source_manifest(
        scope=BACKEND_RUNTIME_SOURCE_SCOPE,
        source_git_sha=expected_source,
        source_base_git_sha=expected_base,
    )
    expected_control = _git_source_manifest(
        scope=RELEASE_CONTROL_SOURCE_SCOPE,
        source_git_sha=expected_source,
        source_base_git_sha=expected_base,
    )
    if not exact_json_equal(
        payload["backend_runtime_source"], expected_backend
    ) or not exact_json_equal(
        payload["release_control_source"], expected_control
    ):
        raise DeploymentAdapterError(
            "candidate intent source manifests differ from Git J-to-A"
        )
    return payload


def _read_frontend_evidence(source_git_sha: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from frontend_build_identity import (
        FrontendBuildIdentityError,
        read_frontend_build_identity,
        read_frontend_reproducibility_proof,
    )

    try:
        frontend_build = read_frontend_build_identity(
            expected_git_sha=source_git_sha,
            require_git_source=False,
        )
        proof = read_frontend_reproducibility_proof(frontend_build=frontend_build)
    except FrontendBuildIdentityError as exc:
        raise DeploymentAdapterError(f"frontend release evidence is invalid: {exc}") from exc
    return frontend_build, proof


def _toolchain(
    command: Sequence[str], *, env: dict[str, str] | None = None
) -> None:
    _run([sys.executable, TOOLCHAIN_SCRIPT, *command], env=env)


def _toolchain_cache_home(parent: dict[str, str]) -> str:
    """Choose a stable cache root which can never be the Git worktree."""
    configured = parent.get("XDG_CACHE_HOME")
    if configured:
        candidate = Path(configured)
        if not candidate.is_absolute():
            raise DeploymentAdapterError("XDG_CACHE_HOME must be an absolute path")
    else:
        original_home = parent.get("HOME")
        if original_home and Path(original_home).is_absolute():
            candidate = Path(original_home) / ".cache"
        else:
            candidate = Path(tempfile.gettempdir()) / (
                f"mezan-release-v5-cache-{os.getuid()}"
            )
    resolved_candidate = candidate.resolve(strict=False)
    resolved_repo = REPO_ROOT.resolve(strict=False)
    if resolved_candidate == resolved_repo or resolved_repo in resolved_candidate.parents:
        raise DeploymentAdapterError(
            "release toolchain cache must be outside the Git worktree"
        )
    return os.fspath(candidate)


def _governed_build(
    source_git_sha: str,
    *,
    client_environment: dict[str, dict[str, Any]] | None = None,
) -> None:
    child_environment = {
        name: os.environ[name]
        for name in TOOLCHAIN_HOST_ENV_ALLOWLIST
        if name in os.environ
    }
    child_environment.setdefault("PATH", os.defpath)
    child_environment["XDG_CACHE_HOME"] = _toolchain_cache_home(os.environ)
    child_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if client_environment is not None:
        reviewed = _reviewed_client_environment(client_environment)
        for name, record in reviewed.items():
            if record["present"]:
                child_environment[name] = record["value"]
            else:
                child_environment.pop(name, None)
    # A neutral HOME prevents Yarn 1 from consulting user-owned .yarnrc or
    # .npmrc files. The durable toolchain cache remains in XDG_CACHE_HOME.
    with tempfile.TemporaryDirectory(prefix="mezan-release-v5-home-") as home:
        child_environment["HOME"] = home
        _toolchain(["ensure"], env=child_environment)
        _toolchain([
            "exec", "--", "yarn", "--cwd", "frontend", "install",
            "--frozen-lockfile", "--non-interactive",
        ], env=child_environment)
        _toolchain([
            "exec", "--", "yarn", "--cwd", "frontend", "build:release",
        ], env=child_environment)
        _toolchain([
            "exec", "--", sys.executable, os.fspath(VERIFY_SCRIPT),
            "--expected-git-sha", source_git_sha,
            "--reviewed-intent-v5",
        ], env=child_environment)


def materialize_identity(intent: dict[str, Any]) -> dict[str, Any]:
    source_git_sha = _full_git_sha(intent.get("source_git_sha"))
    frontend_build, proof = _read_frontend_evidence(source_git_sha)
    if not exact_json_equal(frontend_build, intent.get("frontend_build")):
        raise DeploymentAdapterError(
            "Cloud Build frontend artifact differs from reviewed release intent"
        )
    if not exact_json_equal(proof, intent.get("frontend_reproducibility")):
        raise DeploymentAdapterError(
            "Cloud Build reproducibility proof differs from reviewed release intent"
        )
    identity = _validate_runtime_identity(intent.get("runtime_identity"))
    _atomic_json(IDENTITY_PATH, identity)
    if not exact_json_equal(
        _load_json(IDENTITY_PATH, "materialized runtime identity"),
        identity,
    ):
        raise DeploymentAdapterError("runtime identity atomic materialization failed")
    return identity


def _file_records(
    root: Path,
    *,
    allowed_public_paths: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    if not isinstance(allowed_public_paths, frozenset) or not (
        allowed_public_paths <= FRONTEND_PUBLIC_PACKAGE_PATHS
    ):
        raise DeploymentAdapterError(
            "deployment package public-path allowlist is invalid"
        )
    records: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if relative in allowed_public_paths:
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                raise DeploymentAdapterError(
                    "Frontend public package path must be a regular file"
                )
            try:
                git_mode = validated_source_git_mode(
                    info.st_mode,
                    label="Frontend public package path",
                    path=relative,
                )
            except ValueError as exc:
                raise DeploymentAdapterError(str(exc)) from exc
            if git_mode != "100644":
                raise DeploymentAdapterError(
                    "Frontend public package path must be non-executable"
                )
            records.append({
                "path": relative,
                "bytes": info.st_size,
                "sha256": _sha256(path),
            })
            continue
        try:
            validate_source_path(relative, "deployment package")
        except ValueError as exc:
            raise DeploymentAdapterError(str(exc)) from exc
        if stat.S_ISLNK(info.st_mode):
            raise DeploymentAdapterError(f"deployment package contains symlink: {path}")
        if stat.S_ISDIR(info.st_mode):
            permissions = stat.S_IMODE(info.st_mode)
            if permissions & 0o7022 or permissions & 0o500 != 0o500:
                raise DeploymentAdapterError(
                    f"deployment package has unsafe directory mode: {relative}"
                )
        elif stat.S_ISREG(info.st_mode):
            try:
                validated_source_git_mode(
                    info.st_mode, label="deployment package", path=relative
                )
            except ValueError as exc:
                raise DeploymentAdapterError(str(exc)) from exc
            records.append({
                "path": relative,
                "bytes": info.st_size,
                "sha256": _sha256(path),
            })
        else:
            raise DeploymentAdapterError(
                f"deployment package contains unsupported entry: {relative}"
            )
    return sorted(records, key=lambda row: row["path"])


def _require_real_directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DeploymentAdapterError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DeploymentAdapterError(f"{label} is not a real directory: {path}")


def _copy_backend_package(
    destination: Path,
    *,
    identity: dict[str, Any],
) -> None:
    """Copy exactly the reviewed runtime projection plus generated identity."""
    try:
        validated = validate_backend_runtime_source_manifest(
            identity.get("backend_runtime_source"),
            backend_root=BACKEND_ROOT,
            source_git_sha=identity.get("source_git_sha"),
            source_base_git_sha=identity.get("source_base_git_sha"),
            allow_derived_bytecode=False,
        )
    except (ValueError, TypeError) as exc:
        raise DeploymentAdapterError(
            f"Backend package source does not match its manifest: {exc}"
        ) from exc
    destination.mkdir(mode=0o755)
    for record in validated["files"]:
        relative = record["path"]
        source = BACKEND_ROOT / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        shutil.copy2(source, target, follow_symlinks=False)
    identity_source = BACKEND_ROOT / "release_identity.json"
    try:
        identity_info = identity_source.lstat()
    except OSError as exc:
        raise DeploymentAdapterError(
            "Backend deployment identity is missing"
        ) from exc
    if (
        stat.S_ISLNK(identity_info.st_mode)
        or not stat.S_ISREG(identity_info.st_mode)
        or stat.S_IMODE(identity_info.st_mode) != 0o644
    ):
        raise DeploymentAdapterError(
            "Backend deployment identity must be a regular 0644 file"
        )
    shutil.copy2(identity_source, destination / "release_identity.json")


def _isolated_backend_probe_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


def verify_package_boundaries(intent: dict[str, Any]) -> dict[str, Any]:
    """Prove package membership from isolated copies, not the source workspace."""
    identity = intent["runtime_identity"]
    frontend_build = intent["frontend_build"]
    _require_real_directory(FRONTEND_ROOT, "Frontend package source root")
    _require_real_directory(BUILD_ROOT, "Frontend deployment root")
    _require_real_directory(BACKEND_ROOT, "Backend deployment root")
    with (
        tempfile.TemporaryDirectory(
            prefix="mezan-release-v5-frontend-package-"
        ) as frontend_temporary,
        tempfile.TemporaryDirectory(
            prefix="mezan-release-v5-backend-package-"
        ) as backend_temporary,
    ):
        frontend_package = Path(frontend_temporary) / "frontend"
        backend_package = Path(backend_temporary) / "backend"
        shutil.copytree(
            FRONTEND_ROOT,
            frontend_package,
            symlinks=True,
            ignore=shutil.ignore_patterns("node_modules", ".release"),
        )
        if any(
            ".git" in path.relative_to(package).parts
            for package in (frontend_package, backend_package)
            for path in package.rglob("*")
        ):
            raise DeploymentAdapterError(
                "deployment candidate package contains Git metadata"
            )

        frontend_records = _file_records(
            frontend_package,
            allowed_public_paths=FRONTEND_PUBLIC_PACKAGE_PATHS,
        )
        frontend_by_path = {row["path"]: row for row in frontend_records}
        expected_meta = frontend_build["build_meta"]
        packaged_meta = frontend_by_path.get("build/build-meta.json")
        if packaged_meta != {
            **expected_meta,
            "path": "build/build-meta.json",
        }:
            raise DeploymentAdapterError(
                "Frontend deployment package is missing exact build-meta.json"
            )
        for expected in frontend_build.get("public_files") or []:
            packaged_expected = {
                **expected,
                "path": f"build/{expected['path']}",
            }
            if frontend_by_path.get(packaged_expected["path"]) != packaged_expected:
                raise DeploymentAdapterError(
                    f"Frontend deployment package lost public file: {expected['path']}"
                )
        for runtime_path in (
            "package.json",
            "scripts/governed-preview.cjs",
            "scripts/start-governed-runtime.cjs",
            "vite.config.js",
            "yarn.lock",
        ):
            if runtime_path not in frontend_by_path:
                raise DeploymentAdapterError(
                    f"Frontend deployment package lost runtime file: {runtime_path}"
                )
        index_bytes = (frontend_package / "build" / "index.html").read_bytes()
        meta_bytes = (frontend_package / "build" / "build-meta.json").read_bytes()
        if index_bytes == meta_bytes:
            raise DeploymentAdapterError("build-meta.json resolved to the SPA shell")
        parsed_meta = json.loads(meta_bytes.decode("utf-8"))
        if not isinstance(parsed_meta, dict):
            raise DeploymentAdapterError("packaged build-meta.json is not a JSON object")
        node_probe = subprocess.run(
            [
                "node",
                "-e",
                (
                    "require('./scripts/start-governed-runtime.cjs')"
                    ".validateGovernedRuntimeArtifact()"
                ),
            ],
            cwd=frontend_package,
            env={
                "NODE_ENV": "production",
                "PATH": os.environ.get("PATH", os.defpath),
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if node_probe.returncode:
            raise DeploymentAdapterError(
                "isolated Frontend package runtime validation failed: "
                f"{node_probe.stderr.strip()}"
            )

        _copy_backend_package(backend_package, identity=identity)
        backend_records = _file_records(backend_package)
        backend_by_path = {row["path"]: row for row in backend_records}
        if "release_identity.json" not in backend_by_path:
            raise DeploymentAdapterError(
                "Backend deployment package is missing release_identity.json"
            )
        expected_backend_paths = {
            record["path"]
            for record in identity["backend_runtime_source"]["files"]
        } | {"release_identity.json"}
        if set(backend_by_path) != expected_backend_paths:
            raise DeploymentAdapterError(
                "Backend deployment package membership differs from its manifest"
            )
        for relative, digest in identity["critical_file_hashes"].items():
            packaged = backend_by_path.get(relative)
            if packaged is None or packaged["sha256"] != digest:
                raise DeploymentAdapterError(
                    f"Backend deployment package lost critical file: {relative}"
                )

        probe = (
            "import json; "
            "from release_identity import read_release_identity, release_health_payload; "
            "print(json.dumps({'identity': read_release_identity(), "
            "'health': release_health_payload()}, sort_keys=True))"
        )
        result = subprocess.run(
            [sys.executable, "-I", "-c", (
                "import sys; "
                f"sys.path.insert(0, {os.fspath(backend_package)!r}); " + probe
            )],
            cwd=backend_package.parent,
            env=_isolated_backend_probe_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise DeploymentAdapterError(
                "isolated Backend package identity failed: "
                f"{result.stderr.strip()}"
            )
        isolated_payload = json.loads(result.stdout)
        isolated = isolated_payload.get("identity") or {}
        isolated_health = isolated_payload.get("health") or {}
        if isolated.get("verified_identity_available") is not True:
            raise DeploymentAdapterError(
                "isolated Backend package did not verify its release identity"
            )
        if isolated.get("release_id") != identity["release_id"]:
            raise DeploymentAdapterError("isolated Backend package release ID drifted")
        _assert_isolated_backend_health(isolated, isolated_health)
        if (
            (backend_package.parent / ".git").exists()
            or (backend_package.parent / "frontend").exists()
            or (backend_package / ".git").exists()
        ):
            raise DeploymentAdapterError(
                "isolated Backend package unexpectedly has Git or Frontend siblings"
            )

        return {
            "schema_version": 1,
            "kind": "mezan_release_package_boundary_proof_v1",
            "source_git_sha": intent["source_git_sha"],
            "release_id": identity["release_id"],
            "frontend": {
                "runtime_root": "frontend",
                "static_root": "frontend/build",
                "file_count": len(frontend_records),
                "package_tree_sha256": _canonical_file_tree_sha256(frontend_records),
                "artifact_tree_sha256": frontend_build["artifact_tree_sha256"],
                "build_meta": packaged_meta,
                "content_type_contract": "application/json",
                "isolated_runtime_artifact_verified": True,
            },
            "backend": {
                "runtime_root": "backend",
                "file_count": len(backend_records),
                "package_tree_sha256": _canonical_file_tree_sha256(backend_records),
                "identity": backend_by_path["release_identity.json"],
                "isolated_verified": True,
                "health_verified": True,
                "sibling_frontend_present": False,
                "git_directory_present": False,
            },
        }


def _assert_isolated_backend_health(
    isolated: dict[str, Any],
    isolated_health: dict[str, Any],
) -> None:
    health_release = isolated_health.get("release")
    boot_started_at = (
        health_release.get("boot_started_at")
        if isinstance(health_release, dict)
        else None
    )
    expected_release = {
        **isolated,
        "boot_started_at": boot_started_at,
    }
    expected_health = {
        "ok": True,
        "service": "backend",
        "release": expected_release,
    }
    if (
        not isinstance(boot_started_at, str)
        or not boot_started_at.strip()
        or not exact_json_equal(isolated_health, expected_health)
    ):
        raise DeploymentAdapterError(
            "isolated Backend health payload differs from verified identity"
        )


def build_cloud_release() -> dict[str, Any]:
    # Refuse parent-directory indirection before cleanup touches any generated
    # path. Otherwise a symlinked Frontend/Backend root could redirect cleanup
    # outside the checked-out Cloud Build workspace.
    _require_real_directory(REPO_ROOT, "repository root")
    _require_real_directory(FRONTEND_ROOT, "Frontend source root")
    _require_real_directory(BACKEND_ROOT, "Backend source root")
    _require_real_directory(
        (REPO_ROOT / REVIEWED_BACKEND_REQUIREMENTS_RELATIVE).parent,
        "reviewed Backend requirements root",
    )
    evidence = cloud_build_evidence()
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    materialization = _materialize_cloud_build_backend_requirements()
    print(
        json.dumps(
            {"backend_requirements_materialization": materialization},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    clean_generated_state(remove_dependencies=True)
    try:
        intent = load_release_intent()
        _governed_build(
            intent["source_git_sha"],
            client_environment=intent["client_environment"],
        )
        materialize_identity(intent)
        boundary = verify_package_boundaries(intent)
        result = {
            "built": True,
            "protocol_version": PROTOCOL_VERSION,
            "source_git_sha": intent["source_git_sha"],
            "release_id": intent["runtime_identity"]["release_id"],
            "frontend_artifact_tree_sha256": intent["frontend_build"][
                "artifact_tree_sha256"
            ],
            "package_boundary": boundary,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return result
    except Exception:
        clean_generated_state(remove_dependencies=False)
        raise


def _freeze_output_path(value: Path) -> Path:
    output = Path(value)
    if not output.is_absolute():
        raise DeploymentAdapterError("freeze output path must be absolute")
    try:
        parent = output.parent
        parent_info = parent.lstat()
        if (
            stat.S_ISLNK(parent_info.st_mode)
            or not stat.S_ISDIR(parent_info.st_mode)
            or parent.absolute() != parent.resolve(strict=True)
        ):
            raise OSError("parent is not a real directory")
        if output.exists() and (
            output.is_symlink() or not stat.S_ISREG(output.lstat().st_mode)
        ):
            raise OSError("existing output is not a regular file")
    except OSError as exc:
        raise DeploymentAdapterError(f"freeze output path is unsafe: {exc}") from exc
    repo = REPO_ROOT.resolve(strict=True)
    resolved = output.resolve(strict=False)
    if resolved == repo or repo in resolved.parents:
        raise DeploymentAdapterError(
            "freeze output must be outside the Git worktree"
        )
    return output


def freeze_intent(
    source_git_sha: str,
    source_base_git_sha: str,
    branch: str,
    output_path: Path,
) -> dict[str, Any]:
    source_git_sha = _full_git_sha(source_git_sha)
    source_base_git_sha = _full_git_sha(
        source_base_git_sha, "source_base_git_sha"
    )
    output = _freeze_output_path(output_path)
    _assert_freeze_git_state(
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
    )
    _assert_candidate_source_transition(
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
    )
    backend_runtime_source = _git_source_manifest(
        scope=BACKEND_RUNTIME_SOURCE_SCOPE,
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
    )
    release_control_source = _git_source_manifest(
        scope=RELEASE_CONTROL_SOURCE_SCOPE,
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
    )
    try:
        validate_backend_runtime_source_manifest(
            backend_runtime_source,
            backend_root=BACKEND_ROOT,
            source_git_sha=source_git_sha,
            source_base_git_sha=source_base_git_sha,
            allow_derived_bytecode=False,
        )
        validate_release_control_source_manifest(
            release_control_source,
            repo_root=REPO_ROOT,
            source_git_sha=source_git_sha,
            source_base_git_sha=source_base_git_sha,
        )
    except (ValueError, TypeError) as exc:
        raise DeploymentAdapterError(
            f"Git source manifests differ from the clean worktree: {exc}"
        ) from exc
    frontend_build, proof = _read_frontend_evidence(source_git_sha)
    metadata = _load_json(BUILD_ROOT / "build-meta.json", "frontend build metadata")
    frontend_source = metadata.get("source")
    _validated_frontend_source(frontend_source)
    critical = _critical_hashes()
    client_environment = {
        name: {
            "present": name in os.environ,
            "value": os.environ.get(name) if name in os.environ else None,
        }
        for name in CLIENT_ENV_ALLOWLIST
    }
    reviewed_client_environment = _reviewed_client_environment(
        client_environment
    )
    _assert_client_environment_binding(
        reviewed=reviewed_client_environment,
        frontend_build=frontend_build,
    )
    try:
        from release_protocol_v5 import build_runtime_release_identity

        runtime_identity = build_runtime_release_identity(
            source_git_sha=source_git_sha,
            source_base_git_sha=source_base_git_sha,
            branch=branch,
            frontend_build=frontend_build,
            frontend_reproducibility=proof,
            backend_root=BACKEND_ROOT,
            backend_runtime_source=backend_runtime_source,
            release_control_source=release_control_source,
        )
    except (ImportError, ValueError, TypeError) as exc:
        raise DeploymentAdapterError(f"cannot build runtime release identity: {exc}") from exc
    intent = {
        "schema_version": INTENT_SCHEMA_VERSION,
        "kind": INTENT_KIND,
        "protocol_version": PROTOCOL_VERSION,
        "source_git_sha": source_git_sha,
        "source_base_git_sha": source_base_git_sha,
        "branch": branch,
        "frontend_source": frontend_source,
        "backend_runtime_source": backend_runtime_source,
        "release_control_source": release_control_source,
        "client_environment": reviewed_client_environment,
        "frontend_build": frontend_build,
        "frontend_reproducibility": proof,
        "critical_file_hashes": critical,
        "runtime_identity": runtime_identity,
    }
    _atomic_json(output, intent)
    return intent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a governed release inside Emergent Cloud Build"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("build", help="perform governed Cloud Build materialization")
    freeze = subparsers.add_parser(
        "freeze-intent", help="freeze a reviewed governed build as tracked intent"
    )
    freeze.add_argument("--source-git-sha", required=True)
    freeze.add_argument("--source-base-git-sha", required=True)
    freeze.add_argument("--branch", default="hotfix/prod-snap-meta-final")
    freeze.add_argument("--output", type=Path, required=True)
    subparsers.add_parser(
        "verify-packages", help="verify isolated Frontend/Backend package boundaries"
    )
    subparsers.add_parser(
        "evidence", help="print sanitized Cloud Build/runtime contract facts"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "build":
            build_cloud_release()
        elif args.action == "freeze-intent":
            print(json.dumps(
                freeze_intent(
                    args.source_git_sha,
                    args.source_base_git_sha,
                    args.branch,
                    args.output,
                ),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ))
        elif args.action == "verify-packages":
            print(json.dumps(
                verify_package_boundaries(load_release_intent()),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ))
        else:
            print(json.dumps(cloud_build_evidence(), ensure_ascii=False, indent=2))
        return 0
    except DeploymentAdapterError as exc:
        print(f"EMERGENT_DEPLOYMENT_REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
