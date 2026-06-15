"""Iter-217b — Reversal-Impact Audit Report (Read-Only).

Quantifies the effect of the Iter-217 `compute_balance` fix on every
merchant entity. For each entity that has ANY reversal entries, it
returns:

  • entity_type / entity_id / sub_account / display name
  • count of reversal entries
  • net_before_fix  — what compute_balance returned BEFORE Iter-217
                     (counts reversal entries as standalone)
  • net_after_fix   — what compute_balance returns NOW (reversal +
                     reversed-original pair cancel each other)
  • delta           — net_after_fix − net_before_fix
                     (positive = balance grew, negative = balance
                      shrank; magnitude equals the previously-double-
                      counted reversal contribution)
  • original_entries / reversal_entries — IDs for transparency.

STRICTLY READ-ONLY. No mutation, no migration, no posting. Safe to
call on production any number of times.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends


def make_reversal_impact_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit"])

    async def _resolve_name(uid: str, entity_type: str,
                             entity_id: str) -> str:
        """Best-effort lookup of a human-readable name. Falls back to
        the raw entity_id when the canonical source doesn't have one
        (e.g., system entities like 'salary' or 'subs')."""
        if entity_type == "employee":
            doc = await db.operating_salaries.find_one(
                {"user_id": uid, "id": entity_id, "category": "employee"},
                {"_id": 0, "name": 1},
            )
            if doc:
                return doc.get("name") or entity_id
        if entity_type == "bank":
            doc = await db.accounts.find_one(
                {"user_id": uid, "id": entity_id},
                {"_id": 0, "name": 1},
            )
            if doc:
                return doc.get("name") or entity_id
        if entity_type in ("supplier", "external_person", "courier",
                            "customer", "ad_account", "payment_gateway"):
            doc = await db.counterparties.find_one(
                {"user_id": uid, "id": entity_id},
                {"_id": 0, "name": 1, "ad_provider": 1},
            )
            if doc:
                name = doc.get("name") or entity_id
                if entity_type == "ad_account" and doc.get("ad_provider"):
                    name = f"{name} ({doc['ad_provider']})"
                return name
        return entity_id

    @router.get("/reversal-impact-report")
    async def reversal_impact_report(
        user: dict = Depends(current_user),
        include_ids: bool = True,
    ):
        uid = user["id"]

        # 1. Aggregate reversal entries per (entity_type, entity_id,
        #    sub_account). Each reversal carries the OPPOSITE side of
        #    its original; the net contribution of one reversal to
        #    compute_balance (pre-Iter-217) was:
        #       +amount  if side=debit
        #       -amount  if side=credit
        pipeline = [
            {"$match": {"user_id": uid, "status": "posted",
                         "entry_type": "reversal"}},
            {"$group": {
                "_id": {"entity_type": "$entity_type",
                         "entity_id": "$entity_id",
                         "sub_account": "$sub_account"},
                "count": {"$sum": 1},
                "debit_sum": {"$sum": {"$cond": [
                    {"$eq": ["$side", "debit"]}, "$amount", 0]}},
                "credit_sum": {"$sum": {"$cond": [
                    {"$eq": ["$side", "credit"]}, "$amount", 0]}},
                "reversal_ids": {"$push": "$id"},
                "original_ids": {"$push": "$reverses_entry_id"},
            }},
            {"$sort": {"count": -1}},
        ]
        rows = await db.general_ledger.aggregate(
            pipeline,
        ).to_list(length=2000)

        # 2. For each affected entity bucket, compute:
        #    net_after_fix  = sum over (status=posted AND entry_type!=reversal)
        #    net_before_fix = net_after_fix + reversal_net_contribution
        # where reversal_net_contribution = debit_sum − credit_sum.
        report: list[dict] = []
        for r in rows:
            key = r["_id"]
            et, eid, sub = (key["entity_type"], key["entity_id"],
                             key.get("sub_account"))
            # net_after_fix from the fixed compute_balance logic.
            after_pipe = [
                {"$match": {
                    "user_id": uid, "status": "posted",
                    "entity_type": et, "entity_id": eid,
                    "entry_type": {"$ne": "reversal"},
                    **({"sub_account": sub} if sub else
                        {"sub_account": None}),
                }},
                {"$group": {"_id": "$side",
                             "total": {"$sum": "$amount"}}},
            ]
            d = c = 0.0
            async for s in db.general_ledger.aggregate(after_pipe):
                if s["_id"] == "debit":
                    d = float(s["total"])
                elif s["_id"] == "credit":
                    c = float(s["total"])
            net_after = round(d - c, 2)
            rev_contribution = round(
                float(r["debit_sum"]) - float(r["credit_sum"]), 2,
            )
            net_before = round(net_after + rev_contribution, 2)
            delta = round(net_after - net_before, 2)
            name = await _resolve_name(uid, et, eid)
            row: dict = {
                "entity_type": et,
                "entity_id": eid,
                "sub_account": sub,
                "name": name,
                "reversal_count": r["count"],
                "net_before_fix": net_before,
                "net_after_fix": net_after,
                "delta": delta,
                "delta_direction": (
                    "balance_grew" if delta > 0.005 else
                    ("balance_shrank" if delta < -0.005 else "no_change")
                ),
            }
            if include_ids:
                # Pair each reversal with the original it reverses
                # (parallel arrays from $push). Filter out empty
                # original_ids defensively.
                pairs = []
                orig_ids = r.get("original_ids") or []
                rev_ids = r.get("reversal_ids") or []
                for i, rid in enumerate(rev_ids):
                    oid = orig_ids[i] if i < len(orig_ids) else None
                    pairs.append({"reversal_id": rid,
                                   "original_id": oid})
                row["entries"] = pairs
            report.append(row)

        # 3. Headline summary.
        affected_entities = len(report)
        total_delta = round(sum(r["delta"] for r in report), 2)
        net_obligation_change = round(
            sum(r["delta"] for r in report if r["net_after_fix"] < 0),
            2,
        )
        net_asset_change = round(
            sum(r["delta"] for r in report if r["net_after_fix"] > 0),
            2,
        )

        # 4. Top-by-magnitude (10) — quickest read for the merchant.
        top = sorted(report, key=lambda x: abs(x["delta"]),
                     reverse=True)[:10]

        return {
            "source": "general_ledger",
            "iter": "iter217b",
            "scope": "user",
            "user_id": uid,
            "summary": {
                "affected_entities": affected_entities,
                "total_reversal_count": sum(
                    r["reversal_count"] for r in report),
                "total_delta": total_delta,
                "net_obligation_change_on_liability_entities":
                    net_obligation_change,
                "net_balance_change_on_asset_entities":
                    net_asset_change,
            },
            "top_impact": [{
                "name": r["name"], "entity_type": r["entity_type"],
                "sub_account": r["sub_account"],
                "delta": r["delta"], "net_after_fix": r["net_after_fix"],
                "reversal_count": r["reversal_count"],
            } for r in top],
            "rows": report,
        }

    return router
