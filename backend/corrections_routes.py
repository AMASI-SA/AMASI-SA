"""Iter-196 — Employee Misposting Correction (Read-Only on bank, write
on employee ledger only).

Business need
=============
A salary_payment / advance_grant / custody_grant was recorded against
the WRONG employee. The cash already left the bank — we must NOT
re-touch the bank, but we MUST move the accounting impact between the
two employees.

Mechanics (double-entry preserved)
==================================
Original event leg on employee = DEBIT on sub_account ∈
    {salary_payable, advance, custody}

Correction pair (same sub_account, same amount):
    CREDIT sub_account (wrong_employee)   ← reverses the wrong DEBIT
    DEBIT  sub_account (correct_employee) ← applies the right DEBIT

Σ debit = Σ credit  ✅  bank impact = 0  ✅

Audit trail
===========
Every correction row carries:
    entry_type            = "correction"
    correction_group_id   = uuid (links both rows of the pair)
    corrects_txn_group_id = original txn_group_id (immutable link)
    txn_group_id          = correction_group_id (group invariant)
    metadata: {
        correction_type:           "wrong_employee",
        original_operation:        salary_payment | advance_grant | custody_grant,
        original_employee_id/name: …,
        corrected_to_employee_id/name: …,
        reason:                    <free text from merchant>,
        corrected_by:              <user_id>,
        corrected_at:              <ISO>,
    }

Decisions locked (per merchant approval — Iter-196)
====================================================
1a  partial corrections allowed (amount ≤ remaining_uncorrected)
2b  opening_balance is NOT correctable here (separate path)
3b  a correction itself is NOT correctable (`correction` excluded)
4a  original entries STAY visible; nothing is mutated/hidden
5b  MVP supports salary_payment, advance_grant, custody_grant only

The original ledger rows are never modified or deleted.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


SUPPORTED_OPERATIONS = {
    # original entry_type → sub_account on employee leg
    "salary_payment": "salary_payable",
    "advance_grant":  "advance",
    "custody_grant":  "custody",
}


class CorrectMispostingIn(BaseModel):
    original_txn_group_id: str = Field(..., min_length=1)
    from_employee_id: str = Field(..., min_length=1)
    to_employee_id: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0)
    reason: str = Field(..., min_length=5)


async def _sum_already_corrected(
    db, *, user_id: str, original_txn_group_id: str,
    from_employee_id: str, sub_account: str,
) -> float:
    """Σ amount of prior correction rows that REVERSE off the wrong
    employee for THIS original. Used to enforce
        amount ≤ original_amount − already_corrected.
    """
    pipeline = [
        {"$match": {
            "user_id": user_id,
            "entry_type": "correction",
            "corrects_txn_group_id": original_txn_group_id,
            "entity_type": "employee",
            "entity_id": from_employee_id,
            "sub_account": sub_account,
            # The reversal leg is CREDIT — that's what consumed the
            # wrong employee's debit balance. Counting only credit
            # rows avoids double-counting the paired DEBIT on the
            # correct employee.
            "side": "credit",
            "status": "posted",
        }},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]
    async for r in db.general_ledger.aggregate(pipeline):
        return float(r.get("total") or 0)
    return 0.0


def make_corrections_router(db, current_user):
    router = APIRouter(prefix="/accounting", tags=["corrections"])

    @router.get("/employees/{emp_id}/correctable-operations")
    async def list_correctable_operations(
        emp_id: str,
        user: dict = Depends(current_user),
    ):
        """Returns all original operations posted against `emp_id` that
        can be corrected (salary_payment / advance_grant / custody_grant)
        with the per-operation remaining-uncorrected amount.
        """
        uid = user["id"]
        out: list[dict] = []
        async for row in db.general_ledger.find(
            {"user_id": uid,
             "entity_type": "employee",
             "entity_id": emp_id,
             "entry_type": {"$in": list(SUPPORTED_OPERATIONS.keys())},
             "side": "debit",
             "status": "posted"},
            {"_id": 0, "id": 1, "entry_type": 1, "sub_account": 1,
             "amount": 1, "txn_group_id": 1, "created_at": 1,
             "posted_at": 1, "metadata": 1, "notes": 1},
        ).sort("posted_at", -1).limit(200):
            sub = row.get("sub_account") or SUPPORTED_OPERATIONS[
                row["entry_type"]
            ]
            already = await _sum_already_corrected(
                db, user_id=uid,
                original_txn_group_id=row.get("txn_group_id") or "",
                from_employee_id=emp_id,
                sub_account=sub,
            )
            remaining = round(
                float(row.get("amount") or 0) - already, 2,
            )
            out.append({
                "ledger_id": row["id"],
                "txn_group_id": row.get("txn_group_id"),
                "entry_type": row["entry_type"],
                "operation_label": {
                    "salary_payment": "صرف راتب",
                    "advance_grant":  "منح سُلفة",
                    "custody_grant":  "تسليم عهدة",
                }[row["entry_type"]],
                "sub_account": sub,
                "amount": round(float(row.get("amount") or 0), 2),
                "already_corrected": round(already, 2),
                "remaining_correctable": remaining,
                "posted_at": row.get("posted_at"),
                "notes": row.get("notes"),
                "metadata": row.get("metadata") or {},
            })
        return {"operations": out}

    @router.post("/employees/correct-misposting")
    async def correct_misposting(
        payload: CorrectMispostingIn,
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        if payload.from_employee_id == payload.to_employee_id:
            raise HTTPException(
                400,
                "الموظف الخطأ والموظف الصحيح لا يمكن أن يكونا نفس الشخص.",
            )

        # ── 1) Load the original transaction group ───────────────
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

        # ── 2) Find the employee leg on the WRONG employee ───────
        emp_leg = None
        for r in original_rows:
            if (r.get("entity_type") == "employee"
                    and r.get("entity_id") == payload.from_employee_id
                    and r.get("side") == "debit"
                    and r.get("entry_type") in SUPPORTED_OPERATIONS):
                emp_leg = r
                break
        if not emp_leg:
            raise HTTPException(
                400,
                "العملية الأصلية لا تحتوي على قيد debit للموظف "
                "المختار من نوع قابل للتصحيح "
                "(salary_payment / advance_grant / custody_grant).",
            )

        # ── 3) Block disallowed entry types ──────────────────────
        original_entry_type = emp_leg["entry_type"]
        if original_entry_type == "correction":
            raise HTTPException(
                400,
                "لا يمكن تصحيح قيد تصحيح. (3b — قرار Iter-196).",
            )
        if original_entry_type == "opening_balance":
            raise HTTPException(
                400,
                "لا يمكن تصحيح الرصيد الافتتاحي من هنا. "
                "(2b — للأرصدة الافتتاحية مسار خاص).",
            )

        sub_account = emp_leg.get("sub_account") or \
            SUPPORTED_OPERATIONS[original_entry_type]
        original_amount = float(emp_leg.get("amount") or 0)
        if original_amount <= 0:
            raise HTTPException(400, "المبلغ الأصلي صفر — لا شيء يُصحَّح.")

        # ── 4) Enforce partial-correction quota ──────────────────
        already_corrected = await _sum_already_corrected(
            db, user_id=uid,
            original_txn_group_id=payload.original_txn_group_id,
            from_employee_id=payload.from_employee_id,
            sub_account=sub_account,
        )
        remaining = round(original_amount - already_corrected, 2)
        amount = round(float(payload.amount), 2)
        if amount > remaining + 0.001:
            raise HTTPException(
                400,
                f"المبلغ المطلوب ({amount}) أكبر من المتبقي القابل "
                f"للتصحيح ({remaining}). الأصلي={original_amount}, "
                f"مُصحَّح مسبقاً={already_corrected}.",
            )

        # ── 5) Validate target employee exists ───────────────────
        target_emp = await db.operating_salaries.find_one(
            {"id": payload.to_employee_id, "user_id": uid},
            {"_id": 0, "id": 1, "name": 1, "status": 1},
        ) or await db.employees.find_one(
            {"id": payload.to_employee_id, "user_id": uid},
            {"_id": 0, "id": 1, "name": 1, "status": 1},
        )
        if not target_emp:
            raise HTTPException(404, "الموظف الصحيح غير موجود.")
        if target_emp.get("status") == "deleted":
            raise HTTPException(
                400, "الموظف الصحيح غير نشط — أعد تفعيله أولاً.",
            )

        from_emp = await db.operating_salaries.find_one(
            {"id": payload.from_employee_id, "user_id": uid},
            {"_id": 0, "name": 1},
        ) or await db.employees.find_one(
            {"id": payload.from_employee_id, "user_id": uid},
            {"_id": 0, "name": 1},
        ) or {}

        # ── 6) Build the correction pair (employee ledger only) ──
        # The bank/cash legs of the original stay untouched on purpose.
        from ledger_core import post_txn_group
        actor_name = user.get("name") or user.get("email") or "user"
        now_iso = datetime.now(timezone.utc).isoformat()
        meta_common = {
            "correction_type": "wrong_employee",
            "original_operation": original_entry_type,
            "original_txn_group_id": payload.original_txn_group_id,
            "original_ledger_id": emp_leg.get("id"),
            "original_amount": original_amount,
            "amount_corrected": amount,
            "partial": amount < original_amount - 0.001,
            "original_employee_id": payload.from_employee_id,
            "original_employee_name": from_emp.get("name"),
            "corrected_to_employee_id": payload.to_employee_id,
            "corrected_to_employee_name": target_emp.get("name"),
            "reason": payload.reason.strip(),
            "corrected_by": uid,
            "corrected_at": now_iso,
        }
        entries = [
            {
                "entity_type": "employee",
                "entity_id":   payload.from_employee_id,
                "side":        "credit",
                "amount":      amount,
                "entry_type":  "correction",
                "sub_account": sub_account,
                "notes":       (
                    f"تصحيح: نقل الأثر من {from_emp.get('name') or 'موظف'} "
                    f"إلى {target_emp.get('name') or 'موظف'} — "
                    f"{payload.reason.strip()}"
                ),
                "metadata":    {**meta_common, "leg": "reverse_wrong"},
            },
            {
                "entity_type": "employee",
                "entity_id":   payload.to_employee_id,
                "side":        "debit",
                "amount":      amount,
                "entry_type":  "correction",
                "sub_account": sub_account,
                "notes":       (
                    f"تصحيح: نقل الأثر إلى {target_emp.get('name') or 'موظف'} "
                    f"من {from_emp.get('name') or 'موظف'} — "
                    f"{payload.reason.strip()}"
                ),
                "metadata":    {**meta_common, "leg": "apply_correct"},
            },
        ]

        result = await post_txn_group(
            db,
            user_id=uid, actor_id=uid, actor_name=actor_name,
            entries=entries,
            txn_type="employee_correction",
            notes=payload.reason.strip(),
            metadata={
                "corrects_txn_group_id":
                    payload.original_txn_group_id,
            },
        )
        # Bind the cross-link explicitly on each row so reports can
        # filter by `corrects_txn_group_id` without joining metadata.
        correction_group_id = result["txn_group_id"]
        await db.general_ledger.update_many(
            {"user_id": uid, "txn_group_id": correction_group_id},
            {"$set": {
                "corrects_txn_group_id":
                    payload.original_txn_group_id,
            }},
        )

        return {
            "correction_group_id": correction_group_id,
            "corrects_txn_group_id": payload.original_txn_group_id,
            "original_operation": original_entry_type,
            "sub_account": sub_account,
            "amount": amount,
            "from_employee": {
                "id": payload.from_employee_id,
                "name": from_emp.get("name"),
            },
            "to_employee": {
                "id": payload.to_employee_id,
                "name": target_emp.get("name"),
            },
            "reason": payload.reason.strip(),
            "bank_impact": 0.0,
            # is_partial = هل سيتبقى مبلغ غير مُصحَّح بعد هذا التصحيح؟
            "is_partial": round(remaining - amount, 2) > 0.001,
            "remaining_after_this": round(remaining - amount, 2),
            "entries": [
                {
                    "ledger_id": e.get("id"),
                    "entity_id": e.get("entity_id"),
                    "side": e.get("side"),
                    "amount": e.get("amount"),
                    "sub_account": e.get("sub_account"),
                }
                for e in result["entries"]
            ],
            "summary": (
                f"تم نقل أثر {amount:.2f} ر.س من "
                f"{from_emp.get('name') or 'موظف'} إلى "
                f"{target_emp.get('name') or 'موظف'} "
                f"دون أي حركة بنكية."
            ),
        }

    @router.get("/employees/corrections")
    async def list_corrections(
        emp_id: Optional[str] = None,
        user: dict = Depends(current_user),
    ):
        """Audit log of all corrections. Optional `emp_id` filter
        returns corrections that affected this employee (either as
        the wrong or correct party)."""
        uid = user["id"]
        q = {
            "user_id": uid,
            "entry_type": "correction",
            "status": "posted",
        }
        if emp_id:
            q["entity_id"] = emp_id
        seen_groups: dict = {}
        async for row in db.general_ledger.find(
            q,
            {"_id": 0, "id": 1, "txn_group_id": 1,
             "corrects_txn_group_id": 1, "entity_id": 1, "side": 1,
             "amount": 1, "sub_account": 1, "metadata": 1,
             "notes": 1, "posted_at": 1},
        ).sort("posted_at", -1).limit(500):
            gid = row.get("txn_group_id")
            if not gid:
                continue
            grp = seen_groups.setdefault(gid, {
                "correction_group_id": gid,
                "corrects_txn_group_id":
                    row.get("corrects_txn_group_id")
                    or (row.get("metadata") or {}).get(
                        "original_txn_group_id"),
                "posted_at": row.get("posted_at"),
                "sub_account": row.get("sub_account"),
                "amount": round(float(row.get("amount") or 0), 2),
                "reason": (row.get("metadata") or {}).get("reason"),
                "corrected_by": (row.get("metadata") or {}).get(
                    "corrected_by"),
                "original_operation":
                    (row.get("metadata") or {}).get(
                        "original_operation"),
                "is_partial": (row.get("metadata") or {}).get(
                    "partial", False),
                "from_employee": None,
                "to_employee":   None,
            })
            md = row.get("metadata") or {}
            if row.get("side") == "credit":
                grp["from_employee"] = {
                    "id": row.get("entity_id"),
                    "name": md.get("original_employee_name"),
                }
            else:
                grp["to_employee"] = {
                    "id": row.get("entity_id"),
                    "name": md.get("corrected_to_employee_name"),
                }
        return {"corrections": list(seen_groups.values())}

    return router
