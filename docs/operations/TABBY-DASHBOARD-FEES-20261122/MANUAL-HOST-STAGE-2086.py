#!/usr/bin/env python3
"""Stage the already-rehearsed PR2086 artifacts. Never prepare or publish."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

APP = Path("/app")
CANDIDATE = None
J = "c2d244a3349013c06f3fb7824070bfa12bd0bfe0"
A = "1725672eeccb1272f183c376b9267e363a028325"
B = '104c6b45cd646418a719496fb8c02ee61ddf5483'
D = '76efd2c3aa1563939ee3e27a9ae03f27fc1a3d43'
TREE = '72659c9adcd5fcf3880c4ef4332245a65b620fe3'
RID = 'rg5-be6cf2196ab95412377fd5280c5ff7b6fd3204dd20dd74825b43d85e0fe77d13'
ARTIFACT = '9796b20cca6dd4809bcf27ff4e5cf4817b59ecc4005763c70304064850483827'
ACTOR = "codex-tabby-weekly-fees-2086-20261123"
BRANCH = "hotfix/prod-snap-meta-final"
OUTPUTS = ("frontend/build", "frontend/.release/reproducible-build.json",
           "backend/release_identity.json")
CACHE = "backend/__pycache__"
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0")
RECOVERY = None
STAGE = None
PHASE = "preflight"
LOCKED_GUARD = None


class Stop(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Stop(message)


def configured():
    require(all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value)
                for value in (J, A, B, D, TREE)),
            "disabled release bundle; final commit/tree pins are not configured")
    require(isinstance(RID, str) and re.fullmatch(r"rg5-[0-9a-f]{64}", RID),
            "disabled release bundle; final release identity is not configured")
    require(isinstance(ARTIFACT, str) and re.fullmatch(r"[0-9a-f]{64}", ARTIFACT),
            "disabled release bundle; final artifact pin is not configured")


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


def owned_directory(path):
    real_directory(path)
    info = path.stat()
    require(info.st_uid == os.geteuid() and not info.st_mode & 0o077,
            "directory is not private and owned: " + str(path))


def read_receipt(path):
    """Locate this launcher's result; all source/artifact proof is checked anew."""
    owned_directory(path.parent)
    require(path.parent.parent == Path("/tmp")
            and path.parent.name.startswith("mezan-pr2086-deploy-")
            and path.name == "rehearsal-receipt.json",
            "receipt is outside this launcher's private directory")
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and not path.is_symlink()
            and path.absolute() == path.resolve() and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o400 and info.st_size <= 8192,
            "receipt is not a bounded private immutable regular file")
    record = json.loads(path.read_text())
    expected = {"kind": "mezan_pr2086_rehearsal_v1", "actor": ACTOR,
                "base": J, "source": A, "intent_commit": B, "deployment": D,
                "tree": TREE, "release_id": RID, "artifact_tree": ARTIFACT}
    require(isinstance(record, dict) and set(record) == {*expected, "directory", "candidate", "log_sha256"}
            and all(record.get(key) == value for key, value in expected.items()),
            "receipt does not identify this pinned release")
    require(isinstance(record["directory"], str) and isinstance(record["candidate"], str),
            "receipt paths are invalid")
    work, candidate = Path(record["directory"]), Path(record["candidate"])
    owned_directory(work)
    require(work.parent == Path("/tmp") and work.name.startswith("mezan-pr2086-rehearsal-")
            and candidate == work / "repo", "receipt candidate path is not the isolated rehearsal")
    real_directory(candidate)
    log = work / "adapter.log"
    require(log.is_file() and not log.is_symlink()
            and isinstance(record["log_sha256"], str)
            and re.fullmatch(r"[0-9a-f]{64}", record["log_sha256"])
            and digest(log) == record["log_sha256"], "retained rehearsal log changed")
    return candidate


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
    if LOCKED_GUARD is not None:
        # status() takes this same non-reentrant operation lock. While holding
        # it, require its exact inactive condition directly, including refusal
        # of an incomplete directory or any broken symlink at the lease paths.
        require(not os.path.lexists(LOCKED_GUARD.LEASE_PATH)
                and not os.path.lexists(LOCKED_GUARD.LEASE_DIR),
                "original shared guard has an existing or incomplete lease")
        print("ORIGINAL_GUARD", json.dumps({"active": False, "incomplete": False}), flush=True)
        return
    code = ("import json; from scripts import production_release_guard as g; "
            "s=g.status(); print(json.dumps({'active':s.get('active'),"
            "'incomplete':s.get('incomplete_lease_directory',False)})); "
            "assert s == {'active':False}, 'original shared guard is not inactive'")
    print("ORIGINAL_GUARD", original("guard-status", code), flush=True)


