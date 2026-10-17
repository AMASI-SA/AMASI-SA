"""Bank Transfer Review — Iter-251

Phase 1 (MVP): Independent review queue for ALL incoming bank transfers
that originate from a wallet/platform/customer.  The rule:

    Nothing hits the bank's GL balance until a human Reviewer confirms
    the money actually landed in the bank.

This Phase 1 module is **strictly additive** — it does NOT touch
existing webhooks, settlements, or ledger flows.  Production behaviour
is unchanged until a future phase activates the source hooks behind
the `platform_settlement_to_review_enabled` feature flag.

Collection
----------
`bank_transfer_reviews`

Document
~~~~~~~~
{
  id:                str (uuid)
  user_id:           str

  # ── Source side (where the money is coming FROM) ──
  source_type:       enum  # salla | tamara | tabby | imkan |
                            shipping_cod | customer_transfer | manual
  source_id:         str            # internal id of the source entity
                                     # (settlement_id, order_id, etc.)
  source_external_id: str | None    # provider-side id (e.g. Tamara
                                     # settlement_id, Salla transfer_id)
  source_account_id:   str | None   # wallet/account id on our side
  source_account_name: str          # display label (e.g. "محفظة سلة")

  # ── Target side (the bank the money is going TO) ──
  target_bank_id:    str
  target_bank_name:  str

  # ── Money ──
  expected_amount:   float
  received_amount:   float | None   # null until confirmation
  difference:        float | None   # received - expected (post-confirm)
  currency:          "SAR"          # future-proof

  # ── Identifiers ──
  internal_reference: str | None    # our own ref (settlement number)
  provider_reference: str | None    # provider's ref (Tamara/Tabby ID)
  bank_reference:    str | None     # bank statement ref
  transfer_date:     str (ISO date) # when provider said it sent
  due_date:          str | None     # scheduled payout date (for accruals)

  # ── Review lifecycle ──
  status:            enum
       # pending                  → waiting for reviewer
       # confirmed                → reviewer confirmed exact amount
       # confirmed_with_difference→ confirmed received != expected
       # rejected                 → reviewer rejected/marked invalid
       # legacy_confirmed         → imported from pre-Iter-251 history,
       #                            displayed read-only, NEVER touches GL
  reviewed_by:       str | None
  reviewed_by_name:  str | None
  reviewed_at:       str | None
  review_action:     str | None     # confirm | edit_amount | reject
  review_note:       str | None

  # ── GL link ──
  ledger_txn_group_id: str | None   # set after confirm posts GL

  # ── Difference handling (future phase 5) ──
  difference_disposition: str | None
       # outstanding | fees | manual_settlement | None
  difference_resolved_at: str | None
  difference_ledger_txn_group_id: str | None

  # ── Provenance (for future auto-population from webhooks) ──
  auto_created:      bool           # True when injected by source hook
  provider_payload:  dict | None    # raw webhook payload for audit

  created_at, updated_at, created_by
}

Unique index (future, for hooks):
    (user_id, source_type, source_id, target_bank_id)
    over non-legacy entries — prevents double-posting the same provider
    settlement.

Endpoints
---------
POST   /api/bank-transfer-review                — create manual entry
GET    /api/bank-transfer-review                — list (+ filters)
GET    /api/bank-transfer-review/summary        — counts/totals
GET    /api/bank-transfer-review/{id}           — single record
POST   /api/bank-transfer-review/{id}/confirm   — exact-amount confirm
POST   /api/bank-transfer-review/{id}/confirm-with-difference — diff
POST   /api/bank-transfer-review/{id}/edit-amount — pre-confirm edit
POST   /api/bank-transfer-review/{id}/reject    — reject
DELETE /api/bank-transfer-review/{id}           — only when pending
"""
from __future__ import annotations

import uuid
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field


# ─────────────────────────────── helpers ──────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _r(x) -> float:
    try:
        return round(float(x or 0), 2)
    except Exception:
        return 0.0


SOURCE_TYPES = {"salla", "tamara", "tabby", "imkan",
                "shipping_cod", "customer_transfer", "manual"}

LIFECYCLE_STATUSES = {
    "pending", "confirmed", "confirmed_with_difference",
    "rejected", "legacy_confirmed",
    # Iter-251 · Phase 1.5 — auto-created review when the provider
    # has no `default_bank_for_<provider>` mapped in /settings.  The
    # row is held in this status until a Reviewer manually assigns a
    # bank via /assign-bank.  Never reaches GL until then.
    "missing_target_bank",
}

