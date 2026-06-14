"""Iter-199 — Salary Payment Full Reversal.

Distinct from Iter-196 (employee misposting correction). This
operation FULLY REVERSES a salary_payment transaction across ALL
its legs — including the bank/cash side — by mirroring every row
of the original txn_group with the opposite `side`.

When to use Iter-196 vs Iter-199
================================
Iter-196 (correct-misposting)
    Money LEFT the bank for real. The transaction was simply
    booked against the WRONG employee. Move the employee-side
    impact only. Bank is NEVER touched.

Iter-199 (reverse-salary-payment)
    The salary payment never actually happened OR is being
    cancelled at the accounting layer. Bank balance must be
    restored. Employee's salary_payable must be reinstated.
    Every leg of the original is mirrored.

Guarantees (per merchant requirements)
======================================
• Original entry is NOT modified or deleted.
• A new ledger group is created with `entry_type = reversal`.
• Each new row carries `reversal_of_txn_group_id` pointing to
  the original.
• Bank/cash impact is the EXACT inverse of the original (so the
  bank-detail page shows the reversal as an inflow, restoring
  balance to the pre-payment state).
• Same operation cannot be reversed twice (we check whether any
  `reversal` row already points at the txn_group).
• Reason (free text, ≥ 5 chars) is MANDATORY.
• Full audit trail: original ledger row ids, original employee,
  original bank/cash account, reason, actor, timestamp.

Scope
=====
MVP supports only `entry_type = salary_payment`. Other reversals
(advance_grant, custody_grant) will be added after stability.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


class ReverseSalaryPaymentIn(BaseModel):
    original_txn_group_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=5)


def make_reversals_router(db, current_user):
    router = APIRouter(prefix="/accounting", tags=["reversals"])

    @router.get(
        "/employees/{emp_id}/reversible-salary-payments",
    )
    async def list_reversible(
        emp_id: str,
        user: dict = Depends(current_user),
    ):
        """Lists every salary_payment posted against `emp_id` that
        has NOT been fully reversed yet. Each row carries
        `already_reversed` flag and the bank/cash account name."""
        uid = user["id"]
        out: list = []
        async for row in db.general_ledger.find(
            {"user_id": uid,
             "entity_type": "employee",
             "entity_id": emp_id,
             "entry_type": "salary_payment",
             "side": "debit",
             "sub_account": "salary_payable",
             "status": "posted"},
            {"_id": 0, "id": 1, "amount": 1, "txn_group_id": 1,
             "posted_at": 1, "notes": 1, "metadata": 1},
        ).sort("posted_at", -1).limit(200):
            txn_id = row.get("txn_group_id") or ""
            already = await db.general_ledger.find_one(
                {"user_id": uid, "entry_type": "reversal",
                 "reversal_of_txn_group_id": txn_id,
                 "status": "posted"},
                {"_id": 1},
            )
            # Pull bank/cash leg for context.
            bank_leg = await db.general_ledger.find_one(
                {"user_id": uid, "txn_group_id": txn_id,
                 "entity_type": "bank", "side": "credit",
                 "status": "posted"},
                {"_id": 0, "entity_id": 1, "amount": 1},
            )
            bank_name = None
            if bank_leg:
                bank_acc = await db.accounts.find_one(
                    {"id": bank_leg["entity_id"], "user_id": uid},
                    {"_id": 0, "name": 1, "account_type": 1},
                )
                if bank_acc:
                    bank_name = bank_acc.get("name")
            out.append({
                "ledger_id": row["id"],
                "txn_group_id": txn_id,
                "amount": round(float(row.get("amount") or 0), 2),
                "bank_account_name": bank_name,
                "posted_at": row.get("posted_at"),
                "notes": row.get("notes"),
                "already_reversed": bool(already),
            })
        return {"operations": out}

    @router.post("/employees/reverse-salary-payment")
    async def reverse_salary_payment(
        payload: ReverseSalaryPaymentIn,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1) Load the original txn group ─────────────────────
        original_rows = await db.general_ledger.find(
            {"user_id": uid,
             "txn_group_id": payload.original_txn_group_id,
             "status": "posted"},
            {"_id": 0},
        ).to_list(50)
        if not original_rows:
            raise HTTPException(
                404, "العملية الأصلية غير موجودة أو غير مرحَّلة.",
            )

        # ── 2) Validate the group is a salary_payment ──────────
        types = {r.get("entry_type") for r in original_rows}
        if types != {"salary_payment"}:
            raise HTTPException(
                400,
                "هذه العملية ليست صرف راتب — لا تدعمها هذه الميزة "
                "حالياً (MVP يدعم salary_payment فقط).",
            )

        # ── 3) Block double reversal ───────────────────────────
        already = await db.general_ledger.find_one(
            {"user_id": uid,
             "entry_type": "reversal",
             "reversal_of_txn_group_id": payload.original_txn_group_id,
             "status": "posted"},
            {"_id": 1},
        )
        if already:
            raise HTTPException(
                400,
                "هذه العملية مَعكوسة مسبقاً ولا يمكن عكسها مرتين.",
            )

        # ── 4) Block reversing a correction or a reversal ──────
        if any(r.get("reversal_of_txn_group_id") for r in original_rows):
            raise HTTPException(
                400, "لا يمكن عكس قيد عَكسي.",
            )
        if any(r.get("corrects_txn_group_id") for r in original_rows):
            raise HTTPException(
                400, "لا يمكن عكس قيد تصحيح.",
            )

        # ── 5) Build the mirrored legs ─────────────────────────
        # Pull employee context for human-friendly metadata.
        emp_leg = next(
            (r for r in original_rows
             if r.get("entity_type") == "employee"
             and r.get("side") == "debit"),
            None,
        )
        bank_leg = next(
            (r for r in original_rows
             if r.get("entity_type") == "bank"
             and r.get("side") == "credit"),
            None,
        )
        employee_id = emp_leg.get("entity_id") if emp_leg else None
        bank_id = bank_leg.get("entity_id") if bank_leg else None

        emp_doc = None
        if employee_id:
            emp_doc = await db.operating_salaries.find_one(
                {"id": employee_id, "user_id": uid},
                {"_id": 0, "name": 1},
            ) or await db.employees.find_one(
                {"id": employee_id, "user_id": uid},
                {"_id": 0, "name": 1},
            )
        bank_doc = None
        if bank_id:
            bank_doc = await db.accounts.find_one(
                {"id": bank_id, "user_id": uid},
                {"_id": 0, "name": 1, "account_type": 1},
            )

        actor_name = user.get("name") or user.get("email") or "user"
        now_iso = datetime.now(timezone.utc).isoformat()

        meta_common = {
            "reversal_type": "full",
            "original_operation": "salary_payment",
            "original_txn_group_id": payload.original_txn_group_id,
            "original_amount": round(
                float(emp_leg.get("amount") or 0), 2,
            ) if emp_leg else None,
            "employee_id": employee_id,
            "employee_name": (
                emp_doc.get("name") if emp_doc else None
            ),
            "bank_account_id": bank_id,
            "bank_account_name": (
                bank_doc.get("name") if bank_doc else None
            ),
            "reason": payload.reason.strip(),
            "reversed_by": uid,
            "reversed_at": now_iso,
        }

        mirrored_entries = []
        for src in original_rows:
            flipped_side = (
                "credit" if src.get("side") == "debit" else "debit"
            )
            amt = round(float(src.get("amount") or 0), 2)
            entity_type = src.get("entity_type")
            label = (
                f"عكس صرف راتب {emp_doc.get('name')}"
                if emp_doc else "عكس صرف راتب"
            )
            mirrored_entries.append({
                "entity_type": entity_type,
                "entity_id":   src.get("entity_id"),
                "side":        flipped_side,
                "amount":      amt,
                "entry_type":  "reversal",
                "sub_account": src.get("sub_account") or "main",
                # `reversal` is a strict entry_type — ledger_core
                # enforces a `reason_code` from REASON_CODES. We pass
                # `data_entry_error` because this feature is meant
                # for cancelling a salary payment that was booked by
                # mistake or never actually executed.
                "reason_code": "data_entry_error",
                "notes":       f"{label} — {payload.reason.strip()}",
                "metadata":    {**meta_common,
                                "leg":
                                "bank_restore"
                                if entity_type == "bank"
                                else
                                "employee_restore"
                                if entity_type == "employee"
                                else "contra"},
            })

        # ── 6) Post the reversal group ─────────────────────────
        from ledger_core import post_txn_group
        result = await post_txn_group(
            db,
            user_id=uid, actor_id=uid, actor_name=actor_name,
            entries=mirrored_entries,
            txn_type="salary_payment_reversal",
            notes=f"عكس صرف راتب — {payload.reason.strip()}",
            metadata={"reversal_of_txn_group_id":
                      payload.original_txn_group_id},
        )

        # ── 7) Bind cross-link explicitly per row ──────────────
        reversal_group_id = result["txn_group_id"]
        await db.general_ledger.update_many(
            {"user_id": uid, "txn_group_id": reversal_group_id},
            {"$set": {
                "reversal_of_txn_group_id":
                    payload.original_txn_group_id,
            }},
        )

        return {
            "reversal_group_id": reversal_group_id,
            "reversal_of_txn_group_id":
                payload.original_txn_group_id,
            "amount": round(
                float(emp_leg.get("amount") or 0), 2,
            ) if emp_leg else None,
            "employee": {
                "id": employee_id,
                "name": emp_doc.get("name") if emp_doc else None,
            },
            "bank_account": {
                "id": bank_id,
                "name": bank_doc.get("name") if bank_doc else None,
            },
            "reason": payload.reason.strip(),
            "bank_impact_direction": "restored_inflow",
            "entries": [
                {
                    "ledger_id": e.get("id"),
                    "entity_type": e.get("entity_type"),
                    "entity_id": e.get("entity_id"),
                    "side": e.get("side"),
                    "amount": e.get("amount"),
                    "sub_account": e.get("sub_account"),
                }
                for e in result["entries"]
            ],
            "summary": (
                f"تم عكس صرف الراتب بالكامل — "
                f"المبلغ {meta_common.get('original_amount'):.2f} ر.س "
                f"عاد إلى "
                f"{bank_doc.get('name') if bank_doc else 'البنك'}."
            ),
        }

    @router.get("/employees/salary-reversals")
    async def list_reversals(
        user: dict = Depends(current_user),
    ):
        """Audit log of all salary-payment reversals."""
        uid = user["id"]
        seen: dict = {}
        async for row in db.general_ledger.find(
            {"user_id": uid,
             "entry_type": "reversal",
             "status": "posted",
             "reversal_of_txn_group_id": {"$ne": None}},
            {"_id": 0, "id": 1, "txn_group_id": 1,
             "reversal_of_txn_group_id": 1, "entity_type": 1,
             "entity_id": 1, "side": 1, "amount": 1, "metadata": 1,
             "posted_at": 1, "notes": 1},
        ).sort("posted_at", -1).limit(500):
            gid = row.get("txn_group_id")
            if not gid:
                continue
            md = row.get("metadata") or {}
            # Skip if metadata indicates it's not a salary reversal.
            if md.get("original_operation") != "salary_payment":
                continue
            grp = seen.setdefault(gid, {
                "reversal_group_id": gid,
                "reversal_of_txn_group_id":
                    row.get("reversal_of_txn_group_id"),
                "posted_at": row.get("posted_at"),
                "amount": md.get("original_amount"),
                "reason": md.get("reason"),
                "reversed_by": md.get("reversed_by"),
                "employee": {
                    "id": md.get("employee_id"),
                    "name": md.get("employee_name"),
                },
                "bank_account": {
                    "id": md.get("bank_account_id"),
                    "name": md.get("bank_account_name"),
                },
            })
            _ = grp  # ensure variable is used (lint-friendly)
        return {"reversals": list(seen.values())}

    # ─────────────────────────────────────────────────────────
    # Iter-201 — Expense Reversal (expense_record)
    #
    # Same mirror-every-leg pattern as Iter-199 but applies to
    # entry_type='expense_record'. The reversal flips ALL legs
    # (whether the expense was paid from a bank, cash account,
    # employee custody, or payment_platform), so the source
    # account always gets the money back EXACTLY where it left.
    # ─────────────────────────────────────────────────────────
    @router.get("/expenses/reversible")
    async def list_reversible_expenses(
        user: dict = Depends(current_user),
    ):
        """Lists every expense_record group that hasn't been
        reversed yet, with the source account (the credit leg)
        for context."""
        uid = user["id"]
        # Use group-by aggregation to pull each txn_group once.
        # The merchant typically wants the newest 200.
        pipeline = [
            {"$match": {"user_id": uid,
                         "entry_type": "expense_record",
                         "status": "posted"}},
            {"$sort": {"posted_at": -1}},
            {"$group": {
                "_id": "$txn_group_id",
                "posted_at": {"$first": "$posted_at"},
                "amount": {"$max": "$amount"},
                "notes": {"$first": "$notes"},
                "metadata": {"$first": "$metadata"},
            }},
            {"$sort": {"posted_at": -1}},
            {"$limit": 200},
        ]
        groups = []
        async for g in db.general_ledger.aggregate(pipeline):
            groups.append(g)

        out: list = []
        for g in groups:
            gid = g["_id"]
            if not gid:
                continue
            already = await db.general_ledger.find_one(
                {"user_id": uid, "entry_type": "reversal",
                 "reversal_of_txn_group_id": gid,
                 "status": "posted"},
                {"_id": 1},
            )
            # Source-of-funds leg (whatever has the CREDIT side).
            src_leg = await db.general_ledger.find_one(
                {"user_id": uid, "txn_group_id": gid,
                 "side": "credit",
                 "entity_type": {"$in": [
                     "bank", "cash", "payment_platform",
                     "employee",
                 ]},
                 "status": "posted"},
                {"_id": 0, "entity_type": 1, "entity_id": 1,
                 "sub_account": 1, "amount": 1},
            )
            source_name = None
            source_type = src_leg.get("entity_type") if src_leg else None
            if src_leg:
                if source_type in ("bank", "cash",
                                    "payment_platform"):
                    acc = await db.accounts.find_one(
                        {"id": src_leg["entity_id"],
                         "user_id": uid},
                        {"_id": 0, "name": 1},
                    )
                    source_name = acc.get("name") if acc else None
                elif source_type == "employee":
                    emp = await db.operating_salaries.find_one(
                        {"id": src_leg["entity_id"],
                         "user_id": uid},
                        {"_id": 0, "name": 1},
                    ) or await db.employees.find_one(
                        {"id": src_leg["entity_id"],
                         "user_id": uid},
                        {"_id": 0, "name": 1},
                    )
                    source_name = emp.get("name") if emp else None
            out.append({
                "txn_group_id": gid,
                "amount": round(float(g.get("amount") or 0), 2),
                "source_type": source_type,
                "source_name": source_name,
                "posted_at": g.get("posted_at"),
                "notes": g.get("notes"),
                "already_reversed": bool(already),
            })
        return {"operations": out}

    @router.post("/expenses/reverse")
    async def reverse_expense(
        payload: ReverseSalaryPaymentIn,
        user: dict = Depends(current_user),
    ):
        """Reverse a posted expense_record group by mirroring
        every leg with the opposite side. The original is never
        modified or deleted."""
        uid = user["id"]
        original_rows = await db.general_ledger.find(
            {"user_id": uid,
             "txn_group_id": payload.original_txn_group_id,
             "status": "posted"},
            {"_id": 0},
        ).to_list(50)
        if not original_rows:
            raise HTTPException(
                404, "العملية الأصلية غير موجودة أو غير مرحَّلة.",
            )
        types = {r.get("entry_type") for r in original_rows}
        if types != {"expense_record"}:
            raise HTTPException(
                400, "هذه العملية ليست مصروفاً.",
            )
        # Block double-reverse
        already = await db.general_ledger.find_one(
            {"user_id": uid, "entry_type": "reversal",
             "reversal_of_txn_group_id":
                 payload.original_txn_group_id,
             "status": "posted"},
            {"_id": 1},
        )
        if already:
            raise HTTPException(
                400, "هذا المصروف مَعكوس مسبقاً.",
            )
        if any(r.get("reversal_of_txn_group_id")
               for r in original_rows):
            raise HTTPException(
                400, "لا يمكن عكس قيد عَكسي.")
        if any(r.get("corrects_txn_group_id")
               for r in original_rows):
            raise HTTPException(
                400, "لا يمكن عكس قيد تصحيح.")

        # Pull source/dest context for metadata
        src_leg = next(
            (r for r in original_rows
             if r.get("side") == "credit"
             and r.get("entity_type") in (
                 "bank", "cash", "payment_platform", "employee")),
            None,
        )
        src_id = src_leg.get("entity_id") if src_leg else None
        src_type = src_leg.get("entity_type") if src_leg else None
        src_name = None
        if src_leg and src_type in (
                "bank", "cash", "payment_platform"):
            sa = await db.accounts.find_one(
                {"id": src_id, "user_id": uid},
                {"_id": 0, "name": 1})
            src_name = sa.get("name") if sa else None
        elif src_leg and src_type == "employee":
            se = await db.operating_salaries.find_one(
                {"id": src_id, "user_id": uid},
                {"_id": 0, "name": 1}) or {}
            src_name = se.get("name")

        actor_name = (
            user.get("name") or user.get("email") or "user"
        )
        now_iso = datetime.now(timezone.utc).isoformat()
        total_amount = round(
            float(src_leg.get("amount") or 0), 2,
        ) if src_leg else 0.0

        meta_common = {
            "reversal_type": "full",
            "original_operation": "expense_record",
            "original_txn_group_id":
                payload.original_txn_group_id,
            "original_amount": total_amount,
            "source_account_id": src_id,
            "source_account_type": src_type,
            "source_account_name": src_name,
            "reason": payload.reason.strip(),
            "reversed_by": uid,
            "reversed_at": now_iso,
        }

        mirrored_entries = []
        for src in original_rows:
            flipped_side = (
                "credit" if src.get("side") == "debit"
                else "debit"
            )
            mirrored_entries.append({
                "entity_type": src.get("entity_type"),
                "entity_id":   src.get("entity_id"),
                "side":        flipped_side,
                "amount":      round(
                    float(src.get("amount") or 0), 2),
                "entry_type":  "reversal",
                "sub_account": src.get("sub_account") or "main",
                "reason_code": "data_entry_error",
                "notes":
                    f"عكس مصروف — {payload.reason.strip()}",
                "metadata":    {**meta_common,
                                "leg":
                                "source_restore"
                                if (src.get("side") == "credit"
                                    and src.get("entity_type") in (
                                        "bank", "cash",
                                        "payment_platform",
                                        "employee"))
                                else "expense_restore"},
            })

        from ledger_core import post_txn_group
        result = await post_txn_group(
            db, user_id=uid, actor_id=uid, actor_name=actor_name,
            entries=mirrored_entries,
            txn_type="expense_reversal",
            notes=f"عكس مصروف — {payload.reason.strip()}",
            metadata={"reversal_of_txn_group_id":
                      payload.original_txn_group_id},
        )
        reversal_gid = result["txn_group_id"]
        await db.general_ledger.update_many(
            {"user_id": uid, "txn_group_id": reversal_gid},
            {"$set": {"reversal_of_txn_group_id":
                      payload.original_txn_group_id}},
        )

        return {
            "reversal_group_id": reversal_gid,
            "reversal_of_txn_group_id":
                payload.original_txn_group_id,
            "amount": total_amount,
            "source_account": {
                "id": src_id, "type": src_type, "name": src_name,
            },
            "reason": payload.reason.strip(),
            "bank_impact_direction": "restored_inflow",
            "summary": (
                f"تم عكس مصروف {total_amount:.2f} ر.س — "
                f"عاد المبلغ إلى {src_name or 'مصدر المصروف'}."
            ),
        }

    @router.get("/expenses/reversals")
    async def list_expense_reversals(
        user: dict = Depends(current_user),
    ):
        """Audit log of expense reversals only."""
        uid = user["id"]
        seen: dict = {}
        async for row in db.general_ledger.find(
            {"user_id": uid,
             "entry_type": "reversal", "status": "posted",
             "reversal_of_txn_group_id": {"$ne": None}},
            {"_id": 0, "txn_group_id": 1,
             "reversal_of_txn_group_id": 1,
             "posted_at": 1, "metadata": 1},
        ).sort("posted_at", -1).limit(500):
            gid = row.get("txn_group_id")
            md = row.get("metadata") or {}
            if not gid or \
                    md.get("original_operation") != "expense_record":
                continue
            seen.setdefault(gid, {
                "reversal_group_id": gid,
                "reversal_of_txn_group_id":
                    row.get("reversal_of_txn_group_id"),
                "posted_at": row.get("posted_at"),
                "amount": md.get("original_amount"),
                "reason": md.get("reason"),
                "reversed_by": md.get("reversed_by"),
                "source_account": {
                    "id": md.get("source_account_id"),
                    "type": md.get("source_account_type"),
                    "name": md.get("source_account_name"),
                },
            })
        return {"reversals": list(seen.values())}

    return router
