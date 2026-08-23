"""Iter-219 — BNPL → general_ledger bridge (Phase 2a: sales + refunds).

Bridges new Tabby/Tamara/Emkan `payment_transactions` and `payment_refunds`
events into the Universal Ledger (SSOT) without touching historical
data. Every poster is idempotent via the
`metadata.idempotency_key` field on each leg.

Public:
    post_bnpl_sale_to_ledger(db, *, user_id, txn)
    post_bnpl_refund_to_ledger(db, *, user_id, refund)

Both return:
    {"ok": True, "txn_group_id": <id>}                — newly posted
    {"ok": True, "skipped": True, "reason": "..."}     — idempotent no-op

Conventions (matches the audit proposal):

  • Sale  →  DEBIT  payment_gateway.{tabby|tamara|emkan}/receivable = amount
            CREDIT revenue.bnpl_sales                          = amount

  • Refund → DEBIT  revenue.bnpl_sales                          = amount
            CREDIT payment_gateway.{tabby|tamara|emkan}/receivable = amount

Where  `entity_id` for the payment_gateway leg = the provider name
(`tabby` / `tamara` / `emkan`) and `sub_account = "receivable"`. This matches
the same entity-naming scheme used by `_find_provider_account` and
`compute_settlement_for_provider` so reports keep working.

Phase 2b (NOT in this iteration) will hook bank-transfer postings and
break out commission/VAT/fee expenses. Sales + refunds are by far the
hottest paths (Tabby webhook fires per order capture) so we close
them first.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Statuses that mean "money committed by buyer" — only these get
# booked as revenue. Tabby/Tamara emit several intermediate states
# (created/authorized/etc.) that should NOT yet hit the ledger.
SALE_BOOK_STATUSES = {
    # Tabby
    "closed",            # fully captured + completed
    "captured",          # partial/full capture confirmed
    "authorized",        # captured authorization
    "fully_captured",    # tabby alternate
    # Tamara
    "approved",                # settled approval
    "completed",               # completed
    "partially_captured",      # tamara partial capture
    "shipped",                 # tamara order shipped
    "fully_shipped",           # tamara order shipped
}


def _cutoff_iso() -> Optional[str]:
    """Deployment cutoff — bridge skips any transaction whose
    `created_at_provider` falls strictly before this ISO timestamp.

    Set via env `BNPL_BRIDGE_CUTOFF_ISO` (e.g. "2026-02-12T00:00:00Z").
    If unset / empty → no cutoff applied (bridge books everything that
    passes the other guards).
    """
    raw = (os.environ.get("BNPL_BRIDGE_CUTOFF_ISO") or "").strip()
    return raw or None


def _before_cutoff(created_at_iso: Optional[str]) -> bool:
    cutoff = _cutoff_iso()
    if not cutoff:
        return False
    if not created_at_iso:
        # Unknown date — be conservative, treat as historical.
        return True
    # Lexical ISO-8601 comparison is correct for full timestamps.
    return str(created_at_iso) < cutoff


def _norm_provider(txn: dict) -> str:
    p = (txn.get("provider") or "").lower().strip()
    if p == "imkan":
        p = "emkan"
    return p if p in ("tabby", "tamara", "emkan") else (p or "unknown")


async def _already_posted(db, user_id: str, idem_key: str) -> bool:
    doc = await db.general_ledger.find_one(
        {"user_id": user_id,
         "metadata.idempotency_key": idem_key,
         "status": "posted"},
        {"_id": 1},
    )
    return doc is not None


async def post_bnpl_sale_to_ledger(
    db, *, user_id: str, txn: dict,
) -> dict[str, Any]:
    """Book a single Tabby/Tamara/Emkan sale into general_ledger.

    `txn` is a `payment_transactions` document (post-upsert).
    No-op for unsupported statuses or zero amounts. Safe to call
    after every upsert — re-runs short-circuit via the idem key.
    """
    provider = _norm_provider(txn)
    status = (txn.get("status") or "").lower()
    provider_id = (txn.get("provider_id") or "").strip()
    amount = float(txn.get("amount") or 0)
    if provider not in ("tabby", "tamara", "emkan"):
        return {"ok": True, "skipped": True, "reason": "unknown_provider"}
    if not provider_id:
        return {"ok": True, "skipped": True, "reason": "missing_provider_id"}
    if amount <= 0:
        return {"ok": True, "skipped": True, "reason": "zero_amount"}
    if status not in SALE_BOOK_STATUSES:
        return {"ok": True, "skipped": True,
                "reason": f"status_not_bookable:{status}"}
    if provider == "emkan" and not _cutoff_iso():
        # Emkan is newly enabled by statement evidence. Do not let the
        # settlement catch-up scan create historical receivables until the
        # owner approves an explicit post-cutover timestamp.
        return {"ok": True, "skipped": True,
                "reason": "missing_bridge_cutoff"}
    if _before_cutoff(txn.get("created_at_provider")):
        return {"ok": True, "skipped": True,
                "reason": "before_bridge_cutoff"}

    idem = f"bnpl_sale:{provider}:{provider_id}"
    if await _already_posted(db, user_id, idem):
        return {"ok": True, "skipped": True,
                "reason": "idempotent_duplicate"}

    from ledger_core import post_txn_group
    order_ref = (txn.get("order_reference_id")
                  or txn.get("order_number") or "")
    description = (
        f"بيع {provider.capitalize()} — طلب {order_ref or provider_id}"
    )
    grp = await post_txn_group(
        db, user_id=user_id, actor_id=user_id,
        actor_name="bnpl_bridge",
        txn_type="bnpl_sale", notes=description,
        metadata={
            "provider": provider,
            "provider_id": provider_id,
            "order_reference_id": order_ref,
            "payment_transaction_id": txn.get("id"),
            "amount": round(amount, 2),
            "idempotency_key": idem,
            "iter": "iter219",
        },
        entries=[
            {"entity_type": "payment_gateway", "entity_id": provider,
             "sub_account": "receivable", "side": "debit",
             "amount": amount, "entry_type": "bnpl_sale",
             "notes": f"مديونية {provider} — {order_ref or provider_id}"},
            {"entity_type": "revenue", "entity_id": "bnpl_sales",
             "side": "credit", "amount": amount,
             "entry_type": "bnpl_sale",
             "notes": f"إيراد بيع {provider}"},
        ],
    )
    return {"ok": True, "txn_group_id": grp.get("txn_group_id")}


async def post_bnpl_refund_to_ledger(
    db, *, user_id: str, refund: dict,
) -> dict[str, Any]:
    """Book a single Tabby/Tamara/Emkan refund into general_ledger.

    `refund` is a `payment_refunds` document. Idempotent on
    (provider, provider_refund_id).
    """
    provider = _norm_provider(refund)
    refund_id = (refund.get("provider_refund_id") or "").strip()
    amount = float(refund.get("amount") or 0)
    if provider not in ("tabby", "tamara", "emkan"):
        return {"ok": True, "skipped": True, "reason": "unknown_provider"}
    if not refund_id:
        return {"ok": True, "skipped": True,
                "reason": "missing_refund_id"}
    if amount <= 0:
        return {"ok": True, "skipped": True, "reason": "zero_amount"}
    if provider == "emkan" and not _cutoff_iso():
        return {"ok": True, "skipped": True,
                "reason": "missing_bridge_cutoff"}
    if _before_cutoff(refund.get("refunded_at")
                       or refund.get("created_at_provider")):
        return {"ok": True, "skipped": True,
                "reason": "before_bridge_cutoff"}

    # A refund should only be booked when its underlying sale has been
    # booked. Otherwise we'd post a credit to a receivable that was
    # never debited — turning the receivable negative.
    payment_id = (refund.get("provider_payment_id") or "").strip()
    if payment_id:
        sale_idem = f"bnpl_sale:{provider}:{payment_id}"
        if not await _already_posted(db, user_id, sale_idem):
            return {"ok": True, "skipped": True,
                    "reason": "underlying_sale_not_in_ledger"}

    idem = f"bnpl_refund:{provider}:{refund_id}"
    if await _already_posted(db, user_id, idem):
        return {"ok": True, "skipped": True,
                "reason": "idempotent_duplicate"}

    from ledger_core import post_txn_group
    order_ref = (refund.get("order_reference_id")
                  or refund.get("provider_payment_id") or "")
    description = (
        f"استرجاع {provider.capitalize()} — طلب {order_ref or refund_id}"
    )
    grp = await post_txn_group(
        db, user_id=user_id, actor_id=user_id,
        actor_name="bnpl_bridge",
        txn_type="bnpl_refund", notes=description,
        metadata={
            "provider": provider,
            "provider_refund_id": refund_id,
            "provider_payment_id": refund.get("provider_payment_id"),
            "order_reference_id": order_ref,
            "refund_doc_id": refund.get("id"),
            "amount": round(amount, 2),
            "idempotency_key": idem,
            "iter": "iter219",
        },
        entries=[
            {"entity_type": "revenue", "entity_id": "bnpl_sales",
             "side": "debit", "amount": amount,
             "entry_type": "bnpl_refund",
             "notes": f"عكس إيراد {provider} (استرجاع)"},
            {"entity_type": "payment_gateway", "entity_id": provider,
             "sub_account": "receivable", "side": "credit",
             "amount": amount, "entry_type": "bnpl_refund",
             "notes": f"تخفيض مديونية {provider}"},
        ],
    )
    return {"ok": True, "txn_group_id": grp.get("txn_group_id")}


async def safe_post_sale(db, *, user_id: str, txn: dict) -> None:
    """Wrap `post_bnpl_sale_to_ledger` so sync flows never break on a
    bridge error — failures are logged but don't roll back the
    `payment_transactions` upsert. Next sync retries via idempotency
    on a fresh attempt."""
    try:
        await post_bnpl_sale_to_ledger(db, user_id=user_id, txn=txn)
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "iter-219: sale bridge failed for %s/%s: %s",
            (txn or {}).get("provider"),
            (txn or {}).get("provider_id"), e,
        )


async def safe_post_refund(db, *, user_id: str, refund: dict) -> None:
    try:
        await post_bnpl_refund_to_ledger(
            db, user_id=user_id, refund=refund,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "iter-219: refund bridge failed for %s/%s: %s",
            (refund or {}).get("provider"),
            (refund or {}).get("provider_refund_id"), e,
        )


__all__ = [
    "post_bnpl_sale_to_ledger",
    "post_bnpl_refund_to_ledger",
    "safe_post_sale",
    "safe_post_refund",
]
