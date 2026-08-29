"""Deterministic, fail-closed identity for the deployed frontend artifact."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
EXPECTED_NODE_VERSION = "22.23.2"
EXPECTED_YARN_VERSION = "1.22.22"
CLIENT_ENV_ALLOWLIST = ("REACT_APP_BACKEND_URL",)
NON_PUBLIC_BUILD_FILES = frozenset({"_headers", "_headers.json"})
META_NAME = "build-meta.json"
FRONTEND_ROOT = Path(__file__).resolve().parents[1] / "frontend"
REPO_ROOT = FRONTEND_ROOT.parent
DEFAULT_BUILD_ROOT = FRONTEND_ROOT / "build"
DEFAULT_REPRODUCIBILITY_PROOF_PATH = (
    FRONTEND_ROOT / ".release" / "reproducible-build.json"
)
RETIREMENT_SERVICE_WORKER_PATHS = ("service-worker.js", "sw.js")
RETIREMENT_SERVICE_WORKER_BYTES = 574
RETIREMENT_SERVICE_WORKER_SHA256 = (
    "c81e48cc4257fc1af42c731588f47dfe3a784591bd60f1b603d46d3856f2cebd"
)
_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class FrontendBuildIdentityError(ValueError):
    """Raised when a frontend build cannot prove its exact identity."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _git_blob_oid(value: bytes) -> str:
    header = f"blob {len(value)}\0".encode("utf-8")
    return hashlib.sha1(header + value, usedforsecurity=False).hexdigest()


def _record(path: Path, relative: str) -> dict[str, Any]:
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _canonical_build_tree_sha256(records: list[dict[str, Any]]) -> str:
    canonical = "".join(
        f"{row['sha256']}\0{row['bytes']}\0{row['path']}\n"
        for row in records
    ).encode("utf-8")
    return _sha256_bytes(canonical)


def _canonical_source_tree_sha256(records: list[dict[str, Any]]) -> str:
    canonical = "".join(
        f"{row['git_blob']}\0{row['mode']}\0{row['sha256']}\0"
        f"{row['bytes']}\0{row['path']}\n"
        for row in records
    ).encode("utf-8")
    return _sha256_bytes(canonical)


def _validated_relative_path(value: Any, label: str) -> str:
    relative = str(value or "")
    pure = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or relative.startswith("/")
        or pure.is_absolute()
        or ".." in pure.parts
        or pure.as_posix() != relative
    ):
        raise FrontendBuildIdentityError(f"{label} path is invalid: {relative}")
    return relative


