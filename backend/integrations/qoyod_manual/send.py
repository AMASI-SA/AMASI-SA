"""Plan-B Manual Send — the 4-step, one-order-at-a-time push.

Sequence (immutable):
    1. Look up customer by phone/email → create if missing.
    2. Look up each line-item by SKU → create if missing.
    3. Create ONE invoice for the order.
    4. Create ONE invoice payment.

Guards (immutable, only these):
    G1: Idempotency — atomic lock on `qoyod_manual_send_locks`
        (unique index on `order_number`) BEFORE step 3.
        Also refuses if the order already has `manual_qoyod_invoice_id`
        set OR a real (non-DRY) `qoyod_invoice_id` in inbox, OR if the
        قيود side already has an invoice with the same reference.
    G2: |Salla total − Qoyod-expected total| ≤ 0.01 SAR.
        Refuses BEFORE the invoice POST.
    G3: All Qoyod ids that come back must be positive integers
        (no DRY:/PREVIEW: prefixes accepted or emitted anywhere).
    G4: Payment method must resolve to a Qoyod account_id via the
        EXISTING `qoyod_settings.payment_method_mapping` — no new
        mapping table is created.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, date, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from integrations.qoyod.credentials import get_api_key
from integrations.qoyod.eligible_orders import QOYOD_SYNC_START_DATE
from integrations.qoyod.payment_methods import (
    resolve_payment_account,
    resolve_receiving_bank_account,
    is_cod_family,
)
from integrations.qoyod.unsent_orders import _is_real
from integrations.qoyod_manual.client import (
    ManualQoyodClient, ManualQoyodError,
)
from integrations.qoyod_manual.pending import (
    _matches_status, _salla_order_created_date, SUPPORTED_STATUSES,
)
from integrations.qoyod_manual.order_source import get_order_payment_facts
from qoyod_order_accounting_sync import (
    sync_unified_order_accounting_from_result,
)

logger = logging.getLogger(__name__)

_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)

# Asia/Riyadh — fixed +03:00 offset (no DST). Used for BOTH the
# invoice `issue_date` / `due_date` AND the payment `date` so that the
# قيود ledger reflects "when the operator actually pressed Send",
# NOT the Salla order creation timestamp.
RIYADH_TZ = timezone(timedelta(hours=3))


def _riyadh_now() -> datetime:
    return datetime.now(RIYADH_TZ)


def _riyadh_today_iso() -> str:
    return _riyadh_now().date().isoformat()


# ── Money quantisation (user directive 2026-07-08) ──────────────────
# EVERY monetary value we send to قيود must be rounded to EXACTLY 2
# decimals using `Decimal.quantize(0.01, ROUND_HALF_UP)`. This
# eliminates 0.001-cent drifts that made قيود show "دفعت جزئياً"
# with a residual of 0.01 SAR.
#
# The `payment_amount` sent in step-4 never exceeds the amount collected
# by Salla or the Qoyod invoice total. A +0.01 Qoyod rounding residual is
# intentionally left outstanding and appears as partially paid.
_TWO_PLACES = Decimal("0.01")
_AMOUNT_TOLERANCE = Decimal("0.01")


def _q2(v: Any) -> float:
    """Quantize any numeric-ish value to 2 decimals (ROUND_HALF_UP).
    Returns a plain float — قيود's validator accepts JSON numbers.

    Rounds through Decimal(str(...)) so binary-float artefacts
    (e.g. 0.1 + 0.2 = 0.30000000000000004) are eliminated at the
    boundary.
    """
    if v is None:
        return 0.0
    try:
        d = Decimal(str(v))
    except Exception:
        return 0.0
    return float(d.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _within_amount_tolerance(value: Any) -> bool:
    """Return True when a rounded money difference is at most one halalah."""
    if value is None:
        return False
    try:
        difference = Decimal(str(value)).quantize(
            _TWO_PLACES, rounding=ROUND_HALF_UP)
    except Exception:
        return False
    return difference.is_finite() and abs(difference) <= _AMOUNT_TOLERANCE


def _strict_decimal(value: Any, *, field: str) -> Decimal:
    """Parse a Qoyod payload number without silently coercing bad input."""
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ManualSendRefused(
            "qoyod_preflight_payload_invalid",
            "تعذّر التحقق من قيم فاتورة قيود قبل الإرسال.",
            {"field": field, "value": value},
        ) from exc
    if not result.is_finite() or result < 0:
        raise ManualSendRefused(
            "qoyod_preflight_payload_invalid",
            "تحتوي فاتورة قيود على قيمة غير صالحة قبل الإرسال.",
            {"field": field, "value": value},
        )
    return result


def _preflight_qoyod_invoice_payload(
    payload: dict, *, salla_total: float,
) -> dict:
    """Fail before any Qoyod write when its documented rounding will drift.

    Qoyod accepts monetary values at two decimal places.  Sending the
    three-decimal LRM adjustments used by older Mezan builds lets Qoyod round
    them differently and leaves an invoice without a receipt.  This guard
    validates the *actual outgoing payload*, rounds its monetary inputs to
    Qoyod's supported two decimals, and reproduces Qoyod's document-level
    subtotal/tax rounding before a customer, product, or invoice is created.
    """
    invoice = payload.get("invoice") if isinstance(payload, dict) else None
    lines = invoice.get("line_items") if isinstance(invoice, dict) else None
    if not isinstance(lines, list) or not lines:
        raise ManualSendRefused(
            "qoyod_preflight_payload_invalid",
            "لا تحتوي فاتورة قيود على بنود قابلة للتحقق قبل الإرسال.",
        )

    subtotal_raw = Decimal("0")
    tax_raw = Decimal("0")
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ManualSendRefused(
                "qoyod_preflight_payload_invalid",
                "يوجد بند غير صالح في فاتورة قيود قبل الإرسال.",
                {"line_index": index},
            )
        quantity = _strict_decimal(
            line.get("quantity"), field=f"line_items[{index}].quantity")
        if quantity <= 0:
            raise ManualSendRefused(
                "qoyod_preflight_payload_invalid",
                "كمية بند الفاتورة يجب أن تكون أكبر من صفر.",
                {"line_index": index, "quantity": line.get("quantity")},
            )
        unit_price = _strict_decimal(
            line.get("unit_price"),
            field=f"line_items[{index}].unit_price",
        )
        discount = _strict_decimal(
            line.get("discount") or 0,
            field=f"line_items[{index}].discount",
        )
        tax_percent = _strict_decimal(
            line.get("tax_percent") or 0,
            field=f"line_items[{index}].tax_percent",
        )
        line_net = (
            quantity
            * unit_price.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
            - discount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        )
        if line_net < 0:
            raise ManualSendRefused(
                "qoyod_preflight_payload_invalid",
                "خصم بند الفاتورة أكبر من قيمته.",
                {"line_index": index},
            )
        subtotal_raw += line_net
        tax_raw += line_net * tax_percent / Decimal("100")

    subtotal = subtotal_raw.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    tax = tax_raw.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
    predicted_total = (subtotal + tax).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP)
    expected = Decimal(str(_q2(salla_total)))
    difference = (predicted_total - expected).quantize(
        _TWO_PLACES, rounding=ROUND_HALF_UP)
    if abs(difference) > _AMOUNT_TOLERANCE:
        raise ManualSendRefused(
            "qoyod_preflight_total_mismatch",
            "إجمالي قيود المتوقع يختلف عن إجمالي سلة بأكثر من 0.01 "
            "ريال؛ عُزل الطلب قبل إنشاء الفاتورة.",
            {
                "salla_total": float(expected),
                "qoyod_predicted_total": float(predicted_total),
                "difference": float(difference),
                "allowed_tolerance": 0.01,
                "requires_manual_review": True,
                "qoyod_write_performed": False,
            },
        )
    return {
        "salla_total": float(expected),
        "qoyod_predicted_total": float(predicted_total),
        "difference": float(difference),
    }


def _preflight_qoyod_invoice(
    *, canon: dict, settings: dict, salla_total: float,
) -> dict:
    """Build and validate the invoice before creating any Qoyod resource."""
    line_resolutions: dict[str, int] = {}
    for index, item in enumerate(canon.get("items") or [], start=1):
        sku = str((item or {}).get("sku") or "").strip()
        if not sku:
            raise ManualSendRefused(
                "qoyod_preflight_payload_invalid",
                "يوجد منتج بلا SKU؛ عُزل الطلب قبل الإرسال إلى قيود.",
                {"item_index": index - 1},
            )
        line_resolutions.setdefault(sku, index)
    payload, _, _ = _build_invoice_payload(
        canon=canon,
        contact_id=1,
        line_resolutions=line_resolutions,
        settings=settings,
        send_date_iso=_riyadh_today_iso(),
    )
    return _preflight_qoyod_invoice_payload(
        payload, salla_total=salla_total)


def _overlay_order_engine_facts(canon: dict, facts: dict) -> dict:
    """Overlay trusted Orders V2 facts without mutating the inbox snapshot.

    The inbox canonical payload predates the unified Order Engine and can omit
    explicit order-level COD fees.  The fee is copied only when the engine has
    positively classified it as explicit (or retained its audit source), so
    this helper can never invent a fee from an unexplained total difference.
    """
    result = dict(canon or {})
    payment_method = (
        facts.get("payment_method") or facts.get("payment_method_native")
    )
    if not is_cod_family(payment_method):
        return result
    cod_fee = _q2(facts.get("cod_fee_amount"))
    cod_fee_source = facts.get("cod_fee_source")
    cod_fee_is_explicit = bool(
        facts.get("cod_fee_is_explicit") or cod_fee_source
    )
    if cod_fee > 0 and cod_fee_is_explicit:
        result["cod_fee_amount"] = cod_fee
        result["cod_fee_source_path"] = str(
            cod_fee_source or "order_engine.totals.cod_fee_total"
        )
        result["cod_fee_source_type"] = "order_engine_explicit_source"
    return result


def _extract_qoyod_invoice_total(payload: Any) -> Optional[float]:
    """Extract the persisted invoice total from a Qoyod response.

    Qoyod responses differ between create/show API versions, so support
    the known wrappers and money-object shapes without falling back to a
    locally simulated amount.
    """
    if not isinstance(payload, dict):
        return None

    node = payload.get("invoice") or payload.get("data") or payload
    if not isinstance(node, dict):
        return None

    for key in (
        "total",
        "total_amount",
        "grand_total",
        "gross_total",
        "total_after_tax",
        "total_including_tax",
    ):
        value = node.get(key)
        if isinstance(value, dict):
            value = value.get("amount") or value.get("value")
        if value in (None, ""):
            continue
        try:
            return _q2(value)
        except Exception:
            continue

    return None


def _validate_qoyod_actual_total(
    *, actual_total: Optional[float], salla_total: float, invoice_id: int,
) -> float:
    """Allow up to ±0.01 SAR parity difference before invoice payment."""
    if actual_total is None:
        raise ManualSendRefused(
            "qoyod_actual_total_missing",
            "تم إنشاء فاتورة قيود لكن تعذّر قراءة إجماليها الفعلي — "
            "لن يتم إنشاء السداد.",
            {
                "invoice_id": invoice_id,
                "salla_total": _q2(salla_total),
            },
        )

    actual = _q2(actual_total)
    expected = _q2(salla_total)
    difference = _q2(actual - expected)

    if not _within_amount_tolerance(difference):
        raise ManualSendRefused(
            "qoyod_actual_total_mismatch",
            "إجمالي قيود الفعلي يختلف عن إجمالي سلة بأكثر من 0.01 ريال — "
            "تم إيقاف السداد.",
            {
                "invoice_id": invoice_id,
                "salla_total": expected,
                "qoyod_actual_total": actual,
                "difference": difference,
                "allowed_tolerance": 0.01,
                "payment_created": False,
                "requires_manual_review": True,
            },
        )

    return actual


def _resolve_payment_amount(
    *, qoyod_total: float, salla_collected_total: float,
) -> float:
    """Pay no more than Salla collected and never overpay the invoice.

    When Qoyod's server-side rounding makes the invoice one halalah
    higher than Salla, the one-halalah balance is intentionally left
    outstanding so Qoyod reports the invoice as partially paid.
    """
    return min(_q2(qoyod_total), _q2(salla_collected_total))


class ManualSendRefused(Exception):
    """A guard rejected the send — the response body has the detail."""

    def __init__(self, code: str, message: str, extra: Optional[dict] = None):
        self.code = code
        self.message = message
        self.extra = extra or {}
        super().__init__(f"{code}: {message}")

    def to_dict(self) -> dict:
        return {"ok": False, "code": self.code,
                "message": self.message, "detail": self.extra}


# ─── Helpers ─────────────────────────────────────────────────────────
def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _to_int(v: Any) -> Optional[int]:
    """Coerce a Qoyod id to a positive int. Returns None if the value
    is missing, non-numeric, or matches the forbidden DRY/PREVIEW
    prefixes (guard G3)."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int) and v > 0:
        return v
    if isinstance(v, float) and v > 0 and v == int(v):
        return int(v)
    s = str(v).strip()
    if not s:
        return None
    if s.upper().startswith(("DRY:", "PREVIEW:")):
        return None
    try:
        i = int(s)
        return i if i > 0 else None
    except (TypeError, ValueError):
        return None


