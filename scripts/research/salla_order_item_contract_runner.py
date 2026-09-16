"""Explicit, fixture-bound Salla contract runner for MZ-ORDER-REVISION-SALLA-001."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import math
import os
import re
import socket
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

NOT_CONFIGURED = "SALLA_SANDBOX_NOT_CONFIGURED"
IDENTITY_MISMATCH = "SALLA_DEMO_STORE_IDENTITY_MISMATCH"
ENDPOINT_NOT_ALLOWED = "SALLA_P0_ENDPOINT_NOT_ALLOWED"
WRITES_DISABLED = "SALLA_SANDBOX_WRITES_DISABLED"
FIXTURE_CLASSIFICATION = "MOCK_CONTRACT_FIXTURE"
REAL_EVIDENCE_CLASSIFICATION = "SALLA_DEMO_STORE_EVIDENCE"
AMASI_EVIDENCE_CLASSIFICATION = "SALLA_LIVE_TEST_ORDER_EVIDENCE"
AMASI_MANIFEST_CLASSIFICATION = "AMASI_TEST_ORDER_MANIFEST"
AMASI_CASE_CLASSIFICATION = "AMASI_TEST_ORDER_CASE"
OFFICIAL_BASE_URL = "https://api.salla.dev/admin/v2"
CREDENTIAL_RESOLVER_ENV_UNAVAILABLE = "P0_CREDENTIAL_RESOLVER_ENV_UNAVAILABLE"
CREDENTIAL_RESOLVER_IDENTITY_UNAVAILABLE = "P0_CREDENTIAL_RESOLVER_IDENTITY_UNAVAILABLE"
CREDENTIAL_RESOLVER_FAILED = "P0_CREDENTIAL_RESOLVER_FAILED"
REQUIRED_ORDER_STATES = {"pending", "under_review", "in_progress", "paid", "partially_paid", "completed", "cancelled"}
REQUIRED_PRODUCT_KINDS = {"simple", "size_color_variant", "text_option", "checkbox_yes_no", "multi_quantity", "replacement"}
SENSITIVE_KEYS = {"access_token", "authorization", "bearer", "card", "customer", "email", "mobile", "name", "phone", "receiver", "refresh_token", "token"}
CASE_INVALID = "SALLA_P0_CASE_INVALID"
BASELINE_FETCH_REQUIRED = "SALLA_P0_BASELINE_FETCH_REQUIRED"
DELETE_REPLACEMENT_NOT_PROVEN = "SALLA_P0_DELETE_REPLACEMENT_NOT_PROVEN"
LOCAL_EVIDENCE_DURABILITY = "LOCAL_FILESYSTEM_ONLY_NO_DB_DURABILITY"
LIVE_EXECUTOR_REQUIRED = "SALLA_P0_LIVE_EXECUTOR_REQUIRED"
ATTEMPT_REPLAY_BLOCKED = "SALLA_P0_ATTEMPT_REPLAY_BLOCKED"
APPROVAL_ALREADY_CONSUMED = "SALLA_P0_APPROVAL_ALREADY_CONSUMED"
WRITE_APPROVAL_REQUIRED = "SALLA_P0_WRITE_APPROVAL_REQUIRED"
WRITE_APPROVAL_EXPIRED = "SALLA_P0_WRITE_APPROVAL_EXPIRED"
LIVE_CAPABILITY_EXPIRED = "SALLA_P0_LIVE_CAPABILITY_EXPIRED"
LIVE_CAPABILITY_MISMATCH = "SALLA_P0_LIVE_CAPABILITY_MISMATCH"
CONCURRENT_ATTEMPT_BLOCKED = "SALLA_P0_CONCURRENT_ATTEMPT_BLOCKED"
STATE_ROOT_UNSAFE = "SALLA_P0_STATE_ROOT_UNSAFE"
UPDATE_MUTATION_REQUIRED = "SALLA_P0_UPDATE_MUTATION_REQUIRED"
REDACTION_KEY_COLLISION = "SALLA_P0_REDACTION_KEY_COLLISION"
LINUX_POSIX_REQUIRED = "SALLA_P0_LINUX_POSIX_REQUIRED"
WRITE_APPROVAL_TTL_SECONDS = 300
LIVE_CAPABILITY_TTL_SECONDS = 60
UPDATE_MUTABLE_FIELDS = frozenset({"quantity", "options", "branch_id", "price", "cost", "weight"})


class ContractRunnerError(RuntimeError):
    pass


def _required_security_primitives() -> tuple[int, int, Callable[[], int]]:
    """Return mandatory Linux/POSIX primitives without a permissive fallback."""
    if os.name != "posix" or sys.platform != "linux":
        raise ContractRunnerError(LINUX_POSIX_REQUIRED)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    getuid = getattr(os, "getuid", None)
    if (
        type(nofollow) is not int
        or nofollow <= 0
        or type(directory) is not int
        or directory <= 0
        or not callable(getuid)
    ):
        raise ContractRunnerError(LINUX_POSIX_REQUIRED)
    return nofollow, directory, getuid


def _require_linux_posix() -> Any:
    """Return fcntl only after the supported-host preflight has passed."""
    _required_security_primitives()
    try:
        lock_api = importlib.import_module("fcntl")
    except ImportError:
        raise ContractRunnerError(LINUX_POSIX_REQUIRED) from None
    if not all(hasattr(lock_api, name) for name in ("flock", "LOCK_EX", "LOCK_NB", "LOCK_UN")):
        raise ContractRunnerError(LINUX_POSIX_REQUIRED)
    return lock_api


def _existing_directory_without_symlinks(path: Path) -> Path:
    normalized = Path(os.path.abspath(path))
    current = Path(normalized.anchor)
    for part in normalized.parts[1:]:
        current = current / part
        try:
            metadata = os.lstat(current)
        except OSError:
            raise ContractRunnerError(STATE_ROOT_UNSAFE) from None
        if stat.S_ISLNK(metadata.st_mode):
            raise ContractRunnerError(STATE_ROOT_UNSAFE)
    try:
        metadata = os.lstat(normalized)
    except OSError:
        raise ContractRunnerError(STATE_ROOT_UNSAFE) from None
    if not stat.S_ISDIR(metadata.st_mode):
        raise ContractRunnerError(STATE_ROOT_UNSAFE)
    return normalized


def _read_git_pointer(path: Path, prefix: str) -> str:
    nofollow, _, _ = _required_security_primitives()
    flags = os.O_RDONLY | nofollow
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ContractRunnerError(STATE_ROOT_UNSAFE)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            content = handle.read(4097)
    except ContractRunnerError:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise
    except (OSError, UnicodeError):
        raise ContractRunnerError(STATE_ROOT_UNSAFE) from None
    if len(content) > 4096:
        raise ContractRunnerError(STATE_ROOT_UNSAFE)
    lines = content.splitlines()
    if len(lines) != 1 or not lines[0].startswith(prefix):
        raise ContractRunnerError(STATE_ROOT_UNSAFE)
    value = lines[0][len(prefix):].strip()
    if not value or "\x00" in value:
        raise ContractRunnerError(STATE_ROOT_UNSAFE)
    return value


def _git_common_directory(repository_root: Path) -> Path:
    _require_linux_posix()
    git_entry = repository_root / ".git"
    try:
        metadata = os.lstat(git_entry)
    except OSError:
        raise ContractRunnerError(STATE_ROOT_UNSAFE) from None
    if stat.S_ISLNK(metadata.st_mode):
        raise ContractRunnerError(STATE_ROOT_UNSAFE)
    if stat.S_ISDIR(metadata.st_mode):
        git_directory = _existing_directory_without_symlinks(git_entry)
    elif stat.S_ISREG(metadata.st_mode):
        git_pointer = Path(_read_git_pointer(git_entry, "gitdir:"))
        if not git_pointer.is_absolute():
            git_pointer = repository_root / git_pointer
        git_directory = _existing_directory_without_symlinks(git_pointer)
    else:
        raise ContractRunnerError(STATE_ROOT_UNSAFE)

    common_pointer = git_directory / "commondir"
    if os.path.lexists(common_pointer):
        common_path = Path(_read_git_pointer(common_pointer, ""))
        if not common_path.is_absolute():
            common_path = git_directory / common_path
        return _existing_directory_without_symlinks(common_path)
    return git_directory


def _canonical_state_root() -> Path:
    """Return one clone-wide P0 ledger root shared by all linked worktrees."""
    repository_root = Path(__file__).resolve(strict=True).parents[2]
    common_git_directory = _git_common_directory(repository_root)
    return common_git_directory / "mz-p0-local-state" / "MZ-ORDER-REVISION-SALLA-001"


@dataclass(frozen=True)
class SandboxConfig:
    base_url: str
    demo_store_id: str
    token_scopes: frozenset[str]
    seed_manifest: Path
    evidence_dir: Path
    demo_confirmed: bool
    writes_enabled: bool
    destructive_retry_enabled: bool
    write_approval_id: str
    write_approval_issued_at: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "SandboxConfig":
        source = env if env is not None else os.environ
        required = {
            "base_url": source.get("SALLA_SANDBOX_BASE_URL", "").strip(),
            "demo_store_id": source.get("SALLA_DEMO_STORE_ID", "").strip() or source.get("SALLA_SANDBOX_STORE_ID", "").strip(),
            "token_scopes": source.get("SALLA_DEMO_TOKEN_SCOPES", "").strip(),
            "seed_manifest": source.get("SALLA_SANDBOX_SEED_MANIFEST", "").strip(),
            "evidence_dir": source.get("SALLA_SANDBOX_EVIDENCE_DIR", "").strip(),
        }
        if any(not value for value in required.values()):
            raise ContractRunnerError(NOT_CONFIGURED)
        if required["base_url"].rstrip("/") != OFFICIAL_BASE_URL:
            raise ContractRunnerError("SALLA_DEMO_BASE_URL_NOT_ALLOWED")
        scopes = frozenset(x.strip() for x in required["token_scopes"].split(",") if x.strip())
        return cls(
            OFFICIAL_BASE_URL, required["demo_store_id"], scopes,
            Path(required["seed_manifest"]), Path(required["evidence_dir"]),
            source.get("SALLA_DEMO_STORE_CONFIRMED", "").casefold() == "true",
            source.get("SALLA_SANDBOX_RUN_WRITES", "").casefold() == "true",
            source.get("SALLA_DEMO_ALLOW_DESTRUCTIVE_RETRY", "").casefold() == "true",
            source.get("SALLA_P0_WRITE_APPROVAL_ID", "").strip(),
            source.get("SALLA_P0_WRITE_APPROVAL_ISSUED_AT", "").strip(),
        )


@dataclass(frozen=True)
class AmasiTestConfig(SandboxConfig):
    """Separate opt-in; inherited store-id field is only a compatibility name."""
    expected_store_type: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "AmasiTestConfig":
        source = env if env is not None else os.environ
        keys = ("STORE_ID", "STORE_TYPE", "TOKEN_SCOPES", "MANIFEST", "EVIDENCE_DIR")
        values = {key: source.get(f"SALLA_AMASI_TEST_{key}", "").strip() for key in keys}
        if not all(values.values()):
            raise ContractRunnerError("AMASI_TEST_NOT_CONFIGURED")
        if values["STORE_TYPE"].casefold() == "demo":
            raise ContractRunnerError("AMASI_TEST_STORE_TYPE_INVALID")
        return cls(
            OFFICIAL_BASE_URL, values["STORE_ID"],
            frozenset(x.strip() for x in values["TOKEN_SCOPES"].split(",") if x.strip()),
            Path(values["MANIFEST"]), Path(values["EVIDENCE_DIR"]), False,
            source.get("SALLA_AMASI_TEST_RUN_WRITES", "").casefold() == "true", False,
            source.get("SALLA_P0_WRITE_APPROVAL_ID", "").strip(),
            source.get("SALLA_P0_WRITE_APPROVAL_ISSUED_AT", "").strip(),
            values["STORE_TYPE"],
        )


class _NoLiveTestRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "AMASI_TEST_REDIRECT_REJECTED", headers, fp)


def _is_sensitive(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return normalized in SENSITIVE_KEYS or any(x in normalized for x in ("secret", "password", "authorization", "token"))


def sanitize(value: Any, *, key: str = "", exact_secrets: tuple[str, ...] | frozenset[str] = ()) -> Any:
    if key and _is_sensitive(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            source_key = str(raw_key)
            sanitized_key = sanitize(source_key, exact_secrets=exact_secrets)
            if sanitized_key in result:
                raise ContractRunnerError(REDACTION_KEY_COLLISION)
            result[sanitized_key] = sanitize(raw_value, key=source_key, exact_secrets=exact_secrets)
        return result
    if isinstance(value, list):
        return [sanitize(x, exact_secrets=exact_secrets) for x in value]
    if isinstance(value, str):
        for secret in sorted({secret for secret in exact_secrets if isinstance(secret, str) and secret}, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)(access_token|refresh_token|token)=([^&\s]+)", r"\1=[REDACTED]", value)
    return value


def load_seed_manifest(path: Path, *, classification: str = "SANDBOX_SEED_MANIFEST") -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH") from exc
    if not isinstance(data, dict) or data.get("classification") != classification:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    return data


def _load_config_seed(config: SandboxConfig) -> dict[str, Any]:
    classification = AMASI_MANIFEST_CLASSIFICATION if isinstance(config, AmasiTestConfig) else "SANDBOX_SEED_MANIFEST"
    return load_seed_manifest(config.seed_manifest, classification=classification)


def validate_endpoint(method: str, path: str) -> None:
    parsed = urllib.parse.urlsplit(path)
    clean = parsed.path.rstrip("/") or "/"
    allowed = (
        method == "GET" and clean == "/store/info"
        or method == "GET" and bool(re.fullmatch(r"/orders/[^/]+", clean))
        or method == "GET" and clean == "/orders/items" and bool(urllib.parse.parse_qs(parsed.query).get("order_id"))
        or method == "GET" and bool(re.fullmatch(r"/products/[^/]+", clean))
        or method == "POST" and clean == "/orders/items"
        or method in {"PUT", "DELETE"} and bool(re.fullmatch(r"/orders/items/[^/]+", clean))
    )
    if not allowed:
        raise ContractRunnerError(ENDPOINT_NOT_ALLOWED)


def _decode_response(status: int, raw: str, headers: Any, started: float) -> dict[str, Any]:
    parse_error = None
    if not raw:
        body: Any = None
        parse_error = "EMPTY_RESPONSE"
    else:
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {"unparsed": raw[:2000]}
            parse_error = "NON_JSON_RESPONSE"
    return {"status": status, "body": body, "parse_error": parse_error, "request_id": headers.get("X-Request-Id") if headers else None, "response_id": headers.get("X-Correlation-Id") if headers else None, "elapsed_ms": round((time.monotonic() - started) * 1000)}


def _store_id_candidates(store_id: str) -> list[str | int]:
    candidates: list[str | int] = [store_id]
    if re.fullmatch(r"0|[1-9][0-9]*", store_id):
        candidates.append(int(store_id))
    return candidates


async def resolve_sandbox_access_token(
    config: SandboxConfig,
    db: Any,
    token_resolver: Callable[..., Any],
) -> str:
    """Resolve one Demo Store owner's token without accepting raw token input."""
    try:
        rows = await (
            db.salla_integrations.find(
                {"store_id": {"$in": _store_id_candidates(config.demo_store_id)}},
                {"_id": 0, "user_id": 1},
            )
            .limit(2)
            .to_list(length=2)
        )
    except Exception:
        raise ContractRunnerError(CREDENTIAL_RESOLVER_FAILED) from None
    row = rows[0] if len(rows) == 1 and isinstance(rows[0], dict) else None
    user_id = str((row or {}).get("user_id") or "").strip()
    if not user_id:
        raise ContractRunnerError(CREDENTIAL_RESOLVER_IDENTITY_UNAVAILABLE)
    try:
        token = await token_resolver(
            db,
            user_id,
            recover_needs_reauth=False,
            minimum_validity_sec=300,
        )
    except Exception:
        raise ContractRunnerError(CREDENTIAL_RESOLVER_FAILED) from None
    if not isinstance(token, str) or not token.strip():
        raise ContractRunnerError(CREDENTIAL_RESOLVER_FAILED)
    return token.strip()


