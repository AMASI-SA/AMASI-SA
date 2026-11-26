"""DRAFT: restore only this attempt's Backend after official verification.

Never rerun a verifier, publish, create/clear a lease, or rewrite the original
receipt. Any preservation variance needs a separate exact reviewed observation.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import operator_config as cfg
import prepare_original_host as helper
from operator_preservation import inspect_after_publication, read_json, require_exact_approval


def command(argv, *, allowed=(0,), timeout=45):
    result = subprocess.run(argv, cwd=cfg.REPO, env=helper.host_environment(),
                            capture_output=True, text=True, timeout=timeout, check=False)
    helper.require(result.returncode in allowed, "bounded restoration command failed")
    return result.stdout.strip(), result.returncode


def restore():
    helper.validate_configuration()
    root, stage = cfg.REPO, cfg.REPO / cfg.STAGE_REL
    guard, _ = command([sys.executable, "-B", "scripts/production_release_guard.py", "status"])
    helper.require(json.loads(guard).get("active") is False, "original guard is active; restoration refused")
    output = stage / "restore-continuation.json"
    helper.require(not output.exists() and not output.is_symlink(),
                   "restoration continuation already exists; inspect instead of replaying")
    receipt, original_bytes, receipt_sha = read_json(helper, stage / "receipt.json")
    helper.require(receipt.get("phase") in {"production_verified", "production_verified_local_restore_pending"}
                   and receipt.get("source") == cfg.SOURCE and receipt.get("intent") == cfg.INTENT
                   and receipt.get("deployment") == cfg.DEPLOYMENT and receipt.get("tree") == cfg.TREE
                   and receipt.get("release_id") == cfg.RELEASE_ID
                   and receipt.get("verify_exit_code") == 0 and receipt.get("verify_invocations") == 1
                   and receipt.get("publication_action_count") == 1
                   and receipt.get("development_backend_stopped_by_task") is True
                   and receipt.get("backend_restore_required") is True
                   and receipt.get("supervisor_before", "").split()[1:2] == ["RUNNING"],
                   "original verified receipt does not admit this service restoration")
    proof, _, proof_sha = read_json(helper, stage / "guard-verify.json")
    helper.require(proof_sha == receipt.get("original_proof_sha256") and receipt.get("guard_verify") == proof
                   and proof.get("verified") is True and proof.get("checks") == 3
                   and proof.get("protocol_version") == 5 and proof.get("source_git_sha") == cfg.SOURCE
                   and proof.get("deployment_git_sha") == cfg.DEPLOYMENT
                   and proof.get("release_id") == cfg.RELEASE_ID, "official proof does not admit restoration")
    for arguments, wanted in ((["rev-parse", "HEAD"], cfg.DEPLOYMENT),
                              (["rev-parse", "HEAD^{tree}"], cfg.TREE),
                              (["status", "--porcelain", "--untracked-files=no"], "")):
        helper.require(helper.git_read(root, *arguments) == wanted, "original tracked checkout differs")
    helper.load_previous(root)
    observed = inspect_after_publication(helper, receipt, root)
    approval = None
    if not observed["strictly_preserved"]:
        approval = require_exact_approval(helper, stage, receipt_sha, proof_sha, observed)
    service, code = command(["supervisorctl", "status", "backend"], allowed=(3,))
    helper.require(service.split()[1:2] == ["STOPPED"], "local Backend changed; do not restore another actor's service")
    helper.require((stage / "receipt.json").read_bytes() == original_bytes,
                   "original receipt changed during restoration validation")
    result = {"schema": "operational_backend_restore_continuation_v1", "phase": "restore_ready",
        "created_at": helper.utc_now(), "source": cfg.SOURCE, "deployment": cfg.DEPLOYMENT,
        "release_id": cfg.RELEASE_ID, "original_receipt_sha256": receipt_sha,
        "original_receipt_unchanged": True, "original_guard_proof_sha256": proof_sha,
        "original_guard_active": False, "preservation": observed, "exact_variance_approval": approval,
        "credential_contents_read_hashed_copied_or_modified": False,
        "variance_actor_attribution": "not established", "backend_stopped_by_this_task": True,
        "backend_restore_required": True, "supervisor_before_task": "RUNNING",
        "supervisor_before_restore": {"state": "STOPPED", "exit_code": code}}
    # Exclusive first write records admission before the service start request.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        result.update(phase="restore_start_requested", start_requested_at=helper.utc_now())
        helper.private_json(output, result)
        command(["supervisorctl", "start", "backend"])
        service, code = command(["supervisorctl", "status", "backend"])
        helper.require(service.split()[1:2] == ["RUNNING"], "Backend did not reach RUNNING")
        result.update(development_backend_restored=True, restored_at=helper.utc_now(),
                      backend_restore_required=False,
                      supervisor_after_restore={"state": "RUNNING", "exit_code": code})
        helper.require((stage / "receipt.json").read_bytes() == original_bytes,
                       "original verified receipt changed during restoration")
        helper.require(inspect_after_publication(helper, receipt, root) == observed,
                       "preservation changed during service restoration")
        helper.load_previous(root)
        result["phase"] = "production_verified_local_backend_restored"
    except BaseException as error:
        result.update(phase="restore_requires_inspection", error_type=type(error).__name__,
                      error=str(error)[:1000])
        raise
    finally:
        helper.private_json(output, result)
    print(json.dumps({"phase": result["phase"], "source": cfg.SOURCE,
        "deployment": cfg.DEPLOYMENT, "release_id": cfg.RELEASE_ID,
        "strict_preservation": observed["strictly_preserved"],
        "explicit_variances": len(observed["eligible_variances"]),
        "backend_restore_required": False}), flush=True)
    return result


if __name__ == "__main__":
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt("new restoration continuation interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    restore()