# Allowed provider keys that can be auto-routed to a default bank.
ROUTABLE_PROVIDERS = ("salla", "tamara", "tabby", "imkan")


# ─────────────────────────────── DTOs ─────────────────────────────────
class ReviewCreateIn(BaseModel):
    source_type:         str
    source_id:           str
    source_external_id:  Optional[str] = None
    source_account_id:   Optional[str] = None
    source_account_name: str
    # Iter-251 · Phase 1.5 — both target bank fields are optional now.
    # Auto-created entries can omit them and the system will try to
    # resolve via `default_bank_for_<provider>` setting. If still
    # missing, the row lands in `missing_target_bank` status.
    target_bank_id:      Optional[str] = None
    target_bank_name:    Optional[str] = None
    expected_amount:     float
    transfer_date:       str        # ISO date
    due_date:            Optional[str] = None
    internal_reference:  Optional[str] = None
    provider_reference:  Optional[str] = None
    bank_reference:      Optional[str] = None
    currency:            str = "SAR"
    review_note:         Optional[str] = None
    # Auto-created entries from future webhooks pass a payload blob.
    auto_created:        bool = False
    provider_payload:    Optional[dict] = None


class AssignBankIn(BaseModel):
    target_bank_id:   str
    target_bank_name: str
    review_note:      Optional[str] = None


class ReviewConfirmIn(BaseModel):
    # When omitted, the system confirms the exact expected_amount.
    review_note:    Optional[str] = None
    bank_reference: Optional[str] = None


class ReviewConfirmDiffIn(BaseModel):
    received_amount: float
    review_note:     Optional[str] = None
    bank_reference:  Optional[str] = None


class ReviewEditAmountIn(BaseModel):
    received_amount: float
    review_note:     Optional[str] = None


class ReviewRejectIn(BaseModel):
    review_note: str = Field(..., min_length=1)


