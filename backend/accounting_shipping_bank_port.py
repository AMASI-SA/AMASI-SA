"""Track F bank/cash seam backed by the canonical Track A identity contract.

Final integration must delegate to Track A's require_financial_ledger_identity
with the same transaction-bound database. This module is not a resolver and
must not read accounts, settings, bindings, or any ledger as a fallback.
"""
from typing import Any, Literal, Protocol, TypedDict

from accounting_financial_identity import require_financial_ledger_identity


class ShippingFinancialIdentity(TypedDict):
    id: str
    account_type: Literal["bank", "cash"]
    currency: str
    status: str
    entity_type: str
    entity_id: str
    sub_account: str


class FinancialLedgerIdentityResolver(Protocol):
    """The existing Track A callable shape, not a new identity authority."""

    async def __call__(
        self, db: Any, owner: str, financial_account_id: str, *,
        account_types: tuple[str, ...] = ("bank", "cash"),
        currency: str = "SAR",
    ) -> ShippingFinancialIdentity: ...


async def require_shipping_bank_identity(
    db: Any, owner: str, financial_account_id: str,
) -> ShippingFinancialIdentity:
    """Resolve canonical identity without granting write authority.

    Preserve the transaction-bound database and the exact opening ledger key.
    Native writers retain permission, pause, P02, opening, balance and replay gates.
    """
    return await require_financial_ledger_identity(
        db, owner, financial_account_id, account_types=("bank", "cash"), currency="SAR",
    )
