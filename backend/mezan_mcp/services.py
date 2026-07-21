"""Read-only implementations of the phase-one Mezan MCP tools."""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional
from urllib.parse import quote

import httpx
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from .security import (
    ReadOnlyDatabase,
    ReadOnlyHttpClient,
    sanitize_output,
    validate_public_https_url,
)


SALLA_API_BASE = "https://api.salla.dev/admin/v2"


def _decrypt_salla_access_token(ciphertext: Any) -> str:
    """Decrypt the existing Salla token without importing Salla write routes."""
    if not ciphertext:
        return ""
    primary = os.environ.get("SALLA_TOKEN_ENC_KEY", "").strip()
    if not primary:
        raise RuntimeError("Salla token encryption key is unavailable")
    keys = [Fernet(primary.encode("utf-8"))]
    rotation = os.environ.get("SALLA_TOKEN_ENC_KEY_OLD", "").strip()
    if rotation:
        keys.append(Fernet(rotation.encode("utf-8")))
    if isinstance(ciphertext, str):
        ciphertext = ciphertext.encode("utf-8")
    try:
        return MultiFernet(keys).decrypt(bytes(ciphertext)).decode("utf-8")
    except (InvalidToken, TypeError, ValueError) as exc:
        raise RuntimeError("Stored Salla read credential cannot be decrypted") from exc


def _number(value: Any) -> float:
    if isinstance(value, Mapping):
        value = _first(value, "amount", "value", default=0)
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return round(number, 2) if math.isfinite(number) else 0.0


def _first(source: Mapping[str, Any], *paths: str, default: Any = None) -> Any:
    for path in paths:
        current: Any = source
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                current = None
                break
            current = current[part]
        if current not in (None, "", [], {}):
            return current
    return default


def _order_status(order: Mapping[str, Any]) -> str:
    return str(
        _first(
            order,
            "order_status_native",
            "order_status",
            "order_status_slug",
            "status.name",
            "status.slug",
            default="",
        )
        or ""
    ).strip()


def _safe_options(line: Mapping[str, Any]) -> list[dict[str, str]]:
    raw = _first(line, "options", "product_options", "attributes", default=[])
    if isinstance(raw, Mapping):
        raw = [{"name": key, "value": value} for key, value in raw.items()]
    if not isinstance(raw, list):
        return []
    options: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw[:30]:
        if not isinstance(item, Mapping):
            continue
        name = str(
            _first(item, "name", "label", "key", "option", default="") or ""
        ).strip()
        raw_value = _first(
            item,
            "value",
            "values",
            "selected",
            "choice",
            "text",
            default="",
        )
        if isinstance(raw_value, Mapping):
            raw_value = _first(raw_value, "name", "value", "label", "text", default="")
        elif isinstance(raw_value, list):
            raw_value = ", ".join(
                str(
                    _first(value, "name", "value", "label", "text", default="")
                    if isinstance(value, Mapping)
                    else value
                ).strip()
                for value in raw_value[:20]
                if value not in (None, "", [], {})
            )
        value = str(raw_value or "").strip()
        if not name and not value:
            continue
        identity = (name.casefold(), value.casefold())
        if identity in seen:
            continue
        seen.add(identity)
        options.append({"name": name, "value": value})
    return options