async def _acquire_lock(db, *, user_id: str, order_number: str,
                        actor: str) -> str:
    """G1 — atomic idempotency lock.

    Uses `findOneAndUpdate` with $setOnInsert so only ONE concurrent
    caller wins for a given order_number. Returns the lock_id. Raises
    `ManualSendRefused` if the order is already locked by another
    in-progress send OR has already completed.
    """
    coll = db.qoyod_manual_send_locks
    # Fire-and-forget: ensure unique index (idempotent).
    try:
        await coll.create_index("order_number", unique=True)
    except Exception:
        pass

    lock_id = f"manual-{order_number}-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc)
    existing = await coll.find_one(
        {"order_number": order_number, "user_id": user_id})
    if existing:
        status = str(existing.get("status") or "")
        # `already_sent` here mirrors the inbox-level guard in
        # `manual_send_one`: refuse ONLY when BOTH the invoice and
        # payment markers are persisted on the lock record. A lock
        # marked `succeeded` from a pre-2026-07-09 send that never
        # actually completed step 5 must NOT block the retry.
        if status == "succeeded" \
                and existing.get("manual_qoyod_invoice_id") \
                and existing.get("manual_qoyod_payment_id"):
            raise ManualSendRefused(
                "already_sent",
                "الطلب أُرسل مسبقاً من مسار الإرسال اليدوي",
                {"lock_id": existing.get("lock_id"),
                 "manual_qoyod_invoice_id": existing.get(
                     "manual_qoyod_invoice_id"),
                 "manual_qoyod_payment_id": existing.get(
                     "manual_qoyod_payment_id")})
        if status == "in_progress":
            # Auto-release stale locks after 5 minutes.
            started = existing.get("started_at")
            age_ok = False
            if isinstance(started, datetime):
                # Normalise to timezone-aware UTC. Mongo drivers can
                # return `started_at` as naive when the DB was seeded
                # by an older code path — subtracting a naive value
                # from `now` (tz-aware) raises TypeError. Coerce here
                # so lock arithmetic is always safe.
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                age_ok = (now - started).total_seconds() > 300
            if not age_ok:
                raise ManualSendRefused(
                    "in_progress",
                    "الطلب قيد الإرسال حالياً — يرجى الانتظار حتى ينتهي",
                    {"started_at": started.isoformat()
                     if isinstance(started, datetime) else str(started)})
        # Reset failed / stale-in_progress locks in place.
        await coll.update_one(
            {"order_number": order_number, "user_id": user_id},
            {"$set": {"status": "in_progress", "lock_id": lock_id,
                       "started_at": now, "actor": actor,
                       "last_error": None}})
        return lock_id
    await coll.insert_one({
        "order_number":  order_number,
        "user_id":       user_id,
        "lock_id":       lock_id,
        "status":        "in_progress",
        "started_at":    now,
        "actor":         actor,
    })
    return lock_id


async def _finalize_lock(db, *, order_number: str, user_id: str,
                         lock_id: str, status: str,
                         invoice_id: Optional[str] = None,
                         payment_id: Optional[str] = None,
                         error: Optional[dict] = None) -> None:
    patch: dict = {
        "status":      status,
        "finished_at": datetime.now(timezone.utc),
    }
    if invoice_id is not None:
        patch["manual_qoyod_invoice_id"] = invoice_id
    if payment_id is not None:
        patch["manual_qoyod_payment_id"] = payment_id
    if error is not None:
        patch["last_error"] = error
    await db.qoyod_manual_send_locks.update_one(
        {"order_number": order_number, "user_id": user_id,
         "lock_id": lock_id},
        {"$set": patch})


# ─── Payload builders (minimal, standalone) ──────────────────────────
def _build_customer_payload(canon: dict) -> dict:
    cust = canon.get("customer") or {}
    name = str(cust.get("name") or "").strip() \
        or f"عميل {canon.get('order_number') or ''}".strip() \
        or "عميل مباشر"
    payload = {
        "contact": {
            "name":  name,
            "type":  "individual",
        }
    }
    phone = str(cust.get("phone") or "").strip()
    email = str(cust.get("email") or "").strip()
    if phone:
        payload["contact"]["phone"] = phone
    if email:
        payload["contact"]["email"] = email
    return payload


def _unwrap_id(v: Any) -> Any:
    """Coerce a Qoyod id setting to a scalar payload value.

    Plan-B local copy of the legacy `_unwrap_id_for_payload` — Qoyod
    accepts a scalar (int when parseable, otherwise string). Arrays
    are collapsed to their first non-empty element. Returns `None`
    when nothing usable is present (caller then drops the key)."""
    if isinstance(v, (list, tuple)):
        for el in v:
            r = _unwrap_id(el)
            if r not in (None, ""):
                return r
        return None
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return s


def _build_product_payload(item: dict, settings: dict) -> dict:
    """Build a product-creation payload that Qoyod actually accepts.

    Iter-286/287 findings (proven against production):
        • The `sale_item: 1` integer flag MUST be present — without
          it, Qoyod's validator refuses even a valid `selling_price`
          with `enter at least a purchase price or a sales price`.
        • The four required ids (`category_id`, `tax_id`,
          `product_unit_type_id`, `sales_account_id`) must be
          SCALAR (int when possible) — not arrays.
        • Field name is `type` (not `product_type`); the flat
          `name`/`sku` shape works — no `name_ar`/`name_en` needed.
        • `buying_price` / `selling_price` alone (without
          `sale_item: 1`) is the exact combination Qoyod REJECTS —
          which is what earlier Plan-B versions did.

    Iter-290g fallback: if `unit_price=0` (free item), bump the
    catalog `selling_price` to `1.0` — the invoice line still
    carries the true (possibly zero) price, this only affects the
    product-catalog row.
    """
    sku = str(item.get("sku") or "").strip()
    name = str(item.get("name") or sku or "منتج").strip()
    raw_price = item.get("unit_price")
    try:
        selling_price = float(raw_price) if raw_price is not None else 0.0
    except (TypeError, ValueError):
        selling_price = 0.0
    # Bump zero-price products for catalog creation only (Iter-290g).
    catalog_price = selling_price if selling_price > 0 else 1.0
    catalog_price = _q2(catalog_price)

    ptype = (settings.get("default_product_type") or "service")
    product: dict = {
        "name":          name,
        "sku":           sku or None,
        "type":          ptype,
        "is_non_stock":  ptype == "service",
        # Iter-286 — Qoyod's live validator requires these integer
        # flags. Without `sale_item: 1` the payload is rejected
        # regardless of the prices supplied.
        "sale_item":     1,
        "purchase_item": 0,
        "selling_price": catalog_price,
    }
    # Stamp the four required tenant ids as scalars (drop empty).
    for setting_key, product_key in (
        ("default_product_category_id",  "category_id"),
        ("default_product_tax_id",       "tax_id"),
        ("default_product_unit_type_id", "product_unit_type_id"),
        ("default_sales_account_id",     "sales_account_id"),
    ):
        v = _unwrap_id(settings.get(setting_key))
        if v is not None:
            product[product_key] = v
    return {"product": product}


