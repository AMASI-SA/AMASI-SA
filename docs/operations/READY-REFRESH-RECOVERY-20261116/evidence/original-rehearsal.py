#!/usr/bin/env python3
"""Reviewed PR1955 handoff/rehearsal only; never creates or closes a lease.

Run only after the previous owner's completed publication handoff, exact-M CI,
independent review of these bytes, and durable preservation of this helper.
Requires original /app at J2. A failure retains its evidence and stops; there is
no automatic retry, cleanup, quarantine, rollback, or publication in this file.
"""
if not __debug__:
    raise RuntimeError("optimized Python is forbidden")

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

ROOT = Path("/app")
BRANCH = "hotfix/prod-snap-meta-final"
A3 = "1accaa42e132ca1232ce86b3122971b48169ca81"
J2 = "2589f440106a7c5d463ee03eac3617d113319673"
INTENT = "release/release-intent-v5.json"
INTENT_SHA256 = "0d90b88def88e0c84d595376d050e80f3e18493bf9ca71284382af1af8754899"
RELEASE_ID = "rg5-255b2b13ed893324729b31c21409fa1be3be98d8c536485f712424d70bab4b4e"
PUBLIC_ORIGIN = "https://mezansalla.com"
CONTROLS = (
    "scripts/production_release_guard.py",
    "scripts/emergent_deployment_adapter.py",
    "backend/frontend_build_identity.py",
    "backend/release_protocol_v5.py",
)
GENERATED = (
    "frontend/build", "frontend/node_modules", "frontend/.release",
    "backend/release_identity.json",
)
CHILD_MARKERS = (
    "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE",
    "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH",
    "MEZAN_RELEASE_BOOTSTRAP_INTENT", "MEZAN_RELEASE_USE_REVIEWED_INTENT",
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(*args):
    result = subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True,
    )
    require(result.returncode == 0, "git operation failed: " + args[0])
    return result.stdout


def clean_at(commit):
    require(git("branch", "--show-current").decode().strip() == BRANCH,
            "original workspace is on another branch")
    require(git("rev-parse", "HEAD").decode().strip() == commit,
            "original HEAD differs from the fixed handoff")
    require(not git("status", "--porcelain=v1", "--untracked-files=no").strip(),
            "tracked source or index is dirty")


def fail_walk(error):
    raise error


def reject_bytecode():
    # Inspect names only, before importing any original-workspace Python code.
    for relative in ("backend", "scripts", ".github/workflows", ".github/actions"):
        scan_root = ROOT / relative
        try:
            scan_root.lstat()
        except FileNotFoundError:
            if relative == ".github/actions":
                continue  # This optional tree is absent in the reviewed source.
            raise
        for directory, directories, files in os.walk(
            scan_root, followlinks=False, onerror=fail_walk,
        ):
            require("__pycache__" not in directories,
                    "old bytecode directory blocks rehearsal: " + directory)
            require(not any(Path(name).suffix.casefold() in {".pyc", ".pyo"}
                            for name in files),
                    "old bytecode file blocks rehearsal: " + directory)


def metadata(info):
    return {"mode": stat.S_IMODE(info.st_mode), "type": stat.S_IFMT(info.st_mode),
            "device": info.st_dev, "inode": info.st_ino,
            "bytes": info.st_size, "mtime_ns": info.st_mtime_ns}


def backend_environment_metadata():
    # backend/.env is inspected only through its directory entry metadata.
    with os.scandir(ROOT / "backend") as entries:
        for entry in entries:
            if entry.name == ".env":
                info = entry.stat(follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                        "backend configuration must be a regular file with one link")
                return metadata(info)
    return None


