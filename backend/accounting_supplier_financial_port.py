"""Bind supplier payments to Track A's canonical identity without legacy reads."""
from accounting_financial_identity import (
    list_financial_ledger_identities,
    require_financial_ledger_identity,
)


class SupplierFinancialAccountPort:
    async def list_payment_accounts(self, db, owner):
        return await list_financial_ledger_identities(
            db, owner, account_types=("bank", "cash"), currency="SAR",
        )

    async def require_payment_account(self, db, owner, account_id):
        return await require_financial_ledger_identity(
            db, owner, account_id, account_types=("bank", "cash"), currency="SAR",
        )