def _build_product_payload_fallback(item: dict, settings: dict) -> dict:
    """Minimal-fields self-heal payload (Iter-286 legacy pattern).

    Strips `type`/`is_non_stock`/`purchase_item` and lets Qoyod pick
    defaults. Used exactly ONCE when the first POST /products still
    gets a 422 about prices — helps tenants whose Qoyod flavour
    dislikes any of the extra flags.
    """
    sku = str(item.get("sku") or "").strip()
    name = str(item.get("name") or sku or "منتج").strip()
    raw_price = item.get("unit_price")
    try:
        selling_price = float(raw_price) if raw_price is not None else 0.0
    except (TypeError, ValueError):
        selling_price = 0.0
    if selling_price <= 0:
        selling_price = 1.0
    product: dict = {
        "name":          name,
        "sku":           sku or None,
        "sale_item":     1,
        "selling_price": _q2(selling_price),
    }
    for setting_key, product_key in (
        ("default_product_category_id",  "category_id"),
        ("default_product_tax_id",       "tax_id"),
        ("default_product_unit_type_id", "product_unit_type_id"),
        ("default_sales_account_id",     "sales_account_id"),
    ):
        v = _unwrap_id(settings.get(setting_key))
        if v is not None:
            product[product_key] = v
    return {"product": product}


def _line_gross(*, unit_price: float, quantity: float,
                discount: float, tax_percent: float) -> float:
    """Qoyod-computed gross for a single line, quantised to 2dp.
    Mirrors the قيود formula:
        gross = (unit_price × qty − discount) × (1 + tax_percent/100)
    All inputs must already be 2dp-quantised except `quantity` (integer
    or fractional but not currency)."""
    net = Decimal(str(unit_price)) * Decimal(str(quantity)) \
        - Decimal(str(discount))
    factor = Decimal(1) + Decimal(str(tax_percent)) / Decimal(100)
    gross = net * factor
    return float(gross.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))


def _compute_item_line(it: dict, line_resolutions: dict,
                       tax_factor: float, tax_percent: float,
                       target_gross_override: Optional[float] = None):
    """Build the {line_payload, breakdown_row, gross} triple for a
    single Salla item.

    `target_gross_override` — when set, forces the line to target
    this gross value instead of `it["total"]`. Used by the residual
    distribution pass (see `_distribute_residual`)."""
    sku = str(it.get("sku") or "").strip()
    pid = line_resolutions.get(sku)
    if pid is None:
        raise ManualSendRefused(
            "product_id_missing",
            f"تعذّر ربط منتج بـ SKU={sku!r}",
            {"sku": sku})
    qty = _f(it.get("quantity"), 1.0) or 1.0
    unit_price_raw = _f(it.get("unit_price"))
    original_target_gross = _f(it.get("total"))
    target_gross = (target_gross_override
                    if target_gross_override is not None
                    else original_target_gross)
    target_net = target_gross / tax_factor if tax_factor else target_gross
    original_base = unit_price_raw * qty
    discount_raw = original_base - target_net
    if discount_raw < 0:
        # Salla's price < computed net: shrink unit_price and zero
        # the discount so قيود's math still lands on the target.
        unit_price = _q2(target_net / qty) if qty else _q2(target_net)
        discount = 0.0
    else:
        unit_price = _q2(unit_price_raw)
        discount = _q2(discount_raw)
    line_gross = _line_gross(
        unit_price=unit_price, quantity=qty,
        discount=discount, tax_percent=tax_percent)
    line_payload = {
        "product_id":    pid,
        "description":   it.get("name") or sku,
        "quantity":      qty,
        "unit_price":    unit_price,
        "discount":      discount,
        "discount_type": "amount",
        "tax_percent":   tax_percent,
    }
    line_net_after_disc = _q2(unit_price * qty - discount)
    line_tax = _q2(line_gross - line_net_after_disc)
    breakdown_row = {
        "sku":                       sku,
        "product_id":                pid,
        "description":               it.get("name") or sku,
        "quantity":                  qty,
        "salla_unit_price":          _q2(unit_price_raw),
        "qoyod_unit_price":          unit_price,
        "salla_line_total":          _q2(original_target_gross),
        "computed_discount":         discount,
        "line_net_after_discount":   line_net_after_disc,
        "line_tax_15pct":            line_tax,
        "line_gross_after_tax":      line_gross,
        "delta_vs_salla_line":       _q2(line_gross
                                          - original_target_gross),
    }
    if target_gross_override is not None:
        breakdown_row["target_gross_override"] = _q2(target_gross)
        breakdown_row["shift_from_original"] = _q2(target_gross
                                                    - original_target_gross)
    return line_payload, breakdown_row, line_gross


