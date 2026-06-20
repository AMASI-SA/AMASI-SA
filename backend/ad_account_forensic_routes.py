"""Iter-250b-P0 — Ad-Account write-paths forensic catalog (READ-ONLY).

Outputs:
  1. WRITE_PATHS  — every mutation site in ad_account_routes.py grouped
     by endpoint, with target collection and SSOT classification.
  2. GET /api/audit/ad-account-write-paths-catalog
     (static, fast — no DB scans).
  3. GET /api/audit/ad-account-balance-forensic?ad_account_id=<id>
     (live cross-check between counterparties.current_balance,
     ledger(ad_account/balance), ledger(ad_account/debt),
     ad_account_ledger sum, and liabilities.balance).

No writes, no recomputes, no DB mutations.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


# ════════════════════════════════════════════════════════════════════
# 1. STATIC CATALOG — every mutation site discovered by code scan.
# ════════════════════════════════════════════════════════════════════
WRITE_PATHS: List[Dict[str, Any]] = [
    # ── /topup (POST /ad-accounts/{cp_id}/topup) ──────────────────
    {"endpoint": "POST /ad-accounts/{cp_id}/topup",
     "file": "ad_account_routes.py", "line": 526,
     "collection": "account_transactions", "op": "insert_one",
     "purpose": "خصم البنك (Legacy AT row)",
     "ssot_status": "DUPLICATE", "risk": "HIGH"},
    {"endpoint": "POST /ad-accounts/{cp_id}/topup",
     "file": "ad_account_routes.py", "line": 1044,
     "collection": "liabilities", "op": "update_one",
     "purpose": "تخفيض دين الحساب الإعلاني",
     "ssot_status": "LEGACY", "risk": "HIGH"},
    {"endpoint": "POST /ad-accounts/{cp_id}/topup",
     "file": "ad_account_routes.py", "line": 1057,
     "collection": "counterparties", "op": "update_one",
     "purpose": "تحديث current_balance + debt_balance",
     "ssot_status": "CACHE", "risk": "HIGH"},
    {"endpoint": "POST /ad-accounts/{cp_id}/topup",
     "file": "ad_account_routes.py", "line": 1078,
     "collection": "general_ledger", "op": "insert_many",
     "purpose": "Iter-203 SSOT — 2-leg balanced entry",
     "ssot_status": "SSOT", "risk": "LOW"},
    {"endpoint": "POST /ad-accounts/{cp_id}/topup",
     "file": "ad_account_routes.py", "line": 397,
     "collection": "ad_account_ledger", "op": "insert_one",
     "purpose": "Legacy ad_account_ledger row (متوازي)",
     "ssot_status": "LEGACY", "risk": "MEDIUM"},

    # ── /topup edit (PUT /ad-accounts/{cp_id}/topup/{ledger_id}) ──
    {"endpoint": "PUT /ad-accounts/{cp_id}/topup/{ledger_id}",
     "file": "ad_account_routes.py", "line": 1164,
     "collection": "general_ledger", "op": "reverse + re-post",
     "purpose": "Iter-218 SSOT reverse + new entry",
     "ssot_status": "SSOT", "risk": "MEDIUM"},
    {"endpoint": "PUT /ad-accounts/{cp_id}/topup/{ledger_id}",
     "file": "ad_account_routes.py", "line": 1228,
     "collection": "account_transactions", "op": "delete + insert",
     "purpose": "تحديث الـ AT row القديم",
     "ssot_status": "DUPLICATE", "risk": "HIGH"},
    {"endpoint": "PUT /ad-accounts/{cp_id}/topup/{ledger_id}",
     "file": "ad_account_routes.py", "line": 1283,
     "collection": "ad_account_ledger", "op": "update_one",
     "purpose": "Legacy ledger update",
     "ssot_status": "LEGACY", "risk": "MEDIUM"},

    # ── /spend (POST /ad-accounts/{cp_id}/spend) ─────────────────
    {"endpoint": "POST /ad-accounts/{cp_id}/spend",
     "file": "ad_account_routes.py", "line": 1376,
     "collection": "counterparties", "op": "update_one",
     "purpose": "زيادة debt_balance",
     "ssot_status": "CACHE", "risk": "HIGH"},
    {"endpoint": "POST /ad-accounts/{cp_id}/spend",
     "file": "ad_account_routes.py", "line": 1391,
     "collection": "liabilities", "op": "update_one (or insert)",
     "purpose": "إنشاء/تحديث دين",
     "ssot_status": "LEGACY", "risk": "HIGH"},
    {"endpoint": "POST /ad-accounts/{cp_id}/spend",
     "file": "ad_account_routes.py", "line": 1422,
     "collection": "liabilities", "op": "insert_one",
     "purpose": "إنشاء سجل دين جديد",
     "ssot_status": "LEGACY", "risk": "MEDIUM"},
    {"endpoint": "POST /ad-accounts/{cp_id}/spend",
     "file": "ad_account_routes.py", "line": 2587,
     "collection": "general_ledger", "op": "insert (entries.append)",
     "purpose": "GL leg لـ ad_account entity",
     "ssot_status": "SSOT", "risk": "LOW"},

    # ── /adjustments (POST /ad-accounts/{cp_id}/adjustments) ─────
    {"endpoint": "POST /ad-accounts/{cp_id}/adjustments",
     "file": "ad_account_routes.py", "line": 783,
     "collection": "general_ledger", "op": "insert_many",
     "purpose": "تسوية يدوية في الـ ledger",
     "ssot_status": "SSOT", "risk": "MEDIUM"},

    # ── create / update / delete ad_account (POST /, PATCH, DELETE)
    {"endpoint": "POST /ad-accounts",
     "file": "ad_account_routes.py", "line": 1587,
     "collection": "counterparties", "op": "insert_one",
     "purpose": "إنشاء حساب إعلاني (counterparty doc)",
     "ssot_status": "MASTER", "risk": "LOW"},
    {"endpoint": "PATCH /ad-accounts/{cp_id}",
     "file": "ad_account_routes.py", "line": 1605,
     "collection": "counterparties", "op": "update_one",
     "purpose": "تعديل بيانات الحساب",
     "ssot_status": "MASTER", "risk": "LOW"},
    {"endpoint": "DELETE /ad-accounts/{cp_id}",
     "file": "ad_account_routes.py", "line": 1625,
     "collection": "counterparties", "op": "delete_one",
     "purpose": "حذف حساب إعلاني",
     "ssot_status": "MASTER", "risk": "HIGH"},

    # ── opening balance (PUT /ad-accounts/{cp_id}/opening) ────────
    {"endpoint": "PUT /ad-accounts/{cp_id}/opening",
     "file": "ad_account_routes.py", "line": 2506,
     "collection": "liabilities", "op": "delete + insert",
     "purpose": "إعادة كتابة الدين الافتتاحي",
     "ssot_status": "LEGACY", "risk": "HIGH"},
    {"endpoint": "PUT /ad-accounts/{cp_id}/opening",
     "file": "ad_account_routes.py", "line": 2547,
     "collection": "counterparties", "op": "update_one",
     "purpose": "تحديث opening_balance",
     "ssot_status": "MASTER", "risk": "MEDIUM"},
    {"endpoint": "PUT /ad-accounts/{cp_id}/opening",
     "file": "ad_account_routes.py", "line": 2553,
     "collection": "ad_account_ledger", "op": "insert_one",
     "purpose": "Legacy opening ledger row",
     "ssot_status": "LEGACY", "risk": "MEDIUM"},

    # ── recover/recompute-debt-from-ledger ────────────────────────
    {"endpoint": "POST /ad-accounts/{cp_id}/recover/recompute-debt-from-ledger",
     "file": "ad_account_routes.py", "line": 1729,
     "collection": "liabilities", "op": "update_one",
     "purpose": "إعادة احتساب الدين من الـ ledger",
     "ssot_status": "RECOVERY", "risk": "MEDIUM"},
    {"endpoint": "POST /ad-accounts/{cp_id}/recover/recompute-debt-from-ledger",
     "file": "ad_account_routes.py", "line": 1759,
     "collection": "counterparties", "op": "update_one",
     "purpose": "إعادة احتساب debt_balance",
     "ssot_status": "RECOVERY", "risk": "MEDIUM"},

    # ── recover/cross-account-leak ────────────────────────────────
    {"endpoint": "POST /ad-accounts/recover/cross-account-leak",
     "file": "ad_account_routes.py", "line": 1852,
     "collection": "ad_account_ledger", "op": "delete_one",
     "purpose": "حذف سطور تسرّب بين الحسابات",
     "ssot_status": "RECOVERY", "risk": "HIGH"},
    {"endpoint": "POST /ad-accounts/recover/cross-account-leak",
     "file": "ad_account_routes.py", "line": 1857,
     "collection": "liabilities", "op": "update_many",
     "purpose": "إعادة ربط الديون",
     "ssot_status": "RECOVERY", "risk": "HIGH"},
    {"endpoint": "POST /ad-accounts/recover/cross-account-leak",
     "file": "ad_account_routes.py", "line": 1871,
     "collection": "counterparties", "op": "update_one",
     "purpose": "تصحيح balances",
     "ssot_status": "RECOVERY", "risk": "HIGH"},

    # ── migration (preview / apply / cleanup-duplicates) ──────────
    {"endpoint": "POST /ad-accounts/migration/apply",
     "file": "ad_account_routes.py", "line": 2061,
     "collection": "counterparties + liabilities + ad_account_ledger",
     "op": "bulk mutate",
     "purpose": "هجرة لمرة واحدة — يجب عدم إعادة تشغيلها",
     "ssot_status": "ONE_SHOT", "risk": "HIGH"},
    {"endpoint": "POST /ad-accounts/migration/cleanup-duplicates",
     "file": "ad_account_routes.py", "line": 2371,
     "collection": "counterparties + liabilities + ad_account_ledger",
     "op": "bulk mutate",
     "purpose": "تنظيف التكرارات (one-shot)",
     "ssot_status": "ONE_SHOT", "risk": "HIGH"},

    # ── duplicate topups cleanup ──────────────────────────────────
    {"endpoint": "POST /ad-accounts/diagnostics/duplicate-topups/cleanup",
     "file": "ad_account_routes.py", "line": 2782,
     "collection": "account_transactions + counterparties + "
                   "liabilities + ad_account_ledger",
     "op": "bulk mutate",
     "purpose": "تنظيف topups مكررة",
     "ssot_status": "ONE_SHOT", "risk": "HIGH"},

    # ── snapchat sync (ad spend ingestion from external API) ──────
    {"endpoint": "POST /snapchat/sync (per ad_account)",
     "file": "snapchat_routes.py", "line": 1091,
     "collection": "ad_account_ledger + general_ledger",
     "op": "insert_many",
     "purpose": "استيراد المصاريف اليومية",
     "ssot_status": "SSOT", "risk": "MEDIUM"},
]


# Aggregate stats over WRITE_PATHS
def _catalog_summary() -> Dict[str, Any]:
    by_collection: Dict[str, int] = {}
    by_ssot: Dict[str, int] = {}
    by_risk: Dict[str, int] = {}
    by_endpoint: Dict[str, int] = {}
    for w in WRITE_PATHS:
        c = w["collection"]
        by_collection[c] = by_collection.get(c, 0) + 1
        by_ssot[w["ssot_status"]] = by_ssot.get(w["ssot_status"], 0) + 1
        by_risk[w["risk"]] = by_risk.get(w["risk"], 0) + 1
        by_endpoint[w["endpoint"]] = (
            by_endpoint.get(w["endpoint"], 0) + 1
        )
    duplicates = [
        w for w in WRITE_PATHS if w["ssot_status"] == "DUPLICATE"
    ]
    legacy = [
        w for w in WRITE_PATHS
        if w["ssot_status"] in ("LEGACY", "CACHE")
    ]
    return {
        "total_write_sites": len(WRITE_PATHS),
        "distinct_endpoints": len(by_endpoint),
        "by_collection": by_collection,
        "by_ssot_status": by_ssot,
        "by_risk": by_risk,
        "duplicate_writes_count": len(duplicates),
        "legacy_or_cache_writes_count": len(legacy),
        "ssot_writes_count": by_ssot.get("SSOT", 0),
    }


def _r(n) -> float:
    return round(float(n or 0), 2)


def make_ad_account_forensic_router(db, current_user):
    router = APIRouter(tags=["audit", "ad_accounts"])

    # ── catalog (static) ──────────────────────────────────────────
    @router.get("/audit/ad-account-write-paths-catalog")
    async def catalog(
        user: dict = Depends(current_user),
    ):
        return {
            "ok": True,
            "iter": "iter250b-p0",
            "read_only": True,
            "summary": _catalog_summary(),
            "write_paths": WRITE_PATHS,
            "recommended_ssot": {
                "writes_to": "general_ledger ONLY (Iter-203 pattern)",
                "balance_read_from": (
                    "general_ledger entity_type=ad_account "
                    "sub_account ∈ {balance, debt}"
                ),
                "cache_field": (
                    "counterparties.current_balance / debt_balance "
                    "are CACHE only — never the source of truth"
                ),
                "to_deprecate": [
                    "ad_account_ledger (legacy collection)",
                    "liabilities for ad spend (replaced by GL "
                    "sub_account=debt)",
                    "account_transactions row for topup bank leg "
                    "(replaced by GL bank leg)",
                ],
            },
        }

    # ── live forensic for ONE ad account ──────────────────────────
    @router.get("/audit/ad-account-balance-forensic")
    async def live(
        ad_account_id: str = Query(
            ..., description="counterparty id of an ad account"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        cp = await db.counterparties.find_one(
            {"id": ad_account_id, "user_id": uid,
             "type": "ad_account"},
            {"_id": 0},
        )
        if not cp:
            raise HTTPException(
                404,
                "Ad account not found (counterparty type=ad_account).",
            )

        cb_balance = _r(cp.get("current_balance"))
        cb_debt = _r(cp.get("debt_balance"))
        cb_opening = _r(cp.get("opening_balance"))

        # general_ledger split by sub_account
        async def _gl_net(sub: str) -> Dict[str, Any]:
            d = c = dn = cn = 0
            async for r in db.general_ledger.aggregate([
                {"$match": {
                    "user_id": uid,
                    "entity_type": "ad_account",
                    "entity_id": ad_account_id,
                    "sub_account": sub,
                    "status": "posted",
                    "entry_type": {"$ne": "reversal"},
                }},
                {"$group": {
                    "_id": "$side",
                    "total": {"$sum": "$amount"},
                    "n": {"$sum": 1},
                }},
            ]):
                if r["_id"] == "debit":
                    d, dn = float(r["total"]), int(r["n"])
                elif r["_id"] == "credit":
                    c, cn = float(r["total"]), int(r["n"])
            return {"debits": _r(d), "credits": _r(c),
                    "net": _r(d - c),
                    "debit_count": dn, "credit_count": cn,
                    "row_count": dn + cn}

        gl_balance = await _gl_net("balance")
        gl_debt = await _gl_net("debt")
        gl_any = await _gl_net("main")  # rare for ad accounts

        # ad_account_ledger (legacy)
        legacy_n = 0
        legacy_sum = 0.0
        async for r in db.ad_account_ledger.aggregate([
            {"$match": {"user_id": uid,
                        "counterparty_id": ad_account_id}},
            {"$group": {"_id": None,
                        "n": {"$sum": 1},
                        "sum": {"$sum": "$amount"}}},
        ]):
            legacy_n = int(r["n"])
            legacy_sum = _r(r["sum"])

        # liabilities sum
        liab_sum = 0.0
        liab_n = 0
        async for r in db.liabilities.aggregate([
            {"$match": {"user_id": uid,
                        "counterparty_id": ad_account_id}},
            {"$group": {"_id": None,
                        "n": {"$sum": 1},
                        "sum": {"$sum": "$balance"}}},
        ]):
            liab_n = int(r["n"])
            liab_sum = _r(r["sum"])

        # account_transactions sum (ad_topup / ad-related)
        atx_n = 0
        atx_sum_in = atx_sum_out = 0.0
        atx_by_type: Dict[str, int] = {}
        async for r in db.account_transactions.aggregate([
            {"$match": {"user_id": uid,
                        "$or": [
                            {"counterparty_id": ad_account_id},
                            {"ad_account_id": ad_account_id},
                            {"reference_id": ad_account_id},
                        ]}},
            {"$group": {"_id": {"t": "$transaction_type",
                                "d": "$direction"},
                        "n": {"$sum": 1},
                        "sum": {"$sum": "$amount"}}},
        ]):
            tt = r["_id"].get("t") or "<null>"
            d = r["_id"].get("d") or "<null>"
            atx_by_type[f"{tt}/{d}"] = r["n"]
            atx_n += r["n"]
            if d == "in":
                atx_sum_in += float(r["sum"])
            else:
                atx_sum_out += float(r["sum"])

        # ── Verdicts ──────────────────────────────────────────────
        verdicts: List[Dict[str, Any]] = []

        # 1) Does counterparties.debt_balance match GL debt?
        delta1 = _r(cb_debt - gl_debt["net"])
        verdicts.append({
            "check": (
                "counterparties.debt_balance == "
                "ledger(ad_account, sub_account=debt)"
            ),
            "left": cb_debt, "right": gl_debt["net"],
            "delta": delta1,
            "matches": abs(delta1) <= 0.02,
        })

        # 2) Does counterparties.current_balance match GL balance?
        delta2 = _r(cb_balance - gl_balance["net"])
        verdicts.append({
            "check": (
                "counterparties.current_balance == "
                "ledger(ad_account, sub_account=balance)"
            ),
            "left": cb_balance, "right": gl_balance["net"],
            "delta": delta2,
            "matches": abs(delta2) <= 0.02,
        })

        # 3) Is liabilities still being written to?
        verdicts.append({
            "check": "liabilities collection empty for this account",
            "left": liab_n, "right": 0,
            "delta": liab_n,
            "matches": liab_n == 0,
            "note": (
                "إن وُجدت سطور، فإن الـ legacy path (Iter-118) ما "
                "زال نشطاً ويسبب ازدواج كتابة."
            ),
        })

        # 4) Is ad_account_ledger (legacy) still being written to?
        verdicts.append({
            "check": "ad_account_ledger (legacy) empty",
            "left": legacy_n, "right": 0,
            "delta": legacy_n,
            "matches": legacy_n == 0,
            "note": (
                "هذا الجدول قديم. أي سطر فيه يعني أن /topup أو "
                "/spend ما زالا يكتبان فيه بالتوازي."
            ),
        })

        verdict_score = sum(1 for v in verdicts if v["matches"])
        ssot_health = (
            "HEALTHY" if verdict_score == len(verdicts)
            else "PARTIAL" if verdict_score >= 2
            else "BROKEN"
        )

        return {
            "ok": True,
            "iter": "iter250b-p0",
            "read_only": True,
            "ad_account": {
                "id": cp.get("id"),
                "name": cp.get("name"),
                "platform": cp.get("platform"),
                "currency": cp.get("currency"),
                "opening_balance": cb_opening,
                "current_balance_cached": cb_balance,
                "debt_balance_cached": cb_debt,
            },
            "general_ledger": {
                "sub_account_balance": gl_balance,
                "sub_account_debt": gl_debt,
                "sub_account_main_if_any": gl_any,
            },
            "legacy_collections": {
                "ad_account_ledger": {
                    "row_count": legacy_n,
                    "sum": legacy_sum,
                    "still_written": legacy_n > 0,
                },
                "liabilities": {
                    "row_count": liab_n,
                    "sum_balance": liab_sum,
                    "still_written": liab_n > 0,
                },
                "account_transactions": {
                    "row_count": atx_n,
                    "sum_in": _r(atx_sum_in),
                    "sum_out": _r(atx_sum_out),
                    "net": _r(atx_sum_in - atx_sum_out),
                    "by_type": atx_by_type,
                    "still_written": atx_n > 0,
                },
            },
            "verdicts": verdicts,
            "ssot_health": ssot_health,
            "recommendation": (
                "أوقف الكتابة في liabilities + ad_account_ledger + "
                "account_transactions داخل /topup و /spend و "
                "/opening. اجعل general_ledger هو SSOT الوحيد. "
                "حدّث counterparties.{current,debt}_balance من "
                "Iter-160 trigger فقط (cache)."
            ) if ssot_health != "HEALTHY" else (
                "النموذج صحي — كل المصادر متطابقة. لا حاجة لأي "
                "إصلاح."
            ),
        }

    return router