def snapshot():
    environment = backend_environment_metadata()
    records = {}
    for raw in filter(None, git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0")):
        name = os.fsdecode(raw)
        if name == "backend/.env":
            continue
        require(not any(name == root or name.startswith(root + "/") for root in GENERATED),
                "protected untracked file is inside an adapter cleanup root")
        path = ROOT / name
        info = path.lstat()
        record = metadata(info)
        if stat.S_ISLNK(info.st_mode):
            record["target"] = os.readlink(path)
        elif stat.S_ISREG(info.st_mode):
            # Never follow a file symlink or read a hardlink to backend/.env.
            require(environment is None or
                    (info.st_dev, info.st_ino) != (environment["device"], environment["inode"]),
                    "untracked hardlink to protected backend configuration")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                require(metadata(os.fstat(stream.fileno())) == record,
                        "untracked file changed before inventory")
                record["sha256"] = digest(stream.read())
                require(metadata(os.fstat(stream.fileno())) == metadata(info),
                        "untracked file changed during inventory")
        else:
            raise RuntimeError("unsupported untracked entry")
        records[name] = record
    return {"untracked": records, "backend_env_metadata_only": environment}


def check_cleanup_roots():
    for name in GENERATED[:3]:
        path = ROOT / name
        if os.path.lexists(path):
            require(stat.S_ISDIR(path.lstat().st_mode), "adapter cleanup root is not a real directory")
    identity = ROOT / GENERATED[3]
    if os.path.lexists(identity):
        require(stat.S_ISREG(identity.lstat().st_mode), "generated identity is not a regular file")
    proof_directory = ROOT / "frontend/.release"
    if proof_directory.exists():
        require(all(path.name in {"reproducible-build.json", "reproducible-build.json.tmp"}
                    and stat.S_ISREG(path.lstat().st_mode) for path in proof_directory.iterdir()),
                "unexpected file in adapter proof cleanup directory")


def reject_added_path_collisions(deployment):
    for raw in filter(None, git("diff", "--name-only", "--diff-filter=A", "-z",
                                J2, deployment).split(b"\0")):
        name = os.fsdecode(raw)
        try:
            (ROOT / name).lstat()
        except FileNotFoundError:
            continue
        raise RuntimeError("new tracked path already has a filesystem entry: " + name)


def validate_fetched(deployment):
    require(git("rev-parse", "origin/" + BRANCH).decode().strip() == deployment,
            "current fetched production head differs from fixed M")
    parents = git("show", "-s", "--format=%P", deployment).decode().split()
    require(len(parents) == 2 and parents[0] == J2,
            "M must have ordered parents J2, B")
    intent_commit = parents[1]
    require(git("show", "-s", "--format=%P", intent_commit).decode().split() == [A3],
            "B must have sole parent A3")
    require(git("show", "-s", "--format=%P", A3).decode().split() == [J2],
            "A3 must have sole parent J2")
    tree = git("rev-parse", deployment + "^{tree}").decode().strip()
    require(git("rev-parse", intent_commit + "^{tree}").decode().strip() == tree,
            "M and B trees differ")
    for commit in (intent_commit, deployment):
        require(git("diff", "--name-only", A3, commit).decode().splitlines() == [INTENT],
                "content after A3 changes more than the intent")
    require(git("show", A3 + ":" + INTENT) == git("show", J2 + ":" + INTENT),
            "A3 changed the inherited release intent")
    require(digest(git("show", deployment + ":" + INTENT)) == INTENT_SHA256,
            "M intent bytes differ from the accepted candidate")
    for path in CONTROLS:
        require(git("show", deployment + ":" + path) == git("show", J2 + ":" + path),
                "imported operation control would change across handoff")
    return intent_commit, tree


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-sha", required=True)
    deployment = parser.parse_args().deployment_sha
    require(re.fullmatch(r"[0-9a-f]{40}", deployment), "deployment must be an exact full SHA")
    require(ROOT.resolve() == ROOT and stat.S_ISDIR(ROOT.lstat().st_mode),
            "original /app must be a real directory")
    backend_environment_metadata()
    check_cleanup_roots()
    clean_at(J2)
    reject_bytecode()
    for path in CONTROLS:
        require(stat.S_ISREG((ROOT / path).lstat().st_mode)
                and (ROOT / path).read_bytes() == git("show", J2 + ":" + path),
                "operation control differs from reviewed J2")
    require(git("remote", "get-url", "origin").decode().strip() in {
        "https://github.com/AMASI-SA/AMASI-SA.git",
        "https://github.com/AMASI-SA/AMASI-SA", "git@github.com:AMASI-SA/AMASI-SA.git",
    }, "unexpected original repository origin")
    sys.path.insert(0, str(ROOT / "scripts"))
    import production_release_guard as guard
    import emergent_deployment_adapter as adapter
    require(guard.REPO_ROOT == adapter.REPO_ROOT == ROOT, "unexpected operation module root")
    evidence = Path(tempfile.mkdtemp(prefix="mezan-ready1955-rehearsal-", dir="/tmp"))

    def save(name, value):
        (evidence / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    print(json.dumps({"evidence_directory": str(evidence)}), flush=True)
    try:
        observed = guard.status()  # Outside the outer lock: status locks internally.
        save("status-before.json", observed)
        require(observed.get("active") is False
                and not observed.get("incomplete_lease_directory"), "release handoff is not inactive")
        with guard._release_operation_lock():
            require(not os.path.lexists(guard.LEASE_DIR), "another or incomplete lease owns /app")
            backend_environment_metadata()
            clean_at(J2)
            reject_bytecode()
            before = snapshot()
            save("preservation-before.json", before)
            adapter.load_release_intent()  # Official preflight; old contamination fails before FF/cleanup.
            git("fetch", "origin", BRANCH)
            intent_commit, tree = validate_fetched(deployment)
            reject_added_path_collisions(deployment)
            git("merge", "--ff-only", deployment)
            clean_at(deployment)
            require(snapshot() == before, "unrelated files changed during fast-forward")
            intent = adapter.load_release_intent()
            require(digest((ROOT / INTENT).read_bytes()) == INTENT_SHA256
                    and intent["source_git_sha"] == A3 and intent["source_base_git_sha"] == J2
                    and intent["runtime_identity"]["release_id"] == RELEASE_ID,
                    "reviewed intent binding differs")
            check_cleanup_roots()
            child = dict(os.environ, REACT_APP_BACKEND_URL=PUBLIC_ORIGIN,
                         PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1")
            removed = sorted(name for name in CHILD_MARKERS if name in child)
            for name in CHILD_MARKERS:
                child.pop(name, None)
            save("child-context.json", {"removed_marker_names": removed})
            # The official adapter emits sanitized environment evidence. Never dump child env.
            with (evidence / "adapter.log").open("xb") as log:
                result = subprocess.run(["yarn", "build"], cwd=ROOT / "frontend",
                                        env=child, stdout=log, stderr=subprocess.STDOUT)
            require(result.returncode == 0, "official adapter failed; retained log requires review")
            reject_bytecode()
            adapter.load_release_intent()
            runtime = guard._read_runtime_identity()
            require(guard.exact_json_equal(runtime, intent["runtime_identity"])
                    and runtime["release_id"] == RELEASE_ID, "materialized runtime identity differs")
            guard._assert_local_identity_binding(runtime, expected_git_sha=A3)
            git("fetch", "origin", BRANCH)
            require(validate_fetched(deployment) == (intent_commit, tree), "final release binding changed")
            after = snapshot()
            save("preservation-after.json", after)
            require(after == before, "unrelated files or backend configuration metadata changed")
            clean_at(deployment)
            require(git("rev-parse", "HEAD^{tree}").decode().strip() == tree,
                    "deployment tree moved during rehearsal")
            require(not os.path.lexists(guard.LEASE_DIR), "lease appeared during rehearsal")
            save("result.json", {"ok": True, "source_git_sha": A3, "source_base_git_sha": J2,
                                 "intent_commit": intent_commit, "deployment_git_sha": deployment,
                                 "tree": tree, "release_id": RELEASE_ID,
                                 "untracked_count": len(before["untracked"]),
                                 "preservation_verified": True, "active_lease": False})
        print(json.dumps({"ok": True, "evidence_directory": str(evidence)}), flush=True)
    except Exception as error:
        save("failure.json", {"ok": False, "error_type": type(error).__name__})
        print(json.dumps({"ok": False, "evidence_directory": str(evidence),
                          "error_type": type(error).__name__}), flush=True)
        raise


if __name__ == "__main__":
    main()
