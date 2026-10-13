"""Compare independently collected evidence; never infer sales or repair tags.

This module has no network, browser, persistence, or model client. Collectors
must establish tenant scope and provider ownership before creating these records.
An emitted browser request is not proof of provider receipt or attribution.
"""
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Provider = Literal["tiktok", "snapchat", "meta", "google"]
MAX_AGE = timedelta(minutes=15)


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Scope(Record):
    user_id: str = Field(min_length=1, max_length=160)
    store_id: str = Field(min_length=1, max_length=160)
    provider: Provider
    account_id: str = Field(min_length=1, max_length=160)
    campaign_id: str = Field(min_length=1, max_length=160)


class Observation(Record):
    scope: Scope
    job_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    observed_at: datetime

    @field_validator("observed_at")
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone_required")
        return value


class PixelEvent(Record):
    provider: Provider
    pixel_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    event: Literal["page_view", "view_item", "add_to_cart", "begin_checkout", "purchase", "other"]
    # Only allowlisted facts leave the worker. No URLs, cookies, raw request
    # bodies, email, phone, order IDs, click IDs, or customer identifiers.
    evidence_kind: Literal["configured", "browser_request_attempt"]


class BrowserObservation(Observation):
    status: Literal["completed", "blocked", "timeout", "unavailable"]
    events: tuple[PixelEvent, ...] = Field(default=(), max_length=100)
    consent: Literal["granted", "denied", "unknown"] = "unknown"
    media_downloaded: Literal[False] = False
    provider_events_sent: Literal[False] = False


class ProviderObservation(Observation):
    status: Literal["verified", "scope_missing", "unavailable", "partial"]
    # IDs must come from an owned campaign/ad-group binding, not a list of
    # all pixels available to the advertiser. Shared pixels are legitimate.
    campaign_pixel_ids: tuple[str, ...] = Field(default=(), max_length=20)
    binding_complete: bool = False
    purchase_receipt: Literal["observed", "not_observed_in_window", "unknown"] = "unknown"
    @field_validator("campaign_pixel_ids")
    @classmethod
    def valid_ids(cls, values):
        import re
        if len(set(values)) != len(values) or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) for value in values):
            raise ValueError("invalid_pixel_ids")
        return values


MESSAGES = {
    "not_checked": "لم يُنفّذ فحص المتجر بعد.",
    "browser_unavailable": "لم يكتمل فحص المتجر؛ لا يمكن الحكم على البكسل.",
    "provider_unavailable": "لم تثبت إعدادات مصدر الأحداث لهذه الحملة من المنصة.",
    "stale": "أدلة الفحص قديمة؛ يلزم فحص جديد قبل الاستنتاج.",
    "consent_limited": "موافقة التتبع غير مثبتة؛ قد تمنع ظهور أحداث المتصفح.",
    "binding_unknown": "ربط الحملة بمصدر الأحداث غير مكتمل؛ لا يمكن إجراء مقارنة نهائية.",
    "expected_not_observed": "لم نرصد مصدر أحداث الحملة في هذه الزيارة؛ هذا لا يثبت أنه معطّل.",
    "configured_only": "ظهر معرّف مصدر أحداث الحملة في الإعدادات، لكن لم نرصد محاولة إرسال حدث.",
    "request_observed": "رصدنا محاولة إرسال من المتصفح إلى مصدر أحداث الحملة؛ وصولها للمنصة يحتاج دليلًا مستقلًا.",
    "multiple_pixels": "ظهر أكثر من معرّف؛ وجود بكسلات متعددة لا يثبت خللًا أو تكرار شراء.",
    "purchase_unknown": "صحة أحداث الشراء وربطها بالطلبات لم تُثبت بهذا الفحص.",
}


def compare(scope: Scope, job_id: str, *, browser: BrowserObservation | None,
            provider: ProviderObservation | None, now: datetime | None = None) -> dict:
    """Fail closed on mixed tenants/jobs and stale or incomplete observations."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("timezone_required")
    for item in (browser, provider):
        if item and (item.scope != scope or item.job_id != job_id):
            raise ValueError("evidence_scope_mismatch")
    codes = []
    matched, other = [], []
    stale = any(item and not timedelta(seconds=-30) <= now - item.observed_at <= MAX_AGE
                for item in (browser, provider))
    if stale:
        codes.append("stale")
    elif browser is None:
        codes.append("not_checked")
    elif browser.status != "completed":
        codes.append("browser_unavailable")
    else:
        relevant = [event for event in browser.events if event.provider == scope.provider]
        seen = {event.pixel_id for event in relevant}
        if len(seen) > 1:
            codes.append("multiple_pixels")
        if browser.consent != "granted":
            codes.append("consent_limited")
        if provider is None or provider.status != "verified":
            codes.append("provider_unavailable")
        elif not provider.binding_complete or not provider.campaign_pixel_ids:
            codes.append("binding_unknown")
        else:
            expected = set(provider.campaign_pixel_ids)
            matched, other = sorted(seen & expected), sorted(seen - expected)
            if expected - seen:
                codes.append("expected_not_observed")
            if matched:
                attempted = {event.pixel_id for event in relevant if event.evidence_kind == "browser_request_attempt"}
                codes.append("request_observed" if attempted & expected else "configured_only")
    codes.append("purchase_unknown")
    return {
        "status": "partial" if matched else "unverified",
        "findings": [{"code": code, "message": MESSAGES[code]} for code in codes],
        "matched_pixel_ids": matched, "other_pixel_ids": other,
        "provider_purchase_receipt": provider.purchase_receipt if provider and provider.status == "verified" and not stale else "unknown",
        "financial_attribution_verified": False, "duplicate_purchase_verified": False,
        "pixel_broken_verified": False, "mutations_performed": False,
        "checked_at": now.isoformat(),
    }
