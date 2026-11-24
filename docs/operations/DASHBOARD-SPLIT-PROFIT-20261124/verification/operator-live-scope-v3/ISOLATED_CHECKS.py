#!/usr/bin/env python3
"""Bounded fixture checks only: no main, Git, adapter, network or actual /app access."""
from contextlib import ExitStack, contextmanager
from pathlib import Path
import ast
import copy
import hashlib
import json
import os
import stat
import sys
import tempfile
import types
from unittest.mock import patch

EXPECTED = "d7832f19f5475912f3ebd0ba14c4fd2cd9b61572426f67190719c507d655a343"
assert sys.flags.optimize == 0 and "PYTHONOPTIMIZE" not in os.environ
assert len(sys.argv) == 2
helper = Path(sys.argv[1]).resolve()
payload = helper.read_bytes()
assert hashlib.sha256(payload).hexdigest() == EXPECTED
ast.parse(payload)
operator = types.ModuleType("isolated_pr2122_live_scope")
operator.__file__ = str(helper)
exec(compile(payload, str(helper), "exec"), operator.__dict__)
original_scope = copy.deepcopy(operator.LIVE_SCOPE)
results = []


def forbidden(*args, **kwargs):
    raise AssertionError("Unexpected main, protected rehearsal, subprocess or adapter execution")


operator.main = forbidden
operator.protected_rehearsal = forbidden
operator.run_original_adapter = forbidden
operator.run = forbidden


def blocked(code, function):
    try:
        function()
    except operator.Blocked as exc:
        assert str(exc) == code, (str(exc), code)
    else:
        raise AssertionError("Expected block: " + code)


@contextmanager
def fixture():
    with tempfile.TemporaryDirectory(prefix="pr2122-live-scope-check-") as temp:
        base = Path(temp)
        operator.ROOT = base / "app-fixture"
        operator.STAGE = base / "owned-stage"
        operator.CLONE = operator.STAGE / "rehearsal"
        operator.ROOT.mkdir()
        operator.STAGE.mkdir(mode=0o700)
        operator.RECEIPT = {"steps": [], "backups": []}
        operator.ALLOW_RUNTIME_APPEND = False
        operator.LIVE_SCOPE = copy.deepcopy(original_scope)
        db = operator.ROOT / operator.LIVE_DB
        db.mkdir(parents=True)
        (db / "diagnostic.data").mkdir()
        (db / "diagnostic.data" / "fixture-only").write_bytes(b"must never be read by operator")
        log = operator.ROOT / operator.LIVE_LOG
        log.write_bytes(b"original independent log prefix\n")
        (log.parent / "mongo.log.1").write_bytes(b"exact sibling log")
        sibling = log.parent / "mongo-copy"
        sibling.mkdir()
        (sibling / "owner-note").write_bytes(b"exact sibling directory bytes")
        (operator.ROOT / "owner-note").write_bytes(b"exact foreign bytes")
        for relative in operator.LIVE_SCOPE["directory_identities"]:
            operator.LIVE_SCOPE["directory_identities"][relative] = operator.live_identity(
                (operator.ROOT / relative).lstat()
            )
        operator.LIVE_SCOPE["log_identity"] = operator.live_identity(log.lstat())

        def fake_git(name, *args, **kwargs):
            assert name == "foreign-paths" and args[:2] == ("ls-files", "--others")
            return b".worktrees/\0" if "--ignored" in args else b"owner-note\0"

        operator.git = fake_git
        yield base, db, log


@contextmanager
def forbid_db_access(db):
    native_iterdir, native_lstat, native_open = Path.iterdir, Path.lstat, os.open

    def iterdir(path):
        assert path != db and db not in path.parents, "Database traversal attempted"
        return native_iterdir(path)

    def lstat(path, *args, **kwargs):
        assert db not in path.parents, "Database descendant metadata read attempted"
        return native_lstat(path, *args, **kwargs)

    def open_path(path, *args, **kwargs):
        candidate = Path(os.fsdecode(path))
        assert candidate != db and db not in candidate.parents, "Database open attempted"
        return native_open(path, *args, **kwargs)

    with patch.object(Path, "iterdir", iterdir), patch.object(Path, "lstat", lstat), patch.object(os, "open", open_path):
        yield