def _validated_build_records(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise FrontendBuildIdentityError(f"{label} records are missing")
    records = []
    for item in value:
        if not isinstance(item, dict):
            raise FrontendBuildIdentityError(f"{label} record is invalid")
        relative = _validated_relative_path(item.get("path"), label)
        size = item.get("bytes")
        digest = str(item.get("sha256") or "")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise FrontendBuildIdentityError(
                f"{label} byte count is invalid: {relative}"
            )
        if not _SHA256.fullmatch(digest):
            raise FrontendBuildIdentityError(
                f"{label} SHA is invalid: {relative}"
            )
        records.append({"path": relative, "bytes": size, "sha256": digest})
    if records != sorted(records, key=lambda row: row["path"]):
        raise FrontendBuildIdentityError(f"{label} records are not sorted")
    paths = [row["path"] for row in records]
    if len(paths) != len(set(paths)):
        raise FrontendBuildIdentityError(f"{label} records contain duplicate paths")
    return records


def _validated_source_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise FrontendBuildIdentityError(
            "frontend tracked source records are missing"
        )
    records = []
    for item in value:
        if not isinstance(item, dict):
            raise FrontendBuildIdentityError(
                "frontend tracked source record is invalid"
            )
        relative = _validated_relative_path(
            item.get("path"), "frontend tracked source"
        )
        mode = str(item.get("mode") or "")
        git_blob = str(item.get("git_blob") or "")
        size = item.get("bytes")
        digest = str(item.get("sha256") or "")
        if mode not in {"100644", "100755"}:
            raise FrontendBuildIdentityError(
                f"frontend tracked source mode is invalid: {relative}"
            )
        if not _FULL_GIT_SHA.fullmatch(git_blob):
            raise FrontendBuildIdentityError(
                f"frontend tracked source blob is invalid: {relative}"
            )
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise FrontendBuildIdentityError(
                f"frontend tracked source byte count is invalid: {relative}"
            )
        if not _SHA256.fullmatch(digest):
            raise FrontendBuildIdentityError(
                f"frontend tracked source SHA is invalid: {relative}"
            )
        records.append({
            "path": relative,
            "mode": mode,
            "git_blob": git_blob,
            "bytes": size,
            "sha256": digest,
        })
    if records != sorted(records, key=lambda row: row["path"]):
        raise FrontendBuildIdentityError(
            "frontend tracked source records are not sorted"
        )
    paths = [row["path"] for row in records]
    if len(paths) != len(set(paths)):
        raise FrontendBuildIdentityError(
            "frontend tracked source records contain duplicate paths"
        )
    return records


def _source_proof(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FrontendBuildIdentityError("frontend tracked source proof is missing")
    files = _validated_source_records(value.get("files"))
    expected = {
        "scope": "git_head_frontend_tree_v1",
        "git_tree_oid": str(value.get("git_tree_oid") or ""),
        "file_count": len(files),
        "files": files,
        "tree_sha256": _canonical_source_tree_sha256(files),
    }
    if not _FULL_GIT_SHA.fullmatch(expected["git_tree_oid"]):
        raise FrontendBuildIdentityError(
            "frontend tracked source Git tree is invalid"
        )
    if value != expected:
        raise FrontendBuildIdentityError("frontend tracked source proof is invalid")
    return expected


def _source_summary(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": source["scope"],
        "git_tree_oid": source["git_tree_oid"],
        "file_count": source["file_count"],
        "tree_sha256": source["tree_sha256"],
    }


def _run_git_bytes(repo_root: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        raise FrontendBuildIdentityError(
            f"cannot inspect Git-tracked frontend source: {detail}"
        )
    return proc.stdout


def _tracked_frontend_source(
    *, repo_root: Path, frontend_root: Path
) -> dict[str, Any]:
    try:
        frontend_prefix = frontend_root.resolve().relative_to(
            repo_root.resolve()
        ).as_posix()
    except ValueError as exc:
        raise FrontendBuildIdentityError(
            "frontend root is outside the Git repository"
        ) from exc
    object_format = _run_git_bytes(
        repo_root, "rev-parse", "--show-object-format"
    ).decode("ascii").strip()
    if object_format != "sha1":
        raise FrontendBuildIdentityError(
            f"unsupported Git object format: {object_format}"
        )
    dirty = _run_git_bytes(
        repo_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=normal",
        "--",
        frontend_prefix,
    )
    if dirty:
        raise FrontendBuildIdentityError(
            "frontend source is dirty; rebuild from a clean commit"
        )
    git_tree_oid = _run_git_bytes(
        repo_root, "rev-parse", f"HEAD:{frontend_prefix}"
    ).decode("ascii").strip().lower()
    raw_entries = _run_git_bytes(
        repo_root,
        "ls-tree",
        "-rz",
        "--full-tree",
        "HEAD",
        "--",
        frontend_prefix,
    )
    try:
        entries = [
            entry for entry in raw_entries.decode("utf-8").split("\0")
            if entry
        ]
    except UnicodeDecodeError as exc:
        raise FrontendBuildIdentityError(
            "Git-tracked frontend path is not UTF-8"
        ) from exc
    if not entries:
        raise FrontendBuildIdentityError(
            "Git HEAD has no tracked frontend source files"
        )
    files = []
    expected_prefix = frontend_prefix.rstrip("/") + "/"
    for entry in entries:
        try:
            header, repo_relative = entry.split("\t", 1)
            mode, kind, expected_git_blob = header.split(" ", 2)
        except ValueError as exc:
            raise FrontendBuildIdentityError(
                f"invalid Git-tracked frontend entry: {entry}"
            ) from exc
        if (
            mode not in {"100644", "100755"}
            or kind != "blob"
            or not _FULL_GIT_SHA.fullmatch(expected_git_blob)
            or not repo_relative.startswith(expected_prefix)
        ):
            raise FrontendBuildIdentityError(
                f"unsupported Git-tracked frontend entry: {entry}"
            )
        relative = _validated_relative_path(
            repo_relative[len(expected_prefix):], "frontend tracked source"
        )
        absolute = repo_root / repo_relative
        if absolute.is_symlink() or not absolute.is_file():
            raise FrontendBuildIdentityError(
                f"tracked frontend input must be a regular file: {repo_relative}"
            )
        content = absolute.read_bytes()
        actual_mode = "100755" if absolute.stat().st_mode & 0o111 else "100644"
        actual_git_blob = _git_blob_oid(content)
        if actual_mode != mode or actual_git_blob != expected_git_blob:
            raise FrontendBuildIdentityError(
                f"tracked frontend input differs from Git HEAD: {repo_relative}"
            )
        files.append({
            "path": relative,
            "mode": mode,
            "git_blob": expected_git_blob,
            "bytes": len(content),
            "sha256": _sha256_bytes(content),
        })
    files.sort(key=lambda row: row["path"])
    return {
        "scope": "git_head_frontend_tree_v1",
        "git_tree_oid": git_tree_oid,
        "file_count": len(files),
        "files": files,
        "tree_sha256": _canonical_source_tree_sha256(files),
    }


def _build_records(build_root: Path) -> list[dict[str, Any]]:
    if not build_root.is_dir():
        raise FrontendBuildIdentityError(
            f"frontend build directory is missing: {build_root}"
        )
    records = []
    for path in build_root.rglob("*"):
        if path.is_symlink():
            raise FrontendBuildIdentityError(
                f"frontend build must not contain symlinks: {path}"
            )
        if not path.is_file():
            continue
        relative = path.relative_to(build_root).as_posix()
        if relative != META_NAME:
            records.append(_record(path, relative))
    records.sort(key=lambda row: row["path"])
    return records


class _EntrypointParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.values: list[tuple[str, bool]] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {name.lower(): value for name, value in attrs}
        if tag.lower() == "script" and attributes.get("src"):
            self.values.append((str(attributes["src"]), False))
        if tag.lower() != "link" or not attributes.get("href"):
            return
        rel = str(attributes.get("rel") or "").lower().split()
        if "stylesheet" in rel:
            self.values.append((str(attributes["href"]), True))


def _normalize_entrypoint(
    value: str, *, allow_external: bool
) -> str | None:
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} or parsed.netloc:
        if allow_external:
            return None
        raise FrontendBuildIdentityError(
            f"frontend script entrypoint must be same-origin: {value}"
        )
    if parsed.scheme:
        raise FrontendBuildIdentityError(
            f"unexpected frontend entrypoint scheme: {value}"
        )
    relative = parsed.path.removeprefix("./").lstrip("/")
    pure = PurePosixPath(relative)
    if (
        not relative.startswith("assets/")
        or pure.is_absolute()
        or ".." in pure.parts
        or pure.suffix not in {".js", ".css"}
    ):
        raise FrontendBuildIdentityError(
            f"unexpected frontend entrypoint: {value}"
        )
    return pure.as_posix()


def _entrypoints(
    index_path: Path,
    records_by_path: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    parser = _EntrypointParser()
    try:
        parser.feed(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        raise FrontendBuildIdentityError(
            f"cannot read frontend index: {exc}"
        ) from exc
    paths = sorted({
        normalized
        for value, allow_external in parser.values
        if (
            normalized := _normalize_entrypoint(
                value, allow_external=allow_external
            )
        ) is not None
    })
    if not any(path.endswith(".js") for path in paths):
        raise FrontendBuildIdentityError(
            "frontend index has no JavaScript entrypoint"
        )
    try:
        return [records_by_path[path] for path in paths]
    except KeyError as exc:
        raise FrontendBuildIdentityError(
            f"frontend entrypoint is missing: {exc.args[0]}"
        ) from exc


def _governed_environment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FrontendBuildIdentityError(
            "frontend governed environment proof is missing"
        )
    raw_values = value.get("values")
    if not isinstance(raw_values, dict):
        raise FrontendBuildIdentityError(
            "frontend governed environment values are missing"
        )
    values = {}
    for name in CLIENT_ENV_ALLOWLIST:
        record = raw_values.get(name)
        if not isinstance(record, dict) or not isinstance(
            record.get("present"), bool
        ):
            raise FrontendBuildIdentityError(
                f"frontend governed environment record is invalid: {name}"
            )
        present = record["present"]
        digest = record.get("sha256")
        if present and not _SHA256.fullmatch(str(digest or "")):
            raise FrontendBuildIdentityError(
                f"frontend governed environment SHA is invalid: {name}"
            )
        if not present and digest is not None:
            raise FrontendBuildIdentityError(
                f"frontend absent environment value has a SHA: {name}"
            )
        values[name] = {"present": present, "sha256": digest}
    expected = {
        "mode": "production",
        "effective": {
            "NODE_ENV": "production",
            "VITE_USER_NODE_ENV_present": False,
            "VITE_prefixed_keys": [],
        },
        "allowed_client_keys": list(CLIENT_ENV_ALLOWLIST),
        "values": values,
    }
    if value != expected:
        raise FrontendBuildIdentityError(
            "frontend governed environment proof is invalid"
        )
    return expected


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrontendBuildIdentityError(
            f"cannot read frontend build metadata: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise FrontendBuildIdentityError(
            "frontend build metadata must be a JSON object"
        )
    return payload


def _expected_reproducibility_proof(
    frontend_build: dict[str, Any],
) -> dict[str, Any]:
    build_meta = frontend_build.get("build_meta")
    artifact_tree_sha256 = frontend_build.get("artifact_tree_sha256")
    common_pass = {
        "build_meta": build_meta,
        "artifact_tree_sha256": artifact_tree_sha256,
    }
    return {
        "schema_version": 1,
        "kind": "frontend_two_clean_builds_v1",
        "git_sha": frontend_build.get("git_sha"),
        "source": frontend_build.get("source"),
        "toolchain": frontend_build.get("toolchain"),
        "environment": frontend_build.get("environment"),
        "passes": [
            {"ordinal": 1, **common_pass},
            {"ordinal": 2, **common_pass},
        ],
        "retained_pass": 2,
    }


def validate_frontend_reproducibility_proof(
    *,
    frontend_build: dict[str, Any],
    proof: Any,
) -> dict[str, Any]:
    """Validate an embedded two-build proof without filesystem or Git access."""
    if not isinstance(frontend_build, dict):
        raise FrontendBuildIdentityError(
            "frontend build proof is invalid"
        )
    if not isinstance(proof, dict):
        raise FrontendBuildIdentityError(
            "frontend reproducibility proof is invalid"
        )
    proof_file = proof.get("proof_file")
    records = _validated_build_records(
        [proof_file], "frontend reproducibility proof file"
    )
    if records[0]["path"] != "frontend/.release/reproducible-build.json":
        raise FrontendBuildIdentityError(
            "frontend reproducibility proof file path is invalid"
        )
    if records[0]["bytes"] == 0:
        raise FrontendBuildIdentityError(
            "frontend reproducibility proof file is empty"
        )
    expected = {
        **_expected_reproducibility_proof(frontend_build),
        "proof_file": records[0],
    }
    if proof != expected:
        raise FrontendBuildIdentityError(
            "frontend reproducibility proof does not match retained build"
        )
    return expected


def read_frontend_reproducibility_proof(
    *,
    frontend_build: dict[str, Any],
    proof_path: Path = DEFAULT_REPRODUCIBILITY_PROOF_PATH,
) -> dict[str, Any]:
    """Validate the two-clean-build proof against the retained build B."""
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(proof_path, flags)
        with os.fdopen(descriptor, "rb") as proof_file:
            file_status = os.fstat(proof_file.fileno())
            if not stat.S_ISREG(file_status.st_mode):
                raise OSError("proof must be a regular non-symlink file")
            proof_bytes = proof_file.read()
        payload = json.loads(proof_bytes.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrontendBuildIdentityError(
            f"cannot read frontend reproducibility proof: {exc}"
        ) from exc
    expected = _expected_reproducibility_proof(frontend_build)
    if payload != expected:
        raise FrontendBuildIdentityError(
            "frontend reproducibility proof does not match retained build"
        )
    embedded = {
        **expected,
        "proof_file": {
            "path": "frontend/.release/reproducible-build.json",
            "bytes": len(proof_bytes),
            "sha256": _sha256_bytes(proof_bytes),
        },
    }
    return validate_frontend_reproducibility_proof(
        frontend_build=frontend_build,
        proof=embedded,
    )


def read_frontend_build_identity(
    *,
    build_root: Path = DEFAULT_BUILD_ROOT,
    frontend_root: Path = FRONTEND_ROOT,
    repo_root: Path | None = None,
    expected_git_sha: str | None = None,
    require_git_source: bool = False,
) -> dict[str, Any]:
    """Validate artifact bytes and optionally re-prove the clean HEAD source."""
    meta_path = build_root / META_NAME
    metadata = _read_json(meta_path)

    git_sha = str(metadata.get("git_sha") or "").strip().lower()
    if not _FULL_GIT_SHA.fullmatch(git_sha):
        raise FrontendBuildIdentityError("frontend build git SHA is invalid")
    if expected_git_sha is not None and git_sha != expected_git_sha:
        raise FrontendBuildIdentityError(
            "frontend build is stale: "
            f"metadata={git_sha} expected={expected_git_sha}"
        )

    toolchain = metadata.get("toolchain")
    if not isinstance(toolchain, dict):
        raise FrontendBuildIdentityError("frontend toolchain proof is missing")
    node = str(toolchain.get("node") or "")
    if node != EXPECTED_NODE_VERSION:
        raise FrontendBuildIdentityError(
            f"frontend build requires Node {EXPECTED_NODE_VERSION}; "
            f"found {node or 'unknown'}"
        )
    yarn = str(toolchain.get("yarn") or "")
    if yarn != EXPECTED_YARN_VERSION:
        raise FrontendBuildIdentityError(
            f"frontend build requires Yarn {EXPECTED_YARN_VERSION}; "
            f"found {yarn or 'unknown'}"
        )
    try:
        package = json.loads(
            (frontend_root / "package.json").read_text(encoding="utf-8")
        )
        vite = str(package["dependencies"]["vite"])
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise FrontendBuildIdentityError(
            f"cannot establish the declared Vite version: {exc}"
        ) from exc
    if str(toolchain.get("vite") or "") != vite:
        raise FrontendBuildIdentityError(
            "frontend Vite version does not match package.json"
        )

    source = _source_proof(metadata.get("source"))
    if require_git_source:
        actual_source = _tracked_frontend_source(
            repo_root=(repo_root or frontend_root.parent),
            frontend_root=frontend_root,
        )
        if source != actual_source:
            raise FrontendBuildIdentityError(
                "frontend build source proof does not match clean Git HEAD"
            )
    environment = _governed_environment(metadata.get("environment"))

    files = _build_records(build_root)
    records_by_path = {row["path"]: row for row in files}
    index = records_by_path.get("index.html")
    if index is None:
        raise FrontendBuildIdentityError(
            "frontend build record for index.html is missing"
        )
    entrypoints = _entrypoints(build_root / "index.html", records_by_path)
    assets = [row for row in files if row["path"].startswith("assets/")]
    if not assets:
        raise FrontendBuildIdentityError("frontend build has no assets")
    public_files = [
        row for row in files if row["path"] not in NON_PUBLIC_BUILD_FILES
    ]
    for worker_path in RETIREMENT_SERVICE_WORKER_PATHS:
        worker = records_by_path.get(worker_path)
        if (
            worker is None
            or worker["bytes"] != RETIREMENT_SERVICE_WORKER_BYTES
            or worker["sha256"] != RETIREMENT_SERVICE_WORKER_SHA256
        ):
            raise FrontendBuildIdentityError(
                "frontend retirement service worker is missing or invalid: "
                f"{worker_path}"
            )
    expected = {
        "schema_version": SCHEMA_VERSION,
        "git_sha": git_sha,
        "source": source,
        "toolchain": {"node": node, "yarn": yarn, "vite": vite},
        "environment": environment,
        "build": {
            "mode": "production",
            "output_dir": "frontend/build",
        },
        "index": index,
        "entrypoints": entrypoints,
        "assets": assets,
        "public_files": public_files,
        "files": files,
        "artifact_tree_sha256": _canonical_build_tree_sha256(files),
    }
    if metadata != expected:
        raise FrontendBuildIdentityError(
            "frontend build metadata does not match current source/artifact bytes"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "git_sha": git_sha,
        "source": _source_summary(source),
        "toolchain": expected["toolchain"],
        "environment": environment,
        "index": index,
        "entrypoints": entrypoints,
        "assets": assets,
        "public_files": public_files,
        "artifact_tree_sha256": expected["artifact_tree_sha256"],
        "build_meta": _record(meta_path, META_NAME),
    }
