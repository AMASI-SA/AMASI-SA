"""Deterministic, package-local release identity contract (protocol v5).

The runtime identity is deliberately independent from the operational release
lease.  It contains no actor, timestamp, UUID, Git checkout lookup, or path to
the sibling frontend workspace.  Every value needed by the backend is embedded
in ``release_identity.json`` and bound by a deterministic release id.
"""
from __future__ import annotations

import hashlib
import importlib._bootstrap_external
import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:  # Support both ``python backend/...`` and package-style imports.
    from .frontend_build_identity import (
        CLIENT_ENV_ALLOWLIST,
        EXPECTED_NODE_VERSION,
        EXPECTED_YARN_VERSION,
        RETIREMENT_SERVICE_WORKER_BYTES,
        RETIREMENT_SERVICE_WORKER_PATHS,
        RETIREMENT_SERVICE_WORKER_SHA256,
        SCHEMA_VERSION as FRONTEND_BUILD_SCHEMA_VERSION,
        validate_frontend_reproducibility_proof,
    )
except ImportError:  # pragma: no cover - exercised by production flat imports
    from frontend_build_identity import (
        CLIENT_ENV_ALLOWLIST,
        EXPECTED_NODE_VERSION,
        EXPECTED_YARN_VERSION,
        RETIREMENT_SERVICE_WORKER_BYTES,
        RETIREMENT_SERVICE_WORKER_PATHS,
        RETIREMENT_SERVICE_WORKER_SHA256,
        SCHEMA_VERSION as FRONTEND_BUILD_SCHEMA_VERSION,
        validate_frontend_reproducibility_proof,
    )


RELEASE_PROTOCOL_VERSION = 5
RELEASE_IDENTITY_KIND = "mezan_runtime_release_identity_v5"
RELEASE_IDENTITY_SCHEMA_VERSION = 2
RELEASE_ID_PREFIX = "rg5-"
BACKEND_RUNTIME_SOURCE_SCOPE = "backend_runtime_package_v1"
RELEASE_CONTROL_SOURCE_SCOPE = "release_control_source_v1"
RELEASE_CONTROL_REQUIRED_DIRECTORIES = (
    ".github/workflows",
    "scripts",
)
RELEASE_CONTROL_OPTIONAL_DIRECTORIES = (".github/actions",)
BACKEND_RUNTIME_GENERATED_PATHS = frozenset({"release_identity.json"})
BACKEND_RUNTIME_CONFIGURATION_SIDECARS = frozenset({".env"})
BACKEND_RUNTIME_EXCLUDED_PREFIXES = ("tests/",)
CRITICAL_FILES = (
    "server.py",
    "release_identity.py",
    "release_protocol_v5.py",
    "frontend_build_identity.py",
    "integrations/qoyod_manual/routes.py",
    "integrations/qoyod_manual/send.py",
)

_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RELEASE_ID = re.compile(r"^rg5-[0-9a-f]{64}$")
_CORE_KEYS = frozenset({
    "kind",
    "schema_version",
    "protocol_version",
    "source_git_sha",
    "source_base_git_sha",
    "branch",
    "backend_runtime_source",
    "release_control_source",
    "critical_file_hashes",
    "frontend_build",
    "frontend_reproducibility",
})
_PAYLOAD_KEYS = _CORE_KEYS | {"release_id"}
_FRONTEND_BUILD_KEYS = frozenset({
    "schema_version",
    "git_sha",
    "source",
    "toolchain",
    "environment",
    "index",
    "entrypoints",
    "assets",
    "public_files",
    "artifact_tree_sha256",
    "build_meta",
})


class ReleaseProtocolV5Error(ValueError):
    """Raised when runtime release evidence is incomplete or inconsistent."""


def _is_exact_int(value: Any, expected: int) -> bool:
    """JSON protocol integers must not accept bools or integral floats."""
    return type(value) is int and value == expected


def exact_json_equal(value: Any, expected: Any) -> bool:
    """Compare JSON values without Python's bool/int/float coercions."""
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(value) == set(expected) and all(
            exact_json_equal(value[key], expected[key]) for key in expected
        )
    if isinstance(expected, list):
        return len(value) == len(expected) and all(
            exact_json_equal(left, right)
            for left, right in zip(value, expected)
        )
    return value == expected


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReleaseProtocolV5Error(
            f"release identity is not canonical JSON: {exc}"
        ) from exc


def _sha256_file(path: Path) -> str:
    try:
        file_status = path.lstat()
        if path.is_symlink() or not stat.S_ISREG(file_status.st_mode):
            raise OSError("not a regular non-symlink file")
        content = path.read_bytes()
    except OSError as exc:
        raise ReleaseProtocolV5Error(
            f"cannot hash packaged backend file {path}: {exc}"
        ) from exc
    return hashlib.sha256(content).hexdigest()


def _git_blob_oid(value: bytes) -> str:
    header = f"blob {len(value)}\0".encode("utf-8")
    return hashlib.sha1(
        header + value,
        usedforsecurity=False,
    ).hexdigest()


# Source manifest v2. The complete records are frozen from Git by the release
# adapter. Runtime validation consumes the same records without requiring Git.
_SOURCE_RECORD_KEYS = frozenset({
    "path", "mode", "git_blob", "bytes", "sha256",
})
_SOURCE_MANIFEST_KEYS = frozenset({
    "scope", "source_git_sha", "source_base_git_sha", "source_root_tree_oid",
    "source_base_root_tree_oid", "scope_tree_oid", "source_base_scope_tree_oid",
    "projection_tree_oid", "file_count", "files", "tree_sha256",
    "tombstone_count", "tombstones", "tombstones_sha256", "manifest_sha256",
    "added_count", "modified_count", "deleted_count",
})
_SOURCE_SUMMARY_KEYS = _SOURCE_MANIFEST_KEYS - {"files", "tombstones"}
_SOURCE_SUMMARY_CORE_KEYS = _SOURCE_SUMMARY_KEYS - {"manifest_sha256"}


def _validated_full_git_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _FULL_GIT_SHA.fullmatch(value):
        raise ReleaseProtocolV5Error(
            f"{label} must be 40 lowercase hexadecimal characters"
        )
    return value


def _reject_sensitive_source_path(value: str, label: str) -> None:
    parts = PurePosixPath(value).parts
    folded_parts = [part.casefold() for part in parts]
    basename = folded_parts[-1]
    sensitive = (
        any(part == ".git" for part in folded_parts)
        or any(part == ".env" or part.startswith(".env.") for part in folded_parts)
        or any(part in {"credentials", ".credentials", "token"} for part in folded_parts)
        or "credentials.json" in basename
        or "token.json" in basename
        or basename.endswith((".pem", ".key"))
    )
    if sensitive:
        raise ReleaseProtocolV5Error(
            f"{label} contains a forbidden sensitive path"
        )


def validate_source_path(value: Any, label: str) -> str:
    relative = _validated_relative_path(value, label)
    _reject_sensitive_source_path(relative, label)
    return relative


