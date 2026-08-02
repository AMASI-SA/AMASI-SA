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
MAX_EXPECTED_CURRENT_DAY_DELAY_MINUTES = 180
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PROVIDER_ORDER = ("snapchat", "tiktok", "meta")
SNAPCHAT_V2_PERFORMANCE_COLLECTION = "mezan_snapchat_performance_daily_v2"
SNAPCHAT_LEGACY_ACCOUNT_COLLECTION = "snapchat_account_daily"
SNAPCHAT_LEGACY_STATS_COLLECTION = "snapchat_daily_stats"
SNAPCHAT_INTEGRATION_PROVIDER = "snapchat_ads"
TIKTOK_V2_PERFORMANCE_COLLECTION = "mezan_tiktok_performance_daily_v2"
TIKTOK_LEGACY_PERFORMANCE_COLLECTION = "tiktok_ads_daily"
TIKTOK_INTEGRATION_PROVIDER = "tiktok_ads"
META_V2_PERFORMANCE_COLLECTION = "mezan_meta_performance_daily_v2"
META_CAMPAIGN_V2_PERFORMANCE_COLLECTION = (
    "mezan_meta_campaign_performance_daily_v2"
)
META_LEGACY_PERFORMANCE_COLLECTION = "meta_ads_daily"
META_INTEGRATION_PROVIDER = "meta_ads"
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
        "key": SNAPCHAT_V2_PERFORMANCE_COLLECTION,
        "role": "أداء Snapchat الأصلي للحسابات والحملات المحددة داخل ميزان 2",
        "grain": "حساب أو حملة × يوم الرياض",
        "authoritative_for": [
            "snapchat_provider_reported_spend",
            "snapchat_provider_attribution",
            "snapchat_campaign_identity",
        ],
    },
    {
        "key": SNAPCHAT_LEGACY_ACCOUNT_COLLECTION,
        "role": "صرف حسابات Snapchat التاريخي الاحتياطي عند غياب تفعيل V2",
        "grain": "حساب إعلاني × يوم",
        "authoritative_for": [
            "snapchat_provider_reported_spend",
            "snapchat_provider_attribution",
        ],
    },
    {
        "key": SNAPCHAT_LEGACY_STATS_COLLECTION,
        "role": "أداء Snapchat التاريخي المجمع الاحتياطي عند غياب تفعيل V2",
        "grain": "يوم",
        "authoritative_for": ["snapchat_purchases", "snapchat_revenue"],
    },
    {
        "key": META_V2_PERFORMANCE_COLLECTION,
        "role": "أداء حسابات Meta الأصلي المحفوظ عبر Integrations V2",
        "grain": "حساب إعلاني محدد × يوم",
        "authoritative_for": [
            "meta_provider_reported_spend",
            "meta_impressions",
            "meta_clicks",
            "meta_platform_attribution",
        ],
    },
    {
        "key": META_CAMPAIGN_V2_PERFORMANCE_COLLECTION,
        "role": "تفاصيل حملات Meta الأصلية المحفوظة عبر Integrations V2",
        "grain": "حساب محدد × حملة × يوم",
        "authoritative_for": [
            "meta_campaign_identity",
            "meta_campaign_status",
            "meta_campaign_objective",
            "meta_campaign_performance",
        ],
    },
    {
        "key": META_LEGACY_PERFORMANCE_COLLECTION,
        "role": "مصدر Meta التاريخي الاحتياطي عند غياب مصدر V2",
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
        "key": TIKTOK_V2_PERFORMANCE_COLLECTION,
        "role": "أداء حسابات TikTok الأصلي المحفوظ عبر Integrations V2",
        "grain": "حساب إعلاني متصل × يوم",
        "authoritative_for": [
            "tiktok_provider_reported_spend",
            "tiktok_impressions",
            "tiktok_clicks",
            "tiktok_platform_attribution",
        ],
    },
    {
        "key": TIKTOK_LEGACY_PERFORMANCE_COLLECTION,
        "role": "تغذية TikTok التاريخية الاحتياطية عند غياب التفعيل الأصلي",
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


def _performance_coverage(
    *,
    performance_rows: list[dict],
    spend_series: dict[str, float | None],
    start: date,
    end: date,
    today: date,
    freshness: dict,
    source_truncated: bool,
    source_invalid: bool,
    spend_period_complete: bool,
    revenue_complete: bool,
    conversions_complete: bool,
    unverified_zero_performance: bool,
) -> dict:
    """Return a fail-closed eligibility verdict for performance ratios.

    Connection freshness is not enough: a recent spend sync must never make
    old or incomplete conversion facts look current.  Coverage therefore uses
    the performance rows themselves, checks every requested calendar day, and
    separately verifies that every positive-spend day has a performance row.

    The only tolerated requested-day gap is the still-open current day when
    the integration itself is within its documented delayed window and no
    positive spend has been recorded for that day yet.
    """

    requested_dates: set[str] = set()
    cursor = start
    while cursor <= end:
        requested_dates.add(cursor.isoformat())
        cursor += timedelta(days=1)

    observed_dates = _observed_dates(performance_rows)
    positive_spend_dates = {
        date_key
        for date_key, amount in spend_series.items()
        if amount is not None and amount > 0
    }
    missing_spend_dates = sorted(positive_spend_dates - observed_dates)
    missing_requested_dates = requested_dates - observed_dates

    delay = _optional_nonnegative_number(freshness.get("data_delay_minutes"))
    current_day_iso = today.isoformat()
    allow_current_day_lag = (
        end == today
        and missing_requested_dates == {current_day_iso}
        and current_day_iso not in positive_spend_dates
        and freshness.get("status") == "fresh"
        and delay is not None
        and delay <= MAX_EXPECTED_CURRENT_DAY_DELAY_MINUTES
    )
    uncovered_requested_dates = (
        missing_requested_dates - {current_day_iso}
        if allow_current_day_lag
        else missing_requested_dates
    )

    latest_performance_date: date | None = None
    if observed_dates:
        latest_performance_date = date.fromisoformat(max(observed_dates))
    stale = bool(
        latest_performance_date is not None
        and (end - latest_performance_date).days > 1
    )

    reasons: list[str] = []
    if not performance_rows:
        reasons.append("source_unavailable")
    if source_truncated:
        reasons.append("source_truncated")
    if source_invalid:
        reasons.append("invalid_source_dates")
    if not spend_period_complete:
        reasons.append("incomplete_spend")
    if uncovered_requested_dates or missing_spend_dates:
        reasons.append("missing_performance_dates")
    if stale:
        reasons.append("stale_performance")
    if not revenue_complete:
        reasons.append("incomplete_revenue")
    if not conversions_complete:
        reasons.append("incomplete_conversions")
    if unverified_zero_performance:
        reasons.append("unverified_zero_performance")

    if not performance_rows:
        status = "unavailable"
    elif stale:
        status = "stale"
    elif reasons:
        status = "partial"
    else:
        status = "complete"
    eligible_for_ratios = status == "complete"

    requested_days = len(requested_dates)
    coverage_pct = (
        round(len(observed_dates) / requested_days * 100, 2)
        if requested_days
        else None
    )
    if status == "complete" and allow_current_day_lag:
        detail = (
            "تغطية الأداء مؤهلة للنسب؛ يوم اليوم فقط ما زال ضمن نافذة "
            "التأخر المتوقعة ولا يوجد له صرف موجب مرصود."
        )
    elif status == "complete":
        detail = "تغطية الإيراد والتحويلات مكتملة وحديثة ضمن الفترة."
    elif status == "stale":
        detail = (
            "آخر صف أداء أقدم من نهاية الفترة؛ أُخفيت الإيرادات والتحويلات "
            "والنسب المشتقة."
        )
    elif status == "partial":
        detail = (
            "تغطية الأداء جزئية أو غير مؤكدة؛ أُخفيت الإيرادات والتحويلات "
            "والنسب المشتقة."
        )
    else:
        detail = (
            "لا توجد صفوف أداء موثوقة ضمن الفترة؛ لا يمكن حساب الإيراد "
            "أو التحويلات أو النسب."
        )
    return {
        "status": status,
        "eligible_for_ratios": eligible_for_ratios,
        "observed_days": len(observed_dates),
        "requested_days": requested_days,
        "coverage_pct": coverage_pct,
        "missing_spend_dates": missing_spend_dates,
        "reasons": reasons,
        "detail": detail,
    }


def _snapchat_account_performance_coverage(
    *,
    account_rows: list[dict],
    configured_accounts: list[dict],
    start: date,
    end: date,
    allow_current_day_lag: bool,
    source_truncated: bool,
    source_invalid: bool,
) -> list[dict]:
    """Explain Snapchat coverage independently for every enabled account."""
    requested_dates: list[str] = []
    cursor = start
    while cursor <= end:
        requested_dates.append(cursor.isoformat())
        cursor += timedelta(days=1)
    requested = set(requested_dates)

    rows_by_account: dict[str, list[dict]] = {}
    for row in account_rows:
        account_id = str(row.get("ad_account_id") or "").strip()
        if account_id:
            rows_by_account.setdefault(account_id, []).append(row)

    identities: dict[str, str] = {}
    for account in configured_accounts:
        if account.get("enabled") is not True:
            continue
        account_id = str(account.get("ad_account_id") or "").strip()
        if account_id:
            identities[account_id] = str(
                account.get("name") or account_id
            ).strip()
    if not configured_accounts:
        for account_id, rows in rows_by_account.items():
            identities[account_id] = str(
                rows[0].get("account_name") or account_id
            ).strip()

    def spend_value(row: dict) -> float | None:
        raw = row.get("spend_sar")
        if raw is None:
            raw = row.get("spend")
        return _optional_nonnegative_number(raw)

    def conversion_metric_known(
        row: dict,
        key: str,
        *,
        integer: bool,
    ) -> bool:
        parser = (
            _optional_nonnegative_integer
            if integer
            else _optional_nonnegative_number
        )
        value = parser(row.get(key))
        status = _clean_text(
            row.get("conversion_data_status"),
            limit=24,
        ).lower()
        if status in {"available", "partial"}:
            return value is not None
        if status:
            return False
        return value is not None and value > 0

    result: list[dict] = []
    for account_id, account_name in sorted(
        identities.items(), key=lambda item: (item[1].casefold(), item[0])
    ):
        rows = rows_by_account.get(account_id, [])
        rows_by_date = {
            row["date"]: row
            for row in rows
            if row.get("date") in requested
        }
        spend_dates = {
            date_key
            for date_key, row in rows_by_date.items()
            if spend_value(row) is not None
        }
        conversion_dates = {
            date_key
            for date_key, row in rows_by_date.items()
            if conversion_metric_known(row, "revenue_sar", integer=False)
            and conversion_metric_known(row, "purchases", integer=True)
        }
        missing_spend = sorted(requested - spend_dates)
        missing_conversions = sorted(requested - conversion_dates)
        known_spend_values = [
            value
            for row in rows_by_date.values()
            if (value := spend_value(row)) is not None
        ]
        spend_sar = (
            round(sum(known_spend_values), 2)
            if known_spend_values
            else None
        )
        current_day_iso = end.isoformat()
        account_current_day_lag_allowed = bool(
            allow_current_day_lag
            and rows_by_date
            and current_day_iso in requested
            and (
                current_day_iso in missing_spend
                or current_day_iso in missing_conversions
            )
            and set(missing_spend).issubset({current_day_iso})
            and set(missing_conversions).issubset({current_day_iso})
        )
        required_missing_spend = (
            set(missing_spend) - {current_day_iso}
            if account_current_day_lag_allowed
            else set(missing_spend)
        )
        required_missing_conversions = (
            set(missing_conversions) - {current_day_iso}
            if account_current_day_lag_allowed
            else set(missing_conversions)
        )
        if not rows_by_date:
            status = "unavailable"
            detail = "لا توجد صفوف يومية لهذا الحساب ضمن الفترة."
        elif source_truncated:
            status = "partial"
            detail = (
                "وصل مصدر الحسابات إلى حد القراءة؛ لا يمكن إثبات اكتمال "
                "هذا الحساب."
            )
        elif source_invalid:
            status = "partial"
            detail = (
                "توجد تواريخ غير صالحة في مصدر الحسابات؛ لا يمكن إثبات "
                "اكتمال هذا الحساب."
            )
        elif not required_missing_spend and not required_missing_conversions:
            status = "complete"
            detail = (
                "الصرف والتحويلات مكتملان، مع إبقاء اليوم الحالي ضمن "
                "نافذة التأخر المتوقعة."
                if account_current_day_lag_allowed
                else "الصرف والتحويلات مكتملان لكل أيام الفترة."
            )
        else:
            status = "partial"
            detail = (
                "تغطية الحساب جزئية؛ راجع الأيام الناقصة قبل استخدامه "
                "في ROAS أو المقارنة."
            )
        result.append(
            {
                "account_id": account_id,
                "account_name": account_name or account_id,
                "status": status,
                "spend_sar": spend_sar,
                "spend_days": len(spend_dates),
                "conversion_complete_days": len(conversion_dates),
                "requested_days": len(requested_dates),
                "missing_spend_dates": missing_spend,
                "missing_conversion_dates": missing_conversions,
                "current_day_lag_allowed": account_current_day_lag_allowed,
                "last_observed_date": max(rows_by_date) if rows_by_date else None,
                "detail": detail,
            }
        )
    return result


def _spend_period_complete(
    *,
    spend_series: dict[str, float | None],
    start: date,
    end: date,
    today: date,
    freshness: dict,
    source_truncated: bool,
    source_invalid: bool,
) -> bool:
    """Whether provider spend covers the requested period without guessing."""

    if source_truncated or source_invalid or not spend_series:
        return False
    requested_dates: set[str] = set()
    cursor = start
    while cursor <= end:
        requested_dates.add(cursor.isoformat())
        cursor += timedelta(days=1)
    known_dates = {
        date_key
        for date_key, amount in spend_series.items()
        if amount is not None
    }
    if any(amount is None for amount in spend_series.values()):
        return False
    missing_dates = requested_dates - known_dates
    if not missing_dates:
        return True

    current_day_iso = today.isoformat()
    delay = _optional_nonnegative_number(freshness.get("data_delay_minutes"))
    return bool(
        end == today
        and missing_dates == {current_day_iso}
        and current_day_iso not in spend_series
        and freshness.get("status") == "fresh"
        and delay is not None
        and delay <= MAX_EXPECTED_CURRENT_DAY_DELAY_MINUTES
    )


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


def _normalize_meta_v2_rows(rows: list[dict]) -> list[dict]:
    """Adapt native Meta V2 account-day facts to the Ads Manager read model.

    Native V2 is account-grain, not campaign-grain. The adapter preserves the
    provider-native currency and stored FX evidence, marks the row as aggregate
    only, and never mixes it with the historical ``meta_ads_daily`` source.
    """

    output: list[dict] = []
    for row in rows:
        account_id = _clean_text(row.get("ad_account_id"), limit=120)
        display_name = _clean_text(row.get("display_name"), limit=180)
        output.append(
            {
                "date": row.get("date"),
                "account_id": account_id or None,
                "campaign_id": "_default",
                "campaign_name": (
                    f"إجمالي {display_name}" if display_name else "إجمالي الحساب"
                ),
                "spend": row.get("spend_native"),
                "currency_native": row.get("currency_native"),
                "fx_rate": row.get("fx_rate_to_sar"),
                "purchases": row.get("purchases"),
                "purchase_value": row.get("purchase_value_native"),
                "impressions": row.get("impressions"),
                "clicks": row.get("clicks"),
                "updated_at": row.get("updated_at") or row.get("observed_at"),
                "_data_source": META_V2_PERFORMANCE_COLLECTION,
                "_aggregate_only": True,
            }
        )
    return output


def _normalize_meta_campaign_v2_rows(rows: list[dict]) -> list[dict]:
    output: list[dict] = []
    for row in rows:
        account_id = _clean_text(row.get("ad_account_id"), limit=120)
        campaign_id = _clean_text(row.get("campaign_id"), limit=160)
        if not campaign_id:
            continue
        output.append(
            {
                "date": row.get("date"),
                "account_id": account_id or None,
                "campaign_id": campaign_id,
                "campaign_name": (
                    _clean_text(row.get("campaign_name"), limit=180)
                    or campaign_id
                ),
                "status": row.get("status"),
                "delivery_status": row.get("effective_status"),
                "objective": row.get("objective"),
                "start_time": row.get("start_time"),
                "end_time": row.get("stop_time"),
                "daily_budget_native": row.get("daily_budget_native"),
                "lifetime_budget_native": row.get("lifetime_budget_native"),
                "spend": row.get("spend_native"),
                "currency_native": row.get("currency_native"),
                "fx_rate": row.get("fx_rate_to_sar"),
                "purchases": row.get("purchases"),
                "purchase_value": row.get("purchase_value_native"),
                "impressions": row.get("impressions"),
                "clicks": row.get("clicks"),
                "updated_at": row.get("updated_at") or row.get("observed_at"),
                "_data_source": META_CAMPAIGN_V2_PERFORMANCE_COLLECTION,
            }
        )
    return output


def _snapchat_metric(row: dict, key: str) -> Any:
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    return metrics.get(key)


def _normalize_snapchat_v2_rows(
    rows: list[dict],
    account_names: dict[str, str],
) -> list[dict]:
    """Adapt native Snapchat V2 facts without double-counting account rows.

    Snapchat persists provider-native values plus already-converted SAR values.
    The Ads Manager read model uses the stored SAR evidence directly so it does
    not need to guess an FX rate for historical rows.
    """

    output: list[dict] = []
    for row in rows:
        account_id = _clean_text(row.get("ad_account_id"), limit=120)
        entity_type = _clean_text(row.get("entity_type"), limit=40).lower()
        external_id = _clean_text(
            row.get("campaign_id") or row.get("external_id"),
            limit=160,
        )
        display_name = account_names.get(account_id) or account_id
        purchases = (
            row.get("purchases")
            if row.get("purchases") is not None
            else _snapchat_metric(row, "conversion_purchases")
        )
        revenue_sar = row.get("purchase_value_sar")
        normalized = {
            "date": row.get("date"),
            "account_id": account_id or None,
            "ad_account_id": account_id or None,
            "account_name": display_name or account_id or "حساب Snapchat",
            "campaign_id": (
                external_id if entity_type == "campaign" and external_id else "_default"
            ),
            "campaign_name": (
                f"حملة {external_id}"
                if entity_type == "campaign" and external_id
                else f"إجمالي {display_name or 'الحساب'}"
            ),
            # Normalize Snapchat campaign/account rows to stored SAR evidence.
            "spend": row.get("spend_sar"),
            "spend_sar": row.get("spend_sar"),
            "currency_native": "SAR",
            "fx_rate": 1.0,
            "purchases": purchases,
            "revenue": revenue_sar,
            "revenue_sar": revenue_sar,
            "impressions": _snapchat_metric(row, "impressions"),
            "clicks": _snapchat_metric(row, "swipes"),
            "updated_at": row.get("updated_at"),
            "conversion_data_status": (
                "available"
                if purchases is not None and revenue_sar is not None
                else "partial"
            ),
            "_data_source": SNAPCHAT_V2_PERFORMANCE_COLLECTION,
            "_aggregate_only": entity_type != "campaign",
            "_entity_type": entity_type,
        }
        output.append(normalized)
    return output


def _aggregate_snapchat_v2_daily(account_rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in account_rows:
        date_key = _clean_text(row.get("date"), limit=10)
        if date_key:
            grouped[date_key].append(row)

    output: list[dict] = []
    for date_key, rows in sorted(grouped.items()):
        def complete_sum(key: str, *, integer: bool = False):
            parser = (
                _optional_nonnegative_integer
                if integer
                else _optional_nonnegative_number
            )
            values = [parser(row.get(key)) for row in rows]
            if not values or any(value is None for value in values):
                return None
            total = sum(value for value in values if value is not None)
            return int(total) if integer else round(total, 2)

        complete_accounts = sum(
            row.get("purchases") is not None and row.get("revenue_sar") is not None
            for row in rows
        )
        markers = [
            _clean_text(row.get("updated_at"), limit=80)
            for row in rows
            if _clean_text(row.get("updated_at"), limit=80)
        ]
        output.append(
            {
                "date": date_key,
                "purchases": complete_sum("purchases", integer=True),
                "revenue": complete_sum("revenue_sar"),
                "impressions": complete_sum("impressions", integer=True),
                "clicks": complete_sum("clicks", integer=True),
                "conversion_data_status": (
                    "available" if complete_accounts == len(rows) else "partial"
                ),
                "conversion_accounts_total": len(rows),
                "conversion_accounts_complete": complete_accounts,
                "updated_at": max(markers) if markers else None,
                "_data_source": SNAPCHAT_V2_PERFORMANCE_COLLECTION,
            }
        )
    return output


def _normalize_tiktok_v2_rows(rows: list[dict]) -> list[dict]:
    """Adapt native TikTok account-day facts to the Ads Manager read model."""

    output: list[dict] = []
    for row in rows:
        account_id = _clean_text(row.get("ad_account_id"), limit=120)
        display_name = _clean_text(row.get("display_name"), limit=180)
        output.append(
            {
                "date": row.get("date"),
                "account_id": account_id or None,
                "advertiser_id": account_id or None,
                "campaign_id": "_default",
                "campaign_name": (
                    f"إجمالي {display_name}" if display_name else "إجمالي الحساب"
                ),
                "spend": row.get("spend_native"),
                "currency_native": row.get("currency_native"),
                "fx_rate": row.get("fx_rate_to_sar"),
                "conversions": row.get("conversions"),
                "impressions": row.get("impressions"),
                "clicks": row.get("clicks"),
                "updated_at": row.get("updated_at") or row.get("observed_at"),
                "_data_source": TIKTOK_V2_PERFORMANCE_COLLECTION,
                "_aggregate_only": True,
            }
        )
    return output


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
                "status": _clean_text(row.get("status"), limit=40) or None,
                "delivery_status": (
                    _clean_text(
                        row.get("delivery_status")
                        or row.get("effective_status"),
                        limit=40,
                    )
                    or None
                ),
                "objective": _clean_text(row.get("objective"), limit=80) or None,
                "start_time": _clean_text(row.get("start_time"), limit=80) or None,
                "end_time": (
                    _clean_text(row.get("end_time") or row.get("stop_time"), limit=80)
                    or None
                ),
                "_daily_budget_native": _optional_nonnegative_number(
                    row.get("daily_budget_native")
                ),
                "_lifetime_budget_native": _optional_nonnegative_number(
                    row.get("lifetime_budget_native")
                ),
                "_metadata_date": observed_date,
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
                    _clean_text(row.get("_data_source"), limit=80)
                    or (
                        META_LEGACY_PERFORMANCE_COLLECTION
                        if provider == "meta"
                        else "tiktok_ads_daily"
                    )
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
        if observed_date and (
            not target.get("_metadata_date")
            or observed_date >= target["_metadata_date"]
        ):
            target["status"] = (
                _clean_text(row.get("status"), limit=40) or target.get("status")
            )
            target["delivery_status"] = (
                _clean_text(
                    row.get("delivery_status") or row.get("effective_status"),
                    limit=40,
                )
                or target.get("delivery_status")
            )
            target["objective"] = (
                _clean_text(row.get("objective"), limit=80)
                or target.get("objective")
            )
            target["start_time"] = (
                _clean_text(row.get("start_time"), limit=80)
                or target.get("start_time")
            )
            target["end_time"] = (
                _clean_text(row.get("end_time") or row.get("stop_time"), limit=80)
                or target.get("end_time")
            )
            daily_budget = _optional_nonnegative_number(
                row.get("daily_budget_native")
            )
            lifetime_budget = _optional_nonnegative_number(
                row.get("lifetime_budget_native")
            )
            if daily_budget is not None:
                target["_daily_budget_native"] = daily_budget
            if lifetime_budget is not None:
                target["_lifetime_budget_native"] = lifetime_budget
            target["_metadata_date"] = observed_date

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
        value.pop("_metadata_date", None)
        daily_budget_native = value.pop("_daily_budget_native", None)
        lifetime_budget_native = value.pop("_lifetime_budget_native", None)
        currency = value["spend_currency"]
        value["budget"] = {
            "currency": currency,
            "daily_native": daily_budget_native,
            "lifetime_native": lifetime_budget_native,
        }
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
    totals_complete: bool,
    account_day_values_match: bool = True,
) -> dict:
    if provider_reported_spend_sar is None:
        return {
            "status": "no_data",
            "comparison_basis": "unavailable",
            "severity": "info",
            "action_required": False,
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
    if not totals_complete:
        return {
            "status": "not_comparable",
            "comparison_basis": "unavailable",
            "severity": "warning",
            "action_required": False,
            "provider_reported_spend_sar": _round(
                provider_reported_spend_sar
            ),
            "booked_ad_expense_sar": (
                None
                if booked_ad_expense_sar is None
                else _round(booked_ad_expense_sar)
            ),
            "gap_sar": None,
            "gap_pct": None,
            "detail": (
                "تغطية صرف الفترة أو القيود المحاسبية غير مكتملة؛ "
                "أُخفي الفرق حتى لا تُقارن فترات جزئية."
            ),
        }
    if booked_ad_expense_sar is None:
        return {
            "status": "not_comparable",
            "comparison_basis": "unavailable",
            "severity": "info",
            "action_required": False,
            "provider_reported_spend_sar": _round(provider_reported_spend_sar),
            "booked_ad_expense_sar": None,
            "gap_sar": None,
            "gap_pct": None,
            "detail": "لا توجد قيود محاسبية مُرحّلة قابلة للمقارنة ضمن الفترة.",
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
    material_gap = abs(gap) > threshold
    if not comparable:
        return {
            "status": "not_comparable",
            "comparison_basis": "aggregate_period_only",
            "severity": "warning" if material_gap else "info",
            "action_required": material_gap,
            "provider_reported_spend_sar": provider_value,
            "booked_ad_expense_sar": booked_value,
            "gap_sar": gap,
            "gap_pct": gap_pct,
            "detail": (
                "فرق إجمالي الفترة ظاهر للمراجعة، لكن تغطية الحساب واليوم "
                "غير متطابقة؛ لا يُعامل كتسوية محاسبية مؤكدة."
            ),
        }
    aggregate_within_tolerance = abs(gap) <= threshold
    status = (
        "matched"
        if aggregate_within_tolerance and account_day_values_match
        else "drift"
    )
    if not account_day_values_match and aggregate_within_tolerance:
        detail = (
            "تتعادل فروق الإجمالي، لكن قيمة حساب × يوم واحدة أو أكثر "
            "لا تطابق المصروف المحاسبي ضمن السماحية."
        )
    elif status == "matched":
        detail = "فرق المنصة مقابل المصروف المُرحّل ضمن سماحية 2% أو 1 ر.س."
    else:
        detail = "صرف المنصة لا يطابق المصروف المحاسبي المُرحّل ضمن السماحية."
    return {
        "status": status,
        "comparison_basis": "account_day_aligned",
        "severity": "none" if status == "matched" else "warning",
        "action_required": status == "drift",
        "provider_reported_spend_sar": provider_value,
        "booked_ad_expense_sar": booked_value,
        "gap_sar": gap,
        "gap_pct": gap_pct,
        "detail": detail,
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
        snap_legacy_account_task = _rows(
            self.db,
            SNAPCHAT_LEGACY_ACCOUNT_COLLECTION,
            date_query,
            {
                "_id": 0,
                "date": 1,
                "spend_sar": 1,
                "spend": 1,
                "updated_at": 1,
                "ad_account_id": 1,
                "account_name": 1,
                "purchases": 1,
                "revenue_sar": 1,
                "conversion_data_status": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("ad_account_id", 1)],
        )
        snap_legacy_accounts_task = _rows(
            self.db,
            "snapchat_ad_accounts",
            {"user_id": user_id},
            {
                "_id": 0,
                "ad_account_id": 1,
                "name": 1,
                "enabled": 1,
            },
            limit=MAX_ACCOUNTS,
            sort=[("enabled", -1), ("name", 1), ("ad_account_id", 1)],
        )
        snap_legacy_stats_task = _rows(
            self.db,
            SNAPCHAT_LEGACY_STATS_COLLECTION,
            date_query,
            {
                "_id": 0,
                "date": 1,
                "purchases": 1,
                "revenue": 1,
                "conversion_data_status": 1,
                "conversion_accounts_total": 1,
                "conversion_accounts_complete": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1)],
        )
        snap_v2_task = _rows(
            self.db,
            SNAPCHAT_V2_PERFORMANCE_COLLECTION,
            {**date_query, "provider": SNAPCHAT_INTEGRATION_PROVIDER},
            {
                "_id": 0,
                "date": 1,
                "ad_account_id": 1,
                "entity_type": 1,
                "external_id": 1,
                "campaign_id": 1,
                "currency": 1,
                "spend_native": 1,
                "spend_sar": 1,
                "purchases": 1,
                "purchase_value_native": 1,
                "purchase_value_sar": 1,
                "metrics": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("ad_account_id", 1), ("entity_type", 1)],
        )
        snap_selected_accounts_task = _rows(
            self.db,
            "mezan_integration_accounts_v2",
            {
                "user_id": user_id,
                "provider": SNAPCHAT_INTEGRATION_PROVIDER,
                "connection_provenance": "api_connection",
                "mezan_selected": True,
            },
            {
                "_id": 0,
                "external_account_id": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "currency": 1,
                "timezone": 1,
                "mezan_selected": 1,
            },
            limit=MAX_ACCOUNTS,
            sort=[("display_name", 1), ("external_account_id", 1)],
        )
        tiktok_legacy_task = _rows(
            self.db,
            TIKTOK_LEGACY_PERFORMANCE_COLLECTION,
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
        tiktok_v2_task = _rows(
            self.db,
            TIKTOK_V2_PERFORMANCE_COLLECTION,
            {**date_query, "provider": TIKTOK_INTEGRATION_PROVIDER},
            {
                "_id": 0,
                "date": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "currency_native": 1,
                "spend_native": 1,
                "spend_sar": 1,
                "fx_rate_to_sar": 1,
                "conversions": 1,
                "impressions": 1,
                "clicks": 1,
                "empty_provider_row": 1,
                "observed_at": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("ad_account_id", 1)],
        )
        tiktok_connected_accounts_task = _rows(
            self.db,
            "mezan_integration_accounts_v2",
            {
                "user_id": user_id,
                "provider": TIKTOK_INTEGRATION_PROVIDER,
                "connection_provenance": "api_connection",
                "connection_status": "connected",
            },
            {
                "_id": 0,
                "external_account_id": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "currency": 1,
                "timezone": 1,
            },
            limit=MAX_ACCOUNTS,
            sort=[("display_name", 1), ("external_account_id", 1)],
        )
        meta_v2_task = _rows(
            self.db,
            META_V2_PERFORMANCE_COLLECTION,
            {**date_query, "provider": META_INTEGRATION_PROVIDER},
            {
                "_id": 0,
                "date": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "spend_native": 1,
                "spend_sar": 1,
                "currency_native": 1,
                "fx_rate_to_sar": 1,
                "purchases": 1,
                "purchase_value_native": 1,
                "purchase_value_sar": 1,
                "impressions": 1,
                "clicks": 1,
                "empty_provider_row": 1,
                "observed_at": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("ad_account_id", 1)],
        )
        meta_campaign_v2_task = _rows(
            self.db,
            META_CAMPAIGN_V2_PERFORMANCE_COLLECTION,
            {**date_query, "provider": META_INTEGRATION_PROVIDER},
            {
                "_id": 0,
                "date": 1,
                "ad_account_id": 1,
                "campaign_id": 1,
                "campaign_name": 1,
                "objective": 1,
                "status": 1,
                "effective_status": 1,
                "start_time": 1,
                "stop_time": 1,
                "daily_budget_native": 1,
                "lifetime_budget_native": 1,
                "currency_native": 1,
                "spend_native": 1,
                "fx_rate_to_sar": 1,
                "purchases": 1,
                "purchase_value_native": 1,
                "impressions": 1,
                "clicks": 1,
                "observed_at": 1,
                "updated_at": 1,
            },
            limit=MAX_PERFORMANCE_ROWS,
            sort=[("date", 1), ("campaign_id", 1), ("ad_account_id", 1)],
        )
        meta_legacy_task = _rows(
            self.db,
            META_LEGACY_PERFORMANCE_COLLECTION,
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
        meta_selected_accounts_task = _rows(
            self.db,
            "mezan_integration_accounts_v2",
            {
                "user_id": user_id,
                "provider": META_INTEGRATION_PROVIDER,
                "connection_provenance": "api_connection",
                "mezan_selected": True,
            },
            {
                "_id": 0,
                "external_account_id": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "mezan_selected": 1,
            },
            limit=MAX_ACCOUNTS,
            sort=[("display_name", 1), ("external_account_id", 1)],
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
            snap_legacy_account_rows,
            snap_legacy_accounts,
            snap_legacy_stats_rows,
            snap_v2_rows,
            snap_selected_accounts,
            tiktok_legacy_rows,
            tiktok_v2_rows,
            tiktok_connected_accounts,
            meta_v2_rows,
            meta_campaign_v2_rows,
            meta_legacy_rows,
            meta_selected_accounts,
            accounts,
            legacy_accounts,
            currency_settings,
        ) = await asyncio.gather(
            integration_task,
            booked_expense_task,
            snap_legacy_account_task,
            snap_legacy_accounts_task,
            snap_legacy_stats_task,
            snap_v2_task,
            snap_selected_accounts_task,
            tiktok_legacy_task,
            tiktok_v2_task,
            tiktok_connected_accounts_task,
            meta_v2_task,
            meta_campaign_v2_task,
            meta_legacy_task,
            meta_selected_accounts_task,
            accounts_task,
            legacy_accounts_task,
            currency_settings_task,
        )

        snap_legacy_account_limit_reached = (
            len(snap_legacy_account_rows) > MAX_PERFORMANCE_ROWS
        )
        snap_legacy_accounts_limit_reached = len(snap_legacy_accounts) > MAX_ACCOUNTS
        snap_legacy_stats_limit_reached = (
            len(snap_legacy_stats_rows) > MAX_PERFORMANCE_ROWS
        )
        snap_v2_limit_reached = len(snap_v2_rows) > MAX_PERFORMANCE_ROWS
        snap_selection_limit_reached = len(snap_selected_accounts) > MAX_ACCOUNTS
        tiktok_legacy_limit_reached = len(tiktok_legacy_rows) > MAX_PERFORMANCE_ROWS
        tiktok_v2_limit_reached = len(tiktok_v2_rows) > MAX_PERFORMANCE_ROWS
        tiktok_accounts_limit_reached = len(tiktok_connected_accounts) > MAX_ACCOUNTS
        meta_v2_limit_reached = len(meta_v2_rows) > MAX_PERFORMANCE_ROWS
        meta_campaign_v2_limit_reached = (
            len(meta_campaign_v2_rows) > MAX_PERFORMANCE_ROWS
        )
        meta_legacy_limit_reached = len(meta_legacy_rows) > MAX_PERFORMANCE_ROWS
        meta_selection_limit_reached = len(meta_selected_accounts) > MAX_ACCOUNTS
        accounts_limit_reached = len(accounts) > MAX_ACCOUNTS
        legacy_accounts_limit_reached = len(legacy_accounts) > MAX_ACCOUNTS

        snap_legacy_account_rows = snap_legacy_account_rows[:MAX_PERFORMANCE_ROWS]
        snap_legacy_accounts = snap_legacy_accounts[:MAX_ACCOUNTS]
        snap_legacy_stats_rows = snap_legacy_stats_rows[:MAX_PERFORMANCE_ROWS]
        snap_v2_rows = snap_v2_rows[:MAX_PERFORMANCE_ROWS]
        snap_selected_accounts = snap_selected_accounts[:MAX_ACCOUNTS]
        tiktok_legacy_rows = tiktok_legacy_rows[:MAX_PERFORMANCE_ROWS]
        tiktok_v2_rows = tiktok_v2_rows[:MAX_PERFORMANCE_ROWS]
        tiktok_connected_accounts = tiktok_connected_accounts[:MAX_ACCOUNTS]
        meta_v2_rows = meta_v2_rows[:MAX_PERFORMANCE_ROWS]
        meta_campaign_v2_rows = meta_campaign_v2_rows[:MAX_PERFORMANCE_ROWS]
        meta_legacy_rows = meta_legacy_rows[:MAX_PERFORMANCE_ROWS]
        meta_selected_accounts = meta_selected_accounts[:MAX_ACCOUNTS]
        accounts = accounts[:MAX_ACCOUNTS]
        legacy_accounts = legacy_accounts[:MAX_ACCOUNTS]

        selected_snap_ids = {
            _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            )
            for row in snap_selected_accounts
            if _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            )
        }
        snap_v2_authoritative = bool(snap_v2_rows or snap_selected_accounts)
        if snap_v2_authoritative:
            if selected_snap_ids:
                snap_v2_rows = [
                    row
                    for row in snap_v2_rows
                    if _clean_text(row.get("ad_account_id"), limit=120)
                    in selected_snap_ids
                ]
            snap_account_names = {
                _clean_text(
                    row.get("external_account_id") or row.get("ad_account_id"),
                    limit=120,
                ): _clean_text(row.get("display_name"), limit=180)
                for row in snap_selected_accounts
                if _clean_text(
                    row.get("external_account_id") or row.get("ad_account_id"),
                    limit=120,
                )
            }
            snap_normalized_rows = _normalize_snapchat_v2_rows(
                snap_v2_rows,
                snap_account_names,
            )
            snap_account_rows = [
                row
                for row in snap_normalized_rows
                if row.get("_entity_type") == "ad_account"
            ]
            snap_campaign_rows = [
                row
                for row in snap_normalized_rows
                if row.get("_entity_type") == "campaign"
            ]
            snap_campaign_source_rows = snap_campaign_rows or list(snap_account_rows)
            snap_stats_rows = _aggregate_snapchat_v2_daily(snap_account_rows)
            snap_accounts = [
                {
                    "ad_account_id": _clean_text(
                        row.get("external_account_id") or row.get("ad_account_id"),
                        limit=120,
                    ),
                    "name": _clean_text(row.get("display_name"), limit=180),
                    "enabled": True,
                }
                for row in snap_selected_accounts
                if _clean_text(
                    row.get("external_account_id") or row.get("ad_account_id"),
                    limit=120,
                )
            ]
            snap_source_key = SNAPCHAT_V2_PERFORMANCE_COLLECTION
            snap_stats_source_key = SNAPCHAT_V2_PERFORMANCE_COLLECTION
            snap_account_config_source_key = (
                "mezan_integration_accounts_v2:snapchat_ads"
            )
            active_snap_limit_reached = (
                snap_v2_limit_reached or snap_selection_limit_reached
            )
            active_snap_stats_limit_reached = active_snap_limit_reached
            active_snap_account_config_limit_reached = snap_selection_limit_reached
        else:
            snap_account_rows = snap_legacy_account_rows
            snap_accounts = snap_legacy_accounts
            snap_stats_rows = snap_legacy_stats_rows
            snap_campaign_source_rows = []
            snap_normalized_rows = []
            snap_source_key = SNAPCHAT_LEGACY_ACCOUNT_COLLECTION
            snap_stats_source_key = SNAPCHAT_LEGACY_STATS_COLLECTION
            snap_account_config_source_key = "snapchat_ad_accounts"
            active_snap_limit_reached = snap_legacy_account_limit_reached
            active_snap_stats_limit_reached = snap_legacy_stats_limit_reached
            active_snap_account_config_limit_reached = (
                snap_legacy_accounts_limit_reached
            )

        connected_tiktok_ids = {
            _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            )
            for row in tiktok_connected_accounts
            if _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            )
        }
        tiktok_v2_authoritative = bool(tiktok_v2_rows or tiktok_connected_accounts)
        if tiktok_v2_authoritative:
            if connected_tiktok_ids:
                tiktok_v2_rows = [
                    row
                    for row in tiktok_v2_rows
                    if _clean_text(row.get("ad_account_id"), limit=120)
                    in connected_tiktok_ids
                ]
            tiktok_rows = _normalize_tiktok_v2_rows(tiktok_v2_rows)
            tiktok_source_key = TIKTOK_V2_PERFORMANCE_COLLECTION
            active_tiktok_limit_reached = (
                tiktok_v2_limit_reached or tiktok_accounts_limit_reached
            )
        else:
            tiktok_rows = tiktok_legacy_rows
            tiktok_source_key = TIKTOK_LEGACY_PERFORMANCE_COLLECTION
            active_tiktok_limit_reached = tiktok_legacy_limit_reached

        selected_meta_ids = {
            _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            ).removeprefix("act_")
            for row in meta_selected_accounts
            if _clean_text(
                row.get("external_account_id") or row.get("ad_account_id"),
                limit=120,
            )
        }
        meta_v2_authoritative = bool(meta_v2_rows or meta_selected_accounts)
        if meta_v2_authoritative:
            meta_v2_rows = [
                row
                for row in meta_v2_rows
                if _clean_text(row.get("ad_account_id"), limit=120)
                .removeprefix("act_")
                in selected_meta_ids
            ]
            meta_campaign_v2_rows = [
                row
                for row in meta_campaign_v2_rows
                if _clean_text(row.get("ad_account_id"), limit=120)
                .removeprefix("act_")
                in selected_meta_ids
            ]
            meta_rows = _normalize_meta_v2_rows(meta_v2_rows)
            normalized_meta_campaign_rows = _normalize_meta_campaign_v2_rows(
                meta_campaign_v2_rows
            )
            meta_campaign_source_rows = (
                normalized_meta_campaign_rows or list(meta_rows)
            )
            meta_source_key = META_V2_PERFORMANCE_COLLECTION
            meta_campaign_source_key = META_CAMPAIGN_V2_PERFORMANCE_COLLECTION
            active_meta_limit_reached = (
                meta_v2_limit_reached or meta_selection_limit_reached
            )
            active_meta_campaign_limit_reached = (
                meta_campaign_v2_limit_reached or meta_selection_limit_reached
            )
        else:
            meta_rows = meta_legacy_rows
            meta_campaign_source_rows = meta_legacy_rows
            meta_source_key = META_LEGACY_PERFORMANCE_COLLECTION
            meta_campaign_source_key = META_LEGACY_PERFORMANCE_COLLECTION
            active_meta_limit_reached = meta_legacy_limit_reached
            active_meta_campaign_limit_reached = meta_legacy_limit_reached

        source_limit_reached = {
            snap_source_key: active_snap_limit_reached,
            snap_stats_source_key: active_snap_stats_limit_reached,
            snap_account_config_source_key: active_snap_account_config_limit_reached,
            tiktok_source_key: active_tiktok_limit_reached,
            meta_source_key: active_meta_limit_reached,
            meta_campaign_source_key: active_meta_campaign_limit_reached,
            "ads_accounts": accounts_limit_reached,
            "counterparties": legacy_accounts_limit_reached,
        }
        dated_sources = {
            tiktok_source_key: tiktok_rows,
            meta_source_key: meta_rows,
            meta_campaign_source_key: meta_campaign_source_rows,
        }
        if snap_v2_authoritative:
            dated_sources[snap_source_key] = snap_normalized_rows
        else:
            dated_sources[snap_source_key] = snap_account_rows
            dated_sources[snap_stats_source_key] = snap_stats_rows
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
        snap_campaign_source_rows = [
            row
            for row in snap_campaign_source_rows
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
        meta_campaign_source_rows = [
            row
            for row in meta_campaign_source_rows
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
            "snapchat": _campaign_rows(
                "snapchat",
                snap_campaign_source_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
            "tiktok": _campaign_rows(
                "tiktok",
                tiktok_rows,
                exact_accounts=exact_accounts,
                provider_accounts=provider_accounts,
            ),
            "meta": _campaign_rows(
                "meta",
                meta_campaign_source_rows,
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
        booked_totals_complete = not (
            booked_incomplete
            or booked_expense.get("unscoped_by_date")
            or booked_expense.get("account_mapping_limit_reached")
        )
        integration_cards = {
            row.get("provider"): row
            for row in integration_overview.get("providers") or []
        }

        provider_source_keys = {
            "snapchat": snap_source_key,
            "tiktok": tiktok_source_key,
            "meta": meta_source_key,
        }
        provider_summaries: list[dict] = []
        snap_account_coverage: list[dict] = []
        for provider_key in PROVIDER_ORDER:
            definition = PROVIDER_DEFINITIONS[provider_key]
            spend_evidence = provider_spend_evidence[provider_key]
            spend_series = spend_evidence["series"]
            provider_source_key = provider_source_keys[provider_key]
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
            account_day_values_match = bool(comparable) and all(
                abs(
                    _number(provider_account_date[pair])
                    - _number(booked_account_date[pair])
                )
                <= max(1.0, abs(_number(provider_account_date[pair])) * 0.02)
                for pair in provider_account_date
            )
            card = integration_cards.get(definition["integration_provider"]) or {}
            rows_for_freshness = (
                snap_stats_rows + snap_account_rows
                if provider_key == "snapchat"
                else raw_rows[provider_key]
            )
            latest_observed = _latest_marker(rows_for_freshness)
            freshness = _freshness(
                integration_delay=card.get("data_delay_minutes"),
                latest_observed_at=latest_observed,
                observed_days=len(_observed_dates(rows_for_freshness)),
                requested_days=requested_days,
                now=now,
            )
            spend_period_complete = _spend_period_complete(
                spend_series=spend_series,
                start=start,
                end=end,
                today=today,
                freshness=freshness,
                source_truncated=provider_source_truncated,
                source_invalid=provider_source_invalid,
            )

            if provider_key == "snapchat":
                current_day_iso = today.isoformat()
                freshness_delay = _optional_nonnegative_number(
                    freshness.get("data_delay_minutes")
                )
                allow_current_day_lag = bool(
                    spend_series
                    and end == today
                    and current_day_iso not in spend_series
                    and current_day_iso not in _observed_dates(snap_stats_rows)
                    and freshness.get("status") == "fresh"
                    and freshness_delay is not None
                    and freshness_delay
                    <= MAX_EXPECTED_CURRENT_DAY_DELAY_MINUTES
                )
                snap_account_coverage = (
                    _snapchat_account_performance_coverage(
                        account_rows=snap_account_rows,
                        configured_accounts=snap_accounts,
                        start=start,
                        end=end,
                        allow_current_day_lag=allow_current_day_lag,
                        source_truncated=bool(
                            provider_source_truncated
                            or source_limit_reached[snap_account_config_source_key]
                        ),
                        source_invalid=provider_source_invalid,
                    )
                )
                performance_rows = snap_stats_rows
                performance_source_truncated = source_limit_reached[
                    snap_stats_source_key
                ]
                performance_source_invalid = bool(
                    source_invalid_date_rows[snap_stats_source_key]
                )
                snap_revenue_values = [
                    _optional_nonnegative_number(row.get("revenue"))
                    for row in snap_stats_rows
                ]
                snap_purchase_values = [
                    _optional_nonnegative_integer(row.get("purchases"))
                    for row in snap_stats_rows
                ]
                revenue_complete = bool(snap_stats_rows) and all(
                    value is not None for value in snap_revenue_values
                )
                conversions_complete = bool(snap_stats_rows) and all(
                    value is not None for value in snap_purchase_values
                )
                unverified_zero_performance = False
                for row, revenue_value, purchase_value in zip(
                    snap_stats_rows,
                    snap_revenue_values,
                    snap_purchase_values,
                ):
                    status = _clean_text(
                        row.get("conversion_data_status"),
                        limit=24,
                    ).lower()
                    if status in {"partial", "unavailable"}:
                        revenue_complete = False
                        conversions_complete = False
                    elif status and status != "available":
                        revenue_complete = False
                        conversions_complete = False
                    elif not status:
                        # The historical failed path fabricated zero. Keep
                        # legacy positive facts for compatibility, but treat
                        # every unmarked zero independently as unknown.
                        if revenue_value is None or revenue_value <= 0:
                            revenue_complete = False
                            if revenue_value == 0:
                                unverified_zero_performance = True
                        if purchase_value is None or purchase_value <= 0:
                            conversions_complete = False
                            if purchase_value == 0:
                                unverified_zero_performance = True
                    accounts_total = _optional_nonnegative_integer(
                        row.get("conversion_accounts_total")
                    )
                    accounts_complete = _optional_nonnegative_integer(
                        row.get("conversion_accounts_complete")
                    )
                    if (
                        accounts_total is not None
                        and accounts_complete is not None
                        and accounts_complete < accounts_total
                    ):
                        revenue_complete = False
                        conversions_complete = False
                candidate_revenue_sar = (
                    sum(value for value in snap_revenue_values if value is not None)
                    if revenue_complete
                    else None
                )
                candidate_purchases = (
                    sum(value for value in snap_purchase_values if value is not None)
                    if conversions_complete
                    else None
                )
                unverified_zero_performance = bool(
                    provider_reported_spend_sar
                    and provider_reported_spend_sar > 0
                    and unverified_zero_performance
                )
                snap_impression_values = [
                    _optional_nonnegative_integer(row.get("impressions"))
                    for row in snap_stats_rows
                ]
                snap_click_values = [
                    _optional_nonnegative_integer(row.get("clicks"))
                    for row in snap_stats_rows
                ]
                candidate_impressions = (
                    sum(value for value in snap_impression_values if value is not None)
                    if snap_stats_rows
                    and all(value is not None for value in snap_impression_values)
                    else None
                )
                candidate_clicks = (
                    sum(value for value in snap_click_values if value is not None)
                    if snap_stats_rows
                    and all(value is not None for value in snap_click_values)
                    else None
                )
                campaign_source_rows = snap_campaign_source_rows
            else:
                grouped = campaign_rows[provider_key]
                performance_rows = raw_rows[provider_key]
                performance_source_truncated = provider_source_truncated
                performance_source_invalid = provider_source_invalid
                revenue_values = [
                    row.get("revenue_sar_equivalent")
                    for row in grouped
                ]
                revenue_complete = bool(grouped) and all(
                    value is not None for value in revenue_values
                )
                conversions_complete = bool(grouped) and all(
                    row.get("purchases") is not None for row in grouped
                )
                candidate_revenue_sar = (
                    sum(value for value in revenue_values if value is not None)
                    if revenue_complete
                    else None
                )
                candidate_purchases = (
                    sum(int(row["purchases"]) for row in grouped)
                    if conversions_complete
                    else None
                )
                candidate_impressions = (
                    sum(int(row["impressions"]) for row in grouped)
                    if grouped
                    and all(row.get("impressions") is not None for row in grouped)
                    else None
                )
                candidate_clicks = (
                    sum(int(row["clicks"]) for row in grouped)
                    if grouped
                    and all(row.get("clicks") is not None for row in grouped)
                    else None
                )
                unverified_zero_performance = False
                campaign_source_rows = (
                    meta_campaign_source_rows
                    if provider_key == "meta"
                    else raw_rows[provider_key]
                )

            # The open-current-day lag exception is valid only when both
            # sides of the ratio omit that still-open day. If performance
            # already contains today's facts while spend does not, their
            # date coverage differs and the ratio must fail closed.
            current_day_iso = today.isoformat()
            if (
                end == today
                and current_day_iso in _observed_dates(performance_rows)
                and current_day_iso not in spend_series
            ):
                spend_period_complete = False

            performance_coverage = _performance_coverage(
                performance_rows=performance_rows,
                spend_series=spend_series,
                start=start,
                end=end,
                today=today,
                freshness=freshness,
                source_truncated=performance_source_truncated,
                source_invalid=performance_source_invalid,
                spend_period_complete=spend_period_complete,
                revenue_complete=revenue_complete,
                conversions_complete=conversions_complete,
                unverified_zero_performance=unverified_zero_performance,
            )
            performance_eligible = performance_coverage[
                "eligible_for_ratios"
            ]
            fatal_performance_reasons = {
                "source_unavailable",
                "source_truncated",
                "invalid_source_dates",
                "incomplete_spend",
                "missing_performance_dates",
                "stale_performance",
                "unverified_zero_performance",
            }
            performance_facts_usable = bool(performance_rows) and not (
                fatal_performance_reasons
                & set(performance_coverage.get("reasons") or [])
            )
            revenue_sar = (
                candidate_revenue_sar
                if performance_facts_usable and revenue_complete
                else None
            )
            purchases = (
                candidate_purchases
                if performance_facts_usable and conversions_complete
                else None
            )
            impressions = (
                candidate_impressions if performance_facts_usable else None
            )
            clicks = candidate_clicks if performance_facts_usable else None
            if not performance_eligible and provider_key in campaign_rows:
                for campaign in campaign_rows[provider_key]:
                    for ratio_key in (
                        "roas",
                        "cpa_reported",
                        "cpc_reported",
                        "cpm_reported",
                        "ctr_pct",
                    ):
                        campaign[ratio_key] = None

            metrics = _metric_set(
                provider_reported_spend_sar=provider_reported_spend_sar,
                booked_ad_expense_sar=booked_ad_expense_sar,
                revenue_sar=revenue_sar,
                purchases=purchases,
                impressions=impressions,
                clicks=clicks,
            )
            if not performance_eligible:
                for ratio_key in (
                    "platform_roas",
                    "platform_cpa_sar",
                    "platform_cpc_sar",
                    "platform_cpm_sar",
                    "platform_ctr_pct",
                ):
                    metrics[ratio_key] = None
            campaign_coverage = _campaign_coverage(
                provider_key,
                campaign_source_rows,
            )
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
                    "freshness": freshness,
                    "performance_coverage": performance_coverage,
                    "account_performance_coverage": (
                        snap_account_coverage
                        if provider_key == "snapchat"
                        else []
                    ),
                    "campaign_coverage": campaign_coverage,
                    "reconciliation": _reconciliation(
                        provider_reported_spend_sar,
                        booked_ad_expense_sar,
                        comparable=comparable,
                        account_day_values_match=account_day_values_match,
                        totals_complete=(
                            spend_period_complete
                            and booked_totals_complete
                        ),
                    ),
                    "metric_availability": {
                        "provider_spend": provider_reported_spend_sar is not None,
                        "provider_spend_period_complete": (
                            spend_period_complete
                        ),
                        "booked_expense": booked_ad_expense_sar is not None,
                        "revenue": revenue_sar is not None,
                        "purchases": purchases is not None,
                        "impressions": impressions is not None,
                        "clicks": clicks is not None,
                        "campaigns": campaign_coverage["status"] == "available",
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
                    provider_source_key = provider_source_keys[provider_key]
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
            for provider_key in ("snapchat", "tiktok", "meta")
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
                    or any(
                        not row["metric_availability"][
                            "provider_spend_period_complete"
                        ]
                        for row in selected_summaries
                    )
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
                "ratio_eligible_providers": sum(
                    row["performance_coverage"]["eligible_for_ratios"]
                    for row in selected_summaries
                ),
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
            and row["metric_availability"][
                "provider_spend_period_complete"
            ]
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
            if row["reconciliation"]["severity"] == "warning":
                insights.append(
                    {
                        "code": (
                            f"{row['provider']}_spend_drift"
                            if row["reconciliation"]["status"] == "drift"
                            else f"{row['provider']}_spend_gap"
                        ),
                        "severity": "warning",
                        "title": f"فرق صرف يحتاج مراجعة في {row['provider_label']}",
                        "detail": row["reconciliation"]["detail"],
                        "confidence": (
                            "high"
                            if row["reconciliation"]["comparison_basis"]
                            == "account_day_aligned"
                            else "medium"
                        ),
                        "evidence": {
                            "provider": row["provider"],
                            "gap_sar": row["reconciliation"]["gap_sar"],
                            "gap_pct": row["reconciliation"]["gap_pct"],
                            "comparison_basis": row["reconciliation"][
                                "comparison_basis"
                            ],
                            "action_required": row["reconciliation"][
                                "action_required"
                            ],
                        },
                    }
                )
            if (
                (row["metrics"]["provider_reported_spend_sar"] or 0) > 0
                and not row["performance_coverage"]["eligible_for_ratios"]
            ):
                insights.append(
                    {
                        "code": (
                            f"{row['provider']}_performance_"
                            f"{row['performance_coverage']['status']}"
                        ),
                        "severity": "warning",
                        "title": f"الصرف موجود دون أداء مكتمل في {row['provider_label']}",
                        "detail": row["performance_coverage"]["detail"],
                        "confidence": "high",
                        "evidence": {
                            "provider": row["provider"],
                            "provider_reported_spend_sar": (
                                row["metrics"]["provider_reported_spend_sar"]
                            ),
                            "coverage_status": row["performance_coverage"][
                                "status"
                            ],
                            "coverage_pct": row["performance_coverage"][
                                "coverage_pct"
                            ],
                            "reasons": row["performance_coverage"]["reasons"],
                        },
                    }
                )
        return insights[:12]