def _runtime_credential_dependencies() -> tuple[Callable[..., Any], Callable[..., Any]]:
    backend_dir = Path(__file__).resolve().parents[2] / "backend"
    backend_path = str(backend_dir)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)
    from motor.motor_asyncio import AsyncIOMotorClient
    from salla_integration.service import ensure_fresh_access_token

    return AsyncIOMotorClient, ensure_fresh_access_token


class MockTransport:
    evidence_classification = FIXTURE_CLASSIFICATION

    def __init__(self, responses: dict[tuple[str, str], Any] | None = None):
        self.calls: list[tuple[str, str, str]] = []
        self.responses = responses or {}

    def request(self, method: str, path: str, body: dict[str, Any] | None, correlation_id: str) -> dict[str, Any]:
        validate_endpoint(method, path)
        self.calls.append((method, path, correlation_id))
        response = self.responses.get((method, path))
        if isinstance(response, list):
            return response.pop(0)
        return response or {"status": 200, "body": {"success": True, "data": []}}


def _data(response: dict[str, Any]) -> Any:
    if not isinstance(response, dict):
        return None
    body = response.get("body")
    return body.get("data") if isinstance(body, dict) else None


def _item_product_id(item: dict[str, Any]) -> Any:
    nested = item.get("product")
    return item.get("product_id") or (nested.get("id") if isinstance(nested, dict) else None)


def verify_demo_identity(config: SandboxConfig, transport: Any) -> dict[str, Any]:
    response = transport.request("GET", "/store/info", None, "p0-identity")
    data = _data(response)
    actual_id = str(data.get("id", "")) if isinstance(data, dict) else ""
    actual_type = str(data.get("type", "")).casefold() if isinstance(data, dict) else ""
    if not isinstance(response, dict) or response.get("status") != 200 or actual_id != config.demo_store_id or actual_type != "demo":
        raise ContractRunnerError(IDENTITY_MISMATCH)
    return {"verified": True, "store_id": actual_id, "store_type": actual_type}


def _nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _id_text(value: Any, error: str = CASE_INVALID) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ContractRunnerError(error)
    text = str(value).strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9._:-]+", text):
        raise ContractRunnerError(error)
    return text


def _strict_object(value: Any, *, allowed: set[str], required: set[str] = set(), error: str = CASE_INVALID) -> dict[str, Any]:
    if type(value) is not dict or not required.issubset(value) or not set(value).issubset(allowed):
        raise ContractRunnerError(error)
    return value


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _parse_option_tuple_list(raw: Any, *, allowed_options: set[str], allowed_values: set[str], allow_text: bool) -> frozenset[tuple[str, str]]:
    if not isinstance(raw, list):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    pairs: set[tuple[str, str]] = set()
    for entry in raw:
        if type(entry) is not dict or set(entry) not in ({"option_id", "value_id"}, {"option_id", "value_kind"}):
            raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        option_id = _id_text(entry["option_id"], "BLOCKED_SEED_MISMATCH")
        if option_id not in allowed_options:
            raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        if "value_id" in entry:
            value_key = _id_text(entry["value_id"], "BLOCKED_SEED_MISMATCH")
            if value_key not in allowed_values:
                raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        else:
            if not allow_text or entry["value_kind"] != "text":
                raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
            value_key = "@text"
        pair = (option_id, value_key)
        if pair in pairs:
            raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        pairs.add(pair)
    return frozenset(pairs)


def _seed_product_relations(row: dict[str, Any]) -> tuple[frozenset[tuple[str, str]], tuple[dict[str, Any], ...]]:
    option_ids = {_id_text(x, "BLOCKED_SEED_MISMATCH") for x in row.get("option_ids", [])}
    value_ids = {_id_text(x, "BLOCKED_SEED_MISMATCH") for x in row.get("value_ids", [])}
    option_pairs = _parse_option_tuple_list(
        row.get("option_value_tuples", []),
        allowed_options=option_ids,
        allowed_values=value_ids,
        allow_text=row.get("kind") == "text_option",
    )
    if {option_id for option_id, _ in option_pairs} != option_ids:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    if {value_id for _, value_id in option_pairs if value_id != "@text"} != value_ids:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")

    variant_ids = {_id_text(x, "BLOCKED_SEED_MISMATCH") for x in row.get("variant_ids", [])}
    raw_variants = row.get("variant_tuples", [])
    if not isinstance(raw_variants, list):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    variants: list[dict[str, Any]] = []
    seen_variant_ids: set[str] = set()
    seen_variant_skus: set[str] = set()
    for variant in raw_variants:
        _strict_object(
            variant,
            allowed={"product_id", "variant_id", "sku", "option_value_tuples"},
            required={"product_id", "variant_id", "sku", "option_value_tuples"},
            error="BLOCKED_SEED_MISMATCH",
        )
        product_id = _id_text(variant["product_id"], "BLOCKED_SEED_MISMATCH")
        variant_id = _id_text(variant["variant_id"], "BLOCKED_SEED_MISMATCH")
        sku = _id_text(variant["sku"], "BLOCKED_SEED_MISMATCH")
        variant_pairs = _parse_option_tuple_list(
            variant["option_value_tuples"],
            allowed_options=option_ids,
            allowed_values=value_ids,
            allow_text=False,
        )
        if product_id != str(row["product_id"]) or variant_id not in variant_ids or not variant_pairs or not variant_pairs.issubset(option_pairs):
            raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        if variant_id in seen_variant_ids or sku in seen_variant_skus:
            raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        seen_variant_ids.add(variant_id)
        seen_variant_skus.add(sku)
        variants.append({"product_id": product_id, "variant_id": variant_id, "sku": sku, "option_pairs": variant_pairs})
    if seen_variant_ids != variant_ids:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    return option_pairs, tuple(variants)


def validate_seed_structure(config: SandboxConfig, seed: dict[str, Any]) -> None:
    amasi = isinstance(config, AmasiTestConfig)
    if amasi:
        _strict_object(seed, allowed={"classification", "store_id", "orders", "products", "downstream_reviewed"},
                       required={"classification", "store_id", "orders", "products", "downstream_reviewed"}, error="BLOCKED_SEED_MISMATCH")
        if seed["classification"] != AMASI_MANIFEST_CLASSIFICATION or seed["downstream_reviewed"] is not True:
            raise ContractRunnerError("AMASI_TEST_FIXTURE_REVIEW_REQUIRED")
    if str(seed.get("store_id", "")) != config.demo_store_id:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    orders = seed.get("orders", [])
    required_fields = {"state", "order_id", "order_number", "item_id", "product_id", "sku", "payment_method", "branch_id"}
    if (
        not isinstance(orders, list)
        or len(orders) != (1 if amasi else len(REQUIRED_ORDER_STATES))
        or any(type(x) is not dict for x in orders)
        or (not amasi and {str(x.get("state", "")) for x in orders} != REQUIRED_ORDER_STATES)
        or any(not all(_nonempty(x.get(k)) for k in required_fields) for x in orders)
    ):
        raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
    if amasi:
        for row in orders:
            fields = required_fields | {"test_customer_id", "disposable_test_order", "preserve_item_id"}
            _strict_object(row, allowed=fields, required=fields, error="BLOCKED_SEED_MISMATCH")
            if not isinstance(row["state"], str) or row["state"] not in {"pending", "under_review"} or row["payment_method"] != "bank" or row["disposable_test_order"] is not True:
                raise ContractRunnerError("AMASI_TEST_UNPAID_BANK_ORDER_REQUIRED")
            for key in ("test_customer_id", "preserve_item_id", "order_number", "branch_id"):
                _id_text(row[key], "BLOCKED_SEED_MISMATCH")
    products = seed.get("products", [])
    if (
        not isinstance(products, list)
        or (not 1 <= len(products) <= 6 if amasi else len(products) != len(REQUIRED_PRODUCT_KINDS))
        or any(type(x) is not dict for x in products)
        or (not {str(x.get("kind", "")) for x in products}.issubset(REQUIRED_PRODUCT_KINDS) if amasi else {str(x.get("kind", "")) for x in products} != REQUIRED_PRODUCT_KINDS)
        or any(not _nonempty(x.get("product_id")) or not _nonempty(x.get("sku")) for x in products)
    ):
        raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
    try:
        order_ids = [_id_text(x["order_id"], "BLOCKED_SEED_MISMATCH") for x in orders]
        item_ids = [_id_text(x["item_id"], "BLOCKED_SEED_MISMATCH") for x in orders]
        product_ids = [_id_text(x["product_id"], "BLOCKED_SEED_MISMATCH") for x in products]
        product_skus = [_id_text(x["sku"], "BLOCKED_SEED_MISMATCH") for x in products]
    except (KeyError, ContractRunnerError):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH") from None
    if len(set(order_ids)) != len(order_ids) or len(set(item_ids)) != len(item_ids) or len(set(product_ids)) != len(product_ids) or len(set(product_skus)) != len(product_skus):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    for row in products:
        if amasi:
            _strict_object(row, allowed={"kind", "product_id", "sku", "branch_id", "variant_ids", "option_ids", "value_ids", "option_value_tuples", "variant_tuples"},
                           required={"kind", "product_id", "sku", "branch_id"}, error="BLOCKED_SEED_MISMATCH")
        if row.get("kind") == "size_color_variant" and not all(_nonempty(row.get(k)) for k in ("variant_ids", "option_ids", "value_ids")):
            raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
        if row.get("kind") in {"text_option", "checkbox_yes_no"} and not _nonempty(row.get("option_ids")):
            raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
        if row.get("kind") == "checkbox_yes_no" and not _nonempty(row.get("value_ids")):
            raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
        for key in ("variant_ids", "option_ids", "value_ids"):
            if key in row:
                values = row[key]
                if not isinstance(values, list):
                    raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
                normalized = [_id_text(x, "BLOCKED_SEED_MISMATCH") for x in values]
                if len(normalized) != len(set(normalized)):
                    raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
        _seed_product_relations(row)
    product_pairs = {(str(row["product_id"]), str(row["sku"])) for row in products}
    if any((str(row["product_id"]), str(row["sku"])) not in product_pairs for row in orders):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    if any(_nonempty(v) for k, v in _walk(seed) if k.casefold() in {"customer", "customer_name", "email", "mobile", "phone", "address"}):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")


def _status_slug(order: dict[str, Any]) -> str:
    value = order.get("status")
    if isinstance(value, dict):
        value = value.get("slug") or value.get("name")
    return str(value or "").strip().casefold().replace(" ", "_")


