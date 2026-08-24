"""Protect provider statement evidence once it enters the P01 workflow."""
from __future__ import annotations

from fastapi import HTTPException

from settlements_import.service import delete_file as _base_delete_file


async def delete_unlinked_settlement_file(db, user_id: str, file_id: str) -> dict:
    """Allow legacy deletion only while no accounting record references it.

    A P01 upload creates an auditable settlement record immediately. Its source
    workbook must therefore survive draft review, posting, rejection, and later
    audits. Posted evidence is never deleted; accounting correction happens by
    reversal in the journals phase.
    """
    linked = await db.accounting_settlements_v2.find_one(
        {
            "user_id": user_id,
            "source_file_id": file_id,
        },
        {
            "_id": 0,
            "id": 1,
            "status": 1,
            "ledger_txn_group_id": 1,
        },
    )
    if linked:
        status = str(linked.get("status") or "draft")
        message = (
            "لا يمكن حذف كشف مرتبط بتسوية محاسبية. "
            "القيد المرحّل يُصحح بالعكس ولا يُحذف."
            if status == "posted"
            else "لا يمكن حذف كشف مرتبط بمسودة محاسبية؛ أعد المسودة للمعالجة بدل حذف الدليل."
        )
        raise HTTPException(
            status_code=409,
            detail={
                "code": "accounting_settlement_evidence_locked",
                "message": message,
                "settlement_draft_id": linked.get("id"),
                "settlement_status": status,
                "ledger_txn_group_id": linked.get("ledger_txn_group_id"),
            },
        )
    return await _base_delete_file(db, user_id, file_id)


__all__ = ["delete_unlinked_settlement_file"]
