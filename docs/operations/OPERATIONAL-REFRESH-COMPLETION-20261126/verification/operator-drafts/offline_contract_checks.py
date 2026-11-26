"""Tiny synthetic contract probes only; no /app access or subprocess allowed."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import operator_config as cfg
import operator_preservation as preservation
import owned_output_backup as backup
import prepare_original_host as helper
import restore_original_backend as restore
import verify_original_host as verify


def forbidden(*args, **kwargs):
    raise AssertionError("offline probe attempted a host subprocess")


def rejected(operation):
    try:
        operation()
    except helper.PreparationError:
        return
    raise AssertionError("expected a closed rejection")


def dependency_case(old_links, current_links, *, external=False, mutate=False):
    with tempfile.TemporaryDirectory(prefix="operator-fixture-", dir=HERE) as temporary:
        repo = Path(temporary)
        dependency = repo / cfg.DEPENDENCY_ROOT
        dependency.mkdir(parents=True)
        a, b = dependency / "a", dependency / "b"
        a.write_bytes(b"synthetic dependency payload")
        os.link(a, b) if old_links else b.write_bytes(b"synthetic dependency payload")
        archive = repo / cfg.DEPENDENCY_ARCHIVE_REL
        archive.parent.mkdir(parents=True)
        with tarfile.open(archive, "w:gz", dereference=False) as saved:
            saved.add(dependency, arcname=cfg.DEPENDENCY_ROOT)
        b.unlink()
        os.link(a, b) if current_links else b.write_bytes(b"synthetic dependency payload")
        if external:
            os.link(a, repo / "outside-dependency")
        if mutate:
            b.write_bytes(b"changed synthetic payload")
        runner = SimpleNamespace(repo=repo, previous={"current_generated_backup": {
            "path": str(archive), "sha256": hashlib.sha256(archive.read_bytes()).hexdigest()}})
        records = helper.tree_inventory(repo, (cfg.DEPENDENCY_ROOT,))
        return backup.verify_dependency_reuse(runner, helper, records)


def preservation_fixture(repo):
    credential = repo / cfg.CREDENTIAL
    credential.parent.mkdir(parents=True)
    credential.write_bytes(b"synthetic fixture only")
    book = repo / cfg.BOOKKEEPING
    book.parent.mkdir(parents=True)
    book.write_bytes(b"synthetic original prefix\n")
    note = repo / "notes.txt"
    note.write_bytes(b"synthetic unrelated user file")
    paths = [cfg.BOOKKEEPING, "notes.txt"]
    for index in range(430):
        path = "untracked-" + str(index).zfill(3) + ".txt"
        (repo / path).write_bytes(b"synthetic")
        paths.append(path)
    records = []
    for path in paths:
        record = helper.metadata(repo / path, path)
        record["sha256"] = helper.safe_regular_digest(repo / path, record)
        records.append(record)
    env = helper.metadata(credential, cfg.CREDENTIAL)
    roots = [{key: env[key] for key in helper.META_KEYS}]
    for index in range(58):
        path = "foreign-" + str(index).zfill(2)
        (repo / path).mkdir()
        record = helper.metadata(repo / path, path)
        roots.append({key: record[key] for key in helper.META_KEYS})
    receipt = {"source": cfg.SOURCE, "deployment": cfg.DEPLOYMENT, "release_id": cfg.RELEASE_ID,
        "foreign_roots": roots, "credential_contents_read_hashed_copied_or_modified": False,
        "credential_metadata_only": env, "untracked_files": paths, "untracked_manifest": records}
    return receipt


def preservation_case(kind):
    with tempfile.TemporaryDirectory(prefix="operator-fixture-", dir=HERE) as temporary:
        repo = Path(temporary)
        receipt = preservation_fixture(repo)
        previous = deepcopy(receipt)
        book, credential = repo / cfg.BOOKKEEPING, repo / cfg.CREDENTIAL
        if kind in {"append", "approved", "approval_mismatch"}:
            with book.open("ab") as stream:
                stream.write(b"synthetic appendix\n")
        elif kind == "credential_replacement":
            replacement = repo / "synthetic-replacement"
            replacement.write_bytes(b"synthetic fixture only")
            os.replace(replacement, credential)
        elif kind == "credential_inplace":
            credential.write_bytes(b"changed fixture text!!")
        elif kind == "prefix_changed":
            book.write_bytes(b"different original prefix plus suffix\n")
        elif kind == "append_too_large":
            with book.open("ab") as stream:
                stream.write(b"x" * (cfg.MAX_BOOKKEEPING_APPEND_BYTES + 1))
        elif kind == "unrelated_change":
            (repo / "notes.txt").write_bytes(b"changed unrelated file")
        elif kind == "truncated_roots":
            receipt["foreign_roots"].pop()
        elif kind == "truncated_manifest":
            receipt["untracked_manifest"].pop()
        elif kind == "missing_credential_field":
            receipt["credential_metadata_only"].pop("nlink")

        original_open = os.open

        def no_credential_reads(path, flags, *args, **kwargs):
            if Path(path) == credential:
                raise AssertionError("credential contents were opened by preservation inspection")
            return original_open(path, flags, *args, **kwargs)

        with patch.object(helper, "git_read", return_value="\n".join(receipt["untracked_files"])), \
                patch.object(helper, "load_previous", return_value=previous), \
                patch.object(os, "open", no_credential_reads):
            observation = preservation.inspect_after_publication(helper, receipt, repo)
            if kind in {"approved", "approval_mismatch"}:
                stage = repo / "new-stage"
                stage.mkdir()
                receipt_sha, proof_sha = "0" * 64, "1" * 64
                helper.private_json(stage / cfg.PRESERVATION_OBSERVATION, {
                    "original_receipt_sha256": receipt_sha, "official_guard_proof_sha256": proof_sha,
                    "observation": observation})
                _, _, observed_sha = preservation.read_json(helper, stage / cfg.PRESERVATION_OBSERVATION)
                approval = {"schema": "operational_preservation_exact_approval_v1", "reviewed": True,
                    "actor": cfg.ACTOR, "source": cfg.SOURCE, "deployment": cfg.DEPLOYMENT,
                    "release_id": cfg.RELEASE_ID, "original_receipt_sha256": receipt_sha,
                    "official_guard_proof_sha256": proof_sha,
                    "preservation_observation_sha256": observed_sha,
                    "approved_variances": deepcopy(observation["eligible_variances"]),
                    "variance_actor_attribution": "not established", "credential_contents_verified": False}
                if kind == "approval_mismatch":
                    approval["approved_variances"][0]["appended_bytes"] += 1
                helper.private_json(stage / cfg.PRESERVATION_APPROVAL, approval)
                operation = lambda: preservation.require_exact_approval(
                    helper, stage, receipt_sha, proof_sha, observation)
                if kind == "approval_mismatch":
                    rejected(operation)
                else:
                    operation()
            return observation


def main():
    outputs = []
    with patch("subprocess.Popen", forbidden), patch("subprocess.run", forbidden), \
            patch("subprocess.check_output", forbidden):
        for old, current in ((False, False), (True, True)):
            dependency_case(old, current)
            outputs.append({"case": "matching_hardlinks" if old else "matching_independent_files", "passed": True})
        for old, current in ((True, False), (False, True)):
            rejected(lambda: dependency_case(old, current))
            outputs.append({"case": "hardlink_mismatch_" + str(old) + "_to_" + str(current), "passed": True})
        with patch.object(backup, "generated_digest", side_effect=AssertionError("payload read before external-link rejection")):
            rejected(lambda: dependency_case(False, False, external=True))
        outputs.append({"case": "external_hardlink_rejected", "passed": True})
        rejected(lambda: dependency_case(False, False, mutate=True))
        outputs.append({"case": "changed_dependency_bytes_rejected", "passed": True})
        for kind in ("unchanged", "append", "credential_replacement", "credential_inplace",
                     "prefix_changed", "append_too_large", "unrelated_change", "approved", "approval_mismatch"):
            value = preservation_case(kind)
            if kind == "unchanged":
                assert value["strictly_preserved"] and not value["eligible_for_exact_review"]
            elif kind in {"append", "credential_replacement", "approved", "approval_mismatch"}:
                assert not value["strictly_preserved"] and value["eligible_for_exact_review"]
            else:
                assert value["violations"] and not value["eligible_for_exact_review"]
            outputs.append({"case": "preservation_" + kind, "passed": True})
        for kind in ("truncated_roots", "truncated_manifest", "missing_credential_field"):
            rejected(lambda: preservation_case(kind))
            outputs.append({"case": "preservation_" + kind + "_rejected", "passed": True})
        for name, operation in (("prepare", lambda: helper.main(["--deployment-sha", "0" * 40])),
                                ("verify", verify.main), ("restore", restore.restore)):
            rejected(operation)
            outputs.append({"case": "draft_" + name + "_refused_before_subprocess", "passed": True})
    result = {"scope": "synthetic temp trees and in-process draft refusal only",
              "host_access": False, "subprocesses_permitted": False, "checks": outputs,
              "production_credential_contents_read": False}
    target = HERE / "offline_contract_checks_result.json"
    with target.open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"passed": len(outputs), "result": str(target)}, sort_keys=True))


if __name__ == "__main__":
    main()
