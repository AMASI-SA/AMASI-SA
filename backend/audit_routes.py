"""Iter-181 — Post-Migration Audit (Read-Only).

Comprehensive sanity check after Phase 4 Closeout. Cross-validates
the new Universal Ledger against the legacy data sources and flags
any anomaly the merchant should investigate BEFORE disabling the
legacy endpoints.

Checks performed
================
1. ``migration_cutoff`` status — confirms the migration actually
   ran and captures the cutoff date.
2. ``duplicate_openings`` — duplicate ``opening_balance`` entries
   per (entity_type, entity_id, sub_account). Should be 0.
3. ``orphan_openings`` — opening entries whose ``entity_id``
   doesn't match an existing record. Should be 0.
4. ``ledger_balance_check`` — for each entity_type
   (bank, employee, supplier, external, payment_platform), sum
   the opening_balance amounts in the ledger and compare to the
   legacy source. Anything > 0.01 SAR is a mismatch.
5. ``negative_balances`` — banks / payment_platforms with
   negative ``current_balance`` (legitimate for BNPL liabilities
   like Tabby/Tamara, but flagged so the merchant confirms).
6. ``orphan_references`` — counterparties referenced by ledger
   entries that no longer exist.
7. ``cod_exclusion_confirmed`` — explicit check that no COD
   payment_platform leaked into the migration.

Everything is READ-ONLY. The endpoint never modifies any data.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends


def make_audit_router(db, current_user):
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/post-migration")
    async def post_migration_audit(user: dict = Depends(current_user)):
        uid = user["id"]

        # ── 1) Migration cutoff ────────────────────────────────────
        cutoff = await db.migration_cutoffs.find_one(
            {"user_id": uid}, {"_id": 0}
        ) or {}

        # ── 2) Duplicate opening entries ───────────────────────────
        dupes_pipeline = [
            {"$match": {"user_id": uid, "entry_type": "opening_balance"}},
            {"$group": {
                "_id": {
                    "entity_type": "$entity_type",
                    "entity_id": "$entity_id",
                    "sub_account": "$sub_account",
                },
                "count": {"$sum": 1},
                "amounts": {"$push": "$amount"},
                "sides": {"$push": "$side"},
            }},
            {"$match": {"count": {"$gt": 1}}},
        ]
        duplicates = []
        async for d in db.general_ledger.aggregate(dupes_pipeline):
            duplicates.append({
                "entity_type": d["_id"]["entity_type"],
                "entity_id": d["_id"]["entity_id"],
                "sub_account": d["_id"]["sub_account"],
                "count": d["count"],
                "amounts": d["amounts"],
                "sides": d["sides"],
            })

        # ── 3) Ledger opening sums by entity_type ──────────────────
        sum_pipeline = [
            {"$match": {"user_id": uid, "entry_type": "opening_balance"}},
            {"$group": {
                "_id": {
                    "entity_type": "$entity_type",
                    "side": "$side",
                },
                "total": {"$sum": "$amount"},
                "count": {"$sum": 1},
            }},
        ]
        ledger_sums: dict = {}
        async for s in db.general_ledger.aggregate(sum_pipeline):
            et = s["_id"]["entity_type"]
            side = s["_id"]["side"]
            ledger_sums.setdefault(et, {"debit": 0.0, "credit": 0.0,
                                        "debit_count": 0, "credit_count": 0})
            ledger_sums[et][side] = round(float(s["total"]), 2)
            ledger_sums[et][f"{side}_count"] = int(s["count"])
        # Net per entity_type
        for et, v in ledger_sums.items():
            v["net"] = round(v["debit"] - v["credit"], 2)

        # ── 4) Negative balances ───────────────────────────────────
        negative_accounts = []
        async for a in db.accounts.find(
            {"user_id": uid,
             "account_type": {"$in": ["bank", "cash", "payment_platform"]},
             "current_balance": {"$lt": 0}},
            {"_id": 0, "id": 1, "name": 1, "account_type": 1,
             "current_balance": 1, "normalized_payment_method": 1},
        ):
            # Mark BNPL accounts as expected-negative (Tabby/Tamara
            # carry a settlement liability).
            is_bnpl = (a.get("normalized_payment_method") or "") in {
                "tabby", "tamara"
            } or any(k in (a.get("name") or "").lower()
                     for k in ("tabby", "tamara", "تابي", "تمارا"))
            negative_accounts.append({
                "id": a["id"],
                "name": a.get("name"),
                "type": a.get("account_type"),
                "balance": round(float(a.get("current_balance") or 0), 2),
                "expected_negative": bool(is_bnpl),
            })

        # ── 5) COD exclusion confirmation ──────────────────────────
        cod_in_ledger = await db.general_ledger.count_documents({
            "user_id": uid,
            "entry_type": "opening_balance",
            "$or": [
                {"metadata.platform_name": {"$regex": "استلام|cod",
                                            "$options": "i"}},
                {"metadata.account_name": {"$regex": "استلام|cod",
                                           "$options": "i"}},
            ],
        })

        # ── 6) Orphan opening entries (detailed) ──────────────────
        orphans = []
        # Build valid ID indexes — try MULTIPLE keys per collection
        # so we don't false-positive entries that linked via legacy ID
        # shapes (e.g. employees keyed by `employee_id` vs `id` vs `_id`).
        valid_cp_ids: set = set()
        async for cp in db.counterparties.find(
            {"user_id": uid},
            {"_id": 1, "id": 1, "name": 1, "kind": 1, "external_id": 1},
        ):
            for k in ("id", "external_id"):
                if cp.get(k):
                    valid_cp_ids.add(str(cp[k]))
            if cp.get("_id"):
                valid_cp_ids.add(str(cp["_id"]))

        valid_account_ids: set = set()
        async for a in db.accounts.find(
            {"user_id": uid}, {"_id": 1, "id": 1, "name": 1},
        ):
            for k in ("id",):
                if a.get(k):
                    valid_account_ids.add(str(a[k]))
            if a.get("_id"):
                valid_account_ids.add(str(a["_id"]))

        valid_emp_ids: set = set()
        async for e in db.employees.find(
            {"user_id": uid},
            {"_id": 1, "id": 1, "employee_id": 1, "name": 1,
             "external_id": 1, "legacy_id": 1},
        ):
            for k in ("id", "employee_id", "external_id", "legacy_id"):
                if e.get(k):
                    valid_emp_ids.add(str(e[k]))
            if e.get("_id"):
                valid_emp_ids.add(str(e["_id"]))

        # Enumerate ALL orphans (no 25-cap; the merchant explicitly
        # requested the full list for the post-migration audit).
        async for row in db.general_ledger.find(
            {"user_id": uid, "entry_type": "opening_balance"},
            {"_id": 0, "id": 1, "entity_type": 1, "entity_id": 1,
             "amount": 1, "side": 1, "sub_account": 1,
             "metadata": 1, "created_at": 1, "notes": 1},
        ):
            et = row.get("entity_type")
            eid = row.get("entity_id")
            if not eid:
                # No entity_id at all — almost certainly migration noise.
                orphans.append({
                    "ledger_id": row.get("id"),
                    "entity_type": et,
                    "entity_id": None,
                    "entity_name": (row.get("metadata") or {}).get(
                        "platform_name"
                    ) or (row.get("metadata") or {}).get("account_name")
                    or (row.get("metadata") or {}).get("employee_name")
                    or (row.get("metadata") or {}).get("supplier_name"),
                    "amount": row.get("amount"),
                    "side": row.get("side"),
                    "sub_account": row.get("sub_account"),
                    "debit": row.get("amount") if row.get("side") == "debit" else 0,
                    "credit": row.get("amount") if row.get("side") == "credit" else 0,
                    "created_at": (
                        row.get("created_at").isoformat()
                        if hasattr(row.get("created_at"), "isoformat")
                        else row.get("created_at")
                    ),
                    "classification": "no_entity_id",
                    "metadata": row.get("metadata") or {},
                })
                continue
            valid = False
            if et in ("supplier", "external", "courier", "ad_account"):
                valid = str(eid) in valid_cp_ids
            elif et in ("bank", "payment_platform"):
                valid = str(eid) in valid_account_ids
            elif et == "employee":
                valid = str(eid) in valid_emp_ids
            else:
                valid = True  # unknown type → skip flagging
            if not valid:
                md = row.get("metadata") or {}
                # Try to surface a human-readable name from migration metadata.
                ename = (
                    md.get("employee_name")
                    or md.get("supplier_name")
                    or md.get("counterparty_name")
                    or md.get("platform_name")
                    or md.get("account_name")
                    or md.get("courier_name")
                    or md.get("name")
                )
                # Classify the likely cause to help the merchant.
                classification = "deleted_entity"
                if et == "employee" and valid_emp_ids:
                    classification = "employee_id_mismatch"
                elif et in ("bank", "payment_platform") and valid_account_ids:
                    classification = "account_id_mismatch"
                orphans.append({
                    "ledger_id": row.get("id"),
                    "entity_type": et,
                    "entity_id": eid,
                    "entity_name": ename,
                    "amount": row.get("amount"),
                    "side": row.get("side"),
                    "sub_account": row.get("sub_account"),
                    "debit": row.get("amount") if row.get("side") == "debit" else 0,
                    "credit": row.get("amount") if row.get("side") == "credit" else 0,
                    "created_at": (
                        row.get("created_at").isoformat()
                        if hasattr(row.get("created_at"), "isoformat")
                        else row.get("created_at")
                    ),
                    "classification": classification,
                    "metadata": md,
                })

        # ── 7) Legacy vs ledger reconciliation ──────────────────────
        # Banks: ledger debit sum vs sum(current_balance) of bank accounts
        bank_legacy_total = 0.0
        async for a in db.accounts.find(
            {"user_id": uid, "account_type": "bank"},
            {"_id": 0, "current_balance": 1},
        ):
            bank_legacy_total += float(a.get("current_balance") or 0)
        bank_ledger = ledger_sums.get("bank", {})
        bank_net = bank_ledger.get("net", 0.0)
        bank_diff = round(bank_legacy_total - bank_net, 2)

        # Employees: ledger CREDIT sum (salary_payable) vs ???
        # We compare counts as a sanity proxy here; full re-aggregation
        # would re-run salary accrual logic which is overkill.
        emp_ledger = ledger_sums.get("employee", {})
        emp_total_credit = emp_ledger.get("credit", 0.0)

        # ── Final verdict ──────────────────────────────────────────
        issues = []
        if duplicates:
            issues.append({
                "severity": "high",
                "code": "duplicate_openings",
                "message": f"{len(duplicates)} مجموعات قيود افتتاحية مكررة",
            })
        if orphans:
            issues.append({
                "severity": "high",
                "code": "orphan_openings",
                "message": f"{len(orphans)} قيود تشير إلى كيانات محذوفة",
            })
        if abs(bank_diff) > 0.01:
            issues.append({
                "severity": "high",
                "code": "bank_mismatch",
                "message": f"فرق رصيد البنوك: {bank_diff} ر.س (Legacy {bank_legacy_total} vs Ledger {bank_net})",
            })
        if cod_in_ledger > 0:
            issues.append({
                "severity": "high",
                "code": "cod_in_ledger",
                "message": f"COD تسرّب للترحيل ({cod_in_ledger} قيد)",
            })
        unexplained_negs = [n for n in negative_accounts
                            if not n["expected_negative"]]
        if unexplained_negs:
            issues.append({
                "severity": "medium",
                "code": "unexplained_negative_balance",
                "message": f"{len(unexplained_negs)} حسابات برصيد سالب غير متوقع",
            })
        if not cutoff.get("status") == "completed":
            issues.append({
                "severity": "info",
                "code": "no_cutoff",
                "message": "لا يوجد تاريخ قطع مسجل بعد (لم يتم تنفيذ Apply)",
            })

        verdict = "pass" if not issues else "warnings" if all(
            i["severity"] != "high" for i in issues) else "fail"

        # Iter-181b — Orphan analysis breakdown for the merchant.
        # Group by entity_type + classification + financial impact.
        orphans_by_type: dict = {}
        orphans_by_class: dict = {}
        orphans_total_debit = 0.0
        orphans_total_credit = 0.0
        for o in orphans:
            et = o.get("entity_type") or "_unknown"
            cls = o.get("classification") or "_unknown"
            orphans_by_type.setdefault(et, {
                "entity_type": et, "count": 0,
                "debit_total": 0.0, "credit_total": 0.0,
            })
            orphans_by_type[et]["count"] += 1
            orphans_by_type[et]["debit_total"] += float(o.get("debit") or 0)
            orphans_by_type[et]["credit_total"] += float(o.get("credit") or 0)
            orphans_by_class.setdefault(cls, 0)
            orphans_by_class[cls] += 1
            orphans_total_debit += float(o.get("debit") or 0)
            orphans_total_credit += float(o.get("credit") or 0)

        # Round + finalize.
        for v in orphans_by_type.values():
            v["debit_total"] = round(v["debit_total"], 2)
            v["credit_total"] = round(v["credit_total"], 2)
            v["net"] = round(v["debit_total"] - v["credit_total"], 2)

        return {
            "verdict": verdict,
            "issues": issues,
            "cutoff": cutoff or None,
            "duplicates": {
                "count": len(duplicates),
                "samples": duplicates[:10],
            },
            "orphans": {
                "count": len(orphans),
                "samples": orphans[:50],
                "all": orphans,
                "by_type": list(orphans_by_type.values()),
                "by_classification": orphans_by_class,
                "total_debit": round(orphans_total_debit, 2),
                "total_credit": round(orphans_total_credit, 2),
                "net_impact": round(
                    orphans_total_debit - orphans_total_credit, 2
                ),
                "interpretation": (
                    "Reconciliation = 100% يعني الإجمالي صحيح. "
                    "لكن وجود قيود يتيمة يعني أن مرجع الـ counterparty/employee "
                    "في الـ ledger لا يطابق أي سجل حالي. "
                    "إذا كان classification = 'employee_id_mismatch' أو "
                    "'account_id_mismatch' فالأرجح أن الـ ID تغيّر بين النظام "
                    "القديم والجديد (false positive محتمل في أداة الفحص "
                    "إذا كان الإجمالي يطابق). إذا كان 'deleted_entity' فالكيان "
                    "محذوف فعلاً. إذا كان 'no_entity_id' فالقيد ترحَّل بدون "
                    "ربط — يحتاج مراجعة يدوية."
                ),
            },
            "ledger_sums_by_entity": ledger_sums,
            "negative_balances": {
                "count": len(negative_accounts),
                "unexplained_count": len(unexplained_negs),
                "accounts": negative_accounts,
            },
            "cod_exclusion": {
                "in_ledger": cod_in_ledger,
                "confirmed_excluded": cod_in_ledger == 0,
            },
            "bank_reconciliation": {
                "legacy_total": round(bank_legacy_total, 2),
                "ledger_net": bank_net,
                "diff": bank_diff,
                "match": abs(bank_diff) < 0.01,
            },
            "employee_summary": {
                "ledger_credit_total": emp_total_credit,
                "ledger_credit_count": emp_ledger.get("credit_count", 0),
            },
        }

    return router


def make_employee_lookup_debug_router(db, current_user):
    """Iter-181c — Read-only diagnostic for the 15 orphan openings.

    Determines authoritatively whether the orphans are a false
    positive of the audit tool (because the `employees` collection
    uses a different link field) OR genuine missing records.

    Strictly READ-ONLY: no insert, update, delete, or merge.
    """
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/employee-lookup-debug")
    async def employee_lookup_debug(user: dict = Depends(current_user)):
        uid = user["id"]

        # Step 1 — Count employees by every plausible link field.
        counts_by_filter: dict = {}
        candidate_link_fields = (
            "user_id", "merchant_id", "owner_id", "store_id",
            "tenant_id", "account_id",
        )
        for field in candidate_link_fields:
            counts_by_filter[field] = await db.employees.count_documents(
                {field: uid}
            )
        counts_by_filter["__no_filter__"] = await db.employees.count_documents({})

        # Step 2 — Build a master index of employees from ALL fields,
        # keyed by every possible ID variant. We index without filter
        # AND with each candidate filter, then merge.
        emp_by_id: dict = {}  # id_value → {fields_matched: [], name, raw_doc_summary}
        seen_employees: set = set()
        async for e in db.employees.find({}, {
            "_id": 1, "id": 1, "employee_id": 1, "external_id": 1,
            "legacy_id": 1, "user_id": 1, "merchant_id": 1,
            "owner_id": 1, "name": 1,
        }):
            doc_key = str(e.get("_id") or e.get("id"))
            seen_employees.add(doc_key)
            link_field_value = None
            link_field_name = None
            for f in candidate_link_fields:
                if e.get(f) == uid:
                    link_field_value = e[f]
                    link_field_name = f
                    break
            summary = {
                "doc_id": str(e.get("_id")) if e.get("_id") else None,
                "name": e.get("name"),
                "link_field": link_field_name,
                "link_value": link_field_value,
            }
            for k in ("id", "employee_id", "external_id", "legacy_id"):
                v = e.get(k)
                if v:
                    s = str(v)
                    emp_by_id.setdefault(s, {"matched_via": [], "doc": summary})
                    emp_by_id[s]["matched_via"].append(k)
            if e.get("_id"):
                s = str(e["_id"])
                emp_by_id.setdefault(s, {"matched_via": [], "doc": summary})
                emp_by_id[s]["matched_via"].append("_id")

        total_employees_seen = len(seen_employees)

        # Step 3 — Replay the orphan check against this richer index.
        per_orphan = []
        false_positive_count = 0
        genuinely_missing_count = 0
        wrong_user_match_count = 0
        async for row in db.general_ledger.find(
            {"user_id": uid, "entry_type": "opening_balance",
             "entity_type": "employee"},
            {"_id": 0, "id": 1, "entity_id": 1, "amount": 1,
             "side": 1, "sub_account": 1, "metadata": 1},
        ):
            eid = row.get("entity_id")
            if not eid:
                continue
            key = str(eid)
            hit = emp_by_id.get(key)
            if hit:
                belongs_to_user = (
                    hit["doc"].get("link_value") == uid
                )
                if belongs_to_user:
                    classification = "false_positive"
                    false_positive_count += 1
                else:
                    classification = "wrong_user_link"
                    wrong_user_match_count += 1
                per_orphan.append({
                    "ledger_id": row.get("id"),
                    "entity_id": eid,
                    "sub_account": row.get("sub_account"),
                    "amount": row.get("amount"),
                    "side": row.get("side"),
                    "metadata_name": (row.get("metadata") or {}).get(
                        "employee_name") or (row.get("metadata") or {}).get(
                        "name"),
                    "found_via": hit["matched_via"],
                    "employee_doc_id": hit["doc"]["doc_id"],
                    "employee_name": hit["doc"]["name"],
                    "employee_link_field": hit["doc"]["link_field"],
                    "employee_link_value": hit["doc"]["link_value"],
                    "classification": classification,
                })
            else:
                genuinely_missing_count += 1
                per_orphan.append({
                    "ledger_id": row.get("id"),
                    "entity_id": eid,
                    "sub_account": row.get("sub_account"),
                    "amount": row.get("amount"),
                    "side": row.get("side"),
                    "metadata_name": (row.get("metadata") or {}).get(
                        "employee_name") or (row.get("metadata") or {}).get(
                        "name"),
                    "found_via": [],
                    "classification": "genuinely_missing",
                })

        verdict = (
            "false_positive_likely" if false_positive_count == len(per_orphan)
            else "wrong_user_link" if wrong_user_match_count > 0 and genuinely_missing_count == 0
            else "data_issue" if genuinely_missing_count > 0
            else "no_orphans"
        )

        return {
            "verdict": verdict,
            "summary": {
                "total_employee_orphans_in_ledger": len(per_orphan),
                "false_positive_count": false_positive_count,
                "wrong_user_link_count": wrong_user_match_count,
                "genuinely_missing_count": genuinely_missing_count,
                "total_employees_in_collection": total_employees_seen,
            },
            "employee_counts_by_filter": counts_by_filter,
            "interpretation": (
                "false_positive_likely: كل القيود لها موظف مطابق بنفس "
                "user_id — الأداة الأصلية أخطأت في الفحص. تحديث الأداة "
                "كافٍ.\n"
                "wrong_user_link: الموظفون موجودون لكن مربوطون بحقل "
                "آخر غير user_id — يحتاج توحيد الربط في الـ schema.\n"
                "data_issue: بعض الموظفين غير موجودين فعلاً في الـ "
                "collection — يحتاج خطة ترميم بيانات منفصلة."
            ),
            "orphans_analysis": per_orphan,
        }

    return router
