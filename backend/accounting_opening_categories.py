"""Explicit opening classifications for distinct assets and accrued liabilities.

These contracts record balances at cutover only. They do not schedule prepaid
expense amortization, settle supplier balances, or offset assets and liabilities.
"""
from __future__ import annotations


ADDITIONAL_OPENING_CATEGORIES: dict[str, dict[str, str]] = {
    "supplier_advance": {
        "label": "دفعة مقدمة لمورد",
        "entity_type": "supplier",
        "sub_account": "advance",
        "side": "debit",
        "section": "suppliers",
    },
    "prepaid_expense": {
        "label": "مصروف مدفوع مقدمًا",
        "entity_type": "asset",
        "sub_account": "prepaid_expense",
        "side": "debit",
        "section": "equity",
    },
    "accrued_expense": {
        "label": "مصروف مستحق غير مدفوع",
        "entity_type": "liability",
        "sub_account": "accrued_expense",
        "side": "credit",
        "section": "equity",
    },
}
