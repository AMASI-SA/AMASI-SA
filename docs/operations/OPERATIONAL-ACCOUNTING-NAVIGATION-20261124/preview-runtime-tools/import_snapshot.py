#!/usr/bin/env python3
"""Validate a complete private export, then copy it only to own loopback Mongo.

No source URI or source credential input exists. Original exported BSON bytes
are inserted directly; this program never projects, merges or repairs data.
"""
from __future__ import annotations
import argparse
import ast
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import types
import uuid

from private_common import (AUDIT_COLLECTION, BINDING_COLLECTION, MONGO_URL, REPLICA_SET, SOURCE_SHA,
    STATE_COLLECTION, TOOL_ROOT, RuntimeBlocked, digest, expected_binding, load_export, load_json, owner_is_valid, validate_config)

ARCHIVE_SHA = "a8e08fdd573fae56f184e1c10070bde994845f55bdb1ac9c81ba3dd46b833000"
CLAIM_COLLECTION = "__pr2095_import_claim"
MAX_DOCUMENT = 16 * 1024 * 1024
MAX_DOCUMENTS = 500000
PURE_FUNCTIONS = {"content_key", "manifest", "validated_events", "unpack_issues", "unpack_financial_snapshot", "_audit_metadata"}
PURE_CONSTANTS = {"AUDIT_PAGES", "MARKER", "ISSUES", "ISSUE_MARK", "MAX_ISSUE_BSON", "SNAPSHOT", "SNAPSHOT_MARK", "SNAPSHOT_FIELDS", "MAX_SNAPSHOT_BSON"}
SECRET_KEYS = {"password", "password_hash", "hashed_password", "password_digest", "access_token", "refresh_token",
    "api_key", "apikey", "api_secret", "client_secret", "jwt_secret", "jwt_signing_key", "private_key", "secret_key",
    "authorization", "cookie", "cookies", "session_cookie", "session_token", "session_key", "mfa_secret", "totp_secret",
    "otp_secret", "recovery_codes", "mfa_backup_codes", "mfa_recovery_codes", "card_number", "cvv", "cvc", "card_cvc",
    "card_cvv", "payment_token", "bank_password", "mongo_url", "mongodb_uri", "mongodb_url", "bearer_token", "oauth_token"}


def require(value, code):
    if not value:
        raise RuntimeBlocked(code)


class Budget:
    def __init__(self, seconds=1800, maximum_bytes=2 * 1024**3):
        require(1 <= seconds <= 1800 and 1 <= maximum_bytes <= 2 * 1024**3, "import_limits_invalid")
        self.deadline = time.monotonic() + seconds
        self.maximum_bytes, self.total_bytes = maximum_bytes, 0

    def check(self):
        require(time.monotonic() < self.deadline, "import_deadline_exceeded")

    def remaining(self):
        self.check()
        return max(0.001, min(15.0, self.deadline - time.monotonic()))


