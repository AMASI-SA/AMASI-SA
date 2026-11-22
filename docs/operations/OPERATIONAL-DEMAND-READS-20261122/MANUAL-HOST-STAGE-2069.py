#!/usr/bin/env python3
"""Stage the already-rehearsed PR2069 artifacts. Never prepare or publish."""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path("/app")
CANDIDATE = Path("/tmp/mezan-pr2069-0mhyfa47/repo")
J = "9b4fb716b430c32073b5ea7abaeaff62646d9633"
A = "063611ff163c90d80cdbf96446a28584fe429846"
B = "5db36bd28ae86acaca8ceeb9cceee8e350916f12"
M = "06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35"
TREE = "057c273506c237af2035c7976cc7fe8e9aacaeb2"
RID = "rg5-90a88a6e7ac6a65670274d6180be0338d689f64d91efbf5cdfbbf651ca182bf7"
ARTIFACT = "9437b75f8c005e379a7132e62c97e90af71e596d1907ba935f6a2a01da2cd4f4"
BRANCH = "hotfix/prod-snap-meta-final"
OUTPUTS = ("frontend/build", "frontend/.release/reproducible-build.json",
           "backend/release_identity.json")
CACHE = "backend/__pycache__"
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0")
RECOVERY = None
STAGE = None
PHASE = "preflight"


