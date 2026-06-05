"""Payment Settlements (تسويات المدفوعات)
=========================================
A unified ledger of post-order adjustments for **every** payment method:
partial refunds, full refunds, item removals, order cancellations, and any
manual settlement that affects net revenue per provider.

WHY it exists (iter-56)
-----------------------
Previously the dashboard's "صافي المدفوعات الإلكترونية" only excluded orders
whose **status** had become "refunded/cancelled". This missed a common
real-world case: an order keeps a healthy status but receives a partial
refund (e.g. one item out of three is returned, customer keeps the rest).
Salla deducts the refunded amount from the merchant's wallet, but the
order remains "delivered/completed" in the listing.

This module solves that by introducing a parallel ledger where every
amount-changing event is recorded explicitly. Net revenue per payment
method then becomes:

    net = sum(orders for method, filtered by status) − sum(adjustments where adjusted_at in date range)

The deduction date is the **adjustment date**, not the order date — so a
refund processed today against a 30-day-old order shows up in today's
report (this matches how Salla actually deducts the merchant's wallet).

14-day window (Salla-only)
--------------------------
Salla settles electronic payments to the merchant's bank ~14 days after
order delivery. Two virtual buckets are exposed for "salla" provider:

  • inside_14d  — orders ≤ 14 days old → still in Salla's pending wallet
  • outside_14d — orders > 14 days old → already paid out
  • an adjustment to a 30-day-old order is logged in TODAY's payout, not
    in the original order's payout. We capture this by classifying based
    on `order_created_at` for the "where would Salla pull this from" view.

Endpoints (all under /api/settlements)
--------------------------------------
- GET   /                  → list with filters (provider, from, to, type, window)
- POST  /                  → create one adjustment
- PUT   /{id}              → edit
- DELETE /{id}             → remove
- GET   /summary           → totals per provider for a date range
- GET   /providers         → enumerate active providers for the user
"""

from __future__ import annotations
import uuid
from datetime import datetime, date, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, validator

from auth import get_current_user_from_db


# ── Provider classification ────────────────────────────────────────────────
PROVIDERS = ("salla", "tamara", "tabby", "emkan", "bank_transfer", "cod", "other")

from payment_methods import detect_settlement_provider as _detect_settlement_provider


# Kept for backward-compat with callers that import `detect_provider` from
# this module. All classification logic now lives in payment_methods.py.
PROVIDER_KEYWORDS: dict[str, tuple[str, ...]] = {}


def detect_provider(payment_method: str) -> str:
    """Map a free-text payment method to one of our provider buckets.

    Thin wrapper around `payment_methods.detect_settlement_provider` so the
    whole app shares a single classifier (iter-68).
    """
    return _detect_settlement_provider(payment_method or "")


# ── Adjustment types ───────────────────────────────────────────────────────
ADJUSTMENT_TYPES = {
    "partial_refund",      # استرجاع جزئي
    "full_refund",         # استرجاع كلي
    "item_removed",        # حذف منتج من الطلب
    "order_cancelled",     # إلغاء الطلب
    "manual_adjustment",   # تسوية يدوية أخرى
}

# iter-70.1 — explicit data-source provenance.
#   "manual"  → user typed it into the /settlements UI
#   "auto"    → detected by upsert_unified_order diff (iter-70.2, not active yet)
DETECTION_SOURCES = {"manual", "auto"}

# Where the upsert that fired an auto-detection came from. Kept None for
# manual entries. Used for diagnostics & traceability.
TRIGGER_SOURCES = {"excel_upsert", "make_webhook", "salla_oauth", "manual"}

# Legacy "source" values still accepted on inbound POSTs for backward
# compatibility with the existing UI / older API consumers.
SOURCES = {"manual_sync", "salla_webhook", "make_webhook"}


# ── 14-day window classification (Salla-only) ──────────────────────────────
SALLA_PAYOUT_DAYS = 14  # Salla's standard payout cycle