with fixture() as (base, db, log), patch.object(operator, "assert_live_scope", lambda: None):
    with forbid_db_access(db):
        before = operator.untracked_snapshot()
    assert before["records"][operator.LIVE_DB]["type"] == "live_directory"
    assert not any(name.startswith(operator.LIVE_DB + "/") for name in before["records"])
    assert operator.LIVE_LOG + ".1" in before["records"]
    assert operator.LIVE_DB + "-copy/owner-note" in before["records"]
    (db / "diagnostic.data" / "fixture-only").write_bytes(b"new live fixture bytes")
    (db / "new-fixture-file").write_bytes(b"new live fixture file")
    with log.open("ab") as stream:
        stream.write(b"appended log bytes\n")
    with forbid_db_access(db):
        operator.preserve_foreign(before)
    assert operator.RECEIPT["foreign_preservation"]["live_db_descendant_bytes_or_paths_compared"] is False
    sibling = log.parent / "mongo.log.1"
    sibling.write_bytes(b"changed sibling")
    blocked("FOREIGN_ENTRY_CHANGED", lambda: operator.preserve_foreign(before))
    sibling.write_bytes(b"exact sibling log")
    new_sibling = log.parent / "mongo-unreviewed"
    new_sibling.write_bytes(b"unreviewed sibling")
    blocked("FOREIGN_RECORD_SET_CHANGED", lambda: operator.preserve_foreign(before))
    results.append("Only the exact database descendants are unaccessed; live changes and log append pass, sibling changes and additions fail")

with fixture() as (base, db, log), patch.object(operator, "assert_live_scope", lambda: None):
    before = operator.untracked_snapshot()
    prefix = log.read_bytes()
    log.write_bytes(b"X" + prefix[1:])
    blocked("LIVE_LOG_EXISTING_PREFIX_CHANGED", lambda: operator.preserve_foreign(before))
    log.write_bytes(prefix[:-1])
    blocked("LIVE_LOG_TRUNCATED", lambda: operator.live_log_fingerprint(len(prefix)))
    log.write_bytes(prefix)
    replacement = log.with_name("replacement-fixture")
    replacement.write_bytes(prefix)
    replacement.chmod(stat.S_IMODE(log.lstat().st_mode))
    replacement.replace(log)
    blocked("LIVE_LOG_IDENTITY_CHANGED", lambda: operator.live_log_fingerprint())
    results.append("Log prefix corruption, truncation and byte-identical inode replacement fail")

with fixture() as (base, db, log):
    for target in (db, db / "future-file", db.parent, log, operator.ROOT):
        blocked("WRITE_OVERLAPS_LIVE_FOREIGN_SCOPE", lambda target=target: operator.assert_live_write_disjoint(target))
    redirect = operator.STAGE / "redirect"
    redirect.symlink_to(db, target_is_directory=True)
    blocked("WRITE_OVERLAPS_LIVE_FOREIGN_SCOPE", lambda: operator.assert_live_write_disjoint(redirect / "future-file"))
    operator.assert_live_write_disjoint(operator.STAGE / "cache", db.with_name("mongo-new"), log.with_name("mongo.log.1"))
    results.append("Lexical ancestors/descendants and resolved symlink collisions fail; separate sibling paths remain outside scope")