# ─────────────────────────────── Router ───────────────────────────────
def make_bank_transfer_review_router(db, current_user):
    router = APIRouter(prefix="/bank-transfer-review",
                       tags=["bank-transfer-review"])

    # ---------- ensure unique index (forward-compat for hooks) --------
    # Future webhooks for Salla/Tamara/Tabby/Imkan will use this index
    # to guarantee exactly-once review-record creation per provider
    # settlement.  Partial filter excludes manual & legacy rows so the
    # merchant can still re-test by hand.
    try:
        # Motor schedules this call even without await. The isolated EXIT-2A
        # entrypoint validates its environment/network before importing us.
        if os.environ.get("MEZAN_EXIT2A_REHEARSAL") != "1":
            db.bank_transfer_reviews.create_index(
                [("user_id", 1), ("source_type", 1),
                 ("source_id", 1), ("target_bank_id", 1)],
                unique=True,
                partialFilterExpression={
                    "source_type": {"$in": ["salla", "tamara", "tabby",
                                              "imkan", "shipping_cod",
                                              "customer_transfer"]},
                },
                name="uniq_review_source_target",
            )
    except Exception:
        # In tests / fresh DBs Motor may not yet be connected — the
        # index will be created on the first hit anyway.
        pass

    # ─────────── helpers ───────────
    def _validate_source_type(t: str):
        if t not in SOURCE_TYPES:
            raise HTTPException(
                400,
                f"نوع المصدر غير معروف: {t}. "
                f"المسموح: {', '.join(sorted(SOURCE_TYPES))}",
            )

    async def _fetch_or_404(uid: str, rid: str) -> dict:
        doc = await db.bank_transfer_reviews.find_one(
            {"id": rid, "user_id": uid}, {"_id": 0},
        )
        if not doc:
            raise HTTPException(404, "سجل المراجعة غير موجود")
        return doc

    # Iter-251 · Phase 1.5 — Resolve `default_bank_for_<provider>` from
    # user settings → returns (bank_id, bank_name) or (None, None).
    async def _resolve_default_bank(
        uid: str, provider: str,
    ) -> tuple[Optional[str], Optional[str]]:
        if provider not in ROUTABLE_PROVIDERS:
            return None, None
        s = await db.settings.find_one(
            {"user_id": uid},
            {"_id": 0, f"default_bank_for_{provider}": 1},
        ) or {}
        bank_id = s.get(f"default_bank_for_{provider}")
        if not bank_id:
            return None, None
        acc = await db.accounts.find_one(
            {"user_id": uid, "id": bank_id},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not acc:
            return None, None
        return acc["id"], acc.get("name") or ""

    async def _post_gl_for_confirm(
        uid: str,
        user: dict,
        review: dict,
        received_amount: float,
    ) -> str:
        """Post a balanced 2-leg ledger group:
            DEBIT  bank account (asset+)
            CREDIT source platform account (asset-)
        Returns the txn_group_id.  Raises 4xx on validation failure.
        """
        from ledger_core import post_txn_group as _ptg
        if received_amount <= 0:
            raise HTTPException(400, "المبلغ الواصل يجب أن يكون > 0")
        # We intentionally allow source_account_id to be None for
        # manual test entries — in that case we fall back to a virtual
        # "manual" placeholder so the GL still balances.  Real
        # provider hooks (phase 2+) will always supply real ids.
        source_entity_id = review.get("source_account_id") \
            or f"manual:{review['source_type']}:{review['id']}"
        meta = {
            "review_id":           review["id"],
            "source_type":         review["source_type"],
            "source_id":           review["source_id"],
            "source_external_id":  review.get("source_external_id"),
            "internal_reference":  review.get("internal_reference"),
            "provider_reference":  review.get("provider_reference"),
            "bank_reference":      review.get("bank_reference"),
            "transfer_date":       review.get("transfer_date"),
            "expected_amount":     _r(review.get("expected_amount")),
            "received_amount":     _r(received_amount),
        }
        group = await _ptg(
            db,
            user_id=uid,
            actor_id=user["id"],
            actor_name=user.get("name") or user.get("email") or "",
            entries=[
                {
                    "entity_type": "bank",
                    "entity_id":   review["target_bank_id"],
                    "entry_type":  "payment",
                    "side":        "debit",
                    "amount":      _r(received_amount),
                    "metadata":    {"role": "bank_target"},
                },
                {
                    "entity_type": review["source_type"] + "_wallet",
                    "entity_id":   source_entity_id,
                    "entry_type":  "payment",
                    "side":        "credit",
                    "amount":      _r(received_amount),
                    "metadata":    {"role": "source_platform"},
                },
            ],
            txn_type="bank_transfer_review_confirmed",
            reason_code="bank_transfer_review",
            notes=(review.get("review_note") or "")[:500],
            metadata=meta,
        )
        return group["txn_group_id"]

    # ─────────── CREATE manual entry ───────────
    @router.post("")
    async def create_review(
        payload: ReviewCreateIn,
        user: dict = Depends(current_user),
    ):
        _validate_source_type(payload.source_type)
        if _r(payload.expected_amount) <= 0:
            raise HTTPException(400, "المبلغ المتوقع يجب أن يكون > 0")
        if not payload.source_account_name:
            raise HTTPException(400, "اسم الحساب المصدر مطلوب")

        # Iter-251 · Phase 1.5 — Resolve target bank.
        #   1. If caller passed both target_bank_id + target_bank_name
        #      → use as-is (manual mode).
        #   2. Else try `default_bank_for_<provider>` from settings.
        #   3. Else mark the row `missing_target_bank` and require a
        #      Reviewer to assign via /assign-bank.
        target_bank_id   = (payload.target_bank_id   or "").strip() or None
        target_bank_name = (payload.target_bank_name or "").strip() or None
        if not target_bank_id:
            resolved_id, resolved_name = await _resolve_default_bank(
                user["id"], payload.source_type)
            if resolved_id:
                target_bank_id   = resolved_id
                target_bank_name = resolved_name

        # For manual entries the merchant MUST provide a bank.
        if payload.source_type == "manual" and not target_bank_id:
            raise HTTPException(
                400,
                "للسجلات اليدوية يجب تحديد البنك المستلم.",
            )

        # Idempotency: respect the unique index — only enforced for
        # provider-sourced entries (the partial filter exempts manual).
        # When target_bank_id is still missing we use a sentinel so
        # duplicates from the same provider still collide.
        dedup_target = target_bank_id or "__missing__"
        if payload.source_type != "manual":
            existing = await db.bank_transfer_reviews.find_one(
                {
                    "user_id":        user["id"],
                    "source_type":    payload.source_type,
                    "source_id":      payload.source_id,
                    "target_bank_id": dedup_target,
                },
                {"_id": 0, "id": 1, "status": 1},
            )
            if existing:
                raise HTTPException(
                    409,
                    f"يوجد سجل مراجعة سابق لنفس المصدر والبنك "
                    f"(id={existing['id']}, status={existing['status']})",
                )

        initial_status = (
            "pending" if target_bank_id else "missing_target_bank"
        )

        doc = {
            "id":                  str(uuid.uuid4()),
            "user_id":             user["id"],
            "source_type":         payload.source_type,
            "source_id":           payload.source_id,
            "source_external_id":  payload.source_external_id,
            "source_account_id":   payload.source_account_id,
            "source_account_name": payload.source_account_name.strip(),
            "target_bank_id":      target_bank_id or dedup_target,
            "target_bank_name":    target_bank_name,
            "expected_amount":     _r(payload.expected_amount),
            "received_amount":     None,
            "difference":          None,
            "currency":            payload.currency or "SAR",
            "internal_reference":  payload.internal_reference,
            "provider_reference":  payload.provider_reference,
            "bank_reference":      payload.bank_reference,
            "transfer_date":       payload.transfer_date,
            "due_date":            payload.due_date,
            "status":              initial_status,
            "reviewed_by":         None,
            "reviewed_by_name":    None,
            "reviewed_at":         None,
            "review_action":       None,
            "review_note":         payload.review_note,
            "ledger_txn_group_id": None,
            "difference_disposition":         None,
            "difference_resolved_at":         None,
            "difference_ledger_txn_group_id": None,
            "auto_created":        payload.auto_created,
            "provider_payload":    payload.provider_payload,
            "created_by":          user["id"],
            "created_at":          _now(),
            "updated_at":          _now(),
        }
        await db.bank_transfer_reviews.insert_one(doc)
        doc.pop("_id", None)
        return doc

    # ─────────── LIST ───────────
    @router.get("")
    async def list_reviews(
        status:      Optional[str] = Query(None),
        source_type: Optional[str] = Query(None),
        bank_id:     Optional[str] = Query(None),
        q:           Optional[str] = Query(None),
        from_date:   Optional[str] = Query(None),
        to_date:     Optional[str] = Query(None),
        skip:        int = 0,
        limit:       int = 100,
        user:        dict = Depends(current_user),
    ):
        q_doc: dict = {"user_id": user["id"]}
        if status:
            statuses = [s.strip() for s in status.split(",") if s.strip()]
            invalid = [s for s in statuses if s not in LIFECYCLE_STATUSES]
            if invalid:
                raise HTTPException(
                    400, f"حالات غير معروفة: {', '.join(invalid)}")
            q_doc["status"] = {"$in": statuses}
        if source_type:
            srcs = [s.strip() for s in source_type.split(",") if s.strip()]
            q_doc["source_type"] = {"$in": srcs}
        if bank_id:
            q_doc["target_bank_id"] = bank_id
        if from_date or to_date:
            rng: dict = {}
            if from_date:
                rng["$gte"] = from_date
            if to_date:
                rng["$lte"] = to_date
            q_doc["transfer_date"] = rng
        if q:
            needle = q.strip()
            q_doc["$or"] = [
                {"source_account_name": {"$regex": needle, "$options": "i"}},
                {"target_bank_name":   {"$regex": needle, "$options": "i"}},
                {"internal_reference": {"$regex": needle, "$options": "i"}},
                {"provider_reference": {"$regex": needle, "$options": "i"}},
                {"bank_reference":     {"$regex": needle, "$options": "i"}},
                {"source_external_id": {"$regex": needle, "$options": "i"}},
            ]

        total = await db.bank_transfer_reviews.count_documents(q_doc)
        cursor = (db.bank_transfer_reviews
                    .find(q_doc, {"_id": 0})
                    .sort([("transfer_date", -1), ("created_at", -1)])
                    .skip(max(0, skip))
                    .limit(max(1, min(limit, 500))))
        items = [d async for d in cursor]
        return {"items": items, "total": total,
                "skip": skip, "limit": limit}

    # ─────────── SUMMARY ───────────
    @router.get("/summary")
    async def summary(user: dict = Depends(current_user)):
        uid = user["id"]
        pipeline = [
            {"$match": {"user_id": uid}},
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1},
                "expected_total": {"$sum": "$expected_amount"},
                "received_total": {
                    "$sum": {"$ifNull": ["$received_amount", 0]}},
                "difference_total": {
                    "$sum": {"$ifNull": ["$difference", 0]}},
            }},
        ]
        by_status: dict[str, dict] = {}
        async for row in db.bank_transfer_reviews.aggregate(pipeline):
            by_status[row["_id"]] = {
                "count":            row["count"],
                "expected_total":   _r(row["expected_total"]),
                "received_total":   _r(row["received_total"]),
                "difference_total": _r(row["difference_total"]),
            }
        # Per-source pending counts (for sidebar / dashboard chips).
        pipeline_src = [
            {"$match": {"user_id": uid, "status": "pending"}},
            {"$group": {"_id": "$source_type", "count": {"$sum": 1},
                        "expected_total": {"$sum": "$expected_amount"}}},
        ]
        by_source_pending: dict[str, dict] = {}
        async for r in db.bank_transfer_reviews.aggregate(pipeline_src):
            by_source_pending[r["_id"]] = {
                "count":          r["count"],
                "expected_total": _r(r["expected_total"]),
            }
        return {
            "by_status":         by_status,
            "by_source_pending": by_source_pending,
        }

    # ─────────── GET single ───────────
    @router.get("/{rid}")
    async def get_review(rid: str, user: dict = Depends(current_user)):
        return await _fetch_or_404(user["id"], rid)

    # ─────────── Provider → Bank mapping (Iter-251 · Phase 1.5) ───
    # Returns the current routing configuration so the frontend can
    # render an inline alert ("لم يتم تحديد بنك لـ تمارا") and link
    # the merchant directly to the right Settings section.
    @router.get("/config/provider-banks")
    async def provider_banks(user: dict = Depends(current_user)):
        uid = user["id"]
        s = await db.settings.find_one({"user_id": uid}, {"_id": 0}) or {}
        out: dict = {}
        for prov in ROUTABLE_PROVIDERS:
            bid = s.get(f"default_bank_for_{prov}")
            bank_id, bank_name = None, None
            if bid:
                acc = await db.accounts.find_one(
                    {"user_id": uid, "id": bid},
                    {"_id": 0, "id": 1, "name": 1},
                )
                if acc:
                    bank_id, bank_name = acc["id"], acc.get("name")
            out[prov] = {
                "configured":      bool(bank_id),
                "target_bank_id":  bank_id,
                "target_bank_name": bank_name,
            }
        # Count reviews currently stuck on each provider.
        stuck_cursor = db.bank_transfer_reviews.aggregate([
            {"$match": {"user_id": uid,
                        "status": "missing_target_bank"}},
            {"$group": {"_id": "$source_type", "count": {"$sum": 1}}},
        ])
        async for row in stuck_cursor:
            if row["_id"] in out:
                out[row["_id"]]["missing_target_bank_count"] = row["count"]
        for prov in ROUTABLE_PROVIDERS:
            out[prov].setdefault("missing_target_bank_count", 0)
        return out

    # ─────────── Assign bank to a missing_target_bank row ─────────
    @router.post("/{rid}/assign-bank")
    async def assign_bank(
        rid: str,
        payload: AssignBankIn,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        doc = await _fetch_or_404(uid, rid)
        if doc["status"] != "missing_target_bank":
            raise HTTPException(
                400,
                "تخصيص البنك متاح فقط للسجلات بحالة missing_target_bank",
            )
        if not payload.target_bank_id or not payload.target_bank_name:
            raise HTTPException(400, "معرّف البنك واسم البنك مطلوبان")
        now = _now()
        update = {
            "target_bank_id":   payload.target_bank_id.strip(),
            "target_bank_name": payload.target_bank_name.strip(),
            "status":           "pending",
            "review_note":      payload.review_note or doc.get("review_note"),
            "updated_at":       now,
        }
        await db.bank_transfer_reviews.update_one(
            {"id": rid, "user_id": uid}, {"$set": update},
        )
        return {**doc, **update}

    # ─────────── CONFIRM (exact) ───────────
    @router.post("/{rid}/confirm")
    async def confirm_review(
        rid: str,
        payload: ReviewConfirmIn,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        doc = await _fetch_or_404(uid, rid)
        if doc["status"] != "pending":
            raise HTTPException(
                400,
                f"لا يمكن تأكيد سجل بالحالة «{doc['status']}». "
                "يجب أن يكون «pending».",
            )

        received = _r(doc["expected_amount"])
        txn_group_id = await _post_gl_for_confirm(uid, user, doc, received)

        now = _now()
        update = {
            "status":              "confirmed",
            "received_amount":     received,
            "difference":          0.0,
            "reviewed_by":         user["id"],
            "reviewed_by_name":    user.get("name") or user.get("email"),
            "reviewed_at":         now,
            "review_action":       "confirm",
            "review_note":         (payload.review_note
                                    or doc.get("review_note")),
            "ledger_txn_group_id": txn_group_id,
            "updated_at":          now,
        }
        if payload.bank_reference:
            update["bank_reference"] = payload.bank_reference
        await db.bank_transfer_reviews.update_one(
            {"id": rid, "user_id": uid}, {"$set": update},
        )
        return {**doc, **update}

    # ─────────── CONFIRM-WITH-DIFFERENCE ───────────
    @router.post("/{rid}/confirm-with-difference")
    async def confirm_with_difference(
        rid: str,
        payload: ReviewConfirmDiffIn,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        doc = await _fetch_or_404(uid, rid)
        if doc["status"] != "pending":
            raise HTTPException(
                400,
                f"لا يمكن تأكيد سجل بالحالة «{doc['status']}». "
                "يجب أن يكون «pending».",
            )
        received = _r(payload.received_amount)
        if received <= 0:
            raise HTTPException(400, "المبلغ الواصل يجب أن يكون > 0")
        if received > _r(doc["expected_amount"]):
            raise HTTPException(
                400,
                "المبلغ الواصل يتجاوز المتوقع — استخدم تأكيد عادي "
                "أو راجع المبلغ المتوقع.",
            )

        txn_group_id = await _post_gl_for_confirm(uid, user, doc, received)
        difference = round(received - _r(doc["expected_amount"]), 2)
        # difference is negative because received < expected;
        # absolute remainder remains in the source platform balance.

        now = _now()
        update = {
            "status":              "confirmed_with_difference",
            "received_amount":     received,
            "difference":          difference,
            "reviewed_by":         user["id"],
            "reviewed_by_name":    user.get("name") or user.get("email"),
            "reviewed_at":         now,
            "review_action":       "confirm_with_difference",
            "review_note":         (payload.review_note
                                    or doc.get("review_note")),
            "ledger_txn_group_id": txn_group_id,
            "updated_at":          now,
        }
        if payload.bank_reference:
            update["bank_reference"] = payload.bank_reference
        await db.bank_transfer_reviews.update_one(
            {"id": rid, "user_id": uid}, {"$set": update},
        )
        return {**doc, **update}

    # ─────────── EDIT amount (pre-confirm) ───────────
    @router.post("/{rid}/edit-amount")
    async def edit_amount(
        rid: str,
        payload: ReviewEditAmountIn,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        doc = await _fetch_or_404(uid, rid)
        if doc["status"] != "pending":
            raise HTTPException(
                400,
                "لا يمكن تعديل المبلغ بعد التأكيد أو الرفض.",
            )
        if _r(payload.received_amount) < 0:
            raise HTTPException(400, "المبلغ الواصل لا يقبل قيمة سالبة")
        now = _now()
        update = {
            "received_amount":  _r(payload.received_amount),
            "difference":       round(
                _r(payload.received_amount)
                - _r(doc["expected_amount"]), 2),
            "review_note":      payload.review_note or doc.get("review_note"),
            "updated_at":       now,
        }
        await db.bank_transfer_reviews.update_one(
            {"id": rid, "user_id": uid}, {"$set": update},
        )
        return {**doc, **update}

    # ─────────── REJECT ───────────
    @router.post("/{rid}/reject")
    async def reject_review(
        rid: str,
        payload: ReviewRejectIn,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        doc = await _fetch_or_404(uid, rid)
        if doc["status"] != "pending":
            raise HTTPException(
                400, "لا يمكن رفض سجل تم تأكيده مسبقاً")
        now = _now()
        update = {
            "status":           "rejected",
            "reviewed_by":      user["id"],
            "reviewed_by_name": user.get("name") or user.get("email"),
            "reviewed_at":      now,
            "review_action":    "reject",
            "review_note":      payload.review_note,
            "updated_at":       now,
        }
        await db.bank_transfer_reviews.update_one(
            {"id": rid, "user_id": uid}, {"$set": update},
        )
        return {**doc, **update}

    # ─────────── DELETE (pending only) ───────────
    @router.delete("/{rid}")
    async def delete_review(rid: str, user: dict = Depends(current_user)):
        uid = user["id"]
        doc = await _fetch_or_404(uid, rid)
        if doc["status"] != "pending":
            raise HTTPException(
                400,
                "لا يمكن حذف سجل تم تأكيده — استخدم العكس عبر سجل GL.",
            )
        await db.bank_transfer_reviews.delete_one(
            {"id": rid, "user_id": uid})
        return {"ok": True, "deleted": rid}

    return router