def classify_14d_window(order_created_at_iso: str, ref_date: date | None = None) -> str:
    """Return 'inside_14d' if the order is still within Salla's pending
    wallet, otherwise 'outside_14d'. ref_date defaults to today.
    Anything unparseable falls back to 'unknown'."""
    if not order_created_at_iso:
        return "unknown"
    try:
        order_d = datetime.strptime(order_created_at_iso[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return "unknown"
    ref = ref_date or datetime.now(timezone.utc).date()
    return "inside_14d" if (ref - order_d).days <= SALLA_PAYOUT_DAYS else "outside_14d"


# ── Pydantic models ────────────────────────────────────────────────────────
class SettlementIn(BaseModel):
    order_id: Optional[str] = None
    order_number: str = Field(..., min_length=1, max_length=64)
    payment_method: str = Field(..., min_length=1, max_length=120)
    original_amount: float = Field(..., gt=0)
    new_amount: float = Field(..., ge=0)
    # If client supplies adjustment_amount it must match original - new.
    # We compute it server-side anyway to be safe.
    adjustment_amount: Optional[float] = None
    adjustment_type: str
    order_created_at: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    adjusted_at: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    reason: Optional[str] = Field("", max_length=500)
    source: str = "manual_sync"

    @validator("adjustment_type")
    def _t(cls, v):
        if v not in ADJUSTMENT_TYPES:
            raise ValueError(f"adjustment_type must be one of {sorted(ADJUSTMENT_TYPES)}")
        return v

    @validator("source")
    def _s(cls, v):
        if v not in SOURCES:
            raise ValueError(f"source must be one of {sorted(SOURCES)}")
        return v


class SettlementUpdate(BaseModel):
    new_amount: Optional[float] = Field(None, ge=0)
    adjustment_type: Optional[str] = None
    adjusted_at: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    reason: Optional[str] = Field(None, max_length=500)

    @validator("adjustment_type")
    def _t(cls, v):
        if v is not None and v not in ADJUSTMENT_TYPES:
            raise ValueError(f"adjustment_type must be one of {sorted(ADJUSTMENT_TYPES)}")
        return v


# ── Helpers ────────────────────────────────────────────────────────────────
def _to_public(doc: dict) -> dict:
    """Strip Mongo internals + add derived fields (`provider`, `window`).

    iter-70.1: also surface the new provenance fields (`detection_source`,
    `trigger`, `detection_metadata`) so the frontend can distinguish manual
    vs auto entries. Defaults to `"manual"` for any legacy doc that
    pre-dates iter-70.1 (covered by the backfill at startup).
    """
    out = {k: v for k, v in doc.items() if not k.startswith("_")}
    out["provider"] = detect_provider(out.get("payment_method", ""))
    if out["provider"] == "salla":
        out["window"] = classify_14d_window(out.get("order_created_at", ""))
    else:
        out["window"] = None  # not applicable for non-Salla providers
    # Backward-compatible provenance defaults
    out.setdefault("detection_source", "manual")
    out.setdefault("trigger", "manual")
    out.setdefault("detection_metadata", None)
    return out


async def ensure_settlements_indexes(db) -> None:
    """iter-70.1 — partial unique index that prevents duplicate AUTO
    settlements for the same (order, original→new) tuple.

    Safe to call on every startup. Manual entries are NOT subject to this
    constraint — a merchant may want to log two settlements with the same
    amounts for different reasons.
    """
    try:
        await db.payment_adjustments.create_index(
            [
                ("user_id", 1),
                ("order_number", 1),
                ("original_amount", 1),
                ("new_amount", 1),
            ],
            name="uniq_auto_settlement_per_diff",
            unique=True,
            partialFilterExpression={"detection_source": "auto"},
        )
    except Exception:  # noqa: BLE001
        # Older Mongo (<3.2) doesn't support partial indexes — Layer 1
        # (in-app diff guard) is still enough to dedupe; don't crash boot.
        pass


async def backfill_settlement_provenance(db) -> int:
    """iter-70.1 — one-shot, idempotent backfill so every existing row
    has the new provenance fields. All entries that exist today are by
    definition manual (no detection code ran yet).

    Returns the number of documents updated.
    """
    res = await db.payment_adjustments.update_many(
        {"detection_source": {"$exists": False}},
        {"$set": {
            "detection_source": "manual",
            "trigger": "manual",
            "detection_metadata": None,
        }},
    )
    return int(res.modified_count or 0)


async def record_auto_settlement(
    db,
    *,
    user_id: str,
    order_number: str,
    payment_method: str,
    original_amount: float,
    new_amount: float,
    adjustment_type: str,
    order_created_at: str,
    adjusted_at: str,
    trigger: str,
    detection_metadata: dict | None = None,
    reason: str = "",
    order_id: str = "",
) -> dict | None:
    """iter-70.1 — internal helper for the (future) detection pipeline
    in 70.2. Inserts an auto-detected settlement, dedup-guarded by the
    partial unique index. Returns the inserted doc, or `None` if a
    duplicate already exists for the same diff.

    NOT WIRED UP IN 70.1 — the function exists so the schema, types and
    index can be unit-tested in isolation before 70.2 actually starts
    calling it from `upsert_unified_order()`.
    """
    if adjustment_type not in ADJUSTMENT_TYPES:
        raise ValueError(f"invalid adjustment_type: {adjustment_type!r}")
    if trigger not in TRIGGER_SOURCES:
        raise ValueError(f"invalid trigger: {trigger!r}")
    adj_amount = round(float(original_amount) - float(new_amount), 2)
    if adj_amount <= 0:
        return None  # not a settlement — diff is zero or negative
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "order_id": order_id or "",
        "order_number": order_number,
        "payment_method": payment_method,
        "provider": detect_provider(payment_method),
        "original_amount": round(float(original_amount), 2),
        "new_amount": round(float(new_amount), 2),
        "adjustment_amount": adj_amount,
        "adjustment_type": adjustment_type,
        "order_created_at": order_created_at,
        "adjusted_at": adjusted_at,
        "reason": (reason or "").strip(),
        "source": "manual_sync",   # legacy field — kept for backward compat
        "detection_source": "auto",
        "trigger": trigger,
        "detection_metadata": detection_metadata or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": user_id,
    }
    try:
        await db.payment_adjustments.insert_one(doc)
        # Re-fetch via UUID id (NOT the mutated _id) so callers never see
        # the ObjectId — and lint stays happy (EB001).
        return await db.payment_adjustments.find_one(
            {"id": doc["id"]}, {"_id": 0}
        )
    except Exception as exc:  # noqa: BLE001
        # Likely DuplicateKeyError from the partial unique index → already
        # logged. Treat as no-op so callers stay idempotent.
        if "DuplicateKeyError" in type(exc).__name__:
            return None
        raise


async def stamp_order_amount_history(
    db,
    *,
    user_id: str,
    order_number: str,
    prev_amount: float,
    new_amount: float,
    settlement_id: str | None,
    source: str,
    trigger: str,
) -> None:
    """iter-70.1 — append a lightweight history entry on `unified_orders`
    capturing a single amount change. Used by the (future) detection
    pipeline so the merchant can audit every diff and revert it.

    NOT WIRED UP IN 70.1 — schema-only helper.
    """
    entry = {
        "prev_amount": round(float(prev_amount), 2),
        "new_amount": round(float(new_amount), 2),
        "diff": round(float(prev_amount) - float(new_amount), 2),
        "detected_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "trigger": trigger,
        "settlement_id": settlement_id,
    }
    await db.unified_orders.update_one(
        {"user_id": user_id, "order_number": order_number},
        {"$push": {"amount_history": entry}},
    )


async def aggregate_settlements_by_provider(
    db, user_id: str, from_date: str | None = None, to_date: str | None = None
) -> dict:
    """Return per-provider totals for adjustments whose `adjusted_at` falls
    in the requested range. Used by the dashboard to subtract these from
    each payment method's net sales.

    Returns: {provider: {count, total_adjustment, total_original, total_new}}
    """
    match: dict = {"user_id": user_id}
    if from_date or to_date:
        match["adjusted_at"] = {}
        if from_date:
            match["adjusted_at"]["$gte"] = from_date
        if to_date:
            match["adjusted_at"]["$lte"] = to_date

    docs = await db.payment_adjustments.find(match, {"_id": 0}).to_list(50000)
    out: dict[str, dict] = {p: {"count": 0, "total_adjustment": 0.0,
                                  "total_original": 0.0, "total_new": 0.0}
                            for p in PROVIDERS}
    for d in docs:
        p = detect_provider(d.get("payment_method", ""))
        bucket = out[p]
        bucket["count"] += 1
        bucket["total_adjustment"] += float(d.get("adjustment_amount", 0) or 0)
        bucket["total_original"]   += float(d.get("original_amount", 0) or 0)
        bucket["total_new"]        += float(d.get("new_amount", 0) or 0)
    for p in out:
        out[p]["total_adjustment"] = round(out[p]["total_adjustment"], 2)
        out[p]["total_original"]   = round(out[p]["total_original"], 2)
        out[p]["total_new"]        = round(out[p]["total_new"], 2)
    return out


# ── Router ─────────────────────────────────────────────────────────────────
def attach_settlements_routes(parent_router: APIRouter, db) -> None:
    router = APIRouter(prefix="/settlements", tags=["settlements"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    @router.get("/providers")
    async def list_providers(_: dict = Depends(current_user)):
        """Static list — useful for filter dropdowns on the frontend."""
        labels = {
            "salla":         "سلة",
            "tamara":        "تمارا",
            "tabby":         "تابي",
            "emkan":         "إمكان",
            "bank_transfer": "تحويل بنكي",
            "cod":           "الدفع عند الاستلام",
            "other":         "أخرى",
        }
        return [{"key": p, "label": labels[p], "has_14d_window": p == "salla"}
                for p in PROVIDERS]

    @router.get("")
    async def list_settlements(
        user: dict = Depends(current_user),
        provider: Optional[str] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        adjustment_type: Optional[str] = None,
        window: Optional[str] = None,    # 'inside_14d' | 'outside_14d' (salla only)
    ):
        match: dict = {"user_id": user["id"]}
        if from_date or to_date:
            match["adjusted_at"] = {}
            if from_date:
                match["adjusted_at"]["$gte"] = from_date
            if to_date:
                match["adjusted_at"]["$lte"] = to_date
        if adjustment_type:
            if adjustment_type not in ADJUSTMENT_TYPES:
                raise HTTPException(400, "invalid adjustment_type filter")
            match["adjustment_type"] = adjustment_type
        docs = await db.payment_adjustments.find(
            match, {"_id": 0}
        ).sort("adjusted_at", -1).to_list(20000)
        results = [_to_public(d) for d in docs]
        if provider:
            results = [r for r in results if r["provider"] == provider]
        if window in ("inside_14d", "outside_14d"):
            results = [r for r in results if r.get("window") == window]
        return results

    @router.post("")
    async def create_settlement(payload: SettlementIn, user: dict = Depends(current_user)):
        adj_amount = round(float(payload.original_amount) - float(payload.new_amount), 2)
        if adj_amount <= 0:
            raise HTTPException(
                400,
                "adjustment_amount must be positive (original_amount must be greater than new_amount)",
            )
        # If client also sent adjustment_amount, allow a 0.01 tolerance — but
        # the canonical value is computed from original - new.
        if payload.adjustment_amount is not None and abs(payload.adjustment_amount - adj_amount) > 0.01:
            raise HTTPException(400, "supplied adjustment_amount doesn't match original − new")

        doc = {
            "id": str(uuid.uuid4()),
            "user_id": user["id"],
            "order_id": payload.order_id or "",
            "order_number": payload.order_number,
            "payment_method": payload.payment_method,
            "provider": detect_provider(payload.payment_method),
            "original_amount": round(float(payload.original_amount), 2),
            "new_amount": round(float(payload.new_amount), 2),
            "adjustment_amount": adj_amount,
            "adjustment_type": payload.adjustment_type,
            "order_created_at": payload.order_created_at,
            "adjusted_at": payload.adjusted_at,
            "reason": (payload.reason or "").strip(),
            "source": payload.source,
            # iter-70.1 — new provenance fields. Every entry from this UI
            # endpoint is by definition manual. Auto entries will arrive
            # via the internal `record_auto_settlement()` helper.
            "detection_source": "manual",
            "trigger": "manual",
            "detection_metadata": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": user["id"],
        }
        await db.payment_adjustments.insert_one(doc)
        return _to_public(doc)

    @router.put("/{settlement_id}")
    async def update_settlement(
        settlement_id: str, payload: SettlementUpdate, user: dict = Depends(current_user)
    ):
        existing = await db.payment_adjustments.find_one(
            {"id": settlement_id, "user_id": user["id"]}
        )
        if not existing:
            raise HTTPException(404, "Settlement not found")
        update: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}
        if payload.new_amount is not None:
            new_amt = round(float(payload.new_amount), 2)
            orig = float(existing["original_amount"])
            adj = round(orig - new_amt, 2)
            if adj <= 0:
                raise HTTPException(400, "new_amount must be less than original_amount")
            update["new_amount"] = new_amt
            update["adjustment_amount"] = adj
        if payload.adjustment_type is not None:
            update["adjustment_type"] = payload.adjustment_type
        if payload.adjusted_at is not None:
            update["adjusted_at"] = payload.adjusted_at
        if payload.reason is not None:
            update["reason"] = payload.reason.strip()
        await db.payment_adjustments.update_one(
            {"id": settlement_id, "user_id": user["id"]}, {"$set": update}
        )
        doc = await db.payment_adjustments.find_one(
            {"id": settlement_id, "user_id": user["id"]}, {"_id": 0}
        )
        return _to_public(doc)

    @router.delete("/{settlement_id}")
    async def delete_settlement(settlement_id: str, user: dict = Depends(current_user)):
        res = await db.payment_adjustments.delete_one(
            {"id": settlement_id, "user_id": user["id"]}
        )
        if res.deleted_count == 0:
            raise HTTPException(404, "Settlement not found")
        return {"ok": True}

    @router.get("/summary")
    async def settlements_summary(
        user: dict = Depends(current_user),
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ):
        per_provider = await aggregate_settlements_by_provider(
            db, user["id"], from_date, to_date
        )
        grand = round(sum(p["total_adjustment"] for p in per_provider.values()), 2)
        return {
            "from_date": from_date,
            "to_date": to_date,
            "grand_total_adjustment": grand,
            "by_provider": per_provider,
        }

    parent_router.include_router(router)