@contextmanager
def shared_operation():
    """Coordinate staging with the unchanged original prepare/verify mutex."""
    global LOCKED_GUARD
    require(LOCKED_GUARD is None, "nested shared operation refused")
    sys.path.insert(0, str(APP))
    from scripts import production_release_guard as guard
    require(guard.REPO_ROOT.resolve() == APP
            and Path(guard.__file__).resolve() == APP / "scripts/production_release_guard.py"
            and guard.PROTOCOL_VERSION == 5, "original /app protocol-v5 guard required")
    with guard._release_operation_lock():
        LOCKED_GUARD = guard
        try:
            inactive()
            yield
        finally:
            LOCKED_GUARD = None


def shared(head):
    inactive()
    require(git("rev-parse", "HEAD") == head, "shared HEAD changed")
    require(git("branch", "--show-current") == BRANCH, "wrong shared branch")
    require(not git("status", "--porcelain", "--untracked-files=no"),
            "tracked shared source is dirty")
    tip = git("ls-remote", "--exit-code", "origin", "refs/heads/" + BRANCH)
    require(tip.split() == [D, "refs/heads/" + BRANCH], "remote Production changed")


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


def existing_cache(path):
    if not os.path.lexists(path):
        return False
    cache_shape(path)
    return True


def rehearsal_proof(path):
    require(path.is_file() and not path.is_symlink(),
            "original rehearsal log is missing or not a regular file")
    text = path.read_text()
    proofs = []
    for match in re.finditer(r"(?m)^\{", text):
        try:
            value, _ = json.JSONDecoder().raw_decode(text[match.start():])
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("built") is True:
            proofs.append(value)
    require(len(proofs) == 1, "original adapter success proof is missing or ambiguous")
    proof = proofs[0]
    boundary = proof.get("package_boundary", {})
    require(
        proof.get("protocol_version") == 5
        and proof.get("source_git_sha") == A
        and proof.get("release_id") == RID
        and proof.get("frontend_artifact_tree_sha256") == ARTIFACT
        and boundary.get("kind") == "mezan_release_package_boundary_proof_v1"
        and boundary.get("source_git_sha") == A
        and boundary.get("release_id") == RID
        and boundary.get("frontend", {}).get("isolated_runtime_artifact_verified") is True
        and boundary.get("frontend", {}).get("content_type_contract") == "application/json"
        and boundary.get("frontend", {}).get("artifact_tree_sha256") == ARTIFACT
        and boundary.get("backend", {}).get("isolated_verified") is True
        and boundary.get("backend", {}).get("health_verified") is True
        and boundary.get("backend", {}).get("sibling_frontend_present") is False
        and boundary.get("backend", {}).get("git_directory_present") is False,
        "original adapter proof does not match this release")
    return proof


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


def validate_package_proof(cwd, proof):
    # Re-run the original isolated package verifier, without a build or lease,
    # and compare its entire proof, including membership hashes/counts and the
    # exact Frontend metadata and Backend identity records. Receipt data alone
    # cannot authorize staging generated outputs.
    boundary = json.dumps(proof["package_boundary"], sort_keys=True)
    code = (
        "import json; from scripts import emergent_deployment_adapter as a; "
        "i=a.load_release_intent(); a.verify_release_intent_git(i); "
        "p=a.verify_package_boundaries(i); "
        f"assert p == json.loads({boundary!r}), 'retained package proof changed'; "
        "print(json.dumps({'full_original_package_proof_matches':True}))"
    )
    print("ORIGINAL_PACKAGE_PROOF", original("candidate-package-proof", code, cwd), flush=True)


