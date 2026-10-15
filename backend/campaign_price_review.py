"""Bounded comparison of collector-verified offers, never a pricing decision.

No network, model, database or media access. This contract is internal: a model
or browser page cannot certify its own observations. Collectors must verify
product equivalence, merchant identity and tenant scope before calling it.
"""
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

Money = Annotated[int, Field(ge=0, le=100_000_000)]
Key = Annotated[str, Field(min_length=1, max_length=160)]
MAX_AGE = timedelta(hours=24)


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)


class Offer(Record):
    merchant_key: Key
    url: str = Field(max_length=1500)
    observed_at: datetime
    product_match: Literal['exact', 'similar', 'unknown']
    specification_key: Key
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    destination: Key
    # Final item price for the verified configuration; never base + this value.
    item_minor: Money
    price_basis: Literal['unknown', 'starting_from', 'single_configuration', 'selected_configuration'] = 'unknown'
    # Collector-normalized cross-merchant identity, not a merchant SKU or raw label.
    selected_options_key: Key | None = None
    base_item_minor: Money | None = None  # Informational only, never summed.
    shipping_minor: Money | None = None
    mandatory_fees_minor: Money | None = None
    unconditional_discount_minor: Money | None = None
    tax_included: bool | None = None
    in_stock: bool | None = None
    delivery_days: int | None = Field(default=None, ge=0, le=365)
    returns_key: Key | None = None
    warranty_key: Key | None = None
    payment_terms_key: Key | None = None
    quality_verified: bool = False

    @field_validator('observed_at')
    @classmethod
    def aware(cls, value):
        if value.utcoffset() is None:
            raise ValueError('timezone_required')
        return value

    @field_validator('url')
    @classmethod
    def public_reference(cls, value):
        u = urlsplit(value)
        if u.scheme != 'https' or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('canonical_public_url_required')
        return value


class PriceReviewInput(Record):
    user_id: Key
    store_id: Key
    product_id: Key
    own: Offer
    competitors: tuple[Offer, ...] = Field(max_length=5)


def unavailable_price_review():
    return {'status': 'not_checked', 'position': 'unknown', 'comparisons': [],
            'price_cause_verified': False, 'price_change_allowed': False,
            'message': 'لم تُنفّذ مقارنة أسعار موثقة؛ لا يوجد دليل يسمح بترجيح السعر سببًا لقلة التحويلات.'}


def _total(offer):
    fields = (offer.shipping_minor, offer.mandatory_fees_minor, offer.unconditional_discount_minor)
    if any(value is None for value in fields) or offer.tax_included is not True:
        return None
    # Conditional/member/coupon discounts must not enter this unconditional total.
    if offer.unconditional_discount_minor > offer.item_minor:
        return None
    return offer.item_minor + offer.shipping_minor + offer.mandatory_fees_minor - offer.unconditional_discount_minor


def _usable(offer, now):
    return (timedelta(seconds=-30) <= now - offer.observed_at <= MAX_AGE
            and offer.product_match == 'exact' and offer.quality_verified
            and offer.in_stock is True and _total(offer) is not None
            and (offer.price_basis == 'single_configuration'
                 or (offer.price_basis == 'selected_configuration' and offer.selected_options_key is not None))
            and all(getattr(offer, name) is not None for name in
                    ('delivery_days', 'returns_key', 'warranty_key', 'payment_terms_key')))


def assess_price(evidence, *, user_id, store_id, product_id, now=None):
    if (evidence.user_id, evidence.store_id, evidence.product_id) != (user_id, store_id, product_id):
        raise ValueError('price_evidence_scope_mismatch')
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError('timezone_required')
    result = unavailable_price_review()
    result.update(status='insufficient_evidence', excluded_offers=0, checked_at=now.isoformat())
    own = evidence.own
    if not _usable(own, now):
        result['message'] = 'بيانات سعر المنتج وقيمته وتكلفته النهائية غير مكتملة أو قديمة؛ لا يمكن ترجيح السعر.'
        return result
    def host(offer):
        return urlsplit(offer.url).hostname.removeprefix('www.')
    seen = {host(own)}
    merchants = {own.merchant_key}
    equal_fields = ('selected_options_key', 'specification_key', 'currency', 'destination', 'delivery_days',
                    'returns_key', 'warranty_key', 'payment_terms_key')
    for offer in evidence.competitors:
        domain = host(offer)
        if (domain in seen or offer.merchant_key in merchants or not _usable(offer, now)
                or any(getattr(own, key) != getattr(offer, key) for key in equal_fields)):
            result['excluded_offers'] += 1
            continue
        seen.add(domain)
        merchants.add(offer.merchant_key)
        result['comparisons'].append({'url': offer.url, 'observed_at': offer.observed_at.isoformat(),
            'currency': own.currency, 'own_total_minor': _total(own), 'competitor_total_minor': _total(offer),
            'difference_minor': _total(own) - _total(offer)})
    if len(result['comparisons']) < 2:
        result['message'] = 'العروض المتكافئة الموثقة غير كافية؛ لا يمكن ترجيح السعر قبل استكمال المقارنة.'
        return result
    # This is a sampled price observation, not the market price or a cause of
    # abandonment. Funnel evidence and a measured experiment are still required.
    above = all(row['difference_minor'] > 0 for row in result['comparisons'])
    result.update(status='comparable_sample', position='above_observed_offers' if above else 'not_above_observed_offers',
        message=('التكلفة النهائية أعلى من العروض المتكافئة المرصودة؛ هذا لا يثبت سبب قلة التحويلات.' if above
                 else 'المقارنة المرصودة لا تدعم أن التكلفة النهائية أعلى من جميع العروض المتكافئة.'))
    return result
