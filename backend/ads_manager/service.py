"""Read-only aggregation service for the unified advertising manager.

This service deliberately separates:

* booked advertising expense: posted ``general_ledger`` debit entries;
* provider-reported spend/performance: local Snapchat / TikTok / Meta facts;
* integration health: Apps & Integrations Control Center.

No method in this module writes to MongoDB or calls an advertising platform.
"""
from __future__ import annotations

import asyncio
import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo

from ad_spend_reporting import booked_ad_expense_by_provider_and_date
from integrations_control_center.legacy_readers import sanitize_for_output
from integrations_control_center.service import IntegrationsControlCenterService


RIYADH_TZ = ZoneInfo("Asia/Riyadh")
MAX_RANGE_DAYS = 90
MAX_PERFORMANCE_ROWS = 2_000
MAX_ACCOUNTS = 250
DEFAULT_USD_TO_SAR = 3.7544
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PROVIDER_ORDER = ("snapchat", "tiktok", "meta")
PROVIDER_ALIASES = {
    "snap": "snapchat",
    "snapchat": "snapchat",
    "tiktok": "tiktok",
    "meta": "meta",
    "facebook": "meta",
    "instagram": "meta",
}
PROVIDER_DEFINITIONS = {
    "snapchat": {
        "label": "Snapchat",
        "integration_provider": "snapchat_ads",
    },
    "tiktok": {
        "label": "TikTok",
        "integration_provider": "tiktok_ads",
    },
    "meta": {
        "label": "Meta",
        "integration_provider": "meta_ads",
    },
}

SOURCE_DEFINITIONS = [
    {
        "key": "general_ledger",
        "role": "المصدر المحاسبي للمصروف الإعلاني المُرحّل",
        "grain": "حساب إعلاني × يوم صرف × قيد محاسبي",
        "authoritative_for": ["booked_ad_expense_sar"],
    },
    {
        "key": "snapchat_account_daily",
        "role": "صرف وأداء حسابات Snapchat المبلّغ من المنصة",
        "grain": "حساب إعلاني × يوم",
        "authoritative_for": [
            "snapchat_provider_reported_spend",
            "snapchat_provider_attribution",
        ],
    },
    {
        "key": "snapchat_daily_stats",
        "role": "أداء Snapchat المجمع المحفوظ محليًا",
        "grain": "يوم",
        "authoritative_for": ["snapchat_purchases", "snapchat_revenue"],
    },
    {
        "key": "meta_ads_daily",
        "role": "أداء حملات Meta المحفوظ محليًا",
        "grain": "حملة × يوم",
        "authoritative_for": [
            "meta_provider_reported_spend",
            "meta_campaign_identity",
            "meta_impressions",
            "meta_clicks",
            "meta_platform_attribution",
        ],
    },
    {
        "key": "tiktok_ads_daily",
        "role": "تغذية أداء حملات TikTok المحفوظة محليًا",
        "grain": "حملة × يوم",
        "authoritative_for": [
            "tiktok_provider_reported_spend",
            "tiktok_campaign_identity",
            "tiktok_impressions",
            "tiktok_clicks",
            "tiktok_platform_attribution",
        ],
    },
    {
        "key": "integrations-v2",
        "role": "حالة الربط والصحة وحداثة البيانات",
        "grain": "منصة × حساب",
        "authoritative_for": [
            "connection_status",
            "connection_provenance",
            "integration_health",
        ],
    },
]

