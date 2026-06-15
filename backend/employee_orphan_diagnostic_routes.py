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
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException


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

            # Step 4: repair suggestion (READ-ONLY HINT — not executed).
            # Decision tree:
            #   • employee_id_mismatch  → MANUAL_REVIEW (cross-tenant; risky)
            #   • orphan_reversal       → MANUAL_REVIEW (target is gone)
            #   • missing_counter_entry → REVERSE (group is mathematically broken)
            #   • orphan_opening + metadata has a clear name and not a true zero → RECREATE_EMPLOYEE
            #   • deleted_entity        → RECREATE_EMPLOYEE if name available else REVERSE
            #   • other                 → MANUAL_REVIEW
            # KEEP is reserved for entries whose net contribution is
            # already zero on their sub_account (a self-cancelling pair).
            #   We resolve KEEP later, once we know the per-(emp,sub,etype) net.
            repair_suggestion = "MANUAL_REVIEW"
            repair_reason = "غير محدد بعد — التصنيف غير متعامل معه."
            if classification == "employee_id_mismatch":
                repair_suggestion = "MANUAL_REVIEW"
                repair_reason = (
                    "الموظف يخصّ مستخدماً آخر — قد يكون تسرّباً "
                    "بين الحسابات (cross-tenant). يجب مراجعة يدوية."
                )
            elif classification == "orphan_reversal":
                repair_suggestion = "MANUAL_REVIEW"
                repair_reason = (
                    "قيد عكس بدون مرجع أصلي صالح — "
                    "لا يمكن عكسه أو الإبقاء عليه بأمان دون مراجعة."
                )
            elif classification == "missing_counter_entry":
                repair_suggestion = "REVERSE"
                repair_reason = (
                    "المجموعة غير متوازنة محاسبياً — "
                    "الأسلم عكسها كاملةً ثم إعادة إنشائها بشكل صحيح."
                )
            elif classification == "orphan_opening":
                if meta_name and meta_name != "—":
                    repair_suggestion = "RECREATE_EMPLOYEE"
                    repair_reason = (
                        "قيد افتتاحي على موظف معروف بالاسم لكن سجلّه "
                        "محذوف — إعادة إنشاء الموظف ستحلّ القيد دون "
                        "أي عكس."
                    )
                else:
                    repair_suggestion = "REVERSE"
                    repair_reason = (
                        "قيد افتتاحي بلا اسم/مرجع — "
                        "أسلم خيار هو عكسه."
                    )
            elif classification == "deleted_entity":
                if meta_name and meta_name != "—":
                    repair_suggestion = "RECREATE_EMPLOYEE"
                    repair_reason = (
                        "الموظف محذوف لكن اسمه محفوظ في metadata. "
                        "إعادة إنشاء سجلّ الموظف بنفس entity_id ستفعّل "
                        "القيد تلقائياً."
                    )
                else:
                    repair_suggestion = "REVERSE"
                    repair_reason = (
                        "الموظف محذوف ولا يوجد اسم/بيانات لإعادة "
                        "إنشاء سجلّه — يفضّل العكس."
                    )

            # Step 5: capture and aggregate.
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
                # Iter-223 deep diagnostic — read-only suggestion.
                "repair_suggestion": repair_suggestion,
                "repair_reason": repair_reason,
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
                "repair_suggestions": set(),
                # Iter-223 — explicit debit/credit split per
                # (sub_account, entry_type) for the deep report.
                "by_sub_side": defaultdict(float),
                "by_etype_side": defaultdict(float),
                "txn_group_ids": set(),
            })
            emp["affected_count"] += 1
            emp["classifications"].add(classification)
            emp["repair_suggestions"].add(repair_suggestion)
            if row.get("txn_group_id"):
                emp["txn_group_ids"].add(row.get("txn_group_id"))
            if meta_name and emp["name"] == "—":
                emp["name"] = meta_name
            # orphan_impact = signed impact on this sub_account.
            # debits to salary_payable REDUCE liability; credits INCREASE.
            sign = 1 if side == "debit" else -1
            emp["orphan_impact"][sub] += sign * amt
            emp["by_sub_side"][f"{sub}_{side}"] += amt
            emp["by_etype_side"][f"{etype}_{side}"] += amt

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
            # Explicit fields requested by the deep diagnostic:
            sub_side = emp["by_sub_side"]
            etype_side = emp["by_etype_side"]
            per_employee_out.append({
                "entity_id": emp["entity_id"],
                "employee_id": emp["entity_id"],   # alias for clarity
                "employee_name": emp["name"],
                "name": emp["name"],
                "orphan_count": emp["affected_count"],
                "affected_count": emp["affected_count"],
                # Per-sub debit/credit:
                "salary_payable_debit": _r(sub_side.get("salary_payable_debit", 0)),
                "salary_payable_credit": _r(sub_side.get("salary_payable_credit", 0)),
                "advance_debit": _r(sub_side.get("advance_debit", 0)),
                "advance_credit": _r(sub_side.get("advance_credit", 0)),
                "custody_debit": _r(sub_side.get("custody_debit", 0)),
                "custody_credit": _r(sub_side.get("custody_credit", 0)),
                # Per-entry_type debit/credit:
                "opening_balance_debit": _r(etype_side.get("opening_balance_debit", 0)),
                "opening_balance_credit": _r(etype_side.get("opening_balance_credit", 0)),
                "reversal_debit": _r(etype_side.get("reversal_debit", 0)),
                "reversal_credit": _r(etype_side.get("reversal_credit", 0)),
                "salary_accrual_debit": _r(etype_side.get("salary_accrual_debit", 0)),
                "salary_accrual_credit": _r(etype_side.get("salary_accrual_credit", 0)),
                "salary_payment_debit": _r(etype_side.get("salary_payment_debit", 0)),
                "salary_payment_credit": _r(etype_side.get("salary_payment_credit", 0)),
                # Aggregate net effect across all sub_accounts (signed).
                "net_effect": _r(sum(impact.values())),
                # Existing fields:
                "current_balance": current,
                "orphan_impact": impact,
                "expected_after_fix": expected,
                "difference": diff,
                "classifications": sorted(emp["classifications"]),
                "repair_suggestions": sorted(emp["repair_suggestions"]),
                "txn_group_ids": sorted(emp["txn_group_ids"]),
            })

        # ── Per-txn_group aggregation ────────────────────────────────
        # For every txn_group_id touched by an orphan, recompute the
        # GROUP's total debit/credit across ALL its legs (not just the
        # employee orphan leg) so we can flag genuinely-unbalanced
        # groups vs. groups that simply contain a now-orphan party.
        per_group_out = []
        all_groups = sorted({e["txn_group_id"] for e in entries
                             if e.get("txn_group_id")})
        for gid in all_groups:
            gb = await _group_balance(uid, gid)
            balanced = abs(gb["debit"] - gb["credit"]) < 0.01
            # Affected employees in this group (from our orphan set).
            affected = sorted({
                e["entity_id"] for e in entries
                if e.get("txn_group_id") == gid
            })
            affected_names = sorted({
                e["metadata_name"] for e in entries
                if e.get("txn_group_id") == gid and e["metadata_name"] != "—"
            })
            # entry_types in this group from our orphan set:
            entry_types_in = sorted({
                e["entry_type"] for e in entries
                if e.get("txn_group_id") == gid
            })
            per_group_out.append({
                "txn_group_id": gid,
                "count_entries": gb["count"],
                "total_debit": _r(gb["debit"]),
                "total_credit": _r(gb["credit"]),
                "balanced": balanced,
                "affected_employees": affected,
                "affected_employee_names": affected_names,
                "entry_types": entry_types_in,
            })

        # KEEP detection — bump suggestion to KEEP when the per-employee
        # per-(sub_account, entry_type) sum already nets to zero
        # (self-cancelling orphan pair). We update the entries' repair
        # suggestion in-place AFTER per_employee aggregation.
        net_by_emp_sub_etype: dict = defaultdict(float)
        for e in entries:
            key = (e["entity_id"], e["sub_account"], e["entry_type"])
            sign = 1 if e["side"] == "debit" else -1
            net_by_emp_sub_etype[key] += sign * float(e["amount"])
        for e in entries:
            key = (e["entity_id"], e["sub_account"], e["entry_type"])
            if abs(net_by_emp_sub_etype[key]) < 0.01:
                # Self-cancelling pair — safe to leave alone.
                e["repair_suggestion"] = "KEEP"
                e["repair_reason"] = (
                    "هذا القيد جزء من زوج متعاكس (مدين+دائن صافي=0) "
                    "لنفس الموظف وlsub_account. لا أثر مالي — يمكن الإبقاء."
                )

        # Recount repair_suggestions per employee AFTER KEEP override.
        repair_by_emp: dict = defaultdict(set)
        for e in entries:
            repair_by_emp[e["entity_id"]].add(e["repair_suggestion"])
        for emp_out in per_employee_out:
            emp_out["repair_suggestions"] = sorted(
                repair_by_emp.get(emp_out["entity_id"], set()),
            )

        # By repair_suggestion summary:
        by_repair: dict = defaultdict(
            lambda: {"count": 0, "debit": 0.0, "credit": 0.0},
        )
        for e in entries:
            r = e["repair_suggestion"]
            by_repair[r]["count"] += 1
            by_repair[r]["debit"] += float(e["debit"] or 0)
            by_repair[r]["credit"] += float(e["credit"] or 0)

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
            "by_repair_suggestion": sorted(
                [{"repair_suggestion": k,
                  "count": v["count"],
                  "debit": _r(v["debit"]),
                  "credit": _r(v["credit"]),
                  "net": _r(v["debit"] - v["credit"])}
                 for k, v in by_repair.items()],
                key=lambda x: -x["count"],
            ),
            "groups_count": len(per_group_out),
            "groups_unbalanced_count": sum(
                1 for g in per_group_out if not g["balanced"]
            ),
        }

        return {
            "success": True,
            "user_id": uid,
            "read_only": True,
            "iteration": "iter223-deep",
            "generated_at": _to_iso(datetime.utcnow()),
            "summary": summary,
            "per_employee": sorted(
                per_employee_out,
                key=lambda x: -x["affected_count"],
            ),
            "per_group": sorted(
                per_group_out,
                key=lambda x: (x["balanced"], -x["count_entries"]),
            ),
            "entries": entries,
        }

    # ── Iter-226 — Archive legacy orphans (metadata-only flag) ──
    @router.post("/employee-orphan-openings/archive")
    async def archive_legacy_orphans(
        payload: dict = Body(default_factory=dict),
        user: dict = Depends(current_user),
    ):
        """Mark every currently-orphan employee ledger entry with:
              metadata.legacy_orphan       = True
              metadata.archived_at         = <utc iso>
              metadata.archive_reason      = "<provided or default>"
              metadata.archived_by_user_id = <uid>

        This is a METADATA-ONLY write — neither side, amount, sub_account,
        txn_group_id, nor status are touched. The balance computation
        helpers (`compute_balance`, `compute_balances_bulk`,
        `financial_position_ssot`) now filter out
        `metadata.legacy_orphan = True` so the archived entries no
        longer affect any live number.

        Idempotent: running again is a no-op once everything is
        archived. Includes an `unarchive=True` parameter that **only**
        removes the flag without touching the entry otherwise.
        """
        uid = user["id"]
        unarchive: bool = bool(payload.get("unarchive") or False)
        reason: str = (
            payload.get("reason") or "legacy historical orphan (pre Iter-214)"
        ).strip()[:300]
        only_ids: list[str] = list(payload.get("ledger_ids") or [])

        # Build the eligible set: same logic as the diagnostic but
        # READ ONLY here; we recompute to ensure we never archive a
        # non-orphan entry by mistake.
        index_all, valid_keys, _names = await _build_employee_index(uid)

        candidates: list[str] = []
        async for row in db.general_ledger.find(
            {"user_id": uid,
             "entity_type": "employee",
             "status": {"$ne": "voided"}},
            {"_id": 0, "id": 1, "entity_id": 1, "metadata": 1},
        ):
            eid = str(row.get("entity_id") or "")
            md = row.get("metadata") or {}
            already_archived = bool(md.get("legacy_orphan"))

            if unarchive:
                if not already_archived:
                    continue
                if only_ids and row.get("id") not in only_ids:
                    continue
                candidates.append(row["id"])
                continue

            # Archive flow — only the entries that are CURRENTLY orphan.
            if eid in valid_keys:
                continue   # not orphan
            if already_archived:
                continue   # already done
            if only_ids and row.get("id") not in only_ids:
                continue
            candidates.append(row["id"])

        if not candidates:
            return {
                "success": True,
                "action": "unarchive" if unarchive else "archive",
                "matched": 0,
                "message": (
                    "لا توجد قيود يتيمة جديدة لأرشفتها"
                    if not unarchive else
                    "لا توجد قيود مؤرشفة لإلغاء أرشفتها"
                ),
            }

        now_iso = datetime.now(timezone.utc).isoformat()
        if unarchive:
            res = await db.general_ledger.update_many(
                {"user_id": uid, "id": {"$in": candidates}},
                {"$unset": {
                    "metadata.legacy_orphan": "",
                    "metadata.archived_at": "",
                    "metadata.archive_reason": "",
                    "metadata.archived_by_user_id": "",
                }},
            )
            action = "unarchive"
        else:
            res = await db.general_ledger.update_many(
                {"user_id": uid, "id": {"$in": candidates}},
                {"$set": {
                    "metadata.legacy_orphan": True,
                    "metadata.archived_at": now_iso,
                    "metadata.archive_reason": reason,
                    "metadata.archived_by_user_id": uid,
                }},
            )
            action = "archive"

        # Audit-trail entry (no balance touched).
        await db.accounting_audit_log.insert_one({
            "user_id": uid,
            "actor_id": uid,
            "timestamp": now_iso,
            "action": f"orphan_{action}",
            "summary": (
                f"{action.title()} of {res.modified_count} legacy "
                f"orphan ledger entries — reason: {reason}"
            ),
            "affected_ledger_ids": candidates,
            "iter": "iter226",
        })

        return {
            "success": True,
            "action": action,
            "matched": len(candidates),
            "modified": res.modified_count,
            "reason": reason,
            "archived_at": now_iso if not unarchive else None,
        }

    @router.get("/employee-orphan-openings/archive/status")
    async def archive_status(
        user: dict = Depends(current_user),
    ):
        """Quick read-only count: how many ledger entries are currently
        flagged `metadata.legacy_orphan=True` for this user."""
        uid = user["id"]
        archived_count = await db.general_ledger.count_documents({
            "user_id": uid,
            "entity_type": "employee",
            "metadata.legacy_orphan": True,
        })
        archived_total = 0.0
        async for row in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid,
                "entity_type": "employee",
                "metadata.legacy_orphan": True,
            }},
            {"$group": {
                "_id": "$side",
                "total": {"$sum": "$amount"},
            }},
        ]):
            if row["_id"] == "debit":
                archived_total += float(row.get("total") or 0)
            else:
                archived_total -= float(row.get("total") or 0)
        return {
            "success": True,
            "archived_count": archived_count,
            "archived_net_amount": round(archived_total, 2),
        }

    return router