def backend_runtime_source_path_included(relative: Any) -> bool:
    """Return the single shared Backend package/source projection policy."""
    path = validate_source_path(relative, "Backend runtime source")
    return path not in BACKEND_RUNTIME_GENERATED_PATHS and not any(
        path == prefix.removesuffix("/") or path.startswith(prefix)
        for prefix in BACKEND_RUNTIME_EXCLUDED_PREFIXES
    )


def validated_source_git_mode(mode: int, *, label: str, path: str) -> str:
    """Validate restrictive filesystem permissions and return the Git category."""
    permissions = stat.S_IMODE(mode)
    executable = bool(permissions & 0o111)
    if (
        not permissions & stat.S_IRUSR
        or permissions & 0o7000
        or permissions & 0o022
        or (executable and not permissions & stat.S_IXUSR)
    ):
        raise ReleaseProtocolV5Error(
            f"{label} has unsafe file permissions: {path}: {permissions:04o}"
        )
    return "100755" if executable else "100644"


def _assert_safe_directory_mode(mode: int, *, label: str, path: str) -> None:
    permissions = stat.S_IMODE(mode)
    if (
        permissions & 0o022
        or permissions & 0o7000
        or permissions & 0o500 != 0o500
    ):
        raise ReleaseProtocolV5Error(
            f"{label} has unsafe directory permissions: {path}: {permissions:04o}"
        )


def _assert_safe_derived_cache_mode(
    mode: int,
    *,
    label: str,
    path: str,
) -> None:
    """Allow restrictive Python caches without accepting writable/executable code."""
    permissions = stat.S_IMODE(mode)
    if (
        not permissions & stat.S_IRUSR
        or permissions & 0o7000
        or permissions & 0o022
        or permissions & 0o111
    ):
        raise ReleaseProtocolV5Error(
            f"{label} has unsafe derived-cache permissions: "
            f"{path}: {permissions:04o}"
        )


def _assert_safe_configuration_sidecar_mode(
    mode: int,
    *,
    label: str,
) -> None:
    permissions = stat.S_IMODE(mode)
    if (
        not permissions & stat.S_IRUSR
        or permissions & 0o7000
        or permissions & 0o022
        or permissions & 0o111
    ):
        raise ReleaseProtocolV5Error(
            f"{label} configuration sidecar has unsafe permissions"
        )