OBSERVE_ONLY_POLICY = {
    "mode": "observe_only",
    "mutations_allowed": False,
    "advertising_mutations_enabled": False,
    "ai_can": [
        "قراءة الأداء المحفوظ داخل ميزان.",
        "مقارنة المنصات والحملات وشرح الفروقات.",
        "إظهار فجوات البيانات وحداثة كل مصدر.",
    ],
    "ai_cannot": [
        "إنشاء حملة أو إعلان أو مادة إعلانية.",
        "تعديل الميزانية أو الاستهداف أو حالة التسليم.",
        "إيقاف حملة أو استئنافها أو حذفها.",
        "مزامنة منصة أو تجديد Token من هذه الصفحة.",
        "كتابة أي حركة محاسبية أو تعديل سلة أو قيود.",
    ],
    "lifecycle_required_for_future_writes": [
        "proposal",
        "preview",
        "approval",
        "execution",
        "verification",
        "audit",
        "rollback",
    ],
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(value: Any, *, limit: int = 180) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _number(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _optional_nonnegative_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _optional_nonnegative_integer(value: Any) -> int | None:
    parsed = _optional_nonnegative_number(value)
    if parsed is None or not parsed.is_integer():
        return None
    return int(parsed)


def _round(value: Any, digits: int = 2) -> float:
    return round(_number(value), digits)


def _optional_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return round(numerator / denominator, 2)


def _metric_set(
    *,
    provider_reported_spend_sar: float | None,
    booked_ad_expense_sar: float | None,
    revenue_sar: float | None,
    purchases: int | None,
    impressions: int | None,
    clicks: int | None,
) -> dict:
    return {
        "provider_reported_spend_sar": (
            None
            if provider_reported_spend_sar is None
            else _round(provider_reported_spend_sar)
        ),
        "booked_ad_expense_sar": (
            None
            if booked_ad_expense_sar is None
            else _round(booked_ad_expense_sar)
        ),
        "platform_attributed_revenue_sar": (
            None if revenue_sar is None else _round(revenue_sar)
        ),
        "platform_reported_purchases": purchases,
        "platform_reported_impressions": impressions,
        "platform_reported_clicks": clicks,
        "platform_roas": _optional_ratio(
            revenue_sar,
            provider_reported_spend_sar,
        ),
        "platform_cpa_sar": _optional_ratio(
            provider_reported_spend_sar,
            purchases,
        ),
        "platform_cpc_sar": _optional_ratio(
            provider_reported_spend_sar,
            clicks,
        ),
        "platform_cpm_sar": (
            round((provider_reported_spend_sar / impressions) * 1000, 2)
            if provider_reported_spend_sar is not None
            and impressions is not None
            and impressions > 0
            else None
        ),
        "platform_ctr_pct": (
            round((clicks / impressions) * 100, 2)
            if clicks is not None
            and impressions is not None
            and impressions > 0
            else None
        ),
    }


async def _rows(
    db: Any,
    collection_name: str,
    query: dict,
    projection: dict,
    *,
    limit: int,
    sort: list[tuple[str, int]] | None = None,
) -> list[dict]:
    read_limit = limit + 1
    cursor = db[collection_name].find(query, projection)
    if sort:
        cursor = cursor.sort(sort)
    cursor = cursor.limit(read_limit)
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=read_limit)
    return [row async for row in cursor]


def _parse_range(
    date_from: str | None,
    date_to: str | None,
    *,
    today: date,
) -> tuple[date, date]:
    default_from = today.replace(day=1)
    if date_from and not ISO_DATE_RE.fullmatch(date_from):
        raise ValueError("invalid_date")
    if date_to and not ISO_DATE_RE.fullmatch(date_to):
        raise ValueError("invalid_date")
    try:
        start = date.fromisoformat(date_from) if date_from else default_from
        end = date.fromisoformat(date_to) if date_to else today
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_date") from exc
    if end < start:
        raise ValueError("date_to_before_date_from")
    if end > today:
        raise ValueError("future_date_not_allowed")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise ValueError("range_too_wide")
    return start, end


def _provider_filter(value: str | None) -> str:
    normalized = _clean_text(value, limit=24).lower() or "all"
    if normalized not in {"all", *PROVIDER_ORDER}:
        raise ValueError("invalid_provider")
    return normalized


def _valid_source_date(value: Any, date_from: str, date_to: str) -> bool:
    raw = str(value or "").strip()
    if not ISO_DATE_RE.fullmatch(raw):
        return False
    try:
        date.fromisoformat(raw)
    except ValueError:
        return False
    return date_from <= raw <= date_to


def _latest_marker(rows: Iterable[dict]) -> str | None:
    candidates: list[str] = []
    for row in rows:
        for key in ("updated_at", "last_synced_at", "date"):
            value = _clean_text(row.get(key), limit=64)
            if value:
                candidates.append(value)
                break
    return max(candidates) if candidates else None


def _observed_dates(rows: Iterable[dict]) -> set[str]:
    return {
        value
        for row in rows
        if (value := _clean_text(row.get("date"), limit=10))
    }


def _freshness(
    *,
    integration_delay: Any,
    latest_observed_at: str | None,
    observed_days: int,
    requested_days: int,
    now: datetime,
) -> dict:
    delay: float | None
    try:
        delay = float(integration_delay)
        if not math.isfinite(delay) or delay < 0:
            delay = None
    except (TypeError, ValueError):
        delay = None

    if delay is None and latest_observed_at:
        try:
            marker = latest_observed_at
            if len(marker) == 10:
                marker = f"{marker}T23:59:59+03:00"
            observed = datetime.fromisoformat(marker.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            delay = max(0.0, (now - observed.astimezone(timezone.utc)).total_seconds() / 60)
        except (TypeError, ValueError):
            delay = None

    if latest_observed_at is None and delay is None:
        status = "unavailable"
    elif delay is None:
        status = "unknown"
    elif delay <= 180:
        status = "fresh"
    elif delay <= 24 * 60:
        status = "delayed"
    else:
        status = "stale"
    return {
        "last_observed_at": latest_observed_at,
        "data_delay_minutes": None if delay is None else round(delay, 1),
        "observed_days": observed_days,
        "requested_days": requested_days,
        "status": status,
    }


def _normalized_provider(value: Any) -> str | None:
    return PROVIDER_ALIASES.get(_clean_text(value, limit=32).lower())


def _account_currency_maps(
    accounts: list[dict],
    legacy_accounts: list[dict],
    *,
    usd_to_sar_rate: float | None,
    global_rate_evidence: str,
) -> tuple[dict, dict]:
    exact: dict[tuple[str, str], dict] = {}
    candidates: dict[str, dict[tuple[str | None, float | None], dict]] = defaultdict(dict)

    def add_account(
        *,
        provider: str | None,
        external_id: str,
        internal_id: str,
        currency: str,
        fx_rate: float | None,
        evidence: str,
        overwrite: bool,
    ) -> None:
        if provider not in PROVIDER_ORDER:
            return
        normalized = {
            "currency": currency or None,
            "fx_rate": fx_rate if fx_rate and fx_rate > 0 else None,
            "evidence": evidence,
        }
        meaningful = bool(
            normalized["currency"]
            and (
                normalized["currency"] == "SAR"
                or normalized["fx_rate"] is not None
            )
        )
        if meaningful:
            signature = (normalized["currency"], normalized["fx_rate"])
            candidates[provider][signature] = normalized
        identifiers = {
            external_id,
            external_id.removeprefix("act_") if external_id else "",
            f"act_{external_id.removeprefix('act_')}" if external_id else "",
            internal_id,
        }
        for identifier in identifiers - {""}:
            key = (provider, identifier)
            existing = exact.get(key)
            existing_meaningful = bool(
                existing
                and existing.get("currency")
                and (
                    existing.get("currency") == "SAR"
                    or existing.get("fx_rate") is not None
                )
            )
            if (
                key not in exact
                or (overwrite and meaningful)
                or not existing_meaningful
            ):
                exact[key] = normalized

    for account in accounts:
        provider = _normalized_provider(account.get("provider"))
        external_id = _clean_text(account.get("external_account_id"), limit=120)
        currency = _clean_text(account.get("currency_native"), limit=12).upper()
        fx_settings = account.get("fx_to_sar") or {}
        if not isinstance(fx_settings, dict):
            fx_settings = {}
        fx = _optional_nonnegative_number(fx_settings.get("rate"))
        account_evidence = "ads_account"
        if currency == "USD" and fx_settings.get("mode") == "inherit_from_global":
            fx = (
                usd_to_sar_rate
                if usd_to_sar_rate and usd_to_sar_rate > 0
                else None
            )
            account_evidence = (
                f"ads_account_inherit_{global_rate_evidence}"
            )
        add_account(
            provider=provider,
            external_id=external_id,
            internal_id=_clean_text(account.get("id"), limit=120),
            currency=currency,
            fx_rate=(
                1.0
                if currency == "SAR"
                else fx
                if fx is not None and fx > 0
                else None
            ),
            evidence=account_evidence,
            overwrite=True,
        )

    for account in legacy_accounts:
        provider = _normalized_provider(account.get("ad_provider"))
        external_id = _clean_text(
            account.get("external_account_id") or account.get("external_id"),
            limit=120,
        )
        currency = _clean_text(
            account.get("currency") or account.get("ad_account_currency"),
            limit=12,
        ).upper()
        legacy_fx = (
            1.0
            if currency == "SAR"
            else usd_to_sar_rate
            if currency == "USD" and usd_to_sar_rate and usd_to_sar_rate > 0
            else None
        )
        add_account(
            provider=provider,
            external_id=external_id,
            internal_id=_clean_text(account.get("id"), limit=120),
            currency=currency,
            fx_rate=legacy_fx,
            evidence=(
                f"legacy_ad_account_currency_{global_rate_evidence}"
                if currency == "USD"
                else "legacy_ad_account_currency"
            ),
            overwrite=False,
        )

    by_provider = {
        provider: list(values.values())
        for provider, values in candidates.items()
    }
    return exact, by_provider


def _currency_for_row(
    provider: str,
    row: dict,
    *,
    exact_accounts: dict,
    provider_accounts: dict,
) -> tuple[str | None, float | None, str]:
    row_currency = _clean_text(
        row.get("currency_native") or row.get("currency"),
        limit=12,
    ).upper()
    row_fx = _optional_nonnegative_number(row.get("fx_rate"))
    if row_currency == "SAR":
        return row_currency, 1.0, "provider_row"
    if row_currency and row_fx is not None and row_fx > 0:
        return (
            row_currency,
            row_fx,
            "provider_row",
        )

    account_id = _clean_text(
        row.get("account_id")
        or row.get("advertiser_id")
        or row.get("ad_account_id"),
        limit=120,
    )
    account = exact_accounts.get((provider, account_id)) if account_id else None
    if account:
        currency = account.get("currency")
        rate = 1.0 if currency == "SAR" else account.get("fx_rate")
        if not row_currency or row_currency == currency:
            return currency, rate, account.get("evidence") or "account_match"

    candidates = provider_accounts.get(provider) or []
    if len(candidates) == 1:
        currency = candidates[0].get("currency")
        rate = 1.0 if currency == "SAR" else candidates[0].get("fx_rate")
        if not row_currency or row_currency == currency:
            return (
                currency,
                rate,
                candidates[0].get("evidence")
                or "uniform_provider_account_setting",
            )
    return None, None, "unknown"


def _campaign_rows(
    provider: str,
    rows: list[dict],
    *,
    exact_accounts: dict,
    provider_accounts: dict,
) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    for row in rows:
        campaign_id = _clean_text(row.get("campaign_id"), limit=160) or "_default"
        campaign_name = _clean_text(row.get("campaign_name"), limit=180)
        observed_date = _clean_text(row.get("date"), limit=10)
        account_id = _clean_text(
            row.get("account_id")
            or row.get("advertiser_id")
            or row.get("ad_account_id"),
            limit=120,
        )
        key = (account_id, campaign_id)
        currency, fx_rate, currency_evidence = _currency_for_row(
            provider,
            row,
            exact_accounts=exact_accounts,
            provider_accounts=provider_accounts,
        )
        target = grouped.setdefault(
            key,
            {
                "provider": provider,
                "provider_label": PROVIDER_DEFINITIONS[provider]["label"],
                "account_id": account_id or None,
                "campaign_id": campaign_id,
                "campaign_name": (
                    campaign_name
                    or ("إجمالي غير مفصل" if campaign_id == "_default" else campaign_id)
                ),
                "spend_reported": 0.0,
                "revenue_reported": 0.0,
                "purchases": 0,
                "impressions": 0,
                "clicks": 0,
                "_spend_seen": False,
                "_revenue_seen": False,
                "_purchases_seen": False,
                "_impressions_seen": False,
                "_clicks_seen": False,
                "_spend_complete": True,
                "_revenue_complete": True,
                "_purchases_complete": True,
                "_impressions_complete": True,
                "_clicks_complete": True,
                "_campaign_name_date": observed_date,
                "last_observed_date": None,
                "data_source": (
                    "meta_ads_daily" if provider == "meta" else "tiktok_ads_daily"
                ),
                "spend_currency": currency,
                "_fx_rate": fx_rate,
                "currency_evidence": currency_evidence,
            },
        )
        # Mixed currency evidence at the same campaign grain is unsafe to convert.
        if target["spend_currency"] != currency or target["_fx_rate"] != fx_rate:
            target["spend_currency"] = None
            target["_fx_rate"] = None
            target["currency_evidence"] = "conflicting"
        spend_value = _optional_nonnegative_number(row.get("spend"))
        if spend_value is not None:
            target["spend_reported"] += spend_value
            target["_spend_seen"] = True
        else:
            target["_spend_complete"] = False
        revenue_key = "purchase_value" if provider == "meta" else "revenue"
        revenue_value = _optional_nonnegative_number(row.get(revenue_key))
        if revenue_value is not None:
            target["revenue_reported"] += revenue_value
            target["_revenue_seen"] = True
        else:
            target["_revenue_complete"] = False
        purchase_value = (
            row.get("purchases")
            if row.get("purchases") is not None
            else row.get("conversions")
        )
        parsed_purchases = _optional_nonnegative_integer(purchase_value)
        if parsed_purchases is not None:
            target["purchases"] += parsed_purchases
            target["_purchases_seen"] = True
        else:
            target["_purchases_complete"] = False
        parsed_impressions = _optional_nonnegative_integer(
            row.get("impressions")
        )
        if parsed_impressions is not None:
            target["impressions"] += parsed_impressions
            target["_impressions_seen"] = True
        else:
            target["_impressions_complete"] = False
        parsed_clicks = _optional_nonnegative_integer(row.get("clicks"))
        if parsed_clicks is not None:
            target["clicks"] += parsed_clicks
            target["_clicks_seen"] = True
        else:
            target["_clicks_complete"] = False
        if campaign_name and (
            not target["_campaign_name_date"]
            or observed_date >= target["_campaign_name_date"]
        ):
            target["campaign_name"] = campaign_name
            target["_campaign_name_date"] = observed_date
        if observed_date and (
            target["last_observed_date"] is None
            or observed_date > target["last_observed_date"]
        ):
            target["last_observed_date"] = observed_date

    output: list[dict] = []
    for value in grouped.values():
        spend_seen = value.pop("_spend_seen")
        revenue_seen = value.pop("_revenue_seen")
        purchases_seen = value.pop("_purchases_seen")
        impressions_seen = value.pop("_impressions_seen")
        clicks_seen = value.pop("_clicks_seen")
        spend_complete = value.pop("_spend_complete")
        revenue_complete = value.pop("_revenue_complete")
        purchases_complete = value.pop("_purchases_complete")
        impressions_complete = value.pop("_impressions_complete")
        clicks_complete = value.pop("_clicks_complete")
        spend = (
            _round(value["spend_reported"])
            if spend_seen and spend_complete
            else None
        )
        revenue = (
            _round(value["revenue_reported"])
            if revenue_seen and revenue_complete
            else None
        )
        purchases = (
            int(value["purchases"])
            if purchases_seen and purchases_complete
            else None
        )
        impressions = (
            int(value["impressions"])
            if impressions_seen and impressions_complete
            else None
        )
        clicks = (
            int(value["clicks"])
            if clicks_seen and clicks_complete
            else None
        )
        value.pop("_campaign_name_date", None)
        currency = value["spend_currency"]
        fx_rate = value.pop("_fx_rate")
        sar_equivalent = (
            _round(spend * fx_rate)
            if spend is not None and currency and fx_rate is not None
            else None
        )
        revenue_sar_equivalent = (
            _round(revenue * fx_rate)
            if revenue is not None and currency and fx_rate is not None
            else None
        )
        value.update(
            {
                "spend_reported": spend,
                "spend_sar_equivalent": sar_equivalent,
                "revenue_reported": revenue,
                "purchases": purchases,
                "impressions": impressions,
                "clicks": clicks,
                "roas": _optional_ratio(revenue, spend),
                "cpa_reported": _optional_ratio(spend, purchases),
                "cpc_reported": _optional_ratio(spend, clicks),
                "cpm_reported": (
                    round((spend / impressions) * 1000, 2)
                    if spend is not None
                    and impressions is not None
                    and impressions > 0
                    else None
                ),
                "ctr_pct": (
                    round((clicks / impressions) * 100, 2)
                    if clicks is not None
                    and impressions is not None
                    and impressions > 0
                    else None
                ),
                "spend_share_pct": None,
                "revenue_sar_equivalent": revenue_sar_equivalent,
            }
        )
        output.append(value)
    spend_rows = [
        row for row in output if row["spend_reported"] is not None
    ]
    if spend_rows and all(
        row["spend_sar_equivalent"] is not None for row in spend_rows
    ):
        provider_spend_sar = sum(
            row["spend_sar_equivalent"] for row in spend_rows
        )
        if provider_spend_sar > 0:
            for row in spend_rows:
                row["spend_share_pct"] = round(
                    row["spend_sar_equivalent"] / provider_spend_sar * 100,
                    2,
                )
    output.sort(
        key=lambda row: (
            row.get("spend_sar_equivalent") is not None,
            row.get("spend_sar_equivalent")
            if row.get("spend_sar_equivalent") is not None
            else row.get("spend_reported") or 0,
        ),
        reverse=True,
    )
    return output


def _provider_spend_evidence(
    provider: str,
    rows: list[dict],
    *,
    exact_accounts: dict,
    provider_accounts: dict,
) -> dict[str, Any]:
    """Convert provider-reported daily spend to SAR without guessing currency."""

    totals: dict[str, float] = defaultdict(float)
    account_date: dict[str, float] = defaultdict(float)
    invalid_dates: set[str] = set()
    account_alignment_complete = bool(rows)
    for row in rows:
        date_key = _clean_text(row.get("date"), limit=10)
        if not date_key:
            account_alignment_complete = False
            continue
        raw_account_id = _clean_text(
            row.get("account_id")
            or row.get("advertiser_id")
            or row.get("ad_account_id"),
            limit=120,
        )
        canonical_account = raw_account_id.removeprefix("act_")
        if provider == "snapchat":
            # The older Snapchat writer stored the already-converted SAR value
            # in ``spend``; the multi-account writer also stores ``spend_sar``.
            snap_spend_sar = (
                row.get("spend_sar")
                if row.get("spend_sar") is not None
                else row.get("spend")
            )
            if snap_spend_sar is None:
                invalid_dates.add(date_key)
                account_alignment_complete = False
                continue
            spend_sar = _optional_nonnegative_number(snap_spend_sar)
            if spend_sar is None:
                invalid_dates.add(date_key)
                account_alignment_complete = False
                continue
            totals[date_key] += spend_sar
            if canonical_account:
                account_date[f"{canonical_account}\u241f{date_key}"] += spend_sar
            else:
                account_alignment_complete = False
            continue
        currency, fx_rate, _ = _currency_for_row(
            provider,
            row,
            exact_accounts=exact_accounts,
            provider_accounts=provider_accounts,
        )
        native_spend = _optional_nonnegative_number(row.get("spend"))
        if native_spend is None or not currency or fx_rate is None:
            invalid_dates.add(date_key)
            account_alignment_complete = False
            continue
        spend_sar = native_spend * fx_rate
        totals[date_key] += spend_sar
        if canonical_account:
            account_date[f"{canonical_account}\u241f{date_key}"] += spend_sar
        else:
            account_alignment_complete = False

    series = {
        date_key: None if date_key in invalid_dates else round(amount, 2)
        for date_key, amount in sorted(totals.items())
    } | {
        date_key: None
        for date_key in sorted(invalid_dates)
        if date_key not in totals
    }
    return {
        "series": series,
        "account_date": {
            pair: round(amount, 2)
            for pair, amount in sorted(account_date.items())
        },
        "account_alignment_complete": (
            account_alignment_complete and not invalid_dates
        ),
    }


def _campaign_coverage(provider: str, rows: list[dict]) -> dict:
    if provider == "snapchat":
        return {
            "status": "unavailable",
            "campaign_count": 0,
            "source_rows": 0,
            "detail": (
                "موصل Snapchat الحالي يحفظ أداء الحساب، ولا يحفظ هوية الحملات بعد."
            ),
        }
    campaign_ids = {
        _clean_text(row.get("campaign_id"), limit=160)
        for row in rows
        if _clean_text(row.get("campaign_id"), limit=160) not in {"", "_default"}
    }
    if campaign_ids:
        return {
            "status": "available",
            "campaign_count": len(campaign_ids),
            "source_rows": len(rows),
            "detail": "توجد هوية حملة حقيقية في البيانات المحلية.",
        }
    if rows:
        return {
            "status": "aggregate_only",
            "campaign_count": 0,
            "source_rows": len(rows),
            "detail": "توجد بيانات أداء، لكنها مجمعة دون هوية حملة موثقة.",
        }
    return {
        "status": "unavailable",
        "campaign_count": 0,
        "source_rows": 0,
        "detail": "لا توجد بيانات حملة محلية ضمن الفترة المحددة.",
    }


def _reconciliation(
    provider_reported_spend_sar: float | None,
    booked_ad_expense_sar: float | None,
    *,
    comparable: bool,
) -> dict:
    if provider_reported_spend_sar is None:
        return {
            "status": "no_data",
            "provider_reported_spend_sar": None,
            "booked_ad_expense_sar": (
                None
                if booked_ad_expense_sar is None
                else _round(booked_ad_expense_sar)
            ),
            "gap_sar": None,
            "gap_pct": None,
            "detail": "لا توجد حقيقة صرف من المنصة قابلة للمقارنة ضمن الفترة.",
        }
    if booked_ad_expense_sar is None:
        return {
            "status": "not_comparable",
            "provider_reported_spend_sar": _round(provider_reported_spend_sar),
            "booked_ad_expense_sar": None,
            "gap_sar": None,
            "gap_pct": None,
            "detail": "لا توجد قيود محاسبية مُرحّلة قابلة للمقارنة ضمن الفترة.",
        }
    if not comparable:
        return {
            "status": "not_comparable",
            "provider_reported_spend_sar": _round(provider_reported_spend_sar),
            "booked_ad_expense_sar": _round(booked_ad_expense_sar),
            "gap_sar": None,
            "gap_pct": None,
            "detail": (
                "الحقيقتان معروضتان منفصلتين؛ لم تتطابق تغطية الحساب واليوم "
                "بما يكفي لحساب فرق موثوق."
            ),
        }
    provider_value = _round(provider_reported_spend_sar)
    booked_value = _round(booked_ad_expense_sar)
    gap = _round(provider_value - booked_value)
    gap_pct = (
        round(abs(gap) / provider_value * 100, 2)
        if provider_value > 0
        else 0.0 if booked_value == 0 else None
    )
    threshold = max(1.0, provider_value * 0.02)
    status = "matched" if abs(gap) <= threshold else "drift"
    return {
        "status": status,
        "provider_reported_spend_sar": provider_value,
        "booked_ad_expense_sar": booked_value,
        "gap_sar": gap,
        "gap_pct": gap_pct,
        "detail": (
            "فرق المنصة مقابل المصروف المُرحّل ضمن سماحية 2% أو 1 ر.س."
            if status == "matched"
            else "صرف المنصة لا يطابق المصروف المحاسبي المُرحّل ضمن السماحية."
        ),
    }


def _public_campaign(row: dict) -> dict:
    return {
        key: value
        for key, value in row.items()
        if not key.startswith("_")
    }


class AdsManagerService:
    def __init__(
        self,
        db: Any,
        *,
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self.db = db
        self._now = now

    async def overview(
        self,
        user_id: str,
        *,
        date_from: str | None = None,
        date_to: str | None = None,
        provider: str | None = None,
        campaign_query: str | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> dict:
        now = self._now().astimezone(timezone.utc)
        today = now.astimezone(RIYADH_TZ).date()
        start, end = _parse_range(date_from, date_to, today=today)
        provider_filter = _provider_filter(provider)
        from_iso, to_iso = start.isoformat(), end.isoformat()
        requested_days = (end - start).days + 1
        date_query = {"user_id": user_id, "date": {"$gte": from_iso, "$lte": to_iso}}

        integration_task = IntegrationsControlCenterService(
            self.db,
            now=lambda: now,
        ).overview(user_id)
        booked_expense_task = booked_ad_expense_by_provider_and_date(
            self.db,
            user_id,
            from_iso,
            to_iso,
        )
        snap_account_task = _rows(
            self.db,
            "snapchat_account_daily",
            date_query,
            {
                "_id": 0,
                "date": 1,
                "spend_sar": 1,
                "spend": 1,
                "updated_at": 1,
                "ad_account_id": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("ad_account_id", 1)],
        )
        snap_stats_task = _rows(
            self.db,
            "snapchat_daily_stats",
            date_query,
            {
                "_id": 0,
                "date": 1,
                "purchases": 1,
                "revenue": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1)],
        )
        tiktok_task = _rows(
            self.db,
            "tiktok_ads_daily",
            date_query,
            {
                "_id": 0,
                "date": 1,
                "account_id": 1,
                "advertiser_id": 1,
                "campaign_id": 1,
                "campaign_name": 1,
                "spend": 1,
                "currency": 1,
                "currency_native": 1,
                "fx_rate": 1,
                "purchases": 1,
                "conversions": 1,
                "revenue": 1,
                "impressions": 1,
                "clicks": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("campaign_id", 1), ("account_id", 1)],
        )
        meta_task = _rows(
            self.db,
            "meta_ads_daily",
            date_query,
            {
                "_id": 0,
                "date": 1,
                "account_id": 1,
                "campaign_id": 1,
                "campaign_name": 1,
                "spend": 1,
                "currency": 1,
                "currency_native": 1,
                "fx_rate": 1,
                "purchases": 1,
                "purchase_value": 1,
                "impressions": 1,
                "clicks": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("campaign_id", 1), ("account_id", 1)],
        )
        accounts_task = _rows(
            self.db,
            "ads_accounts",
            {
                "user_id": user_id,
                "provider": {"$in": list(PROVIDER_ORDER)},
                "soft_deleted": {"$ne": True},
            },
            {
                "_id": 0,
                "id": 1,
                "provider": 1,
                "external_account_id": 1,
                "currency_native": 1,
                "fx_to_sar": 1,
            },
            limit=MAX_ACCOUNTS,
            sort=[("provider", 1), ("external_account_id", 1)],
        )
        legacy_accounts_task = _rows(
            self.db,
            "counterparties",
            {
                "user_id": user_id,
                "kind": "ad_account",
                "ad_provider": {"$in": list(PROVIDER_ALIASES)},
            },
            {
                "_id": 0,
                "id": 1,
                "ad_provider": 1,
                "external_account_id": 1,
                "external_id": 1,
                "currency": 1,
                "ad_account_currency": 1,
            },
            limit=MAX_ACCOUNTS,
            sort=[("ad_provider", 1), ("external_account_id", 1)],
        )
        currency_settings_task = self.db.ads_currency_settings.find_one(
            {"user_id": user_id},
            {"_id": 0, "usd_to_sar_rate": 1},
        )

        (
            integration_overview,
            booked_expense,
            snap_account_rows,
            snap_stats_rows,
            tiktok_rows,
            meta_rows,
            accounts,
            legacy_accounts,
            currency_settings,
        ) = await asyncio.gather(
            integration_task,
            booked_expense_task,
            snap_account_task,
            snap_stats_task,
            tiktok_task,
            meta_task,
            accounts_task,
            legacy_accounts_task,
            currency_settings_task,
        )
        source_limit_reached = {
            "snapchat_account_daily": len(snap_account_rows)
            > MAX_PERFORMANCE_ROWS,
            "snapchat_daily_stats": len(snap_stats_rows)
            > MAX_PERFORMANCE_ROWS,
            "tiktok_ads_daily": len(tiktok_rows) > MAX_PERFORMANCE_ROWS,
            "meta_ads_daily": len(meta_rows) > MAX_PERFORMANCE_ROWS,
            "ads_accounts": len(accounts) > MAX_ACCOUNTS,
            "counterparties": len(legacy_accounts) > MAX_ACCOUNTS,
        }
        snap_account_rows = snap_account_rows[:MAX_PERFORMANCE_ROWS]
        snap_stats_rows = snap_stats_rows[:MAX_PERFORMANCE_ROWS]
        tiktok_rows = tiktok_rows[:MAX_PERFORMANCE_ROWS]
        meta_rows = meta_rows[:MAX_PERFORMANCE_ROWS]
        accounts = accounts[:MAX_ACCOUNTS]
        legacy_accounts = legacy_accounts[:MAX_ACCOUNTS]
        dated_sources = {
            "snapchat_account_daily": snap_account_rows,
            "snapchat_daily_stats": snap_stats_rows,
            "tiktok_ads_daily": tiktok_rows,
            "meta_ads_daily": meta_rows,
        }
        source_invalid_date_rows = {
            source: sum(
                not _valid_source_date(row.get("date"), from_iso, to_iso)
                for row in rows
            )
            for source, rows in dated_sources.items()
        }
        snap_account_rows = [
            row
            for row in snap_account_rows
            if _valid_source_date(row.get("date"), from_iso, to_iso)
        ]
        snap_stats_rows = [
            row
            for row in snap_stats_rows
            if _valid_source_date(row.get("date"), from_iso, to_iso)
        ]
        tiktok_rows = [
            row
            for row in tiktok_rows
            if _valid_source_date(row.get("date"), from_iso, to_iso)
        ]
        meta_rows = [
            row
            for row in meta_rows
            if _valid_source_date(row.get("date"), from_iso, to_iso)
        ]
        stored_usd_to_sar_rate = _optional_nonnegative_number(
            (currency_settings or {}).get("usd_to_sar_rate")
        )
        if currency_settings is None:
            usd_to_sar_rate = DEFAULT_USD_TO_SAR
            global_rate_evidence = "approved_default"
        elif stored_usd_to_sar_rate and stored_usd_to_sar_rate > 0:
            usd_to_sar_rate = stored_usd_to_sar_rate
            global_rate_evidence = "global_setting"
        else:
            usd_to_sar_rate = None
            global_rate_evidence = "invalid_global_setting"
        exact_accounts, provider_accounts = _account_currency_maps(
            accounts,
            legacy_accounts,
            usd_to_sar_rate=usd_to_sar_rate,
            global_rate_evidence=global_rate_evidence,
        )
        if (
            source_limit_reached["ads_accounts"]
            or source_limit_reached["counterparties"]
        ):
            # Exact account matches remain safe, but a provider-wide currency
            # inference is unsafe when the account configuration read was cut.
            provider_accounts = {}

        raw_rows = {
            "snapchat": snap_stats_rows,
            "tiktok": tiktok_rows,
            "meta": meta_rows,
        }
        campaign_rows = {
            "tiktok": _campaign_rows(
                "tiktok",
                tiktok_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
            "meta": _campaign_rows(
                "meta",
                meta_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
        }
        provider_spend_evidence = {
            "snapchat": _provider_spend_evidence(
                "snapchat",
                snap_account_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
            "tiktok": _provider_spend_evidence(
                "tiktok",
                tiktok_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
            "meta": _provider_spend_evidence(
                "meta",
                meta_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
        }
        booked_by_provider = booked_expense.get("by_provider") or {}
        booked_incomplete = bool(
            booked_expense.get("row_limit_reached")
            or booked_expense.get("invalid_rows_count")
        )
        integration_cards = {
            row.get("provider"): row
            for row in integration_overview.get("providers") or []
        }

        provider_summaries: list[dict] = []
        for provider_key in PROVIDER_ORDER:
            definition = PROVIDER_DEFINITIONS[provider_key]
            spend_evidence = provider_spend_evidence[provider_key]
            spend_series = spend_evidence["series"]
            provider_source_key = (
                "snapchat_account_daily"
                if provider_key == "snapchat"
                else f"{provider_key}_ads_daily"
            )
            provider_source_truncated = source_limit_reached[
                provider_source_key
            ]
            provider_source_invalid = bool(
                source_invalid_date_rows[provider_source_key]
            )
            spend_values = list(spend_series.values())
            provider_reported_spend_sar = (
                sum(value for value in spend_values if value is not None)
                if not provider_source_truncated
                and not provider_source_invalid
                and spend_values
                and all(value is not None for value in spend_values)
                else None
            )
            booked_series = booked_by_provider.get(provider_key) or {}
            booked_ad_expense_sar = (
                sum(_number(value) for value in booked_series.values())
                if booked_series
                and not booked_incomplete
                else None
            )
            booked_account_date = (
                booked_expense.get("by_provider_account_date") or {}
            ).get(provider_key) or {}
            provider_account_date = spend_evidence["account_date"]
            comparable = (
                not provider_source_truncated
                and not provider_source_invalid
                and not booked_incomplete
                and not booked_expense.get("account_mapping_limit_reached")
                and spend_evidence["account_alignment_complete"]
                and bool(provider_account_date)
                and set(provider_account_date) == set(booked_account_date)
            )
            card = integration_cards.get(definition["integration_provider"]) or {}
            rows_for_freshness = (
                snap_stats_rows + snap_account_rows
                if provider_key == "snapchat"
                else raw_rows[provider_key]
            )

            if provider_key == "snapchat":
                has_performance = bool(snap_stats_rows)
                performance_truncated = source_limit_reached[
                    "snapchat_daily_stats"
                ] or bool(source_invalid_date_rows["snapchat_daily_stats"])
                snap_revenue_values = [
                    _optional_nonnegative_number(row.get("revenue"))
                    for row in snap_stats_rows
                ]
                snap_purchase_values = [
                    _optional_nonnegative_integer(row.get("purchases"))
                    for row in snap_stats_rows
                ]
                revenue_sar = (
                    sum(value for value in snap_revenue_values if value is not None)
                    if has_performance
                    and not performance_truncated
                    and all(value is not None for value in snap_revenue_values)
                    else None
                )
                purchases = (
                    sum(value for value in snap_purchase_values if value is not None)
                    if has_performance
                    and not performance_truncated
                    and all(value is not None for value in snap_purchase_values)
                    else None
                )
                impressions = None
                clicks = None
                campaign_source_rows = []
            else:
                grouped = campaign_rows[provider_key]
                has_performance = bool(raw_rows[provider_key])
                performance_truncated = (
                    provider_source_truncated or provider_source_invalid
                )
                revenue_values = [
                    row.get("revenue_sar_equivalent")
                    for row in grouped
                ]
                revenue_sar = (
                    sum(value for value in revenue_values if value is not None)
                    if grouped
                    and not performance_truncated
                    and all(value is not None for value in revenue_values)
                    else None
                )
                purchases = (
                    sum(int(row["purchases"]) for row in grouped)
                    if has_performance
                    and not performance_truncated
                    and all(row.get("purchases") is not None for row in grouped)
                    else None
                )
                impressions = (
                    sum(int(row["impressions"]) for row in grouped)
                    if has_performance
                    and not performance_truncated
                    and all(row.get("impressions") is not None for row in grouped)
                    else None
                )
                clicks = (
                    sum(int(row["clicks"]) for row in grouped)
                    if has_performance
                    and not performance_truncated
                    and all(row.get("clicks") is not None for row in grouped)
                    else None
                )
                campaign_source_rows = raw_rows[provider_key]

            metrics = _metric_set(
                provider_reported_spend_sar=provider_reported_spend_sar,
                booked_ad_expense_sar=booked_ad_expense_sar,
                revenue_sar=revenue_sar,
                purchases=purchases,
                impressions=impressions,
                clicks=clicks,
            )
            latest_observed = _latest_marker(rows_for_freshness)
            provider_summaries.append(
                {
                    "provider": provider_key,
                    "provider_label": definition["label"],
                    "integration_provider": definition["integration_provider"],
                    "connection_status": card.get("connection_status") or "unknown",
                    "connection_provenance": (
                        card.get("connection_provenance") or "unknown"
                    ),
                    "health_status": (card.get("health") or {}).get("status")
                    or "unknown",
                    "health_score": (card.get("health") or {}).get("score"),
                    "last_sync_at": card.get("last_sync_at"),
                    "metrics": metrics,
                    "freshness": _freshness(
                        integration_delay=card.get("data_delay_minutes"),
                        latest_observed_at=latest_observed,
                        observed_days=len(_observed_dates(rows_for_freshness)),
                        requested_days=requested_days,
                        now=now,
                    ),
                    "campaign_coverage": _campaign_coverage(
                        provider_key,
                        campaign_source_rows,
                    ),
                    "reconciliation": _reconciliation(
                        provider_reported_spend_sar,
                        booked_ad_expense_sar,
                        comparable=comparable,
                    ),
                    "metric_availability": {
                        "provider_spend": provider_reported_spend_sar is not None,
                        "booked_expense": booked_ad_expense_sar is not None,
                        "revenue": revenue_sar is not None,
                        "purchases": purchases is not None,
                        "impressions": impressions is not None,
                        "clicks": clicks is not None,
                        "campaigns": (
                            _campaign_coverage(
                                provider_key,
                                campaign_source_rows,
                            )["status"]
                            == "available"
                        ),
                    },
                }
            )

        selected_keys = (
            set(PROVIDER_ORDER)
            if provider_filter == "all"
            else {provider_filter}
        )
        selected_summaries = [
            row for row in provider_summaries if row["provider"] in selected_keys
        ]
        known_provider_spend = [
            row["metrics"]["provider_reported_spend_sar"]
            for row in selected_summaries
            if row["metrics"]["provider_reported_spend_sar"] is not None
        ]
        known_booked_expense = [
            row["metrics"]["booked_ad_expense_sar"]
            for row in selected_summaries
            if row["metrics"]["booked_ad_expense_sar"] is not None
        ]
        known_revenue = [
            row["metrics"]["platform_attributed_revenue_sar"]
            for row in selected_summaries
            if row["metrics"]["platform_attributed_revenue_sar"] is not None
        ]
        known_purchases = [
            row["metrics"]["platform_reported_purchases"]
            for row in selected_summaries
            if row["metrics"]["platform_reported_purchases"] is not None
        ]
        known_impressions = [
            row["metrics"]["platform_reported_impressions"]
            for row in selected_summaries
            if row["metrics"]["platform_reported_impressions"] is not None
        ]
        known_clicks = [
            row["metrics"]["platform_reported_clicks"]
            for row in selected_summaries
            if row["metrics"]["platform_reported_clicks"] is not None
        ]
        combined_metrics = _metric_set(
            provider_reported_spend_sar=(
                sum(known_provider_spend) if known_provider_spend else None
            ),
            booked_ad_expense_sar=(
                sum(known_booked_expense) if known_booked_expense else None
            ),
            revenue_sar=sum(known_revenue) if known_revenue else None,
            purchases=sum(known_purchases) if known_purchases else None,
            impressions=sum(known_impressions) if known_impressions else None,
            clicks=sum(known_clicks) if known_clicks else None,
        )
        selected_count = len(selected_summaries)
        if (
            len(known_provider_spend) < selected_count
            or len(known_revenue) < selected_count
        ):
            combined_metrics["platform_roas"] = None
        if (
            len(known_provider_spend) < selected_count
            or len(known_purchases) < selected_count
        ):
            combined_metrics["platform_cpa_sar"] = None
        if (
            len(known_provider_spend) < selected_count
            or len(known_clicks) < selected_count
        ):
            combined_metrics["platform_cpc_sar"] = None
        if (
            len(known_provider_spend) < selected_count
            or len(known_impressions) < selected_count
        ):
            combined_metrics["platform_cpm_sar"] = None
        if (
            len(known_clicks) < selected_count
            or len(known_impressions) < selected_count
        ):
            combined_metrics["platform_ctr_pct"] = None

        daily_spend: list[dict] = []
        current = start
        while current <= end:
            date_iso = current.isoformat()
            point = {
                "date": date_iso,
                "snapchat": None,
                "tiktok": None,
                "meta": None,
                "booked_ad_expense_sar": None,
            }
            booked_values: list[float] = []
            for provider_key in PROVIDER_ORDER:
                if provider_key in selected_keys:
                    provider_source_key = (
                        "snapchat_account_daily"
                        if provider_key == "snapchat"
                        else f"{provider_key}_ads_daily"
                    )
                    if (
                        not source_limit_reached[provider_source_key]
                        and not source_invalid_date_rows[provider_source_key]
                    ):
                        point[provider_key] = provider_spend_evidence[
                            provider_key
                        ]["series"].get(date_iso)
                    if (
                        not booked_incomplete
                        and date_iso
                        in (booked_by_provider.get(provider_key) or {})
                    ):
                        booked_values.append(
                            _number(booked_by_provider[provider_key][date_iso])
                        )
            if booked_values:
                point["booked_ad_expense_sar"] = _round(sum(booked_values))
            daily_spend.append(point)
            current += timedelta(days=1)

        all_campaigns = [
            row
            for provider_key in ("tiktok", "meta")
            if provider_key in selected_keys
            for row in campaign_rows[provider_key]
        ]
        query = _clean_text(campaign_query, limit=120).casefold()
        if query:
            all_campaigns = [
                row
                for row in all_campaigns
                if query in str(row.get("campaign_name") or "").casefold()
                or query in str(row.get("campaign_id") or "").casefold()
            ]
        all_campaigns.sort(
            key=lambda row: (
                row.get("spend_sar_equivalent") is not None,
                row.get("spend_sar_equivalent")
                if row.get("spend_sar_equivalent") is not None
                else row.get("spend_reported") or 0,
            ),
            reverse=True,
        )
        total_campaigns = len(all_campaigns)
        pages = math.ceil(total_campaigns / limit) if total_campaigns else 0
        safe_page = min(page, pages) if pages else 1
        offset = (safe_page - 1) * limit
        paged_campaigns = [
            _public_campaign(row)
            for row in all_campaigns[offset : offset + limit]
        ]

        insights = self._insights(selected_summaries)
        return sanitize_for_output({
            "generated_at": now.isoformat(),
            "range": {
                "date_from": from_iso,
                "date_to": to_iso,
                "timezone": "Asia/Riyadh",
                "provider": provider_filter,
            },
            "metrics": combined_metrics,
            "coverage": {
                "revenue_is_partial": len(known_revenue) < len(selected_summaries),
                "provider_spend_is_partial": (
                    len(known_provider_spend) < len(selected_summaries)
                ),
                "booked_expense_is_partial": (
                    len(known_booked_expense) < len(selected_summaries)
                    or bool(booked_expense.get("unscoped_by_date"))
                    or booked_incomplete
                ),
                "providers_with_performance_data": sum(
                    row["metric_availability"]["revenue"]
                    or row["metric_availability"]["purchases"]
                    or row["metric_availability"]["clicks"]
                    for row in selected_summaries
                ),
                "providers_total": len(selected_summaries),
                "campaign_detail_providers": sum(
                    row["campaign_coverage"]["status"] == "available"
                    for row in selected_summaries
                ),
                "revenue_providers": len(known_revenue),
                "conversion_providers": len(known_purchases),
                "click_providers": len(known_clicks),
                "impression_providers": len(known_impressions),
                "provider_spend_providers": len(known_provider_spend),
                "booked_expense_providers": len(known_booked_expense),
                "unscoped_booked_expense_sar": (
                    _round(sum(
                        _number(value)
                        for value in (
                            booked_expense.get("unscoped_by_date") or {}
                        ).values()
                    ))
                    if booked_expense.get("unscoped_by_date")
                    and not booked_incomplete
                    else None
                ),
                "source_row_limit_reached": [
                    source
                    for source, reached in source_limit_reached.items()
                    if reached
                ]
                + (
                    ["general_ledger"]
                    if booked_expense.get("row_limit_reached")
                    else []
                ),
                "source_warnings": [
                    message
                    for condition, message in (
                        (
                            bool(booked_expense.get("invalid_rows_count")),
                            "توجد قيود مصروف إعلاني غير صالحة؛ أُخفي الإجمالي المحاسبي.",
                        ),
                        (
                            bool(
                                booked_expense.get(
                                    "account_mapping_limit_reached"
                                )
                            ),
                            "تجاوز دليل ربط الحسابات المحاسبية حد القراءة؛ عُطلت المطابقة.",
                        ),
                    )
                    if condition
                ]
                + [
                    (
                        f"تجاهل المصدر {source}: {count} صفًا بتاريخ غير صالح؛ "
                        "أُخفيت المقاييس المتأثرة."
                    )
                    for source, count in source_invalid_date_rows.items()
                    if count
                ],
            },
            "providers": selected_summaries,
            "daily_spend": daily_spend,
            "campaigns": paged_campaigns,
            "campaign_pagination": {
                "page": safe_page,
                "limit": limit,
                "total": total_campaigns,
                "pages": pages,
            },
            "insights": insights,
            "sources": SOURCE_DEFINITIONS,
            "policy": OBSERVE_ONLY_POLICY,
        })

    @staticmethod
    def _insights(providers: list[dict]) -> list[dict]:
        insights: list[dict] = []
        spenders = [
            row
            for row in providers
            if (row["metrics"]["provider_reported_spend_sar"] or 0) > 0
        ]
        if spenders:
            highest = max(
                spenders,
                key=lambda row: row["metrics"]["provider_reported_spend_sar"],
            )
            insights.append(
                {
                    "code": "highest_spend_provider",
                    "severity": "info",
                    "title": "أعلى منصة في الصرف",
                    "detail": (
                        f"{highest['provider_label']} هي الأعلى صرفًا ضمن الفترة المحددة."
                    ),
                    "confidence": "high",
                    "evidence": {
                        "provider": highest["provider"],
                        "provider_reported_spend_sar": (
                            highest["metrics"]["provider_reported_spend_sar"]
                        ),
                        "source": "provider_daily_facts",
                    },
                }
            )
        roas_rows = [
            row
            for row in providers
            if row["metrics"]["platform_roas"] is not None
            and (row["metrics"]["provider_reported_spend_sar"] or 0) > 0
        ]
        if roas_rows:
            best = max(roas_rows, key=lambda row: row["metrics"]["platform_roas"])
            insights.append(
                {
                    "code": "highest_observed_roas",
                    "severity": "info",
                    "title": "أعلى ROAS مرصود",
                    "detail": (
                        f"{best['provider_label']} لديها أعلى عائد منسوب للمنصة، "
                        "وهذا ليس حكمًا نهائيًا على الربح أو الإسناد."
                    ),
                    "confidence": "medium",
                    "evidence": {
                        "provider": best["provider"],
                        "roas": best["metrics"]["platform_roas"],
                        "attribution": "provider_reported",
                    },
                }
            )
        for row in providers:
            if row["freshness"]["status"] in {"delayed", "stale"}:
                insights.append(
                    {
                        "code": f"{row['provider']}_data_{row['freshness']['status']}",
                        "severity": "warning",
                        "title": f"بيانات {row['provider_label']} ليست حديثة",
                        "detail": "راجع حداثة المصدر قبل الاعتماد على مقارنة الفترة الحالية.",
                        "confidence": "high",
                        "evidence": {
                            "provider": row["provider"],
                            "delay_minutes": row["freshness"]["data_delay_minutes"],
                            "last_observed_at": row["freshness"]["last_observed_at"],
                        },
                    }
                )
            if row["reconciliation"]["status"] == "drift":
                insights.append(
                    {
                        "code": f"{row['provider']}_spend_drift",
                        "severity": "warning",
                        "title": f"فرق صرف يحتاج مراجعة في {row['provider_label']}",
                        "detail": row["reconciliation"]["detail"],
                        "confidence": "medium",
                        "evidence": {
                            "provider": row["provider"],
                            "gap_sar": row["reconciliation"]["gap_sar"],
                            "gap_pct": row["reconciliation"]["gap_pct"],
                        },
                    }
                )
            if (
                (row["metrics"]["provider_reported_spend_sar"] or 0) > 0
                and not any(
                    row["metric_availability"][key]
                    for key in ("revenue", "purchases", "clicks")
                )
            ):
                insights.append(
                    {
                        "code": f"{row['provider']}_performance_gap",
                        "severity": "warning",
                        "title": f"الصرف موجود دون أداء مكتمل في {row['provider_label']}",
                        "detail": (
                            "يمكن عرض الصرف المالي، لكن بيانات التحويل أو النقر غير "
                            "متاحة بما يكفي للتحليل."
                        ),
                        "confidence": "high",
                        "evidence": {
                            "provider": row["provider"],
                            "provider_reported_spend_sar": (
                                row["metrics"]["provider_reported_spend_sar"]
                            ),
                        },
                    }
                )
        return insights[:12]
