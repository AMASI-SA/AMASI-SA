#!/usr/bin/env python3
"""Promote already-reviewed WhatsApp one-way V5 artifacts from isolated clone to /app.
Does not deploy, restart services, clean node_modules, create a release lease,
modify tracked source, run Git reset/clean, or contact customer systems.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid

APP = Path("/app")
REHEARSAL = Path("/tmp/mezan-oneway-v5-rehearsal-0mQxq08G/repo")
BRANCH = "hotfix/prod-snap-meta-final"
DEPLOY_SHA = "b020f3e918915a5873366ddcaab1db0244ef8e26"
INTENT_SHA = "ac5b459ced939e499b963f5296457de168019323"
BASE_SHA = "53e52767a1bf2b4efd4ec796e8c59de9425c8839"
SOURCE_SHA = "55995c02c2bdcf4ec63fe37ea3c14983244952c5"
RELEASE_ID = "rg5-54047ecc26066c6cf00503af8e8097fafc3b22dbb963aaf5fbba8eaee23eec47"
RECOVERY_ROOT = Path("/root/mezan-v5-recovery")
RECORDS = (
    ("frontend/build", "directory"),
    ("frontend/.release", "directory"),
    ("backend/release_identity.json", "file"),
)

def stop(message: str) -> None:
    raise RuntimeError("V5_ARTIFACT_STOP: " + message)

def git(*args: str, cwd: Path = APP) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True,
                            stdout=subprocess.PIPE, text=True)
    return result.stdout.strip()

def git_raw(*args: str, cwd: Path = APP) -> bytes:
    return subprocess.check_output(["git", *args], cwd=cwd)

def command(*args: str, cwd: Path = APP, log_path: Path | None = None) -> None:
    if log_path is None:
        subprocess.run(args, cwd=cwd, check=True)
    else:
        with log_path.open("wb") as out:
            subprocess.run(args, cwd=cwd, check=True, stdout=out, stderr=subprocess.STDOUT)

def require_path(path: Path, kind: str) -> None:
    if path.is_symlink():
        stop("symlink at protected artifact path: " + str(path))
    if kind == "directory" and not path.is_dir():
        stop("directory missing: " + str(path))
    if kind == "file" and not path.is_file():
        stop("file missing: " + str(path))

def tree_hash(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    count = 0
    if path.is_file():
        paths = [path]
        base = path.parent
    else:
        paths = sorted(path.rglob("*"), key=lambda p: p.relative_to(path).as_posix())
        base = path
    for item in paths:
        # A standalone artifact is content-addressed, independent of its
        # temporary basename (e.g. .release_identity.json.v5-new-XXXX).
        # Directory entries retain their relative paths to detect tree drift.
        rel = "." if path.is_file() else item.relative_to(base).as_posix()
        mode = item.lstat().st_mode
        if stat.S_ISLNK(mode):
            stop("symlink found inside artifact tree: " + rel)
        if stat.S_ISDIR(mode):
            h.update(b"D" + rel.encode() + b"\0")
            continue
        if not stat.S_ISREG(mode):
            stop("nonregular artifact member: " + rel)
        h.update(b"F" + rel.encode() + b"\0")
        with item.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
        count += 1
    return h.hexdigest(), count

def safe_remove(path: Path) -> None:
    if not os.path.lexists(path):
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()

def ensure_app_and_clone() -> None:
    if not APP.is_dir() or not (APP / ".git").is_dir():
        stop("/app must be a real Git checkout")
    if not REHEARSAL.is_dir() or not (REHEARSAL / ".git").is_dir():
        stop("reviewed isolated build directory is missing")
    if str(REHEARSAL.resolve()).startswith(str(APP.resolve()) + "/"):
        stop("rehearsal cannot reside in /app")
    if git("branch", "--show-current") != BRANCH:
        stop("wrong /app branch")
    if git("rev-parse", "HEAD") != DEPLOY_SHA:
        stop("wrong /app HEAD")
    if git("status", "--porcelain", "--untracked-files=no"):
        stop("/app tracked source is dirty")
    if git("branch", "--show-current", cwd=REHEARSAL) != BRANCH:
        stop("rehearsal branch differs")
    if git("rev-parse", "HEAD", cwd=REHEARSAL) != DEPLOY_SHA:
        stop("rehearsal HEAD differs")
    if git("status", "--porcelain", "--untracked-files=no", cwd=REHEARSAL):
        stop("isolated build tracked source is dirty")
    for name in ("AMASI_WHATSAPP_CATALOG_ENABLED",
                 "AMASI_WHATSAPP_ORDER_STAGE_ENABLED",
                 "AMASI_WHATSAPP_ORDER_STAGE_ATTESTED_ENABLED"):
        if os.environ.get(name, "0") != "0":
            stop("WhatsApp feature must remain OFF: " + name)
    # Prevent mistaken reuse of stale or manually modified release structures.
    if git("rev-list", "--parents", "-n", "1", DEPLOY_SHA).split() != [
        DEPLOY_SHA, SOURCE_SHA, INTENT_SHA
    ]:
        stop("deployment merge parents differ from reviewed release")
    if git("rev-list", "--parents", "-n", "1", INTENT_SHA).split() != [
        INTENT_SHA, SOURCE_SHA
    ]:
        stop("intent-only commit has unexpected parent")
    if (git("diff", "--name-only", SOURCE_SHA, DEPLOY_SHA)
            != "release/release-intent-v5.json"):
        stop("deployment differs from source on files other than intent")
    origin = git("remote", "get-url", "origin")
    if origin not in (
        "https://github.com/AMASI-SA/AMASI-SA",
        "https://github.com/AMASI-SA/AMASI-SA.git",
        "git@github.com:AMASI-SA/AMASI-SA.git",
    ):
        stop("unexpected GitHub origin")
    git("fetch", "--no-tags", "origin", BRANCH)
    if git("rev-parse", "FETCH_HEAD") != DEPLOY_SHA:
        stop("GitHub production moved after rehearsal")
    status = json.loads(subprocess.check_output(
        ["python3", "-B", "scripts/production_release_guard.py", "status"],
        cwd=APP, text=True,
    ))
    if status.get("active") is not False or status.get("incomplete_lease_directory"):
        stop("production release lease is not clear")

def verify_isolated() -> None:
    intent = json.loads((REHEARSAL / "release/release-intent-v5.json").read_text())
    identity = json.loads((REHEARSAL / "backend/release_identity.json").read_text())
    if (intent.get("schema_version") != 2
            or intent.get("source_git_sha") != SOURCE_SHA
            or intent.get("source_base_git_sha") != BASE_SHA
            or intent.get("branch") != BRANCH
            or intent.get("runtime_identity") != identity
            or identity.get("release_id") != RELEASE_ID):
        stop("isolated artifacts not equal to reviewed V5 intent")
    if json.loads((APP / "release/release-intent-v5.json").read_text()) != intent:
        stop("isolated intent differs from /app exact reviewed intent")
    if git("rev-parse", SOURCE_SHA, cwd=REHEARSAL) != SOURCE_SHA:
        stop("isolated clone missing source commit")
    meta = json.loads((REHEARSAL / "frontend/build/build-meta.json").read_text())
    proof = json.loads((REHEARSAL / "frontend/.release/reproducible-build.json").read_text())
    if meta.get("git_sha") != SOURCE_SHA or proof.get("git_sha") != SOURCE_SHA:
        stop("isolated frontend metadata not bound to reviewed source")
    for rel, kind in RECORDS:
        require_path(APP / rel, kind)
        require_path(REHEARSAL / rel, kind)

def verify_local_proof(log_path: Path) -> None:
    command("python3", "-B", "scripts/verify_frontend_build.py",
            "--expected-git-sha", SOURCE_SHA, "--reviewed-intent-v5",
            log_path=log_path)
    # This call only reads the materialized identity and its reviewed source manifest.
    command("python3", "-B", "-c",
            "import json; from scripts.production_release_guard import _read_runtime_identity, _read_reviewed_runtime_identity; "
            "a=_read_runtime_identity(); b=_read_reviewed_runtime_identity(); "
            "assert a==b and a['release_id']=='" + RELEASE_ID + "'; "
            "print('V5_IDENTITY_BINDING_PASS')",
            log_path=log_path.with_name("identity-validation.log"))

def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("check", "promote"):
        stop("use check or promote")
    mode = sys.argv[1]
    lock_path = APP / ".git" / "mezan-v5-artifact-handoff.lock"
    with lock_path.open("a+b") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        ensure_app_and_clone()
        verify_isolated()
        # A possible stale staging artifact from an older release is not ours
        # to delete. Refuse rather than risk deleting a previously held file.
        known_orphan = APP / "backend/.release_identity.json.v5-new-92c617c56b20"
        if os.path.lexists(known_orphan):
            stop("legacy staging orphan requires separate manual review")
        for rel, kind in RECORDS:
            require_path(APP / rel, kind)
            require_path(REHEARSAL / rel, kind)
            # Detect symlinks and invalid members before touching /app outputs.
            tree_hash(APP / rel)
            tree_hash(REHEARSAL / rel)
        if mode == "check":
            print("V5_ONEWAY_HANDOFF_PRECHECK_PASS")
            print("DEPLOYMENT_SHA=" + DEPLOY_SHA)
            print("REHEARSAL_REPO=" + str(REHEARSAL))
            print("NO_APP_ARTIFACT_SWAP_NO_DEPLOY_NO_RESTART_NO_LEASE")
            return
        untracked = git_raw("ls-files", "--others", "--exclude-standard", "-z")
        untracked_count = sum(bool(p) for p in untracked.split(b"\0"))
        if untracked_count != 433:
            stop(f"untracked path count differs from approved inspection ({untracked_count})")
        marker = uuid.uuid4().hex[:12]
        recovery_root = RECOVERY_ROOT
        if recovery_root.is_symlink():
            stop("unsafe backup root symlink")
        recovery_root.mkdir(mode=0o700, exist_ok=True)
        backup = recovery_root / ("before-" + marker)
        backup.mkdir(mode=0o700)
        stage = {}
        originals = {}
        expected = {}
        swapped = []
        try:
            # Copy and byte-verify all current outputs *outside /app*.
            for rel, kind in RECORDS:
                old = APP / rel
                src = REHEARSAL / rel
                dest = backup / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                if kind == "directory":
                    shutil.copytree(old, dest, symlinks=True)
                else:
                    shutil.copy2(old, dest)
                before_digest = tree_hash(old)
                if before_digest != tree_hash(dest):
                    stop("backup digest does not match current artifact: " + rel)
                originals[rel] = before_digest
                expected[rel] = tree_hash(src)
            # Stage exact new outputs next to their destination, on same filesystem.
            for rel, kind in RECORDS:
                current = APP / rel
                src = REHEARSAL / rel
                new = current.parent / ("." + current.name + ".v5-new-" + marker)
                if os.path.lexists(new):
                    stop("refusing to replace existing staged artifact: " + str(new))
                # Record the destination BEFORE writing, so a copy/hash
                # failure also gets cleaned up (including standalone files).
                stage[rel] = new
                if kind == "directory":
                    shutil.copytree(src, new, symlinks=False)
                else:
                    shutil.copy2(src, new)
                if tree_hash(new) != expected[rel]:
                    stop("staged artifact digest mismatch: " + rel)
            if git("status", "--porcelain", "--untracked-files=no"):
                stop("tracked source changed during artifact staging")
            # Short final handoff. Previously served files remain backed up.
            for rel, kind in RECORDS:
                current = APP / rel
                prior = current.parent / ("." + current.name + ".v5-prior-" + marker)
                if os.path.lexists(prior):
                    stop("refusing to replace existing prior artifact")
                current.rename(prior)
                try:
                    stage[rel].rename(current)
                except Exception:
                    prior.rename(current)
                    raise
                swapped.append((rel, prior))
            # Backup verified: remove temporary old paths from /app BEFORE the
            # strict runtime manifest scan (otherwise they look like unreviewed files).
            for rel, prior in swapped:
                if tree_hash(prior) != originals[rel] or tree_hash(backup / rel) != originals[rel]:
                    stop("original output changed before verified cleanup: " + rel)
                safe_remove(prior)
            verify_local_proof(backup / "frontend-proof-validation.log")
            if git("rev-parse", "HEAD") != DEPLOY_SHA or git("status", "--porcelain", "--untracked-files=no"):
                stop("Git source changed during handoff")
            if git_raw("ls-files", "--others", "--exclude-standard", "-z") != untracked:
                stop("untracked path list changed after handoff")
            for rel, kind in RECORDS:
                if tree_hash(APP / rel) != expected[rel]:
                    stop("promoted artifact changed: " + rel)
                if tree_hash(backup / rel) != originals[rel]:
                    stop("backup artifact changed: " + rel)
            evidence = {"backup": str(backup), "reviewed_release_id": RELEASE_ID,
                        "source_sha": SOURCE_SHA, "deployment_sha": DEPLOY_SHA,
                        "original_sha256": {r: originals[r][0] for r, _ in RECORDS},
                        "promoted_sha256": {r: expected[r][0] for r, _ in RECORDS},
                        "untracked_paths_preserved": untracked_count}
            (backup / "handoff.json").write_text(json.dumps(evidence, indent=2))
            print("V5_ARTIFACT_HANDOFF_PASS")
            print("RELEASE_ID=" + RELEASE_ID)
            print("GIT_HEAD=" + DEPLOY_SHA)
            print("UNTRACKED_PRESERVED=" + str(untracked_count))
            print("ORIGINAL_ARTIFACTS_BACKUP=" + str(backup))
            print("LEASE_NOT_ACQUIRED")
            print("NO_REPUBLISH_NO_RUNTIME_RESTART")
        except Exception as exc:
            # Transactional rollback: never erase the externally verified backups.
            for rel, prior in reversed(swapped):
                current = APP / rel
                failed = current.parent / ("." + current.name + ".v5-failed-" + marker)
                restored_stage = current.parent / ("." + current.name + ".v5-restore-" + marker)
                try:
                    if os.path.lexists(prior):
                        if os.path.lexists(current):
                            current.rename(failed)
                        prior.rename(current)
                    else:
                        # Originals were already externally backed up and verified.
                        previous = backup / rel
                        require_path(previous, dict(RECORDS)[rel])
                        if restored_stage.exists() or restored_stage.is_symlink():
                            stop("unrecognized rollback staging path")
                        if dict(RECORDS)[rel] == "directory":
                            shutil.copytree(previous, restored_stage, symlinks=True)
                        else:
                            shutil.copy2(previous, restored_stage)
                        if tree_hash(restored_stage) != originals[rel]:
                            stop("rollback copy differs from original")
                        if os.path.lexists(current):
                            current.rename(failed)
                        restored_stage.rename(current)
                    if tree_hash(current) != originals[rel]:
                        stop("restoration failed hash check")
                    safe_remove(failed)
                except Exception as undo_error:
                    print("ROLLBACK_NEEDS_MANUAL_REVIEW", rel, undo_error, file=sys.stderr)
            for rel, staged in stage.items():
                if os.path.lexists(staged):
                    safe_remove(staged)
            print("V5_ARTIFACT_HANDOFF_REFUSED", str(exc)[:400], file=sys.stderr)
            print("ORIGINAL_BACKUP=" + str(backup), file=sys.stderr)
            raise SystemExit(2) from exc

if __name__ == "__main__":
    main()
