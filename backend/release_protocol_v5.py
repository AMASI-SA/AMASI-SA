"""Deterministic, package-local release identity contract (protocol v5).

The runtime identity is deliberately independent from the operational release
lease.  It contains no actor, timestamp, UUID, Git checkout lookup, or path to
the sibling frontend workspace.  Every value needed by the backend is embedded
in ``release_identity.json`` and bound by a deterministic release id.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
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
RELEASE_IDENTITY_SCHEMA_VERSION = 1
RELEASE_ID_PREFIX = "rg5-"
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
    "branch",
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
        or "\\" in value
        or value.startswith("/")
        or pure.is_absolute()
        or ".." in pure.parts
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
) -> dict[str, Any]:
    """Validate deterministic identity against this backend package.

    This function intentionally performs no Git lookup and reads no sibling
    frontend directory.  The only filesystem inputs are the allowlisted files
    inside ``backend_root`` whose hashes are embedded in the identity.
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

    source_git_sha = payload.get("source_git_sha")
    if not isinstance(source_git_sha, str) or not _FULL_GIT_SHA.fullmatch(
        source_git_sha
    ):
        raise ReleaseProtocolV5Error("source_git_sha must be 40 lowercase hex")
    branch = payload.get("branch")
    if not isinstance(branch, str) or not branch.strip():
        raise ReleaseProtocolV5Error("release branch is missing")
    if branch != branch.strip() or "\x00" in branch or len(branch) > 255:
        raise ReleaseProtocolV5Error("release branch is invalid")

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
        "critical_file_hashes": actual_hashes,
        "frontend_build": frontend_build,
        "frontend_reproducibility": frontend_reproducibility,
    }


def build_runtime_release_identity(
    *,
    source_git_sha: str,
    branch: str,
    frontend_build: dict[str, Any],
    frontend_reproducibility: dict[str, Any],
    backend_root: Path,
    critical_files: Iterable[str] = CRITICAL_FILES,
) -> dict[str, Any]:
    """Materialize a deterministic identity from package-local evidence."""
    critical_names = tuple(critical_files)
    core = {
        "kind": RELEASE_IDENTITY_KIND,
        "schema_version": RELEASE_IDENTITY_SCHEMA_VERSION,
        "protocol_version": RELEASE_PROTOCOL_VERSION,
        "source_git_sha": source_git_sha,
        "branch": branch,
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
    )


__all__ = (
    "CRITICAL_FILES",
    "RELEASE_IDENTITY_KIND",
    "RELEASE_IDENTITY_SCHEMA_VERSION",
    "RELEASE_PROTOCOL_VERSION",
    "ReleaseProtocolV5Error",
    "build_runtime_release_identity",
    "canonical_identity_core",
    "deterministic_release_id",
    "exact_json_equal",
    "packaged_critical_file_hashes",
    "validate_runtime_release_identity",
)
