"""Draft-only executable specification. No adapters, database, routes or GL writer.

Inputs described as registry/permissions/positions are trusted server projections
from future shared adapters, NEVER browser-submitted proof. This module is not
registered with the application and cannot authorize or persist a financial write.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROVIDERS = frozenset({"snapchat", "meta", "tiktok", "google"})
PAYMENT_MODES = frozenset({"prepaid_wallet", "postpaid", "direct_debit"})
ACCRUAL_SOURCES = frozenset({"daily_spend", "invoice"})
DAY_STATES = ("OPEN", "DATA_COMPLETE", "DRAFT_CREATED", "RECONCILED", "REVIEWED", "POSTED")
EXCEPTIONS = frozenset({"INCOMPLETE_DATA", "MISSING_TIMEZONE", "MISSING_FUNDING_SOURCE",
    "MISSING_WALLET", "INSUFFICIENT_WALLET_BALANCE", "BANK_DIFFERENCE",
    "LATE_SPEND_ADJUSTMENT", "DUPLICATE_SOURCE", "NEEDS_REVIEW"})
INVOICE_STATES = frozenset({"draft", "unpaid", "partially_paid", "paid", "overdue", "disputed", "cancelled"})
PERMISSIONS = frozenset("accounting.advertising." + suffix for suffix in (
    "view", "settings.manage", "daily.view", "invoices.manage", "debts.review",
    "payments.record", "reconcile", "drafts.review", "journals.post"))
POST_PERMISSION = "accounting.advertising.journals.post"
CENT = Decimal("0.01")
ZERO = Decimal("0")


class ContractError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def token(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 200:
        raise ContractError("INVALID_REFERENCE")
    return value


def amount(value: str | int | Decimal | None, *, signed: bool = False, max_scale: int | None = 12) -> Decimal:
    if value is None:
        raise ContractError("INCOMPLETE_DATA")
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise ContractError("INVALID_AMOUNT")
    try:
        if len(str(value)) > 80:
            raise ContractError("INVALID_AMOUNT")
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ContractError("INVALID_AMOUNT") from exc
    if not result.is_finite() or abs(result) > Decimal("1e18") or (not signed and result < ZERO):
        raise ContractError("INVALID_AMOUNT")
    if max_scale is not None and result.as_tuple().exponent < -max_scale:
        raise ContractError("INVALID_AMOUNT")
    return result


def sar(value: str | int | Decimal, *, signed: bool = False) -> Decimal:
    return amount(value, signed=signed, max_scale=None).quantize(CENT, rounding=ROUND_HALF_UP)


def identity_key(parts: Sequence[str]) -> str:
    """Stable key only; NOT a uniqueness constraint or a transaction."""
    data = json.dumps(tuple(token(p) for p in parts), ensure_ascii=True, separators=(",", ":"))
    return sha256(data.encode()).hexdigest()


def can_preview(actor_owner: str | None, resource_owner: str, grants: Sequence[str] | None, permission: str) -> bool:
    return bool(actor_owner and actor_owner == resource_owner and permission in PERMISSIONS
                and permission != POST_PERMISSION and isinstance(grants, (list, tuple, set, frozenset))
                and permission in grants)


@dataclass(frozen=True)
class LinkedAccount:
    """Projection of an existing integration, not a financial account model."""
    owner_id: str
    linked_account_ref: str
    ad_provider: str
    external_account_id: str
    name: str
    currency: str
    timezone_name: str | None

    def __post_init__(self):
        for value in (self.owner_id, self.linked_account_ref, self.external_account_id, self.name):
            token(value)
        if self.ad_provider not in PROVIDERS:
            raise ContractError("UNSUPPORTED_PROVIDER")
        if len(self.currency) != 3 or not self.currency.isascii() or not self.currency.isalpha() or self.currency != self.currency.upper():
            raise ContractError("INVALID_CURRENCY")

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.owner_id, self.ad_provider, self.external_account_id


def linked_accounts(owner: str, registry: Sequence[LinkedAccount]) -> tuple[LinkedAccount, ...]:
    token(owner)
    rows = tuple(a for a in registry if a.owner_id == owner)
    if len({a.identity for a in rows}) != len(rows) or len({a.linked_account_ref for a in rows}) != len(rows):
        raise ContractError("DUPLICATE_SOURCE")
    return rows


def select_linked(owner: str, registry: Sequence[LinkedAccount], linked_ref: str) -> LinkedAccount:
    for account in linked_accounts(owner, registry):
        if account.linked_account_ref == linked_ref:
            return account
    raise ContractError("ACCOUNT_NOT_LINKED")


def account_zone(name: str | None) -> ZoneInfo:
    if not name:
        raise ContractError("BLOCKED_TIMEZONE_MISSING")
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ContractError("BLOCKED_TIMEZONE_INVALID") from exc


@dataclass(frozen=True)
class CloseWindow:
    economic_date: date
    timezone_name: str
    due_utc: datetime


def close_window(day: date, timezone_name: str | None) -> CloseWindow:
    if type(day) is not date:
        raise ContractError("INVALID_ECONOMIC_DATE")
    zone = account_zone(timezone_name)
    due = datetime.combine(day + timedelta(days=1), time(1), zone).replace(fold=0)
    utc = due.astimezone(timezone.utc)
    if utc.astimezone(zone).replace(tzinfo=None) != due.replace(tzinfo=None):
        raise ContractError("BLOCKED_NONEXISTENT_LOCAL_TIME")
    return CloseWindow(day, zone.key, utc)


def yesterday_due(now: datetime, timezone_name: str | None) -> CloseWindow | None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ContractError("AWARE_TIME_REQUIRED")
    local = now.astimezone(account_zone(timezone_name))
    window = close_window(local.date() - timedelta(days=1), timezone_name)
    return window if now.astimezone(timezone.utc) >= window.due_utc else None


@dataclass(frozen=True)
class FxSnapshot:
    original_amount: Decimal
    original_currency: str
    source_already_sar: bool
    source_sar_amount: Decimal | None
    rate_used: Decimal
    value_sar: Decimal
    fx_source: str
    settings_revision: str
    settings_json: str


def freeze_fx(original, currency: str, *, rate=None, source_already_sar=False,
              source_sar=None, fx_source: str, settings_revision: str, settings: Mapping) -> FxSnapshot:
    native = amount(original)
    if len(currency) != 3 or not currency.isascii() or not currency.isalpha() or currency != currency.upper():
        raise ContractError("INVALID_CURRENCY")
    if type(source_already_sar) is not bool:
        raise ContractError("INVALID_SAR_FLAG")
    token(fx_source)
    token(settings_revision)
    # Serialize immediately, so later mutation of the live settings cannot leak in.
    frozen = json.dumps(dict(settings), sort_keys=True, separators=(",", ":"), allow_nan=False)
    ready = amount(source_sar) if source_already_sar else None
    if source_already_sar:
        used, converted = Decimal("1"), ready
        if (native == ZERO and ready != ZERO) or (currency == "SAR" and sar(native) != sar(ready)):
            raise ContractError("SOURCE_AMOUNT_CONFLICT")
    else:
        if source_sar is not None:
            raise ContractError("AMBIGUOUS_SAR_SOURCE")
        used = Decimal("1") if currency == "SAR" and rate is None else amount(rate)
        if used <= ZERO:
            raise ContractError("FX_RATE_REQUIRED")
        if currency == "SAR" and used != Decimal("1"):
            raise ContractError("DOUBLE_FX")
        converted = native * used
    return FxSnapshot(native, currency, source_already_sar, ready, used, sar(converted),
                      fx_source, settings_revision, frozen)


@dataclass(frozen=True)
class DailyEvidence:
    account_identity: tuple[str, str, str]
    economic_date: date
    timezone_name: str | None
    source_ref: str
    revision: int
    complete: bool
    fx: FxSnapshot | None


@dataclass(frozen=True)
class DailyDraft:
    source_key: str
    evidence: DailyEvidence
    payment_mode: str
    accrual_source: str
    state: str = "DRAFT_CREATED"

    @property
    def proposed_expense_sar(self):
        return self.evidence.fx.value_sar if self.accrual_source == "daily_spend" else ZERO


def daily_draft(account: LinkedAccount, evidence: DailyEvidence, mode: str, accrual: str) -> DailyDraft:
    if mode not in PAYMENT_MODES or accrual not in ACCRUAL_SOURCES:
        raise ContractError("INVALID_ACCOUNT_POLICY")
    close_window(evidence.economic_date, account.timezone_name)
    if evidence.account_identity != account.identity or evidence.timezone_name != account.timezone_name:
        raise ContractError("SOURCE_SCOPE_MISMATCH")
    if evidence.complete is not True or evidence.fx is None:
        raise ContractError("INCOMPLETE_DATA")
    if evidence.fx.original_currency != account.currency:
        raise ContractError("SOURCE_CURRENCY_MISMATCH")
    if type(evidence.revision) is not int or evidence.revision < 1:
        raise ContractError("SOURCE_REVISION_REQUIRED")
    token(evidence.source_ref)
    key = identity_key((*account.identity, "daily_spend", evidence.economic_date.isoformat()))
    return DailyDraft(key, evidence, mode, accrual)


@dataclass(frozen=True)
class RevisionPreview:
    kind: str
    key: str
    difference_sar: Decimal
    replacement: DailyDraft | None
    audit: tuple[str, ...]


def revise_daily(current: DailyDraft, fresh: DailyDraft, *, accounted_total_sar=None) -> RevisionPreview:
    old, new = current.evidence, fresh.evidence
    if old.fx is None or new.fx is None or new.complete is not True:
        raise ContractError("INCOMPLETE_DATA")
    if current.state not in DAY_STATES or current.source_key != fresh.source_key:
        raise ContractError("SOURCE_SCOPE_MISMATCH")
    if (old.timezone_name, current.payment_mode, current.accrual_source) != (new.timezone_name, fresh.payment_mode, fresh.accrual_source):
        raise ContractError("HISTORICAL_POLICY_CHANGED")
    if (old.fx.original_currency, old.fx.source_already_sar) != (new.fx.original_currency, new.fx.source_already_sar):
        raise ContractError("SOURCE_CURRENCY_MISMATCH")
    if new.revision < old.revision:
        raise ContractError("STALE_SOURCE_REVISION")
    payload_old = (old.source_ref, old.fx.original_amount, old.fx.source_sar_amount)
    payload_new = (new.source_ref, new.fx.original_amount, new.fx.source_sar_amount)
    if new.revision == old.revision:
        if payload_old != payload_new:
            raise ContractError("DUPLICATE_SOURCE")
        return RevisionPreview("unchanged", current.source_key, ZERO, None, ())
    historical_fx = freeze_fx(new.fx.original_amount, old.fx.original_currency,
        rate=old.fx.rate_used, source_already_sar=old.fx.source_already_sar,
        source_sar=new.fx.source_sar_amount, fx_source=old.fx.fx_source,
        settings_revision=old.fx.settings_revision, settings=json.loads(old.fx.settings_json))
    if current.state == "POSTED":
        # Existing core must supply original + already-accounted adjustments.
        baseline = amount(accounted_total_sar)
        delta = sar(historical_fx.value_sar - baseline, signed=True)
        key = identity_key((current.source_key, "late_adjustment", str(new.revision)))
        return RevisionPreview("adjustment_draft" if delta else "unchanged", key, delta, None,
                               (str(baseline), str(historical_fx.value_sar), "posted_source_immutable"))
    updated = replace(current, evidence=replace(new, fx=historical_fx), state="DRAFT_CREATED")
    return RevisionPreview("replace_draft", current.source_key,
        sar(historical_fx.value_sar - old.fx.value_sar, signed=True), updated,
        (str(old.fx.value_sar), str(historical_fx.value_sar), "review_invalidated"))


@dataclass(frozen=True)
class InvoiceDraft:
    account: LinkedAccount
    external_number: str
    period_start: date
    period_end: date
    issued_on: date
    due_on: date
    fx: FxSnapshot
    evidence_ref: str
    source: str
    manual_note: str = ""
    provider_status: str | None = None

    def __post_init__(self):
        token(self.external_number)
        token(self.evidence_ref)
        if any(type(d) is not date for d in (self.period_start, self.period_end, self.issued_on, self.due_on)):
            raise ContractError("INVALID_INVOICE_DATE")
        if self.period_end < self.period_start or self.due_on < self.issued_on:
            raise ContractError("INVALID_INVOICE_DATE")
        if self.source not in {"api", "upload", "manual"} or (self.source == "manual" and not self.manual_note.strip()):
            raise ContractError("INVOICE_EVIDENCE_REQUIRED")
        if self.fx.original_currency != self.account.currency:
            raise ContractError("SOURCE_CURRENCY_MISMATCH")

    @property
    def key(self) -> str:
        return identity_key((*self.account.identity, "invoice", self.external_number))


@dataclass(frozen=True)
class InvoicePosition:
    invoice: InvoiceDraft
    paid_original: Decimal = ZERO
    paid_carrying_sar: Decimal = ZERO

    def __post_init__(self):
        object.__setattr__(self, "paid_original", amount(self.paid_original))
        object.__setattr__(self, "paid_carrying_sar", amount(self.paid_carrying_sar))
        if self.paid_original > self.invoice.fx.original_amount or self.paid_carrying_sar > self.invoice.fx.value_sar:
            raise ContractError("ALLOCATION_EXCEEDS_REMAINING")
        if self.paid_original == ZERO and self.paid_carrying_sar != ZERO:
            raise ContractError("INVALID_PAID_POSITION")
        if self.paid_original == self.invoice.fx.original_amount and self.paid_carrying_sar != self.invoice.fx.value_sar:
            raise ContractError("INVALID_PAID_POSITION")

    @property
    def remaining_original(self):
        return self.invoice.fx.original_amount - self.paid_original

    @property
    def remaining_sar(self):
        return self.invoice.fx.value_sar - self.paid_carrying_sar

    def status(self, as_of: date, *, reviewed: bool = False) -> str:
        if not reviewed:
            return "draft"
        if self.remaining_original == ZERO:
            return "paid"
        if self.invoice.due_on < as_of:
            return "overdue"
        return "partially_paid" if self.paid_original else "unpaid"


@dataclass(frozen=True)
class MatchPreview:
    invoice_sar: Decimal
    recognized_sar: Decimal
    difference_sar: Decimal
    kind: str
    proposed_expense_sar: Decimal


def match_invoice(invoice: InvoiceDraft, accrual: str, recognized_sar, *, coverage_complete: bool, approved_difference: bool = False) -> MatchPreview:
    recognized = amount(recognized_sar)
    if accrual not in ACCRUAL_SOURCES:
        raise ContractError("INVALID_ACCOUNT_POLICY")
    if coverage_complete is not True:
        raise ContractError("INCOMPLETE_DATA")
    delta = sar(invoice.fx.value_sar - recognized, signed=True)
    if accrual == "invoice":
        if recognized != ZERO:
            raise ContractError("DUPLICATE_SOURCE")
        kind, proposed = "invoice_draft", invoice.fx.value_sar
    elif delta == ZERO:
        kind, proposed = "matched", ZERO
    else:
        kind, proposed = ("adjustment_draft", delta) if approved_difference is True else ("NEEDS_REVIEW", ZERO)
    return MatchPreview(invoice.fx.value_sar, recognized, delta, kind, proposed)


@dataclass(frozen=True)
class PreviewLeg:
    role: str
    reference: str | None
    debit: Decimal = ZERO
    credit: Decimal = ZERO


@dataclass(frozen=True)
class JournalPreview:
    legs: tuple[PreviewLeg, ...]
    posting_available: bool = False

    def __post_init__(self):
        if any(amount(p.debit) and amount(p.credit) for p in self.legs):
            raise ContractError("INVALID_PREVIEW")
        if self.posting_available or sum(p.debit for p in self.legs) != sum(p.credit for p in self.legs):
            raise ContractError("INVALID_PREVIEW")


def expense_preview(mode: str, value_sar, funding_ref: str | None, *, accrual_source: str, wallet_balance=None,
                    movement_ref=None, bank_principal=None, actual_fee="0") -> JournalPreview:
    value, fee = sar(value_sar), sar(actual_fee)
    if accrual_source != "daily_spend":
        raise ContractError("NON_ACCRUING_DAILY_SOURCE")
    if mode not in PAYMENT_MODES:
        raise ContractError("INVALID_ACCOUNT_POLICY")
    if not funding_ref:
        raise ContractError("MISSING_WALLET" if mode == "prepaid_wallet" else "MISSING_FUNDING_SOURCE")
    token(funding_ref)
    if mode != "direct_debit" and fee:
        raise ContractError("BANK_FEE_NOT_DAILY_EXPENSE")
    if mode == "prepaid_wallet":
        if wallet_balance is None:
            raise ContractError("NEEDS_REVIEW")
        if amount(wallet_balance) < value:
            raise ContractError("INSUFFICIENT_WALLET_BALANCE")
    if mode == "direct_debit":
        if not movement_ref:
            raise ContractError("BANK_EVIDENCE_REQUIRED")
        token(movement_ref)
        if bank_principal is None or sar(bank_principal) != value:
            raise ContractError("BANK_DIFFERENCE")
    role = {"prepaid_wallet": "wallet_asset", "postpaid": "platform_payable", "direct_debit": "actual_bank_cash"}[mode]
    legs = [PreviewLeg("advertising_expense", None, debit=value)]
    if fee:
        legs.append(PreviewLeg("bank_fee_expense", None, debit=fee))
    legs.append(PreviewLeg(role, funding_ref, credit=value + fee))
    return JournalPreview(tuple(legs))


@dataclass(frozen=True)
class PaymentPreview:
    source_ref: str
    movement_ref: str
    evidence_ref: str
    allocations: tuple[tuple[str, Decimal, Decimal], ...]
    remaining: tuple[tuple[str, Decimal, Decimal], ...]
    fx_difference_sar: Decimal
    journal: JournalPreview


def payment_preview(owner: str, positions: Sequence[InvoicePosition], allocations: Sequence[tuple[str, str]],
                    *, source_ref: str, movement_ref: str, evidence_ref: str,
                    bank_principal_sar, bank_fee_sar="0") -> PaymentPreview:
    for ref in (owner, source_ref, movement_ref, evidence_ref):
        token(ref)
    by_key = {p.invoice.key: p for p in positions}
    if len(by_key) != len(positions) or not allocations:
        raise ContractError("DUPLICATE_SOURCE")
    if len({key for key, _ in allocations}) != len(allocations):
        raise ContractError("DUPLICATE_SOURCE")
    booked, remains = [], []
    for key, raw in allocations:
        pos = by_key.get(key)
        if pos is None or pos.invoice.account.owner_id != owner:
            raise ContractError("INVOICE_SCOPE_MISMATCH")
        value = amount(raw)
        if value <= ZERO or value > pos.remaining_original:
            raise ContractError("ALLOCATION_EXCEEDS_REMAINING")
        carrying = pos.remaining_sar if value == pos.remaining_original else sar(pos.remaining_sar * value / pos.remaining_original)
        booked.append((key, value, carrying))
        remains.append((key, pos.remaining_original - value, pos.remaining_sar - carrying))
    carrying_total = sum(row[2] for row in booked)
    principal, fee = sar(bank_principal_sar), sar(bank_fee_sar)
    if principal <= ZERO:
        raise ContractError("BANK_EVIDENCE_REQUIRED")
    fx = sar(principal - carrying_total, signed=True)
    legs = [PreviewLeg("platform_payable", key, debit=carrying) for key, _, carrying in booked]
    if fee:
        legs.append(PreviewLeg("bank_fee_expense", None, debit=fee))
    if fx > ZERO:
        legs.append(PreviewLeg("fx_loss", None, debit=fx))
    elif fx < ZERO:
        legs.append(PreviewLeg("fx_gain", None, credit=-fx))
    legs.append(PreviewLeg("actual_bank_cash", source_ref, credit=principal + fee))
    return PaymentPreview(source_ref, movement_ref, evidence_ref, tuple(booked), tuple(remains), fx, JournalPreview(tuple(legs)))
