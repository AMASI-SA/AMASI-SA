"""DRAFT: run the unchanged original verifier once after explicit publication.

No publish/prepare/lease-clear/financial command is present. Exact publication
receipt fields are supplied separately by root after the platform's completion.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
import signal
import subprocess
import sys

import operator_config as cfg
import prepare_original_host as helper
from operator_preservation import inspect_after_publication, read_json


def instant(value):
    parsed = datetime.fromisoformat(value)
    helper.require(parsed.tzinfo is not None, "publication timestamp must include timezone")
    return parsed


def main():
    helper.validate_configuration()
    root, stage = cfg.REPO, cfg.REPO / cfg.STAGE_REL
    guard = json.loads(helper.capture_unlogged(
        [sys.executable, "-B", "scripts/production_release_guard.py", "status"]))
    receipt, _, _ = read_json(helper, stage / "receipt.json")
    expected = {"source_git_sha": cfg.SOURCE, "deployment_git_sha": cfg.DEPLOYMENT,
                "release_id": cfg.RELEASE_ID, "actor": cfg.ACTOR, "protocol_version": 5}
    # Preconditions do not rewrite a receipt from a different or prior attempt.
    helper.require(receipt.get("phase") == "publication_succeeded"
                   and receipt.get("publication_action_count") == 1
                   and not receipt.get("verify_invocations"), "verification is not newly admitted")
    helper.require(all(receipt.get(key) == value for key, value in {
        "source": cfg.SOURCE, "intent": cfg.INTENT, "deployment": cfg.DEPLOYMENT,
        "tree": cfg.TREE, "release_id": cfg.RELEASE_ID}.items()), "new receipt identity differs")
    helper.require(type(receipt.get("publication_version")) is str
                   and receipt["publication_version"]
                   and receipt["publication_version"] != cfg.OLD_PUBLICATION_VERSION
                   and instant(receipt["publication_succeeded_at"]) > instant(receipt["local_verified_at"]),
                   "newer explicit publication completion is not recorded")
    helper.require(guard.get("active") is True
                   and all(guard.get(key) == value for key, value in expected.items()),
                   "original active guard identity differs")
    for arguments, wanted in ((["rev-parse", "HEAD"], cfg.DEPLOYMENT),
                              (["rev-parse", "HEAD^{tree}"], cfg.TREE),
                              (["status", "--porcelain", "--untracked-files=no"], "")):
        helper.require(helper.git_read(root, *arguments) == wanted, "original tracked checkout differs")
    helper.load_previous(root)

    def save():
        helper.private_json(stage / "receipt.json", receipt)

    verified = False
    try:
        receipt.update(phase="verify_running", verify_runner_pid=os.getpid(),
                       verify_started_at=helper.utc_now(), verify_invocations=1)
        save()
        with (stage / "guard-verify.json").open("x") as out, (stage / "guard-verify.stderr").open("x") as err:
            process = subprocess.Popen([sys.executable, "-B", "scripts/production_release_guard.py",
                "verify", "--url", "https://mezansalla.com"], cwd=root,
                env=helper.host_environment(), stdout=out, stderr=err, start_new_session=True)
            try:
                receipt["guard_verify_pid"] = process.pid
                save()
                exit_code = process.wait()
            except BaseException:
                helper.stop_owned_process(process)
                receipt["interrupted_guard_exit_code"] = process.returncode
                save()
                raise
        receipt.update(verify_exit_code=exit_code, verify_finished_at=helper.utc_now())
        save()
        helper.require(exit_code == 0, "original verifier failed; retain its private stdout/stderr")
        proof, _, proof_sha = read_json(helper, stage / "guard-verify.json")
        helper.require(proof.get("verified") is True and proof.get("checks") == 3
                       and all(proof.get(key) == expected[key] for key in (
                           "source_git_sha", "deployment_git_sha", "release_id", "protocol_version")),
                       "official three-probe proof identity differs")
        final_guard = json.loads(helper.capture_unlogged(
            [sys.executable, "-B", "scripts/production_release_guard.py", "status"]))
        helper.require(final_guard.get("active") is False, "original guard did not close its verified lease")
        receipt.update(phase="production_verified", verified_at=helper.utc_now(), guard_verify=proof,
                       final_guard_status=final_guard, original_proof_sha256=proof_sha)
        verified = True
        save()  # Persist official success before any preservation/restoration step.
        observation = inspect_after_publication(helper, receipt, root)
        if observation["strictly_preserved"]:
            receipt["final_foreign_check"] = {
                "strictly_preserved": True, "foreign_roots": observation["foreign_root_count"],
                "untracked_files": observation["untracked_count"]}
        else:
            receipt.update(phase="production_verified_local_restore_pending",
                           verify_error="preservation_requires_exact_review",
                           preservation_observation=cfg.PRESERVATION_OBSERVATION)
        save()
        # Bind the observation to the final retained receipt bytes. Do not edit
        # that receipt after writing this separate variance observation.
        _, original_bytes, receipt_sha = read_json(helper, stage / "receipt.json")
        observation_path = stage / cfg.PRESERVATION_OBSERVATION
        helper.require(not observation_path.exists() and not observation_path.is_symlink(),
                       "preservation observation already exists; inspect instead of replaying")
        helper.private_json(observation_path, {"observed_at": helper.utc_now(),
            "original_receipt_sha256": receipt_sha, "official_guard_proof_sha256": proof_sha,
            "observation": observation})
        if not observation["strictly_preserved"]:
            print(json.dumps({"phase": receipt["phase"], "official_verified": True,
                "eligible_for_exact_review": observation["eligible_for_exact_review"],
                "backend_restore_required": True}), flush=True)
            return 2
        from restore_original_backend import restore
        restore()
        helper.require((stage / "receipt.json").read_bytes() == original_bytes,
                       "restoration changed the retained original receipt")
        print(json.dumps({"phase": "production_verified_local_backend_restored",
            "source": cfg.SOURCE, "deployment": cfg.DEPLOYMENT,
            "release_id": cfg.RELEASE_ID, "original_proof_sha256": proof_sha}), flush=True)
        return 0
    except BaseException as error:
        # Once the final observation exists, its original receipt is immutable.
        if not (stage / cfg.PRESERVATION_OBSERVATION).exists():
            receipt.update(phase="production_verified_local_restore_pending" if verified else "verify_failed",
                           verify_error=str(error)[:1000], verify_error_type=type(error).__name__,
                           verify_failure_at=helper.utc_now())
            save()
        else:
            failure_path = stage / "postverify-wrapper-failure.json"
            if not failure_path.exists():
                helper.private_json(failure_path, {
                    "failed_at": helper.utc_now(), "source": cfg.SOURCE,
                    "deployment": cfg.DEPLOYMENT, "release_id": cfg.RELEASE_ID,
                    "official_verification_succeeded": verified,
                    "original_receipt_retained_unchanged": True,
                    "error_type": type(error).__name__, "error": str(error)[:1000],
                })
        raise


if __name__ == "__main__":
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt("new verification wrapper interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    raise SystemExit(main())
