"""Pure, bounded product-evidence preparation; no database or provider access.

Inputs are a contract for future trusted source adapters, NOT a public API that
turns caller-supplied 'verified' flags into source proof. No reader or executor
is wired by this module. Product association proves advertised scope only, not
order attribution. Monetary values remain in their original source currencies.
Evidence references identify immutable source rows, not whole multi-account
reports; adapters must qualify report IDs with the row scope. Currency names
use the repository pinned ISO vocabulary, loaded once by its existing module;
the pure builder performs no file, database or network reads.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from accounting_currency import CURRENCY_CODES

Provider = Literal["store_orders", "google_ads", "snapchat_ads", "tiktok", "meta"]
Verification = Literal["verified", "unverified"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _identity(value):
    if value is not None and (not value.strip() or value != value.strip()):
        raise ValueError("identity references cannot be blank or padded")
    return value


def _currency(value):
    if value is not None and value not in CURRENCY_CODES:
        raise ValueError("currency is outside the pinned ISO 4217 vocabulary")
    return value


class Observed(Strict):
    observed_at: datetime
    evidence_ref: str = Field(min_length=1, max_length=240, description="Immutable row-scoped source identity; qualify report references with their exact source row identity.")
    verification: Verification

    @field_validator("evidence_ref")
    @classmethod
    def reference(cls, value):
        return _identity(value)

    @field_validator("observed_at", mode="before")
    @classmethod
    def aware(cls, value):
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observation requires an aware timestamp")
        return value


def _decimal(value):
    if value is None:
        return value
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid decimal metric") from None
    if not number.is_finite() or number < 0 or number.adjusted() > 24 or number.as_tuple().exponent < -18:
        raise ValueError("metrics must be finite and nonnegative")
    return value


class ProductSource(Observed):
    product_id: str = Field(min_length=1, max_length=160)
    mezan_product_id: str | None = Field(default=None, min_length=1, max_length=160)
    status: Literal["available", "out_of_stock", "hidden", "unknown"]
    available_quantity: str | None = Field(default=None, max_length=40)
    unlimited_quantity: bool = False
    cost_quality: Literal["complete", "partial", "unknown"]
    unit_cost_native: str | None = Field(default=None, max_length=40)
    cost_currency_code: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    cost_evidence_ref: str | None = Field(default=None, min_length=1, max_length=240)
    cost_verification: Verification = "unverified"

    @field_validator("product_id", "mezan_product_id", "cost_evidence_ref")
    @classmethod
    def identifiers(cls, value):
        return _identity(value)

    @field_validator("cost_currency_code")
    @classmethod
    def currency(cls, value):
        return _currency(value)

    @field_validator("unit_cost_native", "available_quantity")
    @classmethod
    def numbers(cls, value):
        return _decimal(value)

    @model_validator(mode="after")
    def cost_proof(self):
        if self.unit_cost_native is not None and (self.cost_quality != "complete" or self.cost_verification != "verified" or not self.cost_currency_code or not self.cost_evidence_ref):
            raise ValueError("a reportable unit cost requires complete verified currency and provenance")
        if self.cost_quality == "complete" and self.unit_cost_native is None:
            raise ValueError("complete cost requires a unit cost")
        if self.status == "available" and not self.unlimited_quantity and (self.available_quantity is None or Decimal(self.available_quantity) <= 0):
            raise ValueError("available inventory requires positive quantity or explicit unlimited quantity")
        return self


class ProductSourceRecord(Observed):
    product_id: str = Field(min_length=1, max_length=160)
    provider: Provider
    account_id: str = Field(min_length=1, max_length=160)
    campaign_id: str | None = Field(default=None, min_length=1, max_length=160)
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")
    timezone: str = Field(min_length=1, max_length=80)
    date_from: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    date_to: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    complete: bool
    units_sold: str | None = Field(default=None, max_length=40)
    sales_native: str | None = Field(default=None, max_length=40)
    spend_native: str | None = Field(default=None, max_length=40)
    conversions: str | None = Field(default=None, max_length=40)
    conversion_value_native: str | None = Field(default=None, max_length=40)
    verified_orders: int | None = Field(default=None, ge=0)
    order_evidence_refs: list[str] = Field(default_factory=list, max_length=1000)
    product_link_verification: Verification = "unverified"
    product_link_evidence_ref: str | None = Field(default=None, min_length=1, max_length=240)

    @field_validator("product_id", "account_id", "campaign_id", "product_link_evidence_ref")
    @classmethod
    def identifiers(cls, value):
        return _identity(value)

    @field_validator("currency_code")
    @classmethod
    def currency(cls, value):
        return _currency(value)

    @field_validator("units_sold", "sales_native", "spend_native", "conversions", "conversion_value_native")
    @classmethod
    def numbers(cls, value):
        return _decimal(value)

    @model_validator(mode="after")
    def scope_proof(self):
        first, last = date.fromisoformat(self.date_from), date.fromisoformat(self.date_to)
        if first > last or (last - first).days > 365:
            raise ValueError("invalid bounded source period")
        try:
            ZoneInfo(self.timezone)
        except (KeyError, ValueError):
            raise ValueError("unknown source timezone") from None
        if self.complete and self.observed_at.astimezone(ZoneInfo(self.timezone)).date() <= last:
            raise ValueError("a complete period must be observed after its last local day closes")
        refs = self.order_evidence_refs
        if len(set(refs)) != len(refs) or any(not ref.strip() or len(ref) > 240 for ref in refs):
            raise ValueError("order proofs must be unique bounded references")
        if self.verified_orders is not None and (self.provider != "store_orders" or self.verified_orders != len(refs) or self.verification != "verified" or not self.complete):
            raise ValueError("verified orders require exact complete store-order proofs")
        if refs and self.verified_orders is None:
            raise ValueError("order proofs require an explicit count")
        if self.provider != "store_orders" and (not self.campaign_id or self.product_link_verification != "verified" or not self.product_link_evidence_ref):
            raise ValueError("platform product facts require a verified campaign-product association")
        return self


class ProductEvidenceRequest(Strict):
    plan_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    plan_revision: int = Field(ge=1)
    as_of: datetime
    max_observation_age_seconds: int = Field(ge=60, le=604800)
    products: list[ProductSource] = Field(min_length=1, max_length=100)
    records: list[ProductSourceRecord] = Field(default_factory=list, max_length=2000)

    @field_validator("as_of", mode="before")
    @classmethod
    def aware(cls, value):
        return Observed.aware(value)

    @model_validator(mode="after")
    def unique_scope(self):
        ids = [row.product_id for row in self.products]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate product identity")
        keys = set()
        evidence_scopes = {}
        for row in self.records:
            if row.product_id not in ids:
                raise ValueError("record belongs to an unselected product")
            key = (row.product_id, row.provider, row.account_id, row.campaign_id, row.currency_code, row.timezone, row.date_from, row.date_to)
            if key in keys:
                raise ValueError("duplicate or conflicting evidence scope")
            keys.add(key)
            if row.evidence_ref in evidence_scopes and evidence_scopes[row.evidence_ref] != key:
                raise ValueError("one evidence reference cannot identify conflicting source scopes")
            evidence_scopes[row.evidence_ref] = key
        return self


def _valid(row, request):
    age = (request.as_of - row.observed_at).total_seconds()
    return row.verification == "verified" and 0 <= age <= request.max_observation_age_seconds


def _public_record(row):
    result = row.model_dump(mode="json", exclude={"order_evidence_refs"})
    result["reported_roas"] = (str(Decimal(row.conversion_value_native) / Decimal(row.spend_native))
        if row.conversion_value_native is not None and row.spend_native is not None and Decimal(row.spend_native) > 0 else None)
    result["order_attribution"] = "store_order_proof" if row.provider == "store_orders" and row.verified_orders is not None else "not_established"
    return result


def build_product_evidence(request: ProductEvidenceRequest) -> dict:
    """Prepare independent evidence scopes; never rank or authorize execution."""
    last = request.as_of.astimezone(ZoneInfo("Asia/Riyadh")).date() - timedelta(days=1)
    windows = {"last_14_days": (last - timedelta(days=13), last), "previous_14_days": (last - timedelta(days=27), last - timedelta(days=14)),
        "last_30_days": (last - timedelta(days=29), last), "last_90_days": (last - timedelta(days=89), last)}
    products = []
    for source in request.products:
        records = [row for row in request.records if row.product_id == source.product_id]
        evidence_windows = {}
        for name, (first, end) in windows.items():
            matching = [row for row in records if row.provider == "store_orders" and row.timezone == "Asia/Riyadh" and row.date_from == first.isoformat() and row.date_to == end.isoformat()]
            valid = [row for row in matching if row.complete and _valid(row, request)]
            evidence_windows[name] = {"period": {"from": first.isoformat(), "to": end.isoformat(), "timezone": "Asia/Riyadh"},
                "availability": "missing" if not matching else "complete" if len(valid) == len(matching) else "unproven",
                "rows": [_public_record(row) for row in valid], "unproven_evidence_refs": [row.evidence_ref for row in matching if row not in valid]}
        current, prior = evidence_windows["last_14_days"], evidence_windows["previous_14_days"]
        growth = None
        if current['availability'] == prior['availability'] == 'complete' and len(current['rows']) == len(prior['rows']) == 1:
            a, b = current['rows'][0], prior['rows'][0]
            if (a['account_id'], a['currency_code']) == (b['account_id'], b['currency_code']) and a['units_sold'] is not None and b['units_sold'] is not None and Decimal(b['units_sold']) > 0:
                growth = str((Decimal(a['units_sold']) / Decimal(b['units_sold']) - 1) * 100)
        def ad_recent(row):
            # Preserve native provider periods; a recent refresh must not make
            # a future or year-old performance window look like current demand.
            source_last = request.as_of.astimezone(ZoneInfo(row.timezone)).date() - timedelta(days=1)
            return source_last - timedelta(days=89) <= date.fromisoformat(row.date_from) <= date.fromisoformat(row.date_to) <= source_last
        ads = [row for row in records if row.provider != 'store_orders' and row.complete and _valid(row, request) and ad_recent(row)]
        campaign_evidence = [_public_record(row) for row in ads]
        limitations = ['separate_approved_test_budget_required', 'profitability_not_computed', 'category_membership_and_merchant_mapping_not_verified', 'prior_season_alignment_not_proven',
            'platform_results_are_test_signals_not_success_guarantees', 'provider_conversions_are_not_summed_as_store_orders', 'source_verification_is_an_adapter_precondition_not_established_by_this_pure_builder']
        if source.cost_quality != 'complete': limitations.append('cost_quality_unknown_or_partial')
        if not _valid(source, request): limitations.append('catalog_source_unproven_or_stale')
        if any(not row.complete or not _valid(row, request) for row in records): limitations.append('some_source_records_unproven_or_stale')
        has_store_data = any(window['rows'] for window in evidence_windows.values())
        positive_platform_signal = any(row.conversions is not None and Decimal(row.conversions) > 0 for row in ads)
        status = 'excluded' if source.status in {'hidden', 'out_of_stock'} else 'needs_review' if not _valid(source, request) or source.status != 'available' else 'cross_platform_test_review' if positive_platform_signal else 'evidence_review' if has_store_data else 'new_product_test_review'
        store_orders = current['rows'][0]['verified_orders'] if current['availability'] == 'complete' and len(current['rows']) == 1 else None
        products.append({'product_id': source.product_id, 'mezan_product_id': source.mezan_product_id, 'catalog_evidence_ref': source.evidence_ref,
            'cost_quality': source.cost_quality if _valid(source, request) else 'unknown', 'reported_cost_quality': source.cost_quality, 'unit_cost_native': source.unit_cost_native if _valid(source, request) else None, 'cost_currency_code': source.cost_currency_code, 'cost_evidence_ref': source.cost_evidence_ref,
            'catalog_observed_at': source.observed_at.isoformat(), 'inventory_status': source.status if _valid(source, request) else 'unknown',
            'profitability': None, 'roas': None, 'windows': evidence_windows, 'growth_14_days_percent': growth, 'verified_store_orders': store_orders,
            'campaign_evidence': campaign_evidence, 'market_match': 'not_verified', 'unproven_or_out_of_window_evidence_refs': [row.evidence_ref for row in records if not row.complete or not _valid(row, request) or row.provider != 'store_orders' and not ad_recent(row)], 'candidate_status': status, 'success_classification': 'not_established', 'limitations': limitations, 'execution_authorized': False})
    return {'contract_version': 'google_product_evidence_v1', 'plan_id': request.plan_id, 'plan_revision': request.plan_revision, 'as_of': request.as_of.isoformat(),
        'provider_writes': 0, 'execution_authorized': False, 'reader_wired': False, 'source_verification_performed': False, 'products': products}