class Stop(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Stop(message)


def run(label, argv, cwd=APP, timeout=180, raw=False, diagnostic=False):
    try:
        r = subprocess.run(argv, cwd=cwd, env=ENV, stdin=subprocess.DEVNULL,
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise Stop(label + " could not complete") from None
    if diagnostic and RECOVERY is not None:
        (RECOVERY / (label + ".log")).write_bytes(r.stdout + b"\n" + r.stderr)
    # Never print Git argv, stdout or stderr: a remote may contain credentials.
    require(r.returncode == 0, label + " exit=" + str(r.returncode))
    return r.stdout if raw else r.stdout.decode("utf-8").strip()


def git(*args, cwd=APP, raw=False, timeout=180):
    return run("git-" + args[0], ["git", "--no-optional-locks", *args],
               cwd=cwd, raw=raw, timeout=timeout)


def original(label, code, cwd=APP):
    return run(label, [sys.executable, "-B", "-c", code], cwd=cwd,
               diagnostic=True)


def real_directory(path):
    require(path.is_dir() and not path.is_symlink()
            and path.absolute() == path.resolve(), "directory is not real: " + str(path))


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inventory(path, allow_links=False):
    records = {}

    def visit(item, relative):
        info = item.lstat()
        row = {"mode": stat.S_IMODE(info.st_mode)}
        if stat.S_ISLNK(info.st_mode):
            require(allow_links, "unexpected symlink: " + str(item))
            row.update(kind="symlink", target=os.readlink(item))
        elif stat.S_ISDIR(info.st_mode):
            row["kind"] = "directory"
            for child in sorted(item.iterdir(), key=lambda p: p.name):
                visit(child, child.name if relative == "." else relative + "/" + child.name)
        elif stat.S_ISREG(info.st_mode):
            row.update(kind="file", bytes=info.st_size, sha256=digest(item))
        else:
            raise Stop("unexpected filesystem object: " + str(item))
        records[relative] = row

    visit(path, ".")
    return records


def same(path, expected, allow_links=False):
    require(inventory(path, allow_links) == expected, "content or mode changed: " + str(path))


def copy_verified(source, destination, expected):
    require(not os.path.lexists(destination), "destination already exists: " + str(destination))
    same(source, expected)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)
    same(destination, expected)
    same(source, expected)


def move_verified(source, destination, expected):
    same(source, expected)
    require(not os.path.lexists(destination), "destination already exists: " + str(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    require(source.stat().st_dev == destination.parent.stat().st_dev,
            "atomic move would cross filesystems")
    source.rename(destination)
    same(destination, expected)


def checkpoint(phase, **fields):
    global PHASE
    PHASE = phase
    if RECOVERY is not None:
        record = dict(phase=phase, app=str(APP), candidate=str(CANDIDATE),
                      backup=str(RECOVERY), stage=str(STAGE), **fields)
        temp = RECOVERY / "state.json.tmp"
        temp.write_text(json.dumps(record, indent=2) + "\n")
        temp.replace(RECOVERY / "state.json")
    print("STAGE", phase, flush=True)


def inactive():
    code = ("import json; from scripts import production_release_guard as g; "
            "s=g.status(); print(json.dumps({'active':s.get('active'),"
            "'incomplete':s.get('incomplete_lease_directory',False)})); "
            "assert s == {'active':False}, 'original shared guard is not inactive'")
    print("ORIGINAL_GUARD", original("guard-status", code), flush=True)


def shared(head):
    inactive()
    require(git("rev-parse", "HEAD") == head, "shared HEAD changed")
    require(git("branch", "--show-current") == BRANCH, "wrong shared branch")
    require(not git("status", "--porcelain", "--untracked-files=no"),
            "tracked shared source is dirty")
    tip = git("ls-remote", "--exit-code", "origin", "refs/heads/" + BRANCH)
    require(tip.split() == [M, "refs/heads/" + BRANCH], "remote Production changed")


def unrelated():
    result = {}
    for raw in git("ls-files", "--others", "--exclude-standard", "-z", raw=True).split(b"\0"):
        if raw:
            name = os.fsdecode(raw)
            require(not Path(name).is_absolute() and ".." not in Path(name).parts,
                    "invalid untracked path")
            result[name] = inventory(APP / name, allow_links=True)
    return result


def sidecars():
    return {str(p.relative_to(APP)): inventory(p, allow_links=True)
            for p in (APP / ".env", APP / "backend/.env", APP / "frontend/.env")
            if os.path.lexists(p)}


def dependency_root():
    p = APP / "frontend/node_modules"
    s = p.lstat()
    return (s.st_dev, s.st_ino, s.st_mode, os.readlink(p) if p.is_symlink() else None)


def cache_shape(path):
    real_directory(path)
    require(all(p.is_file() and not p.is_symlink() and p.suffix == ".pyc"
                for p in path.iterdir()), "cache contains unexpected entries")


def validate_outputs(cwd, label):
    code = f"""
import json
from pathlib import Path
from scripts import emergent_deployment_adapter as a
from frontend_build_identity import read_frontend_build_identity, read_frontend_reproducibility_proof
i=a.load_release_intent()
a.verify_release_intent_git(i)
assert i['source_git_sha']=={A!r} and i['source_base_git_sha']=={J!r}
assert i['runtime_identity']['release_id']=={RID!r}
f=read_frontend_build_identity(expected_git_sha={A!r},require_git_source=True)
p=read_frontend_reproducibility_proof(frontend_build=f)
assert f==i['frontend_build'] and p==i['frontend_reproducibility']
assert f['artifact_tree_sha256']=={ARTIFACT!r}
assert json.loads(Path('backend/release_identity.json').read_text())==i['runtime_identity']
print(json.dumps({{'source':i['source_git_sha'],'release_id':{RID!r},'artifact_tree':{ARTIFACT!r},'verified':True}}))
"""
    print(label.upper(), original(label, code, cwd), flush=True)


def main():
    global RECOVERY, STAGE
    for name in ("GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "GIT_DIR",
                 "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CONFIG_PARAMETERS",
                 "GIT_CONFIG_COUNT", "GIT_CONFIG", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
                 "GIT_TRACE", "GIT_TRACE_PACKET", "GIT_TRACE_CURL", "GIT_CURL_VERBOSE",
                 "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF"):
        require(not os.environ.get(name), "unexpected environment override: " + name)
    for path in (APP, APP / ".git", APP / "frontend", APP / "backend",
                 APP / "frontend/.release", CANDIDATE, CANDIDATE / ".git"):
        real_directory(path)
    shared(J)
    require(git("rev-parse", "HEAD", cwd=CANDIDATE) in (B, M),
            "candidate HEAD is neither reviewed B nor its exact merge M")
    require(git("rev-parse", "HEAD^{tree}", cwd=CANDIDATE) == TREE, "candidate tree changed")
    require(not git("status", "--porcelain", "--untracked-files=no", cwd=CANDIDATE),
            "candidate source is dirty")
    require(not os.path.lexists(CANDIDATE / ".git/mezan-production-release.lock"),
            "candidate has an unexpected lease directory")
    remote = git("remote", "get-url", "origin")
    git("fetch", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance",
        remote, M, cwd=CANDIDATE, timeout=300)
    del remote
    require(git("rev-parse", M + "^{tree}", cwd=CANDIDATE) == TREE, "M tree differs from B")
    require(git("rev-list", "--parents", "-n", "1", M, cwd=CANDIDATE).split() == [M, J, B],
            "M is not the reviewed ordinary merge")
    # Advertise M as HEAD for the later local fetch; its tree equals frozen B.
    # This changes only the isolated clone's Git metadata, never its source bytes.
    git("checkout", "--detach", M, cwd=CANDIDATE)
    require(not git("status", "--porcelain", "--untracked-files=no", cwd=CANDIDATE),
            "candidate changed while selecting tree-equivalent M")
    validate_outputs(CANDIDATE, "candidate-proof")
    require(not git("status", "--porcelain=v1", "--untracked-files=normal", "--", "frontend"),
            "shared frontend has untracked or changed inputs")
    before, settings, dependencies = unrelated(), sidecars(), dependency_root()
    require(not git("ls-files", "--", *OUTPUTS, CACHE), "generated paths unexpectedly tracked")
    cache_shape(APP / CACHE)
    git("check-ignore", "-q", "--", CACHE)
    old = {p: inventory(APP / p) for p in (*OUTPUTS, CACHE)}
    new = {p: inventory(CANDIDATE / p) for p in OUTPUTS}
    needed = sum(r.get("bytes", 0) + 4096 for rows in new.values() for r in rows.values())
    backup_bytes = sum(r.get("bytes", 0) + 4096 for rows in old.values() for r in rows.values())
    require(shutil.disk_usage(APP / ".git").free > needed + 20 * 1024**2,
            "insufficient /app space for staged artifacts plus 20 MiB reserve")
    require(shutil.disk_usage("/tmp").free > backup_bytes + 64 * 1024**2,
            "insufficient /tmp space for verified backups")
    RECOVERY = Path(tempfile.mkdtemp(prefix="mezan-pr2069-preserved-", dir="/tmp"))
    STAGE = Path(tempfile.mkdtemp(prefix="pr2069-stage-", dir=APP / ".git"))
    print("BACKUP_DIRECTORY", str(RECOVERY), flush=True)
    print("RETAINED_LOCAL_DIRECTORY", str(STAGE), flush=True)
    (RECOVERY / "manifest.json").write_text(json.dumps(
        {"old": old, "new": new, "untracked": before, "sidecars": settings}, indent=2) + "\n")
    checkpoint("copying-verified-backups", untracked_count=len(before))
    for p in old:
        copy_verified(APP / p, RECOVERY / "old" / p, old[p])
    for p in OUTPUTS:
        copy_verified(CANDIDATE / p, STAGE / "new" / p, new[p])
    shared(J)
    require(unrelated() == before and sidecars() == settings and dependency_root() == dependencies,
            "unrelated files changed before quarantine")
    checkpoint("quarantining-verified-cache")
    move_verified(APP / CACHE, STAGE / "retired" / CACHE, old[CACHE])
    print("OLD_J_SOURCE_PROOF", original("old-j-source-proof",
        "import json; from scripts import emergent_deployment_adapter as a; "
        "i=a.load_release_intent(); a.verify_release_intent_git(i); "
        "print(json.dumps({'source':i['source_git_sha'],'verified':True}))"), flush=True)
    shared(J)
    checkpoint("fetching-local-merge")
    git("fetch", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance", str(CANDIDATE), M)
    require(git("rev-parse", M + "^{tree}") == TREE, "local merge tree changed")
    require(unrelated() == before and sidecars() == settings, "unrelated files changed before FF")
    checkpoint("fast-forwarding-shared-source")
    run("git-fast-forward", ["git", "--no-optional-locks",
        "-c", "maintenance.auto=false", "-c", "gc.auto=0", "merge", "--ff-only", M])
    require(git("rev-parse", "HEAD") == M and git("rev-parse", "HEAD^{tree}") == TREE,
            "shared fast-forward did not match reviewed M")
    shared(M)
    for p in OUTPUTS:
        checkpoint("replacing-output", path=p)
        same(RECOVERY / "old" / p, old[p])
        move_verified(APP / p, STAGE / "retired" / p, old[p])
        move_verified(STAGE / "new" / p, APP / p, new[p])
    checkpoint("validating-shared-artifacts")
    validate_outputs(APP, "shared-proof")
    run("original-frontend-verifier", [sys.executable, "-B", "scripts/verify_frontend_build.py",
        "--expected-git-sha", A, "--reviewed-intent-v5"], diagnostic=True)
    require(unrelated() == before and sidecars() == settings and dependency_root() == dependencies,
            "unrelated files, settings, or dependency root changed")
    require(not os.path.lexists(APP / CACHE), "shared bytecode cache reappeared")
    for p in old:
        same(RECOVERY / "old" / p, old[p])
        same(STAGE / "retired" / p, old[p])
    shared(M)
    result = {"staged": True, "deployment_git_sha": M, "source_git_sha": A,
              "release_id": RID, "artifact_tree": ARTIFACT,
              "unrelated_untracked_files_preserved": len(before), "guard_active": False,
              "prepare_started": False, "published": False,
              "backup_directory": str(RECOVERY), "retained_local_directory": str(STAGE)}
    (RECOVERY / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    checkpoint("ready-for-root-review")
    print("STAGING_RESULT", json.dumps(result), flush=True)
    print("DONE: staged and verified only; no prepare, prepublish or publication", flush=True)


def self_test():
    with tempfile.TemporaryDirectory(prefix="pr2069-stage-helper-test-") as directory:
        root = Path(directory)
        old = root / "old"
        old.mkdir()
        (old / "asset").write_bytes(b"old-reviewed-bytes")
        (old / "asset").chmod(0o640)
        expected = inventory(old)
        copy_verified(old, root / "backup", expected)
        (old / "asset").write_bytes(b"changed-after-backup")
        try:
            move_verified(old, root / "retired", expected)
        except Stop:
            pass
        else:
            raise AssertionError("changed source was moved")
        assert old.exists() and not (root / "retired").exists()
        (old / "asset").write_bytes(b"old-reviewed-bytes")
        move_verified(old, root / "retired", expected)
        same(root / "backup", expected)
        same(root / "retired", expected)
        fresh = root / "fresh"
        fresh.mkdir()
        (fresh / "asset").write_bytes(b"new-governed-bytes")
        (fresh / "asset").chmod(0o644)
        fresh_expected = inventory(fresh)
        copy_verified(fresh, root / "staged", fresh_expected)
        move_verified(root / "staged", old, fresh_expected)
        assert (old / "asset").read_bytes() == b"new-governed-bytes"
        assert (root / "retired/asset").read_bytes() == b"old-reviewed-bytes"
        assert stat.S_IMODE((root / "retired/asset").stat().st_mode) == 0o640
        try:
            move_verified(fresh, old, fresh_expected)
        except Stop:
            pass
        else:
            raise AssertionError("existing destination was replaced")
        assert fresh.exists() and (old / "asset").read_bytes() == b"new-governed-bytes"
        (root / "link").symlink_to(root / "retired")
        try:
            inventory(root / "link")
        except Stop:
            pass
        else:
            raise AssertionError("symlink was followed")
        cache = root / "__pycache__"
        cache.mkdir()
        (cache / "known.cpython-311.pyc").write_bytes(b"preserved-cache")
        cache_shape(cache)
        (cache / "unexpected.txt").write_text("preserve me")
        try:
            cache_shape(cache)
        except Stop:
            pass
        else:
            raise AssertionError("unexpected cache member accepted")
        print("SELF_TEST_PASS: backup bytes/modes, staged replacement, retained rollback, change refusal, destination refusal, symlink refusal, cache refusal")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--self-test"]:
            self_test()
        else:
            require(not sys.argv[1:], "unsupported arguments")
            main()
    except (Stop, OSError, ValueError) as error:
        print("STOP:", str(error), flush=True)
        print("LAST_PHASE", PHASE, "BACKUP", str(RECOVERY), "RETAINED", str(STAGE), flush=True)
        print("No automatic rollback, cleanup, lease, or publication was attempted.", flush=True)
        sys.exit(1)
