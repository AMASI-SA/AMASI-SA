#!/usr/bin/env python3
"""One-use PR2134 Backend restoration for one pinned metadata observation.

This is a separate continuation, not a change to the original verifier,
preservation eligibility rules, receipt, configuration, or observation.
Root may execute only after independent review of these exact bytes. No
credential contents are read, hashed, copied, modified, or claimed equal.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
from types import ModuleType


REPO = Path("/app")
OPERATOR = REPO / ".git/operational-completion-operator-b7c7d13f"
STAGE = REPO / ".git/operational-refresh-completion-b7c7d13f"
SOURCE = "b7c7d13f9685fdbc5d7c93b8f8278638bcb2aa6d"
INTENT = "a59e6ee18f41d13baaa0e863de2ed5810dd43319"
DEPLOYMENT = "33be2d0e1b51faa355d55707c0c374f4c1d6bbe2"
TREE = "57601ee93838dde732e122354f0766c7e7414301"
RELEASE_ID = "rg5-3786c214f5c7213c2b3c118ff79d895b716268f479c94d8c5fba2f7a0de66b86"
MANIFEST_SHA256 = "a9f98d8f90c22617995d25cb7cd7a223d71e53343092daf326ee649e27db817c"
CONFIG_SHA256 = "e0bc4866e20131aef7ea53b35a916234d19bf258b2a9b5e7f2d05ea969516fa5"
RECEIPT_SHA256 = "8974eba82cad7ecd859c3e1b4dced8406daafd2758b39c2a0609f20296c365a1"
PROOF_SHA256 = "6e6534130c99d75433c08e2f281a444fb72c441873e2229c25949bd238ad761b"
OBSERVATION_SHA256 = "31b4c6d6490a01b86c66663fadacfa182db060014ef752c9d034401bad12d29d"
OBSERVATION_FILE = "postpublication-preservation.json"
JOURNAL_FILE = "restore-mtime-31b4c6d6490a01b8.json"
SOLE_VIOLATION = "credential_change_is_not_the_reviewed_replacement_pattern"
PAYLOADS = {
    "README.md", "offline_contract_checks.py", "offline_contract_checks_result.json",
    "operator_config.py", "operator_preservation.py", "owned_output_backup.py",
    "prepare_original_host.py", "restore_original_backend.py", "verify_original_host.py",
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def pinned_bytes(path, expected, maximum=8 * 1024 * 1024):
    """Read only fixed operator/evidence paths, with exact bytes and no symlinks."""
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and path.absolute() == path.resolve(strict=True)
            and before.st_size <= maximum, "pinned evidence is not a bounded regular file")
    fields = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_nlink")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        require(all(getattr(os.fstat(descriptor), key) == getattr(before, key) for key in fields),
                "pinned evidence changed before read")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(maximum + 1)
        require(len(data) <= maximum
                and all(getattr(os.fstat(descriptor), key) == getattr(before, key) for key in fields),
                "pinned evidence changed during read")
    finally:
        os.close(descriptor)
    require(hashlib.sha256(data).hexdigest() == expected, "pinned evidence digest differs")
    return data


def pinned_bundle():
    manifest = json.loads(pinned_bytes(OPERATOR / "draft_manifest.json", MANIFEST_SHA256))
    rows = manifest["files"]
    require(len(rows) == 9 and {row["path"] for row in rows} == PAYLOADS,
            "frozen operator payload cohort differs")
    result = {}
    for row in rows:
        name = row["path"]
        require(Path(name).name == name and name not in result, "operator payload path differs")
        result[name] = pinned_bytes(OPERATOR / name, row["sha256"])
        require(len(result[name]) == row["bytes"], "operator payload length differs")
    require(hashlib.sha256(result["operator_config.py"]).hexdigest() == CONFIG_SHA256,
            "frozen configuration differs")
    return result


def load_frozen_helpers():
    # Execute only the already reviewed, byte-verified definitions, so imports
    # cannot switch to unverified disk contents after the hash check.
    payloads = pinned_bundle()
    sys.dont_write_bytecode = True
    for name in ("operator_config", "prepare_original_host", "operator_preservation"):
        require(name not in sys.modules, "operator helper was already imported")
        module = ModuleType(name)
        module.__file__ = str(OPERATOR / (name + ".py"))
        sys.modules[name] = module
        exec(compile(payloads[name + ".py"], module.__file__, "exec"), module.__dict__)
    return tuple(sys.modules[name] for name in (
        "operator_config", "prepare_original_host", "operator_preservation"))


def command(helper, argv, *, allowed=(0,), timeout=45):
    process = None
    try:
        process = subprocess.Popen(argv, cwd=REPO, env=helper.host_environment(),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, start_new_session=True)
        stdout, _stderr = process.communicate(timeout=timeout)
    except BaseException:
        if process is not None:
            helper.stop_owned_process(process)
        raise
    require(process.returncode in allowed, "bounded continuation command failed")
    return stdout.strip(), process.returncode


def closed_guard(helper):
    text, _ = command(helper, [sys.executable, "-B", "scripts/production_release_guard.py", "status"])
    require(json.loads(text).get("active") is False, "original guard is active; restoration refused")


def exact_checkout(helper):
    for arguments, expected in ((["rev-parse", "HEAD"], DEPLOYMENT),
                                (["rev-parse", "HEAD^{tree}"], TREE),
                                (["status", "--porcelain", "--untracked-files=no"], "")):
        require(helper.git_read(REPO, *arguments) == expected, "original tracked checkout differs")


def exact_pattern(saved):
    require(saved["original_receipt_sha256"] == RECEIPT_SHA256
            and saved["official_guard_proof_sha256"] == PROOF_SHA256,
            "saved observation evidence links differ")
    observation = saved["observation"]
    require(observation["source"] == SOURCE and observation["deployment"] == DEPLOYMENT
            and observation["release_id"] == RELEASE_ID
            and observation["strictly_preserved"] is False
            and observation["eligible_for_exact_review"] is False
            and observation["violations"] == [SOLE_VIOLATION]
            and observation["foreign_root_count"] == 59
            and observation["untracked_count"] == 432
            and observation["foreign_root_changes"] == []
            and observation["variance_actor_attribution"] == "not established",
            "saved observation is not this one reviewed exception")
    before, after = (observation["credential_metadata_only_before"],
                     observation["credential_metadata_only_after"])
    fields = {"path", "device", "inode", "mode", "uid", "gid", "size", "mtime_ns", "nlink"}
    require(set(before) == set(after) == fields and before["path"] == "backend/.env"
            and all(type(before[key]) is int and type(after[key]) is int for key in fields - {"path"})
            and stat.S_ISREG(before["mode"]) and before["nlink"] == 1
            and all(before[key] == after[key] for key in fields - {"mtime_ns"})
            and after["mtime_ns"] > before["mtime_ns"],
            "credential observation is not the exact same-inode mtime-only pattern")
    variances, changes = observation["eligible_variances"], observation["untracked_changes"]
    require(len(variances) == len(changes) == 1, "bookkeeping observation cohort differs")
    variance, change = variances[0], changes[0]
    require(variance["kind"] == "bookkeeping_append"
            and variance["path"] == ".emergent/stripped_env_names"
            and variance["appended_bytes"] == 378
            and variance["original_prefix_preserved"] is True
            and variance["original_prefix_sha256"] == variance["before"]["sha256"]
            and variance["before"] == change["before"] and variance["after"] == change["after"]
            and variance["after"]["size"] - variance["before"]["size"] == 378,
            "bookkeeping append proof differs")
    return observation


def pinned_chain(helper, preservation):
    receipt, receipt_bytes, _ = preservation.read_json(
        helper, STAGE / "receipt.json", expected_sha=RECEIPT_SHA256)
    proof, _proof_bytes, _ = preservation.read_json(
        helper, STAGE / "guard-verify.json", expected_sha=PROOF_SHA256)
    saved, _observation_bytes, _ = preservation.read_json(
        helper, STAGE / OBSERVATION_FILE, expected_sha=OBSERVATION_SHA256)
    require(receipt.get("phase") == "production_verified_local_restore_pending"
            and receipt.get("source") == SOURCE and receipt.get("intent") == INTENT
            and receipt.get("deployment") == DEPLOYMENT and receipt.get("tree") == TREE
            and receipt.get("release_id") == RELEASE_ID
            and receipt.get("verify_exit_code") == 0 and receipt.get("verify_invocations") == 1
            and receipt.get("publication_action_count") == 1
            and receipt.get("development_backend_stopped_by_task") is True
            and receipt.get("backend_restore_required") is True
            and receipt.get("supervisor_before", "").split()[1:2] == ["RUNNING"]
            and receipt.get("original_proof_sha256") == PROOF_SHA256
            and receipt.get("guard_verify") == proof, "retained verified receipt does not admit restoration")
    require(proof.get("verified") is True and proof.get("checks") == 3
            and proof.get("protocol_version") == 5 and proof.get("source_git_sha") == SOURCE
            and proof.get("deployment_git_sha") == DEPLOYMENT
            and proof.get("release_id") == RELEASE_ID, "official proof identity differs")
    helper.load_previous(REPO)
    return receipt, receipt_bytes, exact_pattern(saved)


def run(acknowledged_observation):
    require(acknowledged_observation == OBSERVATION_SHA256,
            "this exact observation must be explicitly selected after independent review")
    cfg, helper, preservation = load_frozen_helpers()
    helper.validate_configuration()
    require(cfg.REPO == REPO and cfg.REPO / cfg.STAGE_REL == STAGE
            and cfg.SOURCE == SOURCE and cfg.INTENT == INTENT and cfg.DEPLOYMENT == DEPLOYMENT
            and cfg.TREE == TREE and cfg.RELEASE_ID == RELEASE_ID, "frozen configuration identity differs")
    closed_guard(helper)  # First host command remains the unchanged guard's status.
    helper.real_directory(STAGE)
    receipt, original_bytes, observed = pinned_chain(helper, preservation)
    output = STAGE / JOURNAL_FILE
    for path in (STAGE / "restore-continuation.json", output, output.with_name(output.name + ".new")):
        require(not path.exists() and not path.is_symlink(), "a restoration journal already exists; inspect instead of replaying")
    result = {
        "schema": "operational_backend_exact_mtime_restore_v1", "phase": "validation_started",
        "created_at": helper.utc_now(), "source": SOURCE, "intent": INTENT,
        "deployment": DEPLOYMENT, "tree": TREE, "release_id": RELEASE_ID,
        "operator_manifest_sha256": MANIFEST_SHA256, "operator_config_sha256": CONFIG_SHA256,
        "original_receipt_sha256": RECEIPT_SHA256, "original_guard_proof_sha256": PROOF_SHA256,
        "preservation_observation_sha256": OBSERVATION_SHA256,
        "operator_acknowledged_observation_sha256": acknowledged_observation,
        "original_receipt_unchanged": True, "original_observation_unchanged": True,
        "original_guard_active": False, "original_eligibility_and_violation_retained": True,
        "exact_saved_observation": observed, "credential_content_equality_verified": False,
        "credential_contents_read_hashed_copied_or_modified_by_continuation": False,
        "variance_actor_attribution": "not established", "backend_stopped_by_this_task": True,
        "backend_restore_required": True, "service_start_attempts": 0,
        "review_basis": "Separate one-use continuation for the pinned observation; original refusal is not rewritten.",
    }
    # Exclusive journal consumes this continuation even if later validation fails.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        exact_checkout(helper)
        require(preservation.inspect_after_publication(helper, receipt, REPO) == observed,
                "current complete preservation observation differs from the exact saved observation")
        pinned_chain(helper, preservation)
        pinned_bundle()
        closed_guard(helper)
        service, code = command(helper, ["supervisorctl", "status", "backend"], allowed=(3,))
        require(service.split()[1:2] == ["STOPPED"], "Backend is not the task-owned stopped service")
        result.update(phase="restore_start_requested", start_requested_at=helper.utc_now(),
                      service_start_attempts=1, supervisor_before_restore={"state": "STOPPED", "exit_code": code})
        helper.private_json(output, result)  # Durable admission before the sole start request.
        command(helper, ["supervisorctl", "start", "backend"])
        service, code = command(helper, ["supervisorctl", "status", "backend"])
        require(service.split()[1:2] == ["RUNNING"], "Backend did not reach RUNNING")
        result.update(development_backend_restored=True, restored_at=helper.utc_now(),
                      backend_restore_required=False,
                      supervisor_after_restore={"state": "RUNNING", "exit_code": code})
        new_receipt, new_bytes, new_observed = pinned_chain(helper, preservation)
        require(new_bytes == original_bytes and new_receipt == receipt and new_observed == observed,
                "immutable restoration evidence changed")
        require(preservation.inspect_after_publication(helper, receipt, REPO) == observed,
                "preservation changed during service restoration")
        exact_checkout(helper)
        pinned_bundle()
        closed_guard(helper)
        result["phase"] = "production_verified_local_backend_restored"
    except BaseException as error:
        result.update(phase="restore_requires_inspection", error_type=type(error).__name__,
                      error=str(error)[:1000], failed_at=helper.utc_now())
        raise
    finally:
        helper.private_json(output, result)
    print(json.dumps({"phase": result["phase"], "deployment": DEPLOYMENT,
        "journal": str(output), "service_start_attempts": result["service_start_attempts"],
        "backend_restore_required": result["backend_restore_required"],
        "strict_preservation": False, "credential_content_equality_verified": False}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--acknowledge-exact-observation", required=True)
    args = parser.parse_args()
    run(args.acknowledge_exact_observation)
    return 0


if __name__ == "__main__":
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt("exact observation restoration interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
