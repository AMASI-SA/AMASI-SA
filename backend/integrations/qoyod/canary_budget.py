"""Iter-2026-02.rev35 — Live-Canary order budget (max_orders=1).

User directive (after Rev34.2 verify all_pass=true):
    "مسموح الآن فقط Live Canary محدود جداً: max_orders=1
     ممنوع إرسال أكثر من طلب واحد"

Design
──────
`qoyod_canary_budget` (one doc per tenant):
    {user_id, max_orders, order_numbers: [..], armed_at, armed_by}

• The budget must be EXPLICITLY armed (confirm_token) before any live
  send can happen. No doc → every reservation refuses (fail-closed).
• `max_orders` is HARD-CAPPED at 1 in this rev. Widening is a separate
  operator decision that must not piggy-back on this endpoint.
• Reservation is ATOMIC (single find_one_and_update with a $size
  guard) and IDEMPOTENT per order — the invoice POST and the
  invoice_payment POST of the SAME order consume ONE slot.
• Nothing here ever touches qoyod_settings — arming the budget does
  NOT enable the canary; the rev31 enable-tabby endpoint stays the
  only settings flip site.

Enforcement sites (belt-and-suspenders):
  1. pipeline._get_api_client — reservation BEFORE a live client is
     minted; refusal raises CanaryBudgetHold → the row is held at its
     current stage (no DRY write, no dead-letter, resumable).
  2. rev32_hardening.assert_final_write_permitted — write-time check
     that the order IS reserved; any bypass → Rev32Violation + kill
     switch (covers one-shot/manual/retry writers too).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from pymongo import ReturnDocument

logger = logging.getLogger(__name__)

BUDGET_COLLECTION  = "qoyod_canary_budget"
ARM_CONFIRM_TOKEN  = "ARM-CANARY-BUDGET"
# rev35 — the whole point of this iteration. Do NOT raise without an
# explicit user decision.
HARD_MAX_ORDERS    = 1

# rev39 — CURRENT canary phase scope (user decree 2026-07):
# mada, ONE order (270513107). The tabby phase is CLOSED. This single
# constant is the ONLY place the phase's payment method is defined;
# pipeline._live_write_permitted and rev32 assert_final_write_permitted
# both compare against it, exactly as they pinned tabby before.
CANARY_SCOPE_ALLOWLIST: list = ["mada"]


class CanaryBudgetRefused(Exception):
    def __init__(self, code: str, human: str):
        super().__init__(human)
        self.code = code


class CanaryBudgetHold(Exception):
    """Raised by pipeline._get_api_client when a live client was about
    to be minted but the canary budget refused the order. The caller
    must HOLD the row (no stage transition, no DRY processing)."""

    def __init__(self, reason: str, order_number: Optional[str]):
        super().__init__(f"canary_budget_hold: {reason}")
        self.reason       = reason
        self.order_number = order_number


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def arm_canary_budget(
    db, *, user_id: str, confirm_token: str,
    max_orders: int = 1, force_reset: bool = False,
    actor: str = "operator",
    pinned_order_number: str | None = None,
) -> dict:
    """Create/reset the tenant's canary budget. Refuses when:
      • confirm_token mismatch
      • max_orders > HARD_MAX_ORDERS (=1) or < 1
      • an existing budget already has used slots and force_reset
        was not explicitly passed."""
    if (confirm_token or "").strip() != ARM_CONFIRM_TOKEN:
        raise CanaryBudgetRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{ARM_CONFIRM_TOKEN}' to arm.")
    if not (1 <= int(max_orders) <= HARD_MAX_ORDERS):
        raise CanaryBudgetRefused(
            "max_orders_out_of_bounds",
            f"rev35 hard-caps max_orders at {HARD_MAX_ORDERS}. "
            f"Got {max_orders!r}. Widening is a separate decision.")

    existing = await db[BUDGET_COLLECTION].find_one({"user_id": user_id})
    used = list((existing or {}).get("order_numbers") or [])
    if existing and used and not force_reset:
        raise CanaryBudgetRefused(
            "budget_already_used",
            f"Budget already consumed by orders {used!r}. Review the "
            "first-run report, then re-arm with force_reset=true if "
            "you explicitly want a fresh slot.")

    now = _now()
    doc = {
        "user_id":       user_id,
        "max_orders":    int(max_orders),
        "order_numbers": [],
        "armed_at":      now,
        "armed_by":      actor,
        # rev39 — when set, ONLY this order may reserve the slot.
        "pinned_order_number": (str(pinned_order_number).strip()
                                if pinned_order_number else None),
        "previous_run_order_numbers": used if force_reset else [],
    }
    await db[BUDGET_COLLECTION].replace_one(
        {"user_id": user_id}, doc, upsert=True)
    logger.warning(
        "rev35 canary_budget_armed user_id=%s max_orders=%s actor=%s "
        "force_reset=%s previous=%s",
        user_id, max_orders, actor, force_reset, used)
    return {
        "ok":            True,
        "outcome":       "ARMED",
        "max_orders":    int(max_orders),
        "order_numbers": [],
        "armed_at":      now.isoformat(),
        "armed_by":      actor,
        "human_message": (
            f"ميزانية الـ Canary مُسلَّحة: طلب واحد فقط "
            f"(max_orders={max_orders}). أول طلب Tabby يمر عبر "
            "البوابات سيحجز الميزانية؛ أي طلب بعده يتوقف "
            "(hold) دون أي كتابة."),
    }


async def get_canary_budget(db, *, user_id: str) -> dict:
    doc = await db[BUDGET_COLLECTION].find_one(
        {"user_id": user_id}, {"_id": 0})
    if not doc:
        return {"ok": True, "armed": False, "max_orders": 0,
                "used": 0, "remaining": 0, "order_numbers": [],
                "note": "الميزانية غير مُسلَّحة — كل إرسال حي مرفوض."}
    used = list(doc.get("order_numbers") or [])
    max_orders = int(doc.get("max_orders") or 0)
    out = {
        "ok":            True,
        "armed":         True,
        "max_orders":    max_orders,
        "used":          len(used),
        "remaining":     max(0, max_orders - len(used)),
        "order_numbers": used,
        "pinned_order_number": doc.get("pinned_order_number"),
    }
    for k in ("armed_at", "last_reserved_at"):
        v = doc.get(k)
        if v is not None:
            out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    out["armed_by"] = doc.get("armed_by")
    return out


async def reserve_canary_budget(
    db, *, user_id: str, order_number: Optional[str],
) -> tuple[bool, str]:
    """Atomic, idempotent, fail-closed reservation.

    Returns (allowed, reason):
      (True,  "already_reserved")      — same order, second write
      (True,  "reserved")              — slot consumed now
      (False, "missing_order_number")  — no order context → refuse
      (False, "canary_budget_not_armed")
      (False, "canary_budget_exhausted")
    """
    order = str(order_number or "").strip()
    if not order:
        return False, "missing_order_number"

    doc = await db[BUDGET_COLLECTION].find_one({"user_id": user_id})
    if not doc:
        return False, "canary_budget_not_armed"
    # rev39 — pinned budget: ONLY the pinned order may reserve.
    pinned = doc.get("pinned_order_number")
    if pinned and order != str(pinned):
        return False, "order_not_pinned"
    if order in list(doc.get("order_numbers") or []):
        return True, "already_reserved"

    res = await db[BUDGET_COLLECTION].find_one_and_update(
        {
            "user_id":       user_id,
            "order_numbers": {"$ne": order},
            "$expr": {"$lt": [
                {"$size": {"$ifNull": ["$order_numbers", []]}},
                "$max_orders",
            ]},
        },
        {"$push": {"order_numbers": order},
         "$set":  {"last_reserved_at": _now()}},
        return_document=ReturnDocument.AFTER,
    )
    if res is not None:
        logger.warning(
            "rev35 canary_budget_reserved user_id=%s order=%s used=%s/%s",
            user_id, order, len(res.get("order_numbers") or []),
            res.get("max_orders"))
        return True, "reserved"

    # Lost the race or budget full — re-read to disambiguate.
    doc = await db[BUDGET_COLLECTION].find_one({"user_id": user_id}) or {}
    if order in list(doc.get("order_numbers") or []):
        return True, "already_reserved"
    return False, "canary_budget_exhausted"


async def is_order_reserved(
    db, *, user_id: str, order_number: Optional[str],
) -> bool:
    order = str(order_number or "").strip()
    if not order:
        return False
    doc = await db[BUDGET_COLLECTION].find_one(
        {"user_id": user_id, "order_numbers": order}, {"_id": 1})
    return doc is not None
