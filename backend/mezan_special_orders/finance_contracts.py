"""Commands for the existing MZ2 ledger owner; all request money is minor units."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Annotated, Literal

from pydantic import Field, StrictInt, field_validator, model_validator

from .contracts import Contract, Currency, Evidence, FxSnapshot, Key, Minor, Text

PositiveMinor = Annotated[StrictInt, Field(gt=0, le=10**12)]


class AccountingPolicy(Contract):
    effective_at: datetime
    classification: Literal["expense_recovery", "other_income"]
    tax_basis_points: Annotated[StrictInt, Field(ge=0, le=10000)]
    tax_treatment: Literal["tax_inclusive", "out_of_scope", "exempt", "zero_rated"]
    evidence: Evidence
    reason: Annotated[str, Field(min_length=5, max_length=1000)]

    @model_validator(mode="after")
    def valid(self):
        if self.effective_at.utcoffset() is None:
            raise ValueError("timezone_required")
        if self.tax_treatment != "tax_inclusive" and self.tax_basis_points != 0:
            raise ValueError("non_taxable_policy_requires_zero_rate")
        if self.evidence.kind != "cost_document":
            raise ValueError("accounting_policy_evidence_required")
        return self


class BankMovement(Contract):
    bank_account_id: Key
    bank_reference: Annotated[str, Field(min_length=3, max_length=128)]
    occurred_at: datetime
    evidence: Evidence
    cash_fx: FxSnapshot
    existing_transaction_id: Key | None = None
    confirmed_new_movement: bool = False

    @model_validator(mode="after")
    def valid(self):
        if self.occurred_at.utcoffset() is None:
            raise ValueError("timezone_required")
        if self.occurred_at > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("future_bank_movement_not_allowed")
        if self.evidence.kind != "bank_receipt":
            raise ValueError("bank_receipt_required")
        if bool(self.existing_transaction_id) == self.confirmed_new_movement:
            raise ValueError("choose_existing_or_confirm_new_bank_movement")
        return self

    @field_validator("confirmed_new_movement", mode="before")
    @classmethod
    def strict_bool(cls, value):
        if type(value) is not bool:
            raise ValueError("boolean_required")
        return value


class BankCollection(Contract):
    receipt_claim_id: Key
    movement: BankMovement


class CodCollection(Contract):
    amount_minor: PositiveMinor
    collector_kind: Literal["courier", "store_driver"]
    collector_id: Key
    evidence: Evidence


class Remittance(Contract):
    parent_movement_id: Key
    amount_minor: PositiveMinor
    movement: BankMovement


class Refund(Contract):
    parent_movement_id: Key
    amount_minor: PositiveMinor
    refund_from: Literal["bank", "custody"]
    movement: BankMovement | None = None
    evidence: Evidence
    reason: Annotated[str, Field(min_length=3, max_length=1000)]

    @model_validator(mode="after")
    def valid(self):
        if (self.refund_from == "bank") != bool(self.movement):
            raise ValueError("bank_refund_movement_required")
        return self


class Expense(Contract):
    kind: Literal["shipping", "service"]
    target_key: Key
    cost_sar_minor: Minor
    counterparty_kind: Literal["supplier", "courier", "store_driver"]
    counterparty_id: Key
    evidence: Evidence
    reason: Annotated[str, Field(min_length=3, max_length=1000)]

    @model_validator(mode="after")
    def valid(self):
        if self.evidence.kind != "cost_document":
            raise ValueError("cost_document_required")
        return self


class ReverseCost(Contract):
    movement_id: Key
    evidence: Evidence
    reason: Annotated[str, Field(min_length=3, max_length=1000)]


class CancelOrder(Contract):
    reason: Annotated[str, Field(min_length=3, max_length=1000)]
    evidence: Evidence


class SourceInvoice(Contract):
    invoice_id: Key


class FinalizeCosts(Contract):
    evidence: Evidence
    reason: Annotated[str, Field(min_length=3, max_length=1000)]


class FinancialCommand(Contract):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    operation: Literal["recognize_agreement", "bank_collection", "cod_collection", "remittance", "refund",
                       "expense", "reverse_cost", "cancel", "supplier_invoice", "finalize_costs"]
    payload: dict


from .contracts import EmptyCommand
FINANCIAL_MODELS = {"recognize_agreement": EmptyCommand, "bank_collection": BankCollection,
    "cod_collection": CodCollection, "remittance": Remittance, "refund": Refund,
    "expense": Expense, "reverse_cost": ReverseCost, "cancel": CancelOrder,
    "supplier_invoice": SourceInvoice, "finalize_costs": FinalizeCosts}