def _safe_items(order: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = _first(order, "products", "items", "line_items", default=[])
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for line in raw[:100]:
        if not isinstance(line, Mapping):
            continue
        quantity = int(_number(_first(line, "quantity", "qty", default=1)))
        items.append(
            {
                "name": str(
                    _first(line, "name", "product_name", "product.name", default="")
                    or ""
                )[:240],
                "sku": str(
                    _first(
                        line,
                        "sku",
                        "SKU",
                        "product.sku",
                        "variant.sku",
                        default="",
                    )
                    or ""
                )[:100],
                "quantity": max(quantity, 0),
                "unit_price": _number(
                    _first(
                        line,
                        "unit_price",
                        "price",
                        "amount",
                        "amounts.price_without_tax",
                        "amounts.price",
                        default=0,
                    )
                ),
                "total": _number(
                    _first(
                        line,
                        "total",
                        "total_amount",
                        "amounts.total",
                        default=0,
                    )
                ),
                "options": _safe_options(line),
            }
        )
    return items


def safe_order(order: Mapping[str, Any]) -> dict[str, Any]:
    """Return operational/accounting fields without customer PII or raw data."""
    payment_method = order.get("payment_method")
    if isinstance(payment_method, Mapping):
        payment_method = _first(
            payment_method,
            "name",
            "code",
            "slug",
            default="",
        )
    shipping_company = order.get("shipping_company")
    if isinstance(shipping_company, Mapping):
        shipping_company = _first(
            shipping_company,
            "name",
            "code",
            "slug",
            default="",
        )
    return sanitize_output(
        {
            "order_number": str(order.get("order_number") or ""),
            "order_date": order.get("order_date"),
            "status": _order_status(order),
            "payment_method": str(payment_method or "")[:120],
            "currency": order.get("currency") or "SAR",
            "subtotal": _number(order.get("subtotal")),
            "discount": _number(order.get("discount")),
            "shipping_cost": _number(order.get("shipping_cost")),
            "tax": _number(order.get("tax")),
            "total_amount": _number(order.get("total_amount")),
            "paid_amount": _number(
                _first(order, "paid_amount", "payment_amount", "amount_paid", default=0)
            ),
            "remaining_amount": _number(
                _first(order, "remaining_amount", "amount_remaining", default=0)
            ),
            "shipping_company": str(shipping_company or "")[:120],
            "tracking_number": order.get("tracking_number"),
            "source": order.get("data_source") or order.get("last_source"),
            "items": _safe_items(order),
        }
    )


async def _to_list(cursor: Any, limit: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit)
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(limit)
    rows: list[dict[str, Any]] = []
    async for row in cursor:
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows


async def _find_order(db: ReadOnlyDatabase, tenant_id: str, order_number: str) -> dict[str, Any]:
    order = await db.unified_orders.find_one(
        {"user_id": tenant_id, "order_number": str(order_number).strip()},
        {"_id": 0},
    )
    if not order:
        raise LookupError("Order was not found for the authenticated tenant")
    return order


def _salla_order_id(order: Mapping[str, Any]) -> Optional[str]:
    raw = order.get("raw_by_source") or {}
    if not isinstance(raw, Mapping):
        raw = {}
    salla = raw.get("salla_direct") or {}
    if not isinstance(salla, Mapping):
        salla = {}
    value = _first(
        order,
        "salla_order_id",
        "order_id",
        "source_order_id",
        default=None,
    ) or _first(salla, "id", "data.id", default=None)
    return str(value).strip() if value not in (None, "") else None


async def _read_salla_order(
    db: ReadOnlyDatabase,
    tenant_id: str,
    local_order: Mapping[str, Any],
) -> dict[str, Any]:
    integration = await db.salla_integrations.find_one(
        {"user_id": tenant_id},
        {"_id": 0, "access_token_encrypted": 1, "status": 1, "expires_at": 1},
    )
    if not integration or integration.get("status") != "connected":
        raise RuntimeError("Salla read connection is unavailable for this tenant")
    try:
        access_token = _decrypt_salla_access_token(
            integration.get("access_token_encrypted") or b""
        )
    except Exception as exc:
        raise RuntimeError("Stored Salla read credential cannot be used") from exc
    if not access_token:
        raise RuntimeError("Stored Salla read credential is missing")
    order_id = _salla_order_id(local_order)
    if not order_id:
        raise RuntimeError("The local order has no Salla order id for a read-only comparison")
    url = validate_public_https_url(
        f"{SALLA_API_BASE}/orders/{quote(order_id, safe='')}"
    )
    timeout = httpx.Timeout(float(os.environ.get("MEZAN_MCP_SALLA_TIMEOUT_SECONDS", "12")))
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as raw_client:
        client = ReadOnlyHttpClient(
            raw_client,
            allowed_hosts={"api.salla.dev"},
        )
        response = await client.get(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            },
        )
    # Never refresh, persist, retry or call another endpoint from this tool.
    if response.status_code == 401:
        raise RuntimeError("Salla rejected the current read token; MCP did not refresh or write it")
    if response.status_code >= 400:
        raise RuntimeError(f"Salla read failed with HTTP {response.status_code}")
    body = response.json()
    data = body.get("data") if isinstance(body, Mapping) else body
    if not isinstance(data, Mapping):
        raise RuntimeError("Salla returned an unsupported order payload")
    return dict(data)