def _product_contract_relations(product_id: str, product: dict[str, Any]) -> tuple[frozenset[tuple[str, str]], frozenset[tuple[str, str, str, frozenset[tuple[str, str]]]]]:
    variants = product.get("variants") if isinstance(product.get("variants"), list) else []
    options = product.get("options") if isinstance(product.get("options"), list) else []
    option_pairs: set[tuple[str, str]] = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = option.get("id") or option.get("option_id")
        if not _nonempty(option_id):
            continue
        option_id = str(option_id)
        values = option.get("values") if isinstance(option.get("values"), list) else option.get("value")
        if not isinstance(values, list):
            values = []
        concrete = {
            str(row.get("id") or row.get("value_id"))
            for row in values
            if isinstance(row, dict) and _nonempty(row.get("id") or row.get("value_id"))
        }
        option_pairs.update((option_id, value_id) for value_id in concrete)
        option_type = str(option.get("type") or option.get("value_type") or "").casefold()
        if not concrete and option_type in {"text", "textarea", "string"}:
            option_pairs.add((option_id, "@text"))

    variant_relations: set[tuple[str, str, str, frozenset[tuple[str, str]]]] = set()
    for variant in variants:
        if not isinstance(variant, dict) or not _nonempty(variant.get("id")) or not _nonempty(variant.get("sku")):
            continue
        selections = _option_selections(variant.get("options"))
        relation_pairs = frozenset((option_id, value) for option_id, kind, value in selections if kind == "id")
        variant_relations.add((product_id, str(variant["id"]), str(variant["sku"]), relation_pairs))
    return frozenset(option_pairs), frozenset(variant_relations)


def _hash_id(value: Any) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def readiness(config: SandboxConfig, transport: Any) -> dict[str, Any]:
    try:
        if isinstance(config, AmasiTestConfig):
            response = transport.request("GET", "/store/info", None, "p0-identity")
            data = _data(response)
            if (response.get("status") != 200 or not isinstance(data, dict)
                    or str(data.get("id", "")) != config.demo_store_id
                    or data.get("type") != config.expected_store_type):
                raise ContractRunnerError("AMASI_TEST_STORE_IDENTITY_MISMATCH")
            identity = {"store_id": str(data["id"])}
        else:
            identity = verify_demo_identity(config, transport)
    except ContractRunnerError as exc:
        return {"status": "BLOCKED_IDENTITY_MISMATCH", "reason": str(exc)}
    if "orders.read_write" not in config.token_scopes or not ({"products.read", "products.read_write"} & config.token_scopes):
        return {"status": "BLOCKED_SCOPE_MISSING", "required": ["orders.read_write", "products.read|products.read_write"]}
    if isinstance(config, AmasiTestConfig) and "shipping.read" not in config.token_scopes:
        return {"status": "BLOCKED_SCOPE_MISSING", "required": ["shipping.read"]}
    try:
        seed = _load_config_seed(config)
        validate_seed_structure(config, seed)
    except ContractRunnerError as exc:
        return {"status": str(exc) if str(exc).startswith("BLOCKED_") else "BLOCKED_SEED_MISMATCH", "reason": str(exc)}
    for row in seed["orders"]:
        order_id = str(row["order_id"])
        order = _data(transport.request("GET", f"/orders/{order_id}", None, "p0-readiness"))
        items = _data(transport.request("GET", f"/orders/items?order_id={urllib.parse.quote(order_id)}", None, "p0-readiness"))
        item_matches = [
            x for x in items or []
            if isinstance(x, dict)
            and str(x.get("id")) == str(row["item_id"])
            and str(_item_product_id(x)) == str(row["product_id"])
            and str(x.get("sku")) == str(row["sku"])
        ]
        if not isinstance(order, dict) or str(order.get("id", "")) != order_id or _status_slug(order) != row["state"] or len(item_matches) != 1:
            return {"status": "BLOCKED_SEED_MISMATCH", "order_id_hash": _hash_id(order_id)}
        if isinstance(config, AmasiTestConfig):
            try:
                _validate_amasi_order(order, row)
                _verify_amasi_no_shipments(transport, order_id, "p0-readiness")
            except ContractRunnerError as exc:
                return {"status": str(exc)}
    for row in seed["products"]:
        product_id = str(row["product_id"])
        product = _data(transport.request("GET", f"/products/{product_id}", None, "p0-readiness"))
        if not isinstance(product, dict) or str(product.get("id", "")) != product_id or str(product.get("sku", "")) != str(row["sku"]):
            return {"status": "BLOCKED_FIXTURE_MISSING", "product_id_hash": _hash_id(product_id)}
        expected_option_pairs, expected_variants = _seed_product_relations(row)
        actual_option_pairs, actual_variants = _product_contract_relations(product_id, product)
        expected_variant_relations = frozenset(
            (variant["product_id"], variant["variant_id"], variant["sku"], variant["option_pairs"])
            for variant in expected_variants
        )
        if not expected_option_pairs.issubset(actual_option_pairs) or not expected_variant_relations.issubset(actual_variants):
            return {"status": "BLOCKED_FIXTURE_MISSING", "product_id_hash": _hash_id(product_id)}
    try:
        config.evidence_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=config.evidence_dir, prefix="p0-readiness-", delete=True):
            pass
    except OSError:
        return {"status": "BLOCKED_FIXTURE_MISSING", "reason": "EVIDENCE_DIRECTORY_NOT_WRITABLE"}
    ready_status = "READY_FOR_SANDBOX_WRITES" if config.demo_confirmed and config.writes_enabled else "READY_FOR_READ_ONLY"
    if isinstance(config, AmasiTestConfig) and config.writes_enabled:
        ready_status = "READY_FOR_AMASI_TEST_WRITES"
    return {
        "status": ready_status,
        "identity_verified": False,
        "response_identity_matched": True, "store_id_hash": _hash_id(identity["store_id"]),
        "scopes_verified": ["orders.read_write", "products.read", *(["shipping.read"] if isinstance(config, AmasiTestConfig) else [])],
        "transactions_scope_required": False, "branches_scope_required": False,
    }


CASE_FIELDS = {
    "classification", "id", "order_id", "method", "path", "before_fetch_path",
    "after_fetch_path", "body", "assertions", "retry_once",
    "disposable_order_confirmed", "simulate_lost_response", "steps",
    "client_request_id",
}
BODY_FIELDS = {
    "order_id", "product_id", "identifier", "identifier_type", "sku",
    "variant_id", "branch_id", "quantity", "options", "price", "cost", "weight",
}
ASSERTION_PATH = re.compile(
    r"^(before|after)\.(?:fetch_ok|order_id|order_status|order_total|paid_amount|outstanding_amount|payment_status|payment_urls|transactions|items_count|items\.[0-9]+\.(?:item_id|product_id|variant_id|sku|quantity|options|price|cost|weight|branch_id|total|branches_quantity))$"
)


def _product_bindings(row: dict[str, Any]) -> list[dict[str, Any]]:
    option_pairs, variants = _seed_product_relations(row)
    bindings = [{
        "product": row,
        "product_id": str(row["product_id"]),
        "variant_id": None,
        "sku": str(row["sku"]),
        "option_pairs": option_pairs,
    }]
    bindings.extend({"product": row, **variant} for variant in variants)
    return bindings


def _matching_product_binding(seed: dict[str, Any], body: dict[str, Any], order_row: dict[str, Any], method: str) -> dict[str, Any]:
    filters: list[tuple[str, str]] = []
    if "product_id" in body:
        filters.append(("product_id", _id_text(body["product_id"])))
    if "sku" in body:
        filters.append(("sku", _id_text(body["sku"])))
    if "variant_id" in body:
        filters.append(("variant_id", _id_text(body["variant_id"])))
    if "identifier" in body or "identifier_type" in body:
        identifier_type = body.get("identifier_type")
        if identifier_type not in {"id", "sku", "variant_id"} or "identifier" not in body:
            raise ContractRunnerError(CASE_INVALID)
        filters.append((str(identifier_type), _id_text(body["identifier"])))
    if method == "PUT" and not filters:
        filters.extend((("product_id", str(order_row["product_id"])), ("sku", str(order_row["sku"]))))
        if _nonempty(order_row.get("variant_id")):
            filters.append(("variant_id", str(order_row["variant_id"])))
    if method == "POST" and not filters:
        raise ContractRunnerError("SALLA_P0_PRODUCT_ID_NOT_AUTHORIZED")

    def matches(binding: dict[str, Any]) -> bool:
        for kind, expected in filters:
            if kind in {"product_id", "id"} and binding["product_id"] != expected:
                return False
            if kind == "sku" and binding["sku"] != expected:
                return False
            if kind == "variant_id" and binding["variant_id"] != expected:
                return False
        return True

    bindings = [binding for row in seed["products"] for binding in _product_bindings(row) if matches(binding)]
    if len(bindings) != 1:
        raise ContractRunnerError("SALLA_P0_PRODUCT_ID_NOT_AUTHORIZED")
    selected = bindings[0]
    if method == "PUT" and (selected["product_id"] != str(order_row["product_id"]) or selected["sku"] != str(order_row["sku"])):
        raise ContractRunnerError("SALLA_P0_PRODUCT_ID_NOT_AUTHORIZED")
    return selected


def _validate_assertions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ContractRunnerError(CASE_INVALID)
    for assertion in value:
        _strict_object(assertion, allowed={"path", "equals"}, required={"path", "equals"})
        if not isinstance(assertion["path"], str) or not ASSERTION_PATH.fullmatch(assertion["path"]):
            raise ContractRunnerError(CASE_INVALID)
        try:
            json.dumps(assertion["equals"], allow_nan=False)
        except (TypeError, ValueError):
            raise ContractRunnerError(CASE_INVALID) from None
    return value


def _validate_options(options: Any, binding: dict[str, Any]) -> frozenset[tuple[str, str]]:
    if not isinstance(options, list):
        raise ContractRunnerError(CASE_INVALID)
    seen: set[str] = set()
    product = binding["product"]
    allowed_pairs: frozenset[tuple[str, str]] = binding["option_pairs"]
    allowed_options = {option_id for option_id, _ in allowed_pairs}
    selected_pairs: set[tuple[str, str]] = set()
    for option in options:
        _strict_object(option, allowed={"option_id", "value_id", "value"}, required={"option_id"})
        option_id = _id_text(option["option_id"])
        if option_id in seen or option_id not in allowed_options:
            raise ContractRunnerError("SALLA_P0_OPTION_ID_NOT_AUTHORIZED")
        seen.add(option_id)
        has_value_id = "value_id" in option
        has_text_value = "value" in option
        if has_value_id == has_text_value:
            raise ContractRunnerError(CASE_INVALID)
        value_key = _id_text(option["value_id"]) if has_value_id else "@text"
        if has_text_value and (product.get("kind") != "text_option" or not isinstance(option["value"], str) or not option["value"].strip()):
            raise ContractRunnerError(CASE_INVALID)
        pair = (option_id, value_key)
        if pair not in allowed_pairs:
            raise ContractRunnerError("SALLA_P0_VALUE_ID_NOT_AUTHORIZED")
        selected_pairs.add(pair)
    if binding["variant_id"] is not None and selected_pairs != set(allowed_pairs):
        raise ContractRunnerError("SALLA_P0_VARIANT_OPTION_TUPLE_MISMATCH")
    return frozenset(selected_pairs)


def _validate_delete_steps(steps: Any, *, order_id: str, item_id: str, seed: dict[str, Any]) -> None:
    if not isinstance(steps, list) or len(steps) != 3:
        raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_REQUIRED")
    expected_actions = ["create_replacement", "fetch_confirm_replacement", "delete_old"]
    allowed_by_action = {
        "create_replacement": {"action", "order_id", "item_id", "replacement_item_id", "replacement_product_id", "replacement_sku", "response_status"},
        "fetch_confirm_replacement": {"action", "order_id", "item_id", "replacement_item_id", "replacement_product_id", "replacement_sku", "fetch_ok"},
        "delete_old": {"action", "order_id", "item_id", "replacement_item_id"},
    }
    for step, action in zip(steps, expected_actions):
        allowed = allowed_by_action[action]
        _strict_object(step, allowed=allowed, required=allowed)
        if step["action"] != action or _id_text(step["order_id"]) != order_id or _id_text(step["item_id"]) != item_id:
            raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_MISMATCH")
    replacement_ids = {_id_text(step["replacement_item_id"]) for step in steps}
    if len(replacement_ids) != 1 or item_id in replacement_ids:
        raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_MISMATCH")
    create, verify, _delete = steps
    if isinstance(create["response_status"], bool) or not isinstance(create["response_status"], int) or not 200 <= create["response_status"] < 300:
        raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_REQUIRED")
    if verify["fetch_ok"] is not True:
        raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_REQUIRED")
    product_pairs = {
        (str(row["product_id"]), str(row["sku"]))
        for row in seed["products"]
        if row.get("kind") == "replacement"
    }
    create_pair = (_id_text(create["replacement_product_id"]), _id_text(create["replacement_sku"]))
    verify_pair = (_id_text(verify["replacement_product_id"]), _id_text(verify["replacement_sku"]))
    if create_pair != verify_pair or create_pair not in product_pairs:
        raise ContractRunnerError("DELETE_REPLACEMENT_PROOF_MISMATCH")


