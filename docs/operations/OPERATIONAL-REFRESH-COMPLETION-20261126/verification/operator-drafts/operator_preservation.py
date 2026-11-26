"""Read-only evidence-chain and preservation rules for the draft operator.

Credential contents are never read. Only two named post-publication variances
can be eligible for a separate exact-observation approval; no waiver is inferred.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

import operator_config as cfg


def read_json(helper, path, *, expected_sha=None):
    path = Path(path)
    record = helper.metadata(path, path.name)
    helper.require(stat.S_ISREG(record["mode"]) and not path.is_symlink()
                   and path.absolute() == path.resolve(strict=True)
                   and record["size"] <= 8 * 1024 * 1024,
                   "operator evidence is not a bounded regular file")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        helper.require(helper.same_stat(os.fstat(descriptor), record), "evidence changed before read")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read()
        helper.require(helper.same_stat(os.fstat(descriptor), record), "evidence changed during read")
    finally:
        os.close(descriptor)
    digest = hashlib.sha256(raw).hexdigest()
    helper.require(expected_sha is None or digest == expected_sha, "pinned operator evidence hash differs")
    value = json.loads(raw)
    helper.require(type(value) is dict, "operator evidence must be a JSON object")
    return value, raw, digest


def validated_previous(helper, repo):
    stage = Path(repo) / cfg.PREVIOUS_REL
    previous, _, _ = read_json(helper, stage / "receipt.json", expected_sha=cfg.PREVIOUS_RECEIPT_SHA256)
    proof, _, _ = read_json(helper, stage / "guard-verify.json", expected_sha=cfg.PREVIOUS_PROOF_SHA256)
    restored, _, _ = read_json(helper, stage / "restore-continuation.json",
                               expected_sha=cfg.PREVIOUS_CONTINUATION_SHA256)
    helper.require(previous.get("phase") == "production_verified_local_restore_pending"
                   and previous.get("verify_error") == "foreign root metadata changed",
                   "previous original receipt does not match the reviewed retained failure")
    expected = {"source": cfg.OLD_SOURCE, "deployment": cfg.BASE,
                "tree": cfg.OLD_TREE, "release_id": cfg.OLD_RELEASE_ID}
    helper.require(all(previous.get(key) == value for key, value in expected.items()),
                   "previous receipt identity differs")
    helper.require(previous.get("verify_invocations") == 1 and previous.get("verify_exit_code") == 0
                   and previous.get("publication_action_count") == 1
                   and previous.get("publication_version") == cfg.OLD_PUBLICATION_VERSION
                   and previous.get("original_proof_sha256") == cfg.PREVIOUS_PROOF_SHA256
                   and previous.get("guard_verify") == proof,
                   "previous original verification/publication evidence differs")
    helper.require(proof.get("verified") is True and proof.get("checks") == 3
                   and proof.get("protocol_version") == 5
                   and proof.get("source_git_sha") == cfg.OLD_SOURCE
                   and proof.get("deployment_git_sha") == cfg.BASE
                   and proof.get("release_id") == cfg.OLD_RELEASE_ID,
                   "previous official guard proof differs")
    helper.require(restored.get("phase") == "production_verified_local_backend_restored"
                   and restored.get("source") == cfg.OLD_SOURCE
                   and restored.get("deployment") == cfg.BASE
                   and restored.get("release_id") == cfg.OLD_RELEASE_ID
                   and restored.get("original_receipt_sha256") == cfg.PREVIOUS_RECEIPT_SHA256
                   and restored.get("original_guard_proof_sha256") == cfg.PREVIOUS_PROOF_SHA256
                   and restored.get("original_receipt_unchanged") is True
                   and restored.get("original_guard_active") is False
                   and restored.get("development_backend_restored") is True
                   and restored.get("backend_restore_required") is False
                   and restored.get("restored_at") == cfg.PREVIOUS_RESTORED_AT
                   and restored.get("supervisor_after_restore", {}).get("state") == "RUNNING"
                   and restored.get("credential_contents_read_hashed_copied_or_modified") is False
                   and restored.get("variance_actor_attribution") == "not established",
                   "separate previous restoration proof differs")
    for key, count in (("foreign_roots", 59), ("untracked_files", 432),
                       ("untracked_manifest", 432), ("owned_generated", 4)):
        helper.require(type(previous.get(key)) is list and len(previous[key]) == count,
                       "previous preservation cohort differs: " + key)
    files = previous["untracked_files"]
    helper.require(len(set(files)) == len(files)
                   and [row["path"] for row in previous["untracked_manifest"]] == files,
                   "previous untracked manifest order differs")
    for path in files:
        helper.relative_path(path)
    for name in ("foreign_roots", "owned_generated"):
        rows = previous[name]
        helper.require(all(type(row) is dict and set(helper.META_KEYS) <= set(row) for row in rows)
                       and len({helper.relative_path(row["path"]) for row in rows}) == len(rows),
                       "previous root metadata differs")
    helper.require({row["path"] for row in previous["owned_generated"]} == set(cfg.GENERATED),
                   "previous owned generated scope differs")
    helper.require(not ({row["path"] for row in previous["foreign_roots"]} & set(cfg.GENERATED)),
                   "previous foreign/generated ownership overlaps")
    credential = restored.get("credential_metadata_variance", {})
    bookkeeping = restored.get("bookkeeping_variance", {})
    old_roots = {row["path"]: row for row in previous["foreign_roots"]}
    old_files = {row["path"]: row for row in previous["untracked_manifest"]}
    helper.require(credential.get("before") == old_roots.get(cfg.CREDENTIAL)
                   and credential.get("after", {}).get("path") == cfg.CREDENTIAL
                   and restored.get("credential_current_metadata_only", {}).get("path") == cfg.CREDENTIAL
                   and bookkeeping.get("before") == old_files.get(cfg.BOOKKEEPING)
                   and bookkeeping.get("after", {}).get("path") == cfg.BOOKKEEPING
                   and bookkeeping.get("original_prefix_preserved") is True
                   and bookkeeping.get("appended_bytes") == 378
                   and bookkeeping["after"]["size"] - bookkeeping["before"]["size"] == 378,
                   "previous restoration variance chain differs")
    helper.require(restored.get("untracked_cohort_unchanged") == 432
                   and restored.get("untracked_records_identical") == 431
                   and restored.get("foreign_roots_unchanged") == 58,
                   "previous continuation preservation counts differ")
    backup = previous.get("current_generated_backup", {})
    helper.require(backup.get("path") == str(Path(repo) / cfg.DEPENDENCY_ARCHIVE_REL)
                   and type(backup.get("sha256")) is str and len(backup["sha256"]) == 64,
                   "previous immutable backup pointer differs")
    return previous, restored


def prefix_digest(helper, path, before, after):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        helper.require(helper.same_stat(os.fstat(descriptor), after), "bookkeeping changed before prefix read")
        digest, remaining = hashlib.sha256(), before["size"]
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            while remaining:
                block = stream.read(min(1024 * 1024, remaining))
                helper.require(bool(block), "bookkeeping prefix is truncated")
                digest.update(block)
                remaining -= len(block)
        helper.require(helper.same_stat(os.fstat(descriptor), after), "bookkeeping changed during prefix read")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def require_complete_baseline(helper, receipt, repo):
    previous = helper.load_previous(repo)
    expected_roots = [row["path"] for row in previous["foreign_roots"]]
    expected_files = previous["untracked_files"]
    roots, files, records = (receipt.get("foreign_roots"), receipt.get("untracked_files"),
                             receipt.get("untracked_manifest"))
    helper.require(type(roots) is list and len(roots) == 59
                   and all(type(row) is dict and set(helper.META_KEYS) <= set(row) for row in roots)
                   and [row["path"] for row in roots] == expected_roots
                   and all(type(row[key]) is int for row in roots for key in helper.META_KEYS if key != "path")
                   and len(set(expected_roots)) == 59, "new foreign-root baseline is incomplete or reordered")
    helper.require(type(files) is list and files == expected_files and len(files) == 432
                   and len(set(files)) == 432 and type(records) is list and len(records) == 432
                   and all(type(row) is dict for row in records)
                   and [row.get("path") for row in records] == files,
                   "new untracked baseline is incomplete or reordered")
    fields = {"path", "device", "inode", "mode", "uid", "gid", "size", "mtime_ns", "nlink"}
    prior_records = {row["path"]: row for row in previous["untracked_manifest"]}
    for row in records:
        helper.require(fields <= set(row)
                       and all(type(row[key]) is int for key in fields - {"path"})
                       and row["size"] >= 0 and row["nlink"] >= 1
                       and (stat.S_ISREG(row["mode"]) or stat.S_ISLNK(row["mode"])),
                       "new untracked metadata schema differs")
        helper.relative_path(row["path"])
        if helper.credential_path(row["path"]):
            helper.require("sha256" not in row, "credential digest is forbidden in the fresh baseline")
            if stat.S_ISREG(row["mode"]):
                helper.require(row.get("metadata_only") is True, "credential baseline is not metadata-only")
        elif stat.S_ISREG(row["mode"]) and "sha256" in prior_records[row["path"]]:
            helper.require(type(row.get("sha256")) is str
                           and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None,
                           "fresh baseline silently dropped a required content digest")
    credential = receipt.get("credential_metadata_only")
    helper.require(receipt.get("credential_contents_read_hashed_copied_or_modified") is False
                   and type(credential) is dict and set(credential) == fields
                   and credential["path"] == cfg.CREDENTIAL
                   and all(type(credential[key]) is int for key in fields - {"path"})
                   and stat.S_ISREG(credential["mode"]) and credential["size"] >= 0
                   and credential["nlink"] >= 1
                   and {key: credential[key] for key in helper.META_KEYS} ==
                       next((row for row in roots if row["path"] == cfg.CREDENTIAL), None),
                   "fresh credential metadata baseline is incomplete or inconsistent")


def inspect_after_publication(helper, receipt, repo):
    """Report exact observations; never authorize a variance by itself."""
    repo = Path(repo)
    require_complete_baseline(helper, receipt, repo)
    files = helper.git_read(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    helper.require(files == receipt["untracked_files"], "untracked preservation cohort changed")
    roots = helper.fresh_foreign_roots(repo, receipt["foreign_roots"])
    records = [helper.fresh_untracked_record(repo, row) for row in receipt["untracked_manifest"]]
    root_changes = [{"before": before, "after": after}
                    for before, after in zip(receipt["foreign_roots"], roots) if before != after]
    file_changes = [{"before": before, "after": after}
                    for before, after in zip(receipt["untracked_manifest"], records) if before != after]
    env_before = receipt["credential_metadata_only"]
    env_after = helper.metadata(repo / cfg.CREDENTIAL, cfg.CREDENTIAL)
    helper.require(stat.S_ISREG(env_after["mode"]) and not (repo / cfg.CREDENTIAL).is_symlink(),
                   "credential is no longer a regular metadata-only file")
    violations, eligible = [], []
    if any(row["before"]["path"] != cfg.CREDENTIAL for row in root_changes):
        violations.append("other_foreign_root_changed")
    if env_before != env_after:
        # Only metadata replacement of this exact same-sized credential is a
        # review candidate. Even accepted metadata cannot prove content equality.
        stable = ("path", "device", "mode", "uid", "gid", "size", "nlink")
        if any(env_before[key] != env_after[key] for key in stable) or env_before["inode"] == env_after["inode"]:
            violations.append("credential_change_is_not_the_reviewed_replacement_pattern")
        else:
            eligible.append({"kind": "credential_metadata_replacement", "path": cfg.CREDENTIAL,
                             "before": env_before, "after": env_after,
                             "contents_read_hashed_copied_or_modified": False,
                             "content_equality_verified": False})
    if root_changes and env_before == env_after:
        violations.append("foreign_root_change_has_no_matching_credential_observation")
    for change in file_changes:
        before, after = change["before"], change["after"]
        if before["path"] != cfg.BOOKKEEPING:
            violations.append("other_untracked_record_changed")
            continue
        appended = after["size"] - before["size"]
        if (any(before[key] != after[key] for key in (*helper.META_KEYS, "nlink"))
                or not 0 < appended <= cfg.MAX_BOOKKEEPING_APPEND_BYTES
                or "sha256" not in before or "sha256" not in after
                or not stat.S_ISREG(after["mode"])):
            violations.append("bookkeeping_change_is_not_bounded_append_only")
            continue
        prefix = prefix_digest(helper, repo / cfg.BOOKKEEPING, before, after)
        if prefix != before["sha256"]:
            violations.append("bookkeeping_original_prefix_changed")
            continue
        eligible.append({"kind": "bookkeeping_append", "path": cfg.BOOKKEEPING,
                         "before": before, "after": after, "original_prefix_sha256": prefix,
                         "original_prefix_preserved": True, "appended_bytes": appended})
    strict = not root_changes and not file_changes and env_before == env_after
    return {"schema": "operational_preservation_observation_v1", "source": receipt["source"],
            "deployment": receipt["deployment"], "release_id": receipt["release_id"],
            "strictly_preserved": strict, "eligible_for_exact_review": bool(eligible) and not violations,
            "foreign_root_count": len(roots), "untracked_count": len(files),
            "foreign_root_changes": root_changes, "untracked_changes": file_changes,
            "credential_metadata_only_before": env_before, "credential_metadata_only_after": env_after,
            "eligible_variances": eligible, "violations": violations,
            "variance_actor_attribution": "not established"}


def require_exact_approval(helper, stage, receipt_sha, proof_sha, observed):
    saved, _, observation_sha = read_json(helper, Path(stage) / cfg.PRESERVATION_OBSERVATION)
    helper.require(saved.get("observation") == observed
                   and saved.get("original_receipt_sha256") == receipt_sha
                   and saved.get("official_guard_proof_sha256") == proof_sha,
                   "preservation changed since the exact saved observation")
    helper.require(observed["eligible_for_exact_review"] and not observed["violations"],
                   "preservation variance is outside the two eligible paths")
    approval, _, approval_sha = read_json(helper, Path(stage) / cfg.PRESERVATION_APPROVAL)
    helper.require(approval.get("schema") == "operational_preservation_exact_approval_v1"
                   and approval.get("reviewed") is True and approval.get("actor") == cfg.ACTOR
                   and approval.get("source") == cfg.SOURCE and approval.get("deployment") == cfg.DEPLOYMENT
                   and approval.get("release_id") == cfg.RELEASE_ID
                   and approval.get("original_receipt_sha256") == receipt_sha
                   and approval.get("official_guard_proof_sha256") == proof_sha
                   and approval.get("preservation_observation_sha256") == observation_sha
                   and approval.get("approved_variances") == observed["eligible_variances"]
                   and approval.get("variance_actor_attribution") == "not established"
                   and approval.get("credential_contents_verified") is False,
                   "exact independent preservation approval is missing or differs")
    return {"approval_sha256": approval_sha, "observation_sha256": observation_sha}
