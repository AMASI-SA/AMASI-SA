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
from datetime import datetime, date, timezone
from typing import Any, Optional

from integrations.qoyod.credentials import get_api_key
from integrations.qoyod.eligible_orders import QOYOD_SYNC_START_DATE
from integrations.qoyod.payment_methods import resolve_payment_account
from integrations.qoyod.unsent_orders import _order_created_date, _is_real
from integrations.qoyod_manual.client import (
    ManualQoyodClient, ManualQoyodError,
)
from integrations.qoyod_manual.pending import _is_completed

logger = logging.getLogger(__name__)

_FLOOR_DATE: date = date.fromisoformat(QOYOD_SYNC_START_DATE)


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
        if status == "succeeded":
            raise ManualSendRefused(
                "already_sent",
                "الطلب أُرسل مسبقاً من مسار الإرسال اليدوي",
                {"lock_id": existing.get("lock_id"),
                 "manual_qoyod_invoice_id": existing.get(
                     "manual_qoyod_invoice_id")})
        if status == "in_progress":
            # Auto-release stale locks after 5 minutes.
            started = existing.get("started_at")
            age_ok = False
            if isinstance(started, datetime):
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


def _build_product_payload(item: dict, settings: dict) -> dict:
    sku = str(item.get("sku") or "").strip()
    name = str(item.get("name") or sku or "منتج").strip()
    price = _f(item.get("unit_price"))
    body: dict = {
        "product": {
            "name_ar":  name,
            "name_en":  name,
            "sku":      sku or None,
            "quantity": 0,
            "buying_price":  price,
            "selling_price": price,
            "product_type":  (settings.get("default_product_type")
                              or "Product"),
            "unit_type":     "unit",
        }
    }
    cat = settings.get("default_product_category_id")
    if cat:
        body["product"]["category_id"] = _to_int(cat)
    tax = settings.get("default_product_tax_id")
    if tax:
        body["product"]["tax_id"] = _to_int(tax)
    sales = settings.get("default_sales_account_id")
    if sales:
        body["product"]["sales_account_id"] = _to_int(sales)
    unit_type = settings.get("default_product_unit_type_id")
    if unit_type:
        body["product"]["unit_type_id"] = _to_int(unit_type)
    return body


