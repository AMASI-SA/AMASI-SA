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


REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend"
BACKEND_ROOT = REPO_ROOT / "backend"
BUILD_ROOT = FRONTEND_ROOT / "build"
PROOF_PATH = FRONTEND_ROOT / ".release" / "reproducible-build.json"
IDENTITY_PATH = BACKEND_ROOT / "release_identity.json"
INTENT_PATH = REPO_ROOT / "release" / "release-intent-v5.json"
TOOLCHAIN_SCRIPT = REPO_ROOT / "scripts" / "frontend_release_toolchain.py"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "verify_frontend_build.py"
INTENT_SCHEMA_VERSION = 1
INTENT_KIND = "mezan_emergent_release_intent_v1"
PROTOCOL_VERSION = 5
FULL_GIT_SHA_LENGTH = 40
CLIENT_ENV_ALLOWLIST = ("REACT_APP_BACKEND_URL",)
INTENT_KEYS = frozenset({
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

from release_protocol_v5 import exact_json_equal  # noqa: E402


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
        actual = {
            "path": relative,
            "mode": "100755" if info.st_mode & 0o111 else "100644",
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

        return validate_runtime_release_identity(payload, backend_root=BACKEND_ROOT)
    except (ImportError, ValueError, TypeError) as exc:
        raise DeploymentAdapterError(f"runtime release identity is invalid: {exc}") from exc


def load_release_intent(path: Path = INTENT_PATH) -> dict[str, Any]:
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
    frontend_source = _validated_frontend_source(payload.get("frontend_source"))
    client_environment = _reviewed_client_environment(
        payload.get("client_environment")
    )
    critical = payload.get("critical_file_hashes")
    if not exact_json_equal(critical, _critical_hashes()):
        raise DeploymentAdapterError("release intent critical Backend hashes are stale")
    runtime_identity = _validate_runtime_identity(payload.get("runtime_identity"))
    if runtime_identity.get("source_git_sha") != source_git_sha:
        raise DeploymentAdapterError("runtime identity source SHA differs from intent")
    if payload.get("branch") != runtime_identity.get("branch"):
        raise DeploymentAdapterError("runtime identity branch differs from intent")
    for name in ("frontend_build", "frontend_reproducibility", "critical_file_hashes"):
        if not exact_json_equal(payload.get(name), runtime_identity.get(name)):
            raise DeploymentAdapterError(f"runtime identity {name} differs from intent")
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


def _file_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise DeploymentAdapterError(f"deployment package contains symlink: {path}")
        if path.is_file():
            records.append({
                "path": path.relative_to(root).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            })
    return sorted(records, key=lambda row: row["path"])


def _require_real_directory(path: Path, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise DeploymentAdapterError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DeploymentAdapterError(f"{label} is not a real directory: {path}")


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
        shutil.copytree(
            BACKEND_ROOT,
            backend_package,
            symlinks=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests"),
        )

        if any(
            ".git" in path.relative_to(package).parts
            for package in (frontend_package, backend_package)
            for path in package.rglob("*")
        ):
            raise DeploymentAdapterError(
                "deployment candidate package contains Git metadata"
            )

        frontend_records = _file_records(frontend_package)
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

        backend_records = _file_records(backend_package)
        backend_by_path = {row["path"]: row for row in backend_records}
        if "release_identity.json" not in backend_by_path:
            raise DeploymentAdapterError(
                "Backend deployment package is missing release_identity.json"
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
    if (
        isolated_health.get("ok") is not True
        or isolated_health.get("service") != "backend"
        or not isinstance(boot_started_at, str)
        or not boot_started_at.strip()
        or not exact_json_equal(health_release, expected_release)
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
    evidence = cloud_build_evidence()
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
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


def freeze_intent(source_git_sha: str, branch: str) -> dict[str, Any]:
    source_git_sha = _full_git_sha(source_git_sha)
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
            branch=branch,
            frontend_build=frontend_build,
            frontend_reproducibility=proof,
            backend_root=BACKEND_ROOT,
        )
    except (ImportError, ValueError, TypeError) as exc:
        raise DeploymentAdapterError(f"cannot build runtime release identity: {exc}") from exc
    intent = {
        "schema_version": INTENT_SCHEMA_VERSION,
        "kind": INTENT_KIND,
        "protocol_version": PROTOCOL_VERSION,
        "source_git_sha": source_git_sha,
        "branch": branch,
        "frontend_source": frontend_source,
        "client_environment": reviewed_client_environment,
        "frontend_build": frontend_build,
        "frontend_reproducibility": proof,
        "critical_file_hashes": critical,
        "runtime_identity": runtime_identity,
    }
    _atomic_json(INTENT_PATH, intent)
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
    freeze.add_argument("--branch", default="hotfix/prod-snap-meta-final")
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
                freeze_intent(args.source_git_sha, args.branch),
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
