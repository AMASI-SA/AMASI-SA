#!/usr/bin/env python3
"""Private, bounded, read-only export of one canonical operational snapshot.

The source URI is accepted only by a no-echo controlling terminal prompt.  No
application server, source credential file, or application environment is read.
See README.md for the precise scope and the private COMPLETE.json contract.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import getpass
import hashlib
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import stat
import sys
import time
import types
import warnings
from urllib.parse import parse_qsl


SOURCE_COMMIT = "9a851854132bd71be922c4aae24b4cc20a546e41"
SCHEMA_VERSION = 2
ARCHIVE_SHA256 = "a8e08fdd573fae56f184e1c10070bde994845f55bdb1ac9c81ba3dd46b833000"
STATES = "operational_balance_states_v1"
PAGES = "operational_balance_audit_pages_v1"
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_CAPTURE_DOCUMENTS = 250000
PURE_FUNCTIONS = frozenset({"content_key", "manifest", "validated_events",
    "unpack_issues", "unpack_financial_snapshot", "_audit_metadata"})
PURE_CONSTANTS = frozenset({"AUDIT_PAGES", "MARKER", "ISSUES", "ISSUE_MARK",
    "MAX_ISSUE_BSON", "SNAPSHOT", "SNAPSHOT_MARK", "SNAPSHOT_FIELDS", "MAX_SNAPSHOT_BSON"})
USER_FIELDS = ("id", "name", "role", "created_by", "is_owner", "is_active",
    "disabled", "deleted_at", "status", "web_page_access", "operational_balance_access")
LIFECYCLE_FIELDS = ("status", "active", "is_active", "archived", "deleted", "is_archived", "is_deleted")
IDENTITY_FIELDS = ("_id", "user_id", "id", "name", "display_name", "company_name",
    "account_type", "currency", *LIFECYCLE_FIELDS, "ready", "setup_issue")
SECRET_KEYS = frozenset({"password", "password_hash", "hashed_password", "access_token",
    "refresh_token", "api_key", "apikey", "client_secret", "jwt_secret", "private_key",
    "authorization", "cookie", "cookies", "session_cookie", "session_token", "mfa_secret",
    "totp_secret", "recovery_codes", "card_number", "cvv", "cvc", "card_cvc", "card_cvv",
    "payment_token", "bank_password", "mongo_url", "mongodb_uri", "mongodb_url",
    "bearer_token", "api_secret", "oauth_token", "mfa_backup_codes", "mfa_recovery_codes",
    "otp_secret", "session_key", "jwt_signing_key", "password_digest", "secret_key"})


class ExportFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise ExportFailure(code)


class Budget:
    def __init__(self, seconds=600, max_bytes=512 * 1024 * 1024, max_pages=4096):
        require(1 <= seconds <= 1800 and 1 <= max_bytes <= 2 * 1024**3
                and 0 <= max_pages <= 8192, "INVALID_LIMITS")
        self.deadline = time.monotonic() + seconds
        self.max_bytes, self.max_pages, self.bytes = max_bytes, max_pages, 0
        self.max_documents, self.documents = MAX_CAPTURE_DOCUMENTS, 0

    def check(self):
        require(time.monotonic() < self.deadline, "TIME_LIMIT")

    def remaining(self):
        self.check()
        return max(0.001, min(15.0, self.deadline - time.monotonic()))

    def add(self, raw):
        self.check()
        require(type(raw) is bytes and 5 <= len(raw) <= MAX_DOCUMENT_BYTES, "DOCUMENT_LIMIT")
        require(self.bytes + len(raw) <= self.max_bytes, "BYTE_LIMIT")
        require(self.documents < self.max_documents, "DOCUMENT_COUNT_LIMIT")
        self.bytes += len(raw)
        self.documents += 1


def parent_fd(path):
    """Resolve absolute path components through directory FDs, refusing symlinks."""
    path = Path(path)
    require(path.is_absolute() and path.name not in {"", ".", ".."}
            and ".." not in path.parts, "UNSAFE_PATH")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd, path.name
    except BaseException:
        os.close(fd)
        raise


def read_regular(path, limit):
    parent, name = parent_fd(path)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, "UNSAFE_SOURCE_FILE")
        data = stream.read(limit + 1)
        require(len(data) <= limit, "UNSAFE_SOURCE_FILE")
        return data


class PrivateOutput:
    """Exclusive directory; every member is exclusive, no-follow and owner-only."""
    def __init__(self, path):
        self.fd, self.subdirs = None, {}
        parent, name = parent_fd(path)
        try:
            os.mkdir(name, 0o700, dir_fd=parent)  # Existing paths always fail.
            self.fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        try:
            info = os.fstat(self.fd)
            require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "OUTPUT_PERMISSIONS")
            for name in ("audit", "context"):
                os.mkdir(name, 0o700, dir_fd=self.fd)
                self.subdirs[name] = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.fd)
            self.write("INCOMPLETE", b"No usable snapshot exists without a verified COMPLETE.json.\n")
        except BaseException:
            self.close()
            raise

    def write(self, name, data):
        parts = name.split("/")
        require(1 <= len(parts) <= 2 and all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) for p in parts), "OUTPUT_NAME")
        fd = self.fd if len(parts) == 1 else self.subdirs[parts[0]]
        out = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(out, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(fd)
        return {"file": name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def complete(self, manifest):
        data = (json.dumps(manifest, ensure_ascii=True, indent=2) + "\n").encode()
        record = self.write("manifest.json", data)
        self.write("COMPLETE.json", (json.dumps({"schema_version": SCHEMA_VERSION, "manifest_sha256": record["sha256"]}) + "\n").encode())
        os.unlink("INCOMPLETE", dir_fd=self.fd)
        os.fsync(self.fd)
        return record["sha256"]

    def require_capacity(self, byte_limit, document_limit):
        available = os.fstatvfs(self.fd)
        # Budget both payload bytes and worst-case allocation slack for many
        # small BSON files, plus bounded manifest/contract overhead.
        reserve = byte_limit + document_limit * max(4096, available.f_frsize) + 256 * 1024**2
        require(available.f_bavail * available.f_frsize >= reserve, "OUTPUT_FREE_SPACE_LOW")
        require(available.f_files == 0 or available.f_favail >= document_limit + 16, "OUTPUT_INODE_SPACE_LOW")

    def close(self):
        for fd in self.subdirs.values():
            os.close(fd)
        self.subdirs = {}
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def load_archive(path, budget):
    """Use exact trusted pure validators without loading application contexts.

    Function ASTs are unchanged.  Only the request-lifecycle checkpoint is
    supplied by this offline operation's deadline.  No server/auth/dotenv,
    worker, network, mutation or full-database code is imported or executed.
    """
    raw = read_regular(path, 256 * 1024)
    require(hashlib.sha256(raw).hexdigest() == ARCHIVE_SHA256, "VALIDATOR_SOURCE_MISMATCH")
    tree = ast.parse(raw)
    selected = []
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Import) and all(x.name == "zlib" for x in node.names):
            selected.append(node)
        elif isinstance(node, ast.ImportFrom) and node.module in {"hashlib", "bson", "bson.errors"}:
            selected.append(node)
        elif isinstance(node, ast.ClassDef) and node.name == "ArchiveError":
            selected.append(node)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in PURE_CONSTANTS:
            selected.append(node)
            names.add(node.targets[0].id)
        elif isinstance(node, ast.FunctionDef) and node.name in PURE_FUNCTIONS:
            selected.append(node)
            names.add(node.name)
    require(names == PURE_FUNCTIONS | PURE_CONSTANTS, "VALIDATOR_CONTRACT_MISMATCH")
    module = types.ModuleType("verified_private_archive")
    module.__dict__["_checkpoint"] = budget.check
    exec(compile(ast.Module(body=selected, type_ignores=[]), "verified_archive_validation", "exec"), module.__dict__)
    return module


def reject_secrets(value, budget):
    """Refuse credential material; never remove fields from a faithful BSON copy."""
    from bson.code import Code
    pending, seen = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        seen += 1
        require(depth <= 64 and seen <= 10_000_000, "CONTENT_SCAN_LIMIT")
        if seen % 1024 == 0:
            budget.check()
        if isinstance(item, dict):
            for key, child in item.items():
                require(isinstance(key, str), "INVALID_DOCUMENT_KEY")
                normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key).lower().replace("-", "_")
                require(normalized not in SECRET_KEYS or child in (None, "", [], {}), "SECRET_MATERIAL_PRESENT")
                pending.append((child, depth + 1))
        elif isinstance(item, (list, tuple)):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            require(not re.search(r"mongodb(?:\+srv)?://|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", item), "SECRET_MATERIAL_PRESENT")
            if isinstance(item, Code) and item.scope is not None:
                pending.append((item.scope, depth + 1))
    budget.check()


class NativeReader:
    """Only explicit find/find_one application operations against the source."""
    def __init__(self, uri, database, budget):
        validate_transport(uri)
        from bson.raw_bson import RawBSONDocument
        from pymongo import MongoClient, timeout
        from pymongo.read_preferences import ReadPreference
        self.client = MongoClient(uri, document_class=RawBSONDocument, connect=False,
            serverSelectionTimeoutMS=15_000, connectTimeoutMS=10_000, socketTimeoutMS=15_000,
            read_preference=ReadPreference.PRIMARY,
            appname="mezan-private-native-snapshot")
        self.db, self.budget = self.client[database], budget
        try:
            with timeout(budget.remaining()):
                self.session = self.client.start_session(snapshot=True)
        except BaseException:
            self.client.close()
            raise

    def one(self, collection, query):
        return self._one(collection, query, self.session)

    def current_one(self, collection, query):
        """Fresh primary read, deliberately outside the capture snapshot."""
        return self._one(collection, query, None)

    def _one(self, collection, query, session):
        from pymongo import timeout
        with timeout(self.budget.remaining()):
            row = self.db[collection].find_one(query, session=session,
                max_time_ms=int(self.budget.remaining() * 1000))
        return None if row is None else row.raw

    def projected(self, collection, query, projection, maximum):
        from pymongo import timeout
        if collection == "unified_orders":
            from navigation_sources import order_source_projection
            require(set(query) == {"user_id"} and projection == order_source_projection(), "ORDER_QUERY_CONTRACT")
            yield from self.order_rows(query["user_id"], maximum)
            return
        cursor = None
        try:
            with timeout(self.budget.remaining()):
                cursor = self.db[collection].find(query, projection, session=self.session,
                    max_time_ms=int(self.budget.remaining() * 1000)).limit(maximum + 1).batch_size(min(100, maximum + 1))
            for index in range(maximum + 1):
                with timeout(self.budget.remaining()):
                    try:
                        row = next(cursor)
                    except StopIteration:
                        return
                require(index < maximum, "CONTEXT_ROW_LIMIT")
                yield row.raw
        finally:
            if cursor is not None:
                cursor.close()

    def order_rows(self, owner, maximum):
        from pymongo import timeout
        from navigation_sources import order_pipeline
        cursor = None
        try:
            with timeout(self.budget.remaining()):
                cursor = self.db["unified_orders"].aggregate(order_pipeline(owner, maximum), session=self.session,
                    maxTimeMS=int(self.budget.remaining() * 1000), allowDiskUse=False, batchSize=100)
            for index in range(maximum + 1):
                with timeout(self.budget.remaining()):
                    try:
                        row = next(cursor)
                    except StopIteration:
                        return
                require(index < maximum, "CONTEXT_ROW_LIMIT")
                yield row.raw
        finally:
            if cursor is not None:
                cursor.close()

    def close(self):
        try:
            self.session.end_session()
        finally:
            self.client.close()


def projection(*fields):
    return {field: 1 for field in fields}


def context_specs(owner, include_directory=False, include_navigation=False, max_source_rows=50000):
    """Closed positive projections certified against this exact candidate.

    Adding a collection requires an explicit source review and new named scope;
    callers cannot provide collection names, queries or arbitrary projections.
    """
    user_projection = {"_id": 1, **{field: 1 for field in USER_FIELDS}}
    settings_fields = ["_id", "user_id", "mezan2_financial_cutover"]
    if include_directory:
        settings_fields += ["payment_methods", *[f"shipping_companies.{field}" for field in
            ("name", "salla_carrier_key", *LIFECYCLE_FIELDS, "is_deferred", "payment_mode",
             "cost_per_order", "cost", "vat_percent", "vat_rate", "cod_fee_tiers", "cod_fee_percent",
             "cod_fee_fixed_per_order", "cod_fee_vat_percent", "cod_fee_vat_included")]]
    scopes = [
        ("owner", "users", {"id": owner}, user_projection, 1, True),
        ("team", "users", {"created_by": owner, "role": {"$ne": "owner"}}, user_projection, 2000, False),
        ("atomic-control", "mz2_atomic_owners", {"_id": owner}, {"_id": 1, "ledger_backend_state": 1}, 1, False),
        ("cutover-control", "settings", {"user_id": owner}, projection(*settings_fields), 1, False),
    ]
    if not include_directory:
        return tuple(scopes)
    for scope, collection in (
        ("employees", "mezan_employees_v2"), ("suppliers", "mezan_suppliers_v2"),
        ("drivers", "store_drivers"), ("external-persons", "mz2_external_persons_v2"),
        ("financial-accounts", "mz2_financial_accounts"), ("accounts", "accounts"),
        ("bank-accounts", "mz2_operational_bank_accounts"),
    ):
        scopes.append((scope, collection, {"user_id": owner}, projection(*IDENTITY_FIELDS), 20000, False))
    additions = (
        ("expense-categories", "expense_categories", ("_id", "user_id", "id", "name", "code", "source", "system", *LIFECYCLE_FIELDS)),
        ("owner-beneficiaries", "mz2_operational_owner_beneficiaries_v2", ("_id", "user_id", "id", "name", "currency", *LIFECYCLE_FIELDS)),
        ("shipping-setup", "mz2_shipping_setup_v2", ("_id", "user_id", "couriers")),
        ("ad-accounts", "mezan_integration_accounts_v2", ("_id", "user_id", "mezan_integration_account_id", "external_account_id", "provider", "connection_status", "display_name", "currency", "timezone", "mezan_selected", *LIFECYCLE_FIELDS)),
        ("ad-bindings", "mz2_ad_account_bindings_v2", ("_id", "user_id", "integration_account_id", "platform_account_id", "platform", "confirmed_by", "confirmed_at", "funding_policy", "funding_mode", "hybrid_policy", "version", "currency", "timezone", *LIFECYCLE_FIELDS)),
        ("ad-policies", "mz2_ad_automation_policies_v2", ("_id", "user_id", "id", "platform", "integration_account_id", "version", "status", "confirmed_by", "confirmed_at", "binding_version", "start_date", "wallet_fraction", "evidence")),
    )
    for scope, collection, fields in additions:
        scopes.append((scope, collection, {"user_id": owner}, projection(*fields), 20000, False))
    scopes.append(("pos-identities", "mz2_opening_facts_v2",
        {"user_id": owner, "category": "other_receivable", "status": "active"},
        projection("_id", "id", "user_id", "entity_id", "entity_type", "sub_account", "side",
            "category", "status", "source", "created_by", "created_at", "version", "display_name",
            "reference", "amount", "currency", "cutover_date", "evidence", "manual_contract"), 20000, False))
    if include_navigation:
        from navigation_sources import extend_scopes
        return extend_scopes(scopes, owner, max_source_rows)
    return tuple(scopes)


def scope_binding(scope, query):
    if scope == "global-ads-scheduler-status":
        return "global_metadata"
    if scope in {"owner", "team"}:
        return "owner_user" if scope == "owner" else "created_by_owner"
    return "owner_id" if "owner_id" in query else "user_id" if "user_id" in query else "owner_document_id"


def matches_query(doc, query):
    """Validate the tiny fixed filter vocabulary used by the closed scopes."""
    for key, expected in query.items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if set(expected) == {"$ne"}:
                if actual == expected["$ne"]:
                    return False
            elif set(expected) == {"$in"}:
                if actual not in expected["$in"]:
                    return False
            else:
                raise ExportFailure("QUERY_CONTRACT_INVALID")
        elif actual != expected:
            return False
    return True


def capture_mode(collection):
    from navigation_sources import RAW_FINANCIAL
    if collection == "unified_orders":
        return "financial_order_subtrees_v2"
    return "raw_bson" if collection in RAW_FINANCIAL else "server_positive_projection"


def source_contract(include_context, include_directory, include_navigation):
    """Stable public policy: private owner values and runtime caps are excluded."""
    from navigation_sources import (CONTRACT_VERSION, CURRENT_FIELDS, EXPECTED_COLLECTIONS, OUTER_POLICY,
        PRIVATE_CONTACT_KEYS, RAW_POLICY, SELECTION_FIELDS, order_pipeline)
    scopes = context_specs("__OWNER__", include_directory, include_navigation) if include_context else ()
    value = {"version": CONTRACT_VERSION, "source_commit": SOURCE_COMMIT,
        "owner_placeholder": "__OWNER__", "core": {"states": STATES, "audit_collection": PAGES,
            "state_query": {"_id": "__OWNER__", "owner_id": "__OWNER__"},
            "pages": "exact ordered state manifest only", "encoding": "raw_bson"},
        "source_snapshot": "one_snapshot_session_with_separate_primary_head_recheck",
        "includes_navigation": include_navigation,
        "scopes": [{"scope": scope, "collection": collection, "query": query, "projection": fields,
            "capture_mode": capture_mode(collection), "binding": scope_binding(scope, query), "required": required}
            for scope, collection, query, fields, _, required in scopes]}
    if include_navigation:
        value.update(expected_collections=sorted(EXPECTED_COLLECTIONS),
            order_output_policy={"outer": OUTER_POLICY, "raw_salla_direct": RAW_POLICY},
            order_shape_policy={"parent_types": ["missing", "null", "object"], "truthiness_changes": "reject",
                "array_order_and_cardinality": "preserve", "selection_values": "exact_bson",
                "selection_fields": sorted(SELECTION_FIELDS), "current_values": "exact_bson",
                "current_fields": sorted(CURRENT_FIELDS), "private_observation_keys": sorted(PRIVATE_CONTACT_KEYS)},
            order_pipeline_template=order_pipeline("__OWNER__", 49999))
        value["order_pipeline_template"][-1] = {"$limit": "__ROW_LIMIT_PLUS_ONE__"}
    raw = (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()
    return value, hashlib.sha256(raw).hexdigest(), raw


def implementation_hashes():
    """Bind the reviewed executable files separately from semantic scope policy."""
    import navigation_sources
    paths = {"export_native_snapshot.py": Path(__file__).absolute(),
        "navigation_sources.py": Path(navigation_sources.__file__).absolute()}
    return {name: hashlib.sha256(read_regular(path, 512 * 1024)).hexdigest() for name, path in paths.items()}


def check_projection(doc, fields):
    """Validate a positive projection, including objects nested inside arrays."""
    allowed = {}
    for key, included in fields.items():
        if not included:
            continue
        tree = allowed
        parts = key.split(".")
        for part in parts[:-1]:
            require(tree.get(part) is not True, "PROJECTION_CONTRACT_INVALID")
            tree = tree.setdefault(part, {})
        tree[parts[-1]] = True

    def check(value, tree):
        if tree is True:
            return
        if isinstance(value, dict):
            require(set(value) <= set(tree), "CONTEXT_PROJECTION_MISMATCH")
            for key, child in value.items():
                check(child, tree[key])
        elif isinstance(value, list):
            for child in value:
                check(child, tree)
        # Mongo may preserve scalar/null values encountered on a projected path.
    check(doc, allowed)


def export_snapshot(reader, output, archive, budget, owner, database, include_context=False, include_directory=False,
                    min_revision=None, require_active=False, progress=None, include_navigation=False, max_source_rows=50000):
    from bson import BSON
    from navigation_sources import EXPECTED_COLLECTIONS, WITNESS, financial_order
    implementation = implementation_hashes()
    require(1 <= max_source_rows <= 200000, "SOURCE_ROW_LIMIT_INVALID")
    if include_navigation:
        include_context = include_directory = True
    def report(stage, **counts):
        if progress is not None:
            progress({"stage": stage, **counts})
    report("reading_state")
    raw = reader.one(STATES, {"_id": owner, "owner_id": owner})
    require(raw is not None, "STATE_NOT_FOUND")
    budget.add(raw)
    state = BSON(raw).decode()
    require(state.get("_id") == owner and state.get("owner_id") == owner, "STATE_OWNER_MISMATCH")
    revision = state.get("revision")
    require(isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0, "STATE_REVISION_INVALID")
    require(min_revision is None or type(min_revision) is int and min_revision >= 0, "MIN_REVISION_INVALID")
    require(min_revision is None or revision >= min_revision, "STATE_BELOW_MIN_REVISION")
    require(not require_active or state.get("status") == "active", "STATE_NOT_ACTIVE")
    logical = archive.unpack_issues(archive.unpack_financial_snapshot(state))
    reject_secrets(logical, budget)
    reject_secrets(state, budget)
    if archive.MARKER in logical:
        meta, tail = archive._audit_metadata(logical)
        refs, expected_events = meta["pages"], meta["count"]
        inline_events = len(tail["tail"])
    else:
        require(isinstance(logical.get("engine", {}).get("audit", []), list), "AUDIT_MANIFEST_MISSING")
        refs, expected_events = [], 0
        inline_events = len(logical.get("engine", {}).get("audit", []))
    require(len(refs) <= budget.max_pages and type(expected_events) is int and 0 <= expected_events <= 5_000_000, "AUDIT_LIMIT")
    del logical
    record = output.write("state.bson", raw)
    record["revision"] = int(revision)
    entries, events = [], 0
    report("copying_audit", copied=0, total=len(refs))
    for index, ref in enumerate(refs):
        require(isinstance(ref, dict) and set(ref) == {"id", "count"}
                and isinstance(ref["id"], str) and re.fullmatch(r"[0-9a-f]{64}", ref["id"])
                and type(ref["count"]) is int and ref["count"] > 0, "AUDIT_REFERENCE_INVALID")
        page = reader.one(PAGES, {"_id": ref["id"], "owner_id": owner})
        require(page is not None, "AUDIT_PAGE_MISSING")
        budget.add(page)
        decoded = BSON(page).decode()
        validated = archive.validated_events(decoded, owner, events, ref)
        reject_secrets(decoded, budget)
        page_record = output.write(f"audit/{index:06d}.bson", page)
        page_record.update(id=ref["id"], start=events, count=len(validated))
        entries.append(page_record)
        events += len(validated)
        del decoded, validated, page
        if (index + 1) % 64 == 0 or index + 1 == len(refs):
            report("copying_audit", copied=index + 1, total=len(refs))
    require(events == expected_events, "AUDIT_COUNT_MISMATCH")
    supplemental = []
    source_ids = set()
    output_bytes = record["bytes"] + sum(entry["bytes"] for entry in entries)
    require(not include_directory or include_context, "DIRECTORY_REQUIRES_CONTEXT")
    if include_context:
        for scope, collection, query, fields, maximum, required in context_specs(owner, include_directory, include_navigation, max_source_rows):
            files = []
            shape_counts = {"documents": 0, "truthiness_checks": 0, "exact_selection_values": 0, "exact_current_observations": 0}
            for index, data in enumerate(reader.projected(collection, query, fields, maximum)):
                require(index < maximum, "CONTEXT_ROW_LIMIT")
                budget.add(data)
                doc = BSON(data).decode()
                if fields is not None:
                    check_projection({key: value for key, value in doc.items() if key != WITNESS}
                        if collection == "unified_orders" else doc, fields)
                require("_id" in doc, "CONTEXT_SOURCE_ID_MISSING")
                source_id = (collection, bytes(BSON.encode({"_id": doc["_id"]})))
                require(source_id not in source_ids, "CONTEXT_DUPLICATE_SOURCE_ID")
                source_ids.add(source_id)
                require(matches_query(doc, query) and (scope != "owner" or doc.get("role") == "owner"), "CONTEXT_OWNER_MISMATCH")
                source_hash = hashlib.sha256(data).hexdigest()
                if collection == "unified_orders":
                    doc, shape = financial_order(doc, require, budget.check)
                    shape_counts["documents"] += 1
                    for key, count in shape.items():
                        shape_counts[key] += count
                    data = bytes(BSON.encode(doc))
                    require(len(data) <= MAX_DOCUMENT_BYTES, "DOCUMENT_LIMIT")
                reject_secrets(doc, budget)
                saved = output.write(f"context/{scope}-{index:06d}.bson", data)
                saved["source_sha256"] = source_hash
                files.append(saved)
                output_bytes += len(data)
                if (index + 1) % 256 == 0:
                    report("copying_context", scopes=len(supplemental), current_documents=index + 1)
            require(not required or len(files) == 1, "OWNER_CONTEXT_MISSING")
            supplemental.append({"scope": scope, "collection": collection, "files": files,
                "count": len(files), "projection": fields, "query": query, "source_count": len(files),
                "cursor_exhausted": True, "row_limit": maximum, "capture_mode": capture_mode(collection),
                "binding": scope_binding(scope, query),
                **({"order_shape_receipt": shape_counts} if collection == "unified_orders" else {})})
            report("copying_context", scopes=len(supplemental), documents=sum(row["count"] for row in supplemental))
    budget.check()
    contract, contract_hash, contract_raw = source_contract(include_context, include_directory, include_navigation)
    output.write("source_contract_v2.json", contract_raw)
    if include_navigation:
        require(EXPECTED_COLLECTIONS <= {scope["collection"] for scope in supplemental}, "SOURCE_CLOSURE_INCOMPLETE")
        require(len(supplemental) == len(contract["scopes"]), "SOURCE_CLOSURE_INCOMPLETE")
    require(implementation == implementation_hashes(), "TOOL_SOURCE_CHANGED")
    report("rechecking_state")
    again = reader.current_one(STATES, {"_id": owner, "owner_id": owner})
    require(again is not None and len(again) <= MAX_DOCUMENT_BYTES, "STATE_RECHECK_FAILED")
    require(hashlib.sha256(again).hexdigest() == record["sha256"]
            and BSON(again).decode().get("revision") == revision, "STATE_CHANGED_DURING_CAPTURE")
    budget.check()
    manifest = {"schema_version": SCHEMA_VERSION, "kind": "mezan_native_bson_snapshot",
        "source_commit": SOURCE_COMMIT, "archive_validator_sha256": ARCHIVE_SHA256,
        "source_database": database, "owner_id": owner,
        "owner_sha256": hashlib.sha256(owner.encode("utf-8")).hexdigest(),
        "captured_at": datetime.now(timezone.utc).isoformat(), "state": record,
        "audit": {"events": events, "inline_events": inline_events, "total_events": events + inline_events,
            "page_count": len(entries), "pages": entries},
        "supplemental": supplemental,
        "source_contract": {"version": contract["version"], "file": "source_contract_v2.json", "sha256": contract_hash},
        "tool_implementation": {"version": SCHEMA_VERSION, "files": implementation},
        "source_recheck": {"raw_sha256_unchanged": True, "revision_unchanged": True,
            "outside_capture_snapshot": True},
        "state_gate": {"minimum_revision": min_revision, "active_required": require_active},
        "consistency": {"source_reads": "one_read_only_snapshot_session",
            "snapshot_fallback_permitted": False,
            "state": "same_raw_hash_and_revision_before_and_after",
            "audit": "immutable_content_keys_in_manifest_order",
            "supplemental": "same_source_snapshot_as_state_and_pages"},
        "scope_limits": {"raw_canonical_state_and_referenced_audit_complete": True,
            "owner_context_included": include_context, "full_database_export": False,
            "resource_directory_included": include_directory,
            "courier_unready_order_discovery_included": include_navigation,
            "full_navigation_source_closure_verified": include_navigation,
            "expected_forecast_source_graph_included": include_navigation,
            "provider_order_label_sources_included": include_navigation},
        "total_bson_bytes": output_bytes, "source_bson_bytes": budget.bytes,
        "capture_limits": {"document_limit": budget.max_documents, "captured_documents": budget.documents,
            "source_byte_limit": budget.max_bytes, "audit_page_limit": budget.max_pages,
            "new_source_row_limit": max_source_rows}}
    return output.complete(manifest), manifest


def validate_transport(uri):
    """Reject transport downgrade locally, without logging or DNS resolution."""
    require(isinstance(uri, str) and 1 <= len(uri) <= 8192 and not any(c in uri for c in "\r\n\x00")
        and uri.startswith(("mongodb://", "mongodb+srv://")), "URI_INPUT_INVALID")
    scheme, rest = uri.split("://", 1)
    authority = rest.split("/", 1)[0].split("?", 1)[0].rpartition("@")[2]
    require(authority and "#" not in uri, "URI_INPUT_INVALID")
    local = True
    for address in authority.split(","):
        host = address[1:address.find("]")] if address.startswith("[") and "]" in address else address.split(":", 1)[0]
        try:
            is_local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            is_local = host.lower() == "localhost"
        local = local and is_local
    options = parse_qsl(uri.partition("?")[2], keep_blank_values=True)
    values = {}
    for key, value in options:
        values.setdefault(key.lower(), []).append(value.lower())
    insecure = ("tlsallowinvalidcertificates", "tlsallowinvalidhostnames", "tlsinsecure",
        "tlsdisablecertificaterevocationcheck", "tlsdisableocspendpointcheck")
    require(not any(any(v != "false" for v in values.get(key, [])) for key in insecure), "INSECURE_TRANSPORT_OPTION")
    require("ssl_cert_reqs" not in values and "ssl_match_hostname" not in values, "INSECURE_TRANSPORT_OPTION")
    tls = values.get("tls", []) + values.get("ssl", [])
    require(all(value in {"true", "false"} for value in tls) and len(set(tls)) <= 1, "TLS_OPTIONS_INVALID")
    if not local:
        require("false" not in tls and (scheme == "mongodb+srv" or "true" in tls), "TLS_REQUIRED")


def hidden_uri():
    import termios
    require(sys.stdin.isatty() and sys.stderr.isatty(), "CONTROLLING_TTY_REQUIRED")
    fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_NOFOLLOW)
    try:
        termios.tcgetattr(fd)  # Refuse an unusable controlling terminal.
    finally:
        os.close(fd)
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        uri = getpass.getpass("Source MongoDB URI (hidden; not saved): ")
    validate_transport(uri)
    return uri


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ExportFailure("ARGUMENTS_INVALID")


def main(argv=None):
    logging.disable(sys.maxsize)
    warnings.filterwarnings("ignore")
    reader = output = None
    try:
        parser = SafeParser(description=__doc__)
        parser.add_argument("--source-db", required=True)
        parser.add_argument("--owner", required=True)
        parser.add_argument("--output-dir", required=True)
        parser.add_argument("--archive-source", required=True)
        parser.add_argument("--include-owner-context", action="store_true")
        parser.add_argument("--include-resource-directory", action="store_true")
        parser.add_argument("--include-navigation-sources", action="store_true")
        parser.add_argument("--max-source-rows", type=int, default=50000)
        parser.add_argument("--max-seconds", type=int, default=600)
        parser.add_argument("--max-mib", type=int, default=512)
        parser.add_argument("--max-pages", type=int, default=4096)
        parser.add_argument("--min-revision", type=int)
        parser.add_argument("--require-active", action="store_true")
        args = parser.parse_args(argv)
        require(re.fullmatch(r"[A-Za-z0-9_-]{1,63}", args.source_db) is not None, "DATABASE_NAME_INVALID")
        require(1 <= len(args.owner) <= 256 and all(ord(c) >= 32 for c in args.owner), "OWNER_INVALID")
        require(args.include_navigation_sources or not args.include_resource_directory or args.include_owner_context, "DIRECTORY_REQUIRES_CONTEXT")
        require(1 <= args.max_source_rows <= 200000, "SOURCE_ROW_LIMIT_INVALID")
        require(args.min_revision is None or args.min_revision >= 0, "MIN_REVISION_INVALID")
        # Validate path/source before prompting; no URI enters a CLI argument.
        budget = Budget(args.max_seconds, args.max_mib * 1024**2, args.max_pages)
        archive = load_archive(args.archive_source, budget)
        output = PrivateOutput(args.output_dir)
        output.require_capacity(budget.max_bytes, budget.max_documents)
        uri = hidden_uri()
        budget.deadline = time.monotonic() + args.max_seconds
        reader = NativeReader(uri, args.source_db, budget)
        uri = None  # The driver retains its own in-memory connection settings.
        digest, manifest = export_snapshot(reader, output, archive, budget, args.owner,
            args.source_db, args.include_owner_context, args.include_resource_directory,
            args.min_revision, args.require_active,
            progress=lambda value: print(json.dumps(value), file=sys.stderr, flush=True),
            include_navigation=args.include_navigation_sources, max_source_rows=args.max_source_rows)
        print(json.dumps({"ok": True, "manifest_sha256": digest,
            "audit_pages": manifest["audit"]["page_count"], "bson_bytes": budget.bytes}))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        code = error.code if isinstance(error, ExportFailure) else "EXPORT_FAILED"
        kind = type(error).__name__
        print(json.dumps({"ok": False, "code": code,
            "error_type": kind if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,100}", kind) else "Exception"}), file=sys.stderr)
        return 2
    finally:
        for resource in (reader, output):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(main())
