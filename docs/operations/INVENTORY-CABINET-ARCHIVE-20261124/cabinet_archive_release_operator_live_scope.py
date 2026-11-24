#!/usr/bin/env python3
"""Cabinet archive release: owned clone rehearsal, then guarded /app FF and adapter.

External prerequisite: root has reviewed this helper, its exact envelope, and successful exact-D CI.
All logs, clone, cache and generated-output backups stay in a unique owned /tmp stage.
No prepared lease, publish, service/preview changes, capacity cleanup, or rollback.
This helper does not prepare a lease, publish, restart, recalculate, or roll back.
On failure it leaves its private receipts/backups in place for owner review.
One reviewed live Mongo dbpath is protected by process/root identities rather
than content equality; its separate log must retain every pre-existing byte.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
ROOT = Path("/app")
BRANCH = "hotfix/prod-snap-meta-final"
J = "c6d05c7814e27e2f22a8c82b5bfe4cf177c17fbe"
A = "b236f51bb6c52e6f5d14a136db92f7474e4f5873"
B = "bdebc069ea5fa980cd16725e422ca70e79dadd17"
TREE = "74946da23e059283f49de2b04baf9860c8ee972d"
RELEASE_ID = "rg5-6cfb0e514bb3c65c229a9d224c329f24136bd0ce483bed1af3b74b05e848ca42"
D = START = EXPECTED_UNTRACKED = None
ALLOW_RUNTIME_APPEND = False
CLONE = None
GIT_OPTIONS = ["-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "core.hooksPath=/dev/null"]
RUNTIME_PATH = ".emergent/stripped_env_names"
RUNTIME_IDENTITY = None
LIVE_DB = ".worktrees/p01-acceptance-state-20260919/mongo"
LIVE_LOG = ".worktrees/p01-acceptance-state-20260919/mongo.log"
LIVE_SCOPE = {
    "dbpath": LIVE_DB,
    "db_descendants_policy": "live_writer_managed_no_operator_access",
    "process": {
        "pid": 96507, "start_ticks": 106766690,
        "cmdline_sha256": "3afd2534eb5355d9b66e77ed088d8d3db819115b34d0e8d2901e052468cd2832",
        "exe": "/usr/bin/mongod", "exe_identity": [3145868, 85851, 493, 0, 0],
    },
    "directory_identities": {
        ".worktrees": [66338, 394231, 493, 0, 0],
        ".worktrees/p01-acceptance-state-20260919": [66338, 396081, 448, 0, 0],
        LIVE_DB: [66338, 396082, 493, 0, 0],
    },
    "logpath": LIVE_LOG,
    "log_identity": [66338, 121637, 420, 0, 0],
    "log_policy": "independent_same_inode_append_only",
}
CLEANUP = (
    "frontend/build", "frontend/.release", "backend/release_identity.json",
    "frontend/node_modules",
)
STAGE: Path | None = None
STEP = "startup"
RECEIPT: dict = {
    "task": "cabinet_archive_owned_clone_and_app_rehearsal", "status": "RUNNING",
    "initial_head": START, "source_sha": A, "intent_sha": B,
    "deployment_sha": D, "tree": TREE, "release_id": RELEASE_ID,
    "external_precondition": "operator verified completed successful exact-D CI",
    "steps": [], "backups": [],
}
ENV = os.environ.copy()
ENV["PYTHONDONTWRITEBYTECODE"] = "1"
ENV["GIT_TERMINAL_PROMPT"] = "0"
ENV["GIT_OPTIONAL_LOCKS"] = "0"


class Blocked(RuntimeError):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise Blocked(code)


def private_json(path: Path, payload: object) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=True, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fsync_directory(path.parent)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_receipt() -> None:
    if STAGE is not None:
        private_json(STAGE / "receipt.json", RECEIPT)


def terminate_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # The leader may already have exited while descendants remain alive.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=10)


def run(name: str, argv: list[str], *, cwd: Path = ROOT,
        environment: dict | None = None, timeout: int = 120,
        capture: bool = False) -> bytes:
    global STEP
    STEP = name
    row = {"step": name, "status": "RUNNING", "timeout_seconds": timeout}
    RECEIPT["steps"].append(row)
    save_receipt()
    # Never print subprocess output or inherited environment to the terminal.
    output = tempfile.TemporaryFile() if STAGE is None else os.fdopen(
        os.open(STAGE / (str(len(RECEIPT["steps"])) + "-" + name + ".log"),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
    started = time.monotonic()
    process = None
    try:
        with output:
            process = subprocess.Popen(
                argv, cwd=cwd, env=environment or ENV, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else output, stderr=output,
                start_new_session=True,
            )
            try:
                data, _ = process.communicate(timeout=timeout)
            except BaseException:
                terminate_group(process)
                raise
            row["exit_code"] = process.returncode
            require(process.returncode == 0, name + "_FAILED")
            row["status"] = "PASS"
            return data or b""
    finally:
        if row["status"] != "PASS":
            row["status"] = "FAILED"
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
        save_receipt()


def git(name: str, *args: str, repo: Path = ROOT) -> bytes:
    return run(name, ["git", *GIT_OPTIONS, "-C", str(repo), *args], capture=True)


def text_git(name: str, *args: str, repo: Path = ROOT) -> str:
    return git(name, *args, repo=repo).decode("utf-8").strip()


def assert_checkout(expected: str, *, repo: Path = ROOT) -> None:
    require(text_git("current-head", "rev-parse", "HEAD", repo=repo) == expected, "HEAD_CHANGED")
    require(text_git("current-branch", "symbolic-ref", "--short", "HEAD", repo=repo) == BRANCH,
            "WRONG_BRANCH")
    require(not git("tracked-status", "status", "--porcelain=v1", "--untracked-files=no", repo=repo),
            "TRACKED_WORKTREE_DIRTY")


def fetch_expected() -> None:
    git("fetch-production", "fetch", "--no-tags", "--no-auto-maintenance", "origin", BRANCH)
    require(text_git("remote-head", "rev-parse", "origin/" + BRANCH) == D,
            "REMOTE_HEAD_CHANGED")
    require(text_git("fetched-head", "rev-parse", "FETCH_HEAD") == D,
            "FETCH_HEAD_CHANGED")


def file_fingerprint(path: Path) -> dict:
    before = path.lstat()
    result = {"mode": stat.S_IMODE(before.st_mode)}
    if stat.S_ISLNK(before.st_mode):
        value = os.fsencode(os.readlink(path))
        result.update(type="symlink", bytes=len(value), sha256=hashlib.sha256(value).hexdigest())
    elif stat.S_ISDIR(before.st_mode):
        result.update(type="directory")
    elif stat.S_ISREG(before.st_mode):
        digest = hashlib.sha256()
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            require(os.fstat(stream.fileno()).st_ino == before.st_ino, "UNTRACKED_FILE_RACED")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        result.update(type="file", bytes=before.st_size, sha256=digest.hexdigest())
    else:
        raise Blocked("UNSUPPORTED_UNTRACKED_ENTRY")
    after = path.lstat()
    require((before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns)
            == (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns),
            "UNTRACKED_ENTRY_CHANGED_DURING_READ")
    return result


def live_identity(info: os.stat_result) -> list[int]:
    return [info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid]


def assert_live_write_disjoint(*paths: Path) -> None:
    for path in paths:
        require(path.is_absolute(), "LIVE_SCOPE_WRITE_PATH_NOT_ABSOLUTE")
        for candidate in (path, path.resolve(strict=False)):
            for protected in (ROOT / LIVE_DB, ROOT / LIVE_LOG):
                require(candidate != protected and candidate not in protected.parents
                        and protected not in candidate.parents, "WRITE_OVERLAPS_LIVE_FOREIGN_SCOPE")


def assert_live_scope() -> None:
    """Read process/root metadata only; never query, stop, or traverse the DB."""
    process = LIVE_SCOPE["process"]
    proc = Path("/proc") / str(process["pid"])

    def start_ticks() -> int:
        value = (proc / "stat").read_text()
        require(value.split("(", 1)[0].strip() == str(process["pid"]), "LIVE_PROCESS_PID_CHANGED")
        fields = value[value.rfind(")") + 2:].split()
        require(len(fields) > 19 and fields[0] not in {"Z", "X", "x"}, "LIVE_PROCESS_NOT_RUNNING")
        return int(fields[19])

    try:
        require(start_ticks() == process["start_ticks"], "LIVE_PROCESS_START_CHANGED")
        command = (proc / "cmdline").read_bytes()
        require(hashlib.sha256(command).hexdigest() == process["cmdline_sha256"], "LIVE_PROCESS_COMMAND_CHANGED")
        arguments = command.rstrip(b"\0").split(b"\0")
        position = arguments.index(b"--dbpath")
        require(arguments[position + 1] == os.fsencode(ROOT / LIVE_DB), "LIVE_PROCESS_DBPATH_CHANGED")
        require(os.readlink(proc / "exe") == process["exe"], "LIVE_PROCESS_EXECUTABLE_CHANGED")
        for info in ((proc / "exe").stat(), Path(process["exe"]).lstat()):
            require(stat.S_ISREG(info.st_mode) and live_identity(info) == process["exe_identity"],
                    "LIVE_PROCESS_EXECUTABLE_IDENTITY_CHANGED")
        for relative, expected in LIVE_SCOPE["directory_identities"].items():
            path = ROOT / relative
            real_ancestors(path, ROOT)
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode) and live_identity(info) == expected,
                    "LIVE_DIRECTORY_IDENTITY_CHANGED")
        real_ancestors(ROOT / LIVE_LOG, ROOT)
        log = (ROOT / LIVE_LOG).lstat()
        require(stat.S_ISREG(log.st_mode) and live_identity(log) == LIVE_SCOPE["log_identity"],
                "LIVE_LOG_IDENTITY_CHANGED")
        require(start_ticks() == process["start_ticks"], "LIVE_PROCESS_START_CHANGED")
    except (OSError, ValueError, IndexError) as exc:
        raise Blocked("LIVE_SCOPE_IDENTITY_UNAVAILABLE") from exc
    assert_live_write_disjoint(ROOT / ".git", ROOT / RUNTIME_PATH, *(ROOT / name for name in CLEANUP))
    if STAGE is not None:
        assert_live_write_disjoint(STAGE)


def live_log_fingerprint(prefix_bytes: int | None = None) -> dict:
    """Hash a bounded prefix; appends during this read are allowed, replacement is not."""
    path = ROOT / LIVE_LOG
    real_ancestors(path, ROOT)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and live_identity(before) == LIVE_SCOPE["log_identity"],
            "LIVE_LOG_IDENTITY_CHANGED")
    length = before.st_size if prefix_bytes is None else prefix_bytes
    require(type(length) is int and 0 <= length <= before.st_size, "LIVE_LOG_TRUNCATED")
    digest, left = hashlib.sha256(), length
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode) and live_identity(opened) == LIVE_SCOPE["log_identity"]
                and opened.st_size >= length, "LIVE_LOG_OPEN_IDENTITY_CHANGED")
        while left:
            value = stream.read(min(1024 * 1024, left))
            require(bool(value), "LIVE_LOG_TRUNCATED_DURING_READ")
            digest.update(value)
            left -= len(value)
        after_fd = os.fstat(stream.fileno())
    after = path.lstat()
    require(all(stat.S_ISREG(info.st_mode) and live_identity(info) == LIVE_SCOPE["log_identity"]
                and info.st_size >= length for info in (after_fd, after)), "LIVE_LOG_CHANGED_DURING_READ")
    return {"type": "file", "mode": stat.S_IMODE(before.st_mode), "bytes": length,
            "sha256": digest.hexdigest()}


def untracked_snapshot() -> dict:
    assert_live_scope()
    def names(*arguments):
        return sorted(os.fsdecode(value).rstrip("/") for value in
                      git("foreign-paths", "ls-files", "--others", *arguments, "-z").split(b"\0") if value)
    entries = names("--exclude-standard")
    ignored = names("--ignored", "--exclude-standard", "--directory")
    require(not any(name == LIVE_DB or name.startswith(LIVE_DB + "/") or name == LIVE_LOG for name in entries),
            "LIVE_SCOPE_NOT_IGNORED")
    require(all(any(name == scope or scope.startswith(name + "/") for name in ignored)
                for scope in (LIVE_DB, LIVE_LOG)), "LIVE_SCOPE_MISSING_FROM_IGNORED_INVENTORY")
    records = {}

    def generated(relative):
        return any(relative == item or relative.startswith(item + "/") for item in CLEANUP)

    def visit(path: Path) -> None:
        relative = path.relative_to(ROOT).as_posix()
        require(relative != "." and ".." not in Path(relative).parts, "UNSAFE_FOREIGN_PATH")
        if generated(relative):
            return  # Preserved independently by verified generated-output backups.
        if relative.startswith(LIVE_DB + "/"):
            return  # No reads or writes inside the one reviewed live dbpath.
        real_ancestors(path, ROOT)
        if relative == LIVE_DB:
            info = path.lstat()
            require(stat.S_ISDIR(info.st_mode) and live_identity(info) == LIVE_SCOPE["directory_identities"][LIVE_DB],
                    "LIVE_DIRECTORY_IDENTITY_CHANGED")
            records[relative] = {"type": "live_directory", "identity": live_identity(info)}
            return
        if relative == LIVE_LOG:
            records[relative] = live_log_fingerprint()
            return
        records[relative] = file_fingerprint(path)
        if records[relative]["type"] == "directory":
            for child in sorted(path.iterdir()):
                visit(child)

    for relative in entries:
        require(not any(relative == item or relative.startswith(item + "/")
                        or item.startswith(relative + "/") for item in CLEANUP),
                "FOREIGN_NONIGNORED_OVERLAPS_ADAPTER_CLEANUP")
    kept_ignored = [name for name in ignored if not generated(name)]
    for relative in sorted(set(entries + kept_ignored)):
        visit(ROOT / relative)
    require(LIVE_DB in records and LIVE_LOG in records, "LIVE_SCOPE_ROOTS_NOT_RECORDED")
    assert_live_scope()
    return {"git_entries": entries, "ignored_entries": kept_ignored, "records": records}


def assert_no_lease(guard: object) -> None:
    require(not os.path.lexists(guard.LEASE_DIR), "ACTIVE_OR_INCOMPLETE_LEASE_DIRECTORY")
    require(not os.path.lexists(guard.LEASE_PATH), "ACTIVE_LEASE")


def preserve_foreign(before: dict) -> None:
    after = untracked_snapshot()
    private_json(STAGE / "foreign-latest.json", after)
    require(after["git_entries"] == before["git_entries"]
            and after["ignored_entries"] == before["ignored_entries"], "FOREIGN_PATH_SET_CHANGED")
    require(set(after["records"]) == set(before["records"]), "FOREIGN_RECORD_SET_CHANGED")
    for name, record in before["records"].items():
        actual = after["records"].get(name)
        if name == LIVE_LOG:
            require(actual["bytes"] >= record["bytes"] and live_log_fingerprint(record["bytes"]) == record,
                    "LIVE_LOG_EXISTING_PREFIX_CHANGED")
            continue
        if not (ALLOW_RUNTIME_APPEND and name == RUNTIME_PATH):
            require(actual == record, "FOREIGN_ENTRY_CHANGED")
            continue
        info = (ROOT / name).lstat()
        require(stat.S_ISREG(info.st_mode)
                and (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)) == RUNTIME_IDENTITY,
                "RUNTIME_METADATA_IDENTITY_CHANGED")
        if actual == record:
            continue
        require(record["type"] == actual.get("type") == "file"
                and actual["mode"] == record["mode"] and actual["bytes"] >= record["bytes"],
                "RUNTIME_METADATA_NOT_APPEND_ONLY")
        descriptor = os.open(ROOT / name, os.O_RDONLY | os.O_NOFOLLOW)
        digest, left = hashlib.sha256(), record["bytes"]
        with os.fdopen(descriptor, "rb") as stream:
            while left:
                value = stream.read(min(1024 * 1024, left))
                require(bool(value), "RUNTIME_METADATA_TRUNCATED")
                digest.update(value)
                left -= len(value)
        require(digest.hexdigest() == record["sha256"], "RUNTIME_METADATA_PREFIX_CHANGED")
    RECEIPT["foreign_preservation"] = {
        "nonignored_paths": len(before["git_entries"]), "ignored_paths": len(before["ignored_entries"]),
        "records": len(before["records"]),
        "identical_entries": sum(after["records"][name] == record for name, record in before["records"].items()),
        "append_only_path": RUNTIME_PATH if after["records"].get(RUNTIME_PATH) != before["records"].get(RUNTIME_PATH) else None,
        "live_db_root_identity_preserved": LIVE_DB,
        "live_db_descendant_bytes_or_paths_compared": False,
        "live_log_identity_and_original_prefix_preserved": LIVE_LOG,
        "live_log_original_prefix_bytes": before["records"][LIVE_LOG]["bytes"],
        "live_log_original_prefix_sha256": before["records"][LIVE_LOG]["sha256"],
        "live_log_latest_observed_bytes": after["records"][LIVE_LOG]["bytes"],
    }
    save_receipt()


def generated_manifest(root: Path) -> dict:
    records = {}
    def visit(path):
        key = path.relative_to(root).as_posix()
        records[key] = file_fingerprint(path)
        if records[key]["type"] == "directory":
            for child in sorted(path.iterdir()):
                visit(child)
    visit(root)
    return records


def reject_generated_mounts(root: Path) -> None:
    # Device numbers alone do not identify nested mounts on OverlayFS.
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        point = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), line.split()[4])
        require(point != str(root) and not point.startswith(str(root).rstrip("/") + "/"),
                "GENERATED_TARGET_CONTAINS_MOUNT")


def sync_backup_tree(root: Path, records: dict) -> None:
    for name, item in records.items():
        if item["type"] == "file":
            descriptor = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    directories = [root / name for name, item in records.items() if item["type"] == "directory"]
    for path in sorted(directories, key=lambda value: len(value.parts), reverse=True) + [root.parent]:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def backup_generated_outputs() -> None:
    global STEP
    backups = STAGE / "backups"
    backups.mkdir(mode=0o700)
    for relative in CLEANUP:
        STEP = "copy-generated-output"
        source = ROOT / relative
        real_ancestors(source, ROOT)
        original = checked_cleanup_entry(relative)
        entry = {"source": relative, "present": original is not None}
        RECEIPT["backups"].append(entry)
        save_receipt()
        if original is None:
            continue
        reject_generated_mounts(source)
        expected = generated_manifest(source)
        storage = storage_measure([source], backups)
        free = free_space(backups)
        entry.update(required_copy_bytes=storage["required_bytes"], available_bytes=free["bytes"])
        save_receipt()
        require(free["bytes"] >= storage["required_bytes"] and free["inodes"] >= storage["entries"],
                "OWNED_BACKUP_CAPACITY_INSUFFICIENT")
        destination = backups / relative.replace("/", "--")
        require(not os.path.lexists(destination), "BACKUP_DESTINATION_EXISTS")
        if stat.S_ISLNK(original.st_mode):
            os.symlink(os.readlink(source), destination)
        elif stat.S_ISDIR(original.st_mode):
            shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
        else:
            shutil.copy2(source, destination, follow_symlinks=False)
        require(generated_manifest(destination) == expected == generated_manifest(source),
                "GENERATED_BACKUP_OR_SOURCE_CHANGED")
        sync_backup_tree(destination, expected)
        private_json(STAGE / (relative.replace("/", "--") + "-manifest.json"), expected)
        entry.update(destination=str(destination), state="COPY_VERIFIED", entries=len(expected),
                     bytes=sum(item.get("bytes", 0) for item in expected.values()))
        save_receipt()


def checked_cleanup_entry(relative: str):
    source = ROOT / relative
    if not os.path.lexists(source):
        return None
    info = source.lstat()
    expected_type = stat.S_ISREG if relative == "backend/release_identity.json" else stat.S_ISDIR
    require(stat.S_ISLNK(info.st_mode) or expected_type(info.st_mode), "UNSAFE_CLEANUP_TARGET_TYPE")
    require(stat.S_ISLNK(info.st_mode) or not (stat.S_IMODE(info.st_mode) & 0o7000),
            "UNSAFE_CLEANUP_TARGET_MODE")
    return info


def validate_intent(repo: Path) -> None:
    code = (
        "from scripts.emergent_deployment_adapter import load_release_intent; "
        "i=load_release_intent(); "
        f"assert i['source_git_sha']=={A!r}; "
        f"assert i['source_base_git_sha']=={J!r}; "
        f"assert i['runtime_identity']['release_id']=={RELEASE_ID!r}; "
        "print('UNCHANGED_INTENT_VALIDATOR_PASS')"
    )
    run("validate-intent", [sys.executable, "-c", code], cwd=repo, environment=build_environment(repo))


def protected_rehearsal(guard: object) -> None:
    global STAGE, CLONE, STEP, RUNTIME_IDENTITY
    # status() itself acquires this lock. Never invoke it while holding the lock.
    assert_no_lease(guard)
    for parent in (ROOT, ROOT / "frontend", ROOT / "backend", ROOT / ".git", Path("/tmp")):
        require(parent.is_dir() and not parent.is_symlink() and parent.resolve(strict=True) == parent,
                "UNSAFE_WORKSPACE_ROOT")
    assert_checkout(START)
    assert_live_scope()
    STAGE = Path(tempfile.mkdtemp(prefix="mezan-cabinet-archive-release-live-", dir="/tmp"))
    STAGE.chmod(0o700)
    fsync_directory(STAGE)
    fsync_directory(STAGE.parent)
    for name in ("cache", "tmp"):
        (STAGE / name).mkdir(mode=0o700)
    RECEIPT["stage"] = str(STAGE)
    RECEIPT["temporary_stage_retention"] = "No automatic cleanup; root must persist receipts. Interruption is not resumable proof."
    helper_bytes = Path(__file__).read_bytes()
    descriptor = os.open(STAGE / "operator-helper.py", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(helper_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    RECEIPT["helper_sha256"] = hashlib.sha256(helper_bytes).hexdigest()
    RECEIPT["live_foreign_scope"] = LIVE_SCOPE
    RECEIPT["preservation_claim"] = "Fresh baseline only; live DB descendants are not accessed or compared. All other foreign paths retain exact equality except the two explicit append-only paths."
    save_receipt()
    print("STAGE_PATH=" + str(STAGE), flush=True)
    before = untracked_snapshot()
    require(len(before["git_entries"]) == EXPECTED_UNTRACKED, "NONIGNORED_PATH_COUNT_CHANGED")
    if ALLOW_RUNTIME_APPEND and RUNTIME_PATH in before["records"]:
        info = (ROOT / RUNTIME_PATH).lstat()
        require(stat.S_ISREG(info.st_mode), "RUNTIME_METADATA_TYPE_INVALID")
        RUNTIME_IDENTITY = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode))
    private_json(STAGE / "foreign-before.json", before)
    RECEIPT["initial_capacity"] = {"app": free_space(ROOT), "owned_stage": free_space(STAGE)}
    save_receipt()
    require(RECEIPT["initial_capacity"]["app"]["bytes"] > 0, "APP_VOLUME_FULL_BEFORE_FETCH")
    require(RECEIPT["initial_capacity"]["owned_stage"]["bytes"] > 0, "OWNED_REHEARSAL_VOLUME_FULL")
    fetch_expected()
    validate_lineage(ROOT)
    check_incoming_paths()
    remote = text_git("origin-url", "remote", "get-url", "origin")
    require(bool(remote) and "\n" not in remote and "\0" not in remote, "INVALID_ORIGIN_URL")
    CLONE = STAGE / "rehearsal"
    require(not os.path.lexists(CLONE), "OWNED_CLONE_ALREADY_EXISTS")
    run("clone-deployment", ["git", *GIT_OPTIONS, "clone", "--no-local", "--no-hardlinks", "--single-branch",
                            "--branch", BRANCH, "--origin", "origin", "--", remote, str(CLONE)],
        cwd=STAGE, timeout=600)
    require(CLONE.resolve(strict=True) == CLONE and not CLONE.is_symlink(), "UNSAFE_OWNED_CLONE")
    assert_checkout(D, repo=CLONE)
    validate_lineage(CLONE)
    require(not git("clone-untracked", "ls-files", "--others", "-z", repo=CLONE), "CLONE_NOT_CLEAN")
    validate_intent(CLONE)
    run_original_adapter(CLONE, "owned-clone")
    assert_checkout(D, repo=CLONE)
    new_outputs = storage_measure([CLONE / name for name in CLEANUP], ROOT)
    RECEIPT["owned_rehearsal"] = {"status": "PASS", "path": str(CLONE), "generated_storage": new_outputs,
                                "source_sha": A, "deployment_sha": D, "release_id": RELEASE_ID}
    save_receipt()
    # Fresh source/foreign/lease checks precede any /app generated backup or FF.
    assert_no_lease(guard)
    assert_checkout(START)
    preserve_foreign(before)
    fetch_expected()
    check_incoming_paths()
    backup_generated_outputs()
    verify_backups_and_originals()
    check_app_capacity(new_outputs, include_source_install=True)
    assert_no_lease(guard)
    assert_checkout(START)
    preserve_foreign(before)
    check_incoming_paths()
    git("fast-forward", "merge", "--ff-only", "--no-edit", D)
    assert_checkout(D)
    validate_intent(ROOT)
    verify_backups_and_originals()
    check_app_capacity(new_outputs, include_source_install=False)
    preserve_foreign(before)
    assert_no_lease(guard)
    run_original_adapter(ROOT, "canonical-app")
    assert_checkout(D)
    preserve_foreign(before)
    fetch_expected()
    assert_checkout(D)
    assert_no_lease(guard)
    RECEIPT.update(status="PASS", lease_created=False, published=False, recalculated=False,
                   automatic_rollback=False, final_capacity={"app": free_space(ROOT), "owned_stage": free_space(STAGE)})
    save_receipt()


def interrupt(signum: int, frame: object) -> None:
    raise Blocked("INTERRUPTED")


def main() -> int:
    global STEP, D, START, EXPECTED_UNTRACKED, ALLOW_RUNTIME_APPEND
    try:
        require(sys.flags.optimize == 0 and "PYTHONOPTIMIZE" not in ENV, "PYTHON_OPTIMIZATION_NOT_ALLOWED")
        require(len(sys.argv) == 2, "REVIEWED_ENVELOPE_REQUIRED")
        envelope_path = Path(sys.argv[1])
        require(envelope_path.is_file() and not envelope_path.is_symlink(), "UNSAFE_ENVELOPE_PATH")
        envelope_bytes = envelope_path.read_bytes()
        require(len(envelope_bytes) <= 16384, "ENVELOPE_TOO_LARGE")
        envelope = json.loads(envelope_bytes)
        require(isinstance(envelope, dict) and set(envelope) == {
            "source_sha", "source_base_sha", "intent_sha", "deployment_sha", "tree", "release_id",
            "initial_head", "expected_untracked_count", "allow_runtime_metadata_append", "live_foreign_scope"}, "ENVELOPE_FIELDS_INVALID")
        require(json.dumps(envelope["live_foreign_scope"], sort_keys=True) == json.dumps(LIVE_SCOPE, sort_keys=True),
                "REVIEWED_LIVE_SCOPE_CHANGED")
        for name in ("source_sha", "source_base_sha", "intent_sha", "deployment_sha", "tree", "initial_head"):
            require(type(envelope[name]) is str and re.fullmatch(r"[0-9a-f]{40}", envelope[name]) is not None,
                    "ENVELOPE_SHA_INVALID")
        require((envelope["source_sha"], envelope["source_base_sha"], envelope["intent_sha"], envelope["tree"], envelope["release_id"])
                == (A, J, B, TREE, RELEASE_ID), "REVIEWED_IDENTITIES_CHANGED")
        require(type(envelope["expected_untracked_count"]) is int and 0 <= envelope["expected_untracked_count"] <= 100000,
                "ENVELOPE_PATH_COUNT_INVALID")
        require(type(envelope["allow_runtime_metadata_append"]) is bool, "ENVELOPE_APPEND_POLICY_INVALID")
        D, START = envelope["deployment_sha"], envelope["initial_head"]
        EXPECTED_UNTRACKED = envelope["expected_untracked_count"]
        ALLOW_RUNTIME_APPEND = envelope["allow_runtime_metadata_append"]
        require(D not in {J, A, START}, "DEPLOYMENT_NOT_NEW")
        RECEIPT.update(initial_head=START, deployment_sha=D, source_sha=A, intent_sha=B, tree=TREE,
                       release_id=RELEASE_ID, envelope_sha256=hashlib.sha256(envelope_bytes).hexdigest(),
                       append_policy_enabled=ALLOW_RUNTIME_APPEND)
        require(Path.cwd() == ROOT and ROOT.resolve() == ROOT, "RUN_FROM_REAL_APP_ROOT")
        for parent in (ROOT / ".git", ROOT / "scripts", ROOT / "frontend", ROOT / "backend"):
            require(parent.is_dir() and not parent.is_symlink() and parent.resolve(strict=True) == parent,
                    "UNSAFE_INITIAL_WORKSPACE_PARENT")
        require(not any(name in ENV for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")), "AMBIENT_GIT_REDIRECTION")
        for name in tuple(ENV):
            if name.startswith("GIT_TRACE") or name in {"GIT_CURL_VERBOSE", "PYTHONHOME", "PYTHONPATH"}:
                ENV.pop(name)
        assert_live_scope()
        require(text_git("git-root", "rev-parse", "--show-toplevel") == str(ROOT), "WRONG_GIT_ROOT")
        require(text_git("git-directory", "rev-parse", "--absolute-git-dir") == str(ROOT / ".git"),
                "UNSUPPORTED_GIT_DIRECTORY")
        assert_checkout(START)
        guard_path = ROOT / "scripts/production_release_guard.py"
        require(guard_path.is_file() and not guard_path.is_symlink(), "UNSAFE_ORIGINAL_GUARD")
        require(guard_path.read_bytes() == git("original-guard-bytes", "show", J + ":scripts/production_release_guard.py"),
                "ORIGINAL_GUARD_CHANGED")
        # The public status command obtains/releases its own lock first. Only
        # after it returns inactive do we acquire that lock for this operation.
        status = json.loads(run("initial-public-status", [sys.executable, str(guard_path), "status"],
                                capture=True, timeout=45))
        require(status.get("active") is False and not status.get("incomplete_lease_directory"), "ACTIVE_OR_INCOMPLETE_RELEASE_LEASE")
        RECEIPT["initial_public_status"] = {"active": False}
        spec = importlib.util.spec_from_file_location("cabinet_archive_original_release_guard", guard_path)
        require(spec is not None and spec.loader is not None, "CANNOT_LOAD_ORIGINAL_GUARD")
        guard = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = guard
        spec.loader.exec_module(guard)
        require(guard.REPO_ROOT == ROOT and guard.GIT_DIR == ROOT / ".git", "GUARD_ROOT_MISMATCH")
        signal.signal(signal.SIGINT, interrupt)
        signal.signal(signal.SIGTERM, interrupt)
        signal.signal(signal.SIGALRM, interrupt)
        STEP = "original-operation-lock"
        signal.alarm(30)
        with guard._release_operation_lock():
            signal.alarm(0)
            protected_rehearsal(guard)
        print("FINAL_STATUS=PASS", flush=True)
        return 0
    except BaseException as exc:
        signal.alarm(0)
        code = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
        RECEIPT.update(status="BLOCKED", failure_step=STEP, failure_code=code, automatic_rollback=False)
        try:
            if STAGE is not None:
                RECEIPT["failure_capacity"] = {"app": free_space(ROOT), "owned_stage": free_space(STAGE)}
            save_receipt()
        except BaseException:
            pass
        print("FINAL_STATUS=BLOCKED:" + code, flush=True)
        return 2



def real_ancestors(path: Path, root: Path) -> None:
    require(root == ROOT or root == STAGE or root == CLONE, "UNEXPECTED_ANCESTOR_ROOT")
    require(root.is_dir() and not root.is_symlink(), "UNSAFE_PARENT_ROOT")
    parent = path.parent
    while parent != root:
        require(root in parent.parents, "PATH_ESCAPES_ROOT")
        require(parent.is_dir() and not parent.is_symlink(), "SYMLINK_OR_MISSING_PARENT")
        parent = parent.parent


def free_space(path: Path) -> dict:
    value = os.statvfs(path)
    return {"bytes": value.f_bavail * value.f_frsize, "inodes": value.f_favail,
            "fragment_bytes": value.f_frsize, "device": path.stat().st_dev}


def storage_measure(roots: list[Path], target: Path) -> dict:
    """Measure without following links; hard-linked foreign extents are not reclaimable.

    Required bytes are normalized to the target allocation unit. Reclaim bytes
    are an estimate for this filesystem, not a promise about OverlayFS/shared
    extents or concurrent writers. Actual adapter success remains the gate.
    """
    block = os.statvfs(target).f_frsize
    device = target.stat().st_dev
    records = {}
    def visit(path):
        if not os.path.lexists(path):
            return
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode),
                "UNSUPPORTED_STORAGE_ENTRY")
        key = (info.st_dev, info.st_ino)
        item = records.setdefault(key, {"seen": 0, "links": info.st_nlink, "device": info.st_dev,
            "allocated": info.st_blocks * 512, "bytes": info.st_size, "directory": stat.S_ISDIR(info.st_mode)})
        item["seen"] += 1
        if stat.S_ISDIR(info.st_mode):
            for child in path.iterdir():
                visit(child)
    for root in roots:
        visit(root)
    # Copying a file may not retain sparsity or hard-link sharing. Charge each
    # destination entry, and never count storage behind a symlink target.
    required = sum(max(block, ((row["bytes"] + block - 1) // block) * block, row["allocated"]) * row["seen"]
                   for row in records.values())
    reclaimable = [row for row in records.values() if row["device"] == device
                   and (row["directory"] or row["seen"] >= row["links"])]
    return {"required_bytes": required, "entries": sum(row["seen"] for row in records.values()),
            "allocated_bytes": sum(row["allocated"] for row in records.values()),
            "reclaimable_bytes": sum(row["allocated"] for row in reclaimable),
            "reclaimable_inodes": len(reclaimable), "target_fragment_bytes": block}


def validate_lineage(repo: Path) -> None:
    require(text_git("deployment-tree", "rev-parse", D + "^{tree}", repo=repo) == TREE, "DEPLOYMENT_TREE_MISMATCH")
    require(text_git("intent-tree", "rev-parse", B + "^{tree}", repo=repo) == TREE, "INTENT_TREE_MISMATCH")
    require(text_git("intent-parents", "show", "-s", "--format=%P", B, repo=repo).split() == [A], "INTENT_PARENT_MISMATCH")
    if D != B:
        require(text_git("deployment-parents", "show", "-s", "--format=%P", D, repo=repo).split() == [J, B],
                "DEPLOYMENT_PARENTS_MISMATCH")
    git("base-ancestry", "merge-base", "--is-ancestor", J, A, repo=repo)
    git("intent-ancestry", "merge-base", "--is-ancestor", B, D, repo=repo)
    git("initial-base-ancestry", "merge-base", "--is-ancestor", START, J, repo=repo)
    require(not git("source-intent-history", "rev-list", "--full-history", J + ".." + A,
                    "--", "release/release-intent-v5.json", repo=repo), "SOURCE_CHANGED_PRIOR_INTENT")
    require(not git("source-intent-diff", "diff", "--name-only", J, A,
                    "--", "release/release-intent-v5.json", repo=repo), "SOURCE_INTENT_BYTES_CHANGED")
    require(git("intent-only-diff", "diff", "--name-only", "-z", A, D, "--", repo=repo)
            == b"release/release-intent-v5.json\0", "NON_INTENT_CHANGE_AFTER_A")
    controls = ("scripts/production_release_guard.py", "scripts/emergent_deployment_adapter.py",
                "scripts/frontend_release_toolchain.py", "scripts/verify_frontend_build.py",
                "frontend/scripts/build-entry.cjs", "frontend/scripts/build-release-frontend.cjs")
    require(not git("unchanged-release-controls", "diff", "--name-only", "-z", J, D, "--", *controls, repo=repo),
            "ORIGINAL_RELEASE_CONTROLS_CHANGED")
    require(not git("tracked-generated-targets", "ls-tree", "-r", "--name-only", "-z", D, "--", *CLEANUP, repo=repo),
            "GENERATED_TARGET_TRACKED")


def check_incoming_paths() -> None:
    existing = {os.fsdecode(value) for value in git("current-tracked-paths", "ls-files", "-z").split(b"\0") if value}
    changed = [os.fsdecode(value) for value in git("incoming-paths", "diff", "--name-only", "--no-renames", "-z", START, D).split(b"\0") if value]
    for relative in changed:
        require(relative and not relative.startswith("/") and ".." not in Path(relative).parts, "UNSAFE_INCOMING_PATH")
        target = ROOT / relative
        assert_live_write_disjoint(target)
        if relative not in existing:
            require(not os.path.lexists(target), "INCOMING_TRACKED_PATH_OVERLAPS_EXISTING_FILE")
        parent = target.parent
        while parent != ROOT:
            if os.path.lexists(parent):
                require(parent.is_dir() and not parent.is_symlink(), "INCOMING_PATH_UNSAFE_PARENT")
            parent = parent.parent


def verify_backups_and_originals() -> None:
    for entry in RECEIPT["backups"]:
        source = ROOT / entry["source"]
        if not entry["present"]:
            require(not os.path.lexists(source), "ORIGINALLY_ABSENT_GENERATED_OUTPUT_APPEARED")
            continue
        real_ancestors(source, ROOT)
        reject_generated_mounts(source)
        expected = json.loads((STAGE / (entry["source"].replace("/", "--") + "-manifest.json")).read_text())
        require(generated_manifest(source) == expected == generated_manifest(Path(entry["destination"])),
                "BACKUP_OR_GENERATED_OUTPUT_CHANGED")


def check_app_capacity(new_outputs: dict, *, include_source_install: bool) -> None:
    free = free_space(ROOT)
    old = storage_measure([ROOT / name for name in CLEANUP], ROOT)
    source_bytes = source_entries = 0
    if include_source_install:
        changed = [os.fsdecode(value) for value in git("source-install-paths", "diff", "--name-only", "--no-renames", "-z", START, D).split(b"\0") if value]
        for relative in changed:
            source = CLONE / relative
            if os.path.lexists(source):
                info = source.lstat()
                require(stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode), "UNEXPECTED_SOURCE_INSTALL_TYPE")
                source_bytes += max(free["fragment_bytes"], info.st_blocks * 512,
                                    ((info.st_size + free["fragment_bytes"] - 1) // free["fragment_bytes"]) * free["fragment_bytes"])
                source_entries += 1
        require(free["bytes"] >= source_bytes and free["inodes"] >= source_entries,
                "APP_CAPACITY_INSUFFICIENT_FOR_FAST_FORWARD")
    required = new_outputs["required_bytes"] + source_bytes
    available = free["bytes"] + old["reclaimable_bytes"]
    row = {"phase": "before-ff" if include_source_install else "before-app-adapter",
           "fresh_free": free, "current_generated": old, "clone_measured_generated": new_outputs,
           "source_install_upper_bound_bytes": source_bytes, "required_bytes": required,
           "available_after_adapter_cleanup_estimate_bytes": available,
           "calculation": "fresh free + measured reclaimable inherited outputs versus measured clone output allocation + changed-source bound",
           "limitations": "No assumed old capacity; shared/OverlayFS extents and concurrent activity can affect actual free space; adapter must still pass."}
    RECEIPT.setdefault("capacity_checks", []).append(row)
    save_receipt()
    require(available >= required and free["inodes"] + old["reclaimable_inodes"] >= new_outputs["entries"] + source_entries,
            f"APP_REBUILD_CAPACITY_INSUFFICIENT_REQUIRED_{required}_AVAILABLE_{available}")


def build_environment(repo: Path) -> dict:
    environment = ENV.copy()
    for name in tuple(environment):
        if name.startswith("GITHUB_") or name in {"MEZAN_RELEASE_BOOTSTRAP_INTENT", "PYTHONHOME", "PYTHONPATH"}:
            environment.pop(name)
    environment.update(REACT_APP_BACKEND_URL="https://mezansalla.com", CI="false",
                       XDG_CACHE_HOME=str(STAGE / "cache"), TMPDIR=str(STAGE / "tmp"),
                       PYTHONPATH=str(repo / "backend") + ":" + str(repo))
    return environment


def run_original_adapter(repo: Path, label: str) -> None:
    assert_live_scope()
    assert_live_write_disjoint(*(repo / name for name in CLEANUP))
    environment = build_environment(repo)
    RECEIPT.setdefault("adapter_runs", []).append({"label": label, "repo": str(repo),
        "started_capacity": {"repo": free_space(repo), "owned_stage": free_space(STAGE)}})
    save_receipt()
    run(label + "-adapter", ["yarn", "build"], cwd=repo / "frontend", environment=environment, timeout=2400)
    run(label + "-verify-frontend", [sys.executable, "scripts/verify_frontend_build.py",
        "--expected-git-sha", A, "--reviewed-intent-v5"], cwd=repo, environment=environment, timeout=180)
    run(label + "-verify-packages", [sys.executable, "scripts/emergent_deployment_adapter.py", "verify-packages"],
        cwd=repo, environment=environment, timeout=240)
    intent = json.loads((repo / "release/release-intent-v5.json").read_text())
    identity = json.loads((repo / "backend/release_identity.json").read_text())
    require(identity == intent["runtime_identity"] and identity["release_id"] == RELEASE_ID
            and identity["source_git_sha"] == A, "RUNTIME_IDENTITY_MISMATCH")
    run(label + "-full-proof", [sys.executable, "-c",
        "from frontend_build_identity import read_frontend_build_identity, read_frontend_reproducibility_proof; "
        "from scripts.emergent_deployment_adapter import load_release_intent; "
        "i=load_release_intent(); f=read_frontend_build_identity(expected_git_sha=i['source_git_sha']); "
        "assert f==i['frontend_build']; "
        "assert read_frontend_reproducibility_proof(frontend_build=f)==i['frontend_reproducibility']; "
        "print('CANONICAL_PROOF_PASS')"], cwd=repo, environment=environment, timeout=180)
    assert_live_scope()
    RECEIPT["adapter_runs"][-1].update(status="PASS", finished_capacity={"repo": free_space(repo), "owned_stage": free_space(STAGE)})
    save_receipt()

if __name__ == "__main__":
    raise SystemExit(main())