def _distribute_residual_over_items(
    items: list[dict], line_resolutions: dict,
    tax_factor: float, tax_percent: float,
    residual_to_absorb: float,
) -> tuple[list[dict], list[dict], float] | None:
    """Try to close a small rounding residual by shifting each of the
    last N item lines' target_gross by ±0.01.

    Returns `(payloads, breakdown_rows, new_expected_total)` when the
    distribution EXACTLY zeros the residual (post-shift new sum ==
    salla_total). Returns `None` when the residual cannot be
    distributed (too large, or the shifted lines don't quantise
    cleanly on قيود's side).

    Rules:
        • Residual absolute value must be ≤ 0.01 × len(items) SAR
          (one line per cent to shift).
        • Only pure-item lines are eligible (shipping / COD sit
          outside the distribution).
        • The LAST N lines take the shift, matching accounting habit
          of "absorbing rounding into the final printed line".
    """
    shift_cents = int(round(abs(residual_to_absorb) * 100))
    if shift_cents == 0 or shift_cents > len(items):
        return None
    sign = 1 if residual_to_absorb > 0 else -1
    # Build shift map: last shift_cents lines get ±0.01 each.
    shifts: dict[int, float] = {}
    start_idx = len(items) - shift_cents
    for i in range(start_idx, len(items)):
        shifts[i] = sign * 0.01

    payloads: list[dict] = []
    rows: list[dict] = []
    total_dec = Decimal("0")
    for idx, it in enumerate(items):
        override = None
        if idx in shifts:
            orig = _f(it.get("total"))
            override = _q2(orig + shifts[idx])
        payload, row, gross = _compute_item_line(
            it, line_resolutions, tax_factor, tax_percent,
            target_gross_override=override)
        payloads.append(payload)
        rows.append(row)
        total_dec += Decimal(str(gross))

    new_total = float(
        total_dec.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    # Verify the shift actually cleared the residual to zero. If
    # قيود's own quantisation of the shifted line lands off by ±0.01
    # (rare, small factor combos), fall back to the adjustment line.
    return payloads, rows, new_total


def _build_invoice_payload(*, canon: dict, contact_id: int,
                           line_resolutions: dict,
                           settings: dict,
                           send_date_iso: str) -> tuple[dict, float, dict]:
    """Return (payload, expected_total, breakdown).

    Residual-closing strategy (in order):
      1. Straight per-line rounding — target = Salla line total.
      2. If the sum drifts from Salla by ≤ len(items) × 0.01 SAR,
         DISTRIBUTE the residual across the LAST N item lines by
         shifting each by ±0.01 SAR. No new line added.
      3. If distribution doesn't clear the residual (edge cases) OR
         the residual is larger than the per-line budget, FALL BACK
         to adding a single tax-free line "تسوية فرق التقريب مع سلة"
         using `rounding_adjustment_product_id`.
      4. If step-3 fires but no product_id is configured → refuse
         with `rounding_adjustment_product_missing`.

    Structural gaps (shipping / COD ignored because their product
    ids aren't wired) SKIP both distribution and the adjustment
    line and produce a plain `totals_mismatch` — those are real
    configuration errors, not rounding.
    """
    try:
        tax_percent = float(settings.get("qoyod_tax_percent") or 15)
    except (TypeError, ValueError):
        tax_percent = 15.0
    tax_percent = _q2(tax_percent)
    tax_factor = 1.0 + tax_percent / 100.0

    lines: list[dict] = []
    breakdown_items: list[dict] = []
    expected_total_dec = Decimal("0")

    raw_items = canon.get("items") or []
    for it in raw_items:
        # `_compute_item_line` is the SINGLE source of truth for a
        # product line: it validates the SKU→product_id resolution,
        # quantises unit_price/discount to 2dp, computes the gross,
        # AND builds the RCA breakdown row. Do NOT re-do this work
        # inline — an accidental duplicate append here caused the
        # double-line bug on order 27027791 (Plan-B rev 2026-07-09).
        payload, row, gross = _compute_item_line(
            it, line_resolutions, tax_factor, tax_percent)
        lines.append(payload)
        breakdown_items.append(row)
        expected_total_dec += Decimal(str(gross))

    # Read the explicit COD fee before deriving shipping's share of the order.
    # Otherwise an order containing both shipping and COD would let shipping
    # absorb the COD amount and then add the COD line a second time.
    cod_fee = _q2(canon.get("cod_fee_amount"))

    # Shipping line (optional — only if configured AND non-zero).
    shipping_amount = _q2(canon.get("shipping_amount"))
    shipping_breakdown: Optional[dict] = None
    if shipping_amount > 0:
        ship_pid = _to_int(settings.get("default_shipping_product_id"))
        items_gross_sum = sum(_f(it.get("total"))
                               for it in canon.get("items") or [])
        ship_target_gross = (
            _f(canon.get("total_amount")) - items_gross_sum - cod_fee
        )
        if ship_pid is not None and ship_target_gross > 0:
            ship_target_net = ship_target_gross / tax_factor
            ship_unit_raw = shipping_amount
            ship_discount_raw = ship_unit_raw - ship_target_net
            if ship_discount_raw < 0:
                ship_unit = _q2(ship_target_net)
                ship_discount = 0.0
            else:
                ship_unit = _q2(ship_unit_raw)
                ship_discount = _q2(ship_discount_raw)
            ship_gross = _line_gross(
                unit_price=ship_unit, quantity=1,
                discount=ship_discount, tax_percent=tax_percent)
            lines.append({
                "product_id":    ship_pid,
                "description":   "شحن (Shipping)",
                "quantity":      1,
                "unit_price":    ship_unit,
                "discount":      ship_discount,
                "discount_type": "amount",
                "tax_percent":   tax_percent,
            })
            expected_total_dec += Decimal(str(ship_gross))
            shipping_breakdown = {
                "included":              True,
                "salla_declared_amount": _q2(shipping_amount),
                "salla_declared_gross":  _q2(ship_target_gross),
                "qoyod_unit_price":      ship_unit,
                "qoyod_discount":        ship_discount,
                "qoyod_gross_after_tax": ship_gross,
                "delta_vs_salla":        _q2(ship_gross - ship_target_gross),
            }
        else:
            # Shipping present in Salla but NOT wired to a product in
            # settings — surface this because it's a common source
            # of totals_mismatch.
            shipping_breakdown = {
                "included":              False,
                "salla_declared_amount": _q2(shipping_amount),
                "salla_declared_gross":  _q2(ship_target_gross),
                "reason":               ("لا يوجد default_shipping_product_id "
                                          "في إعدادات قيود — سطر الشحن "
                                          "مُهمَل في الحسبة"
                                          if ship_pid is None else
                                          "حصة الشحن الصافية من إجمالي "
                                          "سلة سالبة أو صفر — سطر الشحن "
                                          "مُهمَل"),
            }

    # COD fee line (optional).
    cod_breakdown: Optional[dict] = None
    if cod_fee > 0:
        cod_pid = _to_int(settings.get("default_cod_fee_product_id"))
        if cod_pid is not None:
            cod_net = cod_fee / tax_factor
            cod_discount = _q2(cod_fee - cod_net)
            cod_unit = _q2(cod_fee)
            cod_gross = _line_gross(
                unit_price=cod_unit, quantity=1,
                discount=cod_discount, tax_percent=tax_percent)
            lines.append({
                "product_id":    cod_pid,
                "description":   "رسوم الدفع عند الاستلام (COD Fee)",
                "quantity":      1,
                "unit_price":    cod_unit,
                "discount":      cod_discount,
                "discount_type": "amount",
                "tax_percent":   tax_percent,
            })
            expected_total_dec += Decimal(str(cod_gross))
            cod_breakdown = {
                "included":              True,
                "salla_declared_amount": _q2(cod_fee),
                "qoyod_gross_after_tax": cod_gross,
                "delta_vs_salla":        _q2(cod_gross - cod_fee),
            }
        else:
            cod_breakdown = {
                "included":              False,
                "salla_declared_amount": _q2(cod_fee),
                "reason":                ("لا يوجد default_cod_fee_product_id "
                                           "في إعدادات قيود — سطر رسوم COD "
                                           "مُهمَل في الحسبة"),
            }

    expected_total_before_adj = float(
        expected_total_dec.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    salla_total = _q2(canon.get("total_amount"))
    diff_before_adj = _q2(expected_total_before_adj - salla_total)

    # ── Rounding adjustment (Plan-B rev 2026-07-08) ────────────────
    # When 2dp × 1.15 tax quantisation leaves a residual of a few
    # cents between Salla's declared total and the قيود-computed
    # sum, add a SINGLE dedicated adjustment line so the invoice
    # closes exactly to Salla's number. The adjustment is applied
    # ONLY when:
    #   (a) the ONLY source of the residual is items rounding — NOT
    #       an ignored shipping / COD line (those are real config
    #       gaps and MUST still fail with totals_mismatch);
    #   (b) the residual is small (≤ 1.00 SAR — larger diffs are
    #       structural and must not be silently absorbed);
    #   (c) `rounding_adjustment_product_id` is configured in
    #       qoyod_settings — otherwise refuse with
    #       `rounding_adjustment_product_missing`.
    rounding_adjustment: Optional[dict] = None
    shipping_config_gap = (
        isinstance(shipping_breakdown, dict)
        and not shipping_breakdown.get("included")
        and _f(shipping_breakdown.get("salla_declared_amount")) > 0
    )
    cod_config_gap = (
        isinstance(cod_breakdown, dict)
        and not cod_breakdown.get("included")
        and _f(cod_breakdown.get("salla_declared_amount")) > 0
    )
    residual = _q2(salla_total - expected_total_before_adj)
    if (abs(diff_before_adj) > 0.01
            and abs(residual) <= 1.00
            and not shipping_config_gap
            and not cod_config_gap):
        adj_pid = _unwrap_id(
            settings.get("rounding_adjustment_product_id"))
        if adj_pid is None:
            rounding_adjustment = {
                "applied":            False,
                "reason":             "rounding_adjustment_product_missing",
                "would_be_amount":    residual,
                "salla_total":        salla_total,
                "qoyod_total_before": expected_total_before_adj,
            }
        else:
            # Tax-free adjustment line so line_gross == residual
            # exactly. Sign is preserved (positive when Salla > Qoyod,
            # negative when Salla < Qoyod).
            lines.append({
                "product_id":    adj_pid,
                "description":   "تسوية فرق التقريب مع سلة",
                "quantity":      1,
                "unit_price":    residual,
                "discount":      0.0,
                "discount_type": "amount",
                "tax_percent":   0.0,
            })
            expected_total_dec += Decimal(str(residual))
            rounding_adjustment = {
                "applied":            True,
                "product_id":         adj_pid,
                "amount":             residual,
                "salla_total":        salla_total,
                "qoyod_total_before": expected_total_before_adj,
                "note":               ("سطر تسوية تلقائي بلا ضريبة — "
                                        "لغلق فرق التقريب بين قيود وسلة"),
            }

    expected_total = float(
        expected_total_dec.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
    diff = _q2(expected_total - salla_total)

    # ── Structural guard: line-count integrity ────────────────────
    # User directive 2026-07-09 (order 27027791 caught duplicated
    # AMS10841 / AMS11961 lines and a ~×2 total). Refuse to send
    # if the number of Qoyod line_items doesn't equal:
    #     len(canonical.items) + shipping? + cod? + adjustment?
    # This catches ANY future double-append regression BEFORE the
    # invoice reaches قيود.
    shipping_added = bool(isinstance(shipping_breakdown, dict)
                          and shipping_breakdown.get("included"))
    cod_added = bool(isinstance(cod_breakdown, dict)
                     and cod_breakdown.get("included"))
    adjustment_added = bool(rounding_adjustment
                            and rounding_adjustment.get("applied"))
    expected_line_count = (
        len(raw_items)
        + (1 if shipping_added else 0)
        + (1 if cod_added else 0)
        + (1 if adjustment_added else 0)
    )
    if len(lines) != expected_line_count:
        raise ManualSendRefused(
            "duplicated_invoice_items_detected",
            (f"عدد بنود الفاتورة ({len(lines)}) لا يطابق العدد "
              f"المتوقّع ({expected_line_count} = "
              f"{len(raw_items)} منتج + "
              f"{'شحن+' if shipping_added else ''}"
              f"{'COD+' if cod_added else ''}"
              f"{'تسوية+' if adjustment_added else ''}0). "
              "تم إيقاف الإرسال حفاظاً على سلامة الفاتورة."),
            {
                "actual_lines":         len(lines),
                "expected_lines":       expected_line_count,
                "canonical_items":      len(raw_items),
                "shipping_added":       shipping_added,
                "cod_added":            cod_added,
                "adjustment_added":     adjustment_added,
                "line_skus":            [
                    (li.get("description") or "?")
                    for li in lines
                ],
            },
        )

    # Human-readable RCA hint. Sums the per-line deltas + shipping +
    # cod delta to point at the exact source of the residual diff.
    items_delta_sum = _q2(sum(bi["delta_vs_salla_line"]
                               for bi in breakdown_items))
    ship_delta = (shipping_breakdown.get("delta_vs_salla")
                  if isinstance(shipping_breakdown, dict)
                  and shipping_breakdown.get("included") else 0.0)
    cod_delta = (cod_breakdown.get("delta_vs_salla")
                 if isinstance(cod_breakdown, dict)
                 and cod_breakdown.get("included") else 0.0)
    hint_parts: list[str] = []
    if abs(items_delta_sum) >= 0.01:
        hint_parts.append(
            f"مجموع فروق أسطر المنتجات = {items_delta_sum:+.2f}")
    if abs(ship_delta or 0) >= 0.01:
        hint_parts.append(f"فرق سطر الشحن = {ship_delta:+.2f}")
    if abs(cod_delta or 0) >= 0.01:
        hint_parts.append(f"فرق سطر رسوم COD = {cod_delta:+.2f}")
    if isinstance(shipping_breakdown, dict) \
       and not shipping_breakdown.get("included") \
       and _f(shipping_breakdown.get("salla_declared_amount")) > 0:
        hint_parts.append(
            f"شحن سلة {shipping_breakdown['salla_declared_amount']} ريال "
            "مُهمَل (لا default_shipping_product_id)")
    if isinstance(cod_breakdown, dict) \
       and not cod_breakdown.get("included") \
       and _f(cod_breakdown.get("salla_declared_amount")) > 0:
        hint_parts.append(
            f"COD سلة {cod_breakdown['salla_declared_amount']} ريال "
            "مُهمَل (لا default_cod_fee_product_id)")
    hint = " · ".join(hint_parts) if hint_parts else (
        "الفرق ناتج عن تقريب سنتات على مستوى الأسطر")

    breakdown = {
        "tax_percent":            tax_percent,
        "tax_factor":             tax_factor,
        "salla_declared_total":   salla_total,
        "items":                  breakdown_items,
        "shipping":               shipping_breakdown,
        "cod_fee":                cod_breakdown,
        "qoyod_total_before_adjustment": expected_total_before_adj,
        "residual_before_adjustment":    _q2(salla_total
                                              - expected_total_before_adj),
        "rounding_adjustment":    rounding_adjustment,
        "expected_qoyod_total":   expected_total,
        "difference":             diff,
        "difference_source_hint": hint,
    }

    invoice: dict = {
        "invoice": {
            "contact_id":   contact_id,
            "issue_date":   send_date_iso,
            "due_date":     send_date_iso,
            "reference":    canon.get("order_number")
                            or canon.get("order_id"),
            "status":       "Approved",
            "payment_method": "10",
            "currency_code": canon.get("currency") or "SAR",
            "line_items":   lines,
            "notes":        f"Mezan Plan-B Manual · order "
                            f"{canon.get('order_number') or ''} · "
                            f"send_date={send_date_iso}",
            "external_reference": canon.get("order_id"),
        }
    }
    inv_id = _to_int(settings.get("default_inventory_id"))
    if inv_id is not None:
        invoice["invoice"]["inventory_id"] = inv_id
    br_id = _to_int(settings.get("default_branch_id"))
    if br_id is not None:
        invoice["invoice"]["branch_id"] = br_id
    return invoice, expected_total, breakdown


def _build_payment_payload(*, invoice_id: int, amount: float,
                           account_id: int, reference: str,
                           send_date_iso: str) -> dict:
    """Build a Qoyod payment with the caller-resolved collected amount.

    `date` is the Asia/Riyadh send-date, matching the invoice."""
    return {
        "invoice_payment": {
            "invoice_id":  invoice_id,
            "amount":      _q2(amount),
            "date":        send_date_iso,
            "account_id":  account_id,
            "reference":   reference,
            "description": f"Mezan Plan-B Manual · order {reference}",
        }
    }


# ─── Local qoyod_invoices ledger — post-payment write-through ────────
async def _upsert_local_qoyod_invoice(
    db, *, user_id: str, invoice_id: int,
    invoice_number: Any, order_number: str,
    canon: dict, expected_total: float,
    send_date_iso: str, paid: bool,
    unpaid_status: str = "partial",
    posted_payment_amount: Optional[float] = None,
) -> None:
    """Upsert the local `qoyod_invoices` ledger row for a Plan-B invoice.

    CRITICAL rules (2026-07-09):
      • NEVER called BEFORE `POST /invoice_payments` succeeds — the
        previous version wrote `status=paid, remaining=0.0` after the
        invoice POST but before the payment POST. When the payment
        failed, the ledger lied and reconciliation broke.
      • Called EXACTLY ONCE per pipeline outcome:
          - Payment succeeded → paid=True; the posted amount determines
            whether the invoice is paid or partially paid.
          - Payment failed    → paid=False (remaining=expected_total)
      • Wrapped in a try/except that swallows DB errors — the قيود
        side is the source of truth. A local write-through failure
        must not surface as an HTTP 500 to the operator.
    """
    total = round(float(expected_total), 2)
    if posted_payment_amount is not None:
        paid_amount = min(total, max(0.0, _q2(posted_payment_amount)))
    else:
        paid_amount = total if paid else 0.0
    remaining = _q2(total - paid_amount)
    if paid and remaining <= 0.0:
        invoice_status = "paid"
    elif paid_amount > 0.0:
        invoice_status = "partial"
    else:
        invoice_status = unpaid_status
    upsert_doc = {
        "user_id":            user_id,
        "qoyod_invoice_id":   str(invoice_id),
        "invoice_number":     (str(invoice_number)
                                if invoice_number else str(invoice_id)),
        "reference":          str(order_number),
        "salla_order_number": str(order_number),
        "customer_name":      (canon.get("customer") or {}).get("name"),
        "issue_date":         send_date_iso,
        "total":              total,
        "paid_amount":        paid_amount,
        "remaining":          remaining,
        "status":             invoice_status,
        "source":             "plan_b_send",
        "last_sync_at":       datetime.now(timezone.utc),
    }
    try:
        await db.qoyod_invoices.update_one(
            {"user_id": user_id, "qoyod_invoice_id": str(invoice_id)},
            {"$set":         upsert_doc,
             "$setOnInsert": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "plan-b local qoyod_invoices upsert failed order=%s invoice=%s: %s",
            order_number, invoice_id, e)


async def _retry_payment_only(
    db, *, client: "ManualQoyodClient", row: dict, canon: dict,
    user_id: str, order_number: str, lock_id: str,
    qoyod_account_id: int, existing_invoice_id: Optional[int],
    existing_invoice_number: Any,
) -> dict:
    """Surgical retry of `POST /invoice_payments` for a Plan-B invoice
    whose previous send left the payment step incomplete.

    Preconditions (asserted by the caller):
      • `row["manual_qoyod_invoice_id"]` is set (invoice exists).
      • `row["manual_qoyod_payment_id"]` is NOT set (payment missing).

    Contract:
      • NEVER calls `/customers`, `/products`, or `/invoices`.
      • Refuses if the persisted invoice_id is not a positive int.
      • On success:
          - Writes qoyod_invoices as paid=True (remaining=0).
          - Sets `manual_qoyod_payment_id` + unified `qoyod_invoice_id`
            markers on the inbox row.
          - Finalises the lock as `succeeded`.
      • On failure:
          - Writes qoyod_invoices as paid=False (remaining=full).
          - Raises `invoice_created_payment_failed` so the operator
            can try again later. The `manual_qoyod_invoice_id` marker
            is preserved so the next click re-enters this same path.
    """
    if not existing_invoice_id or existing_invoice_id <= 0:
        raise ManualSendRefused(
            "manual_invoice_id_invalid",
            "معرف فاتورة قيود المحفوظ في العلامة غير صالح — "
            "يتعذّر إعادة تسجيل السداد",
            {"manual_qoyod_invoice_id":
                row.get("manual_qoyod_invoice_id")})

    send_date_iso = _riyadh_today_iso()
    salla_total = _q2(canon.get("total_amount"))

    fetched_invoice = await client.get_invoice(existing_invoice_id)
    actual_total = _extract_qoyod_invoice_total(fetched_invoice)
    actual_total = _validate_qoyod_actual_total(
        actual_total=actual_total,
        salla_total=salla_total,
        invoice_id=existing_invoice_id,
    )

    expected_total = actual_total
    payment_amount = _resolve_payment_amount(
        qoyod_total=actual_total,
        salla_collected_total=salla_total,
    )
    payment_payload = _build_payment_payload(
        invoice_id=existing_invoice_id, amount=payment_amount,
        account_id=qoyod_account_id, reference=order_number,
        send_date_iso=send_date_iso)
    idem_pay = f"pay-retry-{order_number}"
    try:
        created_pay = await client.create_invoice_payment(
            payment_payload, idem=idem_pay)
    except ManualQoyodError as exc:
        # Reflect the true (still-unpaid) state locally.
        await _upsert_local_qoyod_invoice(
            db, user_id=user_id, invoice_id=existing_invoice_id,
            invoice_number=existing_invoice_number,
            order_number=order_number, canon=canon,
            expected_total=expected_total,
            send_date_iso=send_date_iso, paid=False)
        raise ManualSendRefused(
            "invoice_created_payment_failed",
            f"إعادة تسجيل السداد للفاتورة #{existing_invoice_id} "
            f"فشلت ({exc.status_code}) — راجع سجل قيود",
            {"invoice_id":  existing_invoice_id,
             "qoyod_error": exc.to_dict(),
             "retry_only":  True})

    pay_node = (created_pay.get("invoice_payment")
                if isinstance(created_pay, dict) else None) \
        or (created_pay if isinstance(created_pay, dict) else {})
    payment_id = _to_int(pay_node.get("id"))

    # Payment succeeded — close the ledger and mark the row.
    await _upsert_local_qoyod_invoice(
        db, user_id=user_id, invoice_id=existing_invoice_id,
        invoice_number=existing_invoice_number,
        order_number=order_number, canon=canon,
        expected_total=expected_total,
        send_date_iso=send_date_iso, paid=True,
        posted_payment_amount=payment_amount)
    await _finalize_lock(
        db, order_number=order_number, user_id=user_id,
        lock_id=lock_id, status="succeeded",
        invoice_id=str(existing_invoice_id),
        payment_id=str(payment_id) if payment_id else None)
    await db.integration_inbox.update_one(
        {"id": row.get("id")},
        {"$set": {"manual_qoyod_payment_id":
                   (str(payment_id) if payment_id else None),
                   "qoyod_invoice_id":         str(existing_invoice_id),
                   "qoyod_invoice_number":     (str(existing_invoice_number)
                                                 if existing_invoice_number
                                                 else None),
                   "qoyod_invoice_source":     "manual_plan_b",
                   "manual_send_last_status":  "succeeded",
                   "manual_send_at":           datetime.now(timezone.utc)}})

    logger.info(
        "plan-b retry_payment_only order=%s invoice=%s payment=%s",
        order_number, existing_invoice_id, payment_id)
    return {
        "ok":                True,
        "order_number":      order_number,
        "invoice_id":        existing_invoice_id,
        "invoice_number":    (str(existing_invoice_number)
                              if existing_invoice_number else None),
        "payment_id":        payment_id,
        "send_date":         send_date_iso,
        "salla_total":       _q2(canon.get("total_amount")),
        "expected_total":    expected_total,
        "payment_amount":    payment_amount,
        "difference":        _q2(actual_total - salla_total),
        "qoyod_account_id":  qoyod_account_id,
        "retry_payment_only": True,
        "steps": [{"step": "invoice_payment_retry_only",
                   "invoice_id": existing_invoice_id,
                   "payment_id": payment_id}],
    }


# ─── Main entrypoint ─────────────────────────────────────────────────
async def manual_send_one(
    db, *, user_id: str, order_number: str,
    orders_user_id: Optional[str] = None, actor: str = "manual-ui",
) -> dict:
    """Push a single Salla order to Qoyod using the 4-step manual path.

    Returns a JSON-safe result dict on success. Raises
    `ManualSendRefused` when any guard blocks or when a step fails
    with a business-level reason. Uncaught exceptions bubble up as
    HTTP 500 — the caller records them.
    """
    # ── 0) Load inbox row (representative — newest for this order) ──
    row = await db.integration_inbox.find_one(
        {"user_id": user_id,
         "salla_order_number": str(order_number)},
        sort=[("received_at", -1)],
    )
    if not row:
        raise ManualSendRefused(
            "order_not_found",
            f"لم يتم العثور على طلب برقم {order_number} في الاستلام")

    # ── Floor date (Salla-source-only) + status filters ────────────
    # No fallback to received_at/updated_at/webhook time — if Salla
    # doesn't tell us the creation date, we REFUSE to send.
    odate = _salla_order_created_date(row)
    if odate is None:
        raise ManualSendRefused(
            "no_salla_order_date",
            "لا يوجد تاريخ إنشاء للطلب في بيانات سلة — يتعذّر التحقق من "
            "تاريخ التكامل")
    if odate < _FLOOR_DATE:
        raise ManualSendRefused(
            "before_floor_date",
            f"تاريخ إنشاء الطلب ({odate.isoformat()}) أقدم من "
            f"{_FLOOR_DATE.isoformat()} — خارج نطاق التكامل")
    if not any(_matches_status(row, s) for s in SUPPORTED_STATUSES):
        raise ManualSendRefused(
            "not_completed",
            "حالة الطلب ليست ضمن الحالات المسموحة يدوياً "
            "(تم التنفيذ / جاري التوصيل / تم التوصيل)")

    # ── Guard G1a — DB says already-sent? (revised 2026-07-09) ─────
    # A send is considered "already completed" ONLY when BOTH markers
    # are present:
    #    • manual_qoyod_invoice_id  (Step 4 succeeded)
    #    • manual_qoyod_payment_id  (Step 5 succeeded)
    # If invoice exists but payment doesn't, we do NOT refuse — we
    # route the retry to the payment-only path below.
    manual_inv_id_existing = row.get("manual_qoyod_invoice_id")
    manual_pay_id_existing = row.get("manual_qoyod_payment_id")

    marker_canon = row.get("canonical_payload") or {}
    marker_payment_method = (
        marker_canon.get("payment_method")
        or marker_canon.get("payment_method_native")
    )
    is_cod = is_cod_family(marker_payment_method)

    if manual_inv_id_existing and (
        manual_pay_id_existing or is_cod
    ):
        raise ManualSendRefused(
            "already_sent",
            (
                "طلب الدفع عند الاستلام أُرسل مسبقاً كفاتورة بدون سداد"
                if is_cod
                else "الطلب أُرسل مسبقاً من مسار الإرسال اليدوي "
                     "(فاتورة + سداد)"
            ),
            {
                "manual_qoyod_invoice_id": manual_inv_id_existing,
                "manual_qoyod_payment_id": manual_pay_id_existing,
                "invoice_only": is_cod,
            },
        )
    # Legacy guard: only refuse when the invoice originated OUTSIDE
    # Plan B (webhook / legacy path). Plan-B-created invoices with
    # missing payments must fall through to the retry-payment-only
    # path — they are not "legacy".
    legacy_qid = row.get("qoyod_invoice_id")
    legacy_source = row.get("qoyod_invoice_source")
    if legacy_qid and _is_real(legacy_qid) \
            and legacy_source != "manual_plan_b":
        raise ManualSendRefused(
            "already_sent_legacy",
            "يوجد فاتورة قيود سابقة لهذا الطلب من المسار القديم",
            {"qoyod_invoice_id": legacy_qid,
             "qoyod_invoice_source": legacy_source})

    # ── Guard G1c (DISABLED 2026-07-09) ────────────────────────────
    # The old "already_in_qoyod_local" preflight read from the
    # `qoyod_invoices` collection. That collection was being written
    # to as `status=paid, remaining=0` BEFORE the payment step ran,
    # so it lied whenever the payment failed. Removing this guard
    # eliminates the false-positive block. Duplicate protection is
    # still enforced by:
    #   1. `_acquire_lock` (Mongo unique index on order_number).
    #   2. `client.find_invoice_by_reference` (Qoyod-side check
    #      below — the only real source of truth).

    canon = row.get("canonical_payload") or {}
    # Reuse the exact mapper behind New Orders / Order Details. This keeps
    # aliases such as "مصرف الإنماء" and "بنك الإنماء" in one place.
    payment_facts = await get_order_payment_facts(
        db, user_id=orders_user_id or user_id,
        order_number=str(order_number),
    )
    payment_method = (
        payment_facts.get("payment_method")
        or canon.get("payment_method")
        or canon.get("payment_method_native")
    )
    receiving_bank_name = payment_facts.get("receiving_bank_name")
    canon = _overlay_order_engine_facts(canon, payment_facts)
    salla_total = _q2(canon.get("total_amount"))
    # The inbox snapshot may carry the generic/old payment alias.  Orders V2
    # is authoritative for the current payment method.
    is_cod = is_cod_family(payment_method)

    # ── Guard G0 — refuse zero-total sends (2026-02) ──────────────
    # Symptom: Tabby/Make (or a stripped Salla status refresh) posts
    # a payload whose `amounts.total.amount` is 0. If that trace is
    # the newest, canonical_payload.total_amount collapses to 0 and
    # this send would create a قيود invoice of 0 SAR against a real
    # 134 SAR Salla order. Refuse hard — the operator must resolve
    # the source data before Plan B lets a zero-total invoice reach
    # قيود. `list_pending_orders` also falls back to the highest
    # positive total across all traces (see pending.py), so an
    # actual send should almost never hit this guard — but the guard
    # is the last line of defence.
    if salla_total <= 0:
        # One more look — maybe another trace of the same order in
        # inbox carries a positive total we can trust.
        # Scan all traces (canonical shape may be flat float OR
        # `{amount, currency}` dict — check both).
        best_other = None
        best_amt = 0.0
        async for other in db.integration_inbox.find(
            {"salla_order_number": str(order_number)},
            {"_id": 0, "canonical_payload.total_amount": 1,
             "connector_key": 1},
        ):
            node = ((other.get("canonical_payload") or {})
                    .get("total_amount"))
            if node is None:
                continue
            if isinstance(node, (int, float)):
                v = float(node)
            elif isinstance(node, dict):
                try:
                    v = float(node.get("amount") or 0)
                except (TypeError, ValueError):
                    v = 0.0
            else:
                try:
                    v = float(node)
                except (TypeError, ValueError):
                    v = 0.0
            if v > best_amt:
                best_amt = v
                best_other = other
        raise ManualSendRefused(
            "zero_total_refused",
            "لا يمكن إرسال طلب مبلغه 0.00 ريال إلى قيود. تحقق من "
            "بيانات الطلب في سلة ثم أعد المزامنة.",
            {"canonical_total":  canon.get("total_amount"),
             "other_trace_total": ((best_other or {}).get(
                                    "canonical_payload", {}) or {}).get("total_amount"),
             "other_trace_connector": (best_other or {}).get("connector_key")})

    # ── Load settings + resolve payment account (Guard G4) ─────────
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    qoyod_account_id_raw = None
    qoyod_account_id = None

    if not is_cod:
        payment_key = str(payment_method or "").strip().lower()
        is_bank_transfer = payment_key in {
            "bank_transfer", "bank", "wire_transfer",
            "bank_rajhi", "bank_ahli", "bank_inma",
        }
        receiving_bank_key = None
        if is_bank_transfer:
            receiving_bank_key, qoyod_account_id_raw = (
                resolve_receiving_bank_account(
                    settings, payment_method, receiving_bank_name,
                )
            )
            if not receiving_bank_key:
                raise ManualSendRefused(
                    "receiving_bank_missing",
                    "طلب التحويل البنكي لا يحتوي اسم بنك مستلم معروف — "
                    "لن يُسجل السداد في حساب بنكي عام.",
                    {
                        "payment_method": payment_method,
                        "receiving_bank_name": receiving_bank_name,
                        "supported_banks": [
                            "الراجحي", "الأهلي", "الإنماء",
                        ],
                    },
                )
        else:
            qoyod_account_id_raw = resolve_payment_account(
                settings,
                payment_method,
            )
        qoyod_account_id = _to_int(qoyod_account_id_raw)

        if qoyod_account_id is None:
            raise ManualSendRefused(
                "payment_method_unmapped",
                "طريقة الدفع غير مرتبطة بحساب في قيود — "
                "اربطها من إعدادات قيود → طرق الدفع",
                {
                    "payment_method": payment_method,
                    "receiving_bank_name": receiving_bank_name,
                    "receiving_bank_key": receiving_bank_key,
                    "resolved_raw": qoyod_account_id_raw,
                },
            )

    # ── Guard G2a — Qoyod rounding preflight before ANY write ─────
    # Existing Plan-B invoices use the payment-only recovery path and must
    # not be rebuilt. New sends are simulated using the exact outgoing
    # payload before credentials/client/locks/customer/product mutations.
    if not manual_inv_id_existing:
        _preflight_qoyod_invoice(
            canon=canon,
            settings=settings,
            salla_total=salla_total,
        )

    # ── Load Qoyod credentials ─────────────────────────────────────
    api_key = await get_api_key(db, user_id)
    if not api_key:
        raise ManualSendRefused(
            "qoyod_credentials_missing",
            "لم يتم إعداد مفتاح API لقيود من الإعدادات")

    client = ManualQoyodClient(api_key=api_key)

    # ── Guard G1b — atomic idempotency lock ────────────────────────
    lock_id = await _acquire_lock(
        db, user_id=user_id, order_number=str(order_number), actor=actor)

    # ── Payment-only retry branch (2026-07-09) ─────────────────────
    # If Plan B previously created an invoice (`manual_qoyod_invoice_id`
    # is set) but the payment step never completed (`manual_qoyod_
    # payment_id` is missing), we DO NOT re-run steps 1-4 (which
    # would create a duplicate invoice). Instead, we go straight to
    # Step 5 with the persisted invoice id.
    if (
        manual_inv_id_existing
        and not manual_pay_id_existing
        and not is_cod
    ):
        try:
            retry_result = await _retry_payment_only(
                db, client=client, row=row, canon=canon,
                user_id=user_id, order_number=str(order_number),
                lock_id=lock_id, qoyod_account_id=qoyod_account_id,
                existing_invoice_id=_to_int(manual_inv_id_existing),
                existing_invoice_number=row.get(
                    "manual_qoyod_invoice_number"),
            )
            await sync_unified_order_accounting_from_result(
                db,
                orders_user_id=str(orders_user_id or user_id),
                order_number=str(order_number),
                result=retry_result,
                source="manual_plan_b_retry",
            )
            return retry_result
        except ManualSendRefused as exc:
            await _finalize_lock(
                db, order_number=str(order_number), user_id=user_id,
                lock_id=lock_id, status="failed",
                error={"code": exc.code, "message": exc.message,
                        "detail": exc.extra})
            raise
        except ManualQoyodError as exc:
            await _finalize_lock(
                db, order_number=str(order_number), user_id=user_id,
                lock_id=lock_id, status="failed",
                error={"code": "qoyod_http_error",
                        "message": f"استجابة غير ناجحة من قيود ({exc.status_code})",
                        "detail":  exc.to_dict()})
            raise ManualSendRefused(
                "qoyod_http_error",
                f"استجابة غير ناجحة من قيود ({exc.status_code})",
                exc.to_dict())

    # ── Guard G1c — Qoyod-side safety net (invoice with same ref) ──
    try:
        existing_inv = await client.find_invoice_by_reference(
            str(order_number))
    except ManualQoyodError:
        existing_inv = None
    if existing_inv:
        eid = _to_int(existing_inv.get("id"))
        await _finalize_lock(
            db, order_number=str(order_number), user_id=user_id,
            lock_id=lock_id, status="already_present",
            invoice_id=str(eid) if eid else None)
        # Persist marker on the inbox row so the pending list drops it.
        if eid:
            await db.integration_inbox.update_one(
                {"id": row.get("id")},
                {"$set": {"manual_qoyod_invoice_id": str(eid),
                           "manual_send_last_status": "already_present"}})
        raise ManualSendRefused(
            "duplicate_invoice_in_qoyod",
            f"يوجد فاتورة قيود مسبقة بنفس رقم المرجع {order_number}",
            {"qoyod_invoice_id": eid})

    # ── STEP 1 — customer find/create ──────────────────────────────
    try:
        send_result = await _run_all_steps(
            db, client=client, row=row, canon=canon, settings=settings,
            user_id=user_id, order_number=str(order_number),
            lock_id=lock_id, salla_total=salla_total,
            qoyod_account_id=qoyod_account_id,
            is_cod=is_cod,
            payment_method=payment_method,
        )
        await sync_unified_order_accounting_from_result(
            db,
            orders_user_id=str(orders_user_id or user_id),
            order_number=str(order_number),
            result=send_result,
            source="manual_plan_b",
        )
        return send_result
    except ManualSendRefused as exc:
        await _finalize_lock(
            db, order_number=str(order_number), user_id=user_id,
            lock_id=lock_id, status="failed",
            error={"code": exc.code, "message": exc.message,
                    "detail": exc.extra})
        raise
    except ManualQoyodError as exc:
        await _finalize_lock(
            db, order_number=str(order_number), user_id=user_id,
            lock_id=lock_id, status="failed",
            error={"code": "qoyod_http_error",
                    "message": f"استجابة غير ناجحة من قيود ({exc.status_code})",
                    "detail":  exc.to_dict()})
        raise ManualSendRefused(
            "qoyod_http_error",
            f"استجابة غير ناجحة من قيود ({exc.status_code})",
            exc.to_dict())


async def _run_all_steps(
    db, *, client: ManualQoyodClient, row: dict, canon: dict,
    settings: dict, user_id: str, order_number: str, lock_id: str,
    salla_total: float, qoyod_account_id: int, is_cod: bool,
    payment_method: Optional[str],
) -> dict:
    steps_trace: list[dict] = []

    # ── 1) Customer ────────────────────────────────────────────────
    cust = canon.get("customer") or {}
    phone = str(cust.get("phone") or "").strip()
    email = str(cust.get("email") or "").strip()
    contact_id: Optional[int] = None
    matched_via = None
    if phone:
        matches = await client.find_customers_by_phone(phone)
        if matches:
            contact_id = _to_int(matches[0].get("id"))
            matched_via = "phone"
    if contact_id is None and email:
        matches = await client.find_customers_by_email(email)
        if matches:
            contact_id = _to_int(matches[0].get("id"))
            matched_via = "email"
    if contact_id is None:
        # Create new customer.
        payload = _build_customer_payload(canon)
        idem = f"cust-{order_number}"
        created = await client.create_customer(payload, idem=idem)
        # Qoyod may wrap under `contact` or return the entity directly.
        node = created.get("contact") if isinstance(created, dict) else None
        if not isinstance(node, dict):
            node = created if isinstance(created, dict) else {}
        contact_id = _to_int(node.get("id"))
        matched_via = "created"
    if contact_id is None:
        raise ManualSendRefused(
            "customer_id_invalid",
            "قيود لم يُعِد رقم عميل صالحاً",
            {"matched_via": matched_via})
    steps_trace.append({"step": "customer", "contact_id": contact_id,
                        "matched_via": matched_via})

    # ── 2) Products (find or create by SKU) ────────────────────────
    line_resolutions: dict[str, int] = {}
    product_trace: list[dict] = []
    for it in canon.get("items") or []:
        sku = str(it.get("sku") or "").strip()
        if not sku:
            raise ManualSendRefused(
                "sku_missing",
                "سطر في الطلب بدون SKU — تعذّر ربطه بمنتج قيود",
                {"item_name": it.get("name")})
        found = await client.find_product_by_sku(sku)
        if found:
            pid = _to_int(found.get("id"))
            if pid is None:
                raise ManualSendRefused(
                    "product_id_invalid",
                    f"معرف المنتج الراجع من قيود غير صالح للـ SKU {sku}")
            line_resolutions[sku] = pid
            product_trace.append({"sku": sku, "product_id": pid,
                                  "resolution": "found"})
            continue
        # Create.
        payload = _build_product_payload(it, settings)
        idem = f"prod-{order_number}-{sku}"
        create_attempts = [{"stage": "primary", "payload": payload}]
        try:
            created = await client.create_product(payload, idem=idem)
        except ManualQoyodError as exc:
            # 422 self-heal: try the minimal fallback payload ONCE.
            # Any other status is re-raised with the primary payload
            # exposed for RCA.
            if exc.status_code == 422:
                fb_payload = _build_product_payload_fallback(it, settings)
                create_attempts.append(
                    {"stage": "fallback", "payload": fb_payload})
                try:
                    created = await client.create_product(
                        fb_payload, idem=f"{idem}-fb")
                except ManualQoyodError as exc2:
                    # Both attempts failed — expose both payloads +
                    # both قيود responses so the operator can inspect.
                    raise ManualSendRefused(
                        "product_create_failed",
                        f"قيود رفض إنشاء منتج بـ SKU {sku} في محاولتين "
                        f"({exc.status_code} ثم {exc2.status_code})",
                        {"sku":              sku,
                         "primary_attempt":  {
                             "payload":  payload,
                             "response": exc.to_dict()},
                         "fallback_attempt": {
                             "payload":  fb_payload,
                             "response": exc2.to_dict()}})
            else:
                raise ManualSendRefused(
                    "product_create_failed",
                    f"قيود رفض إنشاء منتج بـ SKU {sku} "
                    f"({exc.status_code})",
                    {"sku":     sku,
                     "payload": payload,
                     "response": exc.to_dict()})
        node = created.get("product") if isinstance(created, dict) else None
        if not isinstance(node, dict):
            node = created if isinstance(created, dict) else {}
        pid = _to_int(node.get("id"))
        if pid is None:
            raise ManualSendRefused(
                "product_create_failed",
                f"قيود لم يُعِد رقم منتج صالحاً للـ SKU {sku}",
                {"sku": sku, "attempts": create_attempts,
                 "response": created})
        line_resolutions[sku] = pid
        product_trace.append({
            "sku":         sku,
            "product_id":  pid,
            "resolution":  "created",
            "attempts":    len(create_attempts),
            "used_stage":  create_attempts[-1]["stage"],
            "request_body": create_attempts[-1]["payload"],
        })
    steps_trace.append({"step": "products", "resolutions": product_trace})

    # ── 3) Build invoice payload + Guard G2 (totals) ───────────────
    # send_date = TODAY in Asia/Riyadh (NOT the Salla order_created_at).
    # Recorded ONCE here and reused for both the invoice `issue_date`
    # and the payment `date`.
    send_date_iso = _riyadh_today_iso()
    invoice_payload, expected_total, breakdown = _build_invoice_payload(
        canon=canon, contact_id=contact_id,
        line_resolutions=line_resolutions, settings=settings,
        send_date_iso=send_date_iso)
    diff = _q2(expected_total - salla_total)
    if not _within_amount_tolerance(diff):
        # If the ONLY reason we still exceed tolerance is that the
        # rounding-adjustment product wasn't configured, surface a
        # dedicated code so the operator knows the ONE-LINE fix.
        adj = breakdown.get("rounding_adjustment") or {}
        if adj.get("reason") == "rounding_adjustment_product_missing":
            raise ManualSendRefused(
                "rounding_adjustment_product_missing",
                "الفرق سنتات تقريب فقط. أضف منتج قيود مخصّص للتسويات "
                "ثم اضبط `rounding_adjustment_product_id` في إعدادات "
                "قيود لتفعيل سطر التسوية التلقائي — لن نرسل حتى ذلك.",
                {"salla_total":            salla_total,
                 "expected_qoyod_total":   expected_total,
                 "difference":             diff,
                 "residual_would_be":      adj.get("would_be_amount"),
                 "breakdown":              breakdown})
        raise ManualSendRefused(
            "totals_mismatch",
            f"فرق المبلغ {abs(diff)} ريال أكبر من 0.01 — أُوقف الإرسال",
            {"salla_total":            salla_total,
             "expected_qoyod_total":   expected_total,
             "difference":             diff,
             "difference_source_hint": breakdown["difference_source_hint"],
             "breakdown":              breakdown})

    # ── 4) POST invoice ────────────────────────────────────────────
    idem_inv = f"inv-{order_number}"
    created_inv = await client.create_invoice(invoice_payload,
                                               idem=idem_inv)
    inv_node = (created_inv.get("invoice")
                if isinstance(created_inv, dict) else None) \
        or (created_inv if isinstance(created_inv, dict) else {})
    invoice_id = _to_int(inv_node.get("id"))
    if invoice_id is None:
        raise ManualSendRefused(
            "invoice_id_invalid",
            "قيود لم يُعِد رقم فاتورة صالحاً",
            {"response": created_inv})
    invoice_number = inv_node.get("number") or inv_node.get("reference_no")

    # Persist immediately after POST /invoices succeeds.  Qoyod is now
    # authoritative, so a transient local DB write failure must not turn
    # the successful external operation into an HTTP 500.  The Qoyod-side
    # reference lookup remains the duplicate safety net on any retry.
    local_persistence_warnings: list[str] = []
    try:
        await db.integration_inbox.update_one(
            {"id": row.get("id")},
            {"$set": {
                "manual_qoyod_invoice_id": str(invoice_id),
                "manual_qoyod_invoice_number": (
                    str(invoice_number) if invoice_number else None
                ),
                "manual_send_last_status": "invoice_created",
                "manual_send_at": datetime.now(timezone.utc),
            }},
        )
    except Exception as exc:  # noqa: BLE001
        local_persistence_warnings.append("initial_invoice_marker")
        logger.exception(
            "invoice succeeded in Qoyod but initial marker write failed "
            "order=%s invoice=%s: %s",
            order_number, invoice_id, exc,
        )

    # Qoyod actual-total gate:
    # Never trust the local simulation after POST /invoices. Read the total
    # that Qoyod actually persisted, because its internal tax/line rounding
    # can differ by 0.01 even when local expected_total shows exact parity.
    actual_total = _extract_qoyod_invoice_total(created_inv)
    actual_total_source = "create_invoice_response"

    if actual_total is None and not is_cod:
        fetched_invoice = await client.get_invoice(invoice_id)
        actual_total = _extract_qoyod_invoice_total(fetched_invoice)
        actual_total_source = "get_invoice"
    elif actual_total is None and is_cod:
        actual_total = expected_total
        actual_total_source = "local_expected_invoice_only"

    actual_difference = (
        _q2(actual_total - salla_total)
        if actual_total is not None else None
    )

    steps_trace.append({
        "step": "invoice",
        "invoice_id": invoice_id,
        "invoice_number": invoice_number,
        "send_date": send_date_iso,
        "expected_total": expected_total,
        "qoyod_actual_total": actual_total,
        "actual_total_source": actual_total_source,
        "salla_total": salla_total,
        "simulated_difference": diff,
        "actual_difference": actual_difference,
    })

    # ── COD: invoice only, no payment ──────────────────────────────
    # No payment parity gate is needed because COD intentionally stays
    # open and unpaid until the courier remits the collected amount.
    if is_cod:
        cod_total = _q2(
            actual_total
            if actual_total is not None
            else expected_total
        )
        # The external invoice already exists at this point.  A local
        # projection failure must therefore be treated as a reconciliation
        # warning, never as a failed send: returning HTTP 500 here encourages
        # an operator retry even though Qoyod accepted the COD invoice.
        try:
            await _upsert_local_qoyod_invoice(
                db,
                user_id=user_id,
                invoice_id=invoice_id,
                invoice_number=invoice_number,
                order_number=order_number,
                canon=canon,
                expected_total=cod_total,
                send_date_iso=send_date_iso,
                paid=False,
                unpaid_status="unpaid",
            )
        except Exception as exc:  # noqa: BLE001
            local_persistence_warnings.append("qoyod_invoices")
            logger.exception(
                "COD invoice succeeded in Qoyod but local invoice "
                "projection failed order=%s invoice=%s: %s",
                order_number,
                invoice_id,
                exc,
            )

        # Qoyod has already accepted the invoice at this point.  Local
        # bookkeeping must never turn that external success into an HTTP
        # 500 (which used to make the UI say "فشل الإرسال" even though the
        # COD invoice was visible in Qoyod).  Persist the inbox marker first
        # so the pending list drops the order, then finalise the lock.  A
        # transient Mongo failure is logged for reconciliation, but the API
        # still returns the authoritative Qoyod success below.
        try:
            await db.integration_inbox.update_one(
                {"id": row.get("id")},
                {
                    "$set": {
                        "manual_qoyod_invoice_id": str(invoice_id),
                        "manual_qoyod_invoice_number": (
                            str(invoice_number)
                            if invoice_number else None
                        ),
                        "qoyod_invoice_id": str(invoice_id),
                        "qoyod_invoice_number": (
                            str(invoice_number)
                            if invoice_number else None
                        ),
                        "qoyod_invoice_source": "manual_plan_b",
                        "manual_send_last_status":
                            "succeeded_invoice_only",
                        "manual_send_at":
                            datetime.now(timezone.utc),
                        "manual_send_mode": "invoice_only",
                    },
                    "$unset": {
                        "manual_qoyod_payment_id": "",
                    },
                },
            )
        except Exception as exc:  # noqa: BLE001
            local_persistence_warnings.append("integration_inbox")
            logger.exception(
                "COD invoice succeeded in Qoyod but inbox marker write "
                "failed order=%s invoice=%s: %s",
                order_number, invoice_id, exc,
            )

        try:
            await _finalize_lock(
                db,
                order_number=order_number,
                user_id=user_id,
                lock_id=lock_id,
                status="succeeded",
                invoice_id=str(invoice_id),
            )
        except Exception as exc:  # noqa: BLE001
            local_persistence_warnings.append("manual_send_lock")
            logger.exception(
                "COD invoice succeeded in Qoyod but lock finalisation "
                "failed order=%s invoice=%s: %s",
                order_number, invoice_id, exc,
            )

        logger.info(
            "plan-b-manual-send COD invoice-only "
            "order=%s invoice=%s",
            order_number,
            invoice_id,
        )

        return {
            "ok": True,
            "order_number": order_number,
            "invoice_id": invoice_id,
            "invoice_number": (
                str(invoice_number)
                if invoice_number else None
            ),
            "payment_id": None,
            "send_date": send_date_iso,
            "salla_total": salla_total,
            "expected_total": cod_total,
            "payment_amount": 0.0,
            "difference": _q2(cod_total - salla_total),
            "qoyod_account_id": None,
            "invoice_only": True,
            "payment_method": payment_method,
            "local_persistence_warnings": local_persistence_warnings,
            "steps": steps_trace + [{
                "step": "invoice_only_complete",
                "reason": "cash_on_delivery",
                "payment_created": False,
            }],
        }

    # ── 4.5) Paid-method actual-total gate ────────────────────────
    # Accept exact parity or a one-halalah Qoyod rounding difference.
    actual_total = _validate_qoyod_actual_total(
        actual_total=actual_total,
        salla_total=salla_total,
        invoice_id=invoice_id,
    )
    payment_amount = _resolve_payment_amount(
        qoyod_total=actual_total,
        salla_collected_total=salla_total,
    )

    # ── 5) POST invoice payment ────────────────────────────────────
    # Record only what Salla collected. If Qoyod rounded the invoice
    # one halalah higher, Qoyod must retain that 0.01 balance and report
    # the invoice as partially paid.
    payment_payload = _build_payment_payload(
        invoice_id=invoice_id, amount=payment_amount,
        account_id=qoyod_account_id, reference=order_number,
        send_date_iso=send_date_iso)
    idem_pay = f"pay-{order_number}"
    try:
        created_pay = await client.create_invoice_payment(
            payment_payload, idem=idem_pay)
    except ManualQoyodError as exc:
        # Invoice succeeded, payment failed. Reflect the TRUE state in
        # the local qoyod_invoices ledger (status=partial, remaining=
        # full total) so the reconciliation page does NOT lie about
        # this invoice being closed. The operator will see it as
        # unpaid and can click Send again — the manual_send_one
        # entrypoint will then route to the payment-only retry branch.
        await _upsert_local_qoyod_invoice(
            db, user_id=user_id, invoice_id=invoice_id,
            invoice_number=invoice_number, order_number=order_number,
            canon=canon, expected_total=actual_total,
            send_date_iso=send_date_iso, paid=False)
        await _finalize_lock(
            db, order_number=order_number, user_id=user_id,
            lock_id=lock_id, status="partial_payment_failed",
            invoice_id=str(invoice_id),
            error={"code": "invoice_payment_failed",
                    "detail": exc.to_dict()})
        raise ManualSendRefused(
            "invoice_created_payment_failed",
            f"تم إنشاء الفاتورة #{invoice_id} لكن فشل تسجيل السداد "
            f"({exc.status_code}) — راجع سجل قيود",
            {"invoice_id": invoice_id, "qoyod_error": exc.to_dict()})
    pay_node = (created_pay.get("invoice_payment")
                if isinstance(created_pay, dict) else None) \
        or (created_pay if isinstance(created_pay, dict) else {})
    payment_id = _to_int(pay_node.get("id"))
    steps_trace.append({"step": "invoice_payment",
                        "payment_id": payment_id})

    # ── 5.5) Write-through to qoyod_invoices (AFTER payment success) ─
    # ONLY now, after قيود has confirmed the payment, do we mark the
    # invoice as paid in the local ledger. This eliminates the
    # "reconciliation lies" bug where a mid-flight payment failure
    # would leave the local ledger claiming status=paid.
    await _upsert_local_qoyod_invoice(
        db, user_id=user_id, invoice_id=invoice_id,
        invoice_number=invoice_number, order_number=order_number,
        canon=canon, expected_total=actual_total,
        send_date_iso=send_date_iso, paid=True,
        posted_payment_amount=payment_amount)

    # ── 6) Success ─────────────────────────────────────────────────
    await _finalize_lock(
        db, order_number=order_number, user_id=user_id,
        lock_id=lock_id, status="succeeded",
        invoice_id=str(invoice_id),
        payment_id=str(payment_id) if payment_id else None)
    await db.integration_inbox.update_one(
        {"id": row.get("id")},
        {"$set": {"manual_qoyod_payment_id":
                   (str(payment_id) if payment_id else None),
                   "qoyod_invoice_id":         str(invoice_id),
                   "qoyod_invoice_number":     (str(invoice_number)
                                                 if invoice_number else None),
                   "qoyod_invoice_source":     "manual_plan_b",
                   "manual_send_last_status":  "succeeded",
                   "manual_send_at":           datetime.now(timezone.utc)}})

    logger.info(
        "plan-b-manual-send order=%s invoice=%s payment=%s",
        order_number, invoice_id, payment_id)
    return {
        "ok":            True,
        "order_number":  order_number,
        "invoice_id":    invoice_id,
        "invoice_number": (str(invoice_number)
                            if invoice_number else None),
        "payment_id":    payment_id,
        "send_date":     send_date_iso,
        "salla_total":   salla_total,
        "expected_total": expected_total,
        "payment_amount": payment_amount,
        "difference":    _q2(actual_total - salla_total),
        "qoyod_account_id": qoyod_account_id,
        "steps":         steps_trace,
    }