def _source_record_from_path(
    path: Path,
    *,
    relative: str,
    label: str,
) -> dict[str, Any]:
    relative = validate_source_path(relative, label)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise OSError("not a regular file")
            git_mode = validated_source_git_mode(
                info.st_mode, label=label, path=relative
            )
            content = stream.read()
    except OSError as exc:
        raise ReleaseProtocolV5Error(
            f"cannot read {label} {relative}: {exc}"
        ) from exc
    return {
        "path": relative,
        "mode": git_mode,
        "git_blob": _git_blob_oid(content),
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _validated_source_record(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _SOURCE_RECORD_KEYS:
        raise ReleaseProtocolV5Error(f"{label} record is invalid")
    relative = validate_source_path(value.get("path"), label)
    mode = value.get("mode")
    if mode not in {"100644", "100755"}:
        raise ReleaseProtocolV5Error(f"{label} mode is invalid: {relative}")
    git_blob = value.get("git_blob")
    if not isinstance(git_blob, str) or not _FULL_GIT_SHA.fullmatch(git_blob):
        raise ReleaseProtocolV5Error(f"{label} Git blob is invalid: {relative}")
    size = value.get("bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ReleaseProtocolV5Error(f"{label} byte count is invalid: {relative}")
    digest = value.get("sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ReleaseProtocolV5Error(f"{label} SHA256 is invalid: {relative}")
    return {
        "path": relative,
        "mode": mode,
        "git_blob": git_blob,
        "bytes": size,
        "sha256": digest,
    }


def canonical_source_records_sha256(
    records: Iterable[dict[str, Any]],
) -> str:
    value = "".join(
        f"{row['git_blob']}\0{row['mode']}\0{row['sha256']}\0"
        f"{row['bytes']}\0{row['path']}\n"
        for row in records
    ).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def git_source_tree_oid(records: Iterable[dict[str, Any]]) -> str:
    root: dict[str, Any] = {"directories": {}, "files": {}}
    for record in records:
        parts = PurePosixPath(record["path"]).parts
        node = root
        for part in parts[:-1]:
            if part in node["files"]:
                raise ReleaseProtocolV5Error(
                    f"source path collides with a file: {record['path']}"
                )
            node = node["directories"].setdefault(
                part, {"directories": {}, "files": {}}
            )
        name = parts[-1]
        if name in node["directories"] or name in node["files"]:
            raise ReleaseProtocolV5Error(
                f"duplicate or colliding source path: {record['path']}"
            )
        node["files"][name] = record

    def encode_tree(node: dict[str, Any]) -> str:
        entries: list[tuple[bytes, str, str]] = []
        for name, child in node["directories"].items():
            entries.append((f"{name}/".encode(), "40000", encode_tree(child)))
        for name, record in node["files"].items():
            entries.append((name.encode(), record["mode"], record["git_blob"]))
        entries.sort(key=lambda row: row[0])
        body = b"".join(
            mode.encode() + b" " + sort_name.removesuffix(b"/") + b"\0"
            + bytes.fromhex(oid)
            for sort_name, mode, oid in entries
        )
        return hashlib.sha1(
            f"tree {len(body)}\0".encode() + body,
            usedforsecurity=False,
        ).hexdigest()

    return encode_tree(root)


def _canonical_manifest_sha256(summary: dict[str, Any]) -> str:
    core = {key: summary[key] for key in sorted(_SOURCE_SUMMARY_CORE_KEYS)}
    return hashlib.sha256(_canonical_json_bytes(core)).hexdigest()


def canonical_backend_runtime_source_sha256(
    records: Iterable[dict[str, Any]],
) -> str:
    return canonical_source_records_sha256(records)


def _normalize_source_records(
    value: Any,
    *,
    count: Any,
    label: str,
    allow_empty: bool,
) -> list[dict[str, Any]]:
    if (
        type(count) is not int
        or count < 0
        or (not allow_empty and count == 0)
        or not isinstance(value, list)
        or len(value) != count
    ):
        raise ReleaseProtocolV5Error(f"{label} count is invalid")
    records = [_validated_source_record(row, label=label) for row in value]
    if records != sorted(records, key=lambda row: row["path"]):
        raise ReleaseProtocolV5Error(f"{label} records are not sorted")
    paths = [row["path"] for row in records]
    if len(paths) != len(set(paths)):
        raise ReleaseProtocolV5Error(f"{label} records contain duplicate paths")
    return records


def _assert_source_path_set_is_unambiguous(
    records: Iterable[dict[str, Any]],
    *,
    label: str,
) -> None:
    """Reject portable-name and file/directory ambiguity across a delta."""
    folded_paths: dict[str, str] = {}
    folded_parts: list[tuple[tuple[str, ...], str]] = []
    for record in records:
        relative = record["path"]
        folded = unicodedata.normalize("NFC", relative).casefold()
        previous = folded_paths.setdefault(folded, relative)
        if previous != relative:
            raise ReleaseProtocolV5Error(
                f"{label} contains a case-folding path collision: "
                f"{previous}, {relative}"
            )
        folded_parts.append((tuple(folded.split("/")), relative))

    folded_parts.sort()
    for index, (parts, relative) in enumerate(folded_parts):
        for later_parts, later_relative in folded_parts[index + 1:]:
            if later_parts[:len(parts)] != parts:
                break
            if len(later_parts) > len(parts):
                raise ReleaseProtocolV5Error(
                    f"{label} contains a file/directory path collision: "
                    f"{relative}, {later_relative}"
                )


def _assert_records_in_scope(
    records: Iterable[dict[str, Any]],
    *,
    scope: str,
    label: str,
) -> None:
    for record in records:
        relative = record["path"]
        if PurePosixPath(relative).suffix.casefold() in {".pyc", ".pyo"}:
            raise ReleaseProtocolV5Error(
                f"{label} contains prepackaged bytecode"
            )
        if scope == BACKEND_RUNTIME_SOURCE_SCOPE:
            if not backend_runtime_source_path_included(relative):
                raise ReleaseProtocolV5Error(
                    f"{label} contains an excluded path: {relative}"
                )
        elif scope == RELEASE_CONTROL_SOURCE_SCOPE:
            if not (
                relative.startswith(".github/workflows/")
                or relative.startswith(".github/actions/")
                or relative.startswith("scripts/")
            ):
                raise ReleaseProtocolV5Error(
                    f"{label} contains a path outside its scope: {relative}"
                )
        else:
            raise ReleaseProtocolV5Error(f"{label} scope is unsupported")


def validate_source_manifest(
    value: Any,
    *,
    expected_scope: str,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _SOURCE_MANIFEST_KEYS:
        raise ReleaseProtocolV5Error(f"{label} manifest fields are invalid")
    if value.get("scope") != expected_scope:
        raise ReleaseProtocolV5Error(f"{label} scope is invalid")
    source_git_sha = _validated_full_git_sha(
        value.get("source_git_sha"), f"{label} source_git_sha"
    )
    source_base_git_sha = _validated_full_git_sha(
        value.get("source_base_git_sha"), f"{label} source_base_git_sha"
    )
    source_root_tree_oid = _validated_full_git_sha(
        value.get("source_root_tree_oid"), f"{label} source_root_tree_oid"
    )
    source_base_root_tree_oid = _validated_full_git_sha(
        value.get("source_base_root_tree_oid"),
        f"{label} source_base_root_tree_oid",
    )
    scope_tree_oid = _validated_full_git_sha(
        value.get("scope_tree_oid"), f"{label} scope_tree_oid"
    )
    source_base_scope_tree_oid = _validated_full_git_sha(
        value.get("source_base_scope_tree_oid"),
        f"{label} source_base_scope_tree_oid",
    )
    files = _normalize_source_records(
        value.get("files"), count=value.get("file_count"),
        label=f"{label} files", allow_empty=False,
    )
    tombstones = _normalize_source_records(
        value.get("tombstones"), count=value.get("tombstone_count"),
        label=f"{label} tombstones", allow_empty=True,
    )
    overlap = sorted(
        {row["path"] for row in files} & {row["path"] for row in tombstones}
    )
    if overlap:
        raise ReleaseProtocolV5Error(
            f"{label} files overlap tombstones: {', '.join(overlap)}"
        )
    counts = {}
    for name in ("added_count", "modified_count", "deleted_count"):
        count = value.get(name)
        if type(count) is not int or count < 0:
            raise ReleaseProtocolV5Error(f"{label} {name} is invalid")
        counts[name] = count
    if counts["deleted_count"] != len(tombstones):
        raise ReleaseProtocolV5Error(
            f"{label} deleted_count does not match tombstones"
        )
    _assert_source_path_set_is_unambiguous(
        (*files, *tombstones), label=label
    )
    _assert_records_in_scope(
        (*files, *tombstones), scope=expected_scope, label=label
    )
    tree_sha256 = canonical_source_records_sha256(files)
    projection_tree_oid = git_source_tree_oid(files)
    tombstones_sha256 = canonical_source_records_sha256(tombstones)
    if value.get("tree_sha256") != tree_sha256:
        raise ReleaseProtocolV5Error(f"{label} tree SHA256 is not canonical")
    if value.get("tombstones_sha256") != tombstones_sha256:
        raise ReleaseProtocolV5Error(
            f"{label} tombstones SHA256 is not canonical"
        )
    if value.get("projection_tree_oid") != projection_tree_oid:
        raise ReleaseProtocolV5Error(
            f"{label} projection tree OID is not canonical"
        )
    normalized = {
        "scope": expected_scope,
        "source_git_sha": source_git_sha,
        "source_base_git_sha": source_base_git_sha,
        "source_root_tree_oid": source_root_tree_oid,
        "source_base_root_tree_oid": source_base_root_tree_oid,
        "scope_tree_oid": scope_tree_oid,
        "source_base_scope_tree_oid": source_base_scope_tree_oid,
        "projection_tree_oid": projection_tree_oid,
        "file_count": len(files),
        "files": files,
        "tree_sha256": tree_sha256,
        "tombstone_count": len(tombstones),
        "tombstones": tombstones,
        "tombstones_sha256": tombstones_sha256,
        **counts,
    }
    expected_manifest_sha256 = _canonical_manifest_sha256(normalized)
    if value.get("manifest_sha256") != expected_manifest_sha256:
        raise ReleaseProtocolV5Error(f"{label} manifest SHA256 is not canonical")
    return {**normalized, "manifest_sha256": expected_manifest_sha256}


def source_manifest_summary(
    value: Any,
    *,
    expected_scope: str,
    label: str,
) -> dict[str, Any]:
    manifest = validate_source_manifest(
        value, expected_scope=expected_scope, label=label
    )
    return {key: manifest[key] for key in sorted(_SOURCE_SUMMARY_KEYS)}


def validate_source_manifest_summary(
    value: Any,
    *,
    expected_scope: str,
    label: str,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _SOURCE_SUMMARY_KEYS:
        raise ReleaseProtocolV5Error(f"{label} summary fields are invalid")
    if value.get("scope") != expected_scope:
        raise ReleaseProtocolV5Error(f"{label} summary scope is invalid")
    result = dict(value)
    result["source_git_sha"] = _validated_full_git_sha(
        value.get("source_git_sha"), f"{label} source_git_sha"
    )
    result["source_base_git_sha"] = _validated_full_git_sha(
        value.get("source_base_git_sha"), f"{label} source_base_git_sha"
    )
    result["source_root_tree_oid"] = _validated_full_git_sha(
        value.get("source_root_tree_oid"), f"{label} source_root_tree_oid"
    )
    result["source_base_root_tree_oid"] = _validated_full_git_sha(
        value.get("source_base_root_tree_oid"),
        f"{label} source_base_root_tree_oid",
    )
    result["scope_tree_oid"] = _validated_full_git_sha(
        value.get("scope_tree_oid"), f"{label} scope_tree_oid"
    )
    result["projection_tree_oid"] = _validated_full_git_sha(
        value.get("projection_tree_oid"), f"{label} projection_tree_oid"
    )
    result["source_base_scope_tree_oid"] = _validated_full_git_sha(
        value.get("source_base_scope_tree_oid"),
        f"{label} source_base_scope_tree_oid",
    )
    for name in (
        "file_count", "tombstone_count", "added_count", "modified_count",
        "deleted_count",
    ):
        if type(value.get(name)) is not int or value[name] < 0:
            raise ReleaseProtocolV5Error(f"{label} {name} is invalid")
    if value["file_count"] == 0:
        raise ReleaseProtocolV5Error(f"{label} file_count is invalid")
    if value["deleted_count"] != value["tombstone_count"]:
        raise ReleaseProtocolV5Error(
            f"{label} deleted_count does not match tombstone_count"
        )
    for name in ("tree_sha256", "tombstones_sha256", "manifest_sha256"):
        digest = value.get(name)
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ReleaseProtocolV5Error(f"{label} {name} is invalid")
    if value["manifest_sha256"] != _canonical_manifest_sha256(result):
        raise ReleaseProtocolV5Error(f"{label} manifest_sha256 is not canonical")
    return {key: result[key] for key in sorted(_SOURCE_SUMMARY_KEYS)}


def build_source_manifest(
    *,
    scope: str,
    source_git_sha: str,
    source_base_git_sha: str,
    source_root_tree_oid: str,
    source_base_root_tree_oid: str,
    scope_tree_oid: str | None = None,
    source_base_scope_tree_oid: str | None = None,
    files: Iterable[dict[str, Any]],
    base_files: Iterable[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    current = sorted(
        (_validated_source_record(row, label=f"{label} files") for row in files),
        key=lambda row: row["path"],
    )
    baseline = sorted(
        (_validated_source_record(row, label=f"{label} base files") for row in base_files),
        key=lambda row: row["path"],
    )
    baseline_paths = [row["path"] for row in baseline]
    if len(baseline_paths) != len(set(baseline_paths)):
        raise ReleaseProtocolV5Error(
            f"{label} base files contain duplicate paths"
        )
    _assert_source_path_set_is_unambiguous(baseline, label=f"{label} base")
    _assert_records_in_scope(baseline, scope=scope, label=f"{label} base")
    if scope_tree_oid is None or source_base_scope_tree_oid is None:
        raise ReleaseProtocolV5Error(
            f"{label} current and base scope tree OIDs are required"
        )
    current_by_path = {row["path"]: row for row in current}
    baseline_by_path = {row["path"]: row for row in baseline}
    deleted = [
        row for row in baseline if row["path"] not in current_by_path
    ]
    added_count = sum(
        row["path"] not in baseline_by_path for row in current
    )
    modified_count = sum(
        row["path"] in baseline_by_path
        and row != baseline_by_path[row["path"]]
        for row in current
    )
    payload = {
        "scope": scope,
        "source_git_sha": source_git_sha,
        "source_base_git_sha": source_base_git_sha,
        "source_root_tree_oid": source_root_tree_oid,
        "source_base_root_tree_oid": source_base_root_tree_oid,
        "scope_tree_oid": scope_tree_oid,
        "source_base_scope_tree_oid": source_base_scope_tree_oid,
        "projection_tree_oid": git_source_tree_oid(current),
        "file_count": len(current),
        "files": current,
        "tree_sha256": canonical_source_records_sha256(current),
        "tombstone_count": len(deleted),
        "tombstones": deleted,
        "tombstones_sha256": canonical_source_records_sha256(deleted),
        "added_count": added_count,
        "modified_count": modified_count,
        "deleted_count": len(deleted),
    }
    payload["manifest_sha256"] = _canonical_manifest_sha256(payload)
    return validate_source_manifest(payload, expected_scope=scope, label=label)


def _validate_derived_bytecode_directory(path: Path, *, label: str) -> None:
    cache_tag = sys.implementation.cache_tag
    if not cache_tag:
        raise ReleaseProtocolV5Error(f"{label} cannot identify Python cache tag")
    name_pattern = re.compile(
        rf"^(?P<stem>.+)\.{re.escape(cache_tag)}"
        r"(?:\.opt-(?P<optimize>[012]))?\.pyc$"
    )
    for cached in sorted(path.iterdir(), key=lambda item: item.name):
        relative = cached.relative_to(path.parent.parent).as_posix()
        validate_source_path(relative, label)
        info = cached.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ReleaseProtocolV5Error(
                f"{label} bytecode cache contains an unsupported entry: {cached}"
            )
        _assert_safe_derived_cache_mode(
            info.st_mode,
            label=label,
            path=os.fspath(cached),
        )
        match = name_pattern.fullmatch(cached.name)
        if match is None:
            raise ReleaseProtocolV5Error(
                f"{label} contains sourceless bytecode: {cached}"
            )
        source = path.parent / f"{match.group('stem')}.py"
        try:
            source_info = source.lstat()
            if source.is_symlink() or not stat.S_ISREG(source_info.st_mode):
                raise OSError("source is not a regular non-symlink file")
            validated_source_git_mode(
                source_info.st_mode,
                label=label,
                path=os.fspath(source),
            )
            source_bytes = source.read_bytes()
            code = compile(
                source_bytes,
                os.fspath(source.resolve(strict=True)),
                "exec",
                dont_inherit=True,
                optimize=int(match.group("optimize") or 0),
            )
            expected = importlib._bootstrap_external._code_to_timestamp_pyc(
                code,
                int(source_info.st_mtime),
                len(source_bytes),
            )
            actual = cached.read_bytes()
        except (OSError, SyntaxError, ValueError) as exc:
            raise ReleaseProtocolV5Error(
                f"{label} cannot validate derived bytecode {cached}: {exc}"
            ) from exc
        if actual != expected:
            raise ReleaseProtocolV5Error(
                f"{label} contains non-derived bytecode: {cached}"
            )


def _collect_source_records(
    *,
    root: Path,
    scan_roots: Iterable[Path],
    label: str,
    relative_to: Path,
    excluded_prefixes: tuple[str, ...] = (),
    generated_paths: frozenset[str] = frozenset(),
    configuration_sidecars: frozenset[str] = frozenset(),
    allow_derived_bytecode: bool = False,
) -> list[dict[str, Any]]:
    try:
        root_absolute = root.absolute()
        root_resolved = root.resolve(strict=True)
        root_info = root.lstat()
    except OSError as exc:
        raise ReleaseProtocolV5Error(f"{label} root is unavailable: {exc}") from exc
    if root_absolute != root_resolved:
        raise ReleaseProtocolV5Error(f"{label} root has a symlinked parent")
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ReleaseProtocolV5Error(f"{label} root must be a real directory")
    _assert_safe_directory_mode(root_info.st_mode, label=label, path=os.fspath(root))
    records: list[dict[str, Any]] = []

    def visit(directory: Path, *, excluded: bool = False) -> None:
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            relative = path.relative_to(relative_to).as_posix()
            info = path.lstat()
            if relative in configuration_sidecars:
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise ReleaseProtocolV5Error(
                        f"{label} configuration sidecar must be a regular file"
                    )
                _assert_safe_configuration_sidecar_mode(
                    info.st_mode, label=label
                )
                continue
            validate_source_path(relative, label)
            if stat.S_ISLNK(info.st_mode):
                raise ReleaseProtocolV5Error(
                    f"{label} must not contain symlinks: {relative}"
                )
            next_excluded = excluded or any(
                relative == prefix.removesuffix("/") or relative.startswith(prefix)
                for prefix in excluded_prefixes
            )
            if stat.S_ISDIR(info.st_mode):
                _assert_safe_directory_mode(
                    info.st_mode, label=label, path=relative
                )
                if path.name == "__pycache__":
                    if not allow_derived_bytecode:
                        raise ReleaseProtocolV5Error(
                            f"{label} contains prepackaged bytecode: {relative}"
                        )
                    _validate_derived_bytecode_directory(path, label=label)
                else:
                    visit(path, excluded=next_excluded)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ReleaseProtocolV5Error(
                    f"{label} contains an unsupported entry: {relative}"
                )
            validated_source_git_mode(info.st_mode, label=label, path=relative)
            if path.suffix.casefold() in {".pyc", ".pyo"}:
                raise ReleaseProtocolV5Error(
                    f"{label} contains prepackaged bytecode: {relative}"
                )
            if next_excluded:
                continue
            if relative not in generated_paths:
                records.append(_source_record_from_path(
                    path, relative=relative, label=label
                ))

    for scan_root in scan_roots:
        info = scan_root.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ReleaseProtocolV5Error(
                f"{label} directory must be real: {scan_root}"
            )
        _assert_safe_directory_mode(
            info.st_mode, label=label, path=os.fspath(scan_root)
        )
        visit(scan_root)
    return sorted(records, key=lambda row: row["path"])


def _backend_runtime_source_records(
    backend_root: Path,
    *,
    allow_derived_bytecode: bool,
) -> list[dict[str, Any]]:
    return _collect_source_records(
        root=backend_root,
        scan_roots=(backend_root,),
        label="Backend runtime source",
        relative_to=backend_root,
        excluded_prefixes=BACKEND_RUNTIME_EXCLUDED_PREFIXES,
        generated_paths=BACKEND_RUNTIME_GENERATED_PATHS,
        configuration_sidecars=BACKEND_RUNTIME_CONFIGURATION_SIDECARS,
        allow_derived_bytecode=allow_derived_bytecode,
    )


def build_backend_runtime_source_manifest(
    *,
    backend_root: Path,
    source_git_sha: str,
    source_base_git_sha: str,
    source_root_tree_oid: str,
    source_base_root_tree_oid: str,
    scope_tree_oid: str,
    source_base_scope_tree_oid: str,
    base_files: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    files = _backend_runtime_source_records(
        backend_root, allow_derived_bytecode=False
    )
    return build_source_manifest(
        scope=BACKEND_RUNTIME_SOURCE_SCOPE,
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
        source_root_tree_oid=source_root_tree_oid,
        source_base_root_tree_oid=source_base_root_tree_oid,
        scope_tree_oid=scope_tree_oid,
        source_base_scope_tree_oid=source_base_scope_tree_oid,
        files=files,
        base_files=base_files,
        label="Backend runtime source",
    )


def _assert_source_records_match(
    *, expected: list[dict[str, Any]], actual: list[dict[str, Any]], label: str,
) -> None:
    expected_paths = [row["path"] for row in expected]
    actual_paths = [row["path"] for row in actual]
    if actual_paths != expected_paths:
        missing = sorted(set(expected_paths) - set(actual_paths))
        unreviewed = sorted(set(actual_paths) - set(expected_paths))
        raise ReleaseProtocolV5Error(
            f"{label} membership differs from the identity "
            f"(missing={','.join(missing) or 'none'}; "
            f"unreviewed={','.join(unreviewed) or 'none'})"
        )
    for expected_record, actual_record in zip(expected, actual):
        if expected_record != actual_record:
            mismatches = [
                name for name in ("mode", "git_blob", "bytes", "sha256")
                if expected_record[name] != actual_record[name]
            ]
            raise ReleaseProtocolV5Error(
                f"{label} mismatch for {expected_record['path']}: "
                + ", ".join(mismatches)
            )


def validate_backend_runtime_source_manifest(
    value: Any,
    *,
    backend_root: Path,
    source_git_sha: str | None = None,
    source_base_git_sha: str | None = None,
    allow_derived_bytecode: bool = True,
) -> dict[str, Any]:
    manifest = validate_source_manifest(
        value, expected_scope=BACKEND_RUNTIME_SOURCE_SCOPE,
        label="Backend runtime source",
    )
    if source_git_sha is not None and manifest["source_git_sha"] != source_git_sha:
        raise ReleaseProtocolV5Error(
            "Backend runtime source is not bound to source_git_sha"
        )
    if source_base_git_sha is not None and (
        manifest["source_base_git_sha"] != source_base_git_sha
    ):
        raise ReleaseProtocolV5Error(
            "Backend runtime source is not bound to source_base_git_sha"
        )
    _assert_source_records_match(
        expected=manifest["files"],
        actual=_backend_runtime_source_records(
            backend_root, allow_derived_bytecode=allow_derived_bytecode
        ),
        label="Backend runtime source",
    )
    return manifest


def _release_control_source_records(
    repo_root: Path,
    *,
    allow_missing_github: bool = False,
) -> list[dict[str, Any]]:
    github_root = repo_root / ".github"
    if github_root.is_symlink():
        raise ReleaseProtocolV5Error(
            "Release control source .github root must not be a symlink"
        )
    github_present = github_root.exists()
    if github_present:
        github_info = github_root.lstat()
        if stat.S_ISLNK(github_info.st_mode) or not stat.S_ISDIR(github_info.st_mode):
            raise ReleaseProtocolV5Error(
                "Release control source .github root must be a real directory"
            )
    elif not allow_missing_github:
        raise ReleaseProtocolV5Error(
            "Release control source .github root is missing"
        )
    scan_roots = [repo_root / "scripts"]
    if github_present:
        scan_roots.append(repo_root / ".github" / "workflows")
        actions = repo_root / ".github" / "actions"
        if actions.is_symlink():
            raise ReleaseProtocolV5Error(
                "Release control source actions root must not be a symlink"
            )
        if actions.exists():
            scan_roots.append(actions)
    return _collect_source_records(
        root=repo_root,
        scan_roots=scan_roots,
        label="Release control source",
        relative_to=repo_root,
    )


def build_release_control_source_manifest(
    *,
    repo_root: Path,
    source_git_sha: str,
    source_base_git_sha: str,
    source_root_tree_oid: str,
    source_base_root_tree_oid: str,
    scope_tree_oid: str,
    source_base_scope_tree_oid: str,
    base_files: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    return build_source_manifest(
        scope=RELEASE_CONTROL_SOURCE_SCOPE,
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
        source_root_tree_oid=source_root_tree_oid,
        source_base_root_tree_oid=source_base_root_tree_oid,
        scope_tree_oid=scope_tree_oid,
        source_base_scope_tree_oid=source_base_scope_tree_oid,
        files=_release_control_source_records(repo_root),
        base_files=base_files,
        label="Release control source",
    )


def validate_release_control_source_manifest(
    value: Any,
    *,
    repo_root: Path | None = None,
    source_git_sha: str | None = None,
    source_base_git_sha: str | None = None,
    allow_missing_github: bool = False,
) -> dict[str, Any]:
    manifest = validate_source_manifest(
        value, expected_scope=RELEASE_CONTROL_SOURCE_SCOPE,
        label="Release control source",
    )
    if source_git_sha is not None and manifest["source_git_sha"] != source_git_sha:
        raise ReleaseProtocolV5Error(
            "Release control source is not bound to source_git_sha"
        )
    if source_base_git_sha is not None and (
        manifest["source_base_git_sha"] != source_base_git_sha
    ):
        raise ReleaseProtocolV5Error(
            "Release control source is not bound to source_base_git_sha"
        )
    if repo_root is not None:
        github_present = (repo_root / ".github").exists()
        expected = manifest["files"]
        if allow_missing_github and not github_present:
            expected = [
                row for row in expected if row["path"].startswith("scripts/")
            ]
        _assert_source_records_match(
            expected=expected,
            actual=_release_control_source_records(
                repo_root, allow_missing_github=allow_missing_github
            ),
            label="Release control source",
        )
    return manifest


def packaged_critical_file_hashes(
    *,
    backend_root: Path,
    critical_files: Iterable[str] = CRITICAL_FILES,
) -> dict[str, str]:
    """Hash the exact backend-package bytes covered by protocol v5."""
    names = tuple(critical_files)
    if len(names) != len(set(names)):
        raise ReleaseProtocolV5Error("critical file list contains duplicates")
    for relative in names:
        _validated_relative_path(relative, "critical backend file")
    return {
        relative: _sha256_file(backend_root / relative)
        for relative in names
    }


def canonical_identity_core(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the exact deterministic core used to derive ``release_id``."""
    if not isinstance(payload, dict):
        raise ReleaseProtocolV5Error("release identity must be a JSON object")
    missing = _CORE_KEYS - payload.keys()
    if missing:
        raise ReleaseProtocolV5Error(
            "release identity core fields are missing: "
            + ", ".join(sorted(missing))
        )
    return {key: payload[key] for key in sorted(_CORE_KEYS)}


def deterministic_release_id(core: dict[str, Any]) -> str:
    """Derive the stable v5 id from canonical compact sorted JSON bytes."""
    canonical = canonical_identity_core(core)
    digest = hashlib.sha256(_canonical_json_bytes(canonical)).hexdigest()
    return f"{RELEASE_ID_PREFIX}{digest}"


def _validated_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ReleaseProtocolV5Error(f"{label} path is invalid")
    pure = PurePosixPath(value)
    if (
        not value
        or value == "."
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or unicodedata.normalize("NFC", value) != value
        or value.startswith("/")
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or pure.as_posix() != value
    ):
        raise ReleaseProtocolV5Error(f"{label} path is invalid: {value}")
    return value


def _validated_file_record(
    value: Any,
    *,
    label: str,
    expected_path: str | None = None,
    require_nonempty: bool = False,
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"path", "bytes", "sha256"}:
        raise ReleaseProtocolV5Error(f"{label} record is invalid")
    path = _validated_relative_path(value.get("path"), label)
    if expected_path is not None and path != expected_path:
        raise ReleaseProtocolV5Error(
            f"{label} path must be {expected_path}; found {path}"
        )
    size = value.get("bytes")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or (require_nonempty and size == 0)
    ):
        raise ReleaseProtocolV5Error(f"{label} byte count is invalid")
    digest = value.get("sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ReleaseProtocolV5Error(f"{label} SHA256 is invalid")
    return {"path": path, "bytes": size, "sha256": digest}


def _validated_file_records(value: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ReleaseProtocolV5Error(f"{label} records are missing")
    records = [
        _validated_file_record(item, label=label)
        for item in value
    ]
    if records != sorted(records, key=lambda row: row["path"]):
        raise ReleaseProtocolV5Error(f"{label} records are not sorted")
    paths = [record["path"] for record in records]
    if len(paths) != len(set(paths)):
        raise ReleaseProtocolV5Error(f"{label} records contain duplicate paths")
    return records


def _validated_frontend_source(value: Any) -> dict[str, Any]:
    expected_keys = {"scope", "git_tree_oid", "file_count", "tree_sha256"}
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ReleaseProtocolV5Error("embedded frontend source proof is invalid")
    if value.get("scope") != "git_head_frontend_tree_v1":
        raise ReleaseProtocolV5Error("embedded frontend source scope is invalid")
    git_tree_oid = value.get("git_tree_oid")
    if not isinstance(git_tree_oid, str) or not _FULL_GIT_SHA.fullmatch(
        git_tree_oid
    ):
        raise ReleaseProtocolV5Error("embedded frontend Git tree is invalid")
    file_count = value.get("file_count")
    if (
        isinstance(file_count, bool)
        or not isinstance(file_count, int)
        or file_count <= 0
    ):
        raise ReleaseProtocolV5Error("embedded frontend source count is invalid")
    tree_sha256 = value.get("tree_sha256")
    if not isinstance(tree_sha256, str) or not _SHA256.fullmatch(tree_sha256):
        raise ReleaseProtocolV5Error("embedded frontend source SHA256 is invalid")
    return {
        "scope": value["scope"],
        "git_tree_oid": git_tree_oid,
        "file_count": file_count,
        "tree_sha256": tree_sha256,
    }


def _validated_frontend_environment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseProtocolV5Error("embedded frontend environment is invalid")
    raw_values = value.get("values")
    if not isinstance(raw_values, dict):
        raise ReleaseProtocolV5Error(
            "embedded frontend environment values are invalid"
        )
    governed_values: dict[str, dict[str, Any]] = {}
    for name in CLIENT_ENV_ALLOWLIST:
        record = raw_values.get(name)
        if not isinstance(record, dict) or set(record) != {"present", "sha256"}:
            raise ReleaseProtocolV5Error(
                f"embedded frontend environment record is invalid: {name}"
            )
        present = record.get("present")
        digest = record.get("sha256")
        if not isinstance(present, bool):
            raise ReleaseProtocolV5Error(
                f"embedded frontend environment presence is invalid: {name}"
            )
        if present:
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                raise ReleaseProtocolV5Error(
                    f"embedded frontend environment SHA256 is invalid: {name}"
                )
        elif digest is not None:
            raise ReleaseProtocolV5Error(
                f"absent frontend environment value has a SHA256: {name}"
            )
        governed_values[name] = {"present": present, "sha256": digest}
    expected = {
        "mode": "production",
        "effective": {
            "NODE_ENV": "production",
            "VITE_USER_NODE_ENV_present": False,
            "VITE_prefixed_keys": [],
        },
        "allowed_client_keys": list(CLIENT_ENV_ALLOWLIST),
        "values": governed_values,
    }
    if not exact_json_equal(value, expected):
        raise ReleaseProtocolV5Error(
            "embedded frontend governed environment is invalid"
        )
    return expected


def _validated_frontend_build(
    *, source_git_sha: str, value: Any
) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _FRONTEND_BUILD_KEYS:
        raise ReleaseProtocolV5Error(
            "embedded frontend build fields are invalid"
        )
    if not _is_exact_int(
        value.get("schema_version"), FRONTEND_BUILD_SCHEMA_VERSION
    ):
        raise ReleaseProtocolV5Error("embedded frontend build schema is invalid")
    if value.get("git_sha") != source_git_sha:
        raise ReleaseProtocolV5Error(
            "embedded frontend build is not bound to source_git_sha"
        )
    source = _validated_frontend_source(value.get("source"))

    toolchain = value.get("toolchain")
    if not isinstance(toolchain, dict) or set(toolchain) != {
        "node", "yarn", "vite"
    }:
        raise ReleaseProtocolV5Error("embedded frontend toolchain is invalid")
    if toolchain.get("node") != EXPECTED_NODE_VERSION:
        raise ReleaseProtocolV5Error("embedded frontend Node version is invalid")
    if toolchain.get("yarn") != EXPECTED_YARN_VERSION:
        raise ReleaseProtocolV5Error("embedded frontend Yarn version is invalid")
    vite = toolchain.get("vite")
    if (
        not isinstance(vite, str)
        or not vite
        or vite != vite.strip()
        or len(vite) > 64
    ):
        raise ReleaseProtocolV5Error("embedded frontend Vite version is invalid")
    environment = _validated_frontend_environment(value.get("environment"))

    index = _validated_file_record(
        value.get("index"),
        label="embedded frontend index",
        expected_path="index.html",
        require_nonempty=True,
    )
    build_meta = _validated_file_record(
        value.get("build_meta"),
        label="embedded frontend build metadata",
        expected_path="build-meta.json",
        require_nonempty=True,
    )
    public_files = _validated_file_records(
        value.get("public_files"), label="embedded frontend public file"
    )
    public_by_path = {record["path"]: record for record in public_files}
    if public_by_path.get("index.html") != index:
        raise ReleaseProtocolV5Error(
            "embedded frontend index does not match public files"
        )
    if "build-meta.json" in public_by_path:
        raise ReleaseProtocolV5Error(
            "embedded frontend build metadata must be outside artifact records"
        )

    assets = _validated_file_records(
        value.get("assets"), label="embedded frontend asset"
    )
    expected_assets = [
        record for record in public_files
        if record["path"].startswith("assets/")
    ]
    if assets != expected_assets:
        raise ReleaseProtocolV5Error(
            "embedded frontend assets do not match public files"
        )
    entrypoints = _validated_file_records(
        value.get("entrypoints"), label="embedded frontend entrypoint"
    )
    assets_by_path = {record["path"]: record for record in assets}
    if any(
        entrypoint["path"] not in assets_by_path
        or assets_by_path[entrypoint["path"]] != entrypoint
        or PurePosixPath(entrypoint["path"]).suffix not in {".js", ".css"}
        for entrypoint in entrypoints
    ):
        raise ReleaseProtocolV5Error(
            "embedded frontend entrypoints do not match assets"
        )
    if not any(record["path"].endswith(".js") for record in entrypoints):
        raise ReleaseProtocolV5Error(
            "embedded frontend has no JavaScript entrypoint"
        )

    for worker_path in RETIREMENT_SERVICE_WORKER_PATHS:
        worker = public_by_path.get(worker_path)
        if (
            worker is None
            or worker["bytes"] != RETIREMENT_SERVICE_WORKER_BYTES
            or worker["sha256"] != RETIREMENT_SERVICE_WORKER_SHA256
        ):
            raise ReleaseProtocolV5Error(
                "embedded frontend retirement service worker is invalid: "
                f"{worker_path}"
            )

    artifact_tree = value.get("artifact_tree_sha256")
    if not isinstance(artifact_tree, str) or not _SHA256.fullmatch(
        artifact_tree
    ):
        raise ReleaseProtocolV5Error(
            "embedded frontend artifact tree SHA256 is invalid"
        )
    return {
        "schema_version": FRONTEND_BUILD_SCHEMA_VERSION,
        "git_sha": source_git_sha,
        "source": source,
        "toolchain": {
            "node": EXPECTED_NODE_VERSION,
            "yarn": EXPECTED_YARN_VERSION,
            "vite": vite,
        },
        "environment": environment,
        "index": index,
        "entrypoints": entrypoints,
        "assets": assets,
        "public_files": public_files,
        "artifact_tree_sha256": artifact_tree,
        "build_meta": build_meta,
    }


def _validate_frontend_evidence(
    *,
    source_git_sha: str,
    frontend_build: Any,
    frontend_reproducibility: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    validated_build = _validated_frontend_build(
        source_git_sha=source_git_sha,
        value=frontend_build,
    )
    try:
        validated_proof = validate_frontend_reproducibility_proof(
            frontend_build=validated_build,
            proof=frontend_reproducibility,
        )
    except Exception as exc:
        raise ReleaseProtocolV5Error(
            f"embedded frontend reproducibility proof is invalid: {exc}"
        ) from exc
    return validated_build, validated_proof


def validate_runtime_release_identity(
    payload: Any,
    *,
    backend_root: Path,
    critical_files: Iterable[str] = CRITICAL_FILES,
    allow_derived_bytecode: bool = True,
) -> dict[str, Any]:
    """Validate deterministic identity against this backend package.

    This function intentionally performs no Git lookup and reads no sibling
    frontend directory. The filesystem input is the complete governed package
    inside ``backend_root``, whose exact membership and hashes are embedded in
    the identity.
    """
    if not isinstance(payload, dict):
        raise ReleaseProtocolV5Error("release identity must be a JSON object")
    if set(payload) != _PAYLOAD_KEYS:
        raise ReleaseProtocolV5Error(
            "release identity fields do not match protocol v5"
        )
    if payload.get("kind") != RELEASE_IDENTITY_KIND:
        raise ReleaseProtocolV5Error("release identity kind is invalid")
    if not _is_exact_int(
        payload.get("schema_version"), RELEASE_IDENTITY_SCHEMA_VERSION
    ):
        raise ReleaseProtocolV5Error("release identity schema is invalid")
    if not _is_exact_int(
        payload.get("protocol_version"), RELEASE_PROTOCOL_VERSION
    ):
        raise ReleaseProtocolV5Error("release protocol version is invalid")

    source_git_sha = _validated_full_git_sha(
        payload.get("source_git_sha"), "source_git_sha"
    )
    source_base_git_sha = _validated_full_git_sha(
        payload.get("source_base_git_sha"), "source_base_git_sha"
    )
    branch = payload.get("branch")
    if not isinstance(branch, str) or not branch.strip():
        raise ReleaseProtocolV5Error("release branch is missing")
    if branch != branch.strip() or "\x00" in branch or len(branch) > 255:
        raise ReleaseProtocolV5Error("release branch is invalid")

    backend_runtime_source = validate_backend_runtime_source_manifest(
        payload.get("backend_runtime_source"),
        backend_root=backend_root,
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
        allow_derived_bytecode=allow_derived_bytecode,
    )
    release_control_source = validate_source_manifest_summary(
        payload.get("release_control_source"),
        expected_scope=RELEASE_CONTROL_SOURCE_SCOPE,
        label="Release control source",
    )
    if (
        release_control_source["source_git_sha"] != source_git_sha
        or release_control_source["source_base_git_sha"] != source_base_git_sha
    ):
        raise ReleaseProtocolV5Error(
            "Release control source summary is not bound to identity provenance"
        )

    critical_names = tuple(critical_files)
    expected_hashes = payload.get("critical_file_hashes")
    if not isinstance(expected_hashes, dict):
        raise ReleaseProtocolV5Error("critical_file_hashes is invalid")
    if set(expected_hashes) != set(critical_names):
        raise ReleaseProtocolV5Error(
            "critical_file_hashes does not contain the exact critical set"
        )
    if any(
        not isinstance(expected_hashes[name], str)
        or not _SHA256.fullmatch(expected_hashes[name])
        for name in critical_names
    ):
        raise ReleaseProtocolV5Error("critical_file_hashes contains invalid SHA256")
    actual_hashes = packaged_critical_file_hashes(
        backend_root=backend_root,
        critical_files=critical_names,
    )
    if expected_hashes != actual_hashes:
        raise ReleaseProtocolV5Error(
            "critical_file_hashes does not match packaged backend bytes"
        )
    source_by_path = {
        record["path"]: record
        for record in backend_runtime_source["files"]
    }
    if any(
        source_by_path.get(name, {}).get("sha256") != actual_hashes[name]
        for name in critical_names
    ):
        raise ReleaseProtocolV5Error(
            "critical_file_hashes does not match Backend runtime source"
        )

    frontend_build, frontend_reproducibility = _validate_frontend_evidence(
        source_git_sha=source_git_sha,
        frontend_build=payload.get("frontend_build"),
        frontend_reproducibility=payload.get("frontend_reproducibility"),
    )
    core = canonical_identity_core(payload)
    release_id = payload.get("release_id")
    if not isinstance(release_id, str) or not _RELEASE_ID.fullmatch(release_id):
        raise ReleaseProtocolV5Error("release_id is invalid")
    expected_release_id = deterministic_release_id(core)
    if release_id != expected_release_id:
        raise ReleaseProtocolV5Error(
            "release_id does not match the deterministic identity core"
        )

    return {
        **core,
        "release_id": expected_release_id,
        "backend_runtime_source": backend_runtime_source,
        "release_control_source": release_control_source,
        "critical_file_hashes": actual_hashes,
        "frontend_build": frontend_build,
        "frontend_reproducibility": frontend_reproducibility,
    }


def build_runtime_release_identity(
    *,
    source_git_sha: str,
    source_base_git_sha: str,
    branch: str,
    frontend_build: dict[str, Any],
    frontend_reproducibility: dict[str, Any],
    backend_root: Path,
    backend_runtime_source: dict[str, Any],
    release_control_source: dict[str, Any],
    critical_files: Iterable[str] = CRITICAL_FILES,
) -> dict[str, Any]:
    """Materialize a deterministic identity from package-local evidence."""
    source_git_sha = _validated_full_git_sha(source_git_sha, "source_git_sha")
    source_base_git_sha = _validated_full_git_sha(
        source_base_git_sha, "source_base_git_sha"
    )
    critical_names = tuple(critical_files)
    governed_backend_source = validate_backend_runtime_source_manifest(
        backend_runtime_source,
        backend_root=backend_root,
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
        allow_derived_bytecode=False,
    )
    governed_control_manifest = validate_release_control_source_manifest(
        release_control_source,
        source_git_sha=source_git_sha,
        source_base_git_sha=source_base_git_sha,
    )
    governed_control_source = source_manifest_summary(
        governed_control_manifest,
        expected_scope=RELEASE_CONTROL_SOURCE_SCOPE,
        label="Release control source",
    )
    if (
        governed_control_source["source_git_sha"] != source_git_sha
        or governed_control_source["source_base_git_sha"] != source_base_git_sha
    ):
        raise ReleaseProtocolV5Error(
            "Release control source is not bound to identity provenance"
        )
    core = {
        "kind": RELEASE_IDENTITY_KIND,
        "schema_version": RELEASE_IDENTITY_SCHEMA_VERSION,
        "protocol_version": RELEASE_PROTOCOL_VERSION,
        "source_git_sha": source_git_sha,
        "source_base_git_sha": source_base_git_sha,
        "branch": branch,
        "backend_runtime_source": governed_backend_source,
        "release_control_source": governed_control_source,
        "critical_file_hashes": packaged_critical_file_hashes(
            backend_root=backend_root,
            critical_files=critical_names,
        ),
        "frontend_build": frontend_build,
        "frontend_reproducibility": frontend_reproducibility,
    }
    payload = {**core, "release_id": deterministic_release_id(core)}
    return validate_runtime_release_identity(
        payload,
        backend_root=backend_root,
        critical_files=critical_names,
        allow_derived_bytecode=False,
    )


__all__ = (
    "BACKEND_RUNTIME_EXCLUDED_PREFIXES",
    "BACKEND_RUNTIME_CONFIGURATION_SIDECARS",
    "BACKEND_RUNTIME_GENERATED_PATHS",
    "BACKEND_RUNTIME_SOURCE_SCOPE",
    "CRITICAL_FILES",
    "RELEASE_CONTROL_OPTIONAL_DIRECTORIES",
    "RELEASE_CONTROL_REQUIRED_DIRECTORIES",
    "RELEASE_CONTROL_SOURCE_SCOPE",
    "RELEASE_IDENTITY_KIND",
    "RELEASE_IDENTITY_SCHEMA_VERSION",
    "RELEASE_PROTOCOL_VERSION",
    "ReleaseProtocolV5Error",
    "backend_runtime_source_path_included",
    "build_backend_runtime_source_manifest",
    "build_release_control_source_manifest",
    "build_source_manifest",
    "build_runtime_release_identity",
    "canonical_backend_runtime_source_sha256",
    "canonical_source_records_sha256",
    "canonical_identity_core",
    "deterministic_release_id",
    "exact_json_equal",
    "git_source_tree_oid",
    "packaged_critical_file_hashes",
    "source_manifest_summary",
    "validate_backend_runtime_source_manifest",
    "validate_release_control_source_manifest",
    "validate_source_manifest",
    "validate_source_manifest_summary",
    "validate_source_path",
    "validated_source_git_mode",
    "validate_runtime_release_identity",
)
