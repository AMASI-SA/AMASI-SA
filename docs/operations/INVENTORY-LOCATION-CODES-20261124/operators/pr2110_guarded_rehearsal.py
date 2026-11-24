#!/usr/bin/env python3
"""One-shot, reviewed-base-only PR2110 /app rehearsal; requires reviewed envelope.

External prerequisite: exact deployment D CI has completed successfully.
This helper does not prepare a lease, publish, restart, recalculate, or roll back.
On failure it leaves its private receipts/backups in place for owner review.
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
J = "3fd7594af806615d4c53616100dd546ef7b5fe9f"
A = B = D = TREE = RELEASE_ID = None
EXPECTED_UNTRACKED = None
RUNTIME_PATH = ".emergent/stripped_env_names"
RUNTIME_IDENTITY = None
CLEANUP = (
    "frontend/build", "frontend/.release", "backend/release_identity.json",
    "frontend/node_modules",
)
STAGE: Path | None = None
STEP = "startup"
RECEIPT: dict = {
    "task": "PR2110_guarded_app_rehearsal", "status": "RUNNING",
    "initial_head": J, "source_sha": A, "intent_sha": B,
    "deployment_sha": D, "tree": TREE, "release_id": RELEASE_ID,
    "external_precondition": "operator verified completed successful exact-D CI",
    "steps": [], "backups": [],
}
ENV = os.environ.copy()
ENV["PYTHONDONTWRITEBYTECODE"] = "1"
ENV["GIT_TERMINAL_PROMPT"] = "0"


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


def git(name: str, *args: str) -> bytes:
    return run(name, ["git", "-C", str(ROOT), *args], capture=True)


def text_git(name: str, *args: str) -> str:
    return git(name, *args).decode("utf-8").strip()


def assert_checkout(expected: str) -> None:
    require(text_git("current-head", "rev-parse", "HEAD") == expected, "HEAD_CHANGED")
    require(text_git("current-branch", "symbolic-ref", "--short", "HEAD") == BRANCH,
            "WRONG_BRANCH")
    require(not git("tracked-status", "status", "--porcelain=v1", "--untracked-files=no"),
            "TRACKED_WORKTREE_DIRTY")


def fetch_expected() -> None:
    run("fetch-production", ["git", "-C", str(ROOT), "fetch", "--no-tags", "origin", BRANCH])
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


def untracked_snapshot() -> dict:
    entries = sorted(os.fsdecode(value) for value in
                     git("untracked-paths", "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
                     if value)
    records = {}

    def visit(path: Path) -> None:
        relative = path.relative_to(ROOT).as_posix()
        require(relative != "." and ".." not in Path(relative).parts, "UNSAFE_UNTRACKED_PATH")
        records[relative] = file_fingerprint(path)
        if records[relative]["type"] == "directory":
            for child in sorted(path.iterdir()):
                visit(child)

    for relative in entries:
        visit(ROOT / relative)
    return {"git_entries": entries, "records": records}


def assert_no_lease(guard: object) -> None:
    require(not os.path.lexists(guard.LEASE_DIR), "ACTIVE_OR_INCOMPLETE_LEASE_DIRECTORY")
    require(not os.path.lexists(guard.LEASE_PATH), "ACTIVE_LEASE")


def preserve_foreign(before: dict) -> None:
    after = untracked_snapshot()
    require(after["git_entries"] == before["git_entries"], "FOREIGN_PATH_SET_CHANGED")
    for name, record in before["records"].items():
        actual = after["records"].get(name)
        if name == RUNTIME_PATH:
            info = (ROOT / name).lstat()
            require(stat.S_ISREG(info.st_mode)
                    and (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode)) == RUNTIME_IDENTITY,
                    "RUNTIME_METADATA_IDENTITY_CHANGED")
        if name != RUNTIME_PATH or actual == record:
            require(actual == record, "FOREIGN_ENTRY_CHANGED")
            continue
        require(record["type"] == actual.get("type") == "file"
                and actual["mode"] == record["mode"]
                and actual["bytes"] >= record["bytes"], "RUNTIME_METADATA_NOT_APPEND_ONLY")
        descriptor = os.open(ROOT / name, os.O_RDONLY | os.O_NOFOLLOW)
        digest, left = hashlib.sha256(), record["bytes"]
        with os.fdopen(descriptor, "rb") as stream:
            while left:
                value = stream.read(min(1024 * 1024, left))
                require(bool(value), "RUNTIME_METADATA_TRUNCATED")
                digest.update(value)
                left -= len(value)
        require(digest.hexdigest() == record["sha256"], "RUNTIME_METADATA_PREFIX_CHANGED")
    require(set(after["records"]) == set(before["records"]), "FOREIGN_RECORD_SET_CHANGED")
    private_json(STAGE / "untracked-latest.json", after)
    RECEIPT["foreign_preservation"] = {
        "paths": len(before["git_entries"]),
        "identical_entries": sum(after["records"][name] == record for name, record in before["records"].items()),
        "append_only_path": RUNTIME_PATH if after["records"].get(RUNTIME_PATH) != before["records"].get(RUNTIME_PATH) else None,
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
        original = checked_cleanup_entry(relative)
        entry = {"source": relative, "present": original is not None}
        RECEIPT["backups"].append(entry)
        save_receipt()
        if original is None:
            continue
        reject_generated_mounts(source)
        # Copy without following symlinks. Canonical adapter, not this helper,
        # later removes its inherited generated targets inside /app.
        expected = generated_manifest(source)
        needed = sum(item.get("bytes", 0) for item in expected.values())
        require(shutil.disk_usage(backups).free >= needed + 256 * 1024 * 1024,
                "PRIVATE_BACKUP_CAPACITY_INSUFFICIENT")
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
        entry.update(destination=str(destination), state="COPY_VERIFIED", entries=len(expected), bytes=needed)
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


def validate_intent() -> None:
    code = (
        "from scripts.emergent_deployment_adapter import load_release_intent; "
        "i=load_release_intent(); "
        f"assert i['source_git_sha']=={A!r}; "
        f"assert i['source_base_git_sha']=={J!r}; "
        f"assert i['runtime_identity']['release_id']=={RELEASE_ID!r}; "
        "print('UNCHANGED_INTENT_VALIDATOR_PASS')"
    )
    run("validate-intent", [sys.executable, "-c", code])


def protected_rehearsal(guard: object) -> None:
    global STAGE, STEP, RUNTIME_IDENTITY
    assert_no_lease(guard)
    for parent in (ROOT, ROOT / "frontend", ROOT / "backend", ROOT / ".git"):
        require(parent.is_dir() and not parent.is_symlink(), "UNSAFE_WORKSPACE_ROOT")
    STAGE = Path(tempfile.mkdtemp(prefix="mezan-pr2110-rehearsal-", dir="/tmp"))
    STAGE.chmod(0o700)
    fsync_directory(STAGE)
    fsync_directory(STAGE.parent)
    RECEIPT["stage"] = str(STAGE)
    helper_bytes = Path(__file__).read_bytes()
    descriptor = os.open(STAGE / "operator-helper.py", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(helper_bytes)
        stream.flush()
        os.fsync(stream.fileno())
    RECEIPT["helper_sha256"] = hashlib.sha256(helper_bytes).hexdigest()
    save_receipt()
    print("STAGE_PATH=" + str(STAGE), flush=True)
    assert_checkout(J)  # No automatic resumption, reset, or rollback.
    fetch_expected()
    require(text_git("deployment-tree", "rev-parse", D + "^{tree}") == TREE,
            "DEPLOYMENT_TREE_MISMATCH")
    require(text_git("intent-tree", "rev-parse", B + "^{tree}") == TREE,
            "INTENT_TREE_MISMATCH")
    require(text_git("deployment-parents", "show", "-s", "--format=%P", D).split() == [J, B],
            "DEPLOYMENT_PARENTS_MISMATCH")
    require(text_git("intent-parents", "show", "-s", "--format=%P", B).split() == [A],
            "INTENT_PARENT_MISMATCH")
    # Multiple source-only checkpoints are permitted before final source A;
    # full history J..A must never touch the old reviewed intent.
    require(not git("source-intent-history", "rev-list", "--full-history", J + ".." + A,
                    "--", "release/release-intent-v5.json"), "SOURCE_CHANGED_PRIOR_INTENT")
    require(not git("source-intent-diff", "diff", "--name-only", J, A,
                    "--", "release/release-intent-v5.json"), "SOURCE_INTENT_BYTES_CHANGED")
    git("base-ancestry", "merge-base", "--is-ancestor", J, A)
    git("source-ancestry", "merge-base", "--is-ancestor", A, D)
    require(git("intent-only-diff", "diff", "--name-only", "-z", A, D, "--")
            == b"release/release-intent-v5.json\0", "NON_INTENT_CHANGE_AFTER_A")
    before = untracked_snapshot()
    if RUNTIME_PATH in before["records"]:
        info = (ROOT / RUNTIME_PATH).lstat()
        require(stat.S_ISREG(info.st_mode), "RUNTIME_METADATA_TYPE_INVALID")
        RUNTIME_IDENTITY = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode))
    require(len(before["git_entries"]) == EXPECTED_UNTRACKED, "UNTRACKED_COUNT_CHANGED")
    for relative in before["records"]:
        require(not any(relative == item or relative.startswith(item + "/")
                        or item.startswith(relative + "/") for item in CLEANUP),
                "FOREIGN_UNTRACKED_OVERLAPS_ADAPTER_CLEANUP")
    private_json(STAGE / "untracked-before.json", before)
    for relative in CLEANUP:
        checked_cleanup_entry(relative)
    backup_generated_outputs()
    save_receipt()
    # Final read checks immediately precede the only tracked-source operation.
    assert_no_lease(guard)
    assert_checkout(J)
    preserve_foreign(before)
    run("fast-forward", ["git", "-C", str(ROOT), "merge", "--ff-only", "--no-edit", D])
    assert_checkout(D)
    validate_intent()  # Fail before moving generated outputs if source drift exists.
    for entry in RECEIPT["backups"]:
        if entry["present"]:
            source = ROOT / entry["source"]
            reject_generated_mounts(source)
            expected = json.loads((STAGE / (entry["source"].replace("/", "--") + "-manifest.json")).read_text())
            require(generated_manifest(source) == expected, "GENERATED_OUTPUT_CHANGED_BEFORE_ADAPTER")
        else:
            require(not os.path.lexists(ROOT / entry["source"]),
                    "ORIGINALLY_ABSENT_GENERATED_OUTPUT_APPEARED")
    preserve_foreign(before)
    assert_no_lease(guard)
    environment = ENV.copy()
    for name in tuple(environment):
        if name.startswith("GITHUB_"):
            environment.pop(name)
    environment["REACT_APP_BACKEND_URL"] = "https://mezansalla.com"
    # The repository dispatcher invokes the unchanged v5 governed adapter.
    run("canonical-app-adapter", ["yarn", "build"], cwd=ROOT / "frontend",
        environment=environment, timeout=2400)
    run("verify-frontend", [sys.executable, "scripts/verify_frontend_build.py",
                           "--expected-git-sha", A, "--reviewed-intent-v5"], timeout=180)
    run("verify-packages", [sys.executable, "scripts/emergent_deployment_adapter.py",
                           "verify-packages"], timeout=240)
    STEP = "final-identity-and-preservation"
    intent = json.loads((ROOT / "release/release-intent-v5.json").read_text())
    identity = json.loads((ROOT / "backend/release_identity.json").read_text())
    require(identity == intent["runtime_identity"] and identity["release_id"] == RELEASE_ID,
            "RUNTIME_IDENTITY_MISMATCH")
    run("canonical-full-proof", [sys.executable, "-c",
        "from frontend_build_identity import read_frontend_build_identity, read_frontend_reproducibility_proof; "
        "from scripts.emergent_deployment_adapter import load_release_intent; "
        "i=load_release_intent(); f=read_frontend_build_identity(expected_git_sha=i['source_git_sha']); "
        "assert f==i['frontend_build']; "
        "assert read_frontend_reproducibility_proof(frontend_build=f)==i['frontend_reproducibility']; "
        "print('CANONICAL_PROOF_PASS')"], environment={**ENV, "PYTHONPATH": "backend:."})
    require(identity["source_git_sha"] == A, "RUNTIME_SOURCE_MISMATCH")
    assert_checkout(D)
    preserve_foreign(before)
    fetch_expected()
    assert_checkout(D)
    assert_no_lease(guard)
    RECEIPT.update(status="PASS", unchanged_untracked_count=EXPECTED_UNTRACKED,
                   lease_created=False, published=False, recalculated=False)
    save_receipt()


def interrupt(signum: int, frame: object) -> None:
    raise Blocked("INTERRUPTED")


def main() -> int:
    global STEP, A, B, D, TREE, RELEASE_ID, EXPECTED_UNTRACKED
    try:
        require(len(sys.argv) == 2, "REVIEWED_ENVELOPE_REQUIRED")
        envelope_bytes = Path(sys.argv[1]).read_bytes()
        require(len(envelope_bytes) <= 16384, "ENVELOPE_TOO_LARGE")
        envelope = json.loads(envelope_bytes)
        require(set(envelope) == {"source_sha", "source_base_sha", "intent_sha", "deployment_sha",
                                  "tree", "release_id", "expected_untracked_count"}, "ENVELOPE_FIELDS_INVALID")
        require(envelope["source_base_sha"] == J, "ENVELOPE_BASE_CHANGED")
        for name in ("source_sha", "source_base_sha", "intent_sha", "deployment_sha", "tree"):
            require(type(envelope[name]) is str and re.fullmatch(r"[0-9a-f]{40}", envelope[name]) is not None,
                    "ENVELOPE_SHA_INVALID")
        require(type(envelope["release_id"]) is str and re.fullmatch(r"rg5-[0-9a-f]{64}", envelope["release_id"]) is not None,
                "ENVELOPE_ID_INVALID")
        require(type(envelope["expected_untracked_count"]) is int and 0 <= envelope["expected_untracked_count"] <= 10000,
                "ENVELOPE_PATH_COUNT_INVALID")
        A, B, D, TREE, RELEASE_ID, EXPECTED_UNTRACKED = (envelope[k] for k in
            ("source_sha", "intent_sha", "deployment_sha", "tree", "release_id", "expected_untracked_count"))
        require(len({J, A, B, D}) == 4, "ENVELOPE_COMMITS_NOT_DISTINCT")
        RECEIPT.update(source_sha=A, intent_sha=B, deployment_sha=D, tree=TREE, release_id=RELEASE_ID,
                       envelope_sha256=hashlib.sha256(envelope_bytes).hexdigest())
        require(Path.cwd() == ROOT and ROOT.resolve() == ROOT, "RUN_FROM_REAL_APP_ROOT")
        require(text_git("git-root", "rev-parse", "--show-toplevel") == str(ROOT), "WRONG_GIT_ROOT")
        require(text_git("git-directory", "rev-parse", "--absolute-git-dir") == str(ROOT / ".git"),
                "UNSUPPORTED_GIT_DIRECTORY")
        guard_path = ROOT / "scripts/production_release_guard.py"
        require(guard_path.read_bytes() == git("original-guard-bytes", "show", J + ":scripts/production_release_guard.py"),
                "ORIGINAL_GUARD_CHANGED")
        status = json.loads(run("initial-public-status", [sys.executable, str(guard_path), "status"],
                                capture=True, timeout=45))
        require(status.get("active") is False, "ACTIVE_RELEASE_LEASE")
        RECEIPT["initial_public_status"] = {"active": False}
        spec = importlib.util.spec_from_file_location("pr2110_original_release_guard", guard_path)
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
        RECEIPT.update(status="BLOCKED", failure_step=STEP, failure_code=code,
                       automatic_rollback=False)
        try:
            save_receipt()
        except BaseException:
            pass
        print("FINAL_STATUS=BLOCKED:" + code, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
