"""Iter-249 — Bank Statement UI Audit (READ-ONLY).

The user reports two BNPL settlements (TAMARA-2026-06-06-AUTO and
TABBY-2026-06-08-AUTO) that:
  • Exist in `account_transactions` (verified by Iter-248 backfill).
  • Do NOT show up in the bank-account statement UI
    (`/accounts/{bank_id}` page).

This endpoint traces, end-to-end, why the UI filters them out — without
mutating anything.

  GET /api/audit/account-statement-trace?account_id=<bank_id>

Output shape:
{
  "ok": true,
  "iter": "iter249",
  "read_only": true,
  "account_id": "<bank_id>",
  "account": {...summary...},
  "ui_path_decision": {
      "is_migrated": bool,
      "branch_taken": "ledger_based" | "legacy_account_transactions",
      "branch_source_collection": "general_ledger" | "account_transactions",
      "reason": "..."
  },
  "ui_query_simulation": {
      "filter": {...exact mongo filter the UI uses...},
      "total_rows": int,
      "by_transaction_type": {"...": count},
      "bnpl_settlement_rows_returned": int
  },
  "account_transactions_collection": {
      "all_rows_for_account_count": int,
      "by_transaction_type": {"...": count},
      "bnpl_settlement_rows": [...],
      "two_target_references": {
          "TAMARA-2026-06-06-AUTO": {...},
          "TABBY-2026-06-08-AUTO": {...}
      }
  },
  "general_ledger_collection": {
      "bank_entity_rows_for_account_count": int,
      "by_entry_type": {"...": count},
      "bnpl_settlement_rows": [...],
      "two_target_references_in_ledger": {
          "TAMARA-2026-06-06-AUTO": {...},
          "TABBY-2026-06-08-AUTO": {...}
      }
  },
  "appears_in_last_50_ui_rows": {
      "TAMARA-2026-06-06-AUTO": bool,
      "TABBY-2026-06-08-AUTO": bool
  },
  "root_cause": {
      "category": "backend_query" | "frontend_mapping" | "pagination"
                 | "sorting" | "cache" | "different_data_source"
                 | "data_missing",
      "summary": "...",
      "evidence": [...],
      "proposed_fix": "..."
  }
}
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


TARGET_REFS = ["TAMARA-2026-06-06-AUTO", "TABBY-2026-06-08-AUTO"]


def _strip_id(d: Optional[dict]) -> Optional[dict]:
    if not d:
        return d
    d.pop("_id", None)
    return d


def make_bnpl_statement_ui_audit_router(db, current_user):
    router = APIRouter(tags=["audit", "bnpl"])

    @router.get("/audit/account-statement-trace")
    async def trace_account_statement(
        account_id: str = Query(..., description="Bank account id"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1. Account summary ─────────────────────────────────────
        acc = await db.accounts.find_one(
            {"id": account_id, "user_id": uid}, {"_id": 0},
        )
        if not acc:
            raise HTTPException(404, "Account not found for this user.")

        # ── 2. UI path decision (mirrors accounts_routes.list_transactions)
        is_migrated = False
        anchor = None
        if acc.get("account_type") in ("bank", "cash"):
            anchor = await db.general_ledger.find_one(
                {"user_id": uid, "entity_type": "bank",
                 "entity_id": account_id,
                 "entry_type": "opening_balance",
                 "status": "posted"},
                {"_id": 0, "id": 1, "posted_at": 1,
                 "txn_group_id": 1},
            )
            is_migrated = bool(anchor)

        branch = "ledger_based" if is_migrated else "legacy_account_transactions"
        source_col = (
            "general_ledger" if is_migrated else "account_transactions"
        )
        decision_reason = (
            "Account has a posted `opening_balance` row in `general_ledger` "
            "(entity_type=bank). Per accounts_routes._ledger_based_tx_feed, "
            "the UI reads the statement EXCLUSIVELY from `general_ledger` "
            "and IGNORES `account_transactions`."
            if is_migrated
            else "No ledger opening anchor — UI reads from "
            "`account_transactions` directly."
        )

        # ── 3. account_transactions snapshot (raw) ────────────────
        atx_filter = {"user_id": uid, "account_id": account_id}
        atx_total = await db.account_transactions.count_documents(atx_filter)
        atx_by_type: Dict[str, int] = {}
        async for t in db.account_transactions.aggregate([
            {"$match": atx_filter},
            {"$group": {"_id": "$transaction_type", "n": {"$sum": 1}}},
        ]):
            atx_by_type[t["_id"] or "<null>"] = t["n"]

        atx_bnpl_rows: List[Dict[str, Any]] = []
        async for r in db.account_transactions.find(
            {**atx_filter, "transaction_type": "bnpl_settlement"},
            {"_id": 0},
        ).sort("transaction_date", -1):
            atx_bnpl_rows.append(r)

        atx_targets: Dict[str, Any] = {}
        for ref in TARGET_REFS:
            row = await db.account_transactions.find_one(
                {"user_id": uid,
                 "$or": [{"reference": ref},
                         {"settlement_reference": ref},
                         {"description": {"$regex": ref}}]},
                {"_id": 0},
            )
            atx_targets[ref] = row or {"_present": False}

        # ── 4. general_ledger snapshot ────────────────────────────
        gl_filter = {
            "user_id": uid,
            "entity_type": "bank",
            "entity_id": account_id,
            "sub_account": "main",
            "status": "posted",
        }
        gl_total = await db.general_ledger.count_documents(gl_filter)
        gl_by_type: Dict[str, int] = {}
        async for t in db.general_ledger.aggregate([
            {"$match": gl_filter},
            {"$group": {"_id": "$entry_type", "n": {"$sum": 1}}},
        ]):
            gl_by_type[t["_id"] or "<null>"] = t["n"]

        gl_bnpl_rows: List[Dict[str, Any]] = []
        async for r in db.general_ledger.find(
            {**gl_filter, "entry_type": "bnpl_settlement"},
            {"_id": 0, "id": 1, "entry_type": 1, "side": 1,
             "amount": 1, "entity_id": 1, "sub_account": 1,
             "metadata": 1, "posted_at": 1, "created_at": 1,
             "txn_group_id": 1, "status": 1, "notes": 1},
        ).sort("posted_at", -1):
            gl_bnpl_rows.append(r)

        gl_targets: Dict[str, Any] = {}
        for ref in TARGET_REFS:
            # Look anywhere in the ledger for this reference (even
            # outside the bank entity, in case the bank leg was
            # never written).
            anywhere = await db.general_ledger.find_one(
                {"user_id": uid,
                 "$or": [
                     {"metadata.settlement_reference": ref},
                     {"metadata.reference": ref},
                     {"notes": {"$regex": ref}},
                 ],
                 "entry_type": "bnpl_settlement"},
                {"_id": 0},
            )
            if not anywhere:
                gl_targets[ref] = {"_present_in_ledger": False}
                continue
            # Does the SAME txn_group_id include a bank-leg row for
            # THIS account_id?
            grp = anywhere.get("txn_group_id")
            bank_leg_for_this_account = None
            if grp:
                bank_leg_for_this_account = await db.general_ledger.find_one(
                    {"user_id": uid,
                     "txn_group_id": grp,
                     "entity_type": "bank",
                     "entity_id": account_id,
                     "sub_account": "main",
                     "status": "posted"},
                    {"_id": 0, "id": 1, "entity_id": 1, "side": 1,
                     "amount": 1, "sub_account": 1, "status": 1,
                     "posted_at": 1},
                )
            # Also count how many bank legs exist anywhere in this
            # group (might be pointing to a DIFFERENT account_id).
            other_bank_legs: List[Dict[str, Any]] = []
            if grp:
                async for r in db.general_ledger.find(
                    {"user_id": uid,
                     "txn_group_id": grp,
                     "entity_type": "bank"},
                    {"_id": 0, "id": 1, "entity_id": 1, "side": 1,
                     "amount": 1, "sub_account": 1, "status": 1},
                ):
                    other_bank_legs.append(r)
            gl_targets[ref] = {
                "_present_in_ledger": True,
                "txn_group_id": grp,
                "anchor_row": anywhere,
                "bank_leg_for_this_account": bank_leg_for_this_account,
                "all_bank_legs_in_group": other_bank_legs,
            }

        # ── 5. Simulate the EXACT UI query (_ledger_based_tx_feed)
        ui_rows_all: List[Dict[str, Any]] = []
        if is_migrated:
            async for r in db.general_ledger.find(
                gl_filter,
                {"_id": 0, "id": 1, "entry_type": 1, "side": 1,
                 "amount": 1, "notes": 1, "posted_at": 1,
                 "created_at": 1, "txn_group_id": 1, "metadata": 1},
            ).sort([("posted_at", 1), ("created_at", 1), ("id", 1)]):
                ui_rows_all.append(r)
        else:
            async for r in db.account_transactions.find(
                atx_filter, {"_id": 0},
            ).sort([("transaction_date", -1), ("created_at", -1)]):
                ui_rows_all.append(r)

        ui_total_rows = len(ui_rows_all)
        ui_by_type: Dict[str, int] = {}
        type_key = "entry_type" if is_migrated else "transaction_type"
        for r in ui_rows_all:
            k = r.get(type_key) or "<null>"
            ui_by_type[k] = ui_by_type.get(k, 0) + 1
        bnpl_in_ui = ui_by_type.get("bnpl_settlement", 0)

        # Last-50 visibility (UI shows newest-first, but ledger feed
        # iterates oldest-first then JS receives them sorted by
        # posted_at; the UI renders all of them — no client-side
        # truncation. We still simulate "last 50 newest" so we can
        # answer the user's specific question).
        if is_migrated:
            # Re-sort newest-first because UI displays newest first.
            ui_rows_sorted = sorted(
                ui_rows_all,
                key=lambda r: (r.get("posted_at") or r.get("created_at") or ""),
                reverse=True,
            )
        else:
            ui_rows_sorted = ui_rows_all
        last_50 = ui_rows_sorted[:50]
        last_50_refs = []
        for r in last_50:
            md = r.get("metadata") or {}
            last_50_refs.append(
                md.get("settlement_reference")
                or r.get("reference")
                or (r.get("notes") or "")
            )
        in_last_50 = {}
        for ref in TARGET_REFS:
            in_last_50[ref] = any(ref in (s or "") for s in last_50_refs)

        # ── 6. Root cause inference ───────────────────────────────
        evidence: List[str] = []
        if is_migrated:
            evidence.append(
                "UI took the LEDGER-BASED branch because "
                f"general_ledger has an opening_balance row for "
                f"entity_id={account_id} (entity_type=bank)."
            )
        else:
            evidence.append(
                "UI took the LEGACY account_transactions branch."
            )

        evidence.append(
            f"account_transactions has {atx_total} rows for this account, "
            f"of which {atx_by_type.get('bnpl_settlement', 0)} are "
            "bnpl_settlement (Iter-248 backfill artefacts)."
        )
        evidence.append(
            f"general_ledger (bank/main/posted) has {gl_total} rows, of "
            f"which {gl_by_type.get('bnpl_settlement', 0)} are "
            "bnpl_settlement bank legs for THIS account."
        )

        cause = {
            "category": "unknown",
            "summary": "",
            "evidence": evidence,
            "proposed_fix": "",
        }

        ledger_bnpl_count_for_this_acc = gl_by_type.get("bnpl_settlement", 0)
        atx_bnpl_count = atx_by_type.get("bnpl_settlement", 0)

        if is_migrated and ledger_bnpl_count_for_this_acc == 0 \
                and atx_bnpl_count > 0:
            # Smoking gun: data exists in account_transactions but UI
            # reads from ledger.
            cause["category"] = "different_data_source"
            cause["summary"] = (
                "السبب الجذري: واجهة كشف الحساب البنكي تقرأ من "
                "general_ledger (لأن الحساب مُرحَّل/migrated)، "
                "بينما عملية Iter-248 backfill كتبت السجلات في "
                "account_transactions فقط. لا يوجد ربط بين "
                "الجدولين، فلا تظهر التسويات في الواجهة."
            )
            cause["proposed_fix"] = (
                "إضافة كتابة موازية إلى general_ledger عند تنفيذ "
                "تسوية BNPL (debit leg على entity_type=bank, "
                "entity_id=<bank_id>, sub_account=main) — أو "
                "backfill خاص يُنشئ نفس الأسطر داخل general_ledger "
                "للتسويات الموجودة في account_transactions. "
                "البديل القراءي (دمج الجدولين داخل "
                "_ledger_based_tx_feed) يكسر الـ SSOT للرصيد، "
                "لذا الأنسب هو ضمان أن كل bnpl_settlement له bank "
                "leg مسجَّل في general_ledger."
            )
        elif is_migrated and ledger_bnpl_count_for_this_acc > 0 \
                and bnpl_in_ui == 0:
            cause["category"] = "backend_query"
            cause["summary"] = (
                "السجلات موجودة في general_ledger لكن استعلام "
                "UI الفعلي لا يُرجعها — راجع شروط الفلتر "
                "(sub_account / status / entity_id)."
            )
            cause["proposed_fix"] = (
                "افحص _ledger_based_tx_feed: تأكد أن sub_account "
                "للـ bank leg = 'main' و status='posted'."
            )
        elif is_migrated and ledger_bnpl_count_for_this_acc > 0 \
                and bnpl_in_ui > 0 \
                and not all(in_last_50.values()):
            cause["category"] = "pagination"
            cause["summary"] = (
                "السجلات تُرجع من الباك إند لكنها خارج آخر 50 "
                "حركة معروضة في UI."
            )
            cause["proposed_fix"] = (
                "إضافة فلتر/تصفّح زمني في UI أو رفع الـ limit."
            )
        elif not is_migrated and atx_bnpl_count > 0:
            cause["category"] = "frontend_mapping"
            cause["summary"] = (
                "الحساب غير مُرحَّل، السجلات موجودة في "
                "account_transactions ويجب أن تظهر — تحقق من الـ "
                "frontend mapping للنوع bnpl_settlement."
            )
            cause["proposed_fix"] = (
                "أضف bnpl_settlement إلى TRANSACTION_TYPE_LABELS "
                "وتأكد من عرضه."
            )
        elif atx_bnpl_count == 0 and ledger_bnpl_count_for_this_acc == 0:
            cause["category"] = "data_missing"
            cause["summary"] = (
                "لا توجد سجلات bnpl_settlement لهذا الحساب في "
                "أي من الجدولين."
            )
            cause["proposed_fix"] = (
                "تأكد من إجراء Iter-248 backfill على هذا "
                "الحساب وأن account_id يطابق فعلاً."
            )

        return {
            "ok": True,
            "iter": "iter249",
            "read_only": True,
            "account_id": account_id,
            "account": {
                "id": acc.get("id"),
                "name": acc.get("name"),
                "account_type": acc.get("account_type"),
                "currency": acc.get("currency"),
                "current_balance": acc.get("current_balance"),
                "auto_created": acc.get("auto_created"),
                "status": acc.get("status"),
            },
            "ui_path_decision": {
                "is_migrated": is_migrated,
                "branch_taken": branch,
                "branch_source_collection": source_col,
                "reason": decision_reason,
                "ledger_opening_anchor": anchor,
            },
            "ui_query_simulation": {
                "filter": (
                    gl_filter if is_migrated else atx_filter
                ),
                "total_rows": ui_total_rows,
                "by_type": ui_by_type,
                "bnpl_settlement_rows_returned": bnpl_in_ui,
            },
            "account_transactions_collection": {
                "all_rows_for_account_count": atx_total,
                "by_transaction_type": atx_by_type,
                "bnpl_settlement_rows": atx_bnpl_rows,
                "two_target_references": atx_targets,
            },
            "general_ledger_collection": {
                "bank_entity_rows_for_account_count": gl_total,
                "by_entry_type": gl_by_type,
                "bnpl_settlement_rows_for_this_account": gl_bnpl_rows,
                "two_target_references_in_ledger": gl_targets,
            },
            "appears_in_last_50_ui_rows": in_last_50,
            "root_cause": cause,
        }

    return router
