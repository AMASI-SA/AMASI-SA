"""Track F bank/cash seam, closed until the reviewed Track A integration.

Final integration must delegate to Track A's require_financial_ledger_identity
with the same transaction-bound database. This module is not a resolver and
must not read accounts, settings, bindings, or any ledger as a fallback.
"""
from typing import Any, Literal, Protocol, TypedDict

from fastapi import HTTPException


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
    """No flag, supplied ID, or available collection can open this seam.

    Reviewed integration must call:
    require_financial_ledger_identity(db, owner, financial_account_id,
                                     account_types=("bank", "cash"), currency="SAR")
    and return its ledger identity unchanged. Permission, pause, P02, opening,
    currency, balance, and idempotency checks still belong to the native writer.
    """
    raise HTTPException(503, detail={"code": "mz2_shipping_bank_port_not_integrated"})
