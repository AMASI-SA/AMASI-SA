#!/usr/bin/env python3
"""PR2129 fresh current-host rehearsal at exact D; old recovery remains UNKNOWN.

New reviewed root/process pins, foreign baseline and current-output backups.
Receipts, baseline and backups persist under a new private /app/.git stage;
only toolchain/cache/temporary package work uses a separate owned /tmp stage.
One unchanged canonical adapter run, no clone build, FF, quarantine move,
lease creation, publication, service change, automatic retry or rollback.
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
J = "30c970807c05e291e2f48b35c84155b271fb5f09"
A = "0b39c456ea17d6e80f824f44b57563764441216c"
B = "ebaae98290f25db5580d888601d236431961d104"
TREE = "d50681f64d3387eb4b6f2137658be9cb11c62bd8"
RELEASE_ID = "rg5-37ff4cc0a52caf321810a8dce1cb6c8f1c27dd8e2f9e5f0a57944ffc90efaf5a"
D = "b651eacf5ded173c4f3224d195ef874b8bcea312"
# Historical source baseline for the unchanged lineage check. Current HEAD is D.
START = J
EXPECTED_UNTRACKED = 432
EXPECTED_IGNORED = 47
CONTROL = Path("/app/.git/mezan-pr2129-host2-control-20261125")
WORK_STAGE = None
ALLOW_RUNTIME_APPEND = True
CLONE = None
GIT_OPTIONS = ["-c", "gc.auto=0", "-c", "maintenance.auto=false", "-c", "core.hooksPath=/dev/null"]
RUNTIME_PATH = ".emergent/stripped_env_names"
RUNTIME_IDENTITY = (66345, 169292, 420)
LIVE_DB = ".worktrees/p01-acceptance-state-20260919/mongo"
LIVE_LOG = ".worktrees/p01-acceptance-state-20260919/mongo.log"
LIVE_SCOPE = {'db_descendants_policy': 'live_writer_managed_no_operator_access',
 'dbpath': '.worktrees/p01-acceptance-state-20260919/mongo',
 'directory_identities': {'.worktrees': [66345, 131957, 493, 0, 0],
                          '.worktrees/p01-acceptance-state-20260919': [66345, 133807, 448, 0, 0],
                          '.worktrees/p01-acceptance-state-20260919/mongo': [66345, 133808, 493, 0, 0]},
 'log_identity': [66345, 400929, 420, 0, 0],
 'log_policy': 'independent_same_inode_append_only',
 'logpath': '.worktrees/p01-acceptance-state-20260919/mongo.log',
 'process': {'cmdline_sha256': '3afd2534eb5355d9b66e77ed088d8d3db819115b34d0e8d2901e052468cd2832',
             'exe': '/usr/bin/mongod',
             'exe_identity': [1048696, 85851, 493, 0, 0],
             'pid': 313,
             'start_ticks': 115105568}}
CLEANUP = (
    "frontend/build", "frontend/.release", "backend/release_identity.json",
    "frontend/node_modules",
)
STAGE: Path | None = None
STEP = "startup"
RECEIPT: dict = {
    "task": "pr2129_fresh_current_host_rehearsal", "status": "RUNNING",
    "initial_head": D, "historical_source_base": J, "source_sha": A, "intent_sha": B,
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


CURRENT_ENVELOPE = {'allow_runtime_metadata_append': True,
 'deployment_sha': 'b651eacf5ded173c4f3224d195ef874b8bcea312',
 'expected_untracked_count': 432,
 'initial_head': 'b651eacf5ded173c4f3224d195ef874b8bcea312',
 'intent_sha': 'ebaae98290f25db5580d888601d236431961d104',
 'live_foreign_scope': {'db_descendants_policy': 'live_writer_managed_no_operator_access',
                        'dbpath': '.worktrees/p01-acceptance-state-20260919/mongo',
                        'directory_identities': {'.worktrees': [66345, 131957, 493, 0, 0],
                                                 '.worktrees/p01-acceptance-state-20260919': [66345,
                                                                                              133807,
                                                                                              448,
                                                                                              0,
                                                                                              0],
                                                 '.worktrees/p01-acceptance-state-20260919/mongo': [66345,
                                                                                                    133808,
                                                                                                    493,
                                                                                                    0,
                                                                                                    0]},
                        'log_identity': [66345, 400929, 420, 0, 0],
                        'log_policy': 'independent_same_inode_append_only',
                        'logpath': '.worktrees/p01-acceptance-state-20260919/mongo.log',
                        'process': {'cmdline_sha256': '3afd2534eb5355d9b66e77ed088d8d3db819115b34d0e8d2901e052468cd2832',
                                    'exe': '/usr/bin/mongod',
                                    'exe_identity': [1048696, 85851, 493, 0, 0],
                                    'pid': 313,
                                    'start_ticks': 115105568}},
 'release_id': 'rg5-37ff4cc0a52caf321810a8dce1cb6c8f1c27dd8e2f9e5f0a57944ffc90efaf5a',
 'source_base_sha': '30c970807c05e291e2f48b35c84155b271fb5f09',
 'source_sha': '0b39c456ea17d6e80f824f44b57563764441216c',
 'tree': 'd50681f64d3387eb4b6f2137658be9cb11c62bd8'}
CURRENT_ENVELOPE_SHA = 'a5bc2caf6faea02550877efa0c73320d1ee83366241a44fb399d4d222819f5a7'
CURRENT_OBSERVATION_SHA = 'c379d598791e5de54edea2baa3ee83b085fbd31f2b39756dc206ba677c594801'
CURRENT_ROOT_PINS = {'.git': [66345, 131139, 493, 0, 0],
 '/app': [66345, 131073, 493, 0, 0],
 'backend': [66345, 138008, 493, 0, 0]}
RUNTIME_FULL_IDENTITY = [66345, 169292, 420, 0, 0]
NODE_MODULES_PIN = {'allocated_bytes': 17649664,
 'entries': 4298,
 'identity': [66345, 262145, 493, 0, 0],
 'measurement_at': '2026-11-25T18:58:44.592696+00:00',
 'reclaimable_bytes': 17649664,
 'reclaimable_inodes': 4298,
 'required_copy_bytes': 17649664,
 'types': {'directory': 4298}}
QUARANTINE_PIN = {'current_bytes_match_prior_records': True,
 'device_changed_from': 66329,
 'device_changed_to': 66345,
 'directory_identity': [66345, 138446, 493, 0, 0],
 'files': [{'bytes': 39622,
            'identity': [66345, 138447, 420, 0, 0],
            'mtime_ns': 1795618956497225697,
            'name': 'frontend_build_identity.cpython-311.pyc',
            'sha256': '489ba93d9df88a1e4f9a7e703f5ab838f93cf60e5f6f60fe10b7eee45361b67b'},
           {'bytes': 80941,
            'identity': [66345, 138448, 420, 0, 0],
            'mtime_ns': 1795618956509225697,
            'name': 'release_protocol_v5.cpython-311.pyc',
            'sha256': 'c1c09df55f54c1692c24af99f4001907d6717ff06f72b95c21ec58209962143d'}],
 'historical_full_foreign_continuity_established': False,
 'manifest_after': {'bytes': 1898,
                    'sha256': 'c4a03f0b655da8ae5b7c6416b9db389fca35efd711350adb45161dec848beeac'},
 'manifest_before': {'bytes': 1985,
                     'sha256': '6407586437fbb6bcbe03bff7d409d58daeebf215405787c71dd4c17be5eba911'},
 'old_historical_manifest_pins_retained': True,
 'parent_identity': [66345, 138639, 448, 0, 0],
 'path': '/app/.git/mezan-pr2129-bytecode-quarantine-nth6u7ce',
 'source_backend_cache_absent': True}
HISTORICAL_CLONE_OUTPUTS = {'allocated_bytes': 656945152,
 'entries': 77033,
 'reclaimable_bytes': 0,
 'reclaimable_inodes': 0,
 'required_bytes': 658653184,
 'target_fragment_bytes': 4096}
HISTORICAL_CLONE_RECEIPT_SHA = '1a134232998dc0edb7cf91e23a179d87bcebcd80330f61bf31e16cc4adaea854'
HISTORICAL_EVIDENCE_COMMIT = '0de0352250ae05a5375da56c1e498d18a6a32aa7'

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


def interrupt(signum: int, frame: object) -> None:
    raise Blocked("INTERRUPTED")


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
                       XDG_CACHE_HOME=str(WORK_STAGE / "cache"), TMPDIR=str(WORK_STAGE / "tmp"),
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


def assert_current_roots() -> None:
    for name, expected in CURRENT_ROOT_PINS.items():
        path = ROOT if name == "/app" else ROOT / name
        info = path.lstat()
        require(stat.S_ISDIR(info.st_mode) and path.resolve(strict=True) == path
                and live_identity(info) == expected, "CURRENT_HOST_ROOT_CHANGED")
    info = (ROOT / RUNTIME_PATH).lstat()
    require(stat.S_ISREG(info.st_mode) and live_identity(info) == RUNTIME_FULL_IDENTITY,
            "CURRENT_RUNTIME_METADATA_IDENTITY_CHANGED")


def verify_existing_quarantine() -> None:
    parent = Path(QUARANTINE_PIN["path"])
    cache = parent / "backend-__pycache__"
    real_ancestors(cache, ROOT)
    require(parent.resolve(strict=True) == parent and stat.S_ISDIR(parent.lstat().st_mode)
            and live_identity(parent.lstat()) == QUARANTINE_PIN["parent_identity"], "EXISTING_QUARANTINE_PARENT_CHANGED")
    require(cache.resolve(strict=True) == cache and stat.S_ISDIR(cache.lstat().st_mode)
            and live_identity(cache.lstat()) == QUARANTINE_PIN["directory_identity"], "EXISTING_QUARANTINE_ROOT_CHANGED")
    require(not os.path.lexists(ROOT / "backend/__pycache__"), "BACKEND_CACHE_REAPPEARED")
    require(sorted(path.name for path in parent.iterdir())
            == ["backend-__pycache__", "manifest-after.json", "manifest-before.json"], "EXISTING_QUARANTINE_CONTAINER_CHANGED")
    require(sorted(path.name for path in cache.iterdir()) == sorted(row["name"] for row in QUARANTINE_PIN["files"]),
            "EXISTING_QUARANTINE_CHILD_SET_CHANGED")
    for row in QUARANTINE_PIN["files"]:
        path = cache / row["name"]
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and live_identity(info) == row["identity"]
                and info.st_mtime_ns == row["mtime_ns"], "EXISTING_QUARANTINE_FILE_IDENTITY_CHANGED")
        require(file_fingerprint(path) == {"type": "file", "mode": row["identity"][2],
                "bytes": row["bytes"], "sha256": row["sha256"]}, "EXISTING_QUARANTINE_BYTES_CHANGED")
        after = path.lstat()
        require(live_identity(after) == row["identity"] and after.st_mtime_ns == row["mtime_ns"],
                "EXISTING_QUARANTINE_FILE_CHANGED_DURING_READ")
    for name, field in (("manifest-before.json", "manifest_before"), ("manifest-after.json", "manifest_after")):
        pin = QUARANTINE_PIN[field]
        require(file_fingerprint(parent / name) == {"type": "file", "mode": 0o600,
                "bytes": pin["bytes"], "sha256": pin["sha256"]}, "HISTORICAL_QUARANTINE_MANIFEST_CHANGED")
    require(live_identity(parent.lstat()) == QUARANTINE_PIN["parent_identity"]
            and live_identity(cache.lstat()) == QUARANTINE_PIN["directory_identity"], "EXISTING_QUARANTINE_IDENTITY_CHANGED")


def inspect_current_generated() -> dict:
    for relative in CLEANUP[:3]:
        require(not os.path.lexists(ROOT / relative), "EXPECTED_ABSENT_OUTPUT_APPEARED")
    root = ROOT / "frontend/node_modules"
    real_ancestors(root, ROOT)
    info = root.lstat()
    require(stat.S_ISDIR(info.st_mode) and live_identity(info) == NODE_MODULES_PIN["identity"],
            "CURRENT_DEPENDENCY_ROOT_CHANGED")
    reject_generated_mounts(root)
    records = generated_manifest(root)
    require(len(records) == NODE_MODULES_PIN["entries"]
            and all(row["type"] == "directory" for row in records.values()), "CURRENT_DEPENDENCY_CONTENT_CHANGED")
    storage = storage_measure([root], ROOT)
    require(storage["entries"] == NODE_MODULES_PIN["entries"]
            and storage["required_bytes"] == NODE_MODULES_PIN["required_copy_bytes"]
            and storage["allocated_bytes"] == NODE_MODULES_PIN["allocated_bytes"]
            and storage["reclaimable_bytes"] == NODE_MODULES_PIN["reclaimable_bytes"]
            and storage["reclaimable_inodes"] == NODE_MODULES_PIN["reclaimable_inodes"],
            "CURRENT_DEPENDENCY_ALLOCATION_CHANGED")
    return storage


def verify_backup_copies() -> None:
    require([row["source"] for row in RECEIPT["backups"]] == list(CLEANUP), "CURRENT_BACKUP_SCOPE_CHANGED")
    for row in RECEIPT["backups"]:
        if not row["present"]:
            continue
        expected_path = STAGE / "backups" / row["source"].replace("/", "--")
        require(row["state"] == "COPY_VERIFIED" and row["destination"] == str(expected_path), "CURRENT_BACKUP_BINDING_CHANGED")
        real_ancestors(expected_path, STAGE)
        reject_generated_mounts(expected_path)
        manifest = json.loads((STAGE / (row["source"].replace("/", "--") + "-manifest.json")).read_bytes())
        require(generated_manifest(expected_path) == manifest, "CURRENT_BACKUP_COPY_CHANGED")
    RECEIPT["final_current_backups_verified"] = True
    save_receipt()


def check_historical_output_capacity() -> None:
    require(free_space(ROOT)["fragment_bytes"] == HISTORICAL_CLONE_OUTPUTS["target_fragment_bytes"],
            "CAPACITY_REFERENCE_BLOCK_SIZE_CHANGED")
    check_app_capacity(HISTORICAL_CLONE_OUTPUTS, include_source_install=False)
    RECEIPT["capacity_checks"][-1]["footprint_reference"] = {
        "kind": "historical_exact_release_owned_clone_measurement",
        "receipt_sha256": HISTORICAL_CLONE_RECEIPT_SHA,
        "evidence_commit": HISTORICAL_EVIDENCE_COMMIT,
        "fresh_clone_measured_in_this_operation": False,
        "scope": "Expected completed generated-output footprint, not a guarantee of peak temporary allocation; actual adapter must pass.",
    }
    save_receipt()


def protected_current_rehearsal(guard: object) -> None:
    global STAGE, WORK_STAGE
    assert_no_lease(guard)
    assert_checkout(D)
    assert_current_roots()
    assert_live_scope()
    verify_existing_quarantine()
    require(not os.path.lexists(CONTROL / "host2-attempt-claim.json"), "CURRENT_ATTEMPT_ALREADY_CLAIMED")
    STAGE = Path(tempfile.mkdtemp(prefix="mezan-pr2129-host2-stage-", dir=ROOT / ".git"))
    STAGE.chmod(0o700)
    WORK_STAGE = Path(tempfile.mkdtemp(prefix="mezan-pr2129-host2-work-", dir="/tmp"))
    WORK_STAGE.chmod(0o700)
    require(STAGE.resolve(strict=True) == STAGE and WORK_STAGE.resolve(strict=True) == WORK_STAGE,
            "UNSAFE_CURRENT_STAGE")
    for name in ("cache", "tmp"):
        (WORK_STAGE / name).mkdir(mode=0o700)
    helper_bytes = Path(__file__).read_bytes()
    RECEIPT.update(stage=str(STAGE), work_stage=str(WORK_STAGE), helper_sha256=hashlib.sha256(helper_bytes).hexdigest(),
        live_foreign_scope=LIVE_SCOPE, observation_sha256=CURRENT_OBSERVATION_SHA,
        historical_recovery_status="UNKNOWN", historical_final_foreign_preservation_proved=False,
        previous_backups_available=False, current_stage_storage="persistent_app_git",
        preservation_claim="New current-host baseline only. Prior final recovery outcome and historical foreign continuity remain unknown. Existing quarantine is preserved separately; live DB descendants are not accessed.",
        retained_clean_clone_evidence={"commit": HISTORICAL_EVIDENCE_COMMIT, "receipt_sha256": HISTORICAL_CLONE_RECEIPT_SHA,
            "source_sha": A, "deployment_sha": D, "release_id": RELEASE_ID, "build_repeated": False})
    for path, value in ((STAGE / "operator-helper.py", helper_bytes),
                        (CONTROL / "host2-attempt-claim.json", json.dumps({"state": "CLAIMED", "pid": os.getpid(),
                            "stage": str(STAGE), "helper_sha256": RECEIPT["helper_sha256"], "automatic_retry": False},
                            sort_keys=True).encode() + b"\n")):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(path.parent)
    fsync_directory(STAGE.parent)
    fsync_directory(WORK_STAGE)
    fsync_directory(WORK_STAGE.parent)
    save_receipt()
    print("STAGE_PATH=" + str(STAGE), flush=True)
    before = untracked_snapshot()
    require(len(before["git_entries"]) == EXPECTED_UNTRACKED and len(before["ignored_entries"]) == EXPECTED_IGNORED,
            "CURRENT_FOREIGN_PATH_COUNT_CHANGED")
    private_json(STAGE / "foreign-before.json", before)
    RECEIPT["current_foreign_baseline"] = {"sha256": hashlib.sha256((STAGE / "foreign-before.json").read_bytes()).hexdigest(),
        "records": len(before["records"]), "nonignored": len(before["git_entries"]), "ignored": len(before["ignored_entries"]),
        "historical_equality_claimed": False}
    RECEIPT["initial_capacity"] = {"app": free_space(ROOT), "persistent_stage": free_space(STAGE),
                                  "temporary_work": free_space(WORK_STAGE)}
    save_receipt()
    fetch_expected()
    validate_lineage(ROOT)
    validate_intent(ROOT)
    RECEIPT["current_generated_measurement"] = inspect_current_generated()
    save_receipt()
    backup_generated_outputs()
    require([row["present"] for row in RECEIPT["backups"]] == [False, False, False, True], "CURRENT_BACKUP_PRESENCE_CHANGED")
    verify_backups_and_originals()
    check_historical_output_capacity()
    preserve_foreign(before)
    verify_existing_quarantine()
    assert_current_roots()
    assert_live_scope()
    assert_no_lease(guard)
    assert_checkout(D)
    fetch_expected()
    verify_backups_and_originals()
    check_historical_output_capacity()
    run_original_adapter(ROOT, "canonical-app")
    assert_checkout(D)
    preserve_foreign(before)
    verify_existing_quarantine()
    verify_backup_copies()
    fetch_expected()
    validate_lineage(ROOT)
    assert_checkout(D)
    assert_no_lease(guard)
    assert_current_roots()
    assert_live_scope()
    RECEIPT.update(status="PASS", lease_created=False, published=False, recalculated=False,
        automatic_rollback=False, fast_forward_performed=False, quarantine_moved=False,
        old_helper_rerun=False, final_capacity={"app": free_space(ROOT), "persistent_stage": free_space(STAGE),
                                             "temporary_work": free_space(WORK_STAGE)})
    save_receipt()


def main() -> int:
    global STEP
    try:
        require(sys.flags.optimize == 0 and "PYTHONOPTIMIZE" not in ENV, "PYTHON_OPTIMIZATION_NOT_ALLOWED")
        require(len(sys.argv) == 2 and Path(sys.argv[1]) == CONTROL / "execution-envelope.json", "EXACT_CURRENT_ENVELOPE_REQUIRED")
        require(Path.cwd() == ROOT and ROOT.resolve() == ROOT, "RUN_FROM_REAL_APP_ROOT")
        for parent in (ROOT, ROOT / ".git", ROOT / "scripts", ROOT / "frontend", ROOT / "backend", CONTROL, Path("/tmp")):
            require(parent.is_dir() and not parent.is_symlink() and parent.resolve(strict=True) == parent, "UNSAFE_INITIAL_WORKSPACE_PARENT")
        require(stat.S_IMODE(CONTROL.stat().st_mode) == 0o700, "UNSAFE_CONTROL_MODE")
        envelope_path = Path(sys.argv[1])
        require(stat.S_ISREG(envelope_path.lstat().st_mode) and not envelope_path.is_symlink()
                and stat.S_IMODE(envelope_path.stat().st_mode) == 0o600, "UNSAFE_CURRENT_ENVELOPE")
        envelope_bytes = envelope_path.read_bytes()
        require(hashlib.sha256(envelope_bytes).hexdigest() == CURRENT_ENVELOPE_SHA, "CURRENT_ENVELOPE_CHANGED")
        envelope = json.loads(envelope_bytes)
        require(envelope == CURRENT_ENVELOPE, "CURRENT_ENVELOPE_FIELDS_CHANGED")
        RECEIPT["envelope_sha256"] = CURRENT_ENVELOPE_SHA
        require(not any(name in ENV for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
                    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES")), "AMBIENT_GIT_REDIRECTION")
        for name in tuple(ENV):
            if name.startswith("GIT_TRACE") or name in {"GIT_CURL_VERBOSE", "PYTHONHOME", "PYTHONPATH"}:
                ENV.pop(name)
        assert_current_roots()
        assert_live_scope()
        verify_existing_quarantine()
        require(text_git("git-root", "rev-parse", "--show-toplevel") == str(ROOT), "WRONG_GIT_ROOT")
        require(text_git("git-directory", "rev-parse", "--absolute-git-dir") == str(ROOT / ".git"), "UNSUPPORTED_GIT_DIRECTORY")
        assert_checkout(D)
        guard_path = ROOT / "scripts/production_release_guard.py"
        require(guard_path.is_file() and not guard_path.is_symlink(), "UNSAFE_ORIGINAL_GUARD")
        require(guard_path.read_bytes() == git("original-guard-bytes", "show", J + ":scripts/production_release_guard.py"), "ORIGINAL_GUARD_CHANGED")
        status = json.loads(run("initial-public-status", [sys.executable, str(guard_path), "status"], capture=True, timeout=45))
        require(status.get("active") is False and not status.get("incomplete_lease_directory"), "ACTIVE_OR_INCOMPLETE_RELEASE_LEASE")
        RECEIPT["initial_public_status"] = {"active": False}
        spec = importlib.util.spec_from_file_location("pr2129_host2_original_release_guard", guard_path)
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
            protected_current_rehearsal(guard)
        print("FINAL_STATUS=PASS", flush=True)
        return 0
    except BaseException as exc:
        signal.alarm(0)
        code = str(exc) if isinstance(exc, Blocked) else type(exc).__name__
        RECEIPT.update(status="BLOCKED", failure_step=STEP, failure_code=code, automatic_rollback=False,
                       historical_recovery_status="UNKNOWN", historical_final_foreign_preservation_proved=False)
        try:
            if STAGE is not None:
                RECEIPT["failure_capacity"] = {"app": free_space(ROOT), "persistent_stage": free_space(STAGE)}
            save_receipt()
        except BaseException:
            pass
        print("FINAL_STATUS=BLOCKED:" + code, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
