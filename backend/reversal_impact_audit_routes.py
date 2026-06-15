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
        expand_entries: bool = False,
    ):
        """Iter-217b — Read-only assessment of the compute_balance
        fix impact. Set ``expand_entries=true`` to receive the rich
        per-reversal record (amount, side, original/reversal IDs,
        txn_group_ids, reason, posted_at) inline in `rows[*].entries`.
        """
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
                # Pair each reversal with the original it reverses.
                # When `expand_entries=true`, hydrate each pair with
                # the full per-leg metadata (amount, side, group ids,
                # timestamps, reason) — the user can then drill into
                # the exact entries that drove the delta.
                rev_ids = r.get("reversal_ids") or []
                orig_ids = r.get("original_ids") or []
                if not expand_entries:
                    pairs = [{"reversal_id": rid,
                               "original_id": orig_ids[i]
                                              if i < len(orig_ids) else None}
                             for i, rid in enumerate(rev_ids)]
                else:
                    rev_docs = {}
                    async for d in db.general_ledger.find(
                        {"id": {"$in": rev_ids}, "user_id": uid},
                        {"_id": 0},
                    ):
                        rev_docs[d["id"]] = d
                    orig_filter = [x for x in orig_ids if x]
                    orig_docs = {}
                    if orig_filter:
                        async for d in db.general_ledger.find(
                            {"id": {"$in": orig_filter},
                             "user_id": uid},
                            {"_id": 0},
                        ):
                            orig_docs[d["id"]] = d
                    pairs = []
                    for i, rid in enumerate(rev_ids):
                        rev = rev_docs.get(rid, {})
                        oid = orig_ids[i] if i < len(orig_ids) else None
                        orig = orig_docs.get(oid or "", {})
                        rev_side = rev.get("side")
                        rev_amount = float(rev.get("amount") or 0)
                        delta_one = round(
                            (rev_amount if rev_side == "debit"
                             else -rev_amount) * -1, 2,
                        )
                        # ↑ Per-leg delta (= what Iter-217 added to
                        # the entity's balance for THIS reversal,
                        # before considering its pair). Computed as
                        # the negation of the reversal's contribution
                        # to the buggy compute_balance.
                        pairs.append({
                            "reversal_id": rid,
                            "original_id": oid,
                            "entity_type": rev.get("entity_type"),
                            "entity_id": rev.get("entity_id"),
                            "entity_name": name,
                            "sub_account": rev.get("sub_account"),
                            "amount": rev_amount,
                            "reversal_side": rev_side,
                            "original_side": orig.get("side"),
                            "original_entry_no": orig.get("entry_no"),
                            "reversal_entry_no": rev.get("entry_no"),
                            "original_txn_group_id":
                                orig.get("txn_group_id"),
                            "reversal_txn_group_id":
                                rev.get("txn_group_id"),
                            "original_txn_type": (
                                orig.get("metadata") or {}
                            ).get("txn_type") or orig.get("entry_type"),
                            "reason_code": rev.get("reason_code"),
                            "notes": rev.get("notes"),
                            "original_posted_at": orig.get("posted_at"),
                            "reversal_posted_at": rev.get("posted_at"),
                            "delta_contribution": delta_one,
                            "ad_account_name":
                                (orig.get("metadata") or {})
                                .get("ad_account_name"),
                            "spend_date":
                                (orig.get("metadata") or {})
                                .get("spend_date"),
                            "window_period":
                                (orig.get("metadata") or {})
                                .get("window_period"),
                        })
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

    # ── /reversal-impact-report/details ─────────────────────────────
    # Flat list of every reversal entry the merchant has, joined with
    # its original counterpart. Useful when the merchant wants ONE
    # screen with every reversal sorted by absolute impact (e.g., to
    # answer "which 3 reversals caused the +15,850.57 swing on
    # expense.advertising?"). Filterable by entity_type / entity_id /
    # txn_type so the user can drill into a specific area.
    @router.get("/reversal-impact-report/details")
    async def reversal_impact_details(
        user: dict = Depends(current_user),
        entity_type: str | None = None,
        entity_id: str | None = None,
        txn_type: str | None = None,
        sort_by: str = "impact_desc",
        limit: int = 500,
    ):
        uid = user["id"]
        q: dict = {"user_id": uid, "status": "posted",
                    "entry_type": "reversal"}
        if entity_type:
            q["entity_type"] = entity_type
        if entity_id:
            q["entity_id"] = entity_id

        rev_docs = []
        async for d in db.general_ledger.find(q, {"_id": 0}):
            rev_docs.append(d)
        if not rev_docs:
            return {"source": "general_ledger", "iter": "iter217b",
                    "summary": {"total": 0}, "details": []}

        # Bulk-fetch the originals.
        orig_ids = [d.get("reverses_entry_id") for d in rev_docs
                    if d.get("reverses_entry_id")]
        orig_docs: dict = {}
        if orig_ids:
            async for d in db.general_ledger.find(
                {"user_id": uid, "id": {"$in": orig_ids}},
                {"_id": 0},
            ):
                orig_docs[d["id"]] = d

        # Resolve names in batch.
        wanted = {(d["entity_type"], d["entity_id"]) for d in rev_docs}
        name_cache: dict = {}
        for et, eid in wanted:
            name_cache[(et, eid)] = await _resolve_name(uid, et, eid)

        details = []
        for rev in rev_docs:
            oid = rev.get("reverses_entry_id")
            orig = orig_docs.get(oid or "", {})
            if txn_type:
                this_type = (
                    (orig.get("metadata") or {}).get("txn_type")
                    or orig.get("entry_type") or ""
                )
                if this_type != txn_type:
                    continue
            rev_amount = float(rev.get("amount") or 0)
            rev_side = rev.get("side")
            # Per-leg delta — what Iter-217 added/removed from the
            # entity's balance because of this single reversal.
            delta_one = round(
                (rev_amount if rev_side == "credit"
                 else -rev_amount), 2,
            )
            details.append({
                "entity_name": name_cache.get(
                    (rev["entity_type"], rev["entity_id"]),
                    rev["entity_id"],
                ),
                "entity_type": rev["entity_type"],
                "entity_id": rev["entity_id"],
                "sub_account": rev.get("sub_account"),
                "amount": rev_amount,
                "reversal_side": rev_side,
                "original_side": orig.get("side"),
                "original_ledger_id": oid,
                "reversal_ledger_id": rev["id"],
                "original_txn_group_id": orig.get("txn_group_id"),
                "reversal_txn_group_id": rev.get("txn_group_id"),
                "original_entry_no": orig.get("entry_no"),
                "reversal_entry_no": rev.get("entry_no"),
                "original_txn_type": (
                    (orig.get("metadata") or {}).get("txn_type")
                    or orig.get("entry_type")
                ),
                "original_notes": orig.get("notes"),
                "reversal_notes": rev.get("notes"),
                "reason_code": rev.get("reason_code"),
                "original_posted_at": orig.get("posted_at"),
                "reversal_posted_at": rev.get("posted_at"),
                "ad_account_name":
                    (orig.get("metadata") or {}).get("ad_account_name"),
                "spend_date":
                    (orig.get("metadata") or {}).get("spend_date"),
                "window_period":
                    (orig.get("metadata") or {}).get("window_period"),
                "ad_provider":
                    (orig.get("metadata") or {}).get("ad_provider"),
                "delta": delta_one,
            })

        # Sort
        if sort_by == "impact_desc":
            details.sort(key=lambda d: abs(d["delta"]), reverse=True)
        elif sort_by == "posted_at_desc":
            details.sort(key=lambda d: d.get("reversal_posted_at") or "",
                          reverse=True)
        elif sort_by == "amount_desc":
            details.sort(key=lambda d: d["amount"], reverse=True)

        details = details[:limit]

        total_delta = round(sum(d["delta"] for d in details), 2)
        return {
            "source": "general_ledger",
            "iter": "iter217b",
            "filters": {
                "entity_type": entity_type, "entity_id": entity_id,
                "txn_type": txn_type, "sort_by": sort_by, "limit": limit,
            },
            "summary": {
                "total": len(details),
                "total_delta": total_delta,
            },
            "details": details,
        }

    return router
