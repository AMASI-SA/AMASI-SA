"""Owner-confirmed native advertising contracts. No legacy identity authority."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

PLATFORMS = ("snapchat", "meta", "tiktok", "google_ads")
BINDINGS = "mz2_ad_account_bindings_v2"
EXPENSES = "mz2_ad_expense_identities_v2"
FACTS = "mz2_ad_spend_snapshots_v2"
FX = "mz2_ad_fx_snapshots_v2"
AUDIT = "mz2_ad_setup_audit_v2"
LOCKS = "mz2_ad_setup_owners_v2"
POSTINGS = "mz2_ad_postings_v2"
SETUP_COLLECTIONS = frozenset({BINDINGS, EXPENSES, FACTS, FX, AUDIT, LOCKS})
Platform = Literal["snapchat", "meta", "tiktok", "google_ads"]


def fail(code, status=409, **details):
    raise HTTPException(status, {"code": code, **details})


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        fail("ad_amount_invalid", 422)
    if not result.is_finite() or result < 0 or result > Decimal("92233720368547758.07"):
        fail("ad_amount_invalid", 422)
    return result


def money(value):
    return format(decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Binding(Contract):
    platform: Platform
    integration_account_id: str = Field(min_length=1, max_length=160)
    platform_account_id: str = Field(min_length=1, max_length=160)
    wallet_financial_account_id: str | None = Field(default=None, min_length=1, max_length=160)
    payable_financial_account_id: str | None = Field(default=None, min_length=1, max_length=160)
    funding_mode: Literal["prepaid", "postpaid", "hybrid"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    hybrid_policy: Literal["explicit_split"] | None = None
    version: int = Field(ge=0, strict=True)
    evidence: str = Field(min_length=3, max_length=1000)


class Expense(Contract):
    purpose: Literal["advertising", "bank_fee"]
    entity_id: str = Field(min_length=1, max_length=160)
    evidence: str = Field(min_length=3, max_length=1000)


class FxSnapshot(Contract):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    business_date: date
    fx_rate_to_sar: str = Field(min_length=1, max_length=40)
    fx_at: datetime
    fx_source: str = Field(min_length=3, max_length=300)
    evidence: str = Field(min_length=3, max_length=1000)

    @field_validator("fx_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fx_at_timezone_required")
        return value


class SpendApproval(Contract):
    platform: Platform
    integration_account_id: str = Field(min_length=1, max_length=160)
    business_date: date
    expected_source_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence: str = Field(min_length=3, max_length=1000)
    completeness_evidence: str = Field(min_length=3, max_length=1000)
    timezone_evidence: str = Field(min_length=3, max_length=1000)


class SpendPost(Contract):
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    fx_snapshot_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    wallet_sar_amount: str | None = Field(default=None, min_length=1, max_length=40)


class BankMovement(Contract):
    platform: Platform
    integration_account_id: str = Field(min_length=1, max_length=160)
    kind: Literal["wallet_funding", "payable_settlement"]
    bank_financial_account_id: str = Field(min_length=1, max_length=160)
    amount_sar: str = Field(min_length=1, max_length=40)
    bank_evidence_id: str = Field(min_length=1, max_length=160)
    effective_at: datetime
    bank_fee_sar: str | None = Field(default=None, min_length=1, max_length=40)
    bank_fee_evidence: str | None = Field(default=None, min_length=3, max_length=1000)


def spend_legs(binding, expense_id, sar_amount, wallet_balance, wallet_sar_amount=None):
    total = decimal(sar_amount)
    if total <= 0 or total != decimal(money(total)):
        fail("ad_post_amount_invalid")
    mode = binding["funding_mode"]
    if mode == "hybrid":
        if binding.get("hybrid_policy") != "explicit_split" or wallet_sar_amount is None:
            fail("ad_hybrid_explicit_split_required")
        wallet = decimal(wallet_sar_amount)
        if wallet != decimal(money(wallet)) or wallet > total:
            fail("ad_hybrid_split_invalid")
    else:
        if wallet_sar_amount is not None:
            fail("ad_split_requires_hybrid")
        wallet = total if mode == "prepaid" else Decimal(0)
    if wallet > wallet_balance:
        fail("ad_wallet_insufficient_balance")
    legs = [leg("expense", expense_id, None, "debit", total)]
    if wallet:
        legs.append(leg("ad_account", binding["wallet_financial_account_id"], "balance", "credit", wallet))
    if total - wallet:
        legs.append(leg("ad_account", binding["payable_financial_account_id"], "debt", "credit", total - wallet))
    return legs


def leg(entity_type, entity_id, sub_account, side, amount):
    return dict(leg_key=f"{entity_type}:{entity_id}:{sub_account}:{side}",
                entity_type=entity_type, entity_id=entity_id, sub_account=sub_account,
                side=side, amount=money(amount), entry_type="advertising_v2")


def bank_movement_legs(binding, bank_identity, payload, fee_expense_id=None):
    """Pure journal plan; bank identity must come from Track A, never name/ref lookup."""
    amount = decimal(payload.amount_sar)
    if amount <= 0 or amount != decimal(money(amount)):
        fail("ad_bank_amount_invalid")
    wallet = payload.kind == "wallet_funding"
    account_id = binding.get("wallet_financial_account_id" if wallet else "payable_financial_account_id")
    if not account_id:
        fail("ad_bank_movement_binding_missing")
    if (bank_identity.get("entity_type"), bank_identity.get("sub_account")) != ("bank", "main"):
        fail("track_a_bank_identity_invalid")
    fee = decimal(payload.bank_fee_sar) if payload.bank_fee_sar is not None else Decimal(0)
    if fee and (not payload.bank_fee_evidence or not fee_expense_id):
        fail("ad_bank_fee_evidence_and_identity_required")
    if fee != decimal(money(fee)):
        fail("ad_bank_fee_amount_invalid")
    entries = [leg("ad_account", account_id, "balance" if wallet else "debt", "debit", amount),
               leg("bank", bank_identity["entity_id"], "main", "credit", amount + fee)]
    if fee:
        entries.append(leg("expense", fee_expense_id, None, "debit", fee))
    return entries
