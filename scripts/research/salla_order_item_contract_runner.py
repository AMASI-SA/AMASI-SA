"""Fail-closed Demo Store contract runner for MZ-ORDER-REVISION-SALLA-001."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

NOT_CONFIGURED = "SALLA_SANDBOX_NOT_CONFIGURED"
IDENTITY_MISMATCH = "SALLA_DEMO_STORE_IDENTITY_MISMATCH"
ENDPOINT_NOT_ALLOWED = "SALLA_P0_ENDPOINT_NOT_ALLOWED"
WRITES_DISABLED = "SALLA_SANDBOX_WRITES_DISABLED"
FIXTURE_CLASSIFICATION = "MOCK_CONTRACT_FIXTURE"
REAL_EVIDENCE_CLASSIFICATION = "SALLA_DEMO_STORE_EVIDENCE"
OFFICIAL_BASE_URL = "https://api.salla.dev/admin/v2"
REQUIRED_ORDER_STATES = {"pending", "under_review", "in_progress", "paid", "partially_paid", "completed", "cancelled"}
REQUIRED_PRODUCT_KINDS = {"simple", "size_color_variant", "text_option", "checkbox_yes_no", "multi_quantity", "replacement"}
SENSITIVE_KEYS = {"access_token", "authorization", "bearer", "card", "customer", "email", "mobile", "name", "phone", "receiver", "refresh_token", "token"}


class ContractRunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class SandboxConfig:
    base_url: str
    access_token: str
    demo_store_id: str
    token_scopes: frozenset[str]
    seed_manifest: Path
    evidence_dir: Path
    demo_confirmed: bool
    writes_enabled: bool
    destructive_retry_enabled: bool

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "SandboxConfig":
        source = env if env is not None else os.environ
        required = {
            "base_url": source.get("SALLA_SANDBOX_BASE_URL", "").strip(),
            "access_token": source.get("SALLA_SANDBOX_ACCESS_TOKEN", "").strip(),
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
            OFFICIAL_BASE_URL, required["access_token"], required["demo_store_id"], scopes,
            Path(required["seed_manifest"]), Path(required["evidence_dir"]),
            source.get("SALLA_DEMO_STORE_CONFIRMED", "").casefold() == "true",
            source.get("SALLA_SANDBOX_RUN_WRITES", "").casefold() == "true",
            source.get("SALLA_DEMO_ALLOW_DESTRUCTIVE_RETRY", "").casefold() == "true",
        )


def _is_sensitive(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return normalized in SENSITIVE_KEYS or any(x in normalized for x in ("secret", "password", "authorization", "token"))


def sanitize(value: Any, *, key: str = "") -> Any:
    if key and _is_sensitive(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(x) for x in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        value = re.sub(r"(?i)(access_token|refresh_token|token)=([^&\s]+)", r"\1=[REDACTED]", value)
    return value


def load_seed_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH") from exc
    if data.get("classification") != "SANDBOX_SEED_MANIFEST":
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    return data


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


class HttpTransport:
    evidence_classification = REAL_EVIDENCE_CLASSIFICATION

    def __init__(self, config: SandboxConfig):
        self.config = config

    def request(self, method: str, path: str, body: dict[str, Any] | None, correlation_id: str) -> dict[str, Any]:
        validate_endpoint(method, path)
        request = urllib.request.Request(
            f"{self.config.base_url}/{path.lstrip('/')}",
            data=None if body is None else json.dumps(body).encode("utf-8"), method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {self.config.access_token}", "X-Client-Request-Id": correlation_id, "User-Agent": "Mezan-Salla-P0-Contract-Runner/2"},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return _decode_response(response.status, response.read().decode("utf-8", errors="replace"), response.headers, started)
        except urllib.error.HTTPError as exc:
            return _decode_response(exc.code, exc.read().decode("utf-8", errors="replace"), exc.headers, started)
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
            return {"status": None, "body": None, "transport_error": type(exc).__name__, "elapsed_ms": round((time.monotonic() - started) * 1000)}


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
    body = response.get("body")
    return body.get("data") if isinstance(body, dict) else None


def verify_demo_identity(config: SandboxConfig, transport: Any) -> dict[str, Any]:
    response = transport.request("GET", "/store/info", None, "p0-identity")
    data = _data(response)
    actual_id = str(data.get("id", "")) if isinstance(data, dict) else ""
    actual_type = str(data.get("type", "")).casefold() if isinstance(data, dict) else ""
    if response.get("status") != 200 or actual_id != config.demo_store_id or actual_type != "demo":
        raise ContractRunnerError(IDENTITY_MISMATCH)
    return {"verified": True, "store_id": actual_id, "store_type": actual_type}


def _nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != []


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def validate_seed_structure(config: SandboxConfig, seed: dict[str, Any]) -> None:
    if str(seed.get("store_id", "")) != config.demo_store_id:
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")
    orders = seed.get("orders", [])
    required_fields = {"state", "order_id", "order_number", "item_id", "product_id", "sku", "payment_method", "branch_id"}
    if {str(x.get("state", "")) for x in orders} != REQUIRED_ORDER_STATES or any(not all(_nonempty(x.get(k)) for k in required_fields) for x in orders):
        raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
    products = seed.get("products", [])
    if {str(x.get("kind", "")) for x in products} != REQUIRED_PRODUCT_KINDS or any(not _nonempty(x.get("product_id")) or not _nonempty(x.get("sku")) for x in products):
        raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
    for row in products:
        if row.get("kind") == "size_color_variant" and not all(_nonempty(row.get(k)) for k in ("variant_ids", "option_ids", "value_ids")):
            raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
        if row.get("kind") in {"text_option", "checkbox_yes_no"} and not _nonempty(row.get("option_ids")):
            raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
        if row.get("kind") == "checkbox_yes_no" and not _nonempty(row.get("value_ids")):
            raise ContractRunnerError("BLOCKED_FIXTURE_MISSING")
    if any(_nonempty(v) for k, v in _walk(seed) if k.casefold() in {"customer", "customer_name", "email", "mobile", "phone", "address"}):
        raise ContractRunnerError("BLOCKED_SEED_MISMATCH")


def _status_slug(order: dict[str, Any]) -> str:
    value = order.get("status")
    if isinstance(value, dict):
        value = value.get("slug") or value.get("name")
    return str(value or "").strip().casefold().replace(" ", "_")


def _hash_id(value: Any) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def readiness(config: SandboxConfig, transport: Any) -> dict[str, Any]:
    try:
        identity = verify_demo_identity(config, transport)
    except ContractRunnerError as exc:
        return {"status": "BLOCKED_IDENTITY_MISMATCH", "reason": str(exc)}
    if "orders.read_write" not in config.token_scopes or not ({"products.read", "products.read_write"} & config.token_scopes):
        return {"status": "BLOCKED_SCOPE_MISSING", "required": ["orders.read_write", "products.read|products.read_write"]}
    try:
        seed = load_seed_manifest(config.seed_manifest)
        validate_seed_structure(config, seed)
    except ContractRunnerError as exc:
        return {"status": str(exc) if str(exc).startswith("BLOCKED_") else "BLOCKED_SEED_MISMATCH", "reason": str(exc)}
    for row in seed["orders"]:
        order_id = str(row["order_id"])
        order = _data(transport.request("GET", f"/orders/{order_id}", None, "p0-readiness"))
        items = _data(transport.request("GET", f"/orders/items?order_id={urllib.parse.quote(order_id)}", None, "p0-readiness"))
        item_ids = {str(x.get("id")) for x in items or [] if isinstance(x, dict)}
        if not isinstance(order, dict) or str(order.get("id", "")) != order_id or _status_slug(order) != row["state"] or str(row["item_id"]) not in item_ids:
            return {"status": "BLOCKED_SEED_MISMATCH", "order_id_hash": _hash_id(order_id)}
    for row in seed["products"]:
        product_id = str(row["product_id"])
        product = _data(transport.request("GET", f"/products/{product_id}", None, "p0-readiness"))
        if not isinstance(product, dict) or str(product.get("id", "")) != product_id or str(product.get("sku", "")) != str(row["sku"]):
            return {"status": "BLOCKED_FIXTURE_MISSING", "product_id_hash": _hash_id(product_id)}
    try:
        config.evidence_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=config.evidence_dir, prefix="p0-readiness-", delete=True):
            pass
    except OSError:
        return {"status": "BLOCKED_FIXTURE_MISSING", "reason": "EVIDENCE_DIRECTORY_NOT_WRITABLE"}
    return {
        "status": "READY_FOR_SANDBOX_WRITES" if config.demo_confirmed and config.writes_enabled else "READY_FOR_READ_ONLY",
        "identity_verified": True, "store_id_hash": _hash_id(identity["store_id"]),
        "scopes_verified": ["orders.read_write", "products.read"],
        "transactions_scope_required": False, "branches_scope_required": False,
    }


def authorized_ids(seed: dict[str, Any]) -> dict[str, set[str]]:
    return {
        "orders": {str(x["order_id"]) for x in seed["orders"]},
        "items": {str(x["item_id"]) for x in seed["orders"]},
        "products": {str(x["product_id"]) for x in seed["products"]},
        "branches": {str(x.get("branch_id")) for x in seed["orders"] + seed["products"] if _nonempty(x.get("branch_id"))},
    }


def validate_case(case: dict[str, Any], seed: dict[str, Any]) -> None:
    validate_endpoint(str(case.get("method", "")), str(case.get("path", "")))
    ids = authorized_ids(seed)
    body = case.get("body", {})
    order_id = str(case.get("order_id") or body.get("order_id", ""))
    if order_id not in ids["orders"]:
        raise ContractRunnerError("SALLA_P0_ORDER_ID_NOT_AUTHORIZED")
    match = re.fullmatch(r"/orders/items/([^/]+)", urllib.parse.urlsplit(str(case["path"])).path)
    if match and match.group(1) not in ids["items"]:
        raise ContractRunnerError("SALLA_P0_ITEM_ID_NOT_AUTHORIZED")
    product_id = body.get("product_id") or (body.get("identifier") if body.get("identifier_type") == "id" else None)
    if product_id is not None and str(product_id) not in ids["products"]:
        raise ContractRunnerError("SALLA_P0_PRODUCT_ID_NOT_AUTHORIZED")
    if _nonempty(body.get("branch_id")) and str(body["branch_id"]) not in ids["branches"]:
        raise ContractRunnerError("SALLA_P0_BRANCH_ID_NOT_AUTHORIZED")
    ensure_replacement_order(case.get("steps", []))


def ensure_replacement_order(steps: list[dict[str, Any]]) -> None:
    actions = [str(x.get("action")) for x in steps]
    if "delete_old" in actions and not {"create_replacement", "fetch_confirm_replacement"}.issubset(set(actions[:actions.index("delete_old")])):
        raise ContractRunnerError("DELETE_FIRST_FORBIDDEN")


def _amount(value: Any) -> Any:
    return value.get("amount") if isinstance(value, dict) else value


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
        "items": [{"item_id": x.get("id"), "product_id": x.get("product_id") or (x.get("product") or {}).get("id"), "sku": x.get("sku"), "quantity": x.get("quantity"), "options": x.get("options", []), "total": _amount((x.get("amounts") or {}).get("total")), "branches_quantity": x.get("branches_quantity", [])} for x in items if isinstance(x, dict)],
    }


def fetch_snapshot(transport: Any, order_id: str, correlation_id: str) -> dict[str, Any]:
    return extract_snapshot(transport.request("GET", f"/orders/{order_id}", None, correlation_id), transport.request("GET", f"/orders/items?order_id={urllib.parse.quote(order_id)}", None, correlation_id))


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


class EvidenceWriter:
    def __init__(self, directory: Path):
        self.directory = directory

    def write(self, evidence: dict[str, Any]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        case_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(evidence["test_case_id"]))
        path = self.directory / f"{case_id}-{uuid.uuid4().hex[:10]}.json"
        path.write_text(json.dumps(sanitize(evidence), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


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


def run_case(config: SandboxConfig, case: dict[str, Any], transport: Any, writer: EvidenceWriter, webhook_loader: Callable[[str], list[dict[str, Any]]]) -> Path:
    gate = readiness(config, transport)
    if gate["status"] != "READY_FOR_SANDBOX_WRITES":
        raise ContractRunnerError(gate["status"])
    seed = load_seed_manifest(config.seed_manifest)
    validate_case(case, seed)
    order_id = str(case.get("order_id") or case.get("body", {}).get("order_id"))
    correlation_id = case.get("client_request_id") or f"p0-{case['id']}-{uuid.uuid4().hex}"
    started_at = datetime.now(timezone.utc)
    before = fetch_snapshot(transport, order_id, correlation_id)
    response = transport.request(case["method"], case["path"], case.get("body"), correlation_id)
    after = fetch_snapshot(transport, order_id, correlation_id)
    idempotency = "SALLA_IDEMPOTENCY_INCONCLUSIVE"
    if case.get("retry_once"):
        if response.get("transport_error"):
            raise ContractRunnerError("DESTRUCTIVE_RETRY_FORBIDDEN_AFTER_UNKNOWN_WRITE")
        if not config.destructive_retry_enabled or not case.get("disposable_order_confirmed"):
            raise ContractRunnerError("SALLA_DESTRUCTIVE_RETRY_NOT_CONFIRMED")
        count_after_first = len(after["items"])
        transport.request(case["method"], case["path"], case.get("body"), correlation_id)
        after = fetch_snapshot(transport, order_id, correlation_id)
        if len(after["items"]) == count_after_first:
            idempotency = "SALLA_IDEMPOTENCY_OBSERVED"
        elif len(after["items"]) > count_after_first:
            idempotency = "SALLA_IDEMPOTENCY_UNSUPPORTED"
    assertions = execute_assertions(case.get("assertions", []), before, after)
    if response.get("transport_error") or not after["fetch_ok"]:
        verdict, reason = "INCONCLUSIVE", "WRITE_OUTCOME_UNKNOWN_RECONCILIATION_REQUIRED"
    elif not assertions:
        verdict, reason = "INCONCLUSIVE", "HTTP_SUCCESS_WITHOUT_BUSINESS_ASSERTIONS"
    elif all(x["passed"] for x in assertions):
        verdict, reason = "PASS", "ALL_ASSERTIONS_PROVEN_BY_FINAL_FETCH"
    else:
        verdict, reason = "FAIL", "FINAL_FETCH_CONTRADICTED_ASSERTIONS"
    classification = getattr(transport, "evidence_classification", FIXTURE_CLASSIFICATION)
    if classification != REAL_EVIDENCE_CLASSIFICATION:
        verdict, reason = "NOT_EXECUTED", "MOCK_EVIDENCE_CANNOT_PROVE_SALLA_BEHAVIOR"
    evidence = {
        "classification": classification, "timestamp": started_at.isoformat(), "test_case_id": case["id"],
        "store_id_hash": _hash_id(config.demo_store_id), "identity_verified": True,
        "request": {"method": case["method"], "path": case["path"], "body": case.get("body"), "correlation_id": correlation_id},
        "response_status": response.get("status"), "response_body": response.get("body"), "request_id": response.get("request_id"), "response_id": response.get("response_id"), "transport_error": response.get("transport_error"),
        "before": before, "after": after, "semantic_diff": _semantic_diff(before, after),
        "webhook_events": filter_webhooks(webhook_loader(case["id"]), store_id=config.demo_store_id, order_id=order_id, started_at=started_at, correlation_id=correlation_id),
        "assertions_executed": len(assertions), "assertions": assertions, "verdict": verdict, "final_verdict_reason": reason,
        "idempotency": idempotency, "transaction_level_behavior": "UNAVAILABLE_SCOPE_NOT_GRANTED",
    }
    return writer.write(evidence)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("readiness", "run"))
    parser.add_argument("--case-file", type=Path)
    parser.add_argument("--webhook-events", type=Path)
    args = parser.parse_args(argv)
    try:
        config = SandboxConfig.from_env()
        transport = HttpTransport(config)
        if args.command == "readiness":
            print(json.dumps(readiness(config, transport), ensure_ascii=False, indent=2))
            return 0
        if not args.case_file:
            raise ContractRunnerError("SALLA_SANDBOX_CASE_FILE_REQUIRED")
        case = json.loads(args.case_file.read_text(encoding="utf-8"))
        events = json.loads(args.webhook_events.read_text(encoding="utf-8")) if args.webhook_events and args.webhook_events.exists() else []
        path = run_case(config, case, transport, EvidenceWriter(config.evidence_dir), lambda case_id: [x for x in events if x.get("test_case_id") == case_id])
        print(json.dumps({"status": "EVIDENCE_WRITTEN", "path": str(path)}, ensure_ascii=False))
        return 0
    except (ContractRunnerError, OSError, json.JSONDecodeError) as exc:
        print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
