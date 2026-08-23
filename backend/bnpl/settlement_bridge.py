"""Iter-220 — BNPL Settlement Bridge (Phase 2b).

Closes Tabby/Tamara/Emkan receivables when a provider statement is settled
into the merchant's bank account. Splits the gross receivable into
its accounting components:

    DEBIT  bank.{bank_account_id}                   = transferred_amount
    DEBIT  expense.bnpl_commission                  = commission       (if > 0)
    DEBIT  expense.bnpl_commission_vat              = vat              (if > 0)
    DEBIT  expense.bnpl_settlement_fee              = settlement_fee   (if > 0)
    CREDIT payment_gateway.{tabby|tamara|emkan}/receivable = sum_of_above

Strict invariants:
    1. NO HISTORICAL BACKFILL — new settlements only (post-deployment).
    2. The CREDIT (close-out) cannot exceed the current ledger
       receivable. Partial settlements are allowed; over-settlements
       are REJECTED.
    3. Idempotent via `bnpl_settlement:{provider}:{settlement_reference}`.
    4. Regular (non-BNPL) bank transfers are untouched — this bridge
       is invoked explicitly via the dedicated endpoint.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def _norm_provider(p: str) -> str:
    p = (p or "").lower().strip()
    if p == "imkan":
        p = "emkan"
    if p not in ("tabby", "tamara", "emkan"):
        raise HTTPException(400, f"unsupported provider: {p}")
    return p


def _round(v) -> float:
    return round(float(v or 0), 2)


async def _already_posted(db, user_id: str, idem_key: str) -> Optional[str]:
    doc = await db.general_ledger.find_one(
        {"user_id": user_id,
         "metadata.idempotency_key": idem_key,
         "status": "posted"},
        {"_id": 0, "txn_group_id": 1},
    )
    return doc.get("txn_group_id") if doc else None


async def _current_receivable(
    db, *, user_id: str, provider: str,
) -> float:
    from ledger_core import compute_balance
    bal = await compute_balance(
        db, user_id=user_id, entity_type="payment_gateway",
        entity_id=provider, sub_account="receivable",
    )
    # `net_balance` > 0 means the provider OWES us that amount.
    return _round(bal.get("net_balance", 0))


async def _ensure_bridge_caught_up(
    db, *, user_id: str, provider: str,
) -> dict[str, int]:
    """Iter-246x — Before validating the receivable, force-mirror any
    payment_transactions / payment_refunds that the BNPL bridge has
    not yet posted to general_ledger. This eliminates the timing
    error where the merchant sees "settlement exceeds receivable"
    purely because the bridge hadn't caught up to the latest sync.

    Idempotent: every `safe_post_*` call short-circuits on its own
    idempotency key, so calling this twice in a row is a no-op the
    second time.
    """
    from .ledger_bridge import safe_post_sale, safe_post_refund

    posted_sales = 0
    posted_refunds = 0

    async for txn in db.payment_transactions.find(
        {"user_id": user_id, "provider": provider,
         "is_pre_accounting": {"$ne": True}},
        {"_id": 0},
    ):
        try:
            await safe_post_sale(db, user_id=user_id, txn=txn)
            posted_sales += 1
        except Exception:  # noqa: BLE001
            pass

    async for rfd in db.payment_refunds.find(
        {"user_id": user_id, "provider": provider,
         "is_pre_accounting": {"$ne": True}},
        {"_id": 0},
    ):
        try:
            await safe_post_refund(db, user_id=user_id, refund=rfd)
            posted_refunds += 1
        except Exception:  # noqa: BLE001
            pass

    return {"sales_scanned": posted_sales,
            "refunds_scanned": posted_refunds}


async def _find_existing_period_settlement(
    db, *, user_id: str, provider: str,
    period_from: Optional[str], period_to: Optional[str],
) -> Optional[dict]:
    """Iter-246x — Detect a previously-saved settlement covering the
    EXACT same (provider, period_from, period_to).  Returns the
    existing settlement summary or None.

    Match is performed on general_ledger metadata of any posted
    `bnpl_settlement` txn_group.
    """
    if not (period_from and period_to):
        return None

    async for e in db.general_ledger.find(
        {"user_id": user_id,
         "entry_type": "bnpl_settlement",
         "status": "posted",
         "metadata.provider": provider,
         "metadata.period_from": period_from,
         "metadata.period_to": period_to},
        {"_id": 0, "txn_group_id": 1, "metadata": 1,
         "transaction_date": 1, "created_at": 1},
    ):
        md = e.get("metadata") or {}
        return {
            "txn_group_id": e.get("txn_group_id"),
            "settlement_reference": md.get("settlement_reference"),
            "settlement_date": md.get("settlement_date"),
            "transferred_amount": md.get("transferred_amount"),
            "transaction_date": e.get("transaction_date"),
            "created_at": e.get("created_at"),
        }
    return None


# Iter-246x — Official invoice-issuance weekday per provider.
# Iter-246z — Centralised in `bnpl/timezone.py` (Asia/Riyadh SSOT).
from .timezone import (
    INVOICE_WEEKDAY as _INVOICE_WEEKDAY,
    WEEKDAY_AR as _WEEKDAY_AR,
    earliest_save_date_for_period as _earliest_save_date_for_period,
    today_riyadh_iso as _today_riyadh_iso,
)


async def post_bnpl_settlement_to_ledger(
    db, *, user_id: str, actor_id: str, actor_name: str,
    provider: str,
    bank_account_id: str,
    transferred_amount: float,
    commission: float = 0.0,
    commission_vat: float = 0.0,
    settlement_fee: float = 0.0,
    settlement_reference: str,
    settlement_date: Optional[str] = None,
    notes: str = "",
    extra_metadata: Optional[dict] = None,
    # Iter-246x — explicit period for SSOT duplicate guard.
    period_from: Optional[str] = None,
    period_to: Optional[str] = None,
) -> dict[str, Any]:
    """Post a balanced settlement group to general_ledger.

    Returns:
        {
          "ok": True,
          "txn_group_id": str,
          "total_closed": float,   # = transferred + commission + vat + fee
          "remaining_receivable": float,
          "skipped": False,
        }
    On idempotent re-post:
        {"ok": True, "skipped": True, "reason": "idempotent_duplicate",
         "txn_group_id": <existing>}
    """
    provider = _norm_provider(provider)
    transferred = _round(transferred_amount)
    commission = _round(commission)
    commission_vat = _round(commission_vat)
    settlement_fee = _round(settlement_fee)
    total = _round(transferred + commission + commission_vat + settlement_fee)

    if not settlement_reference or not settlement_reference.strip():
        raise HTTPException(400, "settlement_reference إلزامي")
    settlement_reference = settlement_reference.strip()

    if total <= 0:
        raise HTTPException(400, "إجمالي التسوية يجب أن يكون موجباً")
    if transferred < 0 or commission < 0 or commission_vat < 0 or settlement_fee < 0:
        raise HTTPException(400, "جميع المبالغ يجب أن تكون ≥ 0")

    # Idempotency — short-circuit before any DB write or balance check.
    idem = f"bnpl_settlement:{provider}:{settlement_reference}"
    existing = await _already_posted(db, user_id, idem)
    if existing:
        return {
            "ok": True, "skipped": True,
            "reason": "idempotent_duplicate",
            "txn_group_id": existing,
        }

    # Bank account must exist for this user.
    bank = await db.accounts.find_one(
        {"id": bank_account_id, "user_id": user_id},
        {"_id": 0, "id": 1, "account_type": 1, "name": 1},
    )
    if not bank:
        raise HTTPException(404, "حساب البنك غير موجود")
    if (bank.get("account_type") or "").lower() not in ("bank", "cash"):
        raise HTTPException(
            400,
            "يجب أن يكون الحساب الوجهة من نوع bank أو cash",
        )

    # Iter-246x — Period duplicate guard.  Refuse to re-post a
    # settlement for the SAME (provider, period_from, period_to).
    if period_from and period_to:
        dup = await _find_existing_period_settlement(
            db, user_id=user_id, provider=provider,
            period_from=period_from, period_to=period_to,
        )
        if dup:
            raise HTTPException(
                409,
                "توجد تسوية مسجلة مسبقاً لهذا المزود وهذه الفترة. "
                f"المرجع السابق: {dup.get('settlement_reference')} — "
                f"بتاريخ {dup.get('settlement_date') or dup.get('transaction_date')} — "
                f"رقم القيد: {dup.get('txn_group_id')}.",
            )

    # Iter-246x — Invoice-issuance weekday guard.  Refuse to save a
    # settlement BEFORE the provider has officially issued its invoice
    # for the period (Tamara=Saturday, Tabby=Monday, Asia/Riyadh).
    # Saving earlier would post provisional numbers to the books.
    if period_to and provider in _INVOICE_WEEKDAY:
        eligible_iso = _earliest_save_date_for_period(provider, period_to)
        today_iso = _today_riyadh_iso()
        if today_iso < eligible_iso:
            wd = _INVOICE_WEEKDAY[provider]
            day_ar = _WEEKDAY_AR.get(wd, "")
            raise HTTPException(
                400,
                "لا يمكن إنشاء تسوية هذا الأسبوع قبل صدور فاتورة المزود "
                "الرسمية. تمارا تصدر يوم السبت، وتابي تصدر يوم الاثنين. "
                f"({provider.capitalize()} → التاريخ المتاح للتسجيل: "
                f"{eligible_iso} — {day_ar})",
            )

    # Iter-246x — Force the BNPL ledger bridge to catch up before we
    # validate the receivable.  Eliminates the timing race where the
    # merchant sees "settlement exceeds receivable" purely because the
    # bridge hasn't yet posted today's newer sales.  Idempotent.
    try:
        await _ensure_bridge_caught_up(
            db, user_id=user_id, provider=provider)
    except Exception:  # noqa: BLE001 — never block on the catch-up itself
        pass

    # Don't close more than what the receivable holds — guards
    # against a refund-only ledger producing a negative receivable.
    receivable = await _current_receivable(db, user_id=user_id, provider=provider)
    if receivable <= 0:
        raise HTTPException(
            400,
            (
                f"لا يوجد رصيد مستحق على {provider} في الدفتر "
                f"(receivable={receivable}). "
                "تأكد من مزامنة المبيعات بعد cutoff الجسر قبل تسجيل التسوية."
            ),
        )
    if total - receivable > 0.01:
        raise HTTPException(
            400,
            (
                f"إجمالي التسوية ({total}) يتجاوز الرصيد المستحق "
                f"({receivable}) على {provider}. "
                "خفّض المبالغ أو تأكد من مزامنة المبيعات."
            ),
        )

    # Build the balanced double-entry. Skip zero-amount expense legs
    # to avoid clutter — they violate `amount > 0` validation in
    # post_ledger_entry anyway.
    description_ar = (
        f"تسوية {provider.capitalize()} — مرجع {settlement_reference}"
    )
    entries: list[dict] = [
        {"entity_type": "bank", "entity_id": bank_account_id,
         "sub_account": "balance", "side": "debit",
         "amount": transferred, "entry_type": "bnpl_settlement",
         "notes": f"تحويل من {provider} إلى البنك"},
    ] if transferred > 0 else []

    if commission > 0:
        entries.append({
            "entity_type": "expense", "entity_id": "bnpl_commission",
            "side": "debit", "amount": commission,
            "entry_type": "bnpl_settlement",
            "notes": f"عمولة {provider}",
        })
    if commission_vat > 0:
        entries.append({
            "entity_type": "expense", "entity_id": "bnpl_commission_vat",
            "side": "debit", "amount": commission_vat,
            "entry_type": "bnpl_settlement",
            "notes": f"ضريبة عمولة {provider}",
        })
    if settlement_fee > 0:
        entries.append({
            "entity_type": "expense", "entity_id": "bnpl_settlement_fee",
            "side": "debit", "amount": settlement_fee,
            "entry_type": "bnpl_settlement",
            "notes": f"رسوم تسوية {provider}",
        })

    # CREDIT close-out leg
    entries.append({
        "entity_type": "payment_gateway", "entity_id": provider,
        "sub_account": "receivable", "side": "credit",
        "amount": total, "entry_type": "bnpl_settlement",
        "notes": f"إغلاق ذمم {provider}",
    })

    if len(entries) < 2:
        # All zero except total — shouldn't happen given the guards
        # above, but defensive.
        raise HTTPException(400, "لا توجد بنود فعلية في التسوية")

    from ledger_core import post_txn_group
    metadata = {
        "provider": provider,
        "transferred_amount": transferred,
        "commission": commission,
        "commission_vat": commission_vat,
        "settlement_fee": settlement_fee,
        "settlement_reference": settlement_reference,
        "settlement_date": settlement_date or "",
        "bank_account_id": bank_account_id,
        "bank_account_name": bank.get("name") or "",
        "idempotency_key": idem,
        "iter": "iter246x",
        # Iter-246x — persist the period so future duplicate guards
        # can read it back directly from the ledger.
        "period_from": period_from or "",
        "period_to": period_to or "",
        **(extra_metadata or {}),
    }
    grp = await post_txn_group(
        db, user_id=user_id, actor_id=actor_id,
        actor_name=actor_name or "bnpl_settlement_bridge",
        txn_type="bnpl_settlement",
        notes=notes or description_ar,
        metadata=metadata,
        entries=entries,
    )

    # Iter-248 — Forward fix: ALSO mirror a single row into
    # `account_transactions` on the receiving bank so the bank
    # statement UI surfaces the inflow.  IMPORTANT:
    #   • No new ledger entry — ledger already posted above.
    #   • No `current_balance` mutation — it's computed from ledger.
    #   • No call to `mirror_account_txn_to_ledger` (would duplicate).
    # Idempotency: skipped if a row with the same bank-txn idem key
    # already exists.
    bank_txn_idem = (
        f"bnpl_settlement_bank_txn:{provider}:{settlement_reference}")
    existing = await db.account_transactions.find_one(
        {"user_id": user_id, "idempotency_key": bank_txn_idem})
    if not existing and transferred > 0:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        await db.account_transactions.insert_one({
            "id": str(__import__("uuid").uuid4()),
            "user_id": user_id,
            "account_id": bank_account_id,
            "account_name": bank.get("name") or "",
            "direction": "in",
            "transaction_type": "bnpl_settlement",
            "amount": transferred,
            "transaction_date": settlement_date or "",
            "reference": settlement_reference,
            "txn_group_id": grp.get("txn_group_id"),
            "provider": provider,
            "description":
                f"تسوية {provider} - {settlement_reference}",
            "idempotency_key": bank_txn_idem,
            "status": "posted",
            "balance_after": 0.0,
            "created_at": now_iso,
            "updated_at": now_iso,
        })

    remaining = _round(receivable - total)
    return {
        "ok": True,
        "skipped": False,
        "txn_group_id": grp.get("txn_group_id"),
        "total_closed": total,
        "remaining_receivable": remaining,
        "description": description_ar,
    }


__all__ = ["post_bnpl_settlement_to_ledger"]
