"""Draft low-space backup plan: exact old dependency payload plus fresh outputs.

Only the new task stage is written. No generated output or old backup is removed
here; the unchanged governed adapter remains the sole later output remover.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tarfile

import operator_config as cfg


def require_internal_links(helper, records):
    groups, links = {}, {}
    for row in records:
        if not stat.S_ISREG(row["mode"]):
            continue
        identity = (row["device"], row["inode"])
        groups[identity] = groups.get(identity, 0) + 1
        helper.require(identity not in links or links[identity] == row["nlink"],
                       "owned output link metadata changed during inventory")
        links[identity] = row["nlink"]
    helper.require(all(count == links[identity] for identity, count in groups.items()),
                   "owned output hardlink escapes the inventoried generated scope")


def generated_digest(helper, path, record):
    helper.require(not helper.protected_archive_path(record["path"]), "protected generated entry refused")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        helper.require(helper.same_stat(os.fstat(descriptor), record), "generated file changed before read")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        helper.require(helper.same_stat(os.fstat(descriptor), record), "generated file changed during read")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def current_payload(helper, repo, records):
    result, groups, links, files = {}, {}, {}, []
    for row in records:
        path = row["path"]
        if path != cfg.DEPENDENCY_ROOT and not path.startswith(cfg.DEPENDENCY_ROOT + "/"):
            continue
        kind = ("directory" if stat.S_ISDIR(row["mode"]) else
                "symlink" if stat.S_ISLNK(row["mode"]) else "file")
        value = {"kind": kind, "mode": stat.S_IMODE(row["mode"]), "uid": row["uid"], "gid": row["gid"]}
        if kind == "file":
            value["size"] = row["size"]
            identity = (row["device"], row["inode"])
            groups.setdefault(identity, []).append(path)
            helper.require(identity not in links or links[identity] == row["nlink"],
                           "dependency hardlink metadata changed during inventory")
            links[identity] = row["nlink"]
            files.append(row)
        elif kind == "symlink":
            value["target"] = row["symlink_target"]
        helper.require(path not in result, "duplicate current dependency member")
        result[path] = value
    for identity, paths in groups.items():
        helper.require(links[identity] == len(paths),
                       "dependency hardlink escapes the inventoried subtree")
        for path in paths:
            result[path]["hardlink_members"] = sorted(paths)
    # Reject every external alias using metadata before opening any current
    # dependency payload, including aliases to otherwise unknown credentials.
    for row in files:
        result[row["path"]]["sha256"] = generated_digest(helper, repo / row["path"], row)
    helper.require(cfg.DEPENDENCY_ROOT in result, "dependency root inventory missing")
    return result


def verify_dependency_reuse(runner, helper, records):
    repo = runner.repo
    path = repo / cfg.DEPENDENCY_ARCHIVE_REL
    before = helper.metadata(path, cfg.DEPENDENCY_ARCHIVE_REL)
    helper.require(stat.S_ISREG(before["mode"]) and not path.is_symlink()
                   and path.absolute() == path.resolve(strict=True), "old backup is not a real regular file")
    expected = runner.previous["current_generated_backup"]
    helper.require(expected["path"] == str(path)
                   and helper.safe_regular_digest(path, before) == expected["sha256"],
                   "old immutable dependency archive hash differs")
    current = current_payload(helper, repo, records)
    archived, seen, groups, group_root = {}, set(), {}, {}
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        helper.require(helper.same_stat(os.fstat(descriptor), before), "old backup changed before stream")
        with os.fdopen(descriptor, "rb", closefd=False) as raw:
            with gzip.GzipFile(fileobj=raw, mode="rb") as decoded:
                with tarfile.open(fileobj=decoded, mode="r|") as archive:
                    for member in archive:
                        name = helper.relative_path(member.name)
                        helper.require(name not in seen, "duplicate member in old backup")
                        seen.add(name)
                        if name != cfg.DEPENDENCY_ROOT and not name.startswith(cfg.DEPENDENCY_ROOT + "/"):
                            continue
                        helper.require(not helper.protected_archive_path(name), "protected old dependency entry refused")
                        kind = ("directory" if member.isdir() else "symlink" if member.issym()
                                else "file" if member.isfile() or member.islnk() else None)
                        helper.require(kind is not None, "special member in old dependency backup")
                        value = {"kind": kind, "mode": member.mode, "uid": member.uid, "gid": member.gid}
                        if member.isfile():
                            groups[name] = [name]
                            group_root[name] = name
                            content = archive.extractfile(member)
                            helper.require(content is not None, "dependency archive payload missing")
                            digest, size = hashlib.sha256(), 0
                            with content:
                                for block in iter(lambda: content.read(1024 * 1024), b""):
                                    digest.update(block)
                                    size += len(block)
                            helper.require(size == member.size, "dependency archive payload truncated")
                            value.update(size=size, sha256=digest.hexdigest())
                        elif member.islnk():
                            target = helper.relative_path(member.linkname)
                            linked = archived.get(target)
                            helper.require(linked is not None and linked["kind"] == "file",
                                           "dependency hardlink is outside the verified prior subtree")
                            group_root[name] = group_root[target]
                            groups[group_root[name]].append(name)
                            value.update(size=linked["size"], sha256=linked["sha256"])
                        elif member.issym():
                            value["target"] = member.linkname
                        archived[name] = value
                # Drain the gzip trailer to validate CRC even after tar EOF.
                for _ in iter(lambda: decoded.read(1024 * 1024), b""):
                    pass
        helper.require(helper.same_stat(os.fstat(descriptor), before), "old backup changed during stream")
    finally:
        os.close(descriptor)
    for paths in groups.values():
        for name in paths:
            archived[name]["hardlink_members"] = sorted(paths)
    helper.require(archived == current, "current dependencies differ from immutable backup; reuse refused")
    helper.require(helper.metadata(path, cfg.DEPENDENCY_ARCHIVE_REL) == before,
                   "old dependency backup metadata changed")
    digest = hashlib.sha256(json.dumps(current, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"path": cfg.DEPENDENCY_ARCHIVE_REL, "archive_sha256": expected["sha256"],
            "subtree": cfg.DEPENDENCY_ROOT, "members": len(current),
            "payload_manifest_sha256": digest,
            "equality": "complete paths/types/modes/uid/gid/symlink-targets/hardlink-groups/file-sizes/file-SHA256",
            "fresh_metadata_manifest": "generated-before-manifest.json.gz",
            "old_backup_unchanged": True}


def write_manifest(runner, helper, records):
    path = runner.stage / "generated-before-manifest.json.gz"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb", buffering=0) as raw:
        writer = helper.ReserveWriter(raw, runner.stage)
        with gzip.GzipFile(filename="", mode="wb", fileobj=writer, mtime=0) as compressed:
            for chunk in json.JSONEncoder(sort_keys=True, separators=(",", ":")).iterencode(records):
                compressed.write(chunk.encode())
        raw.flush()
        os.fsync(raw.fileno())
    return {"path": path.name, "sha256": writer.digest.hexdigest(), "bytes": writer.bytes,
            "members": len(records)}


def preserve_current_outputs(runner):
    helper = sys.modules[runner.__class__.__module__]
    runner.guard_inactive("guard-before-backup")
    runner.assert_tracked(cfg.BASE)
    helper.check_foreign(runner.receipt, runner.repo)
    runner.phase("generated_backup")
    records = helper.tree_inventory(runner.repo, cfg.GENERATED)
    helper.require(not any(helper.protected_archive_path(row["path"]) for row in records),
                   "generated backup contains a protected configuration entry")
    require_internal_links(helper, records)
    env = helper.metadata(runner.repo / cfg.CREDENTIAL, cfg.CREDENTIAL)
    helper.require(not any((row["device"], row["inode"]) == (env["device"], env["inode"]) for row in records),
                   "generated output aliases credential configuration")
    reused = verify_dependency_reuse(runner, helper, records)
    helper.require(helper.tree_inventory(runner.repo, cfg.GENERATED) == records,
                   "generated inventory changed during dependency comparison")
    manifest = write_manifest(runner, helper, records)
    small = [row for row in records if any(row["path"] == root or row["path"].startswith(root + "/")
                                         for root in cfg.FRESH_OUTPUTS)]
    partial = runner.stage / "generated-small-before.tar.gz.partial"
    final = runner.stage / "generated-small-before.tar.gz"
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb", buffering=0) as stream:
        writer = helper.ReserveWriter(stream, runner.stage)
        with gzip.GzipFile(filename="", mode="wb", fileobj=writer, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", dereference=False) as archive:
                for record in small:
                    path = runner.repo / record["path"]
                    helper.require(helper.metadata(path, record["path"]) == {
                        key: value for key, value in record.items() if key != "symlink_target"},
                        "small generated entry changed before archive")
                    member = archive.gettarinfo(str(path), arcname=record["path"])
                    if member.isfile():
                        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                        with os.fdopen(fd, "rb") as content:
                            helper.require(helper.same_stat(os.fstat(content.fileno()), record),
                                           "small output changed before opening")
                            archive.addfile(member, content)
                            helper.require(helper.same_stat(os.fstat(content.fileno()), record),
                                           "small output changed while archiving")
                    else:
                        archive.addfile(member)
        stream.flush()
        os.fsync(stream.fileno())
    helper.require(helper.tree_inventory(runner.repo, cfg.GENERATED) == records,
                   "generated cohort changed while being backed up")
    with gzip.open(partial, "rb") as decoded:
        with tarfile.open(fileobj=decoded, mode="r|") as archive:
            members = list(archive)
        for _ in iter(lambda: decoded.read(1024 * 1024), b""):
            pass
    helper.require([member.name for member in members] == [row["path"] for row in small],
                   "fresh output archive membership differs")
    helper.require(shutil.disk_usage(runner.stage).free >= cfg.RESERVE,
                   "fresh backup crossed the 128 MiB reserve")
    os.rename(partial, final)
    runner.receipt["current_generated_backup"] = {
        "strategy": "verified_dependency_payload_reuse_plus_fresh_small_outputs",
        "dependency": reused, "fresh_inventory": manifest,
        "fresh_outputs": {"path": str(final), "bytes": writer.bytes,
                          "sha256": writer.digest.hexdigest(), "members": len(small)},
        "free_bytes_after": shutil.disk_usage(runner.stage).free,
        "no_output_removed_before_complete_verification": True,
    }
    helper.check_foreign(runner.receipt, runner.repo)
    runner.phase("generated_backup_verified", complete=True)