def validate_case(case: dict[str, Any], seed: dict[str, Any]) -> None:
    _strict_object(case, allowed=CASE_FIELDS, required={"id", "order_id", "method", "path", "body", "assertions"})
    amasi = seed.get("classification") == AMASI_MANIFEST_CLASSIFICATION
    expected_classification = AMASI_CASE_CLASSIFICATION if amasi else "SANDBOX_CASE_TEMPLATE"
    if case.get("classification", "SANDBOX_CASE_TEMPLATE") != expected_classification:
        raise ContractRunnerError(CASE_INVALID)
    _id_text(case["id"])
    order_id = _id_text(case["order_id"], "SALLA_P0_ORDER_ID_NOT_AUTHORIZED")
    order_rows = [row for row in seed["orders"] if str(row.get("order_id")) == order_id]
    if len(order_rows) != 1:
        raise ContractRunnerError("SALLA_P0_ORDER_ID_NOT_AUTHORIZED")
    order_row = order_rows[0]
    method = case["method"]
    path = case["path"]
    if not isinstance(method, str) or method not in {"POST", "PUT", "DELETE"} or not isinstance(path, str):
        raise ContractRunnerError(CASE_INVALID)
    expected_post_path = "/orders/items"
    item_match = re.fullmatch(r"/orders/items/([A-Za-z0-9._:-]+)", path)
    if (method == "POST" and path != expected_post_path) or (method in {"PUT", "DELETE"} and not item_match):
        raise ContractRunnerError(ENDPOINT_NOT_ALLOWED)
    validate_endpoint(method, path)
    body = _strict_object(case["body"], allowed=BODY_FIELDS)
    if method == "DELETE" and body:
        raise ContractRunnerError(CASE_INVALID)
    if method == "PUT" and not (set(body) & UPDATE_MUTABLE_FIELDS):
        raise ContractRunnerError(UPDATE_MUTATION_REQUIRED)
    if method in {"POST", "PUT"}:
        if "order_id" not in body or _id_text(body["order_id"], "SALLA_P0_ORDER_ID_NOT_AUTHORIZED") != order_id:
            raise ContractRunnerError("SALLA_P0_ORDER_ID_NOT_AUTHORIZED")
    item_id = str(item_match.group(1)) if item_match else ""
    if item_id and str(order_row["item_id"]) != item_id:
        raise ContractRunnerError("SALLA_P0_ITEM_ID_NOT_AUTHORIZED")
    if amasi and item_id == str(order_row["preserve_item_id"]):
        raise ContractRunnerError("AMASI_TEST_ORIGINAL_ITEM_PROTECTED")

    for name in ("retry_once", "disposable_order_confirmed", "simulate_lost_response"):
        if name in case and type(case[name]) is not bool:
            raise ContractRunnerError(CASE_INVALID)
    if case.get("simulate_lost_response", False):
        raise ContractRunnerError("SALLA_P0_SIMULATED_LOST_RESPONSE_UNSUPPORTED")
    _validate_assertions(case["assertions"])
    correlation_id = case.get("client_request_id")
    if correlation_id is not None:
        _id_text(correlation_id)
    expected_fetch = f"/orders/items?order_id={urllib.parse.quote(order_id, safe='')}"
    for name in ("before_fetch_path", "after_fetch_path"):
        if name in case and case[name] != expected_fetch:
            raise ContractRunnerError(CASE_INVALID)

    if "quantity" in body and (type(body["quantity"]) is not int or body["quantity"] <= 0):
        raise ContractRunnerError(CASE_INVALID)
    if method == "POST" and "quantity" not in body:
        raise ContractRunnerError(CASE_INVALID)
    for name in ("price", "cost", "weight"):
        if name in body and (isinstance(body[name], bool) or not isinstance(body[name], (int, float)) or not math.isfinite(body[name]) or body[name] < 0):
            raise ContractRunnerError(CASE_INVALID)

    steps = case.get("steps", [])
    if method == "DELETE" and amasi:
        if steps != [] or str(order_row["preserve_item_id"]) == item_id:
            raise ContractRunnerError("AMASI_TEST_ORIGINAL_ITEM_PROTECTED")
    elif method == "DELETE":
        _validate_delete_steps(steps, order_id=order_id, item_id=item_id, seed=seed)
    elif steps != []:
        raise ContractRunnerError(CASE_INVALID)

    if method != "DELETE":
        binding = _matching_product_binding(seed, body, order_row, method)
        product = binding["product"]
        if "branch_id" in body:
            branch_id = _id_text(body["branch_id"])
            allowed_branches = {str(order_row["branch_id"])}
            if _nonempty(product.get("branch_id")):
                allowed_branches &= {str(product["branch_id"])}
            if branch_id not in allowed_branches:
                raise ContractRunnerError("SALLA_P0_BRANCH_ID_NOT_AUTHORIZED")
        elif method == "POST":
            raise ContractRunnerError("SALLA_P0_BRANCH_ID_NOT_AUTHORIZED")
        if "options" in body:
            _validate_options(body["options"], binding)
        elif binding["variant_id"] is not None:
            raise ContractRunnerError("SALLA_P0_VARIANT_OPTION_TUPLE_MISMATCH")
    if amasi:
        if case.get("disposable_order_confirmed") is not True or case.get("retry_once", False):
            raise ContractRunnerError("AMASI_TEST_SINGLE_REVIEWED_ATTEMPT_REQUIRED")
        if set(body) & {"price", "cost", "weight"} or body.get("quantity", 1) > 2:
            raise ContractRunnerError("AMASI_TEST_MUTATION_LIMIT_EXCEEDED")
        for path in ("before.order_total", "after.order_total"):
            totals = [check["equals"] for check in case["assertions"] if check["path"] == path]
            if len(totals) != 1 or isinstance(totals[0], bool) or not isinstance(totals[0], (int, float)) or not math.isfinite(totals[0]) or totals[0] <= 0:
                raise ContractRunnerError("AMASI_TEST_REVIEWED_TOTALS_REQUIRED")
        if method == "POST" and binding["option_pairs"]:
            selected = _validate_options(body.get("options", []), binding)
            if {key for key, _ in selected} != {key for key, _ in binding["option_pairs"]}:
                raise ContractRunnerError("AMASI_TEST_OPTIONS_REQUIRED")


def validate_prewrite_gates(config: SandboxConfig, case: dict[str, Any]) -> None:
    if isinstance(config, AmasiTestConfig) and not config.writes_enabled:
        raise ContractRunnerError("AMASI_TEST_WRITES_DISABLED")
    if case.get("retry_once") and (not config.destructive_retry_enabled or case.get("disposable_order_confirmed") is not True):
        raise ContractRunnerError("SALLA_DESTRUCTIVE_RETRY_NOT_CONFIRMED")


