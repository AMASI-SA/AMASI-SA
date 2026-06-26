"""Salla webhook → Canonical SalesOrderDTO — pure normalizer.

Day 3 scope is **strict**: validate the incoming payload, build the
DTO, stop. No business rules, no Qoyod calls, no side-effects.

Two public entry points:

  • `validate(raw)`  → (bool, error_dict | None)
      Sanity-checks the raw Salla payload. Cheap, structural.

  • `normalize(raw, *, received_at)`  → SalesOrderDTO
      Builds the canonical DTO. Pure; the only knowledge it has of
      Salla lives in this file.

Both raise/return structured errors (`{"code": <token>, "message": ...}`)
so the state-machine layer can dump them into `stage_history[].error`
without re-massaging.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.dto import (
    SalesOrderDTO, CustomerDTO, LineItemDTO, AddressDTO,
)


# ─────────────────────────────────────────────────────────────────────
# Public exceptions / error shapes
# ─────────────────────────────────────────────────────────────────────
class NormalizationError(ValueError):
    """Raised by `normalize()` when the payload is well-formed
    enough to pass `validate()` yet still cannot produce a DTO
    (e.g. arithmetic with a non-numeric string). Carries a structured
    error dict suitable for `stage_history`.
    """
    def __init__(self, code: str, message: str, **extra):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def to_log_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        out.update(self.extra)
        return out


# ─────────────────────────────────────────────────────────────────────
# Helpers — pure, side-effect-free
# ─────────────────────────────────────────────────────────────────────
def _f(val: Any, default: float = 0.0) -> float:
    """Coerce to float without raising; used for amount fields."""
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _money(node: Any, default: float = 0.0) -> float:
    """Salla money shape: `{amount: "12.30", currency: "SAR"}` OR a bare
    number. Returns the float amount."""
    if isinstance(node, dict):
        return _f(node.get("amount"), default)
    return _f(node, default)


def _currency(node: Any, fallback: str = "SAR") -> str:
    if isinstance(node, dict):
        c = node.get("currency")
        if c:
            return str(c).upper()
    return fallback


def _parse_dt(val: Any) -> Optional[datetime]:
    """Best-effort ISO-8601 / Salla date parser. Returns None if unrecognised."""
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, (int, float)):
        # epoch seconds
        return datetime.fromtimestamp(float(val), tz=timezone.utc)
    if isinstance(val, dict):
        # Salla sometimes wraps dates: `{date: "2026-06-25 ...", timezone: ...}`
        return _parse_dt(val.get("date") or val.get("iso"))
    s = str(val).strip()
    # Try common formats; we don't go nuts here — DTO accepts None.
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",     "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        # Last-resort: Python 3.11+ fromisoformat tolerates "...+03:00".
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


_PHONE_RE = re.compile(r"[^\d+]")


def normalize_phone(raw: Any) -> Optional[str]:
    """Best-effort phone normaliser. Strips spaces/dashes, prefixes
    a Saudi country code when an obviously-Saudi local number is given.

    Examples:
        "0501234567"      → "+966501234567"
        "+966501234567"   → "+966501234567"
        "966 50-123 4567" → "+966501234567"
        None / ""         → None
    """
    if raw is None:
        return None
    s = _PHONE_RE.sub("", str(raw))
    if not s:
        return None
    if s.startswith("+"):
        return s
    if s.startswith("00"):
        return "+" + s[2:]
    if s.startswith("966"):
        return "+" + s
    if s.startswith("05") and len(s) >= 9:
        # Saudi mobile in local format → switch to E.164.
        return "+966" + s[1:]
    if s.startswith("5") and len(s) == 9:
        return "+966" + s
    # Otherwise: prepend a '+' to be syntactically valid E.164-ish.
    return "+" + s


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s or not _EMAIL_RE.match(s):
        return None
    return s


# ─────────────────────────────────────────────────────────────────────
# Step 5 — VALIDATION
# ─────────────────────────────────────────────────────────────────────
def _extract_data(raw: dict) -> dict:
    """Salla wraps the order under `data` for some webhooks and inline
    for others. Treat both shapes uniformly."""
    if not isinstance(raw, dict):
        return {}
    if isinstance(raw.get("data"), dict):
        return raw["data"]
    return raw


def validate(raw: Any) -> tuple[bool, Optional[dict]]:
    """Cheap structural validation. Returns (ok, error_dict_or_none).

    Failure codes (closed set — used by tests):
        invalid_payload_type
        missing_data_object
        missing_order_id
        missing_order_status
        missing_items
        empty_items
    """
    if not isinstance(raw, dict):
        return False, {
            "code": "invalid_payload_type",
            "message": "payload must be a JSON object",
        }
    # `data` envelope is the canonical Salla webhook shape. If it's
    # missing AND the root dict doesn't already look like an order
    # (no top-level id/reference_id), we treat that as an envelope
    # failure — earlier than missing_order_id so the operator gets
    # the most actionable error code.
    has_data_key = isinstance(raw.get("data"), dict) and raw.get("data")
    has_inline_order = bool(raw.get("id") or raw.get("reference_id") or raw.get("order_id"))
    if not has_data_key and not has_inline_order:
        return False, {
            "code": "missing_data_object",
            "message": "payload is missing the `data` envelope",
        }
    data = _extract_data(raw)
    order_id = data.get("reference_id") or data.get("id") or data.get("order_id")
    if not order_id:
        return False, {
            "code": "missing_order_id",
            "message": "order must have `id` or `reference_id`",
        }
    status_node = data.get("status")
    status_native = _extract_status_native(status_node)
    if not status_native:
        return False, {
            "code": "missing_order_status",
            "message": "order must carry a status",
        }
    if "items" not in data:
        return False, {
            "code": "missing_items",
            "message": "order is missing the `items` array",
        }
    items = data.get("items") or []
    if not isinstance(items, list) or len(items) == 0:
        return False, {
            "code": "empty_items",
            "message": "at least one line item is required",
        }
    return True, None


def _extract_status_native(node: Any) -> str:
    if isinstance(node, dict):
        # Salla can present customized status nested.
        custom = node.get("customized")
        if isinstance(custom, dict):
            name = custom.get("name") or custom.get("slug")
            if name:
                return str(name).strip()
        name = node.get("name") or node.get("slug") or node.get("label")
        if name:
            return str(name).strip()
        return ""
    if node is None:
        return ""
    return str(node).strip()


# ─────────────────────────────────────────────────────────────────────
# Step 6 — NORMALIZATION (Salla → SalesOrderDTO)
# ─────────────────────────────────────────────────────────────────────
def _canonical_status(native: str) -> str:
    """Map a Salla status string to a canonical token. Conservative —
    unknown statuses pass through (lower-cased + underscored) so the
    pipeline never silently mis-routes a new Salla status.
    """
    mapping = {
        "تم التنفيذ":      "completed",
        "تم التوصيل":      "delivered",
        "تم الشحن":        "shipped",
        "قيد التنفيذ":     "processing",
        "بانتظار المراجعة": "in_review",
        "بإنتظار المراجعة": "in_review",
        "ملغي":            "cancelled",
        "ملغية":           "cancelled",
        "مسترجع":          "refunded",
        "مسترجعة":         "refunded",
    }
    if native in mapping:
        return mapping[native]
    low = native.strip().lower()
    if low in ("completed", "delivered", "shipped", "processing",
               "in_review", "pending", "cancelled", "refunded"):
        return low
    return low.replace(" ", "_") or "unknown"


def _canonical_payment_method(native: Optional[str]) -> Optional[str]:
    if not native:
        return None
    low = str(native).strip().lower()
    # Best-effort canonical keys; full mapping happens at 4a.
    table = {
        "mada":           "mada",
        "visa":           "visa",
        "mastercard":     "mastercard",
        "apple pay":      "apple_pay",
        "applepay":       "apple_pay",
        "stc pay":        "stc_pay",
        "stcpay":         "stc_pay",
        "cash":           "cash",
        "cod":            "cod",
        "cash on delivery": "cod",
        "bank":           "bank_transfer",
        "bank transfer":  "bank_transfer",
        "tamara":         "tamara",
        "tabby":          "tabby",
        "emkan":          "emkan",
        "إمكان":          "emkan",
        "credit_card":    "credit_card",
        "paypal":         "paypal",
    }
    return table.get(low, low.replace(" ", "_"))


def _normalize_customer(data: dict) -> CustomerDTO:
    raw = data.get("customer") or {}
    if isinstance(raw, str):
        # Salla occasionally returns just the customer's name string.
        return CustomerDTO(name=raw or "ضيف", is_guest=True)
    first = (raw.get("first_name") or "").strip()
    last  = (raw.get("last_name") or "").strip()
    full  = (f"{first} {last}").strip() or (raw.get("name") or "").strip() or "ضيف"
    return CustomerDTO(
        name=full,
        phone=normalize_phone(raw.get("mobile") or raw.get("phone")),
        email=normalize_email(raw.get("email")),
        is_guest=bool(raw.get("is_guest", False)),
        city=(raw.get("city") or None),
        country=(raw.get("country") or None),
    )


def _normalize_item(it: dict) -> LineItemDTO:
    if not isinstance(it, dict):
        raise NormalizationError(
            "invalid_item_shape", "line item must be an object",
            item=str(it)[:120],
        )
    amounts = it.get("amounts") or {}
    sku = (it.get("sku") or "").strip()
    if not sku and isinstance(it.get("product"), dict):
        sku = str(it["product"].get("sku") or "").strip()
    name = (it.get("name") or "").strip()
    if not name and isinstance(it.get("product"), dict):
        name = str(it["product"].get("name") or "").strip()

    # Salla layered prices: `amounts.price_without_tax.amount` + `amounts.tax.amount`
    unit_price = _money(amounts.get("price_without_tax")
                        or amounts.get("price")
                        or it.get("price"))
    tax_amount = _money(amounts.get("tax")) if amounts else _money(it.get("tax"))
    total      = _money(amounts.get("total")) if amounts else _money(it.get("total"))

    product_id = None
    if isinstance(it.get("product"), dict):
        pid = it["product"].get("id")
        product_id = str(pid) if pid is not None else None
    elif it.get("product_id") is not None:
        product_id = str(it["product_id"])

    return LineItemDTO(
        sku=sku, name=name,
        quantity=_f(it.get("quantity"), 1.0),
        unit_price=unit_price,
        tax_amount=tax_amount,
        total=total,
        product_id=product_id,
    )


def _normalize_address(node: Any) -> Optional[AddressDTO]:
    if not isinstance(node, dict) or not node:
        return None
    return AddressDTO(
        line1=node.get("street") or node.get("address") or node.get("line1") or None,
        line2=node.get("line2") or None,
        city=node.get("city") or None,
        region=node.get("region") or node.get("state") or None,
        country=node.get("country") or node.get("country_name") or None,
        postal=node.get("postal_code") or node.get("postcode") or None,
    )


def normalize(raw: dict, *, received_at: Optional[datetime] = None) -> SalesOrderDTO:
    """Build a `SalesOrderDTO` from a validated raw Salla payload.

    Caller responsibility: call `validate()` first. This function trusts
    that the basic shape is sound, but it still raises
    `NormalizationError` for any arithmetic / shape problem it hits
    while building the DTO (e.g. items isn't iterable).
    """
    if not isinstance(raw, dict):
        raise NormalizationError(
            "invalid_payload_type", "payload must be a JSON object")

    data = _extract_data(raw)
    if not data:
        raise NormalizationError(
            "missing_data_object", "payload missing `data` envelope")

    order_id = str(data.get("reference_id") or data.get("id") or data.get("order_id"))
    source_order_id = (str(data.get("id"))
                       if data.get("id") and str(data.get("id")) != order_id
                       else None)
    order_number = str(data.get("reference_id") or data.get("id") or order_id)

    status_native = _extract_status_native(data.get("status"))
    if not status_native:
        raise NormalizationError(
            "missing_order_status", "could not extract status string")
    status_canonical = _canonical_status(status_native)

    # Amounts
    amounts = data.get("amounts") or {}
    currency = _currency(amounts.get("total")
                         or amounts.get("sub_total"), "SAR")

    # Lines — accumulate as DTOs, raising on the first shape error so
    # the state machine can record exactly which item broke.
    items_raw = data.get("items") or []
    if not isinstance(items_raw, list):
        raise NormalizationError(
            "invalid_items_shape", "items must be a list")
    items: list[LineItemDTO] = []
    for idx, it in enumerate(items_raw):
        try:
            items.append(_normalize_item(it))
        except NormalizationError:
            raise
        except Exception as exc:   # pragma: no cover — defensive
            raise NormalizationError(
                "item_build_failed",
                f"item #{idx}: {exc.__class__.__name__}: {exc}",
                item_index=idx,
            )

    # Payment
    pm_native = data.get("payment_method") or data.get("payment_method_name")
    if isinstance(pm_native, dict):
        pm_native = pm_native.get("name")
    pm_native = str(pm_native).strip() if pm_native else None

    dto = SalesOrderDTO(
        order_id=order_id,
        source_order_id=source_order_id,
        order_number=order_number,
        order_status=status_canonical,
        order_status_native=status_native,
        order_date=_parse_dt(data.get("date") or data.get("created_at")),
        completed_at=_parse_dt(data.get("completed_at")
                               or data.get("delivered_at")),
        paid_at=_parse_dt(data.get("paid_at")),
        currency=currency,
        subtotal=_money(amounts.get("sub_total") or amounts.get("subtotal")),
        tax_amount=_money(amounts.get("tax")),
        shipping_amount=_money(amounts.get("shipping")),
        discount_amount=_money(amounts.get("discounts") or amounts.get("discount")),
        total_amount=_money(amounts.get("total")),
        customer=_normalize_customer(data),
        items=items,
        payment_method=_canonical_payment_method(pm_native),
        payment_method_native=pm_native,
        shipping_address=_normalize_address(data.get("shipping_address")
                                            or data.get("ship_to")),
        billing_address=_normalize_address(data.get("billing_address")),
        metadata={
            "source":        "salla",
            "source_event":  raw.get("event") or raw.get("event_type") or "order",
            "received_at":   (received_at or datetime.now(timezone.utc)).isoformat(),
            "salla_id":      str(data.get("id") or ""),
            "salla_ref":     str(data.get("reference_id") or ""),
        },
    )
    return dto
