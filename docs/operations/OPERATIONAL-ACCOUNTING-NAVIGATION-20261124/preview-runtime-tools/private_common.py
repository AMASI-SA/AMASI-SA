"""Fail-closed configuration and immutable-copy checks for private PR2095 runs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

SOURCE_SHA = "9a851854132bd71be922c4aae24b4cc20a546e41"
MONGO_URL = "mongodb://127.0.0.1:27019/?directConnection=true&replicaSet=mezan_pr2095_private"
REPLICA_SET = "mezan_pr2095_private"
AUTH_ORIGIN = "http://127.0.0.1:8001"
BINDING_COLLECTION = "__pr2095_runtime_binding"
STATE_COLLECTION = "operational_balance_states_v1"
AUDIT_COLLECTION = "operational_balance_audit_pages_v1"
READ_COLLECTIONS = frozenset({"mezan_demand_read_jobs_v1", "mezan_demand_read_rows_v1"})
TOOL_ROOT = Path(__file__).resolve().parent
HASH = re.compile(r"[a-f0-9]{64}\Z")


class RuntimeBlocked(RuntimeError):
    """Only fixed codes: do not put private data or exception text in logs."""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path, maximum=2 * 1024 * 1024):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise RuntimeBlocked("private_configuration_unavailable")
    try:
        value = json.loads(path.read_bytes())
    except (ValueError, OSError):
        raise RuntimeBlocked("private_configuration_invalid") from None
    if not isinstance(value, dict):
        raise RuntimeBlocked("private_configuration_invalid")
    return value


def validate_config(value, path):
    path = Path(path).absolute()
    value = dict(value)
    required = {"source_root", "export_directory", "manifest_sha256", "database_name", "expected_owner_id", "preview_origin"}
    if set(value) != required:
        raise RuntimeBlocked("private_configuration_fields_invalid")
    if not re.fullmatch(r"mezan_pr2095_[0-9]{8}(?:_[a-z0-9]{1,24})?", str(value["database_name"])):
        raise RuntimeBlocked("private_database_name_required")
    owner = value["expected_owner_id"]
    if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,160}", owner) or owner.startswith("REPLACE"):
        raise RuntimeBlocked("expected_owner_required")
    if not isinstance(value["manifest_sha256"], str) or not HASH.fullmatch(value["manifest_sha256"]):
        raise RuntimeBlocked("verified_manifest_hash_required")
    origin = urlsplit(value["preview_origin"])
    if (origin.scheme != "https" or not re.fullmatch(r"[-a-z0-9]+\.preview\.emergentagent\.com", origin.hostname or "")
            or origin.username or origin.password or origin.port or origin.path or origin.query or origin.fragment):
        raise RuntimeBlocked("exact_private_preview_origin_required")
    for name in ("source_root", "export_directory"):
        entry = Path(value[name])
        if not entry.is_absolute() or not entry.is_dir() or entry.is_symlink():
            raise RuntimeBlocked("private_absolute_directory_required")
        value[name] = str(entry.resolve())
    backend = Path(value["source_root"]) / "backend"
    if not (backend / "demand_read_runner.py").is_file() or (backend / ".env").exists():
        raise RuntimeBlocked("clean_source_without_dotenv_required")
    value["config_path"] = str(path)
    return value


def load_config(path):
    path = Path(path).absolute()
    return validate_config(load_json(path, 16384), path)


def replace_owner(value, owner):
    if isinstance(value, dict):
        return {key: replace_owner(child, owner) for key, child in value.items()}
    if isinstance(value, list):
        return [replace_owner(child, owner) for child in value]
    return owner if value == "__OWNER__" else value


def load_export(config):
    directory = Path(config["export_directory"])
    if any((directory / name).exists() or (directory / name).is_symlink() for name in ("INCOMPLETE", "INCOMPLETE.json")):
        raise RuntimeBlocked("incomplete_export_refused")
    manifest_path = directory / "manifest.json"
    manifest = load_json(manifest_path, 128 * 1024 * 1024)
    complete = load_json(directory / "COMPLETE.json", 16384)
    expected = config["manifest_sha256"]
    if (digest(manifest_path.read_bytes()) != expected or complete.get("manifest_sha256") != expected
            or complete.get("schema_version") != 2):
        raise RuntimeBlocked("export_manifest_binding_mismatch")
    if (manifest.get("schema_version") != 2 or manifest.get("kind") != "mezan_native_bson_snapshot"
            or manifest.get("source_commit") != SOURCE_SHA or manifest.get("owner_id") != config["expected_owner_id"]
            or manifest.get("source_recheck") != {"raw_sha256_unchanged": True, "revision_unchanged": True, "outside_capture_snapshot": True}
            or manifest.get("consistency", {}).get("source_reads") != "one_read_only_snapshot_session"
            or manifest.get("consistency", {}).get("snapshot_fallback_permitted") is not False):
        raise RuntimeBlocked("export_identity_or_stability_invalid")
    state = manifest.get("state", {})
    if not HASH.fullmatch(str(state.get("sha256", ""))) or type(state.get("revision")) is not int or state["revision"] < 0:
        raise RuntimeBlocked("canonical_state_evidence_required")
    policy_path = TOOL_ROOT / "coverage-policy.json"
    policy = load_json(policy_path, 65536)
    if policy.get("review_complete") is not True or policy.get("source_commit") != SOURCE_SHA:
        raise RuntimeBlocked("source_coverage_review_incomplete")
    implementation = policy.get("tool_implementation")
    if (not isinstance(implementation, dict) or implementation.get("version") != 2
            or set(implementation.get("files", {})) != {"export_native_snapshot.py", "navigation_sources.py"}
            or not all(isinstance(value, str) and HASH.fullmatch(value) for value in implementation["files"].values())
            or manifest.get("tool_implementation") != implementation):
        raise RuntimeBlocked("reviewed_export_implementation_mismatch")
    contract_path = TOOL_ROOT / "source_contract_v2.json"
    contract = load_json(contract_path, 2 * 1024 * 1024)
    contract_hash = digest(contract_path.read_bytes())
    captured_contract = directory / "source_contract_v2.json"
    if (policy.get("source_contract_sha256") != contract_hash
            or manifest.get("source_contract") != {"version": 2, "file": "source_contract_v2.json", "sha256": contract_hash}
            or digest(captured_contract.read_bytes()) != contract_hash
            or contract.get("version") != 2 or contract.get("source_commit") != SOURCE_SHA
            or contract.get("includes_navigation") is not True or contract.get("owner_placeholder") != "__OWNER__"):
        raise RuntimeBlocked("reviewed_source_projection_contract_mismatch")
    required_scopes = {"raw_canonical_state_and_referenced_audit_complete", "owner_context_included",
                       "resource_directory_included", "expected_forecast_source_graph_included", "provider_order_label_sources_included",
                       "full_navigation_source_closure_verified", "courier_unready_order_discovery_included"}
    if not all(manifest.get("scope_limits", {}).get(key) is True for key in required_scopes):
        raise RuntimeBlocked("export_semantic_coverage_incomplete")
    scopes = {scope["scope"]: scope for scope in contract["scopes"]}
    if len(scopes) != len(contract["scopes"]) or len(manifest.get("supplemental", [])) != len(scopes):
        raise RuntimeBlocked("export_scope_set_mismatch")
    counts, seen_scopes = {}, set()
    for entry in manifest.get("supplemental", []):
        name, count = entry.get("collection"), entry.get("count")
        scope = entry.get("scope")
        if not isinstance(name, str) or type(count) is not int or count < 0 or not isinstance(scope, str) or scope in seen_scopes:
            raise RuntimeBlocked("export_collection_coverage_invalid")
        rule = scopes.get(scope)
        if rule is None or any(entry.get(key) != replace_owner(rule.get(key), config["expected_owner_id"])
                for key in ("collection", "query", "projection", "capture_mode", "binding")):
            raise RuntimeBlocked("export_scope_projection_or_query_mismatch")
        if (entry.get("source_count") != count or entry.get("cursor_exhausted") is not True
                or not isinstance(entry.get("files"), list) or len(entry["files"]) != count
                or type(entry.get("row_limit")) is not int or not 1 <= entry["row_limit"] <= 200000
                or count > entry["row_limit"] or rule.get("required") is True and count != 1):
            raise RuntimeBlocked("export_scope_cursor_incomplete")
        if name in counts and (name != "users" or scope not in {"owner", "team"}):
            raise RuntimeBlocked("overlapping_collection_scopes_not_supported")
        seen_scopes.add(scope)
        counts[name] = counts.get(name, 0) + count
    if not set(policy.get("required_collections", [])) <= counts.keys():
        raise RuntimeBlocked("required_source_collection_not_exported")
    if any(name in counts for name in READ_COLLECTIONS | {BINDING_COLLECTION, STATE_COLLECTION, AUDIT_COLLECTION}):
        raise RuntimeBlocked("unexpected_private_or_canonical_supplemental")
    return manifest, counts, digest(policy_path.read_bytes())


def owner_is_valid(row, expected):
    return bool(isinstance(row, dict) and row.get("id") == expected and row.get("role") == "owner"
                and not row.get("disabled") and row.get("is_active") is not False and not row.get("deleted_at"))


def expected_binding(config, manifest, policy_sha):
    return {"_id": config["manifest_sha256"], "schema_version": 1, "import_complete": True,
            "source_commit": SOURCE_SHA, "owner_id": config["expected_owner_id"],
            "database_name": config["database_name"], "replica_set": REPLICA_SET,
            "manifest_sha256": config["manifest_sha256"], "state_sha256": manifest["state"]["sha256"],
            "state_revision": manifest["state"]["revision"], "coverage_policy_sha256": policy_sha}


async def verify_database(client, config, *, count_collections=True):
    """Read-only preflight; never create a marker or repair missing source data."""
    from bson.codec_options import CodecOptions
    from bson.raw_bson import RawBSONDocument
    manifest, counts, policy_sha = load_export(config)
    hello = await client.admin.command("hello")
    if (hello.get("setName") != REPLICA_SET or hello.get("hosts") != ["127.0.0.1:27019"]
            or hello.get("isWritablePrimary") is not True):
        raise RuntimeBlocked("private_replica_identity_mismatch")
    db = client[config["database_name"]]
    marker = await db[BINDING_COLLECTION].find_one({"_id": config["manifest_sha256"]})
    if marker != expected_binding(config, manifest, policy_sha):
        raise RuntimeBlocked("verified_private_import_binding_required")
    owner = config["expected_owner_id"]
    user = await db["users"].find_one({"id": owner}, {"_id": 0, "id": 1, "role": 1, "disabled": 1, "is_active": 1, "deleted_at": 1})
    if not owner_is_valid(user, owner):
        raise RuntimeBlocked("copied_canonical_owner_invalid")
    forbidden = ["password", "password_hash", "mfa_secret", "totp_secret", "access_token", "refresh_token"]
    if await db["users"].find_one({"$or": [{field: {"$exists": True}} for field in forbidden]}, {"_id": 1}):
        raise RuntimeBlocked("authentication_material_in_copy_forbidden")
    raw_collection = db[STATE_COLLECTION].with_options(codec_options=CodecOptions(document_class=RawBSONDocument))
    state = await raw_collection.find_one({"_id": owner, "owner_id": owner})
    if state is None or digest(state.raw) != manifest["state"]["sha256"] or state.get("revision") != manifest["state"]["revision"]:
        raise RuntimeBlocked("copied_canonical_state_mismatch")
    if count_collections:
        for name, count in {**counts, STATE_COLLECTION: 1, AUDIT_COLLECTION: manifest["audit"]["page_count"]}.items():
            if await db[name].count_documents({}, maxTimeMS=5000) != count:
                raise RuntimeBlocked("copied_collection_count_mismatch")
    return db, manifest


def explicit_environment(config, mode):
    if mode not in {"api", "worker"}:
        raise RuntimeBlocked("private_mode_invalid")
    return {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": os.pathsep.join([str(TOOL_ROOT), str(Path(config["source_root"]) / "backend")]),
            "PYTHON_DOTENV_DISABLED": "1", "MONGO_URL": MONGO_URL, "DB_NAME": config["database_name"],
            "MEZAN_PRIVATE_RUNTIME_ACTIVE": "1", "MEZAN_PRIVATE_RUNTIME_CONFIG": config["config_path"],
            "MEZAN_PRIVATE_RUNTIME_MODE": mode, "MEZAN_BACKGROUND_WORKER_PROCESS": "1" if mode == "worker" else "0"}
