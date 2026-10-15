"""Expose Snapchat configured status and real delivery state separately.

``status`` is the configured ACTIVE/PAUSED switch. ``delivery_status`` explains
whether the entity can deliver now. An ACTIVE campaign can therefore be not
delivering because of account payment, daily budget, or paused Ad Squads.
Provider access is read-only; spend and accounting paths are unchanged.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Awaitable, Callable

import httpx

from . import snapchat_account_hourly_refresh as hourly
from . import snapchat_account_timezone_manager as timezone_manager
from .snapchat_native_data_common import (
    SNAPCHAT_API_BASE,
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _collection,
)

ACCOUNT_DELIVERY_SOURCE_MODE = "snapchat_delivery_truth_5m_v2"
PAYMENT_BLOCK_CODES = frozenset({
    "INVALID_REMAINING_AD_ACCOUNT_BUDGET",
    "INVALID_AD_ACCOUNT_LIFETIME_SPEND_CAP",
    "INVALID_OVER_BUDGET_AD_ACCOUNT_FINALIZED_LIFETIME_SPEND",
    "INVALID_OVER_BUDGET_AD_ACCOUNT_REALTIME_LIFETIME_SPEND",
})
POSITIVE_CODES = frozenset({"DELIVERING", "VALID", "LEARNING PHASE", "LEARNING_PHASE"})
ACTIVE_ACCOUNT = frozenset({"ACTIVE", "ENABLED"})
ACTIVE_CAMPAIGN = frozenset({"ACTIVE", "ENABLED"})
ACTIVE_SQUAD = frozenset({"ACTIVE", "ENABLED"})

CAMPAIGN_REASONS = {
    "INVALID_OVER_BUDGET_CAMPAIGN_DAILY_SPEND": (
        "CAMPAIGN_DAILY_BUDGET_EXHAUSTED",
        "لا تسليم — خارج الميزانية اليومية",
        "بلغت الحملة حد الإنفاق اليومي في Snapchat.",
    ),
    "INVALID_CAMPAIGN_LIFETIME_SPEND_CAP": (
        "CAMPAIGN_LIFETIME_BUDGET_BLOCKED",
        "لا تسليم — ميزانية الحملة غير صالحة",
        "حد الإنفاق الكلي للحملة غير صالح.",
    ),
    "INVALID_OVER_BUDGET_CAMPAIGN_FINALIZED_LIFETIME_SPEND": (
        "CAMPAIGN_LIFETIME_BUDGET_EXHAUSTED",
        "لا تسليم — تم بلوغ ميزانية الحملة",
        "تجاوز الإنفاق النهائي حد الحملة الكلي.",
    ),
    "INVALID_OVER_BUDGET_CAMPAIGN_REALTIME_LIFETIME_SPEND": (
        "CAMPAIGN_LIFETIME_BUDGET_EXHAUSTED",
        "لا تسليم — تم بلوغ ميزانية الحملة",
        "تجاوز الإنفاق الحالي حد الحملة الكلي.",
    ),
    "NO_VALID_AD_SQUAD": (
        "NO_VALID_AD_SQUAD",
        "لا تسليم — لا توجد مجموعة إعلانية صالحة",
        "لا توجد مجموعة إعلانية صالحة للتسليم داخل الحملة.",
    ),
    "INVALID_CAMPAIGN_HAS_NO_ACTIVE_AD_SQUAD": (
        "NO_ACTIVE_AD_SQUAD",
        "لا تسليم — لا توجد مجموعة إعلانية نشطة",
        "الحملة مفعلة، لكن جميع المجموعات الإعلانية متوقفة.",
    ),
}

SQUAD_REASONS = {
    "INVALID_OVER_BUDGET_AD_SQUAD_DAILY_SPEND": (
        "AD_SQUAD_DAILY_BUDGET_EXHAUSTED",
        "لا تسليم — خارج الميزانية اليومية",
        "بلغت المجموعات الإعلانية النشطة حد الإنفاق اليومي.",
    ),
    "INVALID_AD_SQUAD_DAILY_SPEND_CAP": (
        "AD_SQUAD_DAILY_BUDGET_INVALID",
        "لا تسليم — ميزانية المجموعة اليومية غير صالحة",
        "حد الإنفاق اليومي للمجموعة الإعلانية غير صالح.",
    ),
    "INVALID_AD_SQUAD_HAS_NO_ACTIVE_ADS": (
        "NO_ACTIVE_AD",
        "لا تسليم — لا يوجد إعلان نشط",
        "المجموعة الإعلانية لا تحتوي إعلانًا نشطًا.",
    ),
    "NOT_DELIVERING_AD_CONTAINS_INVALID_AUDIENCE": (
        "AD_SQUAD_AUDIENCE_INVALID",
        "لا تسليم — الجمهور غير صالح",
        "تحتوي المجموعة على جمهور محذوف أو غير متاح.",
    ),
}

AccountRefresh = Callable[..., Awaitable[dict[str, Any]]]
ReportBuilder = Callable[..., Awaitable[dict[str, Any]]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_delivery_status(value: Any) -> list[str]:
    output: list[str] = []

    def add(item: Any) -> None:
        if isinstance(item, dict):
            for key in ("code", "status", "delivery_status", "reason"):
                if key in item:
                    add(item.get(key))
            return
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                add(nested)
            return
        for token in _text(item).replace(";", ",").split(","):
            code = token.strip().upper()
            if code and code not in output:
                output.append(code)

    add(value)
    return output


def _unwrap_ad_account(payload: dict[str, Any], account_id: str) -> dict[str, Any]:
    if isinstance(payload.get("adaccount"), dict):
        return payload["adaccount"]
    for wrapped in payload.get("adaccounts") or []:
        if not isinstance(wrapped, dict):
            continue
        if "FAIL" in _text(wrapped.get("sub_request_status")).upper():
            continue
        account = wrapped.get("adaccount", wrapped)
        if isinstance(account, dict) and _text(account.get("id")) in {"", account_id}:
            return account
    return payload if _text(payload.get("id")) == account_id else {}


def _state(state: str, code: str, label: str, detail: str | None, **extra: Any) -> dict[str, Any]:
    return {
        "state": state,
        "code": code,
        "label": label,
        "detail": detail,
        "deliverable": state == "DELIVERING",
        **extra,
    }


def account_delivery_block(account_status: Any, delivery_status: Any) -> dict[str, Any] | None:
    status = _text(account_status).upper()
    codes = normalize_delivery_status(delivery_status)
    payment = next((code for code in codes if code in PAYMENT_BLOCK_CODES), None)
    if payment:
        detail = {
            "INVALID_REMAINING_AD_ACCOUNT_BUDGET": "رصيد الحساب أو وسيلة الدفع لا تسمح بالتسليم.",
            "INVALID_AD_ACCOUNT_LIFETIME_SPEND_CAP": "حد الإنفاق الكلي للحساب يمنع التسليم.",
        }.get(payment, "تجاوز الحساب حد الإنفاق المسموح.")
        return {
            "code": "ACCOUNT_PAYMENT_BLOCKED",
            "delivery_label": "لا تسليم — الحساب موقوف بسبب الدفع أو الرصيد",
            "detail": detail,
            "provider_codes": codes,
        }
    if status and status not in ACTIVE_ACCOUNT:
        return {
            "code": "ACCOUNT_NOT_ACTIVE",
            "delivery_label": "لا تسليم — الحساب الإعلاني غير نشط",
            "detail": f"حالة حساب Snapchat الحالية: {status}",
            "provider_codes": codes,
        }
    blocking = next(
        (
            code for code in codes
            if code not in POSITIVE_CODES
            and code != "PENDING"
            and (code.startswith("INVALID_") or code.startswith("NOT_") or code == "TEST_AD_ACCOUNT")
        ),
        None,
    )
    if blocking:
        return {
            "code": "ACCOUNT_DELIVERY_BLOCKED",
            "delivery_label": "لا تسليم — الحساب الإعلاني يمنع التسليم",
            "detail": blocking,
            "provider_codes": codes,
        }
    return None


def _mapped_reason(codes: list[str], mapping: dict[str, tuple[str, str, str]]) -> dict[str, Any] | None:
    for provider_code in codes:
        if provider_code in mapping:
            code, label, detail = mapping[provider_code]
            return _state("NOT_DELIVERING", code, label, detail, provider_code=provider_code)
    generic = next(
        (code for code in codes if code.startswith("INVALID_") or code.startswith("NOT_")),
        None,
    )
    return (
        _state(
            "NOT_DELIVERING",
            "PROVIDER_DELIVERY_BLOCKED",
            "لا تسليم — يوجد مانع من Snapchat",
            generic,
            provider_code=generic,
        )
        if generic
        else None
    )


def campaign_delivery_state(
    configured_status: Any,
    campaign_delivery_status: Any,
    *,
    account_block: dict[str, Any] | None,
    ad_squads: list[dict[str, Any]],
) -> dict[str, Any]:
    configured = _text(configured_status).upper() or "UNKNOWN"
    campaign_codes = normalize_delivery_status(campaign_delivery_status)
    active_squads = [
        row for row in ad_squads
        if _text(row.get("status")).upper() in ACTIVE_SQUAD
    ]
    summary = {
        "campaign_provider_codes": campaign_codes,
        "ad_squad_total": len(ad_squads),
        "ad_squad_active": len(active_squads),
    }

    if configured not in ACTIVE_CAMPAIGN:
        return _state(
            "NOT_DELIVERING",
            "CAMPAIGN_NOT_ACTIVE",
            "غير نشط",
            "الحملة متوقفة من مفتاح الحالة داخل Snapchat.",
            **summary,
        )
    if account_block:
        return _state(
            "NOT_DELIVERING",
            _text(account_block.get("code")) or "ACCOUNT_DELIVERY_BLOCKED",
            _text(account_block.get("delivery_label")) or "لا تسليم — الحساب يمنع التسليم",
            _text(account_block.get("detail")),
            account_provider_codes=list(account_block.get("provider_codes") or []),
            **summary,
        )

    reason = _mapped_reason(campaign_codes, CAMPAIGN_REASONS)
    if reason:
        return {**reason, **summary}
    if ad_squads and not active_squads:
        return _state(
            "NOT_DELIVERING",
            "NO_ACTIVE_AD_SQUAD",
            "لا تسليم — لا توجد مجموعة إعلانية نشطة",
            "الحملة مفعلة، لكن جميع المجموعات الإعلانية متوقفة.",
            **summary,
        )

    squad_codes = [normalize_delivery_status(row.get("delivery_status")) for row in active_squads]
    if any(any(code in POSITIVE_CODES for code in codes) for codes in squad_codes):
        learning = any(
            any(code in {"LEARNING PHASE", "LEARNING_PHASE"} for code in codes)
            for codes in squad_codes
        )
        return _state(
            "DELIVERING",
            "DELIVERING",
            "يتم التسليم",
            "قد تكون في مرحلة التعلم" if learning else None,
            ad_squad_provider_codes=squad_codes,
            **summary,
        )
    for codes in squad_codes:
        reason = _mapped_reason(codes, SQUAD_REASONS)
        if reason:
            return {**reason, "ad_squad_provider_codes": squad_codes, **summary}

    if any(code in POSITIVE_CODES for code in campaign_codes):
        return _state("DELIVERING", "DELIVERING", "يتم التسليم", None, **summary)
    if "PENDING" in campaign_codes:
        return _state(
            "PENDING",
            "PENDING",
            "قيد التحقق من التسليم",
            "Snapchat ما زالت تتحقق من صلاحية التسليم.",
            **summary,
        )
    return _state(
        "UNKNOWN",
        "DELIVERY_UNKNOWN",
        "حالة التسليم غير متاحة",
        "لم ترجع Snapchat حالة تسليم مؤكدة لهذه الحملة بعد.",
        **summary,
    )


async def refresh_snapchat_account_delivery(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
) -> dict[str, Any]:
    account_id = _text(account.get("ad_account_id"))
    if not account_id:
        raise SnapchatNativeSyncError(
            "snapchat_account_id_missing",
            "Selected Snapchat account is missing its ad account ID.",
            status_code=409,
            retryable=False,
        )
    payload = await context.get_json(
        client,
        f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    provider_account = _unwrap_ad_account(payload, account_id)
    if not provider_account:
        raise SnapchatNativeSyncError(
            "snapchat_ad_account_delivery_payload_invalid",
            "Snapchat returned an invalid ad-account delivery payload.",
            status_code=502,
            retryable=True,
        )

    status = _text(provider_account.get("status")) or None
    delivery_codes = normalize_delivery_status(provider_account.get("delivery_status"))
    now_iso = context.now_iso()
    safe_snapshot = {
        key: provider_account.get(key)
        for key in (
            "id", "name", "status", "delivery_status", "currency", "timezone",
            "billing_type", "lifetime_spend_cap_micro",
        )
        if provider_account.get(key) is not None
    }
    await _collection(context.db, "mezan_integration_accounts_v2").update_one(
        {
            "user_id": context.user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "$or": [{"external_account_id": account_id}, {"ad_account_id": account_id}],
        },
        {"$set": {
            "account_status": status,
            "account_delivery_status": delivery_codes,
            "account_delivery_updated_at": now_iso,
            "account_delivery_source_mode": ACCOUNT_DELIVERY_SOURCE_MODE,
            "account_delivery_provider_snapshot": safe_snapshot,
            "last_observed_at": now_iso,
        }},
    )
    account["account_status"] = status
    account["account_delivery_status"] = delivery_codes
    return {
        "source_mode": ACCOUNT_DELIVERY_SOURCE_MODE,
        "ad_account_id": account_id,
        "account_status": status,
        "delivery_status": delivery_codes,
        "blocked": account_delivery_block(status, delivery_codes),
        "errors_count": 0,
        "errors": [],
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def _refresh_with_account_delivery(
    base_refresh: AccountRefresh,
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        delivery = await refresh_snapchat_account_delivery(context, client, access_token, account)
    except SnapchatNativeSyncError as exc:
        if exc.code == "snapchat_needs_reauth":
            raise
        delivery = {
            "source_mode": ACCOUNT_DELIVERY_SOURCE_MODE,
            "ad_account_id": _text(account.get("ad_account_id")),
            "account_status": account.get("account_status"),
            "delivery_status": normalize_delivery_status(account.get("account_delivery_status")),
            "blocked": None,
            "errors_count": 1,
            "errors": [{
                "kind": "account_delivery",
                "code": exc.code,
                "error": exc.code,
                "message": exc.message[:300],
                "retryable": bool(exc.retryable),
            }],
            "source_only": True,
            "provider_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    output = dict(await base_refresh(context, client, access_token, account, *args, **kwargs) or {})
    output["account_delivery"] = delivery
    if delivery.get("errors"):
        combined = [item for item in output.get("errors") or [] if isinstance(item, dict)]
        combined.extend(delivery["errors"])
        output["errors"] = combined
        output["errors_count"] = len(combined)
    return output


async def _account_delivery_row(db: Any, user_id: str, account_id: str) -> dict[str, Any]:
    return await _collection(db, "mezan_integration_accounts_v2").find_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "$or": [{"external_account_id": account_id}, {"ad_account_id": account_id}],
        },
        {"_id": 0, "account_status": 1, "account_delivery_status": 1, "account_delivery_updated_at": 1},
    ) or {}


async def _cursor_rows(cursor: Any, limit: int = 10_000) -> list[dict[str, Any]]:
    return await cursor.to_list(length=limit) if hasattr(cursor, "to_list") else [row async for row in cursor]


async def _current_ad_squad_rows(
    db: Any,
    user_id: str,
    account_id: str,
    *,
    observed_after: str | None,
) -> list[dict[str, Any]]:
    cursor = _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
            "entity_type": "ad_squad",
        },
        {
            "_id": 0, "external_id": 1, "campaign_id": 1, "display_name": 1,
            "status": 1, "delivery_status": 1, "last_observed_at": 1,
        },
    )
    rows = await _cursor_rows(cursor)
    if not observed_after:
        return rows
    return [
        row for row in rows
        if _text(row.get("last_observed_at"))
        and _text(row.get("last_observed_at")) >= observed_after
    ]


async def _build_report_with_effective_delivery(
    base_builder: ReportBuilder,
    db: Any,
    user_id: str,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    output = dict(await base_builder(db, user_id, *args, **kwargs) or {})
    selected_id = _text(output.get("selected_account_id"))
    if not selected_id:
        return output

    account_row = await _account_delivery_row(db, user_id, selected_id)
    account_status = account_row.get("account_status")
    account_codes = normalize_delivery_status(account_row.get("account_delivery_status"))
    account_block = account_delivery_block(account_status, account_codes)
    observed_after = _text(account_row.get("account_delivery_updated_at")) or None
    ad_squad_rows = await _current_ad_squad_rows(
        db, user_id, selected_id, observed_after=observed_after
    )
    squads_by_campaign: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ad_squad_rows:
        if _text(row.get("campaign_id")):
            squads_by_campaign[_text(row.get("campaign_id"))].append(row)

    account_patch = {
        "account_status": account_status,
        "account_delivery_status": account_codes,
        "account_delivery_updated_at": account_row.get("account_delivery_updated_at"),
        "account_delivery_block": account_block,
    }
    if isinstance(output.get("selected_account"), dict):
        output["selected_account"].update(account_patch)
    for account in output.get("accounts") or []:
        if isinstance(account, dict):
            account.update(account_patch)
    for account in output.get("available_accounts") or []:
        if isinstance(account, dict) and _text(account.get("account_id")) == selected_id:
            account.update(account_patch)

    for campaign in output.get("campaigns") or []:
        if not isinstance(campaign, dict):
            continue
        campaign_id = _text(campaign.get("campaign_id"))
        configured = _text(campaign.get("status") or "unknown").upper()
        provider_delivery = campaign.get("delivery_status")
        presentation = campaign_delivery_state(
            configured,
            provider_delivery,
            account_block=account_block,
            ad_squads=squads_by_campaign.get(campaign_id, []),
        )
        campaign.update({
            "configured_status": configured,
            "effective_status": configured,
            "provider_delivery_status": provider_delivery,
            "provider_delivery_status_codes": normalize_delivery_status(provider_delivery),
            "delivery_state": presentation["state"],
            "delivery_reason_code": presentation["code"],
            "delivery_label": presentation["label"],
            "delivery_detail": presentation["detail"],
            "deliverable": presentation["deliverable"],
            "effective_delivery_code": presentation["code"],
            "effective_delivery_label": presentation["label"],
            "effective_delivery_detail": presentation["detail"],
            "ad_squad_delivery": {
                key: value for key, value in presentation.items()
                if key.startswith("ad_squad_")
            },
            **account_patch,
        })

    source = output.setdefault("source", {})
    if isinstance(source, dict):
        source.update({
            "account_delivery_source_mode": ACCOUNT_DELIVERY_SOURCE_MODE,
            "account_delivery_status": account_codes,
            "account_delivery_blocked": bool(account_block),
            "account_delivery_updated_at": account_row.get("account_delivery_updated_at"),
            "ad_squad_delivery_rows": len(ad_squad_rows),
            "configured_status_separate_from_delivery": True,
        })
    return output


def install_snapchat_account_delivery_refresh() -> None:
    current = hourly.refresh_snapchat_account_hours
    if getattr(current, "_mezan_account_delivery_refresh", False):
        return

    async def wrapped(
        context: SnapchatSyncContext,
        client: httpx.AsyncClient,
        access_token: str,
        account: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return await _refresh_with_account_delivery(
            current, context, client, access_token, account, *args, **kwargs
        )

    wrapped._mezan_account_delivery_refresh = True  # type: ignore[attr-defined]
    wrapped._mezan_account_delivery_base = current  # type: ignore[attr-defined]
    hourly.refresh_snapchat_account_hours = wrapped


def install_snapchat_effective_delivery_report() -> None:
    current = timezone_manager.build_account_timezone_campaign_report
    if getattr(current, "_mezan_effective_delivery_report", False):
        return

    async def wrapped(db: Any, user_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await _build_report_with_effective_delivery(current, db, user_id, *args, **kwargs)

    wrapped._mezan_effective_delivery_report = True  # type: ignore[attr-defined]
    wrapped._mezan_effective_delivery_base = current  # type: ignore[attr-defined]
    timezone_manager.build_account_timezone_campaign_report = wrapped


__all__ = [
    "ACCOUNT_DELIVERY_SOURCE_MODE",
    "PAYMENT_BLOCK_CODES",
    "account_delivery_block",
    "campaign_delivery_state",
    "install_snapchat_account_delivery_refresh",
    "install_snapchat_effective_delivery_report",
    "normalize_delivery_status",
    "refresh_snapchat_account_delivery",
]