def main(receipt):
    global RECOVERY, STAGE, CANDIDATE
    configured()
    for name in ("GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE", "GIT_DIR",
                 "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_CONFIG_PARAMETERS",
                 "GIT_CONFIG_COUNT", "GIT_CONFIG", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL",
                 "GIT_TRACE", "GIT_TRACE_PACKET", "GIT_TRACE_CURL", "GIT_CURL_VERBOSE",
                 "GIT_TRACE2_EVENT", "GIT_TRACE2_PERF"):
        require(not os.environ.get(name), "unexpected environment override: " + name)
    CANDIDATE = read_receipt(receipt)
    receipt_record = inventory(receipt)
    for path in (APP, APP / ".git", APP / "frontend", APP / "backend",
                 APP / "frontend/.release", CANDIDATE, CANDIDATE / ".git"):
        real_directory(path)
    shared(J)
    require(git("rev-parse", "HEAD", cwd=CANDIDATE) in (B, D),
            "candidate HEAD is neither reviewed B nor its exact merge D")
    require(git("rev-parse", "HEAD^{tree}", cwd=CANDIDATE) == TREE, "candidate tree changed")
    require(not git("status", "--porcelain", "--untracked-files=no", cwd=CANDIDATE),
            "candidate source is dirty")
    require(not os.path.lexists(CANDIDATE / ".git/mezan-production-release.lock"),
            "candidate has an unexpected lease directory")
    remote = git("remote", "get-url", "origin")
    git("fetch", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance",
        remote, D, cwd=CANDIDATE, timeout=300)
    del remote
    require(git("rev-parse", D + "^{tree}", cwd=CANDIDATE) == TREE, "D tree differs from B")
    require(git("rev-list", "--parents", "-n", "1", D, cwd=CANDIDATE).split() == [D, J, B],
            "D is not the reviewed ordinary merge")
    # Advertise D as HEAD for the later local fetch; its tree equals frozen B.
    # This changes only the isolated clone's Git metadata, never its source bytes.
    git("checkout", "--detach", D, cwd=CANDIDATE)
    require(not git("status", "--porcelain", "--untracked-files=no", cwd=CANDIDATE),
            "candidate changed while selecting tree-equivalent D")
    validate_outputs(CANDIDATE, "candidate-proof")
    rehearsal_log = CANDIDATE.parent / "adapter.log"
    rehearsal_record = inventory(rehearsal_log)
    proof = rehearsal_proof(rehearsal_log)
    require(proof["package_boundary"]["backend"].get("identity", {}).get("sha256")
            == digest(CANDIDATE / "backend/release_identity.json"),
            "rehearsal identity hash differs from retained candidate")
    validate_package_proof(CANDIDATE, proof)
    same(rehearsal_log, rehearsal_record)
    same(receipt, receipt_record)
    require(not git("status", "--porcelain=v1", "--untracked-files=normal", "--", "frontend"),
            "shared frontend has untracked or changed inputs")
    before, settings, dependencies = unrelated(), sidecars(), dependency_root()
    require(not git("ls-files", "--", *OUTPUTS, CACHE), "generated paths unexpectedly tracked")
    cache_present = existing_cache(APP / CACHE)
    if cache_present:
        git("check-ignore", "-q", "--", CACHE)
    old = {p: inventory(APP / p) for p in (*OUTPUTS, *((CACHE,) if cache_present else ()))}
    new = {p: inventory(CANDIDATE / p) for p in OUTPUTS}
    needed = sum(r.get("bytes", 0) + 4096 for rows in new.values() for r in rows.values())
    backup_bytes = sum(r.get("bytes", 0) + 4096 for rows in old.values() for r in rows.values())
    require(shutil.disk_usage(APP / ".git").free > needed + 20 * 1024**2,
            "insufficient /app space for staged artifacts plus 20 MiB reserve")
    require(shutil.disk_usage("/tmp").free > backup_bytes + 64 * 1024**2,
            "insufficient /tmp space for verified backups")
    # The isolated build and package proof are complete before this mutex.
    # Keep prepare() excluded until every shared mutation and final proof ends.
    with shared_operation():
        shared(J)
        RECOVERY = Path(tempfile.mkdtemp(prefix="mezan-pr2086-preserved-", dir="/tmp"))
        STAGE = Path(tempfile.mkdtemp(prefix="pr2086-stage-", dir=APP / ".git"))
        print("BACKUP_DIRECTORY", str(RECOVERY), flush=True)
        print("RETAINED_LOCAL_DIRECTORY", str(STAGE), flush=True)
        (RECOVERY / "manifest.json").write_text(json.dumps(
            {"old": old, "new": new, "untracked": before, "sidecars": settings,
             "cache_was_present": cache_present,
             "rehearsal_log": {"path": str(rehearsal_log), "inventory": rehearsal_record},
             "rehearsal_receipt": {"path": str(receipt), "inventory": receipt_record}}, indent=2) + "\n")
        checkpoint("copying-verified-backups", untracked_count=len(before))
        for p in old:
            copy_verified(APP / p, RECOVERY / "old" / p, old[p])
        for p in OUTPUTS:
            copy_verified(CANDIDATE / p, STAGE / "new" / p, new[p])
        shared(J)
        require(unrelated() == before and sidecars() == settings and dependency_root() == dependencies,
                "unrelated files changed before quarantine")
        same(rehearsal_log, rehearsal_record)
        same(receipt, receipt_record)
        require(os.path.lexists(APP / CACHE) == cache_present, "cache presence changed before staging")
        checkpoint("quarantining-verified-cache" if cache_present else "confirming-cache-absence")
        if cache_present:
            move_verified(APP / CACHE, STAGE / "retired" / CACHE, old[CACHE])
        print("OLD_J_SOURCE_PROOF", original("old-j-source-proof",
            "import json; from scripts import emergent_deployment_adapter as a; "
            "i=a.load_release_intent(); a.verify_release_intent_git(i); "
            "print(json.dumps({'source':i['source_git_sha'],'verified':True}))"), flush=True)
        shared(J)
        checkpoint("fetching-local-merge")
        git("fetch", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance", str(CANDIDATE), D)
        require(git("rev-parse", D + "^{tree}") == TREE, "local merge tree changed")
        require(unrelated() == before and sidecars() == settings, "unrelated files changed before FF")
        checkpoint("fast-forwarding-shared-source")
        run("git-fast-forward", ["git", "--no-optional-locks",
            "-c", "maintenance.auto=false", "-c", "gc.auto=0", "merge", "--ff-only", "--no-overwrite-ignore", D])
        require(git("rev-parse", "HEAD") == D and git("rev-parse", "HEAD^{tree}") == TREE,
                "shared fast-forward did not match reviewed D")
        shared(D)
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
        same(rehearsal_log, rehearsal_record)
        same(receipt, receipt_record)
        shared(D)
        result = {"staged": True, "deployment_git_sha": D, "source_git_sha": A,
                  "release_id": RID, "artifact_tree": ARTIFACT,
                  "unrelated_untracked_files_preserved": len(before), "guard_active": False,
                  "bytecode_cache_was_present": cache_present,
                  "rehearsal_log_sha256": rehearsal_record["."]["sha256"],
                  "prepare_started": False, "published": False,
                  "backup_directory": str(RECOVERY), "retained_local_directory": str(STAGE)}
        (RECOVERY / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        checkpoint("staging-verified")
        print("STAGING_RESULT", json.dumps(result), flush=True)
        print("DONE: staged and verified only; no prepare, prepublish or publication", flush=True)


def self_test():
    with tempfile.TemporaryDirectory(prefix="pr2086-stage-helper-test-") as directory:
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
        absent = root / "absent-cache"
        assert existing_cache(absent) is False and not os.path.lexists(absent)
        cache = root / "__pycache__"
        cache.mkdir()
        (cache / "known.cpython-311.pyc").write_bytes(b"preserved-cache")
        assert existing_cache(cache) is True
        (cache / "unexpected.txt").write_text("preserve me")
        try:
            existing_cache(cache)
        except Stop:
            pass
        else:
            raise AssertionError("unexpected cache member accepted")
        valid = {
            "built": True, "protocol_version": 5, "source_git_sha": A, "release_id": RID,
            "frontend_artifact_tree_sha256": ARTIFACT,
            "package_boundary": {
                "kind": "mezan_release_package_boundary_proof_v1",
                "source_git_sha": A, "release_id": RID,
                "frontend": {"isolated_runtime_artifact_verified": True,
                             "content_type_contract": "application/json", "artifact_tree_sha256": ARTIFACT},
                "backend": {"isolated_verified": True, "health_verified": True,
                            "sibling_frontend_present": False, "git_directory_present": False},
            },
        }
        log = root / "adapter.log"
        log.write_text("original adapter output\n" + json.dumps(valid) + "\n")
        assert rehearsal_proof(log) == valid
        wrong = json.loads(json.dumps(valid))
        wrong["source_git_sha"] = "0" * 40
        for invalid in (json.dumps(wrong), json.dumps(valid) + "\n" + json.dumps(valid), "no proof"):
            log.write_text(invalid + "\n")
            try:
                rehearsal_proof(log)
            except Stop:
                pass
            else:
                raise AssertionError("invalid or ambiguous rehearsal proof accepted")
        print("SELF_TEST_PASS: backup bytes/modes, staged replacement, retained rollback, change refusal, destination refusal, symlink refusal, absent-cache preservation, present-cache refusal, rehearsal proof binding")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--self-test"]:
            self_test()
        else:
            configured()
            parser = argparse.ArgumentParser()
            parser.add_argument("--receipt", type=Path, required=True)
            main(parser.parse_args().receipt)
    except (Stop, OSError, ValueError) as error:
        print("STOP:", str(error), flush=True)
        print("LAST_PHASE", PHASE, "BACKUP", str(RECOVERY), "RETAINED", str(STAGE), flush=True)
        print("No automatic rollback, cleanup, lease, or publication was attempted.", flush=True)
        sys.exit(1)
