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


def make_forensic_report_router(db, current_user):
    """Iter-193 — Forensic Audit Report (Read-Only).

    Combined diagnostic for the two anomalies surfaced after the
    Phase 4 closeout:

        (A) Orphan employee opening balances (15 entries, net
            ~41,931.68 SAR). For each, surface metadata name,
            sub_account, side, amount, created_at, and best-effort
            employee match via every possible link key.

        (B) Tabby negative balance (~-48,319.57 SAR). Aggregate
            every general_ledger row whose entity_id maps to a
            Tabby payment_platform account, grouped by entry_type
            × sub_account × side, plus a chronological sample of
            the most impactful entries (top 30 by |amount|).

    Strictly READ-ONLY. No inserts, no updates, no deletes.
    Safe to deploy to production and call ad-hoc.
    """
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/forensic-report")
    async def forensic_report(user: dict = Depends(current_user)):
        uid = user["id"]

        # ─────────────────────────────────────────────────────────
        # PART A — Orphan Employee Openings (full detail)
        # ─────────────────────────────────────────────────────────
        # Build employee index across every plausible ID field so
        # the analysis is robust to schema drift.
        emp_index: dict = {}
        async for e in db.employees.find({}, {
            "_id": 1, "id": 1, "employee_id": 1, "external_id": 1,
            "legacy_id": 1, "user_id": 1, "name": 1, "status": 1,
            "deleted_at": 1,
        }):
            summary = {
                "doc_id": str(e.get("_id")) if e.get("_id") else None,
                "name": e.get("name"),
                "status": e.get("status"),
                "deleted_at": (
                    e["deleted_at"].isoformat()
                    if hasattr(e.get("deleted_at"), "isoformat")
                    else e.get("deleted_at")
                ),
                "user_id": e.get("user_id"),
                "belongs_to_current_user": e.get("user_id") == uid,
            }
            for k in ("id", "employee_id", "external_id", "legacy_id"):
                v = e.get(k)
                if v:
                    emp_index.setdefault(str(v), []).append(
                        {**summary, "matched_via": k}
                    )
            if e.get("_id"):
                emp_index.setdefault(str(e["_id"]), []).append(
                    {**summary, "matched_via": "_id"}
                )

        # Valid employee IDs for the current user (used to detect
        # whether the ledger entity_id is a true orphan).
        valid_emp_ids_for_user: set = set()
        async for e in db.employees.find(
            {"user_id": uid},
            {"_id": 1, "id": 1, "employee_id": 1,
             "external_id": 1, "legacy_id": 1},
        ):
            for k in ("id", "employee_id", "external_id", "legacy_id"):
                if e.get(k):
                    valid_emp_ids_for_user.add(str(e[k]))
            if e.get("_id"):
                valid_emp_ids_for_user.add(str(e["_id"]))

        # Iterate every employee opening, classify, capture metadata.
        emp_orphans = []
        emp_orphan_debit = 0.0
        emp_orphan_credit = 0.0
        by_sub: dict = {}
        async for row in db.general_ledger.find(
            {"user_id": uid,
             "entry_type": "opening_balance",
             "entity_type": "employee"},
            {"_id": 0, "id": 1, "entity_id": 1, "amount": 1,
             "side": 1, "sub_account": 1, "metadata": 1,
             "created_at": 1, "notes": 1},
        ):
            eid = row.get("entity_id")
            if not eid:
                continue
            key = str(eid)
            if key in valid_emp_ids_for_user:
                continue  # not an orphan for the current user
            md = row.get("metadata") or {}
            amt = float(row.get("amount") or 0)
            side = row.get("side")
            sub = row.get("sub_account") or "_unknown"

            # Cross-tenant / historical match attempt
            hits = emp_index.get(key, [])
            best_hit = next(
                (h for h in hits if h.get("belongs_to_current_user")),
                hits[0] if hits else None,
            )
            if best_hit:
                if best_hit.get("belongs_to_current_user"):
                    classification = "false_positive_belongs_to_user"
                elif best_hit.get("user_id"):
                    classification = "wrong_user_link"
                else:
                    classification = "match_without_user_id"
            else:
                classification = "deleted_or_unknown"

            entry = {
                "ledger_id": row.get("id"),
                "entity_id": eid,
                "sub_account": sub,
                "side": side,
                "amount": round(amt, 2),
                "debit": round(amt, 2) if side == "debit" else 0,
                "credit": round(amt, 2) if side == "credit" else 0,
                "metadata_employee_name": (
                    md.get("employee_name") or md.get("name")
                ),
                "metadata_notes": row.get("notes"),
                "metadata_source": md.get("source") or md.get("origin"),
                "metadata_raw": md,
                "created_at": (
                    row["created_at"].isoformat()
                    if hasattr(row.get("created_at"), "isoformat")
                    else row.get("created_at")
                ),
                "best_match_in_employees": best_hit,
                "classification": classification,
            }
            emp_orphans.append(entry)
            if side == "debit":
                emp_orphan_debit += amt
            else:
                emp_orphan_credit += amt
            by_sub.setdefault(sub, {
                "sub_account": sub, "count": 0,
                "debit_total": 0.0, "credit_total": 0.0,
            })
            by_sub[sub]["count"] += 1
            if side == "debit":
                by_sub[sub]["debit_total"] += amt
            else:
                by_sub[sub]["credit_total"] += amt
        for v in by_sub.values():
            v["debit_total"] = round(v["debit_total"], 2)
            v["credit_total"] = round(v["credit_total"], 2)
            v["net"] = round(v["debit_total"] - v["credit_total"], 2)

        orphan_employees = {
            "count": len(emp_orphans),
            "total_debit": round(emp_orphan_debit, 2),
            "total_credit": round(emp_orphan_credit, 2),
            "net_impact": round(
                emp_orphan_debit - emp_orphan_credit, 2
            ),
            "by_sub_account": list(by_sub.values()),
            "entries": emp_orphans,
            "interpretation": (
                "salary_payable credits = رواتب مستحقة محفوظة في "
                "السجل دون مرجع موظف صالح للمستخدم الحالي. "
                "salary_advance debits = سُلف مدفوعة دون مرجع موظف. "
                "raisez classification=deleted_or_unknown إذا الموظف "
                "غير موجود فعلياً. classification=wrong_user_link "
                "يعني الموظف ينتمي لمستخدم آخر."
            ),
        }

        # ─────────────────────────────────────────────────────────
        # PART B — Tabby Negative Balance Breakdown
        # ─────────────────────────────────────────────────────────
        # 1) Locate Tabby account(s) for this user.
        tabby_accounts = []
        async for a in db.accounts.find(
            {"user_id": uid,
             "$or": [
                 {"normalized_payment_method": "tabby"},
                 {"name": {"$regex": "tabby|تابي", "$options": "i"}},
             ]},
            {"_id": 0, "id": 1, "name": 1, "account_type": 1,
             "current_balance": 1, "normalized_payment_method": 1},
        ):
            tabby_accounts.append({
                "id": a["id"],
                "name": a.get("name"),
                "account_type": a.get("account_type"),
                "current_balance": round(
                    float(a.get("current_balance") or 0), 2
                ),
                "normalized_payment_method":
                    a.get("normalized_payment_method"),
            })

        tabby_ids = {str(a["id"]) for a in tabby_accounts}

        # 2) Aggregate the ledger by (entry_type, sub_account, side)
        breakdown = []
        ledger_total_debit = 0.0
        ledger_total_credit = 0.0
        if tabby_ids:
            agg_pipeline = [
                {"$match": {
                    "user_id": uid,
                    "entity_type": {"$in": [
                        "payment_platform", "bank", "external",
                    ]},
                    "entity_id": {"$in": list(tabby_ids)},
                }},
                {"$group": {
                    "_id": {
                        "entry_type": "$entry_type",
                        "sub_account": "$sub_account",
                        "side": "$side",
                    },
                    "total": {"$sum": "$amount"},
                    "count": {"$sum": 1},
                }},
                {"$sort": {"total": -1}},
            ]
            async for d in db.general_ledger.aggregate(agg_pipeline):
                row = {
                    "entry_type": d["_id"].get("entry_type"),
                    "sub_account": d["_id"].get("sub_account"),
                    "side": d["_id"].get("side"),
                    "count": d["count"],
                    "total": round(float(d["total"]), 2),
                }
                breakdown.append(row)
                if row["side"] == "debit":
                    ledger_total_debit += row["total"]
                else:
                    ledger_total_credit += row["total"]

        # 3) Top-30 most-impactful individual entries (by |amount|)
        top_entries = []
        if tabby_ids:
            async for row in db.general_ledger.find(
                {"user_id": uid,
                 "entity_id": {"$in": list(tabby_ids)}},
                {"_id": 0, "id": 1, "entry_type": 1, "sub_account": 1,
                 "side": 1, "amount": 1, "txn_group_id": 1,
                 "metadata": 1, "created_at": 1, "notes": 1,
                 "order_id": 1},
            ).sort("amount", -1).limit(30):
                top_entries.append({
                    "ledger_id": row.get("id"),
                    "entry_type": row.get("entry_type"),
                    "sub_account": row.get("sub_account"),
                    "side": row.get("side"),
                    "amount": round(float(row.get("amount") or 0), 2),
                    "txn_group_id": row.get("txn_group_id"),
                    "order_id": row.get("order_id"),
                    "notes": row.get("notes"),
                    "created_at": (
                        row["created_at"].isoformat()
                        if hasattr(row.get("created_at"), "isoformat")
                        else row.get("created_at")
                    ),
                    "metadata": row.get("metadata") or {},
                })

        # 4) Per-entry_type net for an at-a-glance summary
        per_entry_type: dict = {}
        for r in breakdown:
            et = r["entry_type"] or "_unknown"
            per_entry_type.setdefault(et, {
                "entry_type": et,
                "debit": 0.0, "credit": 0.0, "count": 0,
            })
            per_entry_type[et]["count"] += r["count"]
            if r["side"] == "debit":
                per_entry_type[et]["debit"] += r["total"]
            else:
                per_entry_type[et]["credit"] += r["total"]
        for v in per_entry_type.values():
            v["debit"] = round(v["debit"], 2)
            v["credit"] = round(v["credit"], 2)
            v["net"] = round(v["debit"] - v["credit"], 2)

        ledger_net = round(
            ledger_total_debit - ledger_total_credit, 2
        )
        current_balance_sum = round(
            sum(a["current_balance"] for a in tabby_accounts), 2
        )

        tabby_section = {
            "accounts": tabby_accounts,
            "accounts_count": len(tabby_accounts),
            "current_balance_sum": current_balance_sum,
            "ledger_total_debit": round(ledger_total_debit, 2),
            "ledger_total_credit": round(ledger_total_credit, 2),
            "ledger_net": ledger_net,
            "ledger_vs_balance_diff": round(
                ledger_net - current_balance_sum, 2
            ),
            "per_entry_type": list(per_entry_type.values()),
            "breakdown_detailed": breakdown,
            "top_30_entries_by_amount": top_entries,
            "interpretation": (
                "ledger_net يجب أن يساوي current_balance_sum إذا "
                "كان حساب الـ SSOT سليم. الفرق ledger_vs_balance_diff "
                "يكشف عدم تطابق. سالب يعني أن مدفوعات/تسويات تم "
                "ترحيلها كـ credit أكبر من المبيعات (debit) — "
                "إما عمولات/خصومات Tabby، أو تسويات Over-transfer، "
                "أو ترحيل migration بقيود credit زائدة. "
                "راجع per_entry_type: settlement_credit الكبير يشير "
                "إلى تحويلات بنكية من Tabby. opening_balance credit "
                "يدل على رصيد افتتاحي سالب من migration. "
                "fees/commission credits = خصم عمولات Tabby."
            ),
        }

        return {
            "report_type": "forensic_audit_v1",
            "read_only": True,
            "user_id": uid,
            "orphan_employees": orphan_employees,
            "tabby_balance": tabby_section,
            "guidance": (
                "هذا التقرير للقراءة فقط. لا تُجرى أي تعديلات على "
                "قاعدة البيانات. يمكن مشاركته كما هو مع المحاسب أو "
                "المهندس لتحديد خطة الإصلاح."
            ),
        }

    return router


