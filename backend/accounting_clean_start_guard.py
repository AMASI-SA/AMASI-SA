"""Fail-closed guards for the Mezan 2 clean-start accounting policy.

Legacy financial data remains readable for diagnostics, but it must never be
copied into Mezan 2.  The only writer allowed to create or change opening
balances is the dedicated P07 workflow.
"""
from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from accounting_module_contract import OPERATION_ID


LEGACY_FINANCIAL_MIGRATION_DISABLED = {
    "code": "legacy_financial_migration_disabled",
    "operation_id": OPERATION_ID,
    "message": (
        "ميزان 2 يبدأ بأرصدة افتتاحية موثقة عبر P07؛ "
        "ترحيل بيانات ميزان القديم معطل نهائيًا"
    ),
}

OPENING_BALANCE_P07_ONLY = {
    "code": "opening_balance_p07_only",
    "operation_id": OPERATION_ID,
    "message": "إنشاء أو تعديل الرصيد الافتتاحي متاح فقط عبر مسار P07",
}

ACCOUNTING_CUTOVER_NOT_SAFE_ACTIVE = {
    "code": "accounting_cutover_not_safe_active",
    "operation_id": OPERATION_ID,
    "message": "الكتابة المحاسبية مقفلة حتى اكتمال P07 وتحقق safe_active",
}

ACCOUNTING_V2_DEDICATED_LEDGER_REQUIRED = {
    "code": "accounting_v2_dedicated_ledger_required",
    "operation_id": OPERATION_ID,
    "message": (
        "قيود ميزان 2 تكتب فقط عبر دفتر V2 المخصص؛ "
        "دفتر ledger العام مخصص لميزان القديم"
    ),
}


def reject_legacy_financial_migration() -> NoReturn:
    """Reject every legacy-to-Mezan-2 financial migration write."""
    raise HTTPException(
        status_code=409,
        detail=dict(LEGACY_FINANCIAL_MIGRATION_DISABLED),
    )


def reject_opening_balance_bypass() -> NoReturn:
    """Reject opening-balance writes outside the dedicated P07 workflow."""
    raise HTTPException(
        status_code=409,
        detail=dict(OPENING_BALANCE_P07_ONLY),
    )


def reject_accounting_v2_bypass() -> NoReturn:
    """Reject attempts to write Mezan 2 rows through a legacy ledger API."""
    raise HTTPException(
        status_code=409,
        detail=dict(ACCOUNTING_V2_DEDICATED_LEDGER_REQUIRED),
    )


async def accounting_safe_active(db, *, user_id: str) -> bool:
    """Stay closed until P07 supplies an authoritative activation verifier.

    Phase A deliberately has no activation path.  Existing settings and
    balanced ``opening_balance`` rows are legacy/untrusted inputs and therefore
    cannot unlock the book.  P07 must replace this function with verification
    of its operation marker and approved preview hash as one atomic change.
    """
    del db, user_id
    return False


async def require_accounting_safe_active(db, *, user_id: str) -> None:
    """Block accounting mutations until all P07 readiness checks pass."""
    if not await accounting_safe_active(db, user_id=user_id):
        raise HTTPException(
            status_code=409,
            detail=dict(ACCOUNTING_CUTOVER_NOT_SAFE_ACTIVE),
        )
