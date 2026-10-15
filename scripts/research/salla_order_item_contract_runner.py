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

# The same contracts run in the CLI and the packaged HTTP test surface.
_backend_contract_path = str(Path(__file__).resolve().parents[2] / "backend")
if _backend_contract_path not in sys.path:
    sys.path.insert(0, _backend_contract_path)
from order_revision_contracts import (
    AMASI_CASE_CLASSIFICATION,
    AMASI_MANIFEST_CLASSIFICATION,
    ASSERTION_PATH,
    AmasiTestConfig,
    BODY_FIELDS,
    CASE_FIELDS,
    CASE_INVALID,
    ContractRunnerError,
    ENDPOINT_NOT_ALLOWED,
    NOT_CONFIGURED,
    OFFICIAL_BASE_URL,
    REDACTION_KEY_COLLISION,
    REQUIRED_ORDER_STATES,
    REQUIRED_PRODUCT_KINDS,
    SENSITIVE_KEYS,
    SandboxConfig,
    UPDATE_MUTABLE_FIELDS,
    UPDATE_MUTATION_REQUIRED,
    _amasi_money,
    _amasi_response_success,
    _amount,
    _data,
    _expected_option_selections,
    _hash_id,
    _id_text,
    _is_sensitive,
    _item_amount,
    _item_matches_binding,
    _item_product_id,
    _matching_product_binding,
    _nonempty,
    _option_selections,
    _parse_option_tuple_list,
    _postcondition,
    _product_bindings,
    _product_contract_relations,
    _resolve_path,
    _seed_product_relations,
    _snapshot_item_by_id,
    _snapshot_items,
    _status_slug,
    _strict_object,
    _update_value,
    _validate_amasi_order,
    _validate_assertions,
    _validate_delete_steps,
    _validate_options,
    _verify_amasi_shipments,
    _walk,
    canonical_digest,
    evaluate_fixed_postconditions,
    execute_assertions,
    extract_snapshot,
    sanitize,
    validate_case,
    validate_endpoint,
    validate_seed_structure,
)

IDENTITY_MISMATCH = "SALLA_DEMO_STORE_IDENTITY_MISMATCH"
WRITES_DISABLED = "SALLA_SANDBOX_WRITES_DISABLED"
FIXTURE_CLASSIFICATION = "MOCK_CONTRACT_FIXTURE"
REAL_EVIDENCE_CLASSIFICATION = "SALLA_DEMO_STORE_EVIDENCE"
AMASI_EVIDENCE_CLASSIFICATION = "SALLA_LIVE_TEST_ORDER_EVIDENCE"
CREDENTIAL_RESOLVER_ENV_UNAVAILABLE = "P0_CREDENTIAL_RESOLVER_ENV_UNAVAILABLE"
CREDENTIAL_RESOLVER_IDENTITY_UNAVAILABLE = "P0_CREDENTIAL_RESOLVER_IDENTITY_UNAVAILABLE"
CREDENTIAL_RESOLVER_FAILED = "P0_CREDENTIAL_RESOLVER_FAILED"
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
LINUX_POSIX_REQUIRED = "SALLA_P0_LINUX_POSIX_REQUIRED"
WRITE_APPROVAL_TTL_SECONDS = 300
LIVE_CAPABILITY_TTL_SECONDS = 60


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


class _NoLiveTestRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "AMASI_TEST_REDIRECT_REJECTED", headers, fp)


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


def verify_demo_identity(config: SandboxConfig, transport: Any) -> dict[str, Any]:
    response = transport.request("GET", "/store/info", None, "p0-identity")
    data = _data(response)
    actual_id = str(data.get("id", "")) if isinstance(data, dict) else ""
    actual_type = str(data.get("type", "")).casefold() if isinstance(data, dict) else ""
    if not isinstance(response, dict) or response.get("status") != 200 or actual_id != config.demo_store_id or actual_type != "demo":
        raise ContractRunnerError(IDENTITY_MISMATCH)
    return {"verified": True, "store_id": actual_id, "store_type": actual_type}


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
                _verify_amasi_shipments(transport, row, "p0-readiness")
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


def fetch_snapshot(transport: Any, order_id: str, correlation_id: str) -> dict[str, Any]:
    return extract_snapshot(transport.request("GET", f"/orders/{order_id}", None, correlation_id), transport.request("GET", f"/orders/items?order_id={urllib.parse.quote(order_id)}", None, correlation_id))


def _amasi_fresh_snapshot(transport: Any, seed: dict[str, Any], case: dict[str, Any], correlation_id: str, *, before_write: bool = False) -> dict[str, Any]:
    row = next(row for row in seed["orders"] if str(row["order_id"]) == str(case["order_id"]))
    order_id = str(row["order_id"])
    shipment_review = _verify_amasi_shipments(transport, row, correlation_id)
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
    final_shipment_review = _verify_amasi_shipments(transport, row, correlation_id)
    if final_shipment_review != shipment_review:
        raise ContractRunnerError("AMASI_TEST_COURIER_STATE_CHANGED")
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
                    payment_status="unpaid", shipment_count=shipment_review["count"], shipment_review=shipment_review)
    return snapshot


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
