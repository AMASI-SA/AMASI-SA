"""BNPL Settlement ↔ Bank-Transfer Auto-Matching Engine (Phase 4-B).

For every weekly settlement invoice (from `compute_weekly_settlements`),
we try to find the actual bank transfer (recorded in `account_transactions`
with `transaction_type='internal_transfer'` & `direction='out'`) that
fulfilled it.

Algorithm (greedy, deterministic):
  • Iterate invoices in chronological order.
  • For each invoice, look at unconsumed OUT transfers from this
    provider's wallet with `transaction_date ∈ [invoice.to, invoice.to + WINDOW]`.
  • Score each candidate by amount delta vs `net_payable`.
  • Accept the best candidate if delta is within tolerance.
  • Mark it consumed so the next invoice won't reuse it.

Tolerance:
  WINDOW           = 14 days (BNPL pays weekly + bank lag)
  AMOUNT_PCT_TOL   = 2 %    (commission rounding, fees)
  AMOUNT_FLAT_TOL  = 3 SAR  (small fixed rounding)
  Effective tol    = max(net_payable * 2 %, 3 SAR)

Output is read-only — we never write to MongoDB.  Persistence will be
added in a follow-up iteration when manual overrides are supported.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from .settlements_service import (
    compute_weekly_settlements,
    _find_provider_account,
    PROVIDERS,
)


WINDOW_DAYS = 14
AMOUNT_PCT_TOL = 0.02   # 2 %
AMOUNT_FLAT_TOL = 3.00  # SAR


def _tolerance(net_payable: float) -> float:
    return max(abs(net_payable) * AMOUNT_PCT_TOL, AMOUNT_FLAT_TOL)


async def _fetch_provider_out_transfers(
    db, user_id: str, account_id: str,
) -> List[Dict[str, Any]]:
    """All OUT internal_transfer rows from the BNPL wallet, in date order."""
    cur = db.account_transactions.find(
        {
            "user_id": user_id,
            "account_id": account_id,
            "transaction_type": "internal_transfer",
            "direction": "out",
        },
        {"_id": 0, "id": 1, "amount": 1, "transaction_date": 1,
         "transfer_id": 1, "description": 1, "notes": 1,
         "peer_account_id": 1, "peer_account_name": 1,
         "created_at": 1},
    ).sort([("transaction_date", 1), ("created_at", 1)])
    return [d async for d in cur]


def _classify(net_payable: float, transfer_amount: float) -> str:
    """`matched` | `over` | `under` based on signed delta."""
    delta = transfer_amount - net_payable
    if abs(delta) <= _tolerance(net_payable):
        return "matched"
    return "over" if delta > 0 else "under"


async def compute_matches_for_provider(
    db, user_id: str, provider: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Match each weekly invoice with the best bank transfer.

    Returns
    -------
    {
        "provider": "tabby",
        "invoices": [
            {
                "invoice_no": 1,
                "from": "...", "to": "...",
                "net_payable": 1234.5,
                "match_status": "matched" | "unmatched" | "over" | "under",
                "matched_transfer": { id, transfer_id, amount,
                                      transaction_date, peer_account_name,
                                      delta } | None,
                "tolerance": 25.0,
            }, …
        ],
        "unmatched_transfers": [ … same shape as transfers … ],
        "totals": {
            "invoices_count":          int,
            "matched_count":           int,
            "unmatched_count":         int,
            "matched_amount":          float,
            "unmatched_invoice_total": float,
            "unmatched_transfer_total":float,
        },
        "linked_account_id":   str | None,
        "linked_account_name": str | None,
    }
    """
    if provider not in PROVIDERS:
        return {"provider": provider, "error": f"unknown provider {provider}"}

    weekly = await compute_weekly_settlements(
        db, user_id, provider, date_from, date_to,
    )
    account = await _find_provider_account(db, user_id, provider)
    if not account:
        return {
            "provider": provider,
            "invoices": [],
            "unmatched_transfers": [],
            "totals": {
                "invoices_count":          len(weekly),
                "matched_count":           0,
                "unmatched_count":         len(weekly),
                "matched_amount":          0.0,
                "unmatched_invoice_total": round(
                    sum(float(r.get("net_payable") or 0) for r in weekly), 2,
                ),
                "unmatched_transfer_total": 0.0,
            },
            "linked_account_id":   None,
            "linked_account_name": None,
            "error_hint": "لا يوجد حساب مزوّد مرتبط بهذا المزود.",
        }

    acc_id = account.get("id")
    transfers = await _fetch_provider_out_transfers(db, user_id, acc_id)
    consumed: set[str] = set()

    invoices_out: List[Dict[str, Any]] = []
    for r in weekly:
        net_payable = round(float(r.get("net_payable") or 0), 2)
        try:
            window_floor = date.fromisoformat(r["to"])
        except (ValueError, KeyError):
            window_floor = None
        window_ceil = (
            window_floor + timedelta(days=WINDOW_DAYS)
            if window_floor else None
        )
        tol = _tolerance(net_payable)

        # Build candidate list filtered by window + not consumed.
        candidates: List[Dict[str, Any]] = []
        for t in transfers:
            if t["id"] in consumed:
                continue
            tdate_raw = t.get("transaction_date")
            try:
                tdate = (
                    date.fromisoformat(tdate_raw[:10])
                    if isinstance(tdate_raw, str) else None
                )
            except ValueError:
                tdate = None
            if window_floor and tdate and tdate < window_floor:
                continue
            if window_ceil and tdate and tdate > window_ceil:
                continue
            candidates.append(t)

        # Pick best candidate by smallest |amount delta|, but only if
        # at least one is within tolerance.  If none within tolerance,
        # surface the closest as a 'near miss' so the merchant can
        # decide manually.
        best: Optional[Dict[str, Any]] = None
        best_abs = float("inf")
        for c in candidates:
            d = abs(float(c.get("amount") or 0) - net_payable)
            if d < best_abs:
                best_abs = d
                best = c

        if best and best_abs <= tol and net_payable > 0:
            consumed.add(best["id"])
            t_amt = round(float(best.get("amount") or 0), 2)
            invoices_out.append({
                **r,
                "match_status": "matched",
                "matched_transfer": {
                    "id":               best["id"],
                    "transfer_id":      best.get("transfer_id"),
                    "amount":           t_amt,
                    "transaction_date": best.get("transaction_date"),
                    "peer_account_id":  best.get("peer_account_id"),
                    "peer_account_name": best.get("peer_account_name"),
                    "description":      best.get("description"),
                    "delta":            round(t_amt - net_payable, 2),
                },
                "tolerance": round(tol, 2),
            })
        else:
            # No good match.  If a candidate exists within window but
            # outside tolerance, attach it as a 'near miss' so the
            # merchant can spot underpayments / duplicates.
            near = best if best else None
            status = "unmatched"
            near_payload = None
            if near and net_payable > 0:
                t_amt = round(float(near.get("amount") or 0), 2)
                delta = t_amt - net_payable
                status = _classify(net_payable, t_amt)
                # Don't consume near matches — leave for manual review.
                near_payload = {
                    "id":               near["id"],
                    "transfer_id":      near.get("transfer_id"),
                    "amount":           t_amt,
                    "transaction_date": near.get("transaction_date"),
                    "peer_account_id":  near.get("peer_account_id"),
                    "peer_account_name": near.get("peer_account_name"),
                    "description":      near.get("description"),
                    "delta":            round(delta, 2),
                }
            invoices_out.append({
                **r,
                "match_status": status,
                "matched_transfer": near_payload,
                "tolerance": round(tol, 2),
            })

    unmatched_transfers = [
        {
            "id":               t["id"],
            "transfer_id":      t.get("transfer_id"),
            "amount":           round(float(t.get("amount") or 0), 2),
            "transaction_date": t.get("transaction_date"),
            "peer_account_id":  t.get("peer_account_id"),
            "peer_account_name": t.get("peer_account_name"),
            "description":      t.get("description"),
        }
        for t in transfers if t["id"] not in consumed
    ]

    matched_inv = [i for i in invoices_out if i["match_status"] == "matched"]
    unmatched_inv = [i for i in invoices_out if i["match_status"] != "matched"]
    totals = {
        "invoices_count":           len(invoices_out),
        "matched_count":            len(matched_inv),
        "unmatched_count":          len(unmatched_inv),
        "matched_amount":           round(
            sum(i["matched_transfer"]["amount"] for i in matched_inv), 2,
        ),
        "unmatched_invoice_total":  round(
            sum(float(i.get("net_payable") or 0) for i in unmatched_inv), 2,
        ),
        "unmatched_transfer_total": round(
            sum(t["amount"] for t in unmatched_transfers), 2,
        ),
    }

    return {
        "provider":            provider,
        "invoices":            invoices_out,
        "unmatched_transfers": unmatched_transfers,
        "totals":              totals,
        "linked_account_id":   acc_id,
        "linked_account_name": account.get("name"),
        "window_days":         WINDOW_DAYS,
        "tolerance_doc": (
            f"المطابقة تقبل فرقاً ±{int(AMOUNT_PCT_TOL*100)}% من صافي المستحق "
            f"(بحد أدنى {AMOUNT_FLAT_TOL} ر.س) ضمن نافذة {WINDOW_DAYS} يوماً "
            f"بعد نهاية الأسبوع."
        ),
    }


async def compute_matches_all_providers(
    db, user_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> Dict[str, Any]:
    """Run matching for every BNPL provider in one call."""
    out: Dict[str, Any] = {"providers": {}}
    for p in PROVIDERS:
        out["providers"][p] = await compute_matches_for_provider(
            db, user_id, p, date_from, date_to,
        )
    return out
