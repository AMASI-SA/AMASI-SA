"""Read-only identity embedded into an Emergent production package."""
from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from frontend_build_identity import read_frontend_build_identity


RELEASE_PROTOCOL_VERSION = 3
DEFAULT_RELEASE_IDENTITY_PATH = Path(__file__).with_name(
    "release_identity.json"
)
_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
BACKEND_ROOT = Path(__file__).resolve().parent
CRITICAL_FILES = (
    "server.py",
    "integrations/qoyod_manual/routes.py",
    "integrations/qoyod_manual/send.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_release_identity(path: Path | None = None) -> dict[str, Any]:
    """Return a safe public identity; never raise during a health probe."""
    identity_path = path or DEFAULT_RELEASE_IDENTITY_PATH
    try:
        payload = json.loads(identity_path.read_text(encoding="utf-8"))
        git_sha = str(payload.get("git_sha") or "").strip().lower()
        if not _FULL_GIT_SHA.fullmatch(git_sha):
            raise ValueError("invalid git_sha")
        release_id = str(payload.get("release_id") or "").strip()
        release_id_value = UUID(release_id)
        if release_id_value.version != 4 or str(release_id_value) != release_id:
            raise ValueError("invalid release_id")
        protocol_version = int(payload.get("protocol_version") or 0)
        if protocol_version != RELEASE_PROTOCOL_VERSION:
            raise ValueError("unsupported protocol_version")
        expected_hashes = payload.get("critical_file_hashes") or {}
        actual_hashes = {
            relative: _sha256(BACKEND_ROOT / relative)
            for relative in CRITICAL_FILES
        }
        hashes_match = all(
            expected_hashes.get(relative) == actual_hashes[relative]
            for relative in CRITICAL_FILES
        )
        expected_frontend_build = payload.get("frontend_build")
        actual_frontend_build = read_frontend_build_identity(
            expected_git_sha=git_sha
        )
        frontend_build_verified = (
            isinstance(expected_frontend_build, dict)
            and expected_frontend_build == actual_frontend_build
        )
        return {
            "verified_identity_available": True,
            "release_id": release_id,
            "git_sha": git_sha,
            "branch": str(payload.get("branch") or ""),
            "prepared_at": str(payload.get("prepared_at") or ""),
            "protocol_version": protocol_version,
            "critical_file_hashes_match": hashes_match,
            "critical_file_hashes": actual_hashes,
            "frontend_build_verified": frontend_build_verified,
            "frontend_build": actual_frontend_build,
        }
    except Exception:
        return {
            "verified_identity_available": False,
            "release_id": None,
            "git_sha": None,
            "branch": None,
            "prepared_at": None,
            "protocol_version": RELEASE_PROTOCOL_VERSION,
            "critical_file_hashes_match": False,
            "critical_file_hashes": {},
            "frontend_build_verified": False,
            "frontend_build": None,
        }


# Captured exactly once while the backend process imports this module. A git
# pull that does not restart the process cannot change the public identity.
BOOT_STARTED_AT = datetime.now(timezone.utc).isoformat()
BOOT_RELEASE_IDENTITY = {
    **read_release_identity(),
    "boot_started_at": BOOT_STARTED_AT,
}
