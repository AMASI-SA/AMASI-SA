"""Preserve the inspected local snapshot, restore reviewed checkout, then prepare."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path("/app")
BACKUP_PARENT = Path("/root")
BRANCH = "hotfix/prod-snap-meta-final"
LOCAL_HEAD = "3b1e47678c0a4f968bb56d2d21525c115d27b22f"
BASE = "2549f053f1ec272cd6e4fc371919775db2d02b6c"
DEPLOYMENT = "049fba51c13b902cd12808e03e87aca203649fb7"
MARKER = ".emergent/stripped_env_names"
HELPER_COMMIT = "8615a4e3ea64a37e953fff7d5604b26d182cd4ce"
HELPER_PATH = "docs/operations/ANDROID72-SERVER-20261106/prepare_cloud_copy_retry.py"
HELPER_SHA256 = "b698ec2ebb82541de59eda63e538b2d0897fe5cc20a1e701cf34b81b073c7a28"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(*args):
    subprocess.run(args, check=True)


def out(*args):
    return subprocess.check_output(args, text=True).strip()


def paths(*args):
    return [p for p in subprocess.check_output(args, text=True).split("\0") if p]


def inactive():
    state = json.loads(out(sys.executable, "-B", "scripts/production_release_guard.py", "status"))
    require(state.get("active") is False, "Another release is active; leave it unchanged")


def stop_preview():
    status = subprocess.run(["supervisorctl", "status", "backend"], capture_output=True, text=True)
    print(status.stdout.strip(), flush=True)
    fields = status.stdout.split()
    require(len(fields) >= 2 and fields[0] == "backend", "Inspect Preview service state")
    if fields[1] in {"RUNNING", "STARTING", "BACKOFF"}:
        run("supervisorctl", "stop", "backend")
    else:
        require(fields[1] in {"STOPPED", "EXITED", "FATAL"}, "Unexpected Preview state")


def allowed_artifact(path):
    return bool(path in {MARKER, "mezan-1711-intent-v5.json",
                         "prepare_mezan_v5_operational_photo_courier.sh"}
                or re.fullmatch(r"\.emergent/testing/browser_runs/[A-Za-z0-9_-]+/(result|state)\.json", path)
                or re.fullmatch(r"deployer-agent-docs/RCA_[0-9a-f-]+\.MD", path))


def safe_file(path, root):
    require(path.is_file() and not path.is_symlink() and
            path.resolve(strict=True).is_relative_to(root), "Unsafe preserved file path")
    for parent in path.parents:
        if parent == root:
            break
        require(not parent.is_symlink(), "Symlinked preservation directory")


def normalize():
    root = ROOT.resolve(strict=True)
    os.chdir(root)
    inactive()
    require(out("git", "branch", "--show-current") == BRANCH, "Wrong branch")
    require(out("git", "rev-parse", "HEAD") == LOCAL_HEAD, "Workspace HEAD changed; inspect it")
    require(not paths("git", "diff", "--cached", "--name-only", "-z"), "Staged changes need inspection")
    require(set(paths("git", "diff", "--name-only", "-z")) <= {MARKER},
            "Unexpected working-tree changes; preserve and inspect them first")
    run("git", "fetch", "origin", BRANCH)
    remote = "origin/" + BRANCH
    require(out("git", "rev-parse", remote) == DEPLOYMENT, "Production source advanced; inspect it")
    require(out("git", "merge-base", LOCAL_HEAD, DEPLOYMENT) == BASE, "Unexpected common base")
    added = paths("git", "diff", "--diff-filter=A", "--name-only", "-z", BASE, LOCAL_HEAD)
    modified = paths("git", "diff", "--diff-filter=M", "--name-only", "-z", BASE, LOCAL_HEAD)
    changed = paths("git", "diff", "--name-only", "-z", BASE, LOCAL_HEAD)
    require(set(modified) == {".gitignore"} and set(changed) == set(added) | {".gitignore"}
            and added and all(allowed_artifact(p) for p in added),
            "Unexpected local source changes; nothing will be switched")
    run("git", "diff", "--quiet", BASE, DEPLOYMENT, "--", ".gitignore")

    target_files = set(paths("git", "ls-tree", "-r", "--name-only", "-z", DEPLOYMENT))
    old_files = set(paths("git", "ls-tree", "-r", "--name-only", "-z", LOCAL_HEAD))
    for name in added:
        require(name not in target_files and not any(p.as_posix() in target_files
                for p in Path(name).parents if p.as_posix() != "."),
                "A preserved artifact conflicts with reviewed source")
    collisions = [p for p in target_files - old_files if os.path.lexists(root / p)]
    require(not collisions, "Untracked files conflict with reviewed source: " + ", ".join(sorted(collisions)))
    for name in [*added, ".gitignore"]:
        safe_file(root / name, root)

    inactive()
    stop_preview()
    saved = Path(tempfile.mkdtemp(prefix="android72-preview-preserved-", dir=BACKUP_PARENT))
    require(not saved.resolve().is_relative_to(root), "Backup must be outside deployment context")
    saved.chmod(0o700)
    archive = "codex/preview-preserved-" + LOCAL_HEAD[:8] + "-" + uuid.uuid4().hex[:8]
    hashes = {}
    for name in [*added, ".gitignore"]:
        target = saved / "files" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    (saved / "working-tree.patch").write_bytes(subprocess.check_output(["git", "diff", "--binary"]))
    run("git", "bundle", "create", str(saved / "local-head.bundle"), BASE + "..HEAD")
    report = {"original_head": LOCAL_HEAD, "reviewed_head": DEPLOYMENT,
              "archive_branch": archive, "backup_path": str(saved),
              "preserved_artifact_count": len(added), "file_sha256": hashes,
              "phase": "backup_complete"}
    journal = saved / "preservation.json"
    journal.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("Private snapshot backup:", saved, flush=True)
    for name, digest in hashes.items():
        require(hashlib.sha256((root / name).read_bytes()).hexdigest() == digest,
                "A preserved file changed during backup; inspect before switching")
    inactive()
    require(out("git", "rev-parse", "HEAD") == LOCAL_HEAD, "HEAD changed during backup")
    require(not paths("git", "diff", "--cached", "--name-only", "-z") and
            set(paths("git", "diff", "--name-only", "-z")) <= {MARKER},
            "Workspace changed during backup; inspect it")
    switched = False
    try:
        run("git", "restore", "--source=HEAD", "--worktree", "--", MARKER)
        run("git", "branch", "-m", archive)
        run("git", "switch", "-c", BRANCH, "--track", remote)
        switched = True
    except Exception:
        if out("git", "branch", "--show-current") == archive and out("git", "rev-parse", "HEAD") == LOCAL_HEAD:
            branch_exists = subprocess.run(["git", "show-ref", "--verify", "--quiet", "refs/heads/" + BRANCH])
            if branch_exists.returncode == 1:
                run("git", "branch", "-m", BRANCH)
        raise
    finally:
        for name in added + ([] if switched else [".gitignore"]):
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saved / "files" / name, destination)
        report["phase"] = "reviewed_checkout_restored" if switched else "switch_failed_files_preserved"
        journal.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    require(out("git", "rev-parse", "HEAD") == DEPLOYMENT and
            out("git", "branch", "--show-current") == BRANCH, "Unexpected reviewed checkout")
    require(not out("git", "status", "--porcelain", "--untracked-files=no"), "Tracked checkout is not clean")
    require(out("git", "rev-parse", archive) == LOCAL_HEAD, "Preserved snapshot branch changed")
    for name in added:
        require(hashlib.sha256((root / name).read_bytes()).hexdigest() == hashes[name],
                "Preserved artifact verification failed")
    print(json.dumps({k: v for k, v in report.items() if k != "file_sha256"}, indent=2), flush=True)
    return report


def main():
    normalize()
    payload = subprocess.check_output(["git", "show", HELPER_COMMIT + ":" + HELPER_PATH])
    require(hashlib.sha256(payload).hexdigest() == HELPER_SHA256, "Preparation helper hash mismatch")
    directory = Path(tempfile.mkdtemp(prefix="android72-normalized-launcher-"))
    helper = directory / "prepare.py"
    helper.write_bytes(payload)
    run(sys.executable, "-B", str(helper))


if __name__ == "__main__":
    main()
