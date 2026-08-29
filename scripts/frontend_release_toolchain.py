#!/usr/bin/env python3
"""Install and execute the governed frontend release toolchain locally.

The production host intentionally keeps its system Node.js installation
untouched.  This helper downloads one pinned official Node.js archive into a
repo-external cache, verifies it before extraction or execution, provisions
the pinned Yarn Classic release through that Node distribution's Corepack,
and runs release commands with a child-only PATH.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - exercised only on unsupported hosts
    fcntl = None  # type: ignore[assignment]


NODE_VERSION = "v22.23.2"
NODE_RELEASE = NODE_VERSION.removeprefix("v")
YARN_VERSION = "1.22.22"
SCHEMA_VERSION = 1
CACHE_LAYOUT_VERSION = "v1"
CACHE_NAMESPACE = "mezan-release-toolchains"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 200_000
YARN_SHA512 = (
    "a6b2f7906b721bba3d67d4aff083df04dad64c399707841b7acf00f6b133b7a"
    "c24255f2652fa22ae3534329dc6180534e98d17432037ff6fd140556e2bb3137e"
)
YARN_LOCATOR = f"yarn@{YARN_VERSION}+sha512.{YARN_SHA512}"
REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_ROOT = REPO_ROOT / "frontend"


class ToolchainError(RuntimeError):
    """Raised when the governed toolchain cannot be proven safe to use."""


@dataclass(frozen=True)
class ArchiveSpec:
    platform_tag: str
    system: str
    machine: str
    filename: str
    url: str
    sha256: str

    @property
    def extracted_root(self) -> str:
        return self.filename.removesuffix(".tar.xz")


def _archive_spec(platform_tag: str, machine: str, sha256: str) -> ArchiveSpec:
    filename = f"node-{NODE_VERSION}-{platform_tag}.tar.xz"
    return ArchiveSpec(
        platform_tag=platform_tag,
        system="Linux",
        machine=machine,
        filename=filename,
        url=f"https://nodejs.org/dist/{NODE_VERSION}/{filename}",
        sha256=sha256,
    )


ARCHIVES = {
    ("Linux", "x86_64"): _archive_spec(
        "linux-x64",
        "x86_64",
        "d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307",
    ),
    ("Linux", "aarch64"): _archive_spec(
        "linux-arm64",
        "aarch64",
        "fff4078c5def658577f92c88db7db3bc0072924bfb93fe52c1e744a54e94abb8",
    ),
}
PINNED_ARCHIVE_URLS = frozenset(spec.url for spec in ARCHIVES.values())


Runner = Callable[..., subprocess.CompletedProcess[str]]
Downloader = Callable[[str, Path], None]


def select_archive(
    system_name: str | None = None, machine: str | None = None
) -> ArchiveSpec:
    system_name = system_name if system_name is not None else platform.system()
    machine = machine if machine is not None else platform.machine()
    try:
        return ARCHIVES[(system_name, machine)]
    except KeyError as exc:
        raise ToolchainError(
            "unsupported frontend release platform: "
            f"system={system_name!r}, architecture={machine!r}; supported "
            "targets are Linux x86_64 and Linux aarch64"
        ) from exc


def _default_cache_root() -> Path:
    xdg_cache = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg_cache:
        base = Path(xdg_cache).expanduser()
        if not base.is_absolute():
            raise ToolchainError("XDG_CACHE_HOME must be an absolute path")
    else:
        home = os.environ.get("HOME", "").strip()
        if not home:
            raise ToolchainError("HOME is required when XDG_CACHE_HOME is unset")
        home_path = Path(home).expanduser()
        if not home_path.is_absolute():
            raise ToolchainError("HOME must be an absolute path")
        base = home_path / ".cache"
    return base / CACHE_NAMESPACE


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _prepare_cache_root(cache_root: Path | None) -> Path:
    root = _absolute(cache_root or _default_cache_root())
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ToolchainError(f"toolchain cache root is not a real directory: {root}")
    if info.st_uid != os.geteuid():
        raise ToolchainError(f"toolchain cache root is not owned by this user: {root}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ToolchainError(
            f"toolchain cache root must not be group/world writable: {root}"
        )
    resolved = root.resolve(strict=True)
    if _is_within(resolved, REPO_ROOT.resolve()):
        raise ToolchainError(
            f"toolchain cache must be outside the Git worktree: {resolved}"
        )
    if os.pathsep in os.fspath(resolved):
        raise ToolchainError(
            f"toolchain cache path cannot contain {os.pathsep!r}: {resolved}"
        )
    return resolved


def _layout_root(cache_root: Path) -> Path:
    layout = cache_root / CACHE_LAYOUT_VERSION
    layout.mkdir(mode=0o700, exist_ok=True)
    info = layout.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ToolchainError(f"toolchain layout is not a real directory: {layout}")
    if info.st_uid != os.geteuid():
        raise ToolchainError(f"toolchain layout is not owned by this user: {layout}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ToolchainError(
            f"toolchain layout must not be group/world writable: {layout}"
        )
    resolved = layout.resolve(strict=True)
    if not _is_within(resolved, cache_root.resolve(strict=True)):
        raise ToolchainError(f"toolchain layout escapes the cache root: {layout}")
    return resolved


def _final_dir(layout_root: Path, spec: ArchiveSpec) -> Path:
    return layout_root / (
        f"node-{NODE_VERSION}-yarn-{YARN_VERSION}-{spec.platform_tag}"
    )


@contextlib.contextmanager
def _toolchain_lock(layout_root: Path, spec: ArchiveSpec) -> Iterable[None]:
    if fcntl is None:
        raise ToolchainError("frontend release locking requires Linux fcntl support")
    lock_path = layout_root / f".{spec.platform_tag}.lock"
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise ToolchainError(f"cannot open toolchain lock: {exc}") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise ToolchainError("toolchain lock is not a user-owned regular file")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise ToolchainError(
            f"Node archive download refused HTTP redirect ({code})"
        )


def _download_archive(url: str, destination: Path) -> None:
    if url not in PINNED_ARCHIVE_URLS:
        raise ToolchainError("Node archive URL is not the pinned official release URL")
    opener = urllib.request.build_opener(_RejectRedirects())
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "mezan-frontend-release-toolchain/1"},
    )
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            try:
                response = opener.open(request, timeout=60)
            except (urllib.error.URLError, TimeoutError, ToolchainError) as exc:
                raise ToolchainError(f"cannot download pinned Node archive: {exc}") from exc
            with response:
                if getattr(response, "status", 200) != 200:
                    raise ToolchainError(
                        f"Node archive download returned HTTP {response.status}"
                    )
                if response.geturl() != url:
                    raise ToolchainError("Node archive download changed URL")
                declared = response.headers.get("Content-Length")
                declared_size: int | None = None
                if declared:
                    try:
                        declared_size = int(declared)
                    except ValueError as exc:
                        raise ToolchainError(
                            "Node archive Content-Length is invalid"
                        ) from exc
                    if declared_size <= 0 or declared_size > MAX_ARCHIVE_BYTES:
                        raise ToolchainError("Node archive size is outside the safe limit")
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ToolchainError("Node archive exceeded the safe size limit")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
                if total == 0:
                    raise ToolchainError("Node archive download was empty")
                if declared_size is not None and total != declared_size:
                    raise ToolchainError(
                        "Node archive download ended before Content-Length"
                    )
    except Exception as exc:
        with contextlib.suppress(FileNotFoundError):
            destination.unlink()
        if isinstance(exc, ToolchainError):
            raise
        raise ToolchainError(f"cannot download pinned Node archive: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _validated_member_name(name: str, spec: ArchiveSpec) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] != spec.extracted_root
    ):
        raise ToolchainError(f"Node archive contains an unsafe path: {name!r}")
    return path


def _collapsed_link_target(member: tarfile.TarInfo, spec: ArchiveSpec) -> PurePosixPath:
    link = PurePosixPath(member.linkname)
    if not member.linkname or "\\" in member.linkname or link.is_absolute():
        raise ToolchainError(
            f"Node archive contains an unsafe link: {member.name!r}"
        )
    combined = (
        link if member.islnk() else PurePosixPath(member.name).parent / link
    )
    parts: list[str] = []
    for part in combined.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ToolchainError(
                    f"Node archive link escapes extraction root: {member.name!r}"
                )
            parts.pop()
        else:
            parts.append(part)
    target = PurePosixPath(*parts)
    if not target.parts or target.parts[0] != spec.extracted_root:
        raise ToolchainError(
            f"Node archive link escapes extraction root: {member.name!r}"
        )
    return target


def _safe_extract(archive: Path, destination: Path, spec: ArchiveSpec) -> None:
    destination.mkdir(mode=0o700)
    try:
        bundle = tarfile.open(archive, mode="r:xz")
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ToolchainError(f"Node archive is incomplete or invalid: {exc}") from exc
    with bundle:
        members = bundle.getmembers()
        if not members:
            raise ToolchainError("Node archive is empty")
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ToolchainError("Node archive contains too many entries")
        total = 0
        hard_links: list[tarfile.TarInfo] = []
        symbolic_links: list[tarfile.TarInfo] = []
        seen: set[str] = set()
        for member in members:
            relative = _validated_member_name(member.name, spec)
            canonical_name = relative.as_posix().rstrip("/")
            if canonical_name in seen:
                raise ToolchainError(
                    f"Node archive contains a duplicate path: {member.name!r}"
                )
            seen.add(canonical_name)
            if member.size < 0:
                raise ToolchainError(
                    f"Node archive contains an invalid size: {member.name!r}"
                )
            total += max(0, member.size)
            if total > MAX_EXTRACTED_BYTES:
                raise ToolchainError("Node archive expands beyond the safe size limit")
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(stat.S_IMODE(member.mode) & 0o755 or 0o755)
                continue
            if member.issym():
                _collapsed_link_target(member, spec)
                symbolic_links.append(member)
                continue
            if member.islnk():
                _collapsed_link_target(member, spec)
                hard_links.append(member)
                continue
            if not member.isfile():
                raise ToolchainError(
                    f"Node archive contains an unsupported entry: {member.name!r}"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise ToolchainError(
                    f"Node archive contains a duplicate path: {member.name!r}"
                )
            source = bundle.extractfile(member)
            if source is None:
                raise ToolchainError(
                    f"Node archive file cannot be read: {member.name!r}"
                )
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(stat.S_IMODE(member.mode) & 0o755 or 0o644)

        for member in hard_links + symbolic_links:
            relative = _validated_member_name(member.name, spec)
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                raise ToolchainError(
                    f"Node archive link collides with another path: {member.name!r}"
                )
            link_target = _collapsed_link_target(member, spec)
            if member.issym():
                os.symlink(member.linkname, target)
            else:
                source = destination.joinpath(*link_target.parts)
                if not source.is_file() or source.is_symlink():
                    raise ToolchainError(
                        f"Node archive hard link target is invalid: {member.name!r}"
                    )
                os.link(source, target)

    extracted_root = destination / spec.extracted_root
    if not extracted_root.is_dir() or extracted_root.is_symlink():
        raise ToolchainError("Node archive did not contain the expected root directory")
    if sorted(path.name for path in destination.iterdir()) != [spec.extracted_root]:
        raise ToolchainError("Node archive contains unexpected top-level entries")


def _records_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ToolchainError("toolchain integrity tree is empty")
    ordered = sorted(records, key=lambda record: str(record["path"]))
    canonical = b"".join(
        _canonical_json_bytes(record).rstrip(b"\n") + b"\n" for record in ordered
    )
    return {
        "file_count": len(ordered),
        "tree_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def _tar_regular_digest(
    bundle: tarfile.TarFile,
    member: tarfile.TarInfo,
    spec: ArchiveSpec,
    seen: set[str] | None = None,
) -> tuple[int, str]:
    seen = set() if seen is None else seen
    if member.name in seen:
        raise ToolchainError(f"Node archive contains a hard-link cycle: {member.name!r}")
    seen.add(member.name)
    if member.islnk():
        target_name = _collapsed_link_target(member, spec).as_posix()
        try:
            target = bundle.getmember(target_name)
        except KeyError as exc:
            raise ToolchainError(
                f"Node archive hard link target is missing: {member.name!r}"
            ) from exc
        return _tar_regular_digest(bundle, target, spec, seen)
    if not member.isfile() or member.size < 0 or member.size > MAX_EXTRACTED_BYTES:
        raise ToolchainError(
            f"verified Node archive entry is invalid: {member.name}"
        )
    source = bundle.extractfile(member)
    if source is None:
        raise ToolchainError(
            f"verified Node archive entry cannot be read: {member.name}"
        )
    digest = hashlib.sha256()
    observed_size = 0
    with source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            observed_size += len(chunk)
            if observed_size > member.size:
                raise ToolchainError(
                    f"verified Node archive entry exceeded its size: {member.name}"
                )
            digest.update(chunk)
    if observed_size != member.size:
        raise ToolchainError(
            f"verified Node archive entry is incomplete: {member.name}"
        )
    return observed_size, digest.hexdigest()


def _archive_critical_hashes(archive: Path, spec: ArchiveSpec) -> dict[str, Any]:
    """Derive executable hashes from the retained, pinned archive itself."""

    relative_members = {
        "node": f"{spec.extracted_root}/bin/node",
        "corepack": (
            f"{spec.extracted_root}/lib/node_modules/corepack/dist/corepack.js"
        ),
    }
    try:
        bundle = tarfile.open(archive, mode="r:xz")
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ToolchainError(f"Node archive is incomplete or invalid: {exc}") from exc
    results: dict[str, Any] = {}
    with bundle:
        for label, member_name in relative_members.items():
            try:
                member = bundle.getmember(member_name)
            except KeyError as exc:
                raise ToolchainError(
                    f"verified Node archive is missing {member_name}"
                ) from exc
            _validated_member_name(member.name, spec)
            size, digest = _tar_regular_digest(bundle, member, spec)
            if size < 1:
                raise ToolchainError(
                    f"verified Node archive entry is empty: {member_name}"
                )
            results[label] = digest

        corepack_prefix = PurePosixPath(
            spec.extracted_root, "lib", "node_modules", "corepack"
        )
        corepack_records: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for member in bundle.getmembers():
            member_path = _validated_member_name(member.name, spec)
            try:
                relative_path = member_path.relative_to(corepack_prefix)
            except ValueError:
                continue
            if not relative_path.parts:
                continue
            relative = relative_path.as_posix().rstrip("/")
            if not relative or relative in seen_paths:
                raise ToolchainError(
                    f"Node archive contains a duplicate Corepack path: {member.name!r}"
                )
            seen_paths.add(relative)
            if member.isdir():
                continue
            if member.issym():
                _collapsed_link_target(member, spec)
                corepack_records.append(
                    {
                        "path": relative,
                        "kind": "symlink",
                        "target": member.linkname,
                    }
                )
                continue
            if member.isfile() or member.islnk():
                size, digest = _tar_regular_digest(bundle, member, spec)
                corepack_records.append(
                    {
                        "path": relative,
                        "kind": "file",
                        "bytes": size,
                        "sha256": digest,
                    }
                )
                continue
            raise ToolchainError(
                f"verified Node archive Corepack entry is invalid: {member.name}"
            )
        results["corepack_tree"] = _records_summary(corepack_records)
    return results


def _default_runner(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), **kwargs)


def _invoke(
    argv: Sequence[str],
    *,
    env: dict[str, str],
    runner: Runner | None,
    cwd: Path | None = None,
    capture: bool = True,
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "env": env,
        "cwd": os.fspath(cwd) if cwd is not None else None,
        "check": False,
        "timeout": timeout,
        "text": True,
    }
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        return (runner or _default_runner)(list(argv), **kwargs)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolchainError(f"cannot execute {argv[0]}: {exc}") from exc


def _checked_output(
    argv: Sequence[str],
    *,
    env: dict[str, str],
    runner: Runner | None,
    cwd: Path | None = None,
    label: str,
    timeout: int = 180,
) -> str:
    process = _invoke(
        argv,
        env=env,
        runner=runner,
        cwd=cwd,
        capture=True,
        timeout=timeout,
    )
    if process.returncode:
        detail = (process.stderr or process.stdout or "").strip()
        raise ToolchainError(f"{label} failed: {detail or process.returncode}")
    return (process.stdout or "").strip()


def _base_toolchain_env(
    toolchain_dir: Path, *, allow_corepack_network: bool = False
) -> dict[str, str]:
    env = dict(os.environ)
    for key in tuple(env):
        if key.startswith("COREPACK_") or key.startswith("BASH_FUNC_") or key in {
            "BASH_ENV",
            "ENV",
            "LD_LIBRARY_PATH",
            "LD_PRELOAD",
            "NODE_OPTIONS",
            "NODE_PATH",
            "YARN_PATH",
            "YARN_IGNORE_PATH",
        }:
            env.pop(key, None)
    bin_dir = toolchain_dir / "node" / "bin"
    existing_path = env.get("PATH", "")
    env["PATH"] = os.fspath(bin_dir) + (os.pathsep + existing_path if existing_path else "")
    env["COREPACK_HOME"] = os.fspath(toolchain_dir / "corepack-home")
    env["COREPACK_DEFAULT_TO_LATEST"] = "0"
    env["COREPACK_ENABLE_STRICT"] = "1"
    env["COREPACK_ENABLE_DOWNLOAD_PROMPT"] = "0"
    env["COREPACK_ENABLE_NETWORK"] = "1" if allow_corepack_network else "0"
    env["YARN_IGNORE_PATH"] = "1"
    return env


def _exec_env(toolchain_dir: Path) -> dict[str, str]:
    env = _base_toolchain_env(toolchain_dir)
    env["BASH_ENV"] = os.fspath(toolchain_dir / "exec-env.sh")
    return env


def _exec_env_bytes(toolchain_dir: Path) -> bytes:
    final_bin = toolchain_dir / "node" / "bin"
    final_corepack_home = toolchain_dir / "corepack-home"
    return (
        "# Generated by scripts/frontend_release_toolchain.py\n"
        f"export PATH={shlex.quote(os.fspath(final_bin))}:\"${{PATH:-}}\"\n"
        "unset NODE_OPTIONS NODE_PATH YARN_PATH COREPACK_HOME "
        "BASH_ENV ENV LD_PRELOAD LD_LIBRARY_PATH "
        "COREPACK_DEFAULT_TO_LATEST COREPACK_ENABLE_STRICT "
        "COREPACK_ENABLE_DOWNLOAD_PROMPT COREPACK_ENABLE_NETWORK "
        "COREPACK_NPM_REGISTRY COREPACK_INTEGRITY_KEYS "
        "COREPACK_ENABLE_PROJECT_SPEC COREPACK_ENABLE_UNSAFE_CUSTOM_URLS\n"
        f"export COREPACK_HOME={shlex.quote(os.fspath(final_corepack_home))}\n"
        "export COREPACK_DEFAULT_TO_LATEST=0\n"
        "export COREPACK_ENABLE_STRICT=1\n"
        "export COREPACK_ENABLE_DOWNLOAD_PROMPT=0\n"
        "export COREPACK_ENABLE_NETWORK=0\n"
        "export YARN_IGNORE_PATH=1\n"
    ).encode("utf-8")


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _write_atomic(path: Path, content: bytes, mode: int = 0o600) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _tree_summary(root: Path) -> dict[str, Any]:
    root = _validated_real_directory(root, "toolchain integrity directory")
    records: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if stat.S_ISDIR(info.st_mode):
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
                raise ToolchainError(f"toolchain directory permissions are unsafe: {path}")
            continue
        if stat.S_ISREG(info.st_mode):
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
                raise ToolchainError(f"toolchain file permissions are unsafe: {path}")
            records.append({
                "path": relative,
                "kind": "file",
                "bytes": info.st_size,
                "sha256": _sha256_file(path),
            })
        elif stat.S_ISLNK(info.st_mode):
            if info.st_uid != os.geteuid():
                raise ToolchainError(f"toolchain symlink ownership is unsafe: {path}")
            resolved = path.resolve(strict=True)
            if not _is_within(resolved, root):
                raise ToolchainError(f"toolchain symlink escapes integrity root: {path}")
            records.append({
                "path": relative,
                "kind": "symlink",
                "target": os.readlink(path),
            })
        else:
            raise ToolchainError(f"toolchain contains an unsafe entry: {path}")
    return _records_summary(records)


def _validated_real_directory(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ToolchainError(f"{label} is missing: {path}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ToolchainError(f"{label} is not a real directory: {path}")
    if info.st_uid != os.geteuid():
        raise ToolchainError(f"{label} is not owned by this user: {path}")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ToolchainError(f"{label} must not be group/world writable: {path}")
    try:
        return path.resolve(strict=True)
    except OSError as exc:
        raise ToolchainError(f"{label} cannot be resolved: {path}") from exc


def _safe_cached_path(
    toolchain_dir: Path,
    relative: str,
) -> tuple[Path, os.stat_result, Path]:
    pure = PurePosixPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or ".." in pure.parts
        or any(part in ("", ".") for part in pure.parts)
    ):
        raise ToolchainError(f"toolchain metadata path is invalid: {relative!r}")
    root = _validated_real_directory(toolchain_dir, "cached toolchain")
    current = toolchain_dir
    for component in pure.parts[:-1]:
        current = current / component
        _validated_real_directory(current, f"toolchain path component {component!r}")
    path = toolchain_dir.joinpath(*pure.parts)
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ToolchainError(f"toolchain path is missing: {relative}") from exc
    if not _is_within(resolved, root):
        raise ToolchainError(f"toolchain path escapes cache: {relative}")
    return path, info, resolved


def _relative_file(toolchain_dir: Path, relative: str, *, executable: bool = False) -> Path:
    path, info, _ = _safe_cached_path(toolchain_dir, relative)
    if not stat.S_ISREG(info.st_mode):
        raise ToolchainError(f"toolchain file is missing or unsafe: {relative}")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise ToolchainError(f"toolchain file ownership or permissions are unsafe: {relative}")
    if executable and not os.access(path, os.X_OK):
        raise ToolchainError(f"toolchain file is not executable: {relative}")
    return path


def _relative_link(toolchain_dir: Path, relative: str) -> Path:
    path, info, resolved = _safe_cached_path(toolchain_dir, relative)
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode)):
        raise ToolchainError(f"toolchain launcher is unsafe: {relative}")
    resolved_info = resolved.lstat()
    if not stat.S_ISREG(resolved_info.st_mode):
        raise ToolchainError(f"toolchain launcher is invalid: {relative}")
    if resolved_info.st_uid != os.geteuid() or stat.S_IMODE(resolved_info.st_mode) & 0o022:
        raise ToolchainError(
            f"toolchain launcher ownership or permissions are unsafe: {relative}"
        )
    return path


def _relative_directory(toolchain_dir: Path, relative: str) -> Path:
    path, info, resolved = _safe_cached_path(toolchain_dir, relative)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ToolchainError(f"toolchain directory is missing or unsafe: {relative}")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise ToolchainError(
            f"toolchain directory ownership or permissions are unsafe: {relative}"
        )
    return resolved


def _validate_yarn_corepack_marker(corepack_home: Path) -> None:
    root = _validated_real_directory(corepack_home, "Corepack home")
    markers = sorted(root.rglob(".corepack"))
    if len(markers) != 1:
        raise ToolchainError(
            "cached Yarn payload must contain exactly one Corepack integrity marker"
        )
    marker = markers[0]
    relative_marker = marker.relative_to(root).as_posix()
    marker = _relative_file(root, relative_marker)
    if marker.stat().st_size > 64 * 1024:
        raise ToolchainError("cached Yarn Corepack integrity marker is too large")
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ToolchainError("cached Yarn Corepack integrity marker is invalid") from exc
    expected_hash = f"sha512.{YARN_SHA512}"
    if not isinstance(value, dict) or value.get("hash") != expected_hash:
        raise ToolchainError("cached Yarn payload does not have the pinned SHA512")
    locator = value.get("locator")
    if locator != {
        "name": "yarn",
        "reference": f"{YARN_VERSION}+sha512.{YARN_SHA512}",
    }:
        raise ToolchainError("cached Yarn Corepack locator is not pinned")
    bins = value.get("bin")
    if not isinstance(bins, dict) or not isinstance(bins.get("yarn"), str):
        raise ToolchainError("cached Yarn Corepack launcher metadata is invalid")
    package_root = marker.parent.resolve(strict=True)
    yarn_bin = (marker.parent / bins["yarn"]).resolve(strict=True)
    if not _is_within(yarn_bin, package_root) or not yarn_bin.is_file():
        raise ToolchainError("cached Yarn payload launcher escapes its package")


def _metadata_static(spec: ArchiveSpec) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "cache_layout_version": CACHE_LAYOUT_VERSION,
        "system": spec.system,
        "machine": spec.machine,
        "platform_tag": spec.platform_tag,
        "node_version": NODE_VERSION,
        "yarn_version": YARN_VERSION,
        "yarn_locator": YARN_LOCATOR,
        "yarn_integrity": f"sha512.{YARN_SHA512}",
        "archive_filename": spec.filename,
        "archive_url": spec.url,
        "archive_sha256": spec.sha256,
        "archive_path": f"source/{spec.filename}",
        "node_path": "node/bin/node",
        "corepack_path": "node/lib/node_modules/corepack/dist/corepack.js",
        "yarn_path": "node/bin/yarn",
        "yarn_corepack_target": (
            "node/lib/node_modules/corepack/dist/yarn.js"
        ),
        "corepack_home": "corepack-home",
        "exec_env_path": "exec-env.sh",
    }


def _toolchain_result(
    toolchain_dir: Path,
    spec: ArchiveSpec,
    metadata: dict[str, Any],
    cache_state: str,
    started: float,
) -> dict[str, Any]:
    return {
        "cache_state": cache_state,
        "platform": spec.platform_tag,
        "toolchain_dir": os.fspath(toolchain_dir),
        "bin_dir": os.fspath(toolchain_dir / "node" / "bin"),
        "node_path": os.fspath(toolchain_dir / metadata["node_path"]),
        "yarn_path": os.fspath(toolchain_dir / metadata["yarn_path"]),
        "node_version": NODE_VERSION,
        "yarn_version": YARN_VERSION,
        "archive_url": spec.url,
        "archive_sha256": spec.sha256,
        "corepack_version": metadata["corepack_version"],
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def _validate_toolchain(
    final_dir: Path,
    spec: ArchiveSpec,
    runner: Runner | None = None,
) -> dict[str, Any]:
    info = final_dir.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ToolchainError("cached toolchain is not a real directory")
    if info.st_uid != os.geteuid():
        raise ToolchainError("cached toolchain is not owned by this user")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ToolchainError("cached toolchain must not be group/world writable")
    metadata_path = _relative_file(final_dir, "metadata.json")
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ToolchainError(f"cached toolchain metadata is invalid: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ToolchainError("cached toolchain metadata is invalid")
    static = _metadata_static(spec)
    for key, expected in static.items():
        if metadata.get(key) != expected:
            raise ToolchainError(f"cached toolchain metadata mismatch: {key}")
    expected_keys = set(static) | {
        "archive_observed_sha256",
        "node_binary_sha256",
        "corepack_sha256",
        "corepack_tree",
        "corepack_version",
        "yarn_launcher_sha256",
        "corepack_home_tree",
        "exec_env_sha256",
        "complete",
    }
    if set(metadata) != expected_keys or metadata.get("complete") is not True:
        raise ToolchainError("cached toolchain metadata shape is invalid")

    archive = _relative_file(final_dir, metadata["archive_path"])
    observed_archive = _sha256_file(archive)
    if observed_archive != spec.sha256 or metadata["archive_observed_sha256"] != spec.sha256:
        raise ToolchainError("cached Node archive checksum does not match the pinned SHA256")

    archive_hashes = _archive_critical_hashes(archive, spec)
    node = _relative_file(final_dir, metadata["node_path"], executable=True)
    if (
        _sha256_file(node) != archive_hashes["node"]
        or metadata.get("node_binary_sha256") != archive_hashes["node"]
    ):
        raise ToolchainError("cached Node binary checksum does not match metadata")
    corepack = _relative_file(final_dir, metadata["corepack_path"])
    if (
        _sha256_file(corepack) != archive_hashes["corepack"]
        or metadata.get("corepack_sha256") != archive_hashes["corepack"]
    ):
        raise ToolchainError("cached Corepack checksum does not match metadata")
    corepack_root = _relative_directory(
        final_dir, "node/lib/node_modules/corepack"
    )
    observed_corepack_tree = _tree_summary(corepack_root)
    if (
        observed_corepack_tree != archive_hashes["corepack_tree"]
        or metadata.get("corepack_tree") != archive_hashes["corepack_tree"]
    ):
        raise ToolchainError("cached Corepack tree does not match verified archive")
    yarn = _relative_link(final_dir, metadata["yarn_path"])
    yarn_resolved = yarn.resolve(strict=True)
    expected_yarn_target = _relative_file(
        final_dir, metadata["yarn_corepack_target"]
    )
    if yarn_resolved != expected_yarn_target:
        raise ToolchainError("cached Yarn launcher does not target local Corepack")
    if _sha256_file(yarn_resolved) != metadata.get("yarn_launcher_sha256"):
        raise ToolchainError("cached Yarn launcher checksum does not match metadata")
    exec_env = _relative_file(final_dir, metadata["exec_env_path"])
    expected_exec_env = _exec_env_bytes(final_dir)
    if (
        exec_env.read_bytes() != expected_exec_env
        or _sha256_file(exec_env) != metadata.get("exec_env_sha256")
    ):
        raise ToolchainError("cached exec environment checksum does not match metadata")
    corepack_home = _relative_directory(final_dir, metadata["corepack_home"])
    _validate_yarn_corepack_marker(corepack_home)
    if _tree_summary(corepack_home) != metadata.get("corepack_home_tree"):
        raise ToolchainError("cached Yarn/Corepack payload does not match metadata")

    env = _exec_env(final_dir)
    node_version = _checked_output(
        [os.fspath(node), "--version"],
        env=env,
        runner=runner,
        label="cached Node version check",
    )
    if node_version != NODE_VERSION:
        raise ToolchainError(
            f"cached Node version mismatch: expected {NODE_VERSION}, got {node_version!r}"
        )
    yarn_version = _checked_output(
        [os.fspath(yarn), "--version"],
        env=env,
        runner=runner,
        cwd=FRONTEND_ROOT,
        label="cached Yarn version check",
    )
    if yarn_version != YARN_VERSION:
        raise ToolchainError(
            f"cached Yarn version mismatch: expected {YARN_VERSION}, got {yarn_version!r}"
        )
    return metadata


def _remove_exact_path(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _clean_stale_stages(layout_root: Path, spec: ArchiveSpec) -> None:
    prefix = f".frontend-toolchain-{spec.platform_tag}-"
    for candidate in layout_root.iterdir():
        if candidate.name.startswith(prefix):
            _remove_exact_path(candidate)


def _atomic_publish(stage: Path, final_dir: Path) -> None:
    quarantine: Path | None = None
    if final_dir.exists() or final_dir.is_symlink():
        quarantine = final_dir.with_name(
            f".{final_dir.name}.corrupt-{uuid.uuid4().hex}"
        )
        os.replace(final_dir, quarantine)
    try:
        os.replace(stage, final_dir)
    except Exception:
        if quarantine is not None and not final_dir.exists():
            os.replace(quarantine, final_dir)
            quarantine = None
        raise
    finally:
        if quarantine is not None:
            _remove_exact_path(quarantine)


def _install_toolchain(
    spec: ArchiveSpec,
    cache_root: Path,
    final_dir: Path,
    downloader: Downloader | None = None,
    runner: Runner | None = None,
) -> None:
    cache_root.mkdir(parents=True, mode=0o700, exist_ok=True)
    cache_root = _validated_real_directory(cache_root, "toolchain install root")
    try:
        final_parent = final_dir.parent.resolve(strict=True)
    except OSError as exc:
        raise ToolchainError("toolchain destination parent cannot be resolved") from exc
    if final_parent != cache_root or not final_dir.name:
        raise ToolchainError("toolchain destination must be directly inside its cache")
    final_dir = cache_root / final_dir.name
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".frontend-toolchain-{spec.platform_tag}-",
            dir=cache_root,
        )
    )
    try:
        archive_tmp = stage / f".{spec.filename}.download"
        (downloader or _download_archive)(spec.url, archive_tmp)
        try:
            archive_info = archive_tmp.lstat()
        except OSError as exc:
            raise ToolchainError("Node archive download did not create a file") from exc
        if (
            not stat.S_ISREG(archive_info.st_mode)
            or archive_info.st_uid != os.geteuid()
            or stat.S_IMODE(archive_info.st_mode) & 0o022
        ):
            raise ToolchainError("downloaded Node archive file is unsafe")
        observed_sha = _sha256_file(archive_tmp)
        if observed_sha != spec.sha256:
            raise ToolchainError(
                "downloaded Node archive checksum mismatch: "
                f"expected {spec.sha256}, got {observed_sha}"
            )
        archive_hashes = _archive_critical_hashes(archive_tmp, spec)

        extracted = stage / "extracted"
        _safe_extract(archive_tmp, extracted, spec)
        node_root = stage / "node"
        os.replace(extracted / spec.extracted_root, node_root)
        _remove_exact_path(extracted)

        source_root = stage / "source"
        source_root.mkdir(mode=0o700)
        os.replace(archive_tmp, source_root / spec.filename)
        corepack_home = stage / "corepack-home"
        corepack_home.mkdir(mode=0o700)

        node = node_root / "bin" / "node"
        corepack = node_root / "lib" / "node_modules" / "corepack" / "dist" / "corepack.js"
        corepack_root = node_root / "lib" / "node_modules" / "corepack"
        if not node.is_file() or node.is_symlink() or not os.access(node, os.X_OK):
            raise ToolchainError("verified Node archive is missing bin/node")
        if not corepack.is_file() or corepack.is_symlink():
            raise ToolchainError("verified Node archive is missing its local Corepack")

        if _sha256_file(node) != archive_hashes["node"]:
            raise ToolchainError("extracted Node binary does not match verified archive")
        if _sha256_file(corepack) != archive_hashes["corepack"]:
            raise ToolchainError("extracted Corepack does not match verified archive")
        if _tree_summary(corepack_root) != archive_hashes["corepack_tree"]:
            raise ToolchainError(
                "extracted Corepack tree does not match verified archive"
            )

        stage_env = _base_toolchain_env(stage, allow_corepack_network=True)
        node_version = _checked_output(
            [os.fspath(node), "--version"],
            env=stage_env,
            runner=runner,
            label="installed Node version check",
        )
        if node_version != NODE_VERSION:
            raise ToolchainError(
                f"installed Node version mismatch: expected {NODE_VERSION}, got {node_version!r}"
            )
        corepack_version = _checked_output(
            [os.fspath(node), os.fspath(corepack), "--version"],
            env=stage_env,
            runner=runner,
            label="local Corepack version check",
        )
        _checked_output(
            [
                os.fspath(node),
                os.fspath(corepack),
                "enable",
                "--install-directory",
                os.fspath(node_root / "bin"),
                "yarn",
            ],
            env=stage_env,
            runner=runner,
            cwd=FRONTEND_ROOT,
            label="local Corepack enable",
        )
        _checked_output(
            [
                os.fspath(node),
                os.fspath(corepack),
                "prepare",
                YARN_LOCATOR,
                "--activate",
            ],
            env=stage_env,
            runner=runner,
            cwd=FRONTEND_ROOT,
            label="local Yarn activation",
            timeout=300,
        )
        yarn = node_root / "bin" / "yarn"
        if not yarn.exists() and not yarn.is_symlink():
            raise ToolchainError("local Corepack did not create a Yarn launcher")
        yarn_resolved = yarn.resolve(strict=True)
        if not _is_within(yarn_resolved, stage.resolve()):
            raise ToolchainError("local Yarn launcher escapes the staged toolchain")
        expected_yarn_target = (
            node_root / "lib" / "node_modules" / "corepack" / "dist" / "yarn.js"
        )
        if yarn_resolved != expected_yarn_target:
            raise ToolchainError("local Yarn launcher does not target bundled Corepack")
        yarn_version = _checked_output(
            [os.fspath(yarn), "--version"],
            env=stage_env,
            runner=runner,
            cwd=FRONTEND_ROOT,
            label="installed Yarn version check",
        )
        if yarn_version != YARN_VERSION:
            raise ToolchainError(
                f"installed Yarn version mismatch: expected {YARN_VERSION}, got {yarn_version!r}"
            )

        _validate_yarn_corepack_marker(corepack_home)

        _write_atomic(stage / "exec-env.sh", _exec_env_bytes(final_dir))

        metadata = {
            **_metadata_static(spec),
            "archive_observed_sha256": observed_sha,
            "node_binary_sha256": archive_hashes["node"],
            "corepack_sha256": archive_hashes["corepack"],
            "corepack_tree": archive_hashes["corepack_tree"],
            "corepack_version": corepack_version,
            "yarn_launcher_sha256": _sha256_file(yarn_resolved),
            "corepack_home_tree": _tree_summary(corepack_home),
            "exec_env_sha256": _sha256_file(stage / "exec-env.sh"),
            "complete": True,
        }
        _write_atomic(stage / "metadata.json", _canonical_json_bytes(metadata))
        _atomic_publish(stage, final_dir)
    except Exception as exc:
        if isinstance(exc, ToolchainError):
            raise
        raise ToolchainError(f"cannot install governed frontend toolchain: {exc}") from exc
    finally:
        if stage.exists() or stage.is_symlink():
            _remove_exact_path(stage)


def _ensure_locked(
    *,
    spec: ArchiveSpec,
    layout_root: Path,
    downloader: Downloader | None,
    runner: Runner | None,
    started: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    final_dir = _final_dir(layout_root, spec)
    existed = final_dir.exists() or final_dir.is_symlink()
    if existed:
        try:
            metadata = _validate_toolchain(final_dir, spec, runner)
            return metadata, _toolchain_result(
                final_dir, spec, metadata, "warm_verified", started
            )
        except (OSError, ToolchainError, ValueError):
            cache_state = "corrupt_reinstalled"
            _remove_exact_path(final_dir)
    else:
        cache_state = "cold_installed"

    _install_toolchain(
        spec,
        layout_root,
        final_dir,
        downloader=downloader,
        runner=runner,
    )
    metadata = _validate_toolchain(final_dir, spec, runner)
    return metadata, _toolchain_result(
        final_dir, spec, metadata, cache_state, started
    )


def ensure_toolchain(
    cache_root: Path | None = None,
    downloader: Downloader | None = None,
    runner: Runner | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    spec = select_archive()
    root = _prepare_cache_root(cache_root)
    layout = _layout_root(root)
    with _toolchain_lock(layout, spec):
        _clean_stale_stages(layout, spec)
        _, result = _ensure_locked(
            spec=spec,
            layout_root=layout,
            downloader=downloader,
            runner=runner,
            started=started,
        )
    return result


def execute(
    command: Sequence[str],
    cache_root: Path | None = None,
    downloader: Downloader | None = None,
    runner: Runner | None = None,
) -> int:
    if not command:
        raise ToolchainError("exec requires a command after --")
    started = time.monotonic()
    spec = select_archive()
    root = _prepare_cache_root(cache_root)
    layout = _layout_root(root)
    with _toolchain_lock(layout, spec):
        _clean_stale_stages(layout, spec)
        metadata, result = _ensure_locked(
            spec=spec,
            layout_root=layout,
            downloader=downloader,
            runner=runner,
            started=started,
        )
        final_dir = Path(result["toolchain_dir"])
        # _ensure_locked validates the retained archive, cached binaries, and
        # exact versions immediately before returning while this lock is held.
        env = _exec_env(final_dir)
        bin_dir = os.fspath(final_dir / "node" / "bin")
        if env["PATH"].split(os.pathsep, 1)[0] != bin_dir:
            raise ToolchainError("local toolchain PATH was not applied")
        node = final_dir / metadata["node_path"]
        yarn = final_dir / metadata["yarn_path"]
        if shutil.which("node", path=env["PATH"]) != os.fspath(node):
            raise ToolchainError("exec would not use the governed Node binary")
        if shutil.which("yarn", path=env["PATH"]) != os.fspath(yarn):
            raise ToolchainError("exec would not use the governed Yarn launcher")
        process = _invoke(
            list(command),
            env=env,
            runner=runner,
            cwd=REPO_ROOT,
            capture=False,
            timeout=24 * 60 * 60,
        )
        return (
            128 + abs(int(process.returncode))
            if process.returncode < 0
            else int(process.returncode)
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Manage the pinned Mezan frontend release toolchain"
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("ensure", help="install or verify the local toolchain")
    exec_parser = subparsers.add_parser(
        "exec", help="run a command with the local release toolchain"
    )
    exec_parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "ensure":
            print(json.dumps(ensure_toolchain(), ensure_ascii=False, indent=2))
            return 0
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        return execute(command)
    except ToolchainError as exc:
        print(f"FRONTEND_TOOLCHAIN_REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