def _build_invoice_payload(*, canon: dict, contact_id: int,
                           line_resolutions: dict,
                           settings: dict) -> tuple[dict, float]:
    """Return (payload, expected_total). Uses the same
    `match_salla_total` math as the frozen `invoice_builder` but in a
    self-contained, minimal form so we depend on nothing that could
    change under our feet."""
    try:
        tax_percent = float(settings.get("qoyod_tax_percent") or 15)
    except (TypeError, ValueError):
        tax_percent = 15.0
    tax_factor = 1.0 + tax_percent / 100.0
    lines: list[dict] = []
    expected_total = 0.0
    for it in canon.get("items") or []:
        sku = str(it.get("sku") or "").strip()
        pid = line_resolutions.get(sku)
        if pid is None:
            raise ManualSendRefused(
                "product_id_missing",
                f"تعذّر ربط منتج بـ SKU={sku!r}",
                {"sku": sku})
        qty = _f(it.get("quantity"), 1.0) or 1.0
        unit_price = _f(it.get("unit_price"))
        target_gross = _f(it.get("total"))
        target_net = round(target_gross / tax_factor, 4)
        original_base = round(unit_price * qty, 4)
        discount = round(original_base - target_net, 4)
        if discount < 0:
            unit_price = round(target_net / qty, 4) if qty else target_net
            discount = 0.0
        lines.append({
            "product_id":    pid,
            "description":   it.get("name") or sku,
            "quantity":      qty,
            "unit_price":    unit_price,
            "discount":      discount,
            "discount_type": "amount",
            "tax_percent":   tax_percent,
        })
        expected_total += round(
            (unit_price * qty - discount) * tax_factor, 2)

    # Shipping line (optional — only if configured AND non-zero).
    shipping_amount = round(_f(canon.get("shipping_amount")), 2)
    if shipping_amount > 0:
        ship_pid = _to_int(settings.get("default_shipping_product_id"))
        if ship_pid is not None:
            items_gross_sum = sum(_f(it.get("total"))
                                   for it in canon.get("items") or [])
            ship_target_gross = round(
                _f(canon.get("total_amount")) - items_gross_sum, 2)
            if ship_target_gross > 0:
                ship_net = round(ship_target_gross / tax_factor, 4)
                ship_unit = round(shipping_amount, 4)
                ship_discount = round(ship_unit - ship_net, 4)
                if ship_discount < 0:
                    ship_unit = ship_net
                    ship_discount = 0.0
                lines.append({
                    "product_id":    ship_pid,
                    "description":   "شحن (Shipping)",
                    "quantity":      1,
                    "unit_price":    ship_unit,
                    "discount":      ship_discount,
                    "discount_type": "amount",
                    "tax_percent":   tax_percent,
                })
                expected_total += round(
                    (ship_unit - ship_discount) * tax_factor, 2)

    # COD fee line (optional).
    cod_fee = round(_f(canon.get("cod_fee_amount")), 2)
    if cod_fee > 0:
        cod_pid = _to_int(settings.get("default_cod_fee_product_id"))
        if cod_pid is not None:
            cod_net = round(cod_fee / tax_factor, 4)
            lines.append({
                "product_id":    cod_pid,
                "description":   "رسوم الدفع عند الاستلام (COD Fee)",
                "quantity":      1,
                "unit_price":    round(cod_fee, 4),
                "discount":      round(cod_fee - cod_net, 4),
                "discount_type": "amount",
                "tax_percent":   tax_percent,
            })
            expected_total += round(
                (cod_fee - (cod_fee - cod_net)) * tax_factor, 2)

    issue_date = datetime.now(timezone.utc).date().isoformat()
    invoice: dict = {
        "invoice": {
            "contact_id":   contact_id,
            "issue_date":   issue_date,
            "due_date":     issue_date,
            "reference":    canon.get("order_number")
                            or canon.get("order_id"),
            "status":       "Approved",
            "payment_method": "10",
            "currency_code": canon.get("currency") or "SAR",
            "line_items":   lines,
            "notes":        f"Mezan Plan-B Manual · order "
                            f"{canon.get('order_number') or ''}",
            "external_reference": canon.get("order_id"),
        }
    }
    inv_id = _to_int(settings.get("default_inventory_id"))
    if inv_id is not None:
        invoice["invoice"]["inventory_id"] = inv_id
    br_id = _to_int(settings.get("default_branch_id"))
    if br_id is not None:
        invoice["invoice"]["branch_id"] = br_id
    return invoice, round(expected_total, 2)


def _build_payment_payload(*, invoice_id: int, amount: float,
                           account_id: int, reference: str) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "invoice_payment": {
            "invoice_id":  invoice_id,
            "amount":      round(amount, 2),
            "date":        today,
            "account_id":  account_id,
            "reference":   reference,
            "description": f"Mezan Plan-B Manual · order {reference}",
        }
    }


