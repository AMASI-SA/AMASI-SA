"""Refresh Snapchat ad-account delivery and expose effective campaign status.

Campaign ``status`` only describes the campaign toggle (ACTIVE/PAUSED).  An
ACTIVE campaign can still be prevented from delivering by its parent ad
account, for example when the account has no remaining budget or a payment
restriction.  This module reads the ad-account object before the existing
campaign catalogue/performance refresh and stores its read-only delivery
status.  The Ads Manager report then keeps the configured campaign status while
also exposing an effective status for the UI.

No provider, campaign, accounting, Salla, or Qoyod mutation is performed.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

import httpx

from . import snapchat_account_hourly_refresh as hourly
from . import snapchat_account_timezone_manager as timezone_manager
from .snapchat_native_data_common import (
    SNAPCHAT_API_BASE,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _collection,
)

ACCOUNT_DELIVERY_SOURCE_MODE = "snapchat_ad_account_delivery_5m_v1"

PAYMENT_BLOCK_CODES = frozenset({
    "INVALID_REMAINING_AD_ACCOUNT_BUDGET",
    "INVALID_AD_ACCOUNT_LIFETIME_SPEND_CAP",
    "INVALID_OVER_BUDGET_AD_ACCOUNT_FINALIZED_LIFETIME_SPEND",
    "INVALID_OVER_BUDGET_AD_ACCOUNT_REALTIME_LIFETIME_SPEND",
})
BENIGN_DELIVERY_CODES = frozenset({"DELIVERING", "VALID", "PENDING"})
ACTIVE_ACCOUNT_STATUSES = frozenset({"ACTIVE", "ENABLED"})
ACTIVE_CAMPAIGN_STATUSES = frozenset({"ACTIVE", "ENABLED"})

AccountRefresh = Callable[..., Awaitable[dict[str, Any]]]
ReportBuilder = Callable[..., Awaitable[dict[str, Any]]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_delivery_status(value: Any) -> list[str]:
    """Return stable uppercase delivery codes from provider payload shapes."""
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
        raw = _text(item)
        if not raw:
            return
        for token in raw.replace(";", ",").split(","):
            code = token.strip().upper()
            if code and code not in output:
                output.append(code)

    add(value)
    return output


def _unwrap_ad_account(payload: dict[str, Any], account_id: str) -> dict[str, Any]:
    direct = payload.get("adaccount")
    if isinstance(direct, dict):
        return direct
    rows = payload.get("adaccounts")
    if isinstance(rows, list):
        for wrapped in rows:
            if not isinstance(wrapped, dict):
                continue
            status = _text(wrapped.get("sub_request_status") or "SUCCESS").upper()
            if "FAIL" in status or "ERROR" in status:
                continue
            account = wrapped.get("adaccount", wrapped)
            if not isinstance(account, dict):
                continue
            candidate_id = _text(account.get("id"))
            if not candidate_id or candidate_id == account_id:
                return account
    if _text(payload.get("id")) == account_id:
        return payload
    return {}


def account_delivery_block(
    account_status: Any,
    delivery_status: Any,
) -> dict[str, Any] | None:
    """Classify only parent-account blocks; campaign toggle remains separate."""
    status = _text(account_status).upper()
    codes = normalize_delivery_status(delivery_status)
    payment_codes = [code for code in codes if code in PAYMENT_BLOCK_CODES]
    if payment_codes:
        code = payment_codes[0]
        detail = {
            "INVALID_REMAINING_AD_ACCOUNT_BUDGET": "رصيد الحساب أو وسيلة الدفع لا تسمح بالتسليم.",
            "INVALID_AD_ACCOUNT_LIFETIME_SPEND_CAP": "حد الإنفاق الكلي للحساب يمنع التسليم.",
            "INVALID_OVER_BUDGET_AD_ACCOUNT_FINALIZED_LIFETIME_SPEND": "تجاوز الحساب حد الإنفاق النهائي.",
            "INVALID_OVER_BUDGET_AD_ACCOUNT_REALTIME_LIFETIME_SPEND": "تجاوز الحساب حد الإنفاق الحالي.",
        }.get(code, "الحساب الإعلاني موقوف بسبب الدفع أو الرصيد.")
        return {
            "code": "ACCOUNT_PAYMENT_BLOCKED",
            "label": "متوقفة بسبب الدفع",
            "delivery_label": "الحساب الإعلاني لا يسلّم بسبب الدفع أو الرصيد",
            "detail": detail,
            "provider_codes": codes,
        }

    blocking_codes = [
        code for code in codes
        if code not in BENIGN_DELIVERY_CODES
        and (
            code.startswith("INVALID_")
            or code.startswith("NOT_")
            or code == "TEST_AD_ACCOUNT"
        )
    ]
    if status and status not in ACTIVE_ACCOUNT_STATUSES:
        return {
            "code": "ACCOUNT_NOT_ACTIVE",
            "label": "متوقفة على مستوى الحساب",
            "delivery_label": "الحساب الإعلاني غير نشط",
            "detail": f"حالة حساب Snapchat الحالية: {status}",
            "provider_codes": codes,
        }
    if blocking_codes:
        return {
            "code": "ACCOUNT_DELIVERY_BLOCKED",
            "label": "متوقفة على مستوى الحساب",
            "delivery_label": "الحساب الإعلاني يمنع التسليم",
            "detail": blocking_codes[0],
            "provider_codes": codes,
        }
    return None


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
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
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
            "id",
            "name",
            "status",
            "delivery_status",
            "currency",
            "timezone",
            "billing_type",
            "lifetime_spend_cap_micro",
        )
        if provider_account.get(key) is not None
    }
    await _collection(context.db, "mezan_integration_accounts_v2").update_one(
        {
            "user_id": context.user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "$or": [
                {"external_account_id": account_id},
                {"ad_account_id": account_id},
            ],
        },
        {
            "$set": {
                "account_status": status,
                "account_delivery_status": delivery_codes,
                "account_delivery_updated_at": now_iso,
                "account_delivery_source_mode": ACCOUNT_DELIVERY_SOURCE_MODE,
                "account_delivery_provider_snapshot": safe_snapshot,
                "last_observed_at": now_iso,
            }
        },
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
        delivery = await refresh_snapchat_account_delivery(
            context, client, access_token, account
        )
    except SnapchatNativeSyncError as exc:
        if exc.code == "snapchat_needs_reauth":
            raise
        delivery = {
            "source_mode": ACCOUNT_DELIVERY_SOURCE_MODE,
            "ad_account_id": _text(account.get("ad_account_id")),
            "account_status": account.get("account_status"),
            "delivery_status": normalize_delivery_status(
                account.get("account_delivery_status")
            ),
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

    result = await base_refresh(
        context, client, access_token, account, *args, **kwargs
    )
    output = dict(result or {})
    output["account_delivery"] = delivery
    delivery_errors = list(delivery.get("errors") or [])
    if delivery_errors:
        combined = [
            item for item in list(output.get("errors") or [])
            if isinstance(item, dict)
        ]
        combined.extend(delivery_errors)
        output["errors"] = combined
        output["errors_count"] = len(combined)
    return output


async def _account_delivery_row(db: Any, user_id: str, account_id: str) -> dict[str, Any]:
    row = await _collection(db, "mezan_integration_accounts_v2").find_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "$or": [
                {"external_account_id": account_id},
                {"ad_account_id": account_id},
            ],
        },
        {
            "_id": 0,
            "account_status": 1,
            "account_delivery_status": 1,
            "account_delivery_updated_at": 1,
        },
    )
    return row or {}


async def _build_report_with_effective_delivery(
    base_builder: ReportBuilder,
    db: Any,
    user_id: str,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    result = await base_builder(db, user_id, *args, **kwargs)
    output = dict(result or {})
    selected_id = _text(output.get("selected_account_id"))
    if not selected_id:
        return output
    account_row = await _account_delivery_row(db, user_id, selected_id)
    account_status = account_row.get("account_status")
    delivery_codes = normalize_delivery_status(
        account_row.get("account_delivery_status")
    )
    block = account_delivery_block(account_status, delivery_codes)

    for container_key in ("selected_account",):
        container = output.get(container_key)
        if isinstance(container, dict):
            container.update({
                "account_status": account_status,
                "account_delivery_status": delivery_codes,
                "account_delivery_updated_at": account_row.get(
                    "account_delivery_updated_at"
                ),
                "account_delivery_block": block,
            })
    for account in output.get("accounts") or []:
        if isinstance(account, dict):
            account.update({
                "account_status": account_status,
                "account_delivery_status": delivery_codes,
                "account_delivery_block": block,
            })
    for account in output.get("available_accounts") or []:
        if isinstance(account, dict) and _text(account.get("account_id")) == selected_id:
            account.update({
                "account_status": account_status,
                "account_delivery_status": delivery_codes,
                "account_delivery_block": block,
            })

    for campaign in output.get("campaigns") or []:
        if not isinstance(campaign, dict):
            continue
        configured = _text(campaign.get("status") or "unknown").upper()
        effective = configured
        label = None
        delivery_label = None
        detail = None
        if block and configured in ACTIVE_CAMPAIGN_STATUSES:
            effective = _text(block.get("code")) or "ACCOUNT_DELIVERY_BLOCKED"
            label = _text(block.get("label")) or "متوقفة على مستوى الحساب"
            delivery_label = _text(block.get("delivery_label"))
            detail = _text(block.get("detail"))
        campaign.update({
            "configured_status": configured,
            "effective_status": effective,
            "effective_status_label": label,
            "effective_delivery_label": delivery_label,
            "effective_delivery_detail": detail,
            "account_status": account_status,
            "account_delivery_status": delivery_codes,
            "account_delivery_block": block,
        })

    source = output.setdefault("source", {})
    if isinstance(source, dict):
        source.update({
            "account_delivery_source_mode": ACCOUNT_DELIVERY_SOURCE_MODE,
            "account_delivery_status": delivery_codes,
            "account_delivery_blocked": bool(block),
            "account_delivery_updated_at": account_row.get(
                "account_delivery_updated_at"
            ),
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
            current,
            context,
            client,
            access_token,
            account,
            *args,
            **kwargs,
        )

    wrapped._mezan_account_delivery_refresh = True  # type: ignore[attr-defined]
    wrapped._mezan_account_delivery_base = current  # type: ignore[attr-defined]
    hourly.refresh_snapchat_account_hours = wrapped


def install_snapchat_effective_delivery_report() -> None:
    current = timezone_manager.build_account_timezone_campaign_report
    if getattr(current, "_mezan_effective_delivery_report", False):
        return

    async def wrapped(
        db: Any,
        user_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return await _build_report_with_effective_delivery(
            current, db, user_id, *args, **kwargs
        )

    wrapped._mezan_effective_delivery_report = True  # type: ignore[attr-defined]
    wrapped._mezan_effective_delivery_base = current  # type: ignore[attr-defined]
    timezone_manager.build_account_timezone_campaign_report = wrapped


__all__ = [
    "ACCOUNT_DELIVERY_SOURCE_MODE",
    "PAYMENT_BLOCK_CODES",
    "account_delivery_block",
    "install_snapchat_account_delivery_refresh",
    "install_snapchat_effective_delivery_report",
    "normalize_delivery_status",
    "refresh_snapchat_account_delivery",
]
