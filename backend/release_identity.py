"""Read-only deterministic identity embedded in the backend package."""
from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # Support both production flat imports and package-style test imports.
    from .release_protocol_v5 import (
        CRITICAL_FILES,
        RELEASE_IDENTITY_KIND,
        RELEASE_IDENTITY_SCHEMA_VERSION,
        RELEASE_PROTOCOL_VERSION,
        validate_runtime_release_identity,
    )
except ImportError:  # pragma: no cover - production imports from backend cwd
    from release_protocol_v5 import (
        CRITICAL_FILES,
        RELEASE_IDENTITY_KIND,
        RELEASE_IDENTITY_SCHEMA_VERSION,
        RELEASE_PROTOCOL_VERSION,
        validate_runtime_release_identity,
    )


DEFAULT_RELEASE_IDENTITY_PATH = Path(__file__).with_name(
    "release_identity.json"
)
BACKEND_ROOT = Path(__file__).resolve().parent


def _read_identity_json(path: Path) -> Any:
    if path.is_symlink():
        raise OSError("release identity must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        file_status = os.fstat(stream.fileno())
        if not stat.S_ISREG(file_status.st_mode):
            raise OSError("release identity must be a regular file")
        raw = stream.read()
    return json.loads(raw.decode("utf-8"))


def _unavailable_identity() -> dict[str, Any]:
    return {
        "verified_identity_available": False,
        "release_id": None,
        "git_sha": None,
        "source_git_sha": None,
        "branch": None,
        # Operational timestamps belong to the lease in v5. Keep this null
        # compatibility field for older health consumers.
        "prepared_at": None,
        "protocol_version": RELEASE_PROTOCOL_VERSION,
        "identity_kind": RELEASE_IDENTITY_KIND,
        "identity_schema_version": RELEASE_IDENTITY_SCHEMA_VERSION,
        "critical_file_hashes_match": False,
        "critical_file_hashes": {},
        "frontend_build_verified": False,
        "frontend_build": None,
        "frontend_reproducibility": None,
    }


def read_release_identity(
    path: Path | None = None,
    *,
    backend_root: Path | None = None,
) -> dict[str, Any]:
    """Return a safe public identity; never raise during a health probe.

    Runtime validation uses only the identity JSON and allowlisted bytes inside
    the backend package. It does not require ``.git`` or a sibling frontend
    directory, so separately packaged Emergent runtimes fail closed rather
    than silently depending on files that Cloud Build does not transfer.
    """
    identity_path = path or DEFAULT_RELEASE_IDENTITY_PATH
    package_root = backend_root or BACKEND_ROOT
    try:
        payload = _read_identity_json(identity_path)
        identity = validate_runtime_release_identity(
            payload,
            backend_root=package_root,
            critical_files=CRITICAL_FILES,
        )
        source_git_sha = identity["source_git_sha"]
        return {
            "verified_identity_available": True,
            "release_id": identity["release_id"],
            # ``git_sha`` remains for v1-v4 health consumers. In protocol v5
            # its exact contract is the governed source commit SHA.
            "git_sha": source_git_sha,
            "source_git_sha": source_git_sha,
            "branch": identity["branch"],
            "prepared_at": None,
            "protocol_version": RELEASE_PROTOCOL_VERSION,
            "identity_kind": RELEASE_IDENTITY_KIND,
            "identity_schema_version": RELEASE_IDENTITY_SCHEMA_VERSION,
            "critical_file_hashes_match": True,
            "critical_file_hashes": identity["critical_file_hashes"],
            "frontend_build_verified": True,
            "frontend_build": identity["frontend_build"],
            "frontend_reproducibility": identity[
                "frontend_reproducibility"
            ],
        }
    except Exception:
        return _unavailable_identity()


# Captured exactly once while the backend process imports this module. A source
# checkout update without a process restart cannot change the public identity.
BOOT_STARTED_AT = datetime.now(timezone.utc).isoformat()
BOOT_RELEASE_IDENTITY = {
    **read_release_identity(),
    "boot_started_at": BOOT_STARTED_AT,
}


def release_health_payload() -> dict[str, Any]:
    """Return the complete read-only health body without I/O or providers."""
    return {
        "ok": True,
        "service": "backend",
        "release": BOOT_RELEASE_IDENTITY,
    }


__all__ = (
    "BACKEND_ROOT",
    "BOOT_RELEASE_IDENTITY",
    "BOOT_STARTED_AT",
    "CRITICAL_FILES",
    "DEFAULT_RELEASE_IDENTITY_PATH",
    "RELEASE_PROTOCOL_VERSION",
    "read_release_identity",
    "release_health_payload",
)
