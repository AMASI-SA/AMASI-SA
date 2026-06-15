"""Iter-222 — Employee Opening Orphans Diagnostic (Read-Only, Phase 1).

Purpose
-------
Produce a definitive forensic report of every general_ledger entry
that references an employee but cannot be resolved cleanly. The user
flagged 15 such orphans in production; this endpoint is the
single-source-of-truth for understanding WHY each one is orphan
before any corrective action is decided.

ZERO writes. ZERO migrations. ZERO side-effects.

Classification taxonomy
=======================
* ``deleted_entity``        — entity_id once matched an employee that
                              is now removed / soft-deleted in the
                              employees collection (no record at all).
* ``employee_id_mismatch``  — entity_id matches an employee record
                              but that record belongs to a *different*
                              user (cross-tenant leak).
* ``missing_counter_entry`` — the txn_group_id has no balancing legs
                              (debits ≠ credits within the group) OR
                              the group only contains this single
                              employee leg.
* ``orphan_opening``        — entry_type == "opening_balance" and the
                              entity_id is unresolved for the current
                              user (the classic case).
* ``orphan_reversal``       — entry_type == "reversal" but the
                              referenced original txn_group_id has
                              been deleted / archived and no longer
                              exists in general_ledger.
* ``other``                 — any other reason the entry is unresolved.

The endpoint is mounted at:
    GET /api/audit/employee-orphan-openings
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, Depends


def _to_iso(v) -> Optional[str]:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:  # noqa: BLE001
            return str(v)
    return str(v)


def _r(v) -> float:
    return round(float(v or 0), 2)


def make_employee_orphan_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit"])

    async def _build_employee_index(uid: str) -> tuple[dict, set, dict]:
        """Returns (index_all_by_key, valid_keys_for_user, name_by_key).

        index_all_by_key: maps any plausible employee identifier (id,
            employee_id, external_id, legacy_id, _id) to a summary
            dict, INCLUDING employees from other users — needed to
            detect cross-tenant mismatches.
        valid_keys_for_user: subset of keys whose owning record has
            user_id == uid.
        name_by_key: best-effort full name lookup, used in the
            per-employee breakdown.
        """
        index_all: dict = {}
        valid_for_user: set = set()
        name_by_key: dict = {}

        async for e in db.employees.find(
            {},
            {"_id": 1, "id": 1, "employee_id": 1, "external_id": 1,
             "legacy_id": 1, "user_id": 1, "name": 1, "full_name": 1,
             "status": 1, "is_deleted": 1, "deleted_at": 1},
        ):
            belongs = (str(e.get("user_id") or "") == uid)
            name = (
                e.get("full_name") or e.get("name") or ""
            ).strip() or "—"
            summary = {
                "employee_id": e.get("id") or e.get("employee_id"),
                "name": name,
                "belongs_to_current_user": belongs,
                "user_id": e.get("user_id"),
                "status": e.get("status"),
                "is_deleted": bool(e.get("is_deleted")),
                "deleted_at": _to_iso(e.get("deleted_at")),
            }
            keys = []
            for k in ("id", "employee_id", "external_id", "legacy_id"):
                v = e.get(k)
                if v:
                    keys.append(str(v))
            if e.get("_id"):
                keys.append(str(e["_id"]))
            for key in keys:
                index_all.setdefault(key, []).append(summary)
                name_by_key.setdefault(key, name)
                if belongs:
                    valid_for_user.add(key)
        return index_all, valid_for_user, name_by_key

    async def _group_balance(uid: str, group_id: str) -> dict:
        """Returns {debit, credit, count} for a given txn_group_id."""
        agg = [
            {"$match": {"user_id": uid, "txn_group_id": group_id}},
            {"$group": {
                "_id": "$side",
                "total": {"$sum": "$amount"},
                "count": {"$sum": 1},
            }},
        ]
        out = {"debit": 0.0, "credit": 0.0, "count": 0}
        async for row in db.general_ledger.aggregate(agg):
            side = row["_id"]
            out[side] = float(row.get("total") or 0)
            out["count"] += int(row.get("count") or 0)
        return out

    async def _original_txn_exists(uid: str, ref: str) -> bool:
        if not ref:
            return False
        doc = await db.general_ledger.find_one(
            {"user_id": uid, "txn_group_id": ref},
            {"_id": 1},
        )
        return doc is not None

    @router.get("/employee-orphan-openings")
    async def employee_orphan_openings(
        user: dict = Depends(current_user),
    ):
        """Read-only forensic report of employee orphan ledger entries.

        Response shape::

            {
              "success": true,
              "user_id": "...",
              "summary": {
                "total_orphans": int,
                "total_debit": float,
                "total_credit": float,
                "net_impact": float,            # debit - credit
                "salary_payable_impact": float, # net change if fixed
                "advance_impact": float,
                "custody_impact": float,
                "by_classification": [
                  {"classification": str, "count": int,
                   "debit": float, "credit": float, "net": float},
                  ...
                ],
                "by_sub_account": [...],
                "by_entry_type": [...],
              },
              "per_employee": [
                 {"entity_id": str, "name": str,
                  "current_balance": {sub_account: float, ...},
                  "expected_after_fix": {sub_account: float, ...},
                  "difference": {sub_account: float, ...},
                  "affected_count": int,
                  "classifications": [str, ...]},
                 ...
              ],
              "entries": [
                 {"ledger_id", "txn_group_id", "entry_type",
                  "entity_id", "sub_account", "side", "amount",
                  "posted_at", "metadata_name", "metadata_notes",
                  "classification", "reason", "group_balance"},
                 ...
              ]
            }
        """
        uid = user["id"]
        index_all, valid_keys, name_by_key = await _build_employee_index(uid)

        entries: list[dict] = []
        total_debit = 0.0
        total_credit = 0.0
        per_emp: dict = {}

        by_class: dict = defaultdict(
            lambda: {"count": 0, "debit": 0.0, "credit": 0.0},
        )
        by_sub: dict = defaultdict(
            lambda: {"count": 0, "debit": 0.0, "credit": 0.0},
        )
        by_etype: dict = defaultdict(
            lambda: {"count": 0, "debit": 0.0, "credit": 0.0},
        )

        # Iterate ALL general_ledger entries on employees for this user.
        # We do NOT restrict to opening_balance because the user's
        # taxonomy includes orphan_reversal + missing_counter_entry +
        # other entry_types.
        async for row in db.general_ledger.find(
            {"user_id": uid,
             "entity_type": "employee",
             "status": {"$ne": "voided"}},
            {"_id": 0, "id": 1, "txn_group_id": 1,
             "entry_type": 1, "entity_id": 1, "sub_account": 1,
             "side": 1, "amount": 1, "posted_at": 1, "created_at": 1,
             "notes": 1, "metadata": 1},
        ):
            eid = str(row.get("entity_id") or "")
            sub = row.get("sub_account") or "_unknown"
            side = row.get("side") or "_unknown"
            etype = row.get("entry_type") or "_unknown"
            amt = float(row.get("amount") or 0)
            md = row.get("metadata") or {}

            # Step 1: resolve the entity ID against the per-user index.
            hits_all = index_all.get(eid, [])
            hits_user = [h for h in hits_all
                          if h.get("belongs_to_current_user")]
            in_employees_at_all = len(hits_all) > 0
            valid_for_user = eid in valid_keys

            # NOT an orphan — the entry resolves cleanly.
            if valid_for_user:
                continue

            # Step 2: classify the orphan reason.
            classification = "other"
            reason = ""

            if hits_all and not hits_user:
                classification = "employee_id_mismatch"
                other_uid = hits_all[0].get("user_id")
                reason = (
                    f"الموظف موجود لكنه ينتمي لمستخدم آخر "
                    f"(user_id={other_uid})."
                )
            elif not in_employees_at_all and etype == "opening_balance":
                classification = "orphan_opening"
                reason = (
                    "قيد افتتاحي يشير إلى موظف غير موجود في "
                    "المجموعة (employees) لأي مستخدم."
                )
            elif not in_employees_at_all and etype == "reversal":
                # Verify whether the reversed group still exists.
                orig_ref = (
                    md.get("reverses_txn_group_id")
                    or md.get("original_txn_group_id")
                    or md.get("reversal_target")
                )
                exists = await _original_txn_exists(uid, orig_ref)
                if not exists:
                    classification = "orphan_reversal"
                    reason = (
                        f"قيد عكس يشير لمعاملة أصلية غير موجودة "
                        f"(target={orig_ref or 'غير محدد'})."
                    )
                else:
                    classification = "other"
                    reason = (
                        f"قيد عكس على موظف غير موجود لكن المعاملة "
                        f"الأصلية موجودة ({orig_ref})."
                    )
            else:
                # Check whether the group is balanced — if not, this is
                # a "missing_counter_entry" case.
                gid = row.get("txn_group_id")
                if gid:
                    gb = await _group_balance(uid, gid)
                    if abs(gb["debit"] - gb["credit"]) > 0.01:
                        classification = "missing_counter_entry"
                        reason = (
                            f"المعاملة (txn_group_id={gid}) غير متوازنة "
                            f"— مدين={_r(gb['debit'])}, "
                            f"دائن={_r(gb['credit'])}، عدد الأرجل={gb['count']}."
                        )
                    elif not in_employees_at_all:
                        classification = "deleted_entity"
                        reason = (
                            "الموظف غير موجود في أي مجموعة employees "
                            "(محذوف نهائياً)."
                        )
                    else:
                        classification = "other"
                        reason = (
                            "تعذّر تصنيف القيد ضمن الفئات المعروفة."
                        )
                elif not in_employees_at_all:
                    classification = "deleted_entity"
                    reason = "الموظف غير موجود — لا txn_group_id لتقييم التوازن."
                else:
                    classification = "other"
                    reason = "غير مصنّف."

            # Step 3: name resolution from metadata/index.
            meta_name = (
                md.get("employee_name") or md.get("name")
                or name_by_key.get(eid) or ""
            ) or "—"

            # Step 4: capture and aggregate.
            entry = {
                "ledger_id": row.get("id"),
                "txn_group_id": row.get("txn_group_id"),
                "entry_type": etype,
                "entity_id": eid,
                "sub_account": sub,
                "side": side,
                "amount": _r(amt),
                "debit": _r(amt) if side == "debit" else 0,
                "credit": _r(amt) if side == "credit" else 0,
                "posted_at": _to_iso(
                    row.get("posted_at") or row.get("created_at"),
                ),
                "metadata_name": meta_name,
                "metadata_notes": row.get("notes"),
                "metadata_source": (
                    md.get("source") or md.get("origin") or ""
                ),
                "classification": classification,
                "reason": reason,
            }
            entries.append(entry)

            if side == "debit":
                total_debit += amt
            else:
                total_credit += amt

            for bucket, key in (
                (by_class, classification),
                (by_sub, sub),
                (by_etype, etype),
            ):
                bucket[key]["count"] += 1
                if side == "debit":
                    bucket[key]["debit"] += amt
                else:
                    bucket[key]["credit"] += amt

            # Per-employee aggregation (uses the entity_id even though
            # it's unresolved — the operator can still see the cluster).
            emp = per_emp.setdefault(eid, {
                "entity_id": eid,
                "name": meta_name,
                "current_balance": defaultdict(float),
                "orphan_impact": defaultdict(float),
                "affected_count": 0,
                "classifications": set(),
            })
            emp["affected_count"] += 1
            emp["classifications"].add(classification)
            if meta_name and emp["name"] == "—":
                emp["name"] = meta_name
            # orphan_impact = signed impact on this sub_account.
            # debits to salary_payable REDUCE liability; credits INCREASE.
            sign = 1 if side == "debit" else -1
            emp["orphan_impact"][sub] += sign * amt

        # Pull the live ledger balance per (entity_id, sub_account) so
        # we can show "current" vs "expected after fix".
        for eid, emp in per_emp.items():
            for sub in list(emp["orphan_impact"].keys()):
                # current_balance_net = sum(debit) - sum(credit) on
                # this (entity_id, sub_account) — same convention as
                # ledger_core.compute_balance.
                from ledger_core import compute_balance
                bal = await compute_balance(
                    db, user_id=uid, entity_type="employee",
                    entity_id=eid, sub_account=sub,
                )
                emp["current_balance"][sub] = _r(bal.get("net_balance"))

        # Normalise per_emp records for JSON.
        per_employee_out = []
        for emp in per_emp.values():
            current = {k: _r(v) for k, v in emp["current_balance"].items()}
            impact = {k: _r(v) for k, v in emp["orphan_impact"].items()}
            expected = {
                k: _r(current.get(k, 0) - impact.get(k, 0))
                for k in set(current) | set(impact)
            }
            diff = {
                k: _r(expected.get(k, 0) - current.get(k, 0))
                for k in expected
            }
            per_employee_out.append({
                "entity_id": emp["entity_id"],
                "name": emp["name"],
                "current_balance": current,
                "orphan_impact": impact,
                "expected_after_fix": expected,
                "difference": diff,
                "affected_count": emp["affected_count"],
                "classifications": sorted(emp["classifications"]),
            })

        # Aggregate orphan_impact by sub_account across all employees
        # for the summary totals.
        impact_by_sub: dict = defaultdict(float)
        for emp in per_emp.values():
            for sub, v in emp["orphan_impact"].items():
                impact_by_sub[sub] += v

        def _fmt(bucket_dict):
            return [
                {"key": k, "count": v["count"],
                 "debit": _r(v["debit"]), "credit": _r(v["credit"]),
                 "net": _r(v["debit"] - v["credit"])}
                for k, v in bucket_dict.items()
            ]

        # Build the summary.
        summary = {
            "total_orphans": len(entries),
            "total_debit": _r(total_debit),
            "total_credit": _r(total_credit),
            "net_impact": _r(total_debit - total_credit),
            "salary_payable_impact": _r(
                impact_by_sub.get("salary_payable", 0),
            ),
            "advance_impact": _r(impact_by_sub.get("advance", 0)),
            "custody_impact": _r(impact_by_sub.get("custody", 0)),
            "by_classification": sorted(
                [{"classification": k, **v}
                 for k, v in (
                    {kk: {
                        "count": vv["count"],
                        "debit": _r(vv["debit"]),
                        "credit": _r(vv["credit"]),
                        "net": _r(vv["debit"] - vv["credit"]),
                    } for kk, vv in by_class.items()}
                 ).items()],
                key=lambda x: -x["count"],
            ),
            "by_sub_account": sorted(
                _fmt(by_sub), key=lambda x: -x["count"],
            ),
            "by_entry_type": sorted(
                _fmt(by_etype), key=lambda x: -x["count"],
            ),
        }

        return {
            "success": True,
            "user_id": uid,
            "read_only": True,
            "summary": summary,
            "per_employee": sorted(
                per_employee_out,
                key=lambda x: -x["affected_count"],
            ),
            "entries": entries,
        }

    return router
