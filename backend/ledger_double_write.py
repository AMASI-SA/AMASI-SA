"""Iter-240 — Double-write helper: account_transactions → general_ledger.

Mirrors a newly-inserted account_transactions row into the SSOT
`general_ledger` so the `/accounts` page (and every other ledger-driven
view) reflects manual transfers, payments, expenses, etc.

Strictly forward-only:
  • Operates ONLY on rows passed to it (call sites: transfers, payments,
    expenses, ad-account topups).
  • NEVER touches historical rows.
  • Idempotent: skips insert if a ledger entry with the same
    `metadata.account_transaction_id` already exists.

Each mirrored ledger row carries:
  metadata.account_transaction_id   ← unique link
  metadata.source = "account_transaction_double_write"
  metadata.transaction_type
  metadata.idempotency_key          ← copied from txn metadata if any
  metadata.created_by_endpoint
  metadata.iter = "iter240"
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _r(v) -> float:
    return round(float(v or 0), 2)


async def mirror_account_txn_to_ledger(
    db,
    *,
    user_id: str,
    account_id: str,
    account_transaction_id: str,
    amount: float,
    direction: str,    # "in" | "out"
    transaction_type: str,
    transaction_date: str | None = None,
    description: str = "",
    counter_entity_type: str = "expense",
    counter_entity_id: str = "manual_uncategorised",
    created_by_endpoint: str = "unknown",
    idempotency_key: str | None = None,
    paired_account_transaction_id: str | None = None,
    currency: str = "SAR",
) -> dict:
    """Idempotent mirror of one account_transaction row into general_ledger.

    Posts a BALANCED double-entry pair:
      • direction="in"  → DEBIT bank entity, CREDIT counter entity
      • direction="out" → CREDIT bank entity, DEBIT counter entity

    For internal transfers (bank → bank) pass:
      counter_entity_type="bank"
      counter_entity_id=<peer bank account id>
      paired_account_transaction_id=<peer account_transactions.id>

    The paired id is stored in metadata so the health endpoint can
    consider BOTH legs of a transfer as already mirrored without
    double-posting.

    Returns: { "skipped": bool, "txn_group_id": str, "reason"?: str }
    """
    if not account_transaction_id or amount is None or amount == 0:
        return {"skipped": True, "reason": "missing id or zero amount"}

    # ── Idempotency guard ────────────────────────────────────────────
    # Match on either side of the pair (covers re-entry from peer row).
    or_clauses = [
        {"metadata.account_transaction_id": account_transaction_id},
        {"metadata.paired_account_transaction_id": account_transaction_id},
    ]
    if paired_account_transaction_id:
        or_clauses.extend([
            {"metadata.account_transaction_id": paired_account_transaction_id},
            {"metadata.paired_account_transaction_id":
             paired_account_transaction_id},
        ])
    existing = await db.general_ledger.find_one(
        {"user_id": user_id, "$or": or_clauses},
        {"_id": 0, "txn_group_id": 1},
    )
    if existing:
        return {
            "skipped": True,
            "reason": "ledger entry already exists for this txn",
            "txn_group_id": existing.get("txn_group_id"),
        }

    amt = _r(abs(float(amount)))
    is_in = (str(direction or "").lower() == "in")
    txn_group_id = str(uuid.uuid4())
    now = _now_iso()
    posted_at = (
        f"{transaction_date}T00:00:00+00:00"
        if transaction_date
        else now
    )

    # Allocate two consecutive monotonic entry numbers (the existing
    # `(user_id, entry_no)` unique index requires every row to carry
    # a non-null, distinct entry_no per user).
    res = await db.general_ledger.aggregate([
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": None, "mx": {"$max": "$entry_no"}}},
    ]).to_list(1)
    cur = int(res[0]["mx"]) if res and res[0].get("mx") is not None else 0
    entry_no_bank = cur + 1
    entry_no_counter = cur + 2

    meta = {
        "account_transaction_id": account_transaction_id,
        "paired_account_transaction_id": paired_account_transaction_id,
        "source": "account_transaction_double_write",
        "transaction_type": transaction_type,
        "idempotency_key": idempotency_key,
        "created_by_endpoint": created_by_endpoint,
        "iter": "iter240",
    }
    base = {
        "user_id": user_id,
        "txn_group_id": txn_group_id,
        "posted_at": posted_at,
        "status": "posted",
        "amount": amt,
        "currency": currency or "SAR",
        "entry_type": f"manual_{transaction_type}",
        "notes": description or f"{transaction_type} — {direction}",
        "metadata": meta,
        "created_at": now,
        "updated_at": now,
    }
    bank_leg = {
        **base,
        "id": str(uuid.uuid4()),
        "entry_no": entry_no_bank,
        "entity_type": "bank",
        "entity_id": account_id,
        "sub_account": "main",
        "side": "debit" if is_in else "credit",
    }
    counter_leg = {
        **base,
        "id": str(uuid.uuid4()),
        "entry_no": entry_no_counter,
        "entity_type": counter_entity_type,
        "entity_id": counter_entity_id,
        "sub_account": "main",
        "side": "credit" if is_in else "debit",
    }
    await db.general_ledger.insert_many([bank_leg, counter_leg])
    return {"skipped": False, "txn_group_id": txn_group_id}
