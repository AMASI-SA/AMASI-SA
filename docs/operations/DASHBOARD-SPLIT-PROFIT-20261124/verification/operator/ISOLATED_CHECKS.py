#!/usr/bin/env python3
"""Independent, bounded local checks; never invokes main or live /app operations."""
from pathlib import Path
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import types
from unittest.mock import patch

EXPECTED = "98719b39a298c98c5a56d517bcdf2edd3f350497e53bd05aadf4202a2d43c65b"
assert len(sys.argv) == 2, "Pass the reviewed helper path"
helper_path = Path(sys.argv[1]).resolve()
payload = helper_path.read_bytes()
assert hashlib.sha256(payload).hexdigest() == EXPECTED
operator = types.ModuleType("isolated_pr2122_operator_review")
operator.__file__ = str(helper_path)
exec(compile(payload, str(helper_path), "exec"), operator.__dict__)
results = []


def expect_block(code, call):
    try:
        call()
    except operator.Blocked as exc:
        assert str(exc) == code, (str(exc), code)
        return
    raise AssertionError("Expected block: " + code)


with tempfile.TemporaryDirectory(prefix="pr2122-review-only-") as temporary:
    base = Path(temporary)
    operator.ROOT = base / "app-fixture"
    operator.STAGE = base / "private-stage"
    operator.ROOT.mkdir()
    operator.STAGE.mkdir(mode=0o700)
    operator.RECEIPT = {"steps": [], "backups": []}
    fixtures = {
        "frontend/build/index.html": b"<html>old generated artifact</html>",
        "frontend/.release/reproducible-build.json": b'{"original":"proof"}',
        "frontend/node_modules/local-bin": b"original executable bytes",
        "backend/release_identity.json": b'{"original":"identity"}',
    }
    for relative, data in fixtures.items():
        target = operator.ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    binary = operator.ROOT / "frontend/node_modules/local-bin"
    binary.chmod(0o755)
    (binary.parent / "link").symlink_to("local-bin")
    original_manifests = {
        relative: operator.generated_manifest(operator.ROOT / relative)
        for relative in operator.CLEANUP
    }
    fsynced = []
    native_fsync = os.fsync

    def observed_fsync(fd):
        fsynced.append(os.readlink("/proc/self/fd/" + str(fd)))
        native_fsync(fd)

    with patch.object(os, "fsync", observed_fsync):
        operator.backup_generated_outputs()
    assert len(operator.RECEIPT["backups"]) == 4
    for entry in operator.RECEIPT["backups"]:
        assert entry["state"] == "COPY_VERIFIED"
        assert operator.generated_manifest(Path(entry["destination"])) == original_manifests[entry["source"]]
        assert operator.generated_manifest(operator.ROOT / entry["source"]) == original_manifests[entry["source"]]
    assert str(operator.STAGE) in fsynced
    assert str(operator.STAGE / "backups") in fsynced
    copied_link = operator.STAGE / "backups/frontend--node_modules/link"
    assert copied_link.is_symlink() and os.readlink(copied_link) == "local-bin"
    assert stat.S_IMODE((operator.STAGE / "backups/frontend--node_modules/local-bin").stat().st_mode) == 0o755
    results.append("all four generated backups preserve content, modes, literal symlinks and fsync receipt/stage entries")

    runtime = operator.ROOT / operator.RUNTIME_PATH
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_bytes(b"original-runtime-prefix\n")
    foreign = operator.ROOT / "owner-note.txt"
    foreign.write_bytes(b"unchanged owner material")
    fake_paths = b".emergent/stripped_env_names\x00owner-note.txt\x00"
    operator.git = lambda *args: fake_paths
    info = runtime.lstat()
    operator.RUNTIME_IDENTITY = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode))
    before = operator.untracked_snapshot()
    with runtime.open("ab") as stream:
        stream.write(b"new runtime metadata\n")
    operator.preserve_foreign(before)
    assert operator.RECEIPT["foreign_preservation"]["append_only_path"] == operator.RUNTIME_PATH
    results.append("only same-file prefix-preserving runtime append is accepted")
    appended = runtime.read_bytes()
    runtime.write_bytes(b"X" + appended[1:])
    expect_block("RUNTIME_METADATA_PREFIX_CHANGED", lambda: operator.preserve_foreign(before))
    runtime.write_bytes(appended)
    replacement = runtime.with_name("replacement")
    replacement.write_bytes(appended)
    replacement.chmod(stat.S_IMODE(info.st_mode))
    replacement.replace(runtime)
    expect_block("RUNTIME_METADATA_IDENTITY_CHANGED", lambda: operator.preserve_foreign(before))
    results.append("runtime prefix corruption and byte-identical inode replacement are blocked")

    actual_read_text = Path.read_text
    nested = operator.ROOT / "frontend/node_modules/mounted"
    fake_mountinfo = (
        "1 0 0:1 / / rw - overlay overlay rw\n"
        + "2 1 0:2 / " + str(nested) + " rw - tmpfs tmpfs rw\n"
    )

    def mounted_read(path, *args, **kwargs):
        return fake_mountinfo if str(path) == "/proc/self/mountinfo" else actual_read_text(path, *args, **kwargs)

    with patch.object(Path, "read_text", mounted_read):
        expect_block("GENERATED_TARGET_CONTAINS_MOUNT", lambda: operator.reject_generated_mounts(operator.ROOT / "frontend/node_modules"))
    results.append("nested mount point is blocked independently of st_dev")

    heartbeat = base / "child-heartbeat"
    child_code = (
        "import pathlib,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); p=pathlib.Path("
        + repr(str(heartbeat)) + ");\nwhile True:\n p.write_text(str(time.monotonic()))\n time.sleep(0.03)\n"
    )
    leader_code = (
        'import subprocess,sys,time; subprocess.Popen([sys.executable, "-c", '
        + repr(child_code) + "]); time.sleep(60)"
    )
    started = time.monotonic()
    try:
        operator.run("isolated-descendant-timeout", [sys.executable, "-c", leader_code], cwd=base, timeout=0.6)
        raise AssertionError("timeout was not raised")
    except subprocess.TimeoutExpired:
        pass
    assert time.monotonic() - started < 12
    assert heartbeat.exists()
    time.sleep(0.15)
    last = heartbeat.read_bytes()
    time.sleep(0.15)
    assert heartbeat.read_bytes() == last, "descendant survived timeout"
    assert operator.RECEIPT["steps"][-1]["status"] == "FAILED"
    results.append("timeout terminates process group including SIGTERM-ignoring descendant and records failure")

assert hashlib.sha256(helper_path.read_bytes()).hexdigest() == EXPECTED
print(json.dumps({
    "helper_sha256": EXPECTED,
    "isolated_checks": "PASS",
    "results": results,
    "no_live_operations": True,
}, indent=2))
