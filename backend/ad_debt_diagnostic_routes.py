"""Iter-230 — Ad Account Debt SSOT Reconciliation (READ-ONLY Phase 1).

Diagnostic only. ZERO writes. ZERO modifications. ZERO archiving.

Compares per ad_account:
  • walk_balance: from `ad_account_ledger` (legacy collection) using the
    same walk logic as `/api/ad-accounts` (ad_account_routes._summarise)
  • ssot_balance: from `general_ledger` aggregating `ad_account.debt`
    entries (credit − debit, post Iter-226 filters)

Mounted at: GET /api/audit/ad-debt-diagnostic
"""
from __future__ import annotations
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends


def _r(v) -> float:
    return round(float(v or 0), 2)


def make_ad_debt_diagnostic_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit"])

    async def _walk_debt(uid: str, cp_id: str) -> float:
        """Replicates `_summarise` debt math from ad_account_routes."""
        balance_walk = 0.0
        debt_walk = 0.0
        async for row in db.ad_account_ledger.find(
            {"user_id": uid, "counterparty_id": cp_id},
            {"_id": 0, "type": 1, "amount": 1},
        ).sort([("date", 1), ("created_at", 1)]):
            ev = row.get("type")
            amt = float(row.get("amount") or 0)
            if ev == "topup":
                to_debt = min(debt_walk, amt) if amt > 0 else 0.0
                debt_walk = max(0.0, debt_walk - to_debt)
                balance_walk += (amt - to_debt)
            elif ev == "opening":
                balance_walk += amt
            elif ev == "opening_debt":
                debt_walk += amt
            elif ev == "spend":
                if amt > 0:
                    take = min(balance_walk, amt)
                    balance_walk -= take
                    debt_walk += (amt - take)
                else:
                    cover = min(debt_walk, -amt)
                    debt_walk -= cover
                    balance_walk += (-amt - cover)
            elif ev in ("settlement", "writeoff"):
                debt_walk = max(0.0, debt_walk - amt)
        return _r(debt_walk)

    async def _ssot_debt_breakdown(uid: str, cp_id: str) -> dict:
        """Aggregate general_ledger `ad_account.debt` for this account
        BY entry_type, separately listing the contributing entries."""
        out: dict = {
            "total_credit": 0.0, "total_debit": 0.0, "net": 0.0,
            "by_entry_type": defaultdict(
                lambda: {"credit": 0.0, "debit": 0.0, "count": 0},
            ),
            "entries": [],
            "archived_count": 0,
            "archived_net": 0.0,
        }
        # NOTE: We READ ALL entries (including legacy_orphan) so the
        # diagnostic shows the full picture. Live SSOT filters archived
        # ones — but here we want to see EVERYTHING.
        async for e in db.general_ledger.find(
            {"user_id": uid,
             "entity_type": "ad_account",
             "entity_id": cp_id,
             "sub_account": "debt",
             "status": "posted"},
            {"_id": 0, "id": 1, "entry_type": 1, "side": 1,
             "amount": 1, "posted_at": 1, "txn_group_id": 1,
             "notes": 1, "metadata": 1},
        ):
            amt = float(e.get("amount") or 0)
            etype = e.get("entry_type") or "unknown"
            side = e.get("side")
            md = e.get("metadata") or {}
            is_archived = bool(md.get("legacy_orphan"))
            is_reversal = (etype == "reversal")

            # Mirror the SSOT live-balance computation EXACTLY:
            # • status=posted (already filtered)
            # • entry_type != reversal
            # • metadata.legacy_orphan != True
            # → contributes to net
            contributes_to_ssot = (not is_reversal) and (not is_archived)

            if contributes_to_ssot:
                if side == "credit":
                    out["total_credit"] += amt
                elif side == "debit":
                    out["total_debit"] += amt
                bucket = out["by_entry_type"][etype]
                bucket[side] = bucket.get(side, 0) + amt
                bucket["count"] += 1
            if is_archived:
                out["archived_count"] += 1
                sign = 1 if side == "credit" else -1
                out["archived_net"] += sign * amt

            out["entries"].append({
                "ledger_id": e.get("id"),
                "txn_group_id": e.get("txn_group_id"),
                "entry_type": etype,
                "side": side,
                "amount": _r(amt),
                "posted_at": str(e.get("posted_at") or ""),
                "notes": e.get("notes"),
                "metadata_source": (
                    md.get("source") or md.get("origin") or ""
                ),
                "is_reversal": is_reversal,
                "is_archived": is_archived,
                "contributes_to_ssot": contributes_to_ssot,
            })

        # SSOT debt convention: credit increases debt, debit reduces it.
        # net liability shown in financial position = credit − debit.
        out["net"] = _r(out["total_credit"] - out["total_debit"])
        out["total_credit"] = _r(out["total_credit"])
        out["total_debit"] = _r(out["total_debit"])
        out["archived_net"] = _r(out["archived_net"])
        out["by_entry_type"] = {
            k: {"credit": _r(v["credit"]), "debit": _r(v["debit"]),
                "count": v["count"]}
            for k, v in out["by_entry_type"].items()
        }
        return out

    @router.get("/ad-debt-diagnostic")
    async def ad_debt_diagnostic(user: dict = Depends(current_user)):
        uid = user["id"]
        results: list[dict] = []
        global_attrib: dict = defaultdict(float)

        async for cp in db.ad_accounts.find(
            {"user_id": uid},
            {"_id": 0, "id": 1, "name": 1, "platform": 1,
             "status": 1},
        ):
            cp_id = cp.get("id")
            if not cp_id:
                continue
            walk = await _walk_debt(uid, cp_id)
            ssot = await _ssot_debt_breakdown(uid, cp_id)
            ssot_net = ssot["net"]
            diff = _r(ssot_net - walk)

            # Attribute the per-account difference by entry_type.
            # Net SSOT change per entry_type = credit − debit.
            # Walk attribution: assume walk's "expected" credit per
            # entry_type matches SSOT credit minus duplicates. Since we
            # cannot know walk's exact bucket allocation, we expose the
            # raw SSOT breakdown so the user can spot anomalies.
            for et, b in ssot["by_entry_type"].items():
                global_attrib[et] += (b["credit"] - b["debit"])

            results.append({
                "account_id": cp_id,
                "account_name": cp.get("name"),
                "platform": cp.get("platform"),
                "status": cp.get("status"),
                "walk_balance": walk,
                "ssot_balance": ssot_net,
                "difference": diff,
                "abs_difference": abs(diff),
                "match": abs(diff) < 0.01,
                "ssot_total_credit": ssot["total_credit"],
                "ssot_total_debit": ssot["total_debit"],
                "ssot_archived_count": ssot["archived_count"],
                "ssot_archived_net": ssot["archived_net"],
                "ssot_by_entry_type": ssot["by_entry_type"],
                "entries": ssot["entries"],
            })

        # Sort: largest absolute diff first.
        results.sort(key=lambda x: -x["abs_difference"])

        # Build summary.
        total_walk = _r(sum(r["walk_balance"] for r in results))
        total_ssot = _r(sum(r["ssot_balance"] for r in results))
        accounts_mismatch = [r for r in results if not r["match"]]

        # Attribution by entry_type across ALL accounts:
        global_attrib_out = sorted(
            [{"entry_type": k, "net_contribution": _r(v)}
             for k, v in global_attrib.items()],
            key=lambda x: -abs(x["net_contribution"]),
        )

        return {
            "success": True,
            "read_only": True,
            "iteration": "iter230-diagnostic",
            "summary": {
                "total_walk_balance": total_walk,
                "total_ssot_balance": total_ssot,
                "total_difference": _r(total_ssot - total_walk),
                "accounts_count": len(results),
                "accounts_mismatch_count": len(accounts_mismatch),
                "global_attribution_by_entry_type": global_attrib_out,
            },
            "accounts": results,
        }

    return router
