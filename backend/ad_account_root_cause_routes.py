"""Iter-250b Phase 0.6 — Ad-Account Root-Cause Forensic (READ-ONLY).

Deep per-account analysis for accounts flagged MANUAL_REVIEW_REQUIRED.
Surfaces:
  • Last 30 GL entries (sorted by date desc)
  • GL aggregations by: sub_account, entry_type, source, reference_id,
    txn_group_id
  • Topups vs Spends breakdown
  • Misattribution & missing-platform detection
  • Identity hints (Snapchat vs other)
  • Per-account accounting recommendation tree

  GET /api/audit/ad-account-root-cause?ad_account_id=<id>

100% read-only — no writes, no recomputes, no DB mutations.
"""
from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query


TOL = 0.02


def _r(n) -> float:
    return round(float(n or 0), 2)


def make_ad_account_root_cause_router(db, current_user):
    router = APIRouter(tags=["audit", "ad_accounts"])

    @router.get("/audit/ad-account-root-cause")
    async def root_cause(
        ad_account_id: str = Query(..., description="Counterparty id"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1. Identity & platform check ──────────────────────────
        cp = await db.counterparties.find_one(
            {"id": ad_account_id, "user_id": uid}, {"_id": 0},
        )
        if not cp:
            raise HTTPException(404, "Ad account not found.")

        identity_hints: List[str] = []
        name = (cp.get("name") or "").lower()
        platform = cp.get("platform") or cp.get("ad_platform")
        if not platform:
            # Try to infer from name
            if "snap" in name:
                identity_hints.append(
                    "اسم الحساب يحوي 'snap' — مرشّح بقوة كـ Snapchat.")
            elif "self service" in name or "self-service" in name:
                identity_hints.append(
                    "اسم Self Service عام جداً — Snap/TikTok/Meta "
                    "لديها كلها Self Service. ابحث في metadata.platform "
                    "ضمن قيود GL لتحديد الهوية."
                )
            elif "meta" in name or "facebook" in name:
                identity_hints.append(
                    "اسم الحساب يحوي 'meta' أو 'facebook'.")
            elif "tiktok" in name:
                identity_hints.append("اسم الحساب يحوي 'tiktok'.")
            else:
                identity_hints.append(
                    "platform غير محدد، والاسم لا يكشف الهوية.")
        else:
            identity_hints.append(
                f"platform مُحدّد في counterparty: {platform}")

        # Look inside GL metadata for platform hints
        platform_hints_from_gl: Dict[str, int] = {}
        async for r in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid, "entity_type": "ad_account",
                "entity_id": ad_account_id, "status": "posted",
            }},
            {"$group": {
                "_id": "$metadata.platform", "n": {"$sum": 1},
            }},
        ]):
            k = r["_id"] or "<null>"
            platform_hints_from_gl[k] = r["n"]

        # ── 2. Last 30 GL entries ─────────────────────────────────
        last30: List[Dict[str, Any]] = []
        async for r in db.general_ledger.find(
            {"user_id": uid, "entity_type": "ad_account",
             "entity_id": ad_account_id, "status": "posted"},
            {"_id": 0, "id": 1, "entry_no": 1, "entry_type": 1,
             "sub_account": 1, "side": 1, "amount": 1,
             "currency": 1, "posted_at": 1, "txn_group_id": 1,
             "notes": 1, "metadata": 1},
        ).sort([("posted_at", -1)]).limit(30):
            last30.append(r)

        # ── 3. Aggregations ───────────────────────────────────────
        async def _aggregate(key: str) -> Dict[str, Any]:
            out: Dict[str, Any] = {}
            async for r in db.general_ledger.aggregate([
                {"$match": {
                    "user_id": uid, "entity_type": "ad_account",
                    "entity_id": ad_account_id, "status": "posted",
                    "entry_type": {"$ne": "reversal"},
                }},
                {"$group": {
                    "_id": {"k": f"${key}", "side": "$side"},
                    "n": {"$sum": 1},
                    "sum": {"$sum": "$amount"},
                }},
            ]):
                k = r["_id"].get("k") or "<null>"
                side = r["_id"].get("side") or "<null>"
                bucket = out.setdefault(str(k), {
                    "debit_count": 0, "credit_count": 0,
                    "debit_sum": 0.0, "credit_sum": 0.0,
                    "net": 0.0,
                })
                if side == "debit":
                    bucket["debit_count"] = r["n"]
                    bucket["debit_sum"] = _r(r["sum"])
                else:
                    bucket["credit_count"] = r["n"]
                    bucket["credit_sum"] = _r(r["sum"])
                bucket["net"] = _r(
                    bucket["debit_sum"] - bucket["credit_sum"])
            return out

        by_sub_account = await _aggregate("sub_account")
        by_entry_type = await _aggregate("entry_type")
        by_source = await _aggregate("metadata.source")
        by_reference_id = await _aggregate("metadata.reference_id")

        # txn_group_id aggregation — only show groups with >1 leg
        # or unusual patterns.
        by_txn_group: Dict[str, Dict[str, Any]] = {}
        async for r in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid, "entity_type": "ad_account",
                "entity_id": ad_account_id, "status": "posted",
            }},
            {"$group": {
                "_id": "$txn_group_id",
                "n": {"$sum": 1},
                "debit_sum": {"$sum": {"$cond": [
                    {"$eq": ["$side", "debit"]}, "$amount", 0]}},
                "credit_sum": {"$sum": {"$cond": [
                    {"$eq": ["$side", "credit"]}, "$amount", 0]}},
                "entry_types": {"$addToSet": "$entry_type"},
                "sub_accounts": {"$addToSet": "$sub_account"},
            }},
            {"$sort": {"n": -1}},
            {"$limit": 20},
        ]):
            k = r["_id"] or "<null>"
            by_txn_group[str(k)] = {
                "leg_count": r["n"],
                "debit_sum": _r(r["debit_sum"]),
                "credit_sum": _r(r["credit_sum"]),
                "net": _r(r["debit_sum"] - r["credit_sum"]),
                "entry_types": r["entry_types"],
                "sub_accounts": r["sub_accounts"],
            }

        # ── 4. Topups vs Spends ───────────────────────────────────
        topup_total = 0.0
        topup_count = 0
        spend_total = 0.0
        spend_count = 0
        adjustment_total = 0.0
        adjustment_count = 0
        opening_total = 0.0
        opening_count = 0

        async for r in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid, "entity_type": "ad_account",
                "entity_id": ad_account_id, "status": "posted",
                "entry_type": {"$ne": "reversal"},
            }},
            {"$group": {
                "_id": "$entry_type",
                "n": {"$sum": 1},
                "debit_sum": {"$sum": {"$cond": [
                    {"$eq": ["$side", "debit"]}, "$amount", 0]}},
                "credit_sum": {"$sum": {"$cond": [
                    {"$eq": ["$side", "credit"]}, "$amount", 0]}},
            }},
        ]):
            et = r["_id"] or "<null>"
            net = _r(r["debit_sum"] - r["credit_sum"])
            if "topup" in et.lower() or "deposit" in et.lower() \
                    or et == "ad_topup":
                topup_total = _r(topup_total + abs(net))
                topup_count += r["n"]
            elif "spend" in et.lower() or "expense" in et.lower() \
                    or et == "ad_spend":
                spend_total = _r(spend_total + abs(net))
                spend_count += r["n"]
            elif "opening" in et.lower():
                opening_total = _r(opening_total + abs(net))
                opening_count += r["n"]
            elif "adjustment" in et.lower():
                adjustment_total = _r(adjustment_total + abs(net))
                adjustment_count += r["n"]

        topups_vs_spends = {
            "topups_total": topup_total,
            "topups_count": topup_count,
            "spends_total": spend_total,
            "spends_count": spend_count,
            "opening_total": opening_total,
            "opening_count": opening_count,
            "adjustment_total": adjustment_total,
            "adjustment_count": adjustment_count,
            "difference (topups − spends − opening)": _r(
                topup_total - spend_total - opening_total),
        }

        # ── 5. Misattribution detection ───────────────────────────
        misattribution: List[Dict[str, Any]] = []

        # 5a) Entries with no platform in metadata
        no_platform_count = await db.general_ledger.count_documents({
            "user_id": uid, "entity_type": "ad_account",
            "entity_id": ad_account_id, "status": "posted",
            "$or": [
                {"metadata.platform": {"$exists": False}},
                {"metadata.platform": None},
                {"metadata.platform": ""},
            ],
        })
        if no_platform_count > 0:
            misattribution.append({
                "kind": "missing_platform",
                "count": no_platform_count,
                "note": (
                    "قيود في GL لهذا الحساب لا تحوي "
                    "metadata.platform — يصعب التحقّق من نسبتها "
                    "للحساب الصحيح."
                ),
            })

        # 5b) Entries whose metadata.bank_account_id refers to an
        # account that no longer exists
        suspicious_bank_refs: List[str] = []
        async for r in db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid, "entity_type": "ad_account",
                "entity_id": ad_account_id, "status": "posted",
                "metadata.bank_account_id": {"$exists": True,
                                             "$ne": None},
            }},
            {"$group": {"_id": "$metadata.bank_account_id"}},
        ]):
            bank_id = r["_id"]
            if bank_id:
                exists = await db.counterparties.count_documents(
                    {"id": bank_id, "user_id": uid})
                exists += await db.accounts.count_documents(
                    {"id": bank_id, "user_id": uid})
                if exists == 0:
                    suspicious_bank_refs.append(bank_id)
        if suspicious_bank_refs:
            misattribution.append({
                "kind": "orphan_bank_reference",
                "count": len(suspicious_bank_refs),
                "ids": suspicious_bank_refs,
                "note": (
                    "قيود تشير إلى bank_account_id لا يوجد في "
                    "أي من counterparties/accounts."
                ),
            })

        # ── 6. Accounting recommendation tree ─────────────────────
        gl_balance_net = (
            by_sub_account.get("balance", {}).get("net", 0.0))
        gl_debt_net = (
            by_sub_account.get("debt", {}).get("net", 0.0))
        suggestions: List[Dict[str, Any]] = []

        if gl_balance_net > TOL and gl_debt_net < -TOL:
            # The case for Meta and الرياض.
            suggestions.append({
                "kind": "review_sub_account_split",
                "title": (
                    "ادرس فصل sub_account بين balance و debt"),
                "explain": (
                    f"GL(balance) = {gl_balance_net} موجب وغير "
                    f"صفري، و GL(debt) = {gl_debt_net} سالب. "
                    "هذا يعني أن topups سُجِّلت في sub_account=balance "
                    "بينما المصاريف انعكست في sub_account=debt. "
                    "الأصل أن topups تُغذّي debt (تُخفِّضه) أو تخلق "
                    "رصيداً موجباً، وأن spends تزيد debt. اختر "
                    "تصميماً واحداً وثبّته."
                ),
                "action_hint": (
                    "إنشاء قيد تسوية يدوي في general_ledger "
                    "ينقل المبلغ من sub_account=balance إلى "
                    "sub_account=debt (أو العكس) حسب التصميم "
                    "المعتمد."
                ),
            })

        if gl_debt_net < -TOL and gl_balance_net <= TOL \
                and topup_total > spend_total + TOL:
            # The case for Self Service.
            suggestions.append({
                "kind": "missing_spend_entries",
                "title": "إضافة spend مفقود",
                "explain": (
                    f"المدفوعات (topups) = {topup_total} تفوق "
                    f"المصاريف المسجّلة (spends) = {spend_total} "
                    f"بفارق {_r(topup_total - spend_total)}. "
                    "إما أن مصاريف الفترة لم تُسجَّل، أو أن topup "
                    "نُسب لهذا الحساب بالخطأ."
                ),
                "action_hint": (
                    "1) راجع كشف الإنفاق من المنصة (Snap/Meta) "
                    "للفترة المقابلة وقارن المجاميع. "
                    "2) إن وجدت spend ناقص أنشئ قيد ad_spend "
                    "يدوي في GL. "
                    "3) إن وجدت topup خاطئ، أنشئ قيد عكسي "
                    "(reversal) ثم أعد القيد على الحساب الصحيح."
                ),
            })

        if no_platform_count > 0:
            suggestions.append({
                "kind": "tag_missing_platform",
                "title": "أضف metadata.platform للقيود",
                "explain": (
                    f"{no_platform_count} قيد بدون "
                    "metadata.platform — يصعب التتبع."
                ),
                "action_hint": (
                    "إضافة platform يدوياً في metadata عبر admin "
                    "endpoint مستقل (لا يُغيّر الأرقام، فقط الـ tags)."
                ),
            })

        if "Self Service" in (cp.get("name") or ""):
            suggestions.append({
                "kind": "rename_self_service",
                "title": "إعادة تسمية Self Service",
                "explain": (
                    "اسم 'Self Service' عام جداً ولا يحدّد المنصّة. "
                    "كل المنصّات لديها واجهة Self Service. هذا "
                    "يجعل التتبع أصعب."
                ),
                "action_hint": (
                    "بناءً على platform_hints_from_gl، اختر الاسم "
                    "الصحيح (Snap Self Service / Meta Self Service "
                    "/ TikTok Self Service) عبر PATCH على "
                    "counterparty doc."
                ),
            })

        # ── 7. Final decision matrix ──────────────────────────────
        decision = {
            "can_just_recompute_cache": (
                len(suggestions) == 0
                or all(s["kind"] in ("tag_missing_platform",
                                     "rename_self_service")
                       for s in suggestions)
            ),
            "needs_gl_adjustment": any(
                s["kind"] in ("review_sub_account_split",
                              "missing_spend_entries")
                for s in suggestions
            ),
            "needs_metadata_cleanup": no_platform_count > 0,
            "needs_rename": "Self Service" in (cp.get("name") or ""),
        }

        return {
            "ok": True,
            "iter": "iter250b-p0.6-root-cause",
            "read_only": True,
            "ad_account": {
                "id": cp.get("id"),
                "name": cp.get("name"),
                "platform_from_counterparty": platform,
                "currency": cp.get("currency"),
                "kind": cp.get("kind"),
                "current_balance_cached":
                    _r(cp.get("current_balance")),
                "debt_balance_cached":
                    _r(cp.get("debt_balance")),
            },
            "identity_hints": identity_hints,
            "platform_hints_from_gl_metadata": platform_hints_from_gl,
            "topups_vs_spends": topups_vs_spends,
            "aggregations": {
                "by_sub_account": by_sub_account,
                "by_entry_type": by_entry_type,
                "by_source": by_source,
                "by_reference_id_top": by_reference_id,
                "by_txn_group_top20": by_txn_group,
            },
            "misattribution_signals": misattribution,
            "last_30_gl_entries": last30,
            "accounting_suggestions": suggestions,
            "decision_matrix": decision,
            "constraints_honored": [
                "No writes to general_ledger",
                "No writes to counterparties",
                "No writes to liabilities",
                "No writes to ad_account_ledger",
                "No recompute / migration / cleanup",
                "No feature flag",
            ],
        }

    return router
