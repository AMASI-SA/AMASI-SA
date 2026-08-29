"""Deterministic, fail-closed identity for the deployed frontend artifact."""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
EXPECTED_NODE_MAJOR = 22
EXPECTED_YARN_VERSION = "1.22.22"
SOURCE_FILES = (".nvmrc", "package.json", "vite.config.js", "yarn.lock")
META_NAME = "build-meta.json"
FRONTEND_ROOT = Path(__file__).resolve().parents[1] / "frontend"
DEFAULT_BUILD_ROOT = FRONTEND_ROOT / "build"
_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_NODE_VERSION = re.compile(r"^(\d+)\.\d+\.\d+$")


class FrontendBuildIdentityError(ValueError):
    """Raised when a frontend build cannot prove its exact identity."""


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _record(path: Path, relative: str) -> dict[str, Any]:
    return {
        "path": relative,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
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
        self.values: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {name.lower(): value for name, value in attrs}
        if tag.lower() == "script" and attributes.get("src"):
            self.values.append(str(attributes["src"]))
        if tag.lower() != "link" or not attributes.get("href"):
            return
        rel = str(attributes.get("rel") or "").lower().split()
        if "stylesheet" in rel:
            self.values.append(str(attributes["href"]))


def _normalize_entrypoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        raise FrontendBuildIdentityError(
            f"frontend entrypoint must be same-origin: {value}"
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
    paths = sorted({_normalize_entrypoint(value) for value in parser.values})
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


def _tree_sha256(records: list[dict[str, Any]]) -> str:
    canonical = "".join(
        f"{row['sha256']}\0{row['bytes']}\0{row['path']}\n"
        for row in records
    ).encode("utf-8")
    return _sha256_bytes(canonical)


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


def read_frontend_build_identity(
    *,
    build_root: Path = DEFAULT_BUILD_ROOT,
    frontend_root: Path = FRONTEND_ROOT,
    expected_git_sha: str | None = None,
) -> dict[str, Any]:
    """Validate every build byte and return the compact release proof."""
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
    node_match = _NODE_VERSION.fullmatch(node)
    if not node_match or int(node_match.group(1)) != EXPECTED_NODE_MAJOR:
        raise FrontendBuildIdentityError(
            f"frontend build requires Node 22.x; found {node or 'unknown'}"
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

    try:
        source = {
            relative: _sha256(frontend_root / relative)
            for relative in SOURCE_FILES
        }
    except OSError as exc:
        raise FrontendBuildIdentityError(
            f"frontend source proof is missing: {exc}"
        ) from exc

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
    expected = {
        "schema_version": SCHEMA_VERSION,
        "git_sha": git_sha,
        "source": source,
        "toolchain": {
            "node": node,
            "yarn": yarn,
            "vite": vite,
        },
        "build": {
            "mode": "production",
            "output_dir": "frontend/build",
        },
        "index": index,
        "entrypoints": entrypoints,
        "assets": assets,
        "files": files,
        "artifact_tree_sha256": _tree_sha256(files),
    }
    if metadata != expected:
        raise FrontendBuildIdentityError(
            "frontend build metadata does not match current source/artifact bytes"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "git_sha": git_sha,
        "source": source,
        "toolchain": expected["toolchain"],
        "index": index,
        "entrypoints": entrypoints,
        "assets": assets,
        "artifact_tree_sha256": expected["artifact_tree_sha256"],
        "build_meta_sha256": _sha256(meta_path),
    }
