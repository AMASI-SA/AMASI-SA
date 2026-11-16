#!/usr/bin/env python3
"""PR1955 only: one receipt-bound prepublish or verify, never publication.

Use only after the accepted original rehearsal and final M gates. The official
prepare CLI must first create private prepare.json in the fixed receipt root.
An existing phase directory forbids a second attempt. Failures retain evidence.
This wrapper never prepares, builds, aborts, edits source, or writes order data.
"""
import sys

sys.dont_write_bytecode = True
if not __debug__:
    raise RuntimeError("optimized Python is forbidden")

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess

ROOT = Path("/app")
RECEIPTS = Path("/tmp/ready1955-publication-20261116")
BRANCH = "hotfix/prod-snap-meta-final"
ACTOR = "chatgpt-ready1955-20261116-root"
A = "1accaa42e132ca1232ce86b3122971b48169ca81"
J = "2589f440106a7c5d463ee03eac3617d113319673"
B = "dd4fc24dbef4b2d6e4abafac5ee5ee47d14e802b"
M = "b05cd3c9b25ce22df1b2dd06c23d97606bd632c6"
TREE = "aded5cb29b69b5bb09b7386932ad9fc5c35d1f44"
RUNTIME = "rg5-255b2b13ed893324729b31c21409fa1be3be98d8c536485f712424d70bab4b4e"
INTENT_PATH = "release/release-intent-v5.json"
INTENT_HASH = "0d90b88def88e0c84d595376d050e80f3e18493bf9ca71284382af1af8754899"
FRONTEND_TREE = "8017e720f266ed5d6b0141d83312512e8052396c7da65e22557dce1c8fe79fcb"
ORIGIN = "https://mezansalla.com"
PRIOR_PLATFORM = "c1784c4"
CONTROLS = {
    "scripts/production_release_guard.py": "4e56f1f60f5f79931658b8f95a0aeb2269471025ca84f7e5b651cd284806349a",
    "backend/frontend_build_identity.py": "7896dfa2c9b4fea8c426b47c5b75e331380c0a4a3f947028aa4f5afa101f9d87",
    "backend/release_protocol_v5.py": "1961ac18a5363ec425ceec4f98f96f611d378b09e19380d4756bd6776987d8ef",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def real_directory(path, private=False):
    info = path.lstat()
    require(path.resolve(strict=True) == path and stat.S_ISDIR(info.st_mode),
            "directory is not real")
    if private:
        require(stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.geteuid(),
                "receipt directory ownership or mode differs")


def read_file(path, private=False):
    require(path.resolve(strict=True) == path, "input path contains a symlink")
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
            "input must be a regular file with one link")
    if private:
        require(stat.S_IMODE(before.st_mode) == 0o600 and before.st_uid == os.geteuid(),
                "private input ownership or mode differs")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        require(stamp(os.fstat(stream.fileno())) == stamp(before), "input changed before read")
        data = stream.read()
        require(stamp(os.fstat(stream.fileno())) == stamp(before), "input changed during read")
    require(stamp(path.lstat()) == stamp(before), "input path changed after read")
    return data


def new_file(path):
    real_directory(path.parent, private=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    info = os.fstat(descriptor)
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o600,
            "evidence output is not a private regular file")
    return os.fdopen(descriptor, "w", encoding="utf-8")