def open_directory(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts, "import_absolute_path_required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise RuntimeBlocked("import_directory_invalid") from None


class ExportFiles:
    def __init__(self, directory):
        self.fd = open_directory(directory)
        info = os.fstat(self.fd)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            self.close()
            raise RuntimeBlocked("export_directory_not_private")

    def close(self):
        if self.fd is not None:
            os.close(self.fd); self.fd = None

    def read(self, record):
        name = record.get("file")
        require(isinstance(name, str) and (name == "state.bson" or re.fullmatch(r"(?:audit|context)/[A-Za-z0-9_-]{1,160}\.bson", name)), "export_file_path_invalid")
        size, expected = record.get("bytes"), record.get("sha256")
        require(type(size) is int and 5 <= size <= MAX_DOCUMENT and isinstance(expected, str)
                and re.fullmatch(r"[a-f0-9]{64}", expected), "export_file_record_invalid")
        pieces = name.split("/")
        parent = self.fd
        try:
            if len(pieces) == 2:
                parent = os.open(pieces[0], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
            fd = os.open(pieces[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_size == size and info.st_uid == os.getuid()
                        and stat.S_IMODE(info.st_mode) == 0o600, "export_file_not_private_regular")
                data = stream.read(size + 1)
            require(len(data) == size and digest(data) == expected, "export_file_hash_mismatch")
            return data
        except OSError:
            raise RuntimeBlocked("export_file_unavailable") from None
        finally:
            if parent != self.fd:
                os.close(parent)


def archive_validator(source_root, budget):
    raw = (Path(source_root) / "backend" / "operational_balance_archive.py").read_bytes()
    require(digest(raw) == ARCHIVE_SHA, "exact_archive_validator_required")
    selected, names = [], set()
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Import) and all(alias.name == "zlib" for alias in node.names):
            selected.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module in {"hashlib", "bson", "bson.errors"}:
            selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "ArchiveError":
            selected.append(node)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in PURE_CONSTANTS:
            selected.append(node); names.add(node.targets[0].id)
        elif isinstance(node, ast.FunctionDef) and node.name in PURE_FUNCTIONS:
            selected.append(node); names.add(node.name)
    require(names == PURE_FUNCTIONS | PURE_CONSTANTS, "archive_validator_contract_mismatch")
    module = types.ModuleType("private_import_archive_validation")
    module.__dict__["_checkpoint"] = budget.check
    exec(compile(ast.Module(body=selected, type_ignores=[]), "verified_archive_validation", "exec"), module.__dict__)
    return module


def reject_secrets(value, budget):
    from bson.code import Code
    pending, seen = [(value, 0)], 0
    while pending:
        row, depth = pending.pop(); seen += 1
        require(depth <= 64 and seen <= 10000000, "import_content_scan_limit")
        if seen % 1024 == 0: budget.check()
        if isinstance(row, dict):
            for key, child in row.items():
                require(isinstance(key, str), "import_document_key_invalid")
                name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower().replace("-", "_")
                require(name not in SECRET_KEYS or child in (None, "", [], {}), "import_secret_material_present")
                pending.append((child, depth + 1))
        elif isinstance(row, (list, tuple)):
            pending.extend((child, depth + 1) for child in row)
        elif isinstance(row, str):
            require(not re.search(r"mongodb(?:\+srv)?://|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", row), "import_secret_material_present")
            if isinstance(row, Code) and row.scope is not None: pending.append((row.scope, depth + 1))


def matches_query(document, query):
    for key, expected in query.items():
        actual = document.get(key)
        if isinstance(expected, dict):
            if set(expected) == {"$ne"}:
                if actual == expected["$ne"]: return False
            elif set(expected) == {"$in"}:
                if actual not in expected["$in"]: return False
            else:
                raise RuntimeBlocked("import_fixed_query_contract_invalid")
        elif actual != expected:
            return False
    return True


def check_projection(document, projection):
    if projection is None: return
    tree = {}
    for field, included in projection.items():
        require(included == 1, "import_projection_invalid")
        branch = tree
        parts = field.split(".")
        for part in parts[:-1]:
            require(branch.get(part) is not True, "import_projection_invalid")
            branch = branch.setdefault(part, {})
        branch[parts[-1]] = True
    def visit(row, allowed):
        if allowed is True: return
        if isinstance(row, dict):
            require(set(row) <= set(allowed), "import_projection_extra_field")
            for key, child in row.items(): visit(child, allowed[key])
        elif isinstance(row, list):
            for child in row: visit(child, allowed)
    visit(document, tree)


def check_order_output(document, contract, budget):
    """Validate the reviewed nested output policy without altering BSON values."""
    policy = contract["order_output_policy"]
    shape = contract["order_shape_policy"]
    current_fields = set(shape["current_fields"])
    private_keys = set(shape["private_observation_keys"])
    selections = set(shape["selection_fields"])
    receipt = {"truthiness_checks": 0, "exact_selection_values": 0, "exact_current_observations": 0}
    visits = 0

    def current(value):
        require(value is None or isinstance(value, dict) and set(value) <= current_fields, "import_order_current_shape_invalid")
        pending, examined = [value], 0
        while pending:
            row = pending.pop(); examined += 1
            require(examined <= 2000000, "import_order_shape_limit")
            if examined % 256 == 0: budget.check()
            if isinstance(row, dict):
                require(not any(re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower() in private_keys for key in row), "import_order_private_observation_field")
                pending.extend(row.values())
            elif isinstance(row, list): pending.extend(row)

    def visit(value, rule, path=()):
        nonlocal visits
        visits += 1
        require(len(path) <= 64 and visits <= 2000000, "import_order_shape_limit")
        if visits % 256 == 0: budget.check()
        if rule in ("exact", "current"):
            if rule == "current":
                current(value); receipt["exact_current_observations"] += 1
            if path and path[-1] in selections: receipt["exact_selection_values"] += 1
        elif isinstance(value, dict):
            require(set(value) <= set(rule), "import_order_extra_nested_field")
            receipt["truthiness_checks"] += 1
            for key, child in value.items(): visit(child, rule[key], (*path, key))
        elif isinstance(value, list):
            for index, child in enumerate(value): visit(child, rule, (*path, str(index)))
        # Scalars and null at a filtered subtree remain source-observed values.

    require(set(document) <= set(policy["outer"]) | {"raw_by_source"}, "import_order_extra_outer_field")
    for key, child in document.items():
        if key != "raw_by_source": visit(child, policy["outer"][key], (key,))
    if "raw_by_source" in document:
        parent = document["raw_by_source"]
        require(parent is None or isinstance(parent, dict) and set(parent) <= {"salla_direct"}, "import_order_parent_shape_invalid")
        if isinstance(parent, dict) and "salla_direct" in parent:
            direct = parent["salla_direct"]
            require(direct is None or isinstance(direct, dict), "import_order_parent_shape_invalid")
            if isinstance(direct, dict): visit(direct, policy["raw_salla_direct"], ("raw_by_source", "salla_direct"))
    return receipt


@dataclass(frozen=True)
class Record:
    collection: str
    file_record: dict
    identity: object
    identity_bytes: bytes


def validate_files(config, manifest, counts, files, budget):
    """Validate every byte and semantic binding before any destination write."""
    from bson import BSON
    owner = config["expected_owner_id"]
    require(manifest.get("archive_validator_sha256") == ARCHIVE_SHA, "export_archive_validator_mismatch")
    archive = archive_validator(config["source_root"], budget)
    contract = load_json(TOOL_ROOT / "source_contract_v2.json")
    records, identities, used_paths = [], set(), set()
    def add(collection, file_record):
        budget.check()
        require(len(records) < MAX_DOCUMENTS, "import_document_count_limit")
        require(file_record.get("file") not in used_paths, "import_duplicate_file_reference")
        used_paths.add(file_record.get("file"))
        data = files.read(file_record)
        budget.total_bytes += len(data)
        require(budget.total_bytes <= budget.maximum_bytes, "import_byte_limit")
        document = BSON(data).decode()
        require("_id" in document, "import_original_id_required")
        key = bytes(BSON.encode({"_id": document["_id"]}))
        require(len(key) <= 4096 and (collection, key) not in identities, "import_duplicate_or_oversized_identity")
        identities.add((collection, key))
        reject_secrets(document, budget)
        records.append(Record(collection, file_record, document["_id"], key))
        return document
    state = add(STATE_COLLECTION, manifest["state"])
    require(state.get("_id") == owner and state.get("owner_id") == owner
            and state.get("revision") == manifest["state"]["revision"], "import_canonical_state_identity_mismatch")
    gate = manifest.get("state_gate", {})
    minimum = gate.get("minimum_revision")
    require(minimum is None or type(minimum) is int and state["revision"] >= minimum, "import_state_below_capture_gate")
    require(gate.get("active_required") is not True or state.get("status") == "active", "import_state_not_active")
    logical = archive.unpack_issues(archive.unpack_financial_snapshot(state))
    reject_secrets(logical, budget)
    if archive.MARKER in logical:
        metadata, tail = archive._audit_metadata(logical)
        references, expected_events, inline = metadata["pages"], metadata["count"], len(tail["tail"])
    else:
        inline_rows = logical.get("engine", {}).get("audit", [])
        require(isinstance(inline_rows, list), "import_audit_shape_invalid")
        references, expected_events, inline = [], 0, len(inline_rows)
    del logical
    audit = manifest["audit"]
    require(len(references) <= 8192 and audit.get("page_count") == len(references)
            and len(audit.get("pages", [])) == len(references), "import_audit_page_set_mismatch")
    events = 0
    for reference, file_record in zip(references, audit["pages"]):
        require(file_record.get("id") == reference.get("id") and file_record.get("start") == events
                and file_record.get("count") == reference.get("count"), "import_audit_reference_mismatch")
        page = add(AUDIT_COLLECTION, file_record)
        events += len(archive.validated_events(page, owner, events, reference))
    require(events == expected_events == audit.get("events") and inline == audit.get("inline_events")
            and events + inline == audit.get("total_events"), "import_audit_count_mismatch")
    captured_counts = {}
    for entry in manifest["supplemental"]:
        actual_receipt = {"documents": 0, "truthiness_checks": 0, "exact_selection_values": 0, "exact_current_observations": 0}
        for file_record in entry["files"]:
            source_hash = file_record.get("source_sha256")
            require(isinstance(source_hash, str) and re.fullmatch(r"[a-f0-9]{64}", source_hash), "import_source_hash_missing")
            if entry["capture_mode"] != "financial_order_subtrees_v2":
                require(source_hash == file_record["sha256"], "import_nonfiltered_source_changed")
            row = add(entry["collection"], file_record)
            require(matches_query(row, entry["query"]), "import_owner_or_scope_mismatch")
            check_projection(row, entry["projection"])
            if entry["capture_mode"] == "financial_order_subtrees_v2":
                for key, value in check_order_output(row, contract, budget).items(): actual_receipt[key] += value
                actual_receipt["documents"] += 1
            if entry["scope"] == "owner":
                require(owner_is_valid(row, owner), "import_canonical_owner_invalid")
        if entry["capture_mode"] == "financial_order_subtrees_v2":
            receipt = entry.get("order_shape_receipt", {})
            require(set(receipt) == {"documents", "truthiness_checks", "exact_selection_values", "exact_current_observations"}
                    and receipt == actual_receipt and receipt["documents"] == entry["count"]
                    and all(type(value) is int and 0 <= value <= 1000000000 for value in receipt.values()), "import_order_shape_receipt_invalid")
        captured_counts[entry["collection"]] = captured_counts.get(entry["collection"], 0) + entry["count"]
    require(captured_counts == counts and budget.total_bytes == manifest.get("total_bson_bytes"), "import_capture_totals_mismatch")
    require(type(manifest.get("source_bson_bytes")) is int and budget.total_bytes <= manifest["source_bson_bytes"] <= 2 * 1024**3, "import_source_byte_bound_invalid")
    limits = manifest.get("capture_limits", {})
    require(type(limits.get("document_limit")) is int and 1 <= limits["document_limit"] <= 250000
            and limits.get("captured_documents") == len(records) <= limits["document_limit"]
            and type(limits.get("source_byte_limit")) is int and manifest["source_bson_bytes"] <= limits["source_byte_limit"] <= 2 * 1024**3
            and type(limits.get("audit_page_limit")) is int and len(references) <= limits["audit_page_limit"] <= 8192
            and type(limits.get("new_source_row_limit")) is int and 1 <= limits["new_source_row_limit"] <= 200000,
            "import_capture_limits_invalid")
    return records


def socket_guard():
    def audit(event, args):
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
            address = args[-1]
            require(isinstance(address, tuple) and len(address) >= 2 and address[:2] == ("127.0.0.1", 27019), "import_own_mongo_endpoint_only")
        elif event == "socket.getaddrinfo":
            require(args[0] in {"127.0.0.1", b"127.0.0.1"} and int(args[1]) == 27019, "import_literal_mongo_only")
        elif event in {"socket.bind", "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.posix_spawnp"}:
            raise RuntimeBlocked("import_unrelated_io_forbidden")
    sys.addaudithook(audit)


def batches(records):
    batch, size, collection = [], 0, None
    for record in records:
        amount = record.file_record["bytes"]
        if batch and (record.collection != collection or len(batch) >= 200 or size + amount > 8 * 1024 * 1024):
            yield collection, batch
            batch, size = [], 0
        batch.append(record); size += amount; collection = record.collection
    if batch: yield collection, batch


def write_config(config):
    path = Path(config["config_path"])
    require(re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", path.name), "runtime_config_name_invalid")
    parent = open_directory(path.parent)
    identity = None
    try:
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            info = os.fstat(stream.fileno())
            identity = (info.st_dev, info.st_ino)
            stream.write((json.dumps({key: value for key, value in config.items() if key != "config_path"}, indent=2) + "\n").encode())
            stream.flush(); os.fsync(stream.fileno())
        os.fsync(parent)
        return identity
    except BaseException:
        if identity is not None:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (info.st_dev, info.st_ino) == identity: os.unlink(path.name, dir_fd=parent)
        raise
    finally:
        os.close(parent)


def remove_own_config(config, identity):
    if identity is None: return
    path = Path(config["config_path"])
    parent = open_directory(path.parent)
    try:
        info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if (info.st_dev, info.st_ino) == identity:
            os.unlink(path.name, dir_fd=parent); os.fsync(parent)
    finally:
        os.close(parent)


def copy_to_new_database(client, config, manifest, counts, policy_sha, records, files, budget, progress=None):
    from bson import BSON
    from bson.raw_bson import RawBSONDocument
    from bson.codec_options import CodecOptions
    from pymongo import timeout
    from pymongo.write_concern import WriteConcern
    name = config["database_name"]
    require(isinstance(name, str) and re.fullmatch(r"mezan_pr2095_[0-9]{8}(?:_[a-z0-9]{1,24})?", name), "import_private_database_name_required")
    with timeout(budget.remaining()):
        hello = client.admin.command("hello")
        require(hello.get("setName") == REPLICA_SET and hello.get("hosts") == ["127.0.0.1:27019"]
                and hello.get("isWritablePrimary") is True, "import_private_replica_mismatch")
        require(name not in client.list_database_names(), "import_new_empty_database_required")
        db = client.get_database(name, codec_options=CodecOptions(document_class=RawBSONDocument), write_concern=WriteConcern("majority", j=True))
        require(not db.list_collection_names(), "import_new_empty_database_required")
    require(not Path(config["config_path"]).exists(), "runtime_config_must_be_new")
    binding = expected_binding(config, manifest, policy_sha)
    config_identity, binding_attempted, inserted = None, False, 0
    try:
        with timeout(budget.remaining()):
            # First destination write occurs only after all export validation.
            db[CLAIM_COLLECTION].insert_one({"_id":"exclusive-import", "import_id":uuid.uuid4().hex,
                "manifest_sha256":config["manifest_sha256"], "source_commit":SOURCE_SHA})
        for collection, batch in batches(records):
            raw = [RawBSONDocument(files.read(record.file_record)) for record in batch]
            with timeout(budget.remaining()): db[collection].insert_many(raw, ordered=True)
            inserted += len(batch)
            if progress and (inserted == len(records) or inserted % 1000 < len(batch)):
                progress({"stage":"copying_private_bson", "documents":inserted, "total":len(records)})
        expected_counts = {**counts, STATE_COLLECTION:1, AUDIT_COLLECTION:manifest["audit"]["page_count"]}
        for collection, count in expected_counts.items():
            with timeout(budget.remaining()):
                require(db[collection].count_documents({}, maxTimeMS=5000) == count, "import_target_count_mismatch")
        verified = 0
        for collection, batch in batches(records):
            expected = {record.identity_bytes:record.file_record["sha256"] for record in batch}
            seen = set()
            with timeout(budget.remaining()):
                cursor = db[collection].find({"_id":{"$in":[record.identity for record in batch]}}, max_time_ms=10000)
                for row in cursor:
                    key = bytes(BSON.encode({"_id":row["_id"]}))
                    require(key in expected and key not in seen and digest(row.raw) == expected[key], "import_target_bson_mismatch")
                    seen.add(key)
            require(seen == set(expected), "import_target_document_missing")
            verified += len(batch)
        # Counts and byte-exact state/archive/source rows are verified before
        # either publication artifact is created. The config alone cannot boot.
        config_identity = write_config(config)
        with timeout(budget.remaining()):
            binding_attempted = True
            db[BINDING_COLLECTION].insert_one(binding)
        return {"ok":True, "manifest_sha256":config["manifest_sha256"], "documents":verified,
                "collections":len(expected_counts), "bson_bytes":budget.total_bytes}
    except BaseException:
        # Only this new task's exact marker/config may be withdrawn. Partial
        # data and the exclusive-import claim remain; no database is dropped.
        if binding_attempted:
            try:
                with timeout(5): db[BINDING_COLLECTION].delete_one(binding)
            except Exception:
                pass  # Config is withdrawn; caller reports failure, never ready.
        try: remove_own_config(config, config_identity)
        except OSError: pass
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--export-directory", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--expected-owner-sha256", required=True)
    parser.add_argument("--database-name", required=True)
    parser.add_argument("--runtime-config", required=True)
    parser.add_argument("--preview-origin", required=True)
    parser.add_argument("--max-seconds", type=int, default=1800)
    args = parser.parse_args()
    from private_common import load_json
    budget = Budget(args.max_seconds)
    manifest_header = load_json(Path(args.export_directory) / "manifest.json", 128 * 1024 * 1024)
    owner = manifest_header.get("owner_id")
    require(isinstance(owner, str) and re.fullmatch(r"[a-f0-9]{64}", args.expected_owner_sha256)
            and digest(owner.encode()) == args.expected_owner_sha256, "import_expected_owner_mismatch")
    config = validate_config({"source_root":args.source_root, "export_directory":args.export_directory,
        "manifest_sha256":args.manifest_sha256, "database_name":args.database_name,
        "expected_owner_id":owner, "preview_origin":args.preview_origin}, args.runtime_config)
    head = subprocess.check_output(["git", "-C", config["source_root"], "rev-parse", "HEAD"], text=True, timeout=15).strip()
    dirty = subprocess.check_output(["git", "-C", config["source_root"], "status", "--porcelain", "--untracked-files=no"], text=True, timeout=15)
    require(head == SOURCE_SHA and not dirty.strip(), "import_exact_clean_source_required")
    manifest, counts, policy_sha = load_export(config)
    require(manifest.get("owner_sha256") == args.expected_owner_sha256, "import_owner_receipt_mismatch")
    files, client = ExportFiles(config["export_directory"]), None
    try:
        records = validate_files(config, manifest, counts, files, budget)
        print(json.dumps({"stage":"export_verified_before_write", "documents":len(records), "bson_bytes":budget.total_bytes}), flush=True)
        socket_guard()
        from pymongo import MongoClient
        logging.getLogger("pymongo").setLevel(logging.CRITICAL)
        client = MongoClient(MONGO_URL, connect=False, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
                             socketTimeoutMS=15000, maxPoolSize=2, appname="mezan-private-verified-import")
        result = copy_to_new_database(client, config, manifest, counts, policy_sha, records, files, budget,
            progress=lambda value:print(json.dumps(value), flush=True))
        print(json.dumps(result), flush=True)
        return 0
    finally:
        files.close()
        if client is not None: client.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Exception, KeyboardInterrupt) as error:
        code = str(error) if isinstance(error, RuntimeBlocked) else "private_import_failed"
        print(json.dumps({"ok":False, "code":code, "ready":False}), file=sys.stderr)
        raise SystemExit(2) from None
