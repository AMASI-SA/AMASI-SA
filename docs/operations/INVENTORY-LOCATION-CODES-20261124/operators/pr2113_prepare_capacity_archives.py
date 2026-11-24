#!/usr/bin/env python3
"""Prepare verified private archives only; no deletion or space reclamation."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import tarfile
import tempfile
import time
import types

sys.dont_write_bytecode = True
ROOT = Path("/app")
D = "cb98e5660380d362e78f937d60abc7139cd1c462"
EXPECTED_UNTRACKED = 432
HELPER = ROOT / ".git/mezan-pr2113-recovery-operator-jyn9frvb/pr2113_recover_d_rehearsal.py"
HELPER_SHA256 = "4fe5070533e8f6308fc88516d1af80f8fa12f5523e7ecf8a7777317a4182b798"
BACKUP_STAGE = ROOT / ".git/mezan-pr2113-recover-d-cm4y9sgc"
TARGETS = (
    ("original-node-modules-backup", BACKUP_STAGE / "backups/frontend--node_modules"),
    ("failed-install-node-modules", ROOT / "frontend/node_modules"),
)
KNOWN_MANIFEST = BACKUP_STAGE / "frontend--node_modules-manifest.json"
H = None


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RuntimeError(code)


def real_directory(path: Path) -> None:
    require(path.resolve(strict=True) == path and stat.S_ISDIR(path.lstat().st_mode),
            "DIRECTORY_MUST_BE_REAL_AND_ORDINARY")


def read_regular(path: Path) -> bytes:
    require(path.resolve(strict=True) == path, "INPUT_PATH_HAS_SYMLINK")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "INPUT_MUST_BE_REGULAR")
        return stream.read()


def load_verified_module(name: str, path: Path, data: bytes):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    # Execute only the exact verified module bytes; its __main__ branch is false.
    exec(compile(data, str(path), "exec"), module.__dict__)
    return module


def relative_parent(root_fd: int, relative: str) -> tuple[int, str]:
    descriptor = os.dup(root_fd)
    parts = Path(relative).parts or (".",)
    require(not Path(relative).is_absolute() and ".." not in parts, "UNSAFE_MANIFEST_PATH")
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, parts[-1]
    except BaseException:
        os.close(descriptor)
        raise


def member_name(relative: str) -> str:
    return "payload" if relative == "." else "payload/" + relative


def write_archive(source: Path, records: dict, destination: Path) -> None:
    root_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0, compresslevel=6) as compressed:
                with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT,
                                  dereference=False) as archive:
                    for relative, record in sorted(records.items()):
                        parent_fd, name = relative_parent(root_fd, relative)
                        try:
                            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                            require(stat.S_IMODE(info.st_mode) == record["mode"], "SOURCE_MODE_CHANGED")
                            member = tarfile.TarInfo(member_name(relative))
                            member.mode = record["mode"]
                            member.uid = member.gid = member.mtime = 0
                            member.uname = member.gname = ""
                            if record["type"] == "directory":
                                require(stat.S_ISDIR(info.st_mode), "SOURCE_TYPE_CHANGED")
                                member.type = tarfile.DIRTYPE
                                archive.addfile(member)
                            elif record["type"] == "symlink":
                                require(stat.S_ISLNK(info.st_mode), "SOURCE_TYPE_CHANGED")
                                member.linkname = os.readlink(name, dir_fd=parent_fd)
                                value = os.fsencode(member.linkname)
                                require(len(value) == record["bytes"] and hashlib.sha256(value).hexdigest()
                                        == record["sha256"], "SOURCE_LINK_CHANGED")
                                member.type = tarfile.SYMTYPE
                                archive.addfile(member)
                            else:
                                require(record["type"] == "file" and stat.S_ISREG(info.st_mode),
                                        "SOURCE_TYPE_UNSUPPORTED")
                                source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
                                with os.fdopen(source_fd, "rb") as stream:
                                    opened = os.fstat(stream.fileno())
                                    require((opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size)
                                            == (info.st_dev, info.st_ino, info.st_mode, record["bytes"]),
                                            "SOURCE_FILE_RACED")
                                    member.type, member.size = tarfile.REGTYPE, record["bytes"]
                                    archive.addfile(member, stream)
                        finally:
                            os.close(parent_fd)
            output.flush()
            os.fsync(output.fileno())
        H.fsync_directory(destination.parent)
    finally:
        os.close(root_fd)


def verify_archive(path: Path, records: dict) -> dict:
    expected = {member_name(relative): record for relative, record in records.items()}
    seen = set()
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive:
            require(member.name in expected and member.name not in seen, "ARCHIVE_EXTRA_OR_DUPLICATE")
            seen.add(member.name)
            record = expected[member.name]
            require(member.mode == record["mode"], "ARCHIVE_MODE_MISMATCH")
            if record["type"] == "directory":
                require(member.type == tarfile.DIRTYPE and member.size == 0, "ARCHIVE_DIRECTORY_MISMATCH")
            elif record["type"] == "symlink":
                value = os.fsencode(member.linkname)
                require(member.type == tarfile.SYMTYPE and member.size == 0
                        and len(value) == record["bytes"]
                        and hashlib.sha256(value).hexdigest() == record["sha256"], "ARCHIVE_LINK_MISMATCH")
            else:
                require(record["type"] == "file" and member.type == tarfile.REGTYPE
                        and member.size == record["bytes"], "ARCHIVE_FILE_MISMATCH")
                # This reads a regular member stream only; no filesystem extraction.
                stream = archive.extractfile(member)
                require(stream is not None, "ARCHIVE_FILE_UNREADABLE")
                digest, count = hashlib.sha256(), 0
                with stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                        count += len(chunk)
                require(count == record["bytes"] and digest.hexdigest() == record["sha256"],
                        "ARCHIVE_CONTENT_MISMATCH")
        require(seen == set(expected), "ARCHIVE_MEMBERS_MISSING")
        # Consume remaining zero padding and the gzip trailer without extracting.
        for chunk in iter(lambda: archive.fileobj.read(1024 * 1024), b""):
            require(not any(chunk), "ARCHIVE_NONZERO_TRAILING_PAYLOAD")
    return {"verified_members": len(seen), "verification": "exact path/type/mode/bytes/SHA256 or symlink target",
            "filesystem_extraction_performed": False}


def prepare_one(label: str, source: Path, known: dict | None) -> None:
    H.STEP = "archive-" + label
    real_directory(source)
    H.reject_generated_mounts(source)
    before = H.generated_manifest(source)
    if known is not None:
        require(before == known, "ORIGINAL_BACKUP_DIFFERS_FROM_KNOWN_MANIFEST")
    H.private_json(H.STAGE / (label + "-before.json"), before)
    row = {"label": label, "source": str(source), "status": "RUNNING",
           "source_entries": len(before), "source_regular_bytes": sum(
               item["bytes"] for item in before.values() if item["type"] == "file"),
           "known_backup_manifest_matches": True if known is not None else None}
    H.RECEIPT["archives"].append(row)
    H.save_receipt()
    started = time.monotonic()
    destination = H.STAGE / (label + ".tar.gz")
    write_archive(source, before, destination)
    verified = verify_archive(destination, before)
    after = H.generated_manifest(source)
    require(after == before, "SOURCE_MANIFEST_CHANGED_DURING_ARCHIVING")
    H.private_json(H.STAGE / (label + "-after.json"), after)
    row.update(status="PASS", archive=str(destination), archive_record=H.file_fingerprint(destination),
               source_manifest_stable=True, elapsed_seconds=round(time.monotonic() - started, 3), **verified)
    H.save_receipt()


def main() -> int:
    global H
    try:
        require(len(sys.argv) == 1, "NO_ARGUMENTS_ACCEPTED")
        require(Path.cwd() == ROOT, "RUN_FROM_APP_ROOT")
        for path in (ROOT, ROOT / ".git", ROOT / "frontend", Path("/tmp")):
            real_directory(path)
        helper_bytes = read_regular(HELPER)
        require(hashlib.sha256(helper_bytes).hexdigest() == HELPER_SHA256, "REVIEWED_HELPER_HASH_MISMATCH")
        H = load_verified_module("pr2113_capacity_reviewed_functions", HELPER, helper_bytes)
        H.STAGE = Path(tempfile.mkdtemp(prefix="mezan-pr2113-capacity-", dir="/tmp"))
        H.STAGE.chmod(0o700)
        H.ENV["GIT_OPTIONAL_LOCKS"] = "0"
        H.RECEIPT = {"task": "PR2113_capacity_archive_preparation", "status": "RUNNING",
                     "stage": str(H.STAGE), "deployment_sha": D, "expected_untracked_count": EXPECTED_UNTRACKED,
                     "reviewed_helper_sha256": HELPER_SHA256, "steps": [], "archives": [],
                     "scope": "archive preparation only; fresh preservation baseline",
                     "app_data_mutation": False, "deletion_performed": False,
                     "git_mutation": False, "git_packing_performed": False, "space_reclaimed_bytes": 0}
        H.save_receipt()
        print("STAGE_PATH=" + str(H.STAGE), flush=True)
        H.assert_checkout(D)
        require(H.text_git("git-directory", "rev-parse", "--absolute-git-dir") == str(ROOT / ".git"),
                "UNSUPPORTED_GIT_DIRECTORY")
        guard_path = ROOT / "scripts/production_release_guard.py"
        guard_bytes = read_regular(guard_path)
        require(guard_bytes == H.git("original-guard-bytes", "show", H.J + ":scripts/production_release_guard.py"),
                "ORIGINAL_GUARD_CHANGED")
        status = json.loads(H.run("original-public-status", [sys.executable, str(guard_path), "status"],
                                  capture=True, timeout=45))
        require(status.get("active") is False, "ACTIVE_RELEASE_LEASE")
        H.RECEIPT["initial_public_status"] = {"active": False}
        guard = load_verified_module("pr2113_capacity_original_guard", guard_path, guard_bytes)
        require(guard.REPO_ROOT == ROOT and guard.GIT_DIR == ROOT / ".git", "GUARD_ROOT_MISMATCH")
        for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
            signal.signal(signum, H.interrupt)
        H.STEP = "original-operation-lock"
        signal.alarm(30)
        with guard._release_operation_lock():
            signal.alarm(0)
            H.assert_no_lease(guard)
            H.assert_checkout(D)
            before = H.untracked_snapshot()
            require(len(before["git_entries"]) == EXPECTED_UNTRACKED, "UNTRACKED_COUNT_CHANGED")
            if H.RUNTIME_PATH in before["records"]:
                info = (ROOT / H.RUNTIME_PATH).lstat()
                require(stat.S_ISREG(info.st_mode), "RUNTIME_METADATA_TYPE_INVALID")
                H.RUNTIME_IDENTITY = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode))
            H.private_json(H.STAGE / "untracked-before.json", before)
            known_bytes = read_regular(KNOWN_MANIFEST)
            known = json.loads(known_bytes)
            H.RECEIPT["known_backup_manifest"] = {"path": str(KNOWN_MANIFEST), "bytes": len(known_bytes),
                                                  "sha256": hashlib.sha256(known_bytes).hexdigest()}
            for ordinal, (label, source) in enumerate(TARGETS):
                H.assert_no_lease(guard)
                H.assert_checkout(D)
                prepare_one(label, source, known if ordinal == 0 else None)
                H.preserve_foreign(before)
            require(read_regular(KNOWN_MANIFEST) == known_bytes, "KNOWN_BACKUP_MANIFEST_CHANGED")
            H.assert_checkout(D)
            H.preserve_foreign(before)
            H.assert_no_lease(guard)
            H.RECEIPT.update(status="PASS", lease_created=False, published=False,
                             unchanged_untracked_count=EXPECTED_UNTRACKED)
            H.save_receipt()
        print("FINAL_STATUS=PASS:ARCHIVE_PREPARATION_ONLY:NO_SPACE_RECLAIMED", flush=True)
        return 0
    except BaseException as exc:
        signal.alarm(0)
        code = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        if isinstance(exc, OSError):
            code += ":errno=" + str(exc.errno)
        if H is not None and H.STAGE is not None:
            H.RECEIPT.update(status="BLOCKED", failure_step=H.STEP, failure_code=code)
            try:
                H.save_receipt()
            except BaseException:
                pass
        print("FINAL_STATUS=BLOCKED:" + code, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