def save(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with new_file(path) as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return sha(raw.encode("utf-8"))


def git(*args):
    result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True,
                            env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    require(result.returncode == 0, "Git read failed")
    return result.stdout.decode("utf-8").strip()


def pin_source():
    real_directory(ROOT)
    # Metadata only; no backend configuration content is opened or hashed.
    real_directory(ROOT / "backend")
    with os.scandir(ROOT / "backend") as entries:
        for entry in entries:
            if entry.name == ".env":
                info = entry.stat(follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                        "backend configuration has an unsafe alias")
    require(git("branch", "--show-current") == BRANCH and git("rev-parse", "HEAD") == M,
            "original branch or HEAD differs from fixed M")
    require(git("rev-parse", "HEAD^{tree}") == TREE, "deployment tree differs")
    require(not git("status", "--porcelain=v1", "--untracked-files=no"), "tracked source is dirty")
    require(git("show", "-s", "--format=%P", M) == J + " " + B, "M parents differ")
    require(git("diff", "--name-only", A, M).splitlines() == [INTENT_PATH],
            "content after A differs beyond intent")
    for relative, expected in CONTROLS.items():
        path = ROOT / relative
        real_directory(path.parent)
        require(not os.path.lexists(path.parent / "__pycache__")
                and not os.path.lexists(path.with_suffix(".pyc"))
                and not os.path.lexists(path.with_suffix(".pyo")), "control bytecode exists")
        require(sha(read_file(path)) == expected, "official control bytes differ")
    raw = read_file(ROOT / INTENT_PATH)
    require(sha(raw) == INTENT_HASH, "reviewed intent differs")
    return json.loads(raw)


def aware(value):
    require(isinstance(value, str), "timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None and parsed.utcoffset() is not None,
            "timestamp must include its timezone")
    return parsed


def result_binding(value, verified=False):
    require(isinstance(value, dict) and value.get("source_git_sha") == A
            and value.get("git_sha") == A and value.get("deployment_git_sha") == M
            and value.get("release_id") == RUNTIME and value.get("protocol_version") == 5,
            "official result identity differs")
    if verified:
        require(value.get("verified") is True and type(value.get("checks")) is int
                and value["checks"] == 3 and value.get("source_base_git_sha") == J
                and value.get("frontend_artifact_tree_sha256") == FRONTEND_TREE,
                "official verification result is incomplete")
    else:
        require(value.get("ready_to_publish") is True, "official prepublish did not accept")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepublish", "verify"))
    parser.add_argument("--prepare-sha256", required=True)
    parser.add_argument("--platform-version")
    parser.add_argument("--deployment-completed-at")
    args = parser.parse_args()
    phase_dir = None
    try:
        require(re.fullmatch(r"[0-9a-f]{64}", args.prepare_sha256), "prepare digest is invalid")
        intent = pin_source()
        real_directory(RECEIPTS, private=True)
        raw = read_file(RECEIPTS / "prepare.json", private=True)
        require(sha(raw) == args.prepare_sha256, "prepare receipt digest differs")
        prepared = json.loads(raw)
        require(isinstance(prepared, dict) and prepared.get("actor") == ACTOR
                and prepared.get("source_git_sha") == A and prepared.get("git_sha") == A
                and prepared.get("source_base_git_sha") == J
                and prepared.get("deployment_git_sha") == M
                and prepared.get("release_id") == RUNTIME and prepared.get("branch") == BRANCH
                and prepared.get("protocol_version") == 5, "prepare receipt is not this release")
        prepared_at = aware(prepared.get("prepared_at"))
        if args.phase == "verify":
            require(args.platform_version is not None
                    and re.fullmatch(r"[0-9a-f]{7,40}", args.platform_version)
                    and args.platform_version != PRIOR_PLATFORM, "new platform version is required")
            completed = aware(args.deployment_completed_at)
            require(prepared_at < completed <= datetime.now(timezone.utc),
                    "completion must follow prepare and cannot be in the future")
            real_directory(RECEIPTS / "prepublish", private=True)
            prior = json.loads(read_file(RECEIPTS / "prepublish" / "request.json", private=True))
            require(prior.get("prepare_sha256") == args.prepare_sha256,
                    "prepublish used another prepare receipt")
            result_binding(json.loads(read_file(RECEIPTS / "prepublish" / "result.json", private=True)))
        else:
            require(args.platform_version is None and args.deployment_completed_at is None,
                    "publication observations belong only to verify")
        require(prepared.get("runtime_identity") == intent.get("runtime_identity"),
                "prepare runtime differs from reviewed intent")
        sys.path.insert(0, str(ROOT / "scripts"))
        import production_release_guard as guard
        require(guard.REPO_ROOT == ROOT and Path(guard.__file__).resolve()
                == ROOT / "scripts/production_release_guard.py", "unexpected official guard")
        reserved = RECEIPTS / args.phase
        reserved.mkdir(mode=0o700)  # No exist_ok: reserve exactly one attempt.
        phase_dir = reserved
        save(phase_dir / "request.json", {
            "phase": args.phase, "prepare_sha256": args.prepare_sha256, "actor": ACTOR,
            "source_git_sha": A, "source_base_git_sha": J, "deployment_git_sha": M,
            "release_id": RUNTIME, "requested_at": datetime.now(timezone.utc).isoformat(),
            "operator_recorded_platform_version": args.platform_version,
            "operator_recorded_completion_at": args.deployment_completed_at,
        })
        with new_file(phase_dir / "operation.log") as log, redirect_stdout(log), redirect_stderr(log):
            with guard._release_operation_lock():
                pin_source()
                guard._assert_active_lease_unchanged(prepared)
                if args.phase == "prepublish":
                    result = guard._prepublish_locked()
                else:
                    result = guard._verify_locked(ORIGIN)
                result_hash = save(phase_dir / "result.json", result)
                pin_source()
                result_binding(result, verified=args.phase == "verify")
            if args.phase == "verify":
                # Public status takes the mutex itself; never call it inside our lock.
                status = guard.status()
                save(phase_dir / "status-after.json", status)
                require(status.get("active") is False
                        and not status.get("incomplete_lease_directory"), "release lease is not inactive")
        print(json.dumps({"ok": True, "phase": args.phase, "deployment_git_sha": M,
                          "release_id": RUNTIME, "result_sha256": result_hash,
                          "result_path": str(phase_dir / "result.json"),
                          **({"verified": True, "checks": result["checks"], "active": False}
                             if args.phase == "verify" else {"ready_to_publish": True})}))
        return 0
    except Exception as error:
        recorded = False
        if phase_dir is not None:
            try:
                save(phase_dir / "failure.json", {"ok": False, "error_type": type(error).__name__,
                                                   "error": str(error)})
                recorded = True
            except Exception:
                pass  # Never replace existing evidence or retry an operation.
        print(json.dumps({"ok": False, "phase": args.phase,
                          "error_type": type(error).__name__, "failure_recorded": recorded}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