# ─── Main entrypoint ─────────────────────────────────────────────────
async def manual_send_one(
    db, *, user_id: str, order_number: str, actor: str = "manual-ui",
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

    # ── Floor date + status filters (mirror the pending listing) ────
    odate = _order_created_date(row)
    if odate is None or odate < _FLOOR_DATE:
        raise ManualSendRefused(
            "before_floor_date",
            f"تاريخ الطلب أقدم من {_FLOOR_DATE.isoformat()} — "
            "خارج نطاق التكامل")
    if not _is_completed(row):
        raise ManualSendRefused(
            "not_completed",
            "حالة الطلب ليست (تم التنفيذ) — الإرسال مسموح لهذه الحالة فقط")

    # ── Guard G1a — DB says already-sent? ──────────────────────────
    if row.get("manual_qoyod_invoice_id"):
        raise ManualSendRefused(
            "already_sent",
            "الطلب أُرسل مسبقاً من مسار الإرسال اليدوي",
            {"manual_qoyod_invoice_id": row["manual_qoyod_invoice_id"]})
    legacy_qid = row.get("qoyod_invoice_id")
    if legacy_qid and _is_real(legacy_qid):
        raise ManualSendRefused(
            "already_sent_legacy",
            "يوجد فاتورة قيود سابقة لهذا الطلب من المسار القديم",
            {"qoyod_invoice_id": legacy_qid})

    canon = row.get("canonical_payload") or {}
    salla_total = round(_f(canon.get("total_amount")), 2)
    payment_method = (canon.get("payment_method")
                       or canon.get("payment_method_native"))

    # ── Load settings + resolve payment account (Guard G4) ─────────
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    qoyod_account_id_raw = resolve_payment_account(settings, payment_method)
    qoyod_account_id = _to_int(qoyod_account_id_raw)
    if qoyod_account_id is None:
        raise ManualSendRefused(
            "payment_method_unmapped",
            "طريقة الدفع غير مرتبطة بحساب في قيود — "
            "اربطها من إعدادات قيود → طرق الدفع",
            {"payment_method": payment_method,
             "resolved_raw":  qoyod_account_id_raw})

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
        return await _run_all_steps(
            db, client=client, row=row, canon=canon, settings=settings,
            user_id=user_id, order_number=str(order_number),
            lock_id=lock_id, salla_total=salla_total,
            qoyod_account_id=qoyod_account_id,
        )
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
    salla_total: float, qoyod_account_id: int,
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
        created = await client.create_product(payload, idem=idem)
        node = created.get("product") if isinstance(created, dict) else None
        if not isinstance(node, dict):
            node = created if isinstance(created, dict) else {}
        pid = _to_int(node.get("id"))
        if pid is None:
            raise ManualSendRefused(
                "product_create_failed",
                f"قيود لم يُعِد رقم منتج صالحاً للـ SKU {sku}",
                {"response": created})
        line_resolutions[sku] = pid
        product_trace.append({"sku": sku, "product_id": pid,
                              "resolution": "created"})
    steps_trace.append({"step": "products", "resolutions": product_trace})

    # ── 3) Build invoice payload + Guard G2 (totals) ───────────────
    invoice_payload, expected_total = _build_invoice_payload(
        canon=canon, contact_id=contact_id,
        line_resolutions=line_resolutions, settings=settings)
    diff = round(expected_total - salla_total, 2)
    if abs(diff) > 0.01:
        raise ManualSendRefused(
            "totals_mismatch",
            f"فرق المبلغ {abs(diff)} ريال أكبر من 0.01 — أُوقف الإرسال",
            {"salla_total": salla_total,
             "expected_qoyod_total": expected_total,
             "difference": diff})

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
    steps_trace.append({"step": "invoice",
                        "invoice_id": invoice_id,
                        "invoice_number": invoice_number,
                        "expected_total": expected_total,
                        "salla_total": salla_total,
                        "difference": diff})

    # Persist marker immediately so a retry can't double-post.
    await db.integration_inbox.update_one(
        {"id": row.get("id")},
        {"$set": {"manual_qoyod_invoice_id": str(invoice_id),
                   "manual_qoyod_invoice_number": (str(invoice_number)
                                                    if invoice_number else None),
                   "manual_send_last_status": "invoice_created",
                   "manual_send_at": datetime.now(timezone.utc)}})

    # ── 5) POST invoice payment ────────────────────────────────────
    payment_payload = _build_payment_payload(
        invoice_id=invoice_id, amount=salla_total,
        account_id=qoyod_account_id, reference=order_number)
    idem_pay = f"pay-{order_number}"
    try:
        created_pay = await client.create_invoice_payment(
            payment_payload, idem=idem_pay)
    except ManualQoyodError as exc:
        # Invoice succeeded, payment failed — surface a distinct code
        # so the operator can retry ONLY the payment via a follow-up.
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
                   "manual_send_last_status": "succeeded",
                   "manual_send_at": datetime.now(timezone.utc)}})

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
        "salla_total":   salla_total,
        "expected_total": expected_total,
        "difference":    diff,
        "qoyod_account_id": qoyod_account_id,
        "steps":         steps_trace,
    }
