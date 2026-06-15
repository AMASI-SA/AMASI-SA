"""Iter-220 — BNPL Settlement Bridge (Phase 2b).

Closes Tabby/Tamara receivables when a weekly invoice is settled
into the merchant's bank account. Splits the gross receivable into
its accounting components:

    DEBIT  bank.{bank_account_id}                   = transferred_amount
    DEBIT  expense.bnpl_commission                  = commission       (if > 0)
    DEBIT  expense.bnpl_commission_vat              = vat              (if > 0)
    DEBIT  expense.bnpl_settlement_fee              = settlement_fee   (if > 0)
    CREDIT payment_gateway.{tabby|tamara}/receivable = sum_of_above

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
    if p not in ("tabby", "tamara"):
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
        "iter": "iter220",
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
