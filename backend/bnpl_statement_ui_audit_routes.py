"""Iter-249 — Bank Statement UI Audit (READ-ONLY).

Two invocation modes:

  1. By bank account id (original):
       GET /api/audit/account-statement-trace?account_id=<BANK_ID>

  2. By settlement reference (shortcut — auto-resolves the bank):
       GET /api/audit/account-statement-trace
            ?settlement_reference=TAMARA-2026-06-06-AUTO
       GET /api/audit/account-statement-trace
            ?settlement_reference=TABBY-2026-06-08-AUTO

If both params are provided, `settlement_reference` takes priority and
the response includes a `warning` field.

This endpoint mutates NOTHING — it only reads `accounts`,
`account_transactions`, and `general_ledger`.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query


TARGET_REFS = ["TAMARA-2026-06-06-AUTO", "TABBY-2026-06-08-AUTO"]


async def _resolve_bank_from_reference(
    db, uid: str, ref: str,
) -> Optional[Dict[str, Any]]:
    """Find the bank_account_id linked to a bnpl_settlement reference.

    Search order:
      1. general_ledger row with entry_type='bnpl_settlement' whose
         metadata.settlement_reference == ref AND entity_type='bank'
         (this is the bank leg — direct hit).
      2. Same as (1) but with notes containing ref (looser match).
      3. account_transactions row with reference == ref (Iter-248
         backfill artefact) — fall back to its account_id.
    Returns a dict with bank_account_id / provider / txn_group_id /
    transferred_amount / source, or None.
    """
    # 1) Bank-leg row in ledger (direct).
    row = await db.general_ledger.find_one(
        {"user_id": uid,
         "entry_type": "bnpl_settlement",
         "entity_type": "bank",
         "metadata.settlement_reference": ref,
         "status": "posted"},
        {"_id": 0, "entity_id": 1, "amount": 1,
         "txn_group_id": 1, "metadata": 1, "side": 1,
         "posted_at": 1},
    )
    if row:
        md = row.get("metadata") or {}
        return {
            "source": "general_ledger.bnpl_settlement.bank_leg",
            "bank_account_id": (
                md.get("bank_account_id") or row.get("entity_id")
            ),
            "bank_account_name": md.get("bank_account_name") or "",
            "provider": md.get("provider"),
            "txn_group_id": row.get("txn_group_id"),
            "transferred_amount": float(
                md.get("transferred_amount")
                or row.get("amount") or 0
            ),
            "settlement_date": md.get("settlement_date") or "",
        }

    # 2) Any bnpl_settlement row in the ledger that mentions the ref
    #    in notes — use it to get txn_group_id, then derive bank leg.
    any_row = await db.general_ledger.find_one(
        {"user_id": uid,
         "entry_type": "bnpl_settlement",
         "$or": [
             {"metadata.settlement_reference": ref},
             {"metadata.reference": ref},
             {"notes": {"$regex": ref}},
         ]},
        {"_id": 0, "txn_group_id": 1, "metadata": 1},
    )
    if any_row and any_row.get("txn_group_id"):
        md = any_row.get("metadata") or {}
        grp = any_row["txn_group_id"]
        bank_leg = await db.general_ledger.find_one(
            {"user_id": uid,
             "txn_group_id": grp,
             "entity_type": "bank",
             "status": "posted"},
            {"_id": 0, "entity_id": 1, "amount": 1,
             "metadata": 1, "side": 1},
        )
        if bank_leg:
            bmd = bank_leg.get("metadata") or {}
            return {
                "source": "general_ledger.via_txn_group_id",
                "bank_account_id": (
                    bmd.get("bank_account_id")
                    or bank_leg.get("entity_id")
                ),
                "bank_account_name": (
                    bmd.get("bank_account_name")
                    or md.get("bank_account_name") or ""
                ),
                "provider": md.get("provider"),
                "txn_group_id": grp,
                "transferred_amount": float(
                    md.get("transferred_amount")
                    or bank_leg.get("amount") or 0
                ),
                "settlement_date": md.get("settlement_date") or "",
            }

    # 3) Iter-248 backfill artefact in account_transactions.
    atx = await db.account_transactions.find_one(
        {"user_id": uid,
         "transaction_type": "bnpl_settlement",
         "$or": [
             {"reference": ref},
             {"settlement_reference": ref},
             {"description": {"$regex": ref}},
         ]},
        {"_id": 0},
    )
    if atx:
        return {
            "source": "account_transactions.iter248_backfill",
            "bank_account_id": atx.get("account_id"),
            "bank_account_name": atx.get("account_name") or "",
            "provider": atx.get("provider"),
            "txn_group_id": atx.get("txn_group_id"),
            "transferred_amount": float(atx.get("amount") or 0),
            "settlement_date": atx.get("transaction_date") or "",
        }

    return None


def make_bnpl_statement_ui_audit_router(db, current_user):
    router = APIRouter(tags=["audit", "bnpl"])

    @router.get("/audit/account-statement-trace")
    async def trace_account_statement(
        account_id: Optional[str] = Query(
            None, description="Bank account id"),
        settlement_reference: Optional[str] = Query(
            None,
            description="BNPL settlement reference (auto-resolves "
                        "the bank account)"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        if not account_id and not settlement_reference:
            raise HTTPException(
                400,
                "Provide either `account_id` or "
                "`settlement_reference`.",
            )

        # ── 0. Resolve bank_account_id from settlement_reference ──
        resolved_section: Optional[Dict[str, Any]] = None
        warning: Optional[str] = None
        if settlement_reference:
            resolved = await _resolve_bank_from_reference(
                db, uid, settlement_reference,
            )
            if not resolved or not resolved.get("bank_account_id"):
                return {
                    "ok": False,
                    "iter": "iter249",
                    "read_only": True,
                    "input": {
                        "account_id": account_id,
                        "settlement_reference": settlement_reference,
                    },
                    "resolved_from_settlement_reference": None,
                    "error": (
                        "لم يتم العثور على أي قيد "
                        "bnpl_settlement بهذا المرجع في "
                        "general_ledger ولا في "
                        "account_transactions."
                    ),
                }
            if account_id and account_id != resolved["bank_account_id"]:
                warning = (
                    f"تم تجاهل account_id={account_id} لأن "
                    "settlement_reference له الأولوية وأشار إلى "
                    f"bank_account_id={resolved['bank_account_id']}."
                )
            account_id = resolved["bank_account_id"]
            resolved_section = {
                "settlement_reference": settlement_reference,
                "provider": resolved.get("provider"),
                "txn_group_id": resolved.get("txn_group_id"),
                "bank_account_id": resolved.get("bank_account_id"),
                "bank_account_name": resolved.get("bank_account_name"),
                "transferred_amount": resolved.get("transferred_amount"),
                "settlement_date": resolved.get("settlement_date"),
                "resolution_source": resolved.get("source"),
            }

        # ── 1. Account summary ─────────────────────────────────────
        acc = await db.accounts.find_one(
            {"id": account_id, "user_id": uid}, {"_id": 0},
        )
        if not acc:
            raise HTTPException(
                404,
                f"Account not found for this user (id={account_id}).",
            )

        # ── 2. UI path decision (mirrors accounts_routes._ledger_..)
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

        branch = "ledger_based" if is_migrated \
            else "legacy_account_transactions"
        source_col = (
            "general_ledger" if is_migrated else "account_transactions"
        )
        decision_reason = (
            "Account has a posted `opening_balance` row in "
            "`general_ledger` (entity_type=bank). Per "
            "accounts_routes._ledger_based_tx_feed, the UI reads the "
            "statement EXCLUSIVELY from `general_ledger` and IGNORES "
            "`account_transactions`."
            if is_migrated
            else "No ledger opening anchor — UI reads from "
            "`account_transactions` directly."
        )

        # ── 3. account_transactions snapshot ──────────────────────
        atx_filter = {"user_id": uid, "account_id": account_id}
        atx_total = await db.account_transactions.count_documents(
            atx_filter)
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

        # ── 4. Target references probe ────────────────────────────
        # When the user passed settlement_reference, focus on that
        # exact ref; otherwise probe the two known target refs.
        probe_refs = (
            [settlement_reference] if settlement_reference
            else TARGET_REFS
        )

        atx_targets: Dict[str, Any] = {}
        for ref in probe_refs:
            row = await db.account_transactions.find_one(
                {"user_id": uid,
                 "$or": [{"reference": ref},
                         {"settlement_reference": ref},
                         {"description": {"$regex": ref}}]},
                {"_id": 0},
            )
            atx_targets[ref] = row or {"_present": False}

        # ── 5. general_ledger snapshot ────────────────────────────
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
        for ref in probe_refs:
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
            grp = anywhere.get("txn_group_id")
            bank_leg_for_this_account = None
            other_bank_legs: List[Dict[str, Any]] = []
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

        # ── 6. Simulate the EXACT UI query ────────────────────────
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

        # last-50 visibility (newest first)
        if is_migrated:
            ui_rows_sorted = sorted(
                ui_rows_all,
                key=lambda r: (
                    r.get("posted_at")
                    or r.get("created_at") or ""
                ),
                reverse=True,
            )
        else:
            ui_rows_sorted = ui_rows_all
        last_50 = ui_rows_sorted[:50]
        last_50_refs: List[str] = []
        for r in last_50:
            md = r.get("metadata") or {}
            last_50_refs.append(
                md.get("settlement_reference")
                or r.get("reference")
                or (r.get("notes") or "")
            )
        in_last_50 = {}
        for ref in probe_refs:
            in_last_50[ref] = any(
                ref in (s or "") for s in last_50_refs
            )

        # ── 7. Root cause inference ───────────────────────────────
        evidence: List[str] = []
        if is_migrated:
            evidence.append(
                "UI took the LEDGER-BASED branch because "
                "general_ledger has an opening_balance row for "
                f"entity_id={account_id} (entity_type=bank)."
            )
        else:
            evidence.append(
                "UI took the LEGACY account_transactions branch."
            )
        evidence.append(
            f"account_transactions has {atx_total} rows for this "
            f"account, of which "
            f"{atx_by_type.get('bnpl_settlement', 0)} are "
            "bnpl_settlement (Iter-248 backfill artefacts)."
        )
        evidence.append(
            f"general_ledger (bank/main/posted) has {gl_total} rows, "
            f"of which {gl_by_type.get('bnpl_settlement', 0)} are "
            "bnpl_settlement bank legs for THIS account."
        )

        cause: Dict[str, Any] = {
            "category": "unknown",
            "summary": "",
            "evidence": evidence,
            "proposed_fix": "",
        }
        gl_bnpl_count = gl_by_type.get("bnpl_settlement", 0)
        atx_bnpl_count = atx_by_type.get("bnpl_settlement", 0)

        if is_migrated and gl_bnpl_count == 0 and atx_bnpl_count > 0:
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
                "backfill خاص يُنشئ نفس الأسطر داخل "
                "general_ledger للتسويات الموجودة في "
                "account_transactions."
            )
        elif is_migrated and gl_bnpl_count > 0 and bnpl_in_ui == 0:
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
        elif is_migrated and gl_bnpl_count > 0 and bnpl_in_ui > 0 \
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
                "account_transactions ويجب أن تظهر — تحقق من "
                "frontend mapping للنوع bnpl_settlement."
            )
            cause["proposed_fix"] = (
                "أضف bnpl_settlement إلى TRANSACTION_TYPE_LABELS "
                "وتأكد من عرضه."
            )
        elif atx_bnpl_count == 0 and gl_bnpl_count == 0:
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
            "input": {
                "account_id": account_id,
                "settlement_reference": settlement_reference,
            },
            "warning": warning,
            "resolved_from_settlement_reference": resolved_section,
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
                "filter": gl_filter if is_migrated else atx_filter,
                "total_rows": ui_total_rows,
                "by_type": ui_by_type,
                "bnpl_settlement_rows_returned": bnpl_in_ui,
            },
            "account_transactions_collection": {
                "all_rows_for_account_count": atx_total,
                "by_transaction_type": atx_by_type,
                "bnpl_settlement_rows": atx_bnpl_rows,
                "probed_references": atx_targets,
            },
            "general_ledger_collection": {
                "bank_entity_rows_for_account_count": gl_total,
                "by_entry_type": gl_by_type,
                "bnpl_settlement_rows_for_this_account": gl_bnpl_rows,
                "probed_references_in_ledger": gl_targets,
            },
            "appears_in_last_50_ui_rows": in_last_50,
            "root_cause": cause,
        }

    return router
