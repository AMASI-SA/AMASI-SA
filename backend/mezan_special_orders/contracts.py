"""Strict, immutable contracts. Money crosses the API in integer minor units."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

Text = Annotated[str, Field(min_length=1, max_length=300)]
Key = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:/-]+$")]
Minor = Annotated[StrictInt, Field(ge=0, le=10**12)]
Quantity = Annotated[StrictInt, Field(ge=1, le=999)]
Currency = Literal["SAR", "QAR", "AED", "USD", "KWD", "BHD", "OMR"]
Purpose = Literal["replacement", "gift", "creator", "marketing"]
Bucket = Literal["compensation", "marketing", "customer_care", "administrative"]
Stage = Literal["pending_review", "reviewed", "processing", "ready_to_ship", "completed", "delivering", "delivered", "cancelled", "refunded"]
EXPONENTS = {"SAR": 2, "QAR": 2, "AED": 2, "USD": 2, "KWD": 3, "BHD": 3, "OMR": 3}


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Actor(Contract):
    # Must be constructed from the authenticated session, never the request body.
    tenant_id: Key
    actor_id: Key
    permissions: frozenset[str] = frozenset()


class FxSnapshot(Contract):
    currency: Currency = "SAR"
    rate_to_sar: Annotated[str, Field(min_length=1, max_length=32)] = "1"
    captured_at: datetime
    evidence_id: Key

    @field_validator("captured_at")
    @classmethod
    def timezone_required(cls, value):
        if value.utcoffset() is None:
            raise ValueError("timezone_required")
        return value

    @model_validator(mode="after")
    def check_rate(self):
        try:
            rate = Decimal(self.rate_to_sar)
        except InvalidOperation as exc:
            raise ValueError("invalid_exchange_rate") from exc
        if not rate.is_finite() or not Decimal("0.000000001") <= rate <= Decimal("1000000"):
            raise ValueError("invalid_exchange_rate")
        if self.currency == "SAR" and rate != 1:
            raise ValueError("sar_rate_must_be_one")
        return self

    def to_sar_minor(self, amount: int) -> int:
        if type(amount) is not int or amount < 0:
            raise ValueError("invalid_minor_amount")
        return int((Decimal(amount) * Decimal(self.rate_to_sar) * 100 /
                    (10 ** EXPONENTS[self.currency])).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class Address(Contract):
    country_code: Annotated[str, Field(min_length=2, max_length=2)] = "SA"
    city: Text
    district: Text
    formatted: Annotated[str, Field(min_length=5, max_length=1500)]
    postal_code: str | None = Field(default=None, max_length=20)
    building_number: str | None = Field(default=None, max_length=30)
    short_address: str | None = Field(default=None, max_length=40)
    latitude: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    longitude: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)


class Recipient(Contract):
    name: Text
    mobile: Annotated[str, Field(min_length=7, max_length=20, pattern=r"^\+?[0-9]{7,19}$")]
    address: Address | None = None


class Evidence(Contract):
    # Private object identity, NOT a public/signed URL. Verified server-side.
    object_id: Key
    kind: Literal["bank_receipt", "carrier_label", "cost_document"]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Delivery(Contract):
    method: Literal["courier", "carrier"]
    carrier_key: Key | None = None
    tracking_number: str | None = Field(default=None, min_length=1, max_length=128)
    label: Evidence | None = None
    customer_charge_minor: Minor = 0

    @model_validator(mode="after")
    def validate_delivery(self):
        if self.method == "carrier":
            if not self.carrier_key or not self.tracking_number or not self.label:
                raise ValueError("new_carrier_label_required")
            if self.label.kind != "carrier_label":
                raise ValueError("invalid_label_kind")
        elif self.carrier_key or self.tracking_number or self.label:
            raise ValueError("courier_cannot_reuse_carrier_label")
        return self


class OptionRule(Contract):
    key: Key
    label: Text
    required: bool = True
    choices: tuple[Text, ...] = ()  # Empty means customer-specified text.
    max_length: Annotated[StrictInt, Field(ge=1, le=3000)] = 300


class OptionValue(Contract):
    key: Key
    value: Annotated[str, Field(min_length=1, max_length=3000)]


class Product(Contract):
    # Resolved by the catalog/original-order adapter, never client prices/costs.
    tenant_id: Key
    product_id: Key
    variant_id: Key | None = None
    sku: Text
    name: Text
    image_url: str | None = Field(default=None, max_length=2048)
    option_rules: tuple[OptionRule, ...] = ()
    requires_options: bool = False
    catalog_revision: Text

    @model_validator(mode="after")
    def rules_valid(self):
        keys = [r.key for r in self.option_rules]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_option_rule")
        if self.requires_options and not self.option_rules:
            raise ValueError("option_schema_unavailable")
        return self


class OriginalLine(Contract):
    item_id: Key
    product: Product
    quantity: Quantity
    options: tuple[OptionValue, ...] = ()


class OriginalOrder(Contract):
    tenant_id: Key
    order_id: Key
    order_number: Key
    recipient: Recipient
    items: tuple[OriginalLine, ...]
    label_object_id: Key | None = None
    tracking_number: str | None = None
    source: Literal["salla", "mezan"] = "salla"
    source_revision: Text


class Selection(Contract):
    line_key: Key
    original_item_id: Key | None = None
    product_id: Key | None = None
    variant_id: Key | None = None
    quantity: Quantity = 1
    # None inherits the original; empty explicitly requests no options.
    options: tuple[OptionValue, ...] | None = None
    substitution_reason: str | None = Field(default=None, min_length=3, max_length=500)
    customer_charge_minor: Minor = 0  # Total for this entire line, NOT unit price.


class ServiceCharge(Contract):
    key: Key
    label: Text
    customer_charge_minor: Minor = 0


class CollectionPlan(Contract):
    bank_transfer_minor: Minor = 0
    cod_minor: Minor = 0


class CreateOrder(Contract):
    purpose: Purpose
    expense_bucket: Bucket
    reason: Annotated[str, Field(min_length=3, max_length=1000)]
    original_order_number: Key | None = None
    recipient: Recipient | None = None
    delivery: Delivery
    items: Annotated[tuple[Selection, ...], Field(min_length=1, max_length=100)]
    services: Annotated[tuple[ServiceCharge, ...], Field(max_length=30)] = ()
    fx: FxSnapshot
    collection: CollectionPlan = Field(default_factory=CollectionPlan)
    campaign_id: Key | None = None
    cost_center_id: Key | None = None

    @model_validator(mode="after")
    def validate_purpose(self):
        if self.purpose == "replacement":
            if not self.original_order_number or self.expense_bucket != "compensation":
                raise ValueError("replacement_original_and_compensation_required")
            if any(not i.original_item_id for i in self.items):
                raise ValueError("replacement_item_link_required")
            ids = [i.original_item_id for i in self.items]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate_original_line")
        elif self.original_order_number or any(i.original_item_id for i in self.items):
            raise ValueError("unexpected_original_order_link")
        elif not self.recipient:
            raise ValueError("recipient_required")
        if self.purpose in {"creator", "marketing"} and self.expense_bucket != "marketing":
            raise ValueError("marketing_bucket_required")
        if self.purpose == "gift" and self.expense_bucket == "compensation":
            raise ValueError("compensation_requires_replacement")
        keys = [i.line_key for i in self.items]
        service_keys = [s.key for s in self.services]
        if len(keys) != len(set(keys)) or len(service_keys) != len(set(service_keys)):
            raise ValueError("duplicate_line_key")
        due = sum(i.customer_charge_minor for i in self.items) + self.delivery.customer_charge_minor + sum(s.customer_charge_minor for s in self.services)
        if due > 10**12:
            raise ValueError("order_charge_limit_exceeded")
        if self.collection.bank_transfer_minor + self.collection.cod_minor != due:
            raise ValueError("collection_plan_must_equal_allocated_charges")
        return self


class ReceiptClaim(Contract):
    evidence: Evidence
    bank_account_id: Key
    amount_minor: Annotated[StrictInt, Field(gt=0, le=10**12)]
    transferred_at: datetime
    reference: str | None = Field(default=None, max_length=150)

    @model_validator(mode="after")
    def validate_claim(self):
        if self.evidence.kind != "bank_receipt":
            raise ValueError("bank_receipt_required")
        if self.transferred_at.utcoffset() is None:
            raise ValueError("timezone_required")
        return self


class LedgerProof(Contract):
    # Returned exclusively by a server-owned MZ2 adapter after ledger read-back.
    tenant_id: Key
    order_id: Key
    movement_id: Key
    kind: Literal["bank_collection", "cod_collection", "remittance", "refund"]
    currency: Currency
    amount_minor: Annotated[StrictInt, Field(gt=0, le=10**12)]
    amount_sar_minor: Annotated[StrictInt, Field(gt=0, le=10**14)]
    account_id: Key
    evidence_id: Key
    receipt_claim_id: Key | None = None
    parent_movement_id: Key | None = None
    refund_from: Literal["bank", "custody"] | None = None
    credit_reference: Key | None = None
    posted: Literal[True] = True
    ledger: Literal["mezan_v2"] = "mezan_v2"


class CostProof(Contract):
    tenant_id: Key
    order_id: Key
    movement_id: Key
    kind: Literal["product", "shipping", "service"]
    target_key: Key
    unit_indices: tuple[Quantity, ...] = ()
    reverses_movement_id: Key | None = None
    # These are recognized expense facts, not a second payable or inventory issue.
    cost_sar_minor: Minor
    counterparty_id: Key
    origin: Literal["inventory_issue", "supplier_receipt", "carrier_charge", "service_receipt"]
    expense_bucket: Bucket
    evidence_id: Key
    posted: Literal[True] = True
    ledger: Literal["mezan_v2"] = "mezan_v2"


class WorkflowProof(Contract):
    tenant_id: Key
    order_id: Key
    source_revision: Annotated[StrictInt, Field(ge=1)]
    workflow_revision: Annotated[StrictInt, Field(ge=1)]
    snapshot_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    stage: Stage
    event_id: Key
    source: Literal["mezan"] = "mezan"
    options_complete: Literal[True] = True


class SettlementAdjustment(Contract):
    """Sidecar for a Salla order; source total and source paid stay unchanged."""
    original_order_id: Key
    original_order_number: Key
    basis: Literal["existing_source_balance", "additional_agreed_charge"]
    source_total_minor: Minor
    source_paid_minor: Minor
    source_revision: Text
    amount_minor: Annotated[StrictInt, Field(gt=0, le=10**12)]
    reason: Annotated[str, Field(min_length=3, max_length=1000)]
    evidence_id: Key
    fx: FxSnapshot

    @model_validator(mode="after")
    def check_balance(self):
        if self.source_paid_minor > self.source_total_minor:
            raise ValueError("source_overpayment_requires_reconciliation")
        if self.basis == "existing_source_balance" and self.amount_minor != self.source_total_minor - self.source_paid_minor:
            raise ValueError("must_match_existing_source_balance")
        return self


class AmendOptions(Contract):
    line_key: Key
    options: Annotated[tuple[OptionValue, ...], Field(max_length=100)]


class MovementReference(Contract):
    movement_id: Key


class WorkflowReference(Contract):
    event_id: Key


class RejectReceipt(Contract):
    claim_id: Key
    reason: Annotated[str, Field(min_length=3, max_length=500)]


class EmptyCommand(Contract):
    pass


class Command(Contract):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    operation: Literal["amend_options", "freeze_source", "attach_receipt", "reject_receipt", "observe_payment", "observe_cost", "observe_workflow"]
    payload: dict


COMMAND_MODELS = {"amend_options": AmendOptions, "freeze_source": EmptyCommand,
                  "attach_receipt": ReceiptClaim, "reject_receipt": RejectReceipt,
                  "observe_payment": MovementReference, "observe_cost": MovementReference,
                  "observe_workflow": WorkflowReference}
