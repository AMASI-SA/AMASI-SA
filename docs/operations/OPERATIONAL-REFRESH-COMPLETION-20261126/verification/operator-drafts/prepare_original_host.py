#!/usr/bin/env python3
"""DRAFT: prepare the original /app for the completion release; never lease or publish.

DRAFT CONFIGURATION REFUSES EXECUTION. Root must freeze operator_config.py.
This file is deliberately outside the frozen source tree.
Run once, after the ordinary merge, with the exact deployment commit:

    python -B prepare_original_host.py --deployment-sha <D>

The first host operation is the original guard's read-only status command.
The new stage is exclusive: an existing stage requires operator inspection,
not an automatic replay. Backend remains stopped for the owner's later
prepare/publish/verify sequence. No old output or cache is restored here.

Importing this module has no host side effects. check_foreign() is available
to a later read-only verifier using the saved receipt.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone


import operator_config as cfg

REPO, SOURCE, INTENT, TREE, BASE = cfg.REPO, cfg.SOURCE, cfg.INTENT, cfg.TREE, cfg.BASE
RELEASE_ID, OLD_SOURCE, OLD_RELEASE_ID = cfg.RELEASE_ID, cfg.OLD_SOURCE, cfg.OLD_RELEASE_ID
BRANCH, STAGE_REL, PREVIOUS_REL = cfg.BRANCH, cfg.STAGE_REL, cfg.PREVIOUS_REL
OLD_DEPENDENCIES_REL, GENERATED, RESERVE = cfg.OLD_DEPENDENCIES_REL, cfg.GENERATED, cfg.RESERVE
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
META_KEYS = ("path", "device", "inode", "mode", "uid", "gid")


class PreparationError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise PreparationError(message)



def validate_configuration(deployment=None):
    require(cfg.DRAFT is False, "draft operator configuration has not been frozen/reviewed")
    for value in (SOURCE, INTENT, cfg.DEPLOYMENT, TREE, BASE, OLD_SOURCE, cfg.OLD_TREE):
        require(type(value) is str and FULL_SHA.fullmatch(value) is not None,
                "operator Git identity is not a full reviewed SHA")
    for value in (cfg.PREVIOUS_RECEIPT_SHA256, cfg.PREVIOUS_PROOF_SHA256,
                  cfg.PREVIOUS_CONTINUATION_SHA256):
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None,
                "previous evidence chain hash is not frozen")
    require(re.fullmatch(r"rg5-[0-9a-f]{64}", RELEASE_ID) is not None,
            "new runtime release identity is not frozen")
    require(STAGE_REL == ".git/operational-refresh-completion-" + SOURCE[:8]
            and STAGE_REL != PREVIOUS_REL and cfg.ACTOR == "operational-refresh-completion-20261126",
            "new operator stage/actor is not exact")
    require(deployment is None or deployment == cfg.DEPLOYMENT,
            "deployment argument differs from frozen operator configuration")
    require(BRANCH == "hotfix/prod-snap-meta-final", "production branch differs")

def utc_now():
    return datetime.now(timezone.utc).isoformat()


def relative_path(value):
    require(type(value) is str and value, "invalid saved relative path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.as_posix() == value
            and "\\" not in value and "\x00" not in value
            and all(part not in ("", ".", "..") for part in path.parts),
            "unsafe saved relative path")
    return value


def credential_path(value):
    """Explicit metadata-only policy; never infer safety from an old digest."""
    parts = [part.lower() for part in PurePosixPath(value).parts]
    exact = {".env", ".npmrc", ".yarnrc", ".netrc", ".pypirc",
             ".ssh", ".aws", ".kube", "id_rsa", "id_ed25519"}
    return any(
        part in exact or part.startswith(".env.")
        or any(word in part for word in ("credential", "secret", "token",
                                        "private_key", "private-key",
                                        "service_account", "service-account"))
        or part.endswith((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))
        for part in parts
    )


def protected_archive_path(value):
    """Actual configuration names, not public dependency/token source names."""
    parts = [part.lower() for part in PurePosixPath(value).parts]
    basename = parts[-1]
    exact = {".env", ".npmrc", ".yarnrc", ".netrc", ".pypirc",
             "id_rsa", "id_ed25519", "credentials.json", "credentials",
             "secrets.json", "service-account.json", "service_account.json"}
    return (basename in exact or basename.startswith(".env.")
            or basename.endswith((".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"))
            or any(part in {".ssh", ".aws", ".kube"} for part in parts))


def metadata(path, relative):
    info = path.lstat()
    return {"path": relative, "device": info.st_dev, "inode": info.st_ino,
            "mode": info.st_mode, "uid": info.st_uid, "gid": info.st_gid,
            "size": info.st_size, "mtime_ns": info.st_mtime_ns, "nlink": info.st_nlink}


def same_stat(info, record):
    return all(getattr(info, attr) == record[key] for key, attr in (
        ("device", "st_dev"), ("inode", "st_ino"), ("mode", "st_mode"),
        ("uid", "st_uid"), ("gid", "st_gid"), ("size", "st_size"),
        ("mtime_ns", "st_mtime_ns"), ("nlink", "st_nlink"),
    ))


def real_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode),
            "required directory is not a real directory")
    require(path.absolute() == path.resolve(strict=True),
            "required directory has an indirect parent")
    return info


def safe_regular_digest(path, record):
    require(not credential_path(record["path"]), "credential content read refused")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        require(same_stat(os.fstat(descriptor), record), "file changed before digest")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        require(same_stat(os.fstat(descriptor), record), "file changed during digest")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def host_environment():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["REACT_APP_BACKEND_URL"] = "https://mezansalla.com"
    return env


def stop_owned_process(process):
    """Join only the process group created by this runner."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def capture_unlogged(argv, *, cwd=REPO, timeout=60):
    process = subprocess.Popen(argv, cwd=cwd, env=host_environment(),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        stop_owned_process(process)
        raise
    require(process.returncode == 0, "read-only host command failed")
    return stdout


def git_read(repo, *arguments):
    return capture_unlogged(["git", *arguments], cwd=repo).rstrip("\n")


def fresh_untracked_record(repo, template):
    relative = relative_path(template["path"])
    path = repo / relative
    record = metadata(path, relative)
    mode = record["mode"]
    if stat.S_ISLNK(mode):
        record["symlink_target"] = os.readlink(path)
    else:
        require(stat.S_ISREG(mode), "untracked entry is not a file or symlink")
        if credential_path(relative):
            record["metadata_only"] = True
            record["metadata_only_reason"] = "credential_path_policy"
        elif "sha256" in template:
            record["sha256"] = safe_regular_digest(path, record)
        else:
            record["metadata_only"] = True
            record["metadata_only_reason"] = "previous_manifest_has_no_digest"
    return record


def fresh_foreign_roots(repo, records):
    result = []
    for row in records:
        current = metadata(repo / relative_path(row["path"]), row["path"])
        result.append({key: current[key] for key in META_KEYS})
    return result


def check_foreign(receipt, repo=REPO):
    """Read-only comparison against this attempt's fresh baseline."""
    repo = Path(repo)
    from operator_preservation import require_complete_baseline
    require_complete_baseline(sys.modules[__name__], receipt, repo)
    current_files = git_read(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    require(current_files == receipt["untracked_files"], "untracked file cohort changed")
    current_roots = fresh_foreign_roots(repo, receipt["foreign_roots"])
    require(current_roots == receipt["foreign_roots"], "foreign root metadata changed")
    current_manifest = [fresh_untracked_record(repo, row)
                        for row in receipt["untracked_manifest"]]
    require(current_manifest == receipt["untracked_manifest"],
            "untracked file metadata or permitted digest changed")
    if "credential_metadata_only" in receipt:
        current_credential = metadata(repo / cfg.CREDENTIAL, cfg.CREDENTIAL)
        require(current_credential == receipt["credential_metadata_only"],
                "credential metadata changed from the fresh baseline")
    return {"foreign_roots": len(current_roots), "untracked_files": len(current_files),
            "metadata_only": sum(bool(row.get("metadata_only")) for row in current_manifest)}


def private_json(path, value):
    temporary = path.with_name(path.name + ".new")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def load_previous(repo):
    from operator_preservation import validated_previous
    previous, _restored = validated_previous(sys.modules[__name__], repo)
    return previous


def tree_inventory(repo, roots):
    """No-follow metadata inventory, including symlinks but no special nodes."""
    device = repo.lstat().st_dev
    rows = []

    def visit(path, relative):
        record = metadata(path, relative)
        mode = record["mode"]
        if stat.S_ISLNK(mode):
            record["symlink_target"] = os.readlink(path)
        elif stat.S_ISDIR(mode):
            require(record["device"] == device and not os.path.ismount(path),
                    "nested filesystem in generated/cache tree")
        else:
            require(stat.S_ISREG(mode), "special node in generated/cache tree")
        rows.append(record)
        if stat.S_ISDIR(mode):
            for child in sorted(path.iterdir(), key=lambda value: value.name):
                visit(child, child.relative_to(repo).as_posix())

    for relative in roots:
        path = repo / relative_path(relative)
        require(not path.is_symlink(), "owned root is a symlink")
        require(path.absolute() == path.resolve(strict=True), "owned root has an indirect parent")
        visit(path, relative)
    return rows


class ReserveWriter:
    """Count/hash compressed bytes while retaining the required free reserve."""
    def __init__(self, stream, stage):
        self.stream, self.stage = stream, stage
        self.digest = hashlib.sha256()
        self.bytes = 0

    def write(self, data):
        require(shutil.disk_usage(self.stage).free >= RESERVE + len(data) + 4096,
                "compressed backup would cross the 128 MiB reserve")
        written = self.stream.write(data)
        require(written == len(data), "short backup write")
        self.digest.update(data)
        self.bytes += written
        return written

    def flush(self):
        self.stream.flush()

    def tell(self):
        return self.bytes


class Runner:
    def __init__(self, deployment):
        self.repo, self.stage = REPO, REPO / STAGE_REL
        self.receipt = {
            "schema": "operational_original_preparation_v1",
            "source": SOURCE, "intent": INTENT, "deployment": deployment,
            "tree": TREE, "release_id": RELEASE_ID, "source_base": BASE, "base": BASE,
            "phase": "not_started", "created_at": utc_now(),
            "development_backend_stopped_by_task": False,
            "supervisor_before": None, "backend_restore_required": False,
            "commands": [], "cache_quarantine": [],
            "no_lease_or_publish": True, "lease_created": False,
            "publication_action_count": 0,
        }
        self.ready = False
        self.sequence = 0

    def save(self):
        self.receipt["updated_at"] = utc_now()
        private_json(self.stage / "receipt.json", self.receipt)

    def phase(self, name, *, complete=False):
        self.receipt["phase"] = name
        if complete:
            self.receipt["last_completed_phase"] = name
        self.save()
        print(json.dumps({"phase": name, "complete": complete}), flush=True)

    def command(self, label, argv, *, cwd=None, env=None, timeout=180, capture=False,
                allowed_exit_codes=(0,)):
        self.sequence += 1
        log_name = f"{self.sequence:02d}-{label}.log"
        started = time.monotonic()
        entry = {"label": label, "log": log_name, "started_at": utc_now()}
        self.receipt["commands"].append(entry)
        self.save()
        descriptor = os.open(self.stage / log_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", buffering=0) as stream:
            process = subprocess.Popen(argv, cwd=cwd or self.repo, env=env or host_environment(),
                                       stdout=stream, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            try:
                entry["pid"] = process.pid
                self.save()
                while True:
                    remaining = timeout - (time.monotonic() - started)
                    require(remaining > 0, "owned command exceeded its bounded runtime")
                    try:
                        process.wait(timeout=min(15, remaining))
                        break
                    except subprocess.TimeoutExpired:
                        print(json.dumps({"phase": self.receipt["phase"], "running": label,
                                          "elapsed_seconds": int(time.monotonic() - started)}),
                              flush=True)
            except BaseException:
                stop_owned_process(process)
                entry["interrupted"] = True
                raise
            finally:
                entry["exit_code"] = process.returncode
                entry["finished_at"] = utc_now()
                self.save()
        require(process.returncode in allowed_exit_codes,
                "command failed; inspect saved log: " + log_name)
        if capture:
            return (self.stage / log_name).read_text(encoding="utf-8").rstrip("\n")
        return None

    def guard_inactive(self, label):
        value = json.loads(self.command(label, [sys.executable, "-B",
            "scripts/production_release_guard.py", "status"], capture=True))
        require(value.get("active") is False, "original release guard is active")
        return value

    def assert_tracked(self, expected):
        require(git_read(self.repo, "rev-parse", "HEAD") == expected, "original HEAD changed")
        require(git_read(self.repo, "branch", "--show-current") == BRANCH,
                "original branch differs")
        require(not git_read(self.repo, "status", "--porcelain", "--untracked-files=no"),
                "original tracked worktree is dirty")

    def initialize(self):
        # Deliberately precedes receipt reads, stage creation, service changes and Git fetch.
        first = json.loads(capture_unlogged([sys.executable, "-B",
            "scripts/production_release_guard.py", "status"]))
        require(first.get("active") is False, "original release guard is active")
        real_directory(self.repo)
        git_dir = real_directory(self.repo / ".git")
        require(git_dir.st_dev == self.repo.lstat().st_dev, "stage is on another filesystem")
        real_directory(self.repo / "backend")
        real_directory(self.repo / "frontend")
        require(not self.stage.exists() and not self.stage.is_symlink(),
                "task stage already exists; inspect it instead of rerunning")
        self.assert_tracked(BASE)
        previous = load_previous(self.repo)
        require(git_read(self.repo, "ls-files", "--others", "--exclude-standard").splitlines()
                == previous["untracked_files"], "current untracked cohort differs from prior receipt")
        require(shutil.disk_usage(self.repo).free >= RESERVE + 4 * 1024 * 1024,
                "insufficient free space for receipt/baseline reserve")
        self.stage.mkdir(mode=0o700)
        self.ready = True
        self.receipt.update({
            "initial_guard": first, "previous_receipt": PREVIOUS_REL + "/receipt.json",
            "previous_receipt_phase": previous["phase"],
            "free_bytes_before": shutil.disk_usage(self.repo).free,
            "prior_backups_retention": "No previous stage or backup is an allowed mutation target.",
            "previous_receipt_sha256": cfg.PREVIOUS_RECEIPT_SHA256,
            "previous_restore_continuation": PREVIOUS_REL + "/restore-continuation.json",
            "previous_restore_continuation_sha256": cfg.PREVIOUS_CONTINUATION_SHA256,
            "previous_guard_proof_sha256": cfg.PREVIOUS_PROOF_SHA256,
            "prior_backup_roots": [
                metadata(self.repo / path, path) for path in sorted({
                    cfg.DEPENDENCY_ARCHIVE_REL, OLD_DEPENDENCIES_REL,
                    *(row["path"] for row in previous.get("prior_backup_roots", [])),
                })
            ],
        })
        self.previous = previous
        self.phase("initial_guard_inactive", complete=True)

    def validate_previous_and_stop(self):
        self.phase("previous_runtime_validation")
        code = (
            "import json,sys;sys.path.insert(0,'/app/backend');"
            "from release_identity import read_release_identity;"
            "value=read_release_identity();"
            "print(json.dumps({key:value.get(key) for key in "
            "('verified_identity_available','backend_runtime_source_verified',"
            "'critical_file_hashes_match','frontend_build_verified','source_git_sha','release_id')}))"
        )
        identity = json.loads(self.command("previous-runtime", [sys.executable, "-B", "-c", code],
                                           capture=True))
        require(all(identity.get(key) is True for key in (
            "verified_identity_available", "backend_runtime_source_verified",
            "critical_file_hashes_match", "frontend_build_verified")),
            "previous runtime identity or derived caches are not valid")
        require(identity.get("source_git_sha") == OLD_SOURCE
                and identity.get("release_id") == OLD_RELEASE_ID, "previous runtime identity differs")
        self.receipt["previous_runtime_identity"] = identity
        supervisor = self.command("supervisor-before", ["supervisorctl", "status", "backend"],
                                  capture=True)
        require(re.match(r"^backend\s+RUNNING\b", supervisor) is not None,
                "Backend does not have the observed RUNNING baseline")
        self.receipt["supervisor_before"] = supervisor
        self.guard_inactive("guard-before-stop")
        self.assert_tracked(BASE)
        self.phase("backend_stop_requested")
        self.receipt["backend_restore_required"] = True
        self.save()
        self.command("backend-stop", ["supervisorctl", "stop", "backend"], timeout=90)
        self.receipt["development_backend_stopped_by_task"] = True
        self.save()
        stopped = self.command("supervisor-stopped", ["supervisorctl", "status", "backend"],
                               capture=True, allowed_exit_codes=(3,))
        require(re.match(r"^backend\s+STOPPED\b", stopped) is not None,
                "Backend did not reach STOPPED")
        self.receipt["supervisor_stopped"] = stopped
        self.phase("backend_stopped", complete=True)
        self.receipt["foreign_roots"] = fresh_foreign_roots(self.repo, self.previous["foreign_roots"])
        self.receipt["untracked_files"] = list(self.previous["untracked_files"])
        self.receipt["untracked_manifest"] = [
            fresh_untracked_record(self.repo, row) for row in self.previous["untracked_manifest"]
        ]
        self.receipt["owned_generated"] = [metadata(self.repo / path, path) for path in GENERATED]
        credential = metadata(self.repo / cfg.CREDENTIAL, cfg.CREDENTIAL)
        require(stat.S_ISREG(credential["mode"]) and not (self.repo / cfg.CREDENTIAL).is_symlink(),
                "credential baseline must be a regular metadata-only file")
        self.receipt["credential_metadata_only"] = credential
        self.receipt["credential_contents_read_hashed_copied_or_modified"] = False
        self.receipt["foreign_baseline_captured_at"] = utc_now()
        self.receipt["foreign_check"] = check_foreign(self.receipt, self.repo)
        self.phase("baseline_recorded", complete=True)

    def quarantine_caches(self):
        self.phase("cache_validation")
        code = """
import json, os, pathlib, sys
sys.path.insert(0, '/app/backend')
from release_protocol_v5 import _validate_derived_bytecode_directory
root = pathlib.Path('/app/backend')
paths = []
for current, directories, files in os.walk(root, followlinks=False):
    for name in list(directories):
        path = pathlib.Path(current) / name
        if path.is_symlink():
            raise RuntimeError('symlink in Backend cache traversal')
        if name == '__pycache__':
            _validate_derived_bytecode_directory(path, label='Original derived cache')
            paths.append(path.relative_to('/app').as_posix())
            directories.remove(name)
print(json.dumps(sorted(paths)))
"""
        paths = json.loads(self.command("derived-cache-validation",
            [sys.executable, "-B", "-c", code], capture=True))
        require(type(paths) is list and all(type(value) is str for value in paths),
                "cache validation did not return its full path list")
        records = tree_inventory(self.repo, paths)
        private_json(self.stage / "derived-caches-before.json", records)
        self.receipt["cache_manifest"] = "derived-caches-before.json"
        self.receipt["validated_cache_count"] = len(paths)
        self.phase("cache_quarantine")
        for relative in paths:
            source = self.repo / relative
            expected = [row for row in records if row["path"] == relative][0]
            require(metadata(source, relative) == expected, "cache changed after validation")
            destination = self.stage / "derived-caches" / relative
            destination.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            require(not destination.exists() and not destination.is_symlink(),
                    "cache destination already exists")
            self.receipt["cache_move_pending"] = relative
            self.save()
            os.rename(source, destination)
            self.receipt["cache_quarantine"].append({
                "path": relative, "quarantine_relative": destination.relative_to(self.stage).as_posix(),
                "device": expected["device"], "inode": expected["inode"],
            })
            self.receipt.pop("cache_move_pending", None)
            self.save()
        check_foreign(self.receipt, self.repo)
        self.phase("cache_quarantined", complete=True)
        self.command("previous-frontend", [sys.executable, "-B", "scripts/verify_frontend_build.py",
            "--expected-git-sha", OLD_SOURCE, "--reviewed-intent-v5"])
        self.phase("previous_frontend_verified", complete=True)

    def preserve_generated(self):
        from owned_output_backup import preserve_current_outputs
        preserve_current_outputs(self)

    def advance_source(self):
        self.guard_inactive("guard-before-source")
        self.assert_tracked(BASE)
        check_foreign(self.receipt, self.repo)
        self.phase("source_fetch")
        self.command("fetch-production", ["git", "fetch", "origin", BRANCH], timeout=180)
        deployment = self.receipt["deployment"]
        require(git_read(self.repo, "rev-parse", f"origin/{BRANCH}") == deployment,
                "remote production is not the reviewed deployment")
        for earlier, later in ((BASE, SOURCE), (SOURCE, INTENT), (INTENT, deployment)):
            self.command("ancestry", ["git", "merge-base", "--is-ancestor", earlier, later])
        require(git_read(self.repo, "rev-parse", f"{INTENT}^{{tree}}") == TREE,
                "intent commit tree differs")
        require(git_read(self.repo, "rev-parse", f"{deployment}^{{tree}}") == TREE,
                "deployment tree differs from reviewed intent tree")
        require(git_read(self.repo, "diff", "--name-only", SOURCE, deployment).splitlines()
                == ["release/release-intent-v5.json"], "source-to-deployment is not intent-only")
        require(not git_read(self.repo, "rev-list", "--full-history", f"{BASE}..{SOURCE}",
                             "--", "release/release-intent-v5.json"),
                "source ancestry modified the preceding intent")
        new_intent = json.loads(git_read(self.repo, "show",
                                         f"{deployment}:release/release-intent-v5.json"))
        require(new_intent.get("source_git_sha") == SOURCE
                and new_intent.get("source_base_git_sha") == BASE
                and new_intent.get("branch") == BRANCH
                and new_intent.get("runtime_identity", {}).get("release_id") == RELEASE_ID,
                "reviewed intent identity differs")
        self.guard_inactive("guard-before-fast-forward")
        self.assert_tracked(BASE)
        self.phase("source_fast_forward")
        self.command("fast-forward", ["git", "merge", "--ff-only", deployment], timeout=180)
        self.assert_tracked(deployment)
        check_foreign(self.receipt, self.repo)
        self.phase("source_advanced", complete=True)

    def build_and_verify(self):
        self.guard_inactive("guard-before-adapter")
        self.assert_tracked(self.receipt["deployment"])
        check_foreign(self.receipt, self.repo)
        env = host_environment()
        for key in ("CI", "GITHUB_ACTIONS", "GITHUB_SHA", "GITHUB_WORKSPACE",
                    "GITHUB_EVENT_NAME", "GITHUB_EVENT_PATH", "MEZAN_RELEASE_BOOTSTRAP_INTENT"):
            env.pop(key, None)
        env["REACT_APP_BACKEND_URL"] = "https://mezansalla.com"
        self.receipt["adapter_environment_contract"] = {
            "github_markers_unset": True, "bootstrap_unset": True,
            "ci_unset": True, "public_origin": "https://mezansalla.com",
            "dont_write_bytecode": True,
        }
        self.phase("adapter_build")
        self.command("original-adapter", ["yarn", "build"], cwd=self.repo / "frontend",
                     env=env, timeout=2400)
        self.phase("adapter_completed", complete=True)
        self.command("frontend-verifier", [sys.executable, "-B", "scripts/verify_frontend_build.py",
            "--expected-git-sha", SOURCE, "--reviewed-intent-v5"])
        self.phase("frontend_verified", complete=True)
        self.command("package-verifier", [sys.executable, "-B",
            "scripts/emergent_deployment_adapter.py", "verify-packages"], timeout=300)
        self.phase("packages_verified", complete=True)
        intent = json.loads((self.repo / "release/release-intent-v5.json").read_text())
        actual = json.loads((self.repo / "backend/release_identity.json").read_text())
        require(actual == intent["runtime_identity"], "materialized identity differs from intent")
        require(actual.get("source_git_sha") == SOURCE
                and actual.get("release_id") == RELEASE_ID, "materialized release identity differs")
        self.assert_tracked(self.receipt["deployment"])
        self.receipt["foreign_check"] = check_foreign(self.receipt, self.repo)
        for old in self.receipt["prior_backup_roots"]:
            require(metadata(self.repo / old["path"], old["path"]) == old,
                    "an earlier backup root changed")
        self.receipt["final_guard"] = self.guard_inactive("guard-after-local-verification")
        self.receipt["owned_generated_after"] = [
            metadata(self.repo / path, path) for path in GENERATED
        ]
        self.receipt["status"] = "awaiting_owner_release"
        self.receipt["backend_restore_required"] = True
        load_previous(self.repo)  # Recheck all three immutable old evidence files.
        self.receipt["local_verified_at"] = utc_now()
        self.phase("local_verified", complete=True)

    def execute(self):
        try:
            self.initialize()
            self.validate_previous_and_stop()
            self.quarantine_caches()
            self.preserve_generated()
            self.advance_source()
            self.build_and_verify()
        except BaseException as error:
            if self.ready:
                self.receipt["status"] = "failed"
                self.receipt["failed_at_phase"] = self.receipt["phase"]
                self.receipt["error_type"] = type(error).__name__
                self.receipt["error"] = str(error)
                self.receipt["failure_at"] = utc_now()
                try:
                    self.save()
                except Exception:
                    pass
            # Foreign paths and command stderr stay in private receipt/logs.
            print(json.dumps({"status": "failed", "phase": self.receipt["phase"],
                              "error_type": type(error).__name__,
                              "backend_restore_required": self.receipt["backend_restore_required"]}),
                  flush=True)
            raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-sha", required=True)
    args = parser.parse_args(argv)
    require(FULL_SHA.fullmatch(args.deployment_sha) is not None,
            "deployment SHA must be full lowercase hexadecimal")
    validate_configuration(args.deployment_sha)
    sys.dont_write_bytecode = True

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("operator interrupted preparation")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        Runner(args.deployment_sha).execute()
    except BaseException:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