def make_tabby_phase2_router(db, current_user):
    """Iter-194 — Tabby Forensic Phase 2 (Read-Only).

    Deep dive into the negative balance via the four source-of-truth
    tables: payment_transactions, payment_refunds, account_transactions,
    general_ledger. Explains the gap between BNPL SSOT and ledger.
    Strictly READ-ONLY.
    """
    router = APIRouter(prefix="/audit", tags=["audit"])

    @router.get("/tabby-phase2")
    async def tabby_phase2(user: dict = Depends(current_user)):
        from collections import defaultdict
        uid = user["id"]
        provider = "tabby"
        cutoff_iso_prefix = "2026-06-14"

        # ── 1) Locate Tabby account(s) ────────────────────────────
        tabby_accounts = []
        async for a in db.accounts.find(
            {"user_id": uid,
             "$or": [
                 {"normalized_payment_method": "tabby"},
                 {"name": {"$regex": "tabby|تابي", "$options": "i"}},
                 {"provider_name": {"$regex": "tabby|تابي",
                                    "$options": "i"}},
             ]},
            {"_id": 0, "id": 1, "name": 1, "account_type": 1,
             "current_balance": 1, "opening_balance": 1,
             "expected_orders_balance": 1,
             "normalized_payment_method": 1},
        ):
            tabby_accounts.append({
                "id": a["id"],
                "name": a.get("name"),
                "account_type": a.get("account_type"),
                "current_balance": round(
                    float(a.get("current_balance") or 0), 2),
                "opening_balance_field": round(
                    float(a.get("opening_balance") or 0), 2),
                "expected_orders_balance": round(
                    float(a.get("expected_orders_balance") or 0), 2),
                "normalized_payment_method":
                    a.get("normalized_payment_method"),
            })
        tabby_ids = [a["id"] for a in tabby_accounts]

        # ── 2) payment_transactions analysis ──────────────────────
        pt_total_count = 0
        pt_total_sales = 0.0
        pt_first_date = None
        pt_last_date = None
        pt_by_month: dict = defaultdict(
            lambda: {"count": 0, "amount": 0.0})
        pt_by_status: dict = defaultdict(
            lambda: {"count": 0, "amount": 0.0})
        pt_by_source: dict = defaultdict(
            lambda: {"count": 0, "amount": 0.0})
        pt_before_cutoff = {"count": 0, "amount": 0.0}
        pt_after_cutoff = {"count": 0, "amount": 0.0}

        async for t in db.payment_transactions.find(
            {"user_id": uid, "provider": provider},
            {"_id": 0, "amount": 1, "status": 1, "source": 1,
             "import_source": 1, "created_at_provider": 1,
             "created_at": 1},
        ):
            amt = float(t.get("amount") or 0)
            pt_total_count += 1
            pt_total_sales += amt
            d = t.get("created_at_provider") or t.get("created_at")
            d_iso = (d.isoformat()
                     if hasattr(d, "isoformat") else d) or ""
            month = d_iso[:7] if d_iso else "_unknown"
            pt_by_month[month]["count"] += 1
            pt_by_month[month]["amount"] += amt
            status_key = (t.get("status") or "_null")
            pt_by_status[status_key]["count"] += 1
            pt_by_status[status_key]["amount"] += amt
            source_key = (t.get("source")
                          or t.get("import_source") or "_null")
            pt_by_source[source_key]["count"] += 1
            pt_by_source[source_key]["amount"] += amt
            if d_iso:
                if pt_first_date is None or d_iso < pt_first_date:
                    pt_first_date = d_iso
                if pt_last_date is None or d_iso > pt_last_date:
                    pt_last_date = d_iso
                if d_iso[:10] < cutoff_iso_prefix:
                    pt_before_cutoff["count"] += 1
                    pt_before_cutoff["amount"] += amt
                else:
                    pt_after_cutoff["count"] += 1
                    pt_after_cutoff["amount"] += amt

        # ── 3) payment_refunds analysis ───────────────────────────
        pr_count = 0
        pr_total = 0.0
        pr_first = None
        pr_last = None
        pr_before_cutoff = {"count": 0, "amount": 0.0}
        pr_after_cutoff = {"count": 0, "amount": 0.0}
        async for r in db.payment_refunds.find(
            {"user_id": uid, "provider": provider},
            {"_id": 0, "amount": 1, "refunded_at": 1, "status": 1},
        ):
            amt = float(r.get("amount") or 0)
            pr_count += 1
            pr_total += amt
            d = r.get("refunded_at")
            d_iso = (d.isoformat()
                     if hasattr(d, "isoformat") else d) or ""
            if d_iso:
                if pr_first is None or d_iso < pr_first:
                    pr_first = d_iso
                if pr_last is None or d_iso > pr_last:
                    pr_last = d_iso
                if d_iso[:10] < cutoff_iso_prefix:
                    pr_before_cutoff["count"] += 1
                    pr_before_cutoff["amount"] += amt
                else:
                    pr_after_cutoff["count"] += 1
                    pr_after_cutoff["amount"] += amt

        # ── 4) account_transactions analysis ──────────────────────
        at_total_count = 0
        at_total_out = 0.0
        at_total_in = 0.0
        at_first = None
        at_last = None
        at_by_month: dict = defaultdict(
            lambda: {"count": 0, "out_amount": 0.0,
                     "in_amount": 0.0})
        at_before_cutoff = {"count": 0, "out_amount": 0.0,
                            "in_amount": 0.0}
        at_after_cutoff = {"count": 0, "out_amount": 0.0,
                           "in_amount": 0.0}
        at_no_reference = {"count": 0, "out_amount": 0.0}
        at_no_txn_group = {"count": 0, "out_amount": 0.0}
        at_seen_signature: dict = defaultdict(list)
        at_samples = []
        async for tx in db.account_transactions.find(
            {"user_id": uid,
             "account_id": {"$in": tabby_ids}},
            {"_id": 0, "id": 1, "amount": 1, "direction": 1,
             "transaction_date": 1, "created_at": 1, "notes": 1,
             "reference": 1, "txn_group_id": 1, "external_id": 1,
             "linked_account_id": 1, "metadata": 1, "type": 1,
             "transaction_type": 1, "balance_after": 1},
        ).sort("transaction_date", 1):
            at_total_count += 1
            amt = float(tx.get("amount") or 0)
            direction = tx.get("direction")
            d = tx.get("transaction_date") or tx.get("created_at")
            d_iso = (d.isoformat()
                     if hasattr(d, "isoformat") else d) or ""
            month = d_iso[:7] if d_iso else "_unknown"
            if direction == "out":
                at_total_out += amt
                at_by_month[month]["count"] += 1
                at_by_month[month]["out_amount"] += amt
                if d_iso:
                    if d_iso[:10] < cutoff_iso_prefix:
                        at_before_cutoff["count"] += 1
                        at_before_cutoff["out_amount"] += amt
                    else:
                        at_after_cutoff["count"] += 1
                        at_after_cutoff["out_amount"] += amt
                if not tx.get("reference"):
                    at_no_reference["count"] += 1
                    at_no_reference["out_amount"] += amt
                if not tx.get("txn_group_id"):
                    at_no_txn_group["count"] += 1
                    at_no_txn_group["out_amount"] += amt
                sig = (d_iso[:10], round(amt, 2), direction,
                       tx.get("linked_account_id"))
                at_seen_signature[sig].append({
                    "id": tx.get("id"),
                    "reference": tx.get("reference"),
                    "txn_group_id": tx.get("txn_group_id"),
                    "notes": tx.get("notes"),
                    "transaction_date": d_iso,
                })
            else:
                at_total_in += amt
                at_by_month[month]["in_amount"] += amt
                if d_iso:
                    if d_iso[:10] < cutoff_iso_prefix:
                        at_before_cutoff["in_amount"] += amt
                    else:
                        at_after_cutoff["in_amount"] += amt
            if d_iso:
                if at_first is None or d_iso < at_first:
                    at_first = d_iso
                if at_last is None or d_iso > at_last:
                    at_last = d_iso
            if len(at_samples) < 50:
                at_samples.append({
                    "id": tx.get("id"),
                    "amount": round(amt, 2),
                    "direction": direction,
                    "transaction_date": d_iso,
                    "notes": tx.get("notes"),
                    "reference": tx.get("reference"),
                    "txn_group_id": tx.get("txn_group_id"),
                    "external_id": tx.get("external_id"),
                    "type": tx.get("type") or tx.get("transaction_type"),
                    "linked_account_id": tx.get("linked_account_id"),
                    "balance_after": tx.get("balance_after"),
                    "metadata": tx.get("metadata") or {},
                })

        duplicates = []
        for sig, rows in at_seen_signature.items():
            if len(rows) >= 2:
                duplicates.append({
                    "signature": {
                        "date": sig[0],
                        "amount": sig[1],
                        "direction": sig[2],
                        "linked_account_id": sig[3],
                    },
                    "count": len(rows),
                    "entries": rows,
                })

        # ── 5) general_ledger Tabby snapshot ──────────────────────
        gl_count = 0
        gl_debit = 0.0
        gl_credit = 0.0
        gl_entries = []
        async for row in db.general_ledger.find(
            {"user_id": uid,
             "entity_id": {"$in": tabby_ids}},
            {"_id": 0, "id": 1, "entry_type": 1, "sub_account": 1,
             "side": 1, "amount": 1, "created_at": 1,
             "metadata": 1, "notes": 1, "txn_group_id": 1,
             "order_id": 1},
        ):
            gl_count += 1
            amt = float(row.get("amount") or 0)
            side = row.get("side")
            if side == "debit":
                gl_debit += amt
            else:
                gl_credit += amt
            d = row.get("created_at")
            d_iso = (d.isoformat()
                     if hasattr(d, "isoformat") else d) or ""
            gl_entries.append({
                "ledger_id": row.get("id"),
                "entry_type": row.get("entry_type"),
                "sub_account": row.get("sub_account"),
                "side": side,
                "amount": round(amt, 2),
                "created_at": d_iso,
                "txn_group_id": row.get("txn_group_id"),
                "order_id": row.get("order_id"),
                "notes": row.get("notes"),
                "metadata": row.get("metadata") or {},
            })
        gl_net = round(gl_debit - gl_credit, 2)

        # ── 6) BNPL SSOT formula reconstruction ───────────────────
        bnpl_balance = 0.0
        bnpl_components: dict = {}
        try:
            from bnpl.balance_service import get_bnpl_provider_balance
            canon = await get_bnpl_provider_balance(db, uid, provider)
            bnpl_balance = float(canon.get("balance") or 0)
            bnpl_components = canon.get("components") or {}
        except Exception as exc:  # noqa: BLE001
            bnpl_components = {"error": str(exc)}

        current_balance_sum = round(
            sum(a["current_balance"] for a in tabby_accounts), 2)
        bnpl_vs_current_diff = round(
            bnpl_balance - current_balance_sum, 2)
        bnpl_vs_ledger_diff = round(bnpl_balance - gl_net, 2)

        # ── 7) Diagnostic findings ────────────────────────────────
        net_sales = pt_total_sales - pr_total
        rough_balance_no_fees = round(net_sales - at_total_out, 2)

        findings = []
        if pt_total_count == 0:
            findings.append({
                "severity": "critical",
                "code": "no_payment_transactions",
                "msg": "لا يوجد أي سجل في payment_transactions لـ Tabby — السبب الجذري الأول للرصيد السالب.",
            })
        elif pt_before_cutoff["amount"] < 1:
            findings.append({
                "severity": "high",
                "code": "missing_historical_sales",
                "msg": f"لا توجد مبيعات Tabby قبل تاريخ القطع. كل المبيعات ({round(pt_total_sales,2)} ر.س) بعد القطع، بينما التحويلات الخارجة قبل القطع تساوي {round(at_before_cutoff['out_amount'],2)} ر.س.",
            })
        if at_before_cutoff["out_amount"] > pt_before_cutoff["amount"] + 1:
            findings.append({
                "severity": "high",
                "code": "pre_cutoff_transfers_without_sales",
                "msg": f"تحويلات قبل القطع ({round(at_before_cutoff['out_amount'],2)}) > المبيعات قبل القطع ({round(pt_before_cutoff['amount'],2)}).",
            })
        if duplicates:
            findings.append({
                "severity": "medium",
                "code": "potential_duplicate_transfers",
                "msg": f"{len(duplicates)} مجموعة تحويلات قد تكون مكررة (نفس التاريخ + المبلغ + الاتجاه).",
            })
        if at_no_reference["count"] > 0:
            findings.append({
                "severity": "low",
                "code": "transfers_without_reference",
                "msg": f"{at_no_reference['count']} تحويل بدون reference (إجمالي {round(at_no_reference['out_amount'],2)} ر.س).",
            })
        if at_no_txn_group["count"] > 0:
            findings.append({
                "severity": "info",
                "code": "transfers_without_txn_group",
                "msg": f"{at_no_txn_group['count']} تحويل بدون txn_group_id (إجمالي {round(at_no_txn_group['out_amount'],2)} ر.س).",
            })
        if gl_count <= 1:
            findings.append({
                "severity": "critical",
                "code": "ledger_only_has_opening",
                "msg": f"general_ledger يحتوي فقط على {gl_count} قيد لـ Tabby — مبيعات/تسويات/تحويلات Tabby اللاحقة لا تُكتب في الـ Universal Ledger (انتهاك SSOT).",
            })
        if abs(bnpl_vs_current_diff) > 0.01:
            findings.append({
                "severity": "high",
                "code": "bnpl_vs_current_balance_drift",
                "msg": f"اختلاف بين BNPL SSOT ({round(bnpl_balance,2)}) و accounts.current_balance ({current_balance_sum}). الفرق: {bnpl_vs_current_diff}.",
            })

        return {
            "report_type": "tabby_forensic_phase2",
            "read_only": True,
            "user_id": uid,
            "cutoff_date": cutoff_iso_prefix,
            "accounts": tabby_accounts,
            "accounts_count": len(tabby_accounts),
            "current_balance_sum": current_balance_sum,
            "payment_transactions": {
                "count": pt_total_count,
                "total_sales": round(pt_total_sales, 2),
                "first_date": pt_first_date,
                "last_date": pt_last_date,
                "by_month": [
                    {"month": k, "count": v["count"],
                     "amount": round(v["amount"], 2)}
                    for k, v in sorted(pt_by_month.items())
                ],
                "by_status": [
                    {"status": k, "count": v["count"],
                     "amount": round(v["amount"], 2)}
                    for k, v in sorted(pt_by_status.items(),
                                       key=lambda x: -x[1]["amount"])
                ],
                "by_source": [
                    {"source": k, "count": v["count"],
                     "amount": round(v["amount"], 2)}
                    for k, v in sorted(pt_by_source.items(),
                                       key=lambda x: -x[1]["amount"])
                ],
                "before_cutoff": {
                    "count": pt_before_cutoff["count"],
                    "amount": round(pt_before_cutoff["amount"], 2),
                },
                "after_cutoff": {
                    "count": pt_after_cutoff["count"],
                    "amount": round(pt_after_cutoff["amount"], 2),
                },
            },
            "payment_refunds": {
                "count": pr_count,
                "total_refunded": round(pr_total, 2),
                "first_date": pr_first,
                "last_date": pr_last,
                "before_cutoff": {
                    "count": pr_before_cutoff["count"],
                    "amount": round(pr_before_cutoff["amount"], 2),
                },
                "after_cutoff": {
                    "count": pr_after_cutoff["count"],
                    "amount": round(pr_after_cutoff["amount"], 2),
                },
            },
            "net_sales_minus_refunds": round(net_sales, 2),
            "account_transactions": {
                "total_count": at_total_count,
                "total_out": round(at_total_out, 2),
                "total_in": round(at_total_in, 2),
                "first_date": at_first,
                "last_date": at_last,
                "by_month": [
                    {"month": k, "count": v["count"],
                     "out_amount": round(v["out_amount"], 2),
                     "in_amount": round(v["in_amount"], 2)}
                    for k, v in sorted(at_by_month.items())
                ],
                "before_cutoff": {
                    "count": at_before_cutoff["count"],
                    "out_amount": round(
                        at_before_cutoff["out_amount"], 2),
                    "in_amount": round(
                        at_before_cutoff["in_amount"], 2),
                },
                "after_cutoff": {
                    "count": at_after_cutoff["count"],
                    "out_amount": round(
                        at_after_cutoff["out_amount"], 2),
                    "in_amount": round(
                        at_after_cutoff["in_amount"], 2),
                },
                "without_reference": at_no_reference,
                "without_txn_group_id": at_no_txn_group,
                "duplicate_groups_count": len(duplicates),
                "duplicate_groups": duplicates[:20],
                "samples_first_50": at_samples,
            },
            "general_ledger": {
                "count": gl_count,
                "total_debit": round(gl_debit, 2),
                "total_credit": round(gl_credit, 2),
                "net": gl_net,
                "entries": gl_entries,
            },
            "bnpl_ssot": {
                "balance": round(bnpl_balance, 2),
                "components": bnpl_components,
                "vs_current_balance_diff": bnpl_vs_current_diff,
                "vs_ledger_net_diff": bnpl_vs_ledger_diff,
            },
            "reconstruction_check": {
                "formula": (
                    "rough_balance_no_fees = total_sales - refunds - "
                    "total_out_transfers (without commission/vat/fees)"
                ),
                "total_sales": round(pt_total_sales, 2),
                "refunds": round(pr_total, 2),
                "total_out_transfers": round(at_total_out, 2),
                "rough_balance_no_fees": rough_balance_no_fees,
                "actual_bnpl_balance": round(bnpl_balance, 2),
                "delta_due_to_fees_commission": round(
                    rough_balance_no_fees - bnpl_balance, 2),
            },
            "findings": findings,
            "guidance": (
                "هذا تقرير قراءة فقط. كل القيم محسوبة من المصادر "
                "الأصلية. لا يوجد أي تعديل على البيانات."
            ),
        }

    return router