def _compare_items(local: list[dict[str, Any]], remote: list[dict[str, Any]]) -> dict[str, Any]:
    def index(items: list[dict[str, Any]]) -> dict[str, int]:
        result: dict[str, int] = {}
        for item in items:
            key = str(item.get("sku") or item.get("name") or "").strip()
            if not key:
                continue
            result[key] = result.get(key, 0) + int(item.get("quantity") or 0)
        return result

    local_index = index(local)
    remote_index = index(remote)
    keys = sorted(set(local_index) | set(remote_index))
    mismatches = [
        {"key": key, "mezan_quantity": local_index.get(key, 0), "salla_quantity": remote_index.get(key, 0)}
        for key in keys
        if local_index.get(key, 0) != remote_index.get(key, 0)
    ]
    return {"matches": not mismatches, "mismatches": mismatches[:100]}


class MezanReadOnlyTools:
    def __init__(self, raw_db: Any):
        self.db = ReadOnlyDatabase(raw_db)

    async def mezan_health(self, tenant_id: str, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        await self.db.ping()
        return {
            "ok": True,
            "service": "mezan-mcp-gateway",
            "environment": os.environ.get("MEZAN_ENVIRONMENT", "production"),
            "access": "read-only",
            "qoyod_network_access": False,
            "server_time": datetime.now(timezone.utc).isoformat(),
        }

    async def mezan_get_system_status(self, tenant_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        await self.db.ping()
        hours = min(max(int(arguments.get("hours", 24)), 1), 168)
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        failures = await self._recent_failures(tenant_id, since=since, limit=20)
        return {
            "ok": True,
            "database": "reachable",
            "environment": os.environ.get("MEZAN_ENVIRONMENT", "production"),
            "read_only": True,
            "recent_failure_count": len(failures),
            "failure_window_hours": hours,
        }

    async def mezan_get_order(self, tenant_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        order_number = str(arguments.get("order_number") or "").strip()
        if not order_number:
            raise ValueError("order_number is required")
        return safe_order(await _find_order(self.db, tenant_id, order_number))

    async def mezan_compare_order_with_salla(
        self, tenant_id: str, arguments: Mapping[str, Any]
    ) -> dict[str, Any]:
        order_number = str(arguments.get("order_number") or "").strip()
        if not order_number:
            raise ValueError("order_number is required")
        local_raw = await _find_order(self.db, tenant_id, order_number)
        remote_raw = await _read_salla_order(self.db, tenant_id, local_raw)
        local = safe_order(local_raw)
        remote = safe_order(
            {
                **dict(remote_raw),
                "order_number": _first(remote_raw, "reference_id", "number", "id", default=order_number),
                "order_status": _first(remote_raw, "status.name", "status.slug", "status", default=""),
                "total_amount": _first(remote_raw, "amounts.total.amount", "total.amount", "total", default=0),
                "subtotal": _first(remote_raw, "amounts.sub_total.amount", "subtotal.amount", "subtotal", default=0),
                "discount": _first(remote_raw, "amounts.discounts.amount", "discount.amount", "discount", default=0),
                "shipping_cost": _first(remote_raw, "amounts.shipping_cost.amount", "shipping.amount", "shipping_cost", default=0),
                "tax": _first(remote_raw, "amounts.tax.amount", "tax.amount", "tax", default=0),
                "currency": _first(remote_raw, "currency", "amounts.total.currency", default="SAR"),
                "products": _first(remote_raw, "items", "products", default=[]),
            }
        )
        amount_difference = round(_number(local.get("total_amount")) - _number(remote.get("total_amount")), 2)
        status_matches = str(local.get("status") or "").casefold() == str(remote.get("status") or "").casefold()
        item_result = _compare_items(local.get("items") or [], remote.get("items") or [])
        return {
            "order_number": order_number,
            "matches": abs(amount_difference) == 0 and status_matches and item_result["matches"],
            "amount_difference": amount_difference,
            "status": {
                "matches": status_matches,
                "mezan": local.get("status"),
                "salla": remote.get("status"),
            },
            "items": item_result,
            "mezan": local,
            "salla": remote,
            "salla_access": "GET only; no token refresh or persistence",
        }

    async def mezan_get_error_trace(self, tenant_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        reference = str(arguments.get("error_reference") or "").strip()
        order_number = str(arguments.get("order_number") or "").strip()
        if not reference and not order_number:
            raise ValueError("error_reference or order_number is required")
        clauses: list[dict[str, Any]] = []
        if reference:
            clauses.extend([
                {"error_reference": reference},
                {"last_error_reference": reference},
            ])
        if order_number:
            clauses.extend([
                {"order_number": order_number},
                {"salla_order_number": order_number},
                {"canonical_payload.order_number": order_number},
            ])
        query = {"user_id": tenant_id, "$or": clauses}
        projection = {
            "_id": 0,
            "raw_payload": 0,
            "canonical_payload.customer": 0,
            "canonical_payload.shipping_address": 0,
        }
        results: list[dict[str, Any]] = []
        for collection_name in (
            "integration_inbox",
            "salla_sync_logs",
            "import_jobs",
            "webhook_parse_failures",
        ):
            cursor = getattr(self.db, collection_name).find(query, projection)
            for row in await _to_list(cursor, 25):
                results.append(
                    sanitize_output(
                        {
                            "source": collection_name,
                            "status": row.get("status") or row.get("state"),
                            "stage": row.get("stage") or row.get("event_type"),
                            "order_number": row.get("order_number") or row.get("salla_order_number"),
                            "error_code": row.get("error_code") or row.get("last_error_code"),
                            "error_reference": row.get("error_reference") or row.get("last_error_reference"),
                            "error_message": str(row.get("last_error") or row.get("error") or "")[:500],
                            "occurred_at": row.get("updated_at") or row.get("created_at") or row.get("received_at"),
                        }
                    )
                )
        return {"matches": len(results), "trace": results[:50]}

    async def _recent_failures(
        self, tenant_id: str, *, since: datetime, limit: int
    ) -> list[dict[str, Any]]:
        query = {
            "user_id": tenant_id,
            "$or": [
                {"status": {"$in": ["failed", "error", "dead_letter"]}},
                {"state": {"$in": ["failed", "error", "dead_letter"]}},
                {"last_error": {"$nin": [None, ""]}},
            ],
        }
        # Collections use different timestamp field names, so the bounded
        # result set is clipped again in Python after the failure query.
        rows: list[dict[str, Any]] = []
        for collection_name in ("salla_sync_logs", "integration_inbox", "import_jobs", "webhook_parse_failures"):
            cursor = getattr(self.db, collection_name).find(query, {"_id": 0, "raw_payload": 0})
            for row in await _to_list(cursor, limit):
                when = row.get("updated_at") or row.get("created_at") or row.get("received_at") or row.get("occurred_at")
                if isinstance(when, datetime):
                    comparable = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
                    if comparable < since:
                        continue
                rows.append(
                    sanitize_output(
                        {
                            "source": collection_name,
                            "order_number": row.get("order_number") or row.get("salla_order_number"),
                            "status": row.get("status") or row.get("state"),
                            "stage": row.get("stage") or row.get("event_type"),
                            "error_code": row.get("error_code") or row.get("last_error_code"),
                            "error_reference": row.get("error_reference") or row.get("last_error_reference"),
                            "error_message": str(row.get("last_error") or row.get("error") or "")[:500],
                            "occurred_at": when,
                        }
                    )
                )
        rows.sort(key=lambda row: str(row.get("occurred_at") or ""), reverse=True)
        return rows[:limit]

    async def mezan_list_recent_failures(self, tenant_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        limit = min(max(int(arguments.get("limit", 20)), 1), 100)
        hours = min(max(int(arguments.get("hours", 24)), 1), 720)
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        rows = await self._recent_failures(tenant_id, since=since, limit=limit)
        return {"count": len(rows), "hours": hours, "failures": rows}

    async def mezan_qoyod_reconciliation(self, tenant_id: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        order_number = str(arguments.get("order_number") or "").strip()
        if not order_number:
            raise ValueError("order_number is required")
        order = safe_order(await _find_order(self.db, tenant_id, order_number))
        inbox_rows = await _to_list(
            self.db.integration_inbox.find(
                {
                    "user_id": tenant_id,
                    "$or": [
                        {"salla_order_number": order_number},
                        {"canonical_payload.order_number": order_number},
                    ],
                },
                {
                    "_id": 0,
                    "raw_payload": 0,
                    "canonical_payload.customer": 0,
                    "canonical_payload.shipping_address": 0,
                },
            ),
            25,
        )
        invoice_rows = await _to_list(
            self.db.qoyod_invoices.find(
                {
                    "user_id": tenant_id,
                    "$or": [
                        {"salla_order_number": order_number},
                        {"order_number": order_number},
                        {"reference": order_number},
                    ],
                },
                {"_id": 0},
            ),
            25,
        )
        invoices = []
        for row in invoice_rows:
            total = _number(_first(row, "total", "total_amount", "gross_amount", default=0))
            invoices.append(
                sanitize_output(
                    {
                        "qoyod_invoice_id": row.get("qoyod_invoice_id") or row.get("invoice_id"),
                        "invoice_number": row.get("invoice_number") or row.get("reference"),
                        "status": row.get("status"),
                        "total": total,
                        "paid": row.get("paid"),
                        "remaining": _number(row.get("remaining")),
                        "difference_from_mezan": round(total - _number(order.get("total_amount")), 2),
                    }
                )
            )
        markers = [
            sanitize_output(
                {
                    "status": row.get("status") or row.get("state"),
                    "qoyod_invoice_id": row.get("manual_qoyod_invoice_id") or row.get("qoyod_invoice_id"),
                    "qoyod_payment_id": row.get("qoyod_payment_id"),
                    "last_error_code": row.get("last_error_code") or row.get("error_code"),
                    "updated_at": row.get("updated_at") or row.get("received_at"),
                }
            )
            for row in inbox_rows
        ]
        return {
            "order_number": order_number,
            "mezan_total": order.get("total_amount"),
            "local_qoyod_invoices": invoices,
            "local_pipeline_markers": markers,
            "qoyod_access": "local read-only reconciliation; no Qoyod network call",
        }

    async def mezan_get_database_schema(self, tenant_id: str, _arguments: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_source": "static allowlist; no database values inspected",
            "tenant_isolation_field": "user_id",
            "collections": {
                "unified_orders": [
                    "user_id", "order_number", "order_date", "order_status",
                    "payment_method", "currency", "subtotal", "discount",
                    "shipping_cost", "tax", "total_amount", "products",
                ],
                "salla_integrations": ["user_id", "status", "expires_at"],
                "salla_sync_logs": ["user_id", "status", "stage", "error_code", "updated_at"],
                "integration_inbox": [
                    "user_id", "salla_order_number", "status", "state",
                    "qoyod_invoice_id", "manual_qoyod_invoice_id", "error_code",
                ],
                "qoyod_invoices": [
                    "user_id", "salla_order_number", "qoyod_invoice_id",
                    "invoice_number", "status", "total", "remaining",
                ],
                "import_jobs": ["user_id", "status", "stage", "error_code", "updated_at"],
            },
            "excluded": [
                "raw payloads", "tokens", "secrets", "customer phone",
                "customer email", "customer address", "arbitrary SQL or Mongo queries",
            ],
        }


TOOL_METHODS = {
    "mezan_health": MezanReadOnlyTools.mezan_health,
    "mezan_get_system_status": MezanReadOnlyTools.mezan_get_system_status,
    "mezan_get_order": MezanReadOnlyTools.mezan_get_order,
    "mezan_compare_order_with_salla": MezanReadOnlyTools.mezan_compare_order_with_salla,
    "mezan_get_error_trace": MezanReadOnlyTools.mezan_get_error_trace,
    "mezan_list_recent_failures": MezanReadOnlyTools.mezan_list_recent_failures,
    "mezan_qoyod_reconciliation": MezanReadOnlyTools.mezan_qoyod_reconciliation,
    "mezan_get_database_schema": MezanReadOnlyTools.mezan_get_database_schema,
}


async def invoke_tool(
    tools: MezanReadOnlyTools,
    name: str,
    tenant_id: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    method = TOOL_METHODS.get(name)
    if method is None:
        raise KeyError(f"Unknown MCP tool: {name}")
    result = await method(tools, tenant_id, arguments)
    return sanitize_output(result)