def _write_approval_expiry(config: SandboxConfig, now: datetime) -> datetime:
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", config.write_approval_id):
        raise ContractRunnerError(WRITE_APPROVAL_REQUIRED)
    try:
        issued_at = datetime.fromisoformat(config.write_approval_issued_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ContractRunnerError(WRITE_APPROVAL_REQUIRED) from None
    if issued_at.tzinfo is None:
        raise ContractRunnerError(WRITE_APPROVAL_REQUIRED)
    issued_at = issued_at.astimezone(timezone.utc)
    now = now.astimezone(timezone.utc)
    approval_expires = issued_at + timedelta(seconds=WRITE_APPROVAL_TTL_SECONDS)
    if issued_at > now + timedelta(seconds=5) or now > approval_expires:
        raise ContractRunnerError(WRITE_APPROVAL_EXPIRED)
    return min(approval_expires, now + timedelta(seconds=LIVE_CAPABILITY_TTL_SECONDS))


def ensure_replacement_order(steps: list[dict[str, Any]]) -> None:
    """Compatibility helper for callers that only need ordering, not full binding."""
    actions = [str(x.get("action")) for x in steps if isinstance(x, dict)]
    if actions and actions != ["create_replacement", "fetch_confirm_replacement", "delete_old"]:
        raise ContractRunnerError("DELETE_FIRST_FORBIDDEN")


def _amount(value: Any) -> Any:
    return value.get("amount") if isinstance(value, dict) else value


def _item_amount(item: dict[str, Any], name: str) -> Any:
    direct = item.get(name)
    if direct is not None:
        return _amount(direct)
    amounts = item.get("amounts")
    return _amount(amounts.get(name)) if isinstance(amounts, dict) else None


def extract_snapshot(order_response: dict[str, Any], items_response: dict[str, Any]) -> dict[str, Any]:
    order = _data(order_response) if isinstance(_data(order_response), dict) else {}
    items = _data(items_response) if isinstance(_data(items_response), list) else []
    amounts = order.get("amounts") if isinstance(order.get("amounts"), dict) else {}
    payment = order.get("payment") if isinstance(order.get("payment"), dict) else {}
    return {
        "fetch_ok": order_response.get("status") == 200 and items_response.get("status") == 200,
        "order_id": order.get("id"), "order_status": _status_slug(order),
        "order_total": _amount(amounts.get("total") or order.get("total")),
        "paid_amount": _amount(amounts.get("paid") or payment.get("paid_amount") or order.get("paid_amount")),
        "outstanding_amount": _amount(amounts.get("remaining") or payment.get("remaining_amount") or order.get("remaining_amount")),
        "payment_status": payment.get("status") or order.get("payment_status"),
        "payment_urls": sanitize(payment.get("urls") or order.get("payment_urls") or []),
        "transactions": "UNAVAILABLE_SCOPE_NOT_GRANTED",
        "items_count": len(items),
        "items": [
            {
                "item_id": x.get("id"),
                "product_id": _item_product_id(x),
                "variant_id": x.get("variant_id") or ((x.get("variant") or {}).get("id") if isinstance(x.get("variant"), dict) else None),
                "sku": x.get("sku"),
                "quantity": x.get("quantity"),
                "options": x.get("options", []),
                "price": _item_amount(x, "price"),
                "cost": _item_amount(x, "cost"),
                "weight": x.get("weight"),
                "branch_id": x.get("branch_id"),
                "total": _amount((x.get("amounts") or {}).get("total")) if isinstance(x.get("amounts"), dict) else None,
                "branches_quantity": x.get("branches_quantity", []),
            }
            for x in items
            if isinstance(x, dict)
        ],
    }


def fetch_snapshot(transport: Any, order_id: str, correlation_id: str) -> dict[str, Any]:
    return extract_snapshot(transport.request("GET", f"/orders/{order_id}", None, correlation_id), transport.request("GET", f"/orders/items?order_id={urllib.parse.quote(order_id)}", None, correlation_id))


def _amasi_money(value: Any, currency: str) -> Decimal:
    if isinstance(value, dict):
        if value.get("currency") != currency:
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
        value = value.get("amount")
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN") from None
    if not amount.is_finite() or amount < 0:
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    return amount


def _amasi_response_success(response: dict[str, Any]) -> bool:
    body = response.get("body")
    status = response.get("status")
    if type(status) is not int or not 200 <= status < 300 or not isinstance(body, dict) or body.get("success") is not True:
        return False
    declared = body.get("status")
    return declared is None or (type(declared) is int and 200 <= declared < 300)


def _validate_amasi_order(order: dict[str, Any], row: dict[str, Any]) -> None:
    customer = order.get("customer")
    if (str(order.get("id", "")) != str(row["order_id"])
            or str(order.get("reference_id", "")) != str(row["order_number"])
            or not isinstance(customer, dict)
            or str(customer.get("id", "")) != str(row["test_customer_id"])
            or _status_slug(order) != row["state"]):
        raise ContractRunnerError("AMASI_TEST_ORDER_IDENTITY_OR_STATE_MISMATCH")
    if order.get("payment_method") != "bank" or order.get("payment_methods") != []:
        raise ContractRunnerError("AMASI_TEST_UNPAID_BANK_ORDER_REQUIRED")
    if order.get("shipping_status") not in (None, "not_shippable"):
        raise ContractRunnerError("AMASI_TEST_SHIPMENT_ABSENCE_UNPROVEN")
    amounts = order.get("amounts")
    actions = order.get("payment_actions")
    remaining = actions.get("remaining_action") if isinstance(actions, dict) else None
    total_value = amounts.get("total") if isinstance(amounts, dict) else None
    if not isinstance(total_value, dict) or not isinstance(remaining, dict):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    currency = total_value.get("currency")
    if currency != "SAR" or remaining.get("has_remaining_amount") is not True:
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    total = _amasi_money(total_value, currency)
    if (total <= 0 or _amasi_money(remaining.get("paid_amount"), currency) != 0
            or _amasi_money(remaining.get("remaining_amount"), currency) != total):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    refund = actions.get("refund_action")
    if refund is not None:
        if not isinstance(refund, dict) or refund.get("can_refund") is True:
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
        for field_name in ("paid_amount", "refund_amount"):
            if _amasi_money(refund.get(field_name), currency) != 0:
                raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
        if ("has_refund_amount" in refund and refund["has_refund_amount"] is not False
                or refund.get("pending_refund_amount") is not None and _amasi_money(refund["pending_refund_amount"], currency) != 0):
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    payment = order.get("payment")
    if payment is not None and not isinstance(payment, dict):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    if (payment or {}).get("reference") not in (None, ""):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    for container, fields in ((order, ("paid_amount", "refund_amount")),
                              (amounts, ("paid", "refunded")),
                              (payment or {}, ("paid_amount", "refund_amount"))):
        for field_name in fields:
            if field_name in container and _amasi_money(container[field_name], currency) != 0:
                raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    for container in (order, payment or {}):
        if "remaining_amount" in container and _amasi_money(container["remaining_amount"], currency) != total:
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
        if "has_remaining_amount" in container and container["has_remaining_amount"] is not True:
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    for value in (order.get("payment_status"), order.get("payment_collection_status"),
                  (payment or {}).get("status"), (payment or {}).get("collection_status")):
        if value is not None and (not isinstance(value, str) or value not in {"pending", "unpaid"}):
            raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    bank = order.get("bank")
    if bank is not None and not isinstance(bank, dict):
        raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")
    for container in (order, payment or {}, bank or {}):
        for key in ("receipt", "receipt_image", "bank_receipt", "transfer_receipt", "transaction_reference", "transactions",
                    "payment_receipt_url", "receipt_url", "attachment_url", "proof_url", "proof", "transfer_receipt_url",
                    "paid_at", "captured_at", "refunded_at", "transaction_id"):
            if container.get(key) not in (None, "", [], {}):
                raise ContractRunnerError("AMASI_TEST_PAYMENT_UNPROVEN")


def _verify_amasi_no_shipments(transport: Any, order_id: str, correlation_id: str) -> None:
    response = transport.request("GET", f"/shipments?order_id={urllib.parse.quote(order_id, safe='')}&per_page=1", None, correlation_id)
    body = response.get("body")
    page = body.get("pagination") if isinstance(body, dict) else None
    if (response.get("status") != 200 or not isinstance(body, dict)
            or body.get("success") is not True or body.get("data") != [] or not isinstance(page, dict)):
        raise ContractRunnerError("AMASI_TEST_SHIPMENT_ABSENCE_UNPROVEN")
    for key, expected in (("total", 0), ("count", 0), ("currentPage", 1)):
        if type(page.get(key)) is not int or page[key] != expected:
            raise ContractRunnerError("AMASI_TEST_SHIPMENT_ABSENCE_UNPROVEN")
    if type(page.get("totalPages")) is not int or page["totalPages"] not in (0, 1):
        raise ContractRunnerError("AMASI_TEST_SHIPMENT_ABSENCE_UNPROVEN")
    for links in (page, page.get("links", {}), body.get("links", {})):
        if not isinstance(links, dict) or any(links.get(key) not in (None, "") for key in ("next", "nextPage", "next_page_url")):
            raise ContractRunnerError("AMASI_TEST_SHIPMENT_ABSENCE_UNPROVEN")


def _amasi_fresh_snapshot(transport: Any, seed: dict[str, Any], case: dict[str, Any], correlation_id: str, *, before_write: bool = False) -> dict[str, Any]:
    row = next(row for row in seed["orders"] if str(row["order_id"]) == str(case["order_id"]))
    order_id = str(row["order_id"])
    _verify_amasi_no_shipments(transport, order_id, correlation_id)
    order_response = transport.request("GET", f"/orders/{order_id}", None, correlation_id)
    order = _data(order_response)
    if order_response.get("status") != 200 or not isinstance(order, dict):
        raise ContractRunnerError(BASELINE_FETCH_REQUIRED)
    _validate_amasi_order(order, row)
    items_response = transport.request("GET", f"/orders/items?order_id={urllib.parse.quote(order_id, safe='')}", None, correlation_id)
    # Item reads are another network boundary: recheck payment/state afterward
    # before interpreting the combined snapshot or dispatching the mutation.
    order_response = transport.request("GET", f"/orders/{order_id}", None, correlation_id)
    order = _data(order_response)
    if not isinstance(order, dict):
        raise ContractRunnerError(BASELINE_FETCH_REQUIRED)
    _validate_amasi_order(order, row)
    snapshot = extract_snapshot(order_response, items_response)
    items = _data(items_response)
    if (not snapshot["fetch_ok"] or not isinstance(items, list) or not items
            or any(not isinstance(item, dict) or not _nonempty(item.get("id")) for item in items)
            or len({str(item["id"]) for item in items}) != len(items)):
        raise ContractRunnerError(BASELINE_FETCH_REQUIRED)
    if not any(str(item["id"]) == str(row["preserve_item_id"]) for item in items):
        raise ContractRunnerError("AMASI_TEST_ORIGINAL_ITEM_MISSING")
    if before_write:
        targets = [item for item in items if str(item["id"]) == str(row["item_id"])]
        if (len(targets) != 1 or str(_item_product_id(targets[0])) != str(row["product_id"])
                or str(targets[0].get("sku")) != str(row["sku"])):
            raise ContractRunnerError("AMASI_TEST_TARGET_ITEM_MISMATCH")
        expected_total = next(check["equals"] for check in case["assertions"] if check["path"] == "before.order_total")
        if _amasi_money(order["amounts"]["total"], "SAR") != _amasi_money(expected_total, "SAR"):
            raise ContractRunnerError("AMASI_TEST_BASELINE_TOTAL_CHANGED")
    snapshot.update(paid_amount=0, outstanding_amount=_amount(order["payment_actions"]["remaining_action"]["remaining_amount"]),
                    payment_status="unpaid", shipment_count=0)
    return snapshot


def _resolve_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def execute_assertions(assertions: list[dict[str, Any]], before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    context = {"before": before, "after": after}
    return [{"path": x.get("path"), "expected": x.get("equals"), "actual": _resolve_path(context, str(x.get("path", ""))), "passed": _resolve_path(context, str(x.get("path", ""))) == x.get("equals")} for x in assertions]


def canonical_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AttemptLease:
    attempt_id: str
    directory: Path
    manifest_digest: str
    case_digest: str
    operation_digest: str
    approval_digest: str
    expires_at: str
    order_hash: str
    lock_handle: Any = field(repr=False, compare=False)
    lock_api: Any = field(repr=False, compare=False)


class LocalAttemptLedger:
    """Append-only local P0 replay/concurrency guard; never a live capability."""

    def __init__(self, state_root: Path):
        self.root = Path(state_root)

    @staticmethod
    def _private_owner(stat_result: os.stat_result) -> bool:
        _, _, getuid = _required_security_primitives()
        return stat_result.st_uid == getuid()

    @classmethod
    def _verify_private_directory(cls, path: Path) -> None:
        try:
            metadata = os.lstat(path)
        except OSError:
            raise ContractRunnerError(STATE_ROOT_UNSAFE) from None
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or not cls._private_owner(metadata)
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ContractRunnerError(STATE_ROOT_UNSAFE)

    def _prepare_root(self) -> None:
        if not self.root.is_absolute() or self.root != Path(os.path.abspath(self.root)):
            raise ContractRunnerError(STATE_ROOT_UNSAFE)
        current = Path(self.root.anchor)
        created: set[Path] = set()
        for part in self.root.parts[1:]:
            current = current / part
            try:
                metadata = os.lstat(current)
            except FileNotFoundError:
                try:
                    os.mkdir(current, 0o700)
                except OSError:
                    raise ContractRunnerError(STATE_ROOT_UNSAFE) from None
                created.add(current)
                metadata = os.lstat(current)
            except OSError:
                raise ContractRunnerError(STATE_ROOT_UNSAFE) from None
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise ContractRunnerError(STATE_ROOT_UNSAFE)
            if current in created:
                os.chmod(current, 0o700)
        if self.root.parent.name == "mz-p0-local-state":
            self._verify_private_directory(self.root.parent)
        self._verify_private_directory(self.root)

    def _assert_under_root(self, path: Path) -> None:
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            raise ContractRunnerError(STATE_ROOT_UNSAFE) from None
        current = self.root
        self._verify_private_directory(current)
        for part in relative.parts[:-1]:
            current = current / part
            self._verify_private_directory(current)

    def _secure_directory(self, path: Path) -> None:
        self._assert_under_root(path)
        try:
            os.mkdir(path, 0o700)
            os.chmod(path, 0o700)
        except FileExistsError:
            pass
        except OSError:
            raise ContractRunnerError(STATE_ROOT_UNSAFE) from None
        self._verify_private_directory(path)

    def _open_lock(self, path: Path) -> Any:
        self._assert_under_root(path)
        nofollow, _, _ = _required_security_primitives()
        flags = os.O_RDWR | os.O_CREAT | nofollow
        try:
            descriptor = os.open(path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or not self._private_owner(metadata)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ContractRunnerError(STATE_ROOT_UNSAFE)
            return os.fdopen(descriptor, "a+", encoding="utf-8")
        except ContractRunnerError:
            try:
                os.close(descriptor)
            except (OSError, UnboundLocalError):
                pass
            raise
        except OSError:
            raise ContractRunnerError(STATE_ROOT_UNSAFE) from None

    @staticmethod
    def _validate_digest(value: str) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ContractRunnerError(CASE_INVALID)
        return value

    def _write_new(self, path: Path, record: dict[str, Any]) -> None:
        self._assert_under_root(path)
        encoded = json.dumps(sanitize(record), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
        nofollow, directory, _ = _required_security_primitives()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if stat.S_IMODE(os.lstat(path).st_mode) != 0o600:
            raise ContractRunnerError(STATE_ROOT_UNSAFE)
        directory_flags = os.O_RDONLY | directory | nofollow
        directory_fd = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def _read_record(self, path: Path) -> dict[str, Any]:
        self._assert_under_root(path)
        try:
            metadata = os.lstat(path)
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or not self._private_owner(metadata)
                or stat.S_IMODE(metadata.st_mode) != 0o600
            ):
                raise ContractRunnerError(STATE_ROOT_UNSAFE)
            value = json.loads(path.read_text(encoding="utf-8"))
        except ContractRunnerError:
            raise
        except (OSError, json.JSONDecodeError):
            raise ContractRunnerError(ATTEMPT_REPLAY_BLOCKED) from None
        if not isinstance(value, dict):
            raise ContractRunnerError(ATTEMPT_REPLAY_BLOCKED)
        return value

    def acquire(
        self,
        *,
        store_id: str,
        order_id: str,
        manifest_digest: str,
        case_digest: str,
        operation_digest: str,
        approval_id: str,
        expires_at: str,
    ) -> AttemptLease:
        lock_api = _require_linux_posix()
        manifest_digest = self._validate_digest(manifest_digest)
        case_digest = self._validate_digest(case_digest)
        operation_digest = self._validate_digest(operation_digest)
        if not isinstance(approval_id, str) or not approval_id.strip():
            raise ContractRunnerError(WRITE_APPROVAL_REQUIRED)
        try:
            parsed_expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            raise ContractRunnerError(WRITE_APPROVAL_REQUIRED) from None
        if parsed_expiry.tzinfo is None:
            raise ContractRunnerError(WRITE_APPROVAL_REQUIRED)

        approval_digest = hashlib.sha256(approval_id.strip().encode("utf-8")).hexdigest()
        order_hash = hashlib.sha256(f"{store_id}:{order_id}".encode("utf-8")).hexdigest()
        attempt_id = hashlib.sha256(f"{manifest_digest}:{case_digest}:{operation_digest}:{approval_digest}".encode("ascii")).hexdigest()[:24]
        attempts_dir = self.root / "attempts"
        approvals_dir = self.root / "approvals"
        locks_dir = self.root / "order-locks"
        self._prepare_root()
        self._secure_directory(attempts_dir)
        self._secure_directory(approvals_dir)
        self._secure_directory(locks_dir)
        lock_handle = self._open_lock(locks_dir / f"{order_hash}.lock")
        try:
            lock_api.flock(lock_handle.fileno(), lock_api.LOCK_EX | lock_api.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            raise ContractRunnerError(CONCURRENT_ATTEMPT_BLOCKED) from None
        try:
            for prior_lease in attempts_dir.glob("*/lease.json"):
                try:
                    prior = self._read_record(prior_lease)
                    terminal_path = prior_lease.parent / "terminal.json"
                    terminal = self._read_record(terminal_path) if terminal_path.exists() else None
                except ContractRunnerError:
                    raise ContractRunnerError(ATTEMPT_REPLAY_BLOCKED) from None
                if prior.get("order_hash") == order_hash and (not terminal or terminal.get("attempt_outcome") == "UNKNOWN"):
                    raise ContractRunnerError(ATTEMPT_REPLAY_BLOCKED)
        except Exception:
            lock_api.flock(lock_handle.fileno(), lock_api.LOCK_UN)
            lock_handle.close()
            raise
        attempt_dir = attempts_dir / operation_digest
        if os.path.lexists(attempt_dir):
            lock_api.flock(lock_handle.fileno(), lock_api.LOCK_UN)
            lock_handle.close()
            raise ContractRunnerError(ATTEMPT_REPLAY_BLOCKED) from None

        # Reserve the approval before an UNKNOWN-capable lease exists. A reused
        # approval therefore leaves no attempt directory or orphan lease.
        try:
            self._write_new(
                approvals_dir / f"{approval_digest}.json",
                {
                    "record_type": "P0_APPROVAL_RESERVED",
                    "approval_digest": approval_digest,
                    "attempt_id": attempt_id,
                    "manifest_digest": manifest_digest,
                    "case_digest": case_digest,
                    "operation_digest": operation_digest,
                    "order_hash": order_hash,
                    "outcome_if_lease_absent": "NOT_ATTEMPTED",
                },
            )
        except FileExistsError:
            lock_api.flock(lock_handle.fileno(), lock_api.LOCK_UN)
            lock_handle.close()
            raise ContractRunnerError(APPROVAL_ALREADY_CONSUMED) from None
        except Exception:
            lock_api.flock(lock_handle.fileno(), lock_api.LOCK_UN)
            lock_handle.close()
            raise

        lease = AttemptLease(
            attempt_id=attempt_id,
            directory=attempt_dir,
            manifest_digest=manifest_digest,
            case_digest=case_digest,
            operation_digest=operation_digest,
            approval_digest=approval_digest,
            expires_at=parsed_expiry.isoformat(),
            order_hash=order_hash,
            lock_handle=lock_handle,
            lock_api=lock_api,
        )
        try:
            self._secure_directory(attempt_dir)
            self._write_new(
                attempt_dir / "lease.json",
                {
                    "record_type": "P0_ATTEMPT_LEASE",
                    "attempt_id": attempt_id,
                    "store_id_hash": hashlib.sha256(str(store_id).encode("utf-8")).hexdigest(),
                    "order_hash": order_hash,
                    "manifest_digest": manifest_digest,
                    "case_digest": case_digest,
                    "operation_digest": operation_digest,
                    "approval_digest": approval_digest,
                    "expires_at": lease.expires_at,
                    "outcome_if_terminal_absent": "UNKNOWN",
                    "durability": LOCAL_EVIDENCE_DURABILITY,
                },
            )
        except Exception:
            lock_api.flock(lock_handle.fileno(), lock_api.LOCK_UN)
            lock_handle.close()
            raise ContractRunnerError(ATTEMPT_REPLAY_BLOCKED) from None
        return lease

    def write_terminal(self, lease: AttemptLease, outcome: str) -> Path:
        if outcome not in {"TERMINAL", "UNKNOWN"}:
            raise ContractRunnerError(CASE_INVALID)
        path = lease.directory / "terminal.json"
        try:
            self._write_new(
                path,
                {"record_type": "P0_ATTEMPT_TERMINAL", "attempt_id": lease.attempt_id, "attempt_outcome": outcome, "operation_digest": lease.operation_digest, "order_hash": lease.order_hash},
            )
        finally:
            lease.lock_api.flock(lease.lock_handle.fileno(), lease.lock_api.LOCK_UN)
            lease.lock_handle.close()
        return path


class EvidenceWriter:
    def __init__(self, directory: Path):
        self.directory = directory

    @staticmethod
    def _safe_case_id(case_id: Any) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", str(case_id))

    def _write_new(self, path: Path, record: dict[str, Any]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(sanitize(record), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with path.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return path

    def write_intent(self, case_id: str, attempt_id: str, intent: dict[str, Any]) -> Path:
        path = self.directory / f"{self._safe_case_id(case_id)}-{attempt_id}.intent.json"
        return self._write_new(path, intent)

    def write_terminal(self, case_id: str, attempt_id: str, evidence: dict[str, Any]) -> Path:
        path = self.directory / f"{self._safe_case_id(case_id)}-{attempt_id}.terminal.json"
        return self._write_new(path, evidence)

    def write(self, evidence: dict[str, Any]) -> Path:
        attempt_id = uuid.uuid4().hex[:16]
        path = self.directory / f"{self._safe_case_id(evidence['test_case_id'])}-{attempt_id}.json"
        return self._write_new(path, evidence)


def _semantic_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {k: {"before": before.get(k), "after": after.get(k)} for k in sorted(before.keys() | after.keys()) if before.get(k) != after.get(k)}


def filter_webhooks(events: list[dict[str, Any]], *, store_id: str, order_id: str, started_at: datetime, correlation_id: str) -> list[dict[str, Any]]:
    allowed = {"order.updated", "order.products.updated", "order.payment.updated", "order.total.price.updated"}
    result = []
    for event in events:
        if str(event.get("store_id")) != store_id or str(event.get("order_id")) != order_id or event.get("type") not in allowed or event.get("correlation_id") != correlation_id:
            continue
        try:
            occurred = datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if occurred >= started_at:
            sanitized = sanitize(event)
            sanitized.pop("store_id", None)
            sanitized["store_id_hash"] = _hash_id(store_id)
            result.append(sanitized)
    return result


def validate_webhook_events(events: Any, *, config: SandboxConfig, case: dict[str, Any], correlation_id: str) -> list[dict[str, Any]]:
    if not isinstance(events, list):
        raise ContractRunnerError(CASE_INVALID)
    allowed = {"test_case_id", "id", "type", "store_id", "order_id", "correlation_id", "occurred_at"}
    required = {"type", "store_id", "order_id", "correlation_id", "occurred_at"}
    order_id = _id_text(case["order_id"])
    for event in events:
        _strict_object(event, allowed=allowed, required=required)
        if event["type"] not in {"order.updated", "order.products.updated", "order.payment.updated", "order.total.price.updated"}:
            raise ContractRunnerError(CASE_INVALID)
        if str(event["store_id"]) != config.demo_store_id or str(event["order_id"]) != order_id or event["correlation_id"] != correlation_id:
            raise ContractRunnerError(CASE_INVALID)
        if "test_case_id" in event and str(event["test_case_id"]) != str(case["id"]):
            raise ContractRunnerError(CASE_INVALID)
        try:
            occurred = datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00"))
        except ValueError:
            raise ContractRunnerError(CASE_INVALID) from None
        if occurred.tzinfo is None:
            raise ContractRunnerError(CASE_INVALID)
    return events


def _webhook_wait_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("webhook wait must be between 0 and 30 seconds") from None
    if not math.isfinite(seconds) or not 0 <= seconds <= 30:
        raise argparse.ArgumentTypeError("webhook wait must be between 0 and 30 seconds")
    return seconds


def _collect_postwrite_webhooks(
    path: Path | None,
    *,
    initial_events: list[dict[str, Any]],
    config: SandboxConfig,
    case: dict[str, Any],
    correlation_id: str,
    started_at: datetime,
    wait_seconds: float,
) -> list[dict[str, Any]]:
    """Observe a bounded window without replaying the write or trusting old input."""
    if path is None:
        return []
    deadline = time.monotonic() + wait_seconds
    seen = {canonical_digest(event) for event in initial_events}
    collected = []
    while True:
        try:
            events = json.loads(path.read_text(encoding="utf-8"))
            validate_webhook_events(events, config=config, case=case, correlation_id=correlation_id)
        except (OSError, ValueError, TypeError, ContractRunnerError):
            # A collector failure after dispatch is not a reason to repeat it.
            raise ContractRunnerError("SALLA_P0_WEBHOOK_SOURCE_INVALID") from None
        observed_at = datetime.now(timezone.utc)
        for event in events:
            digest = canonical_digest(event)
            if digest in seen:
                continue
            seen.add(digest)
            occurred_at = datetime.fromisoformat(str(event["occurred_at"]).replace("Z", "+00:00"))
            if occurred_at > observed_at:
                continue
            collected.extend(filter_webhooks(
                [event], store_id=config.demo_store_id, order_id=str(case["order_id"]),
                started_at=started_at, correlation_id=correlation_id,
            ))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return collected
        time.sleep(min(0.1, remaining))


def _safe_fetch_snapshot(transport: Any, order_id: str, correlation_id: str) -> dict[str, Any]:
    try:
        return fetch_snapshot(transport, order_id, correlation_id)
    except Exception as exc:
        return extract_snapshot(
            {"status": None, "body": None, "transport_error": type(exc).__name__},
            {"status": None, "body": None, "transport_error": type(exc).__name__},
        )


def _verify_delete_replacement_in_baseline(case: dict[str, Any], before: dict[str, Any], *, seed: dict[str, Any] | None = None) -> None:
    if case["method"] != "DELETE":
        return
    if seed and seed.get("classification") == AMASI_MANIFEST_CLASSIFICATION:
        row = next(row for row in seed["orders"] if str(row["order_id"]) == str(case["order_id"]))
        if (_snapshot_item_by_id(before, str(row["preserve_item_id"])) is None
                or _snapshot_item_by_id(before, str(row["item_id"])) is None
                or str(row["preserve_item_id"]) == str(row["item_id"])):
            raise ContractRunnerError("AMASI_TEST_ORIGINAL_ITEM_PROTECTED")
        return
    verify_step = case["steps"][1]
    replacement_item_id = str(verify_step["replacement_item_id"])
    replacement_product_id = str(verify_step["replacement_product_id"])
    replacement_sku = str(verify_step["replacement_sku"])
    proven = any(
        str(item.get("item_id")) == replacement_item_id
        and str(item.get("product_id")) == replacement_product_id
        and str(item.get("sku")) == replacement_sku
        for item in before.get("items", [])
        if isinstance(item, dict)
    )
    if not proven:
        raise ContractRunnerError(DELETE_REPLACEMENT_NOT_PROVEN)


def _postcondition(name: str, expected: Any, actual: Any, passed: bool | None = None) -> dict[str, Any]:
    return {"check": name, "expected": expected, "actual": actual, "passed": expected == actual if passed is None else passed}


def _snapshot_items(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    items = snapshot.get("items") if isinstance(snapshot, dict) else None
    return [item for item in items or [] if isinstance(item, dict)] if isinstance(items, list) else []


def _snapshot_item_by_id(snapshot: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    matches = [item for item in _snapshot_items(snapshot) if str(item.get("item_id")) == item_id]
    return matches[0] if len(matches) == 1 else None


def _option_selections(options: Any) -> frozenset[tuple[str, str, str]]:
    if not isinstance(options, list):
        return frozenset()
    selected: set[tuple[str, str, str]] = set()
    for option in options:
        if not isinstance(option, dict):
            continue
        option_id = option.get("option_id")
        if option_id is None:
            option_id = option.get("product_option_id")
        if option_id is None:
            option_id = option.get("id")
        raw_value = option.get("value_id")
        if raw_value is None:
            raw_value = option.get("option_value_id")
        nested_value = option.get("value")
        if raw_value is None and isinstance(nested_value, dict):
            raw_value = nested_value.get("id")
        if _nonempty(option_id) and _nonempty(raw_value):
            selected.add((str(option_id), "id", str(raw_value)))
        elif _nonempty(option_id) and isinstance(nested_value, str) and nested_value.strip():
            selected.add((str(option_id), "text", nested_value.strip()))
    return frozenset(selected)


def _expected_option_selections(body: dict[str, Any]) -> frozenset[tuple[str, str, str]]:
    selected: set[tuple[str, str, str]] = set()
    for option in body.get("options", []):
        if "value_id" in option:
            selected.add((str(option["option_id"]), "id", str(option["value_id"])))
        else:
            selected.add((str(option["option_id"]), "text", str(option["value"]).strip()))
    return frozenset(selected)


def _item_matches_binding(item: dict[str, Any], binding: dict[str, Any], body: dict[str, Any]) -> bool:
    if str(item.get("product_id")) != binding["product_id"] or str(item.get("sku")) != binding["sku"]:
        return False
    actual_variant = str(item.get("variant_id")) if _nonempty(item.get("variant_id")) else None
    if actual_variant != binding["variant_id"]:
        return False
    if "quantity" in body and item.get("quantity") != body["quantity"]:
        return False
    if "options" in body and _option_selections(item.get("options")) != _expected_option_selections(body):
        return False
    for field in ("price", "cost", "weight", "branch_id"):
        if field in body and item.get(field) != body[field]:
            return False
    return True


def _update_value(container: dict[str, Any], field_name: str, *, request_body: bool = False) -> Any:
    value = container.get(field_name)
    if field_name != "options":
        return value
    selections = _expected_option_selections(container) if request_body else _option_selections(value)
    return [list(selection) for selection in sorted(selections)]


def evaluate_fixed_postconditions(case: dict[str, Any], seed: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    order_id = str(case["order_id"])
    order_row = next(row for row in seed["orders"] if str(row["order_id"]) == order_id)
    before_items = _snapshot_items(before)
    after_items = _snapshot_items(after)
    before_id_values = [item.get("item_id") for item in before_items]
    after_id_values = [item.get("item_id") for item in after_items]
    before_ids = {str(item_id) for item_id in before_id_values}
    after_ids = {str(item_id) for item_id in after_id_values}
    before_by_id = {str(item.get("item_id")): item for item in before_items}
    after_by_id = {str(item.get("item_id")): item for item in after_items}
    conditions = [
        _postcondition("before_fetch_ok", True, before.get("fetch_ok")),
        _postcondition("after_fetch_ok", True, after.get("fetch_ok")),
        _postcondition("before_order_identity", order_id, str(before.get("order_id"))),
        _postcondition("after_order_identity", order_id, str(after.get("order_id"))),
        _postcondition("before_items_count_consistent", len(before_items), before.get("items_count")),
        _postcondition("after_items_count_consistent", len(after_items), after.get("items_count")),
        _postcondition("before_item_ids_present", True, all(_nonempty(item_id) for item_id in before_id_values)),
        _postcondition("after_item_ids_present", True, all(_nonempty(item_id) for item_id in after_id_values)),
        _postcondition("before_item_ids_unique", len(before_items), len(before_ids)),
        _postcondition("after_item_ids_unique", len(after_items), len(after_ids)),
    ]
    method = case["method"]
    body = case["body"]
    if method == "POST":
        binding = _matching_product_binding(seed, body, order_row, method)
        new_ids = after_ids - before_ids
        matching_new = [item for item in after_items if str(item.get("item_id")) in new_ids and _item_matches_binding(item, binding, body)]
        conditions.extend(
            [
                _postcondition("add_one_new_item", 1, len(new_ids)),
                _postcondition("add_target_binding", 1, len(matching_new)),
                _postcondition("add_item_count_delta", len(before_items) + 1, len(after_items)),
                _postcondition("add_preserves_existing_items", sorted(before_ids), sorted(before_ids & after_ids)),
                _postcondition(
                    "add_existing_items_unchanged",
                    {item_id: before_by_id[item_id] for item_id in sorted(before_ids)},
                    {item_id: after_by_id.get(item_id) for item_id in sorted(before_ids)},
                ),
            ]
        )
    elif method == "PUT":
        item_id = urllib.parse.urlsplit(case["path"]).path.rsplit("/", 1)[-1]
        before_target = _snapshot_item_by_id(before, item_id)
        after_target = _snapshot_item_by_id(after, item_id)
        binding = _matching_product_binding(seed, body, order_row, method)
        requested_fields = sorted(set(body) & UPDATE_MUTABLE_FIELDS)
        expected_values = {field_name: _update_value(body, field_name, request_body=True) for field_name in requested_fields}
        before_requested = {
            field_name: _update_value(before_target or {}, field_name)
            for field_name in requested_fields
        }
        after_requested = {
            field_name: _update_value(after_target or {}, field_name)
            for field_name in requested_fields
        }
        changed_requested_fields = [
            field_name
            for field_name in requested_fields
            if before_requested[field_name] != expected_values[field_name]
            and after_requested[field_name] == expected_values[field_name]
        ]
        unrequested_fields = sorted(UPDATE_MUTABLE_FIELDS - set(requested_fields))
        before_unrequested = {
            field_name: _update_value(before_target or {}, field_name)
            for field_name in unrequested_fields
        }
        after_unrequested = {
            field_name: _update_value(after_target or {}, field_name)
            for field_name in unrequested_fields
        }
        conditions.extend(
            [
                _postcondition("update_target_present_before", True, before_target is not None),
                _postcondition("update_target_stable_after", True, after_target is not None),
                _postcondition("update_item_count_stable", len(before_items), len(after_items)),
                _postcondition("update_unrelated_item_ids_stable", sorted(before_ids - {item_id}), sorted(after_ids - {item_id})),
                _postcondition(
                    "update_unrelated_items_unchanged",
                    {key: before_by_id[key] for key in sorted(before_ids - {item_id})},
                    {key: after_by_id.get(key) for key in sorted(before_ids - {item_id})},
                ),
                _postcondition("update_requested_binding", True, bool(after_target and _item_matches_binding(after_target, binding, body))),
                _postcondition("update_requested_values_applied", expected_values, after_requested),
                _postcondition("update_requested_field_delta", True, bool(changed_requested_fields)),
                _postcondition("update_unrequested_fields_stable", before_unrequested, after_unrequested),
            ]
        )
    else:
        item_id = urllib.parse.urlsplit(case["path"]).path.rsplit("/", 1)[-1]
        if seed.get("classification") == AMASI_MANIFEST_CLASSIFICATION:
            preserved_id = str(order_row["preserve_item_id"])
            conditions.extend([
                _postcondition("delete_target_present_before", True, _snapshot_item_by_id(before, item_id) is not None),
                _postcondition("delete_only_target_removed", sorted(before_ids - {item_id}), sorted(after_ids)),
                _postcondition("delete_item_count_delta", len(before_items) - 1, len(after_items)),
                _postcondition("delete_original_item_preserved", True, preserved_id != item_id and preserved_id in before_ids & after_ids),
                _postcondition("delete_remaining_items_unchanged",
                               {key: before_by_id[key] for key in sorted(before_ids - {item_id})},
                               {key: after_by_id.get(key) for key in sorted(after_ids)}),
            ])
            return conditions
        verify_step = case["steps"][1]
        replacement_id = str(verify_step["replacement_item_id"])
        replacement_before = _snapshot_item_by_id(before, replacement_id)
        replacement_after = _snapshot_item_by_id(after, replacement_id)
        replacement_matches = lambda item: bool(
            item
            and str(item.get("product_id")) == str(verify_step["replacement_product_id"])
            and str(item.get("sku")) == str(verify_step["replacement_sku"])
        )
        conditions.extend(
            [
                _postcondition("delete_target_present_before", True, _snapshot_item_by_id(before, item_id) is not None),
                _postcondition("delete_target_absent_after", True, _snapshot_item_by_id(after, item_id) is None),
                _postcondition("delete_replacement_proven_before", True, replacement_matches(replacement_before)),
                _postcondition("delete_replacement_preserved_after", True, replacement_matches(replacement_after)),
                _postcondition("delete_item_count_delta", len(before_items) - 1, len(after_items)),
                _postcondition("delete_only_target_removed", sorted(before_ids - {item_id}), sorted(after_ids)),
                _postcondition(
                    "delete_remaining_items_unchanged",
                    {key: before_by_id[key] for key in sorted(before_ids - {item_id})},
                    {key: after_by_id.get(key) for key in sorted(after_ids)},
                ),
            ]
        )
    return conditions


def _analyze_observed_attempt(
    case: dict[str, Any],
    seed: dict[str, Any],
    before: dict[str, Any],
    response: dict[str, Any],
    after: dict[str, Any],
    webhook_events: list[dict[str, Any]],
    retry_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fixed = evaluate_fixed_postconditions(case, seed, before, after)
    assertions = execute_assertions(case.get("assertions", []), before, after)
    attempt_outcome = "UNKNOWN" if response.get("transport_error") or not after.get("fetch_ok") or (retry_response and retry_response.get("transport_error")) else "TERMINAL"
    if attempt_outcome == "UNKNOWN":
        observed_verdict, observed_reason = "INCONCLUSIVE", "WRITE_OUTCOME_UNKNOWN_RECONCILIATION_REQUIRED"
    elif (not isinstance(response.get("status"), int) or not 200 <= response["status"] < 300
          or seed.get("classification") == AMASI_MANIFEST_CLASSIFICATION and not _amasi_response_success(response)):
        observed_verdict, observed_reason = "FAIL", "SALLA_WRITE_REJECTED"
    elif retry_response is not None and (not isinstance(retry_response.get("status"), int) or not 200 <= retry_response["status"] < 300):
        observed_verdict, observed_reason = "FAIL", "SALLA_RETRY_REJECTED"
    elif not all(row["passed"] for row in fixed):
        observed_verdict, observed_reason = "FAIL", "FIXED_OPERATION_POSTCONDITIONS_FAILED"
    elif assertions and not all(row["passed"] for row in assertions):
        observed_verdict, observed_reason = "FAIL", "SUPPLEMENTAL_ASSERTIONS_FAILED"
    else:
        observed_verdict, observed_reason = "PASS", "FIXED_OPERATION_POSTCONDITIONS_PROVEN"
    return {
        "attempt_outcome": attempt_outcome,
        "observed_verdict": observed_verdict,
        "observed_outcome_reason": observed_reason,
        "fixed_postconditions": fixed,
        "assertions_executed": len(assertions),
        "assertions": assertions,
        "webhook_events": webhook_events,
    }


def analyze_attempt(
    case: dict[str, Any],
    seed: dict[str, Any],
    before: dict[str, Any],
    response: dict[str, Any],
    after: dict[str, Any],
    webhook_events: list[dict[str, Any]],
    retry_response: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pure analysis API. It cannot emit live evidence or perform any I/O."""
    validate_case(case, seed)
    observed = _analyze_observed_attempt(case, seed, before, response, after, webhook_events, retry_response)
    return {
        "classification": FIXTURE_CLASSIFICATION,
        "identity_verified": False,
        "verdict": "NOT_EXECUTED",
        "final_verdict_reason": "MOCK_EVIDENCE_CANNOT_PROVE_SALLA_BEHAVIOR",
        **observed,
    }


def run_case(config: SandboxConfig, case: dict[str, Any], transport: Any, writer: EvidenceWriter, webhook_loader: Callable[[str], list[dict[str, Any]]]) -> Path:
    """Compatibility gate: imported callers cannot execute a commercial write."""
    seed = _load_config_seed(config)
    validate_seed_structure(config, seed)
    validate_case(case, seed)
    correlation_id = case.get("client_request_id") or f"p0-{case['id']}-{uuid.uuid4().hex}"
    validate_webhook_events(webhook_loader(case["id"]), config=config, case=case, correlation_id=correlation_id)
    validate_prewrite_gates(config, case)
    raise ContractRunnerError(LIVE_EXECUTOR_REQUIRED)


def _print_prewrite_block(command: str, reason: str) -> None:
    if command == "run":
        print("ADD_EXECUTED=false")
        print(f"REASON={reason}")
    else:
        print(reason)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("readiness", "run"))
    parser.add_argument("--environment", choices=("demo", "amasi-test-orders"), default="demo")
    parser.add_argument("--case-file", type=Path)
    parser.add_argument("--webhook-events", type=Path)
    parser.add_argument(
        "--webhook-wait-seconds", type=_webhook_wait_seconds, default=5.0,
        help="post-write observation window for --webhook-events (0..30 seconds; default: 5)",
    )
    args = parser.parse_args(argv)
    try:
        _require_linux_posix()
    except ContractRunnerError as exc:
        _print_prewrite_block(args.command, str(exc))
        return 2
    try:
        config = AmasiTestConfig.from_env() if args.environment == "amasi-test-orders" else SandboxConfig.from_env()
    except ContractRunnerError as exc:
        _print_prewrite_block(args.command, str(exc))
        return 2
    amasi = isinstance(config, AmasiTestConfig)
    evidence_classification = AMASI_EVIDENCE_CLASSIFICATION if amasi else REAL_EVIDENCE_CLASSIFICATION
    case: dict[str, Any] | None = None
    events: list[dict[str, Any]] = []
    seed: dict[str, Any] | None = None
    capability_expiry: datetime | None = None
    if args.command == "run":
        try:
            if not args.case_file:
                raise ContractRunnerError("SALLA_SANDBOX_CASE_FILE_REQUIRED")
            case = json.loads(args.case_file.read_text(encoding="utf-8"))
            if args.webhook_events:
                events = json.loads(args.webhook_events.read_text(encoding="utf-8"))
            seed = _load_config_seed(config)
            validate_seed_structure(config, seed)
            validate_case(case, seed)
            validate_prewrite_gates(config, case)
            correlation_id = case.get("client_request_id") or "p0-prevalidation-no-events"
            validate_webhook_events(events, config=config, case=case, correlation_id=correlation_id)
            if events and not case.get("client_request_id"):
                raise ContractRunnerError(CASE_INVALID)
        except (ContractRunnerError, OSError, json.JSONDecodeError, TypeError) as exc:
            _print_prewrite_block(args.command, str(exc) if isinstance(exc, ContractRunnerError) else CASE_INVALID)
            return 2

    if amasi and args.command == "readiness":
        try:
            seed = _load_config_seed(config)
            validate_seed_structure(config, seed)
        except ContractRunnerError as exc:
            _print_prewrite_block(args.command, str(exc))
            return 2

    # The credential resolver and HTTP implementation intentionally live only in
    # this CLI call. No imported API accepts a raw token, config, or transport and
    # can consequently obtain a write-capable object.
    mongo_url = str(os.environ.get("MONGO_URL") or "").strip()
    db_name = str(os.environ.get("DB_NAME") or "").strip()
    if not mongo_url or not db_name:
        _print_prewrite_block(args.command, CREDENTIAL_RESOLVER_ENV_UNAVAILABLE)
        return 2
    if args.command == "run":
        try:
            capability_expiry = _write_approval_expiry(config, datetime.now(timezone.utc))
        except ContractRunnerError as exc:
            _print_prewrite_block(args.command, str(exc))
            return 2
    client = None
    try:
        try:
            client_factory, token_resolver = _runtime_credential_dependencies()
            client = client_factory(mongo_url)
            access_token = asyncio.run(resolve_sandbox_access_token(config, client[db_name], token_resolver))
        except ContractRunnerError:
            raise
        except Exception:
            raise ContractRunnerError(CREDENTIAL_RESOLVER_FAILED) from None
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
    except ContractRunnerError as exc:
        _print_prewrite_block(args.command, str(exc))
        return 2

    token_value = access_token.strip()
    http_open = urllib.request.build_opener(_NoLiveTestRedirect()).open if amasi else urllib.request.urlopen

    def redact_live(record: Any) -> Any:
        return sanitize(record, exact_secrets=(token_value,))

    write_attempted = False
    live_capability: dict[str, Any] | None = None
    live_lease: AttemptLease | None = None
    live_operation: dict[str, Any] | None = None

    def request(method: str, path: str, body: dict[str, Any] | None, correlation_id: str) -> dict[str, Any]:
        nonlocal write_attempted
        shipment_path = (
            f"/shipments?order_id={urllib.parse.quote(str(seed['orders'][0]['order_id']), safe='')}&per_page=1"
            if amasi and seed is not None else None
        )
        if not (amasi and method == "GET" and body is None and path == shipment_path):
            validate_endpoint(method, path)
        http_request = urllib.request.Request(
            f"{config.base_url}/{path.lstrip('/')}",
            data=None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"),
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token_value}",
                "X-Client-Request-Id": correlation_id,
                "User-Agent": "Mezan-Salla-P0-Contract-Runner/3",
            },
        )
        started = time.monotonic()
        try:
            if method != "GET":
                if live_capability is None or live_lease is None or live_operation is None or seed is None or case is None:
                    raise ContractRunnerError(LIVE_EXECUTOR_REQUIRED)
                if amasi:
                    fresh_before = _amasi_fresh_snapshot(transport, seed, case, correlation_id, before_write=True)
                    if canonical_digest(fresh_before) != canonical_digest(before):
                        raise ContractRunnerError("AMASI_TEST_BASELINE_CHANGED")
                current_operation = {
                    **({"environment": "amasi-test-orders"} if amasi else {}),
                    "store_id": config.demo_store_id,
                    "manifest_digest": canonical_digest(seed),
                    "case_digest": canonical_digest(case),
                    "order_id": str(case["order_id"]),
                    "method": case["method"],
                    "path": case["path"],
                    "body": case.get("body"),
                    "retry_once": case.get("retry_once", False),
                }
                now = datetime.now(timezone.utc)
                approval_digest = hashlib.sha256(config.write_approval_id.strip().encode("utf-8")).hexdigest()
                if now > live_capability["expires_at"]:
                    raise ContractRunnerError(LIVE_CAPABILITY_EXPIRED)
                if (
                    live_capability["attempt_id"] != live_lease.attempt_id
                    or live_capability["manifest_digest"] != canonical_digest(seed)
                    or live_capability["case_digest"] != canonical_digest(case)
                    or live_capability["operation_digest"] != canonical_digest(current_operation)
                    or live_capability["operation_digest"] != live_lease.operation_digest
                    or live_capability["approval_digest"] != live_lease.approval_digest
                    or live_capability["approval_digest"] != approval_digest
                    or current_operation != live_operation
                    or method != live_operation["method"]
                    or path != live_operation["path"]
                    or body != live_operation["body"]
                    or correlation_id != live_capability["correlation_id"]
                ):
                    raise ContractRunnerError(LIVE_CAPABILITY_MISMATCH)
                if live_capability["remaining_write_budget"] <= 0:
                    raise ContractRunnerError(ATTEMPT_REPLAY_BLOCKED)
                live_capability["remaining_write_budget"] -= 1
                write_attempted = True
            with http_open(http_request, timeout=30) as response:
                decoded = _decode_response(response.status, response.read().decode("utf-8", errors="replace"), response.headers, started)
                if amasi and method == "GET" and not _amasi_response_success(decoded):
                    raise ContractRunnerError("AMASI_TEST_PROVIDER_READ_REJECTED")
                return decoded
        except urllib.error.HTTPError as exc:
            decoded = _decode_response(exc.code, exc.read().decode("utf-8", errors="replace"), exc.headers, started)
            if amasi and method == "GET" and not _amasi_response_success(decoded):
                raise ContractRunnerError("AMASI_TEST_PROVIDER_READ_REJECTED") from None
            return decoded
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
            return {"status": None, "body": None, "transport_error": type(exc).__name__, "elapsed_ms": round((time.monotonic() - started) * 1000)}

    class _CliReadOnlyTransport:
        __slots__ = ()

        def request(self, method: str, path: str, body: dict[str, Any] | None, correlation_id: str) -> dict[str, Any]:
            if method != "GET" or body is not None:
                raise ContractRunnerError(LIVE_EXECUTOR_REQUIRED)
            return request(method, path, None, correlation_id)

    transport = _CliReadOnlyTransport()
    try:
        if args.command == "readiness":
            gate = readiness(config, transport)
            print(json.dumps(gate, ensure_ascii=False, indent=2))
            return 2 if amasi and not gate["status"].startswith("READY_FOR_") else 0
        assert case is not None and seed is not None and capability_expiry is not None
        gate = readiness(config, transport)
        if gate["status"] != ("READY_FOR_AMASI_TEST_WRITES" if amasi else "READY_FOR_SANDBOX_WRITES"):
            raise ContractRunnerError(gate["status"])

        order_id = str(case["order_id"])
        correlation_id = case.get("client_request_id") or f"p0-{case['id']}-{uuid.uuid4().hex}"
        before = _amasi_fresh_snapshot(transport, seed, case, correlation_id, before_write=True) if amasi else _safe_fetch_snapshot(transport, order_id, correlation_id)
        if not before["fetch_ok"] or str(before.get("order_id")) != order_id:
            raise ContractRunnerError(BASELINE_FETCH_REQUIRED)
        _verify_delete_replacement_in_baseline(case, before, seed=seed)

        manifest_digest = canonical_digest(seed)
        case_digest = canonical_digest(case)
        operation = {
            **({"environment": "amasi-test-orders"} if amasi else {}),
            "store_id": config.demo_store_id,
            "manifest_digest": manifest_digest,
            "case_digest": case_digest,
            "order_id": order_id,
            "method": case["method"],
            "path": case["path"],
            "body": case.get("body"),
            "retry_once": case.get("retry_once", False),
        }
        operation_digest = canonical_digest(operation)
        ledger = LocalAttemptLedger(_canonical_state_root())
        lease = ledger.acquire(
            store_id=config.demo_store_id,
            order_id=order_id,
            manifest_digest=manifest_digest,
            case_digest=case_digest,
            operation_digest=operation_digest,
            approval_id=config.write_approval_id,
            expires_at=capability_expiry.isoformat(),
        )
        writer = EvidenceWriter(config.evidence_dir)
        started_at = datetime.now(timezone.utc)
        intent_path: Path | None = None
        evidence_path: Path | None = None
        response: dict[str, Any] = {"status": None, "body": None, "transport_error": "NOT_ATTEMPTED"}
        retry_response: dict[str, Any] | None = None
        after = before
        terminal_outcome = "UNKNOWN"
        live_operation = operation
        live_lease = lease
        live_capability = {
            "attempt_id": lease.attempt_id,
            "manifest_digest": manifest_digest,
            "case_digest": case_digest,
            "operation_digest": operation_digest,
            "approval_digest": lease.approval_digest,
            "correlation_id": correlation_id,
            "expires_at": capability_expiry,
            "remaining_write_budget": 2 if case.get("retry_once") else 1,
        }
        try:
            intent_path = writer.write_intent(
                str(case["id"]),
                lease.attempt_id,
                redact_live({
                    "record_type": "WRITE_INTENT_START",
                    "classification": evidence_classification,
                    "timestamp": started_at.isoformat(),
                    "attempt_id": lease.attempt_id,
                    "test_case_id": case["id"],
                    "store_id_hash": _hash_id(config.demo_store_id),
                    "identity_verified": True,
                    "manifest_digest": manifest_digest,
                    "case_digest": case_digest,
                    "operation_digest": operation_digest,
                    "capability_expires_at": capability_expiry.isoformat(),
                    "request": {"method": case["method"], "path": case["path"], "body": case.get("body"), "correlation_id": correlation_id},
                    "baseline": before,
                    "outcome_if_terminal_absent": "UNKNOWN",
                    "durability": LOCAL_EVIDENCE_DURABILITY,
                }),
            )
            try:
                response = request(case["method"], case["path"], case.get("body"), correlation_id)
            except ContractRunnerError as exc:
                response = {"status": None, "body": None, "transport_error": str(exc)}
            except Exception as exc:
                response = {"status": None, "body": None, "transport_error": type(exc).__name__}
            after = _amasi_fresh_snapshot(transport, seed, case, correlation_id) if amasi else _safe_fetch_snapshot(transport, order_id, correlation_id)
            idempotency = "SALLA_IDEMPOTENCY_INCONCLUSIVE"
            first_postconditions = evaluate_fixed_postconditions(case, seed, before, after)
            first_write_proven = (
                isinstance(response.get("status"), int)
                and 200 <= response["status"] < 300
                and all(row["passed"] for row in first_postconditions)
            )
            if case.get("retry_once") and first_write_proven:
                after_first = after
                try:
                    retry_response = request(case["method"], case["path"], case.get("body"), correlation_id)
                except ContractRunnerError as exc:
                    retry_response = {"status": None, "body": None, "transport_error": str(exc)}
                except Exception as exc:
                    retry_response = {"status": None, "body": None, "transport_error": type(exc).__name__}
                after = _safe_fetch_snapshot(transport, order_id, correlation_id)
                if (
                    isinstance(retry_response.get("status"), int)
                    and 200 <= retry_response["status"] < 300
                    and after["fetch_ok"]
                    and canonical_digest(after) == canonical_digest(after_first)
                ):
                    idempotency = "SALLA_IDEMPOTENCY_OBSERVED"
                elif not retry_response.get("transport_error") and after["fetch_ok"]:
                    idempotency = "SALLA_IDEMPOTENCY_UNSUPPORTED"

            filtered_events = _collect_postwrite_webhooks(
                args.webhook_events,
                initial_events=events,
                config=config,
                case=case,
                started_at=started_at,
                correlation_id=correlation_id,
                wait_seconds=args.webhook_wait_seconds,
            )
            observed = _analyze_observed_attempt(case, seed, before, response, after, filtered_events, retry_response)
            terminal_outcome = observed["attempt_outcome"]
            final_verdict = observed["observed_verdict"]
            final_reason = observed["observed_outcome_reason"]
            if final_verdict == "PASS" and not filtered_events:
                final_verdict = "INCONCLUSIVE"
                final_reason = "WEBHOOK_SOURCE_NOT_CONFIGURED" if args.webhook_events is None else "WEBHOOK_EVIDENCE_NOT_OBSERVED"
            evidence = {
                "record_type": "WRITE_ATTEMPT_TERMINAL",
                "classification": evidence_classification,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "test_case_id": case["id"],
                "attempt_id": lease.attempt_id,
                "intent_record": intent_path.name,
                "durability": LOCAL_EVIDENCE_DURABILITY,
                "store_id_hash": _hash_id(config.demo_store_id),
                "identity_verified": True,
                "manifest_digest": manifest_digest,
                "case_digest": case_digest,
                "operation_digest": operation_digest,
                "request": {"method": case["method"], "path": case["path"], "body": case.get("body"), "correlation_id": correlation_id},
                "response_status": response.get("status"),
                "response_body": response.get("body"),
                "request_id": response.get("request_id"),
                "response_id": response.get("response_id"),
                "transport_error": response.get("transport_error"),
                "retry_response": retry_response,
                "before": before,
                "after": after,
                "semantic_diff": _semantic_diff(before, after),
                "verdict": final_verdict,
                "final_verdict_reason": final_reason,
                "idempotency": idempotency,
                "transaction_level_behavior": "UNAVAILABLE_SCOPE_NOT_GRANTED",
                **observed,
            }
            evidence_path = writer.write_terminal(str(case["id"]), lease.attempt_id, redact_live(evidence))
            ledger.write_terminal(lease, terminal_outcome)
        except Exception as exc:
            if evidence_path is None and intent_path is not None:
                try:
                    evidence_path = writer.write_terminal(
                        str(case["id"]),
                        lease.attempt_id,
                        redact_live({
                            "record_type": "WRITE_ATTEMPT_TERMINAL",
                            "classification": evidence_classification,
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "test_case_id": case["id"],
                            "attempt_id": lease.attempt_id,
                            "intent_record": intent_path.name,
                            "attempt_outcome": "UNKNOWN",
                            "durability": LOCAL_EVIDENCE_DURABILITY,
                            "store_id_hash": _hash_id(config.demo_store_id),
                            "identity_verified": True,
                            "manifest_digest": manifest_digest,
                            "case_digest": case_digest,
                            "operation_digest": operation_digest,
                            "request": {"method": case["method"], "path": case["path"], "body": case.get("body"), "correlation_id": correlation_id},
                            "response_status": response.get("status"),
                            "response_body": response.get("body"),
                            "request_id": response.get("request_id"),
                            "response_id": response.get("response_id"),
                            "transport_error": response.get("transport_error") or (str(exc) if isinstance(exc, ContractRunnerError) else type(exc).__name__),
                            "retry_response": retry_response,
                            "before": before,
                            "after": after,
                            "semantic_diff": _semantic_diff(before, after),
                            "webhook_events": [],
                            "assertions_executed": 0,
                            "assertions": [],
                            "fixed_postconditions": [],
                            "verdict": "INCONCLUSIVE",
                            "final_verdict_reason": "WRITE_OUTCOME_UNKNOWN_RECONCILIATION_REQUIRED",
                            "observed_verdict": "INCONCLUSIVE",
                            "observed_outcome_reason": "WRITE_OUTCOME_UNKNOWN_RECONCILIATION_REQUIRED",
                            "idempotency": "SALLA_IDEMPOTENCY_INCONCLUSIVE",
                            "transaction_level_behavior": "UNAVAILABLE_SCOPE_NOT_GRANTED",
                        }),
                    )
                except Exception:
                    evidence_path = None
            try:
                ledger.write_terminal(lease, "UNKNOWN")
            except Exception:
                pass
            if isinstance(exc, ContractRunnerError):
                raise
            raise ContractRunnerError("SALLA_P0_ATTEMPT_FAILED") from None
        assert evidence_path is not None
        path = evidence_path
        print(json.dumps({"status": "EVIDENCE_WRITTEN", "path": str(path)}, ensure_ascii=False))
        return 0
    except (ContractRunnerError, OSError, json.JSONDecodeError) as exc:
        if args.command == "run" and not write_attempted:
            _print_prewrite_block(args.command, str(exc))
        else:
            print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