with fixture() as (base, db, log):
    fake_proc_root = base / "fake-proc"
    process = operator.LIVE_SCOPE["process"]
    proc = fake_proc_root / str(process["pid"])
    proc.mkdir(parents=True)
    executable = base / "fixture-mongod"
    executable.write_bytes(b"fixture executable identity only")
    executable.chmod(0o755)
    process["exe"] = str(executable)
    process["exe_identity"] = operator.live_identity(executable.lstat())
    (proc / "exe").symlink_to(executable)
    command = b"mongod\0--dbpath\0" + os.fsencode(db) + b"\0"
    (proc / "cmdline").write_bytes(command)
    process["cmdline_sha256"] = hashlib.sha256(command).hexdigest()

    def proc_stat(start):
        return str(process["pid"]) + " (fixture mongod) " + " ".join(["S"] + ["0"] * 18 + [str(start)] + ["0"] * 4)

    original_stat = proc_stat(process["start_ticks"])
    (proc / "stat").write_text(original_stat)

    def fixture_path(value):
        return fake_proc_root if str(value) == "/proc" else Path(value)

    with patch.object(operator, "Path", fixture_path):
        operator.assert_live_scope()
        (proc / "stat").write_text(proc_stat(process["start_ticks"] + 1))
        blocked("LIVE_PROCESS_START_CHANGED", operator.assert_live_scope)
        (proc / "stat").write_text(original_stat)
        (proc / "cmdline").write_bytes(command + b"unexpected\0")
        blocked("LIVE_PROCESS_COMMAND_CHANGED", operator.assert_live_scope)
        (proc / "cmdline").write_bytes(command)
        process["exe_identity"][1] += 1
        blocked("LIVE_PROCESS_EXECUTABLE_IDENTITY_CHANGED", operator.assert_live_scope)
        process["exe_identity"][1] -= 1
        operator.LIVE_SCOPE["directory_identities"][operator.LIVE_DB][1] += 1
        blocked("LIVE_DIRECTORY_IDENTITY_CHANGED", operator.assert_live_scope)
        operator.LIVE_SCOPE["directory_identities"][operator.LIVE_DB][1] -= 1
        operator.LIVE_SCOPE["log_identity"][1] += 1
        blocked("LIVE_LOG_IDENTITY_CHANGED", operator.assert_live_scope)
    results.append("Synthetic process/start, command, executable, directory and log identities are enforced without real process or database access")

with fixture() as (base, db, log):
    data = {
        "frontend/build/index.html": b"original generated artifact",
        "frontend/.release/reproducible-build.json": b'{"original":"proof"}',
        "frontend/node_modules/fixture-bin": b"original executable",
        "backend/release_identity.json": b'{"original":"identity"}',
    }
    for relative, content in data.items():
        target = operator.ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    binary = operator.ROOT / "frontend/node_modules/fixture-bin"
    binary.chmod(0o755)
    binary.with_name("fixture-link").symlink_to("fixture-bin")
    originals = {name: operator.generated_manifest(operator.ROOT / name) for name in operator.CLEANUP}
    synced = []
    native_fsync = os.fsync

    def observed_fsync(fd):
        synced.append(os.readlink("/proc/self/fd/" + str(fd)))
        native_fsync(fd)

    with patch.object(os, "fsync", observed_fsync):
        operator.backup_generated_outputs()
    assert len(operator.RECEIPT["backups"]) == 4
    for entry in operator.RECEIPT["backups"]:
        assert entry["state"] == "COPY_VERIFIED"
        assert operator.generated_manifest(Path(entry["destination"])) == originals[entry["source"]]
        assert operator.generated_manifest(operator.ROOT / entry["source"]) == originals[entry["source"]]
    assert str(operator.STAGE) in synced and str(operator.STAGE / "backups") in synced
    results.append("All four capacity-checked generated backups preserve source bytes, modes, literal symlinks and fsync receipts")

with fixture() as (base, db, log):
    with patch.object(operator, "ENV", {"GITHUB_ACTIONS": "true", "GITHUB_SHA": "fixture", "MEZAN_RELEASE_BOOTSTRAP_INTENT": "1", "PYTHONPATH": "foreign-fixture", "PYTHONHOME": "foreign-fixture", "PYTHONDONTWRITEBYTECODE": "1"}):
        env = operator.build_environment(operator.ROOT)
    assert not any(name.startswith("GITHUB_") for name in env)
    assert "MEZAN_RELEASE_BOOTSTRAP_INTENT" not in env and "PYTHONHOME" not in env
    assert env["REACT_APP_BACKEND_URL"] == "https://mezansalla.com"
    assert env["XDG_CACHE_HOME"] == str(operator.STAGE / "cache")
    assert env["TMPDIR"] == str(operator.STAGE / "tmp")
    assert env["PYTHONPATH"] == str(operator.ROOT / "backend") + ":" + str(operator.ROOT)
    results.append("Adapter environment strips inherited GitHub/bootstrap markers and uses owned cache/tmp with the reviewed public origin")

assert hashlib.sha256(helper.read_bytes()).hexdigest() == EXPECTED
assert operator.D is None and operator.START is None and operator.EXPECTED_UNTRACKED is None
print(json.dumps({
    "helper_sha256": EXPECTED,
    "isolated_checks": "PASS",
    "groups": len(results),
    "results": results,
    "main_invoked": False,
    "adapter_invoked": False,
    "git_or_network_invoked": False,
    "actual_app_or_database_access": False,
    "execution_envelope_created": False,
}, indent=2))
