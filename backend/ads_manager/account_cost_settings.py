"""Mezan 2 native exchange-rate and bank-fee settings per ad account.

This module is deliberately isolated from the legacy ``counterparties`` and
``ads_currency_settings`` collections.  The account catalogue comes only from
``mezan_integration_accounts_v2`` and settings are persisted only in
``mezan_ad_account_cost_settings_v2``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


COLLECTION = "mezan_ad_account_cost_settings_v2"
ACCOUNT_COLLECTION = "mezan_integration_accounts_v2"
ADVERTISING_PROVIDERS = (
    "snapchat_ads",
    "tiktok_ads",
    "meta_ads",
    "google_ads",
)
PROVIDER_LABELS = {
    "snapchat_ads": "Snapchat",
    "tiktok_ads": "TikTok",
    "meta_ads": "Meta",
    "google_ads": "Google Ads",
}
PROVIDER_ORDER = {provider: index for index, provider in enumerate(ADVERTISING_PROVIDERS)}
DEFAULT_USD_TO_SAR = 3.7544
DEFAULT_BANK_COMMISSION = {
    "snapchat_ads": 2.30,
    "tiktok_ads": 0.0,
    "meta_ads": 0.0,
    "google_ads": 0.0,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return parsed if parsed == parsed and abs(parsed) != float("inf") else fallback


class AccountCostSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    native_currency: Literal["SAR", "USD"]
    exchange_rate_to_sar: float = Field(gt=0, le=20)
    bank_commission_pct: float = Field(ge=0, le=20)
    apply_bank_commission: bool

    @model_validator(mode="after")
    def normalize_sar_rate(self):
        if self.native_currency == "SAR" and abs(self.exchange_rate_to_sar - 1.0) > 0.000001:
            raise ValueError("SAR accounts must use exchange rate 1")
        return self


async def ensure_account_cost_settings_indexes(db: Any) -> None:
    await db[COLLECTION].create_index(
        [("user_id", 1), ("mezan_integration_account_id", 1)],
        unique=True,
        name="mezan_ad_account_cost_settings_v2_identity_unique",
    )
    await db[COLLECTION].create_index(
        [("user_id", 1), ("provider", 1), ("external_account_id", 1)],
        name="mezan_ad_account_cost_settings_v2_provider_account",
    )


async def _integration_accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    cursor = db[ACCOUNT_COLLECTION].find(
        {
            "user_id": user_id,
            "provider": {"$in": list(ADVERTISING_PROVIDERS)},
            "connection_provenance": "api_connection",
            "external_account_id": {"$nin": [None, ""]},
        },
        {
            "_id": 0,
            "mezan_integration_account_id": 1,
            "provider": 1,
            "external_account_id": 1,
            "ad_account_id": 1,
            "display_name": 1,
            "currency": 1,
            "timezone": 1,
            "connection_status": 1,
            "mezan_selected": 1,
            "last_sync_at": 1,
        },
    )
    rows = await cursor.to_list(length=500) if hasattr(cursor, "to_list") else [row async for row in cursor]
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for row in rows:
        account_key = _text(row.get("mezan_integration_account_id"))
        external_id = _text(row.get("external_account_id") or row.get("ad_account_id"))
        provider = _text(row.get("provider"))
        if not account_key or not external_id or provider not in ADVERTISING_PROVIDERS:
            continue
        if account_key in seen:
            continue
        seen.add(account_key)
        output.append({**row, "external_account_id": external_id})
    output.sort(
        key=lambda row: (
            PROVIDER_ORDER.get(row.get("provider"), 99),
            _text(row.get("display_name")).casefold(),
            row.get("external_account_id") or "",
        )
    )
    return output


def _effective_item(account: dict[str, Any], setting: dict[str, Any] | None) -> dict[str, Any]:
    provider = _text(account.get("provider"))
    provider_currency = _text(account.get("currency")).upper()
    native_currency = _text((setting or {}).get("native_currency")).upper()
    if native_currency not in {"SAR", "USD"}:
        native_currency = provider_currency if provider_currency in {"SAR", "USD"} else "SAR"
    default_rate = 1.0 if native_currency == "SAR" else DEFAULT_USD_TO_SAR
    exchange_rate = _number((setting or {}).get("exchange_rate_to_sar"), default_rate)
    if native_currency == "SAR":
        exchange_rate = 1.0
    elif exchange_rate <= 0:
        exchange_rate = DEFAULT_USD_TO_SAR
    default_pct = DEFAULT_BANK_COMMISSION.get(provider, 0.0)
    commission_pct = _number((setting or {}).get("bank_commission_pct"), default_pct)
    apply_fee = (setting or {}).get("apply_bank_commission")
    if not isinstance(apply_fee, bool):
        apply_fee = default_pct > 0
    return {
        "mezan_integration_account_id": account["mezan_integration_account_id"],
        "provider": provider,
        "provider_label": PROVIDER_LABELS.get(provider, provider),
        "external_account_id": account["external_account_id"],
        "display_name": account.get("display_name") or account["external_account_id"],
        "provider_currency": provider_currency or None,
        "timezone": account.get("timezone"),
        "connection_status": account.get("connection_status") or "unknown",
        "selected": account.get("mezan_selected") is True,
        "last_sync_at": account.get("last_sync_at"),
        "native_currency": native_currency,
        "exchange_rate_to_sar": round(exchange_rate, 6),
        "bank_commission_pct": round(max(0.0, commission_pct), 4),
        "apply_bank_commission": apply_fee,
        "configured": setting is not None,
        "updated_at": (setting or {}).get("updated_at"),
        "source_mode": "mezan2_ad_account_cost_settings_v1",
    }


async def list_account_cost_settings(db: Any, user_id: str) -> dict[str, Any]:
    await ensure_account_cost_settings_indexes(db)
    accounts = await _integration_accounts(db, user_id)
    ids = [row["mezan_integration_account_id"] for row in accounts]
    settings_cursor = db[COLLECTION].find(
        {"user_id": user_id, "mezan_integration_account_id": {"$in": ids}},
        {"_id": 0},
    )
    settings_rows = (
        await settings_cursor.to_list(length=500)
        if hasattr(settings_cursor, "to_list")
        else [row async for row in settings_cursor]
    )
    by_id = {row.get("mezan_integration_account_id"): row for row in settings_rows}
    items = [_effective_item(account, by_id.get(account["mezan_integration_account_id"])) for account in accounts]
    return {
        "generated_at": _now(),
        "items": items,
        "summary": {
            "accounts_total": len(items),
            "configured": sum(item["configured"] for item in items),
            "fee_enabled": sum(item["apply_bank_commission"] for item in items),
            "usd_accounts": sum(item["native_currency"] == "USD" for item in items),
            "sar_accounts": sum(item["native_currency"] == "SAR" for item in items),
        },
        "policy": {
            "source": ACCOUNT_COLLECTION,
            "settings_collection": COLLECTION,
            "legacy_counterparties_read": False,
            "legacy_ads_currency_settings_read": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        },
    }


async def save_account_cost_settings(
    db: Any,
    user_id: str,
    mezan_integration_account_id: str,
    payload: AccountCostSettingsInput,
) -> dict[str, Any]:
    await ensure_account_cost_settings_indexes(db)
    account = await db[ACCOUNT_COLLECTION].find_one(
        {
            "user_id": user_id,
            "mezan_integration_account_id": mezan_integration_account_id,
            "provider": {"$in": list(ADVERTISING_PROVIDERS)},
            "connection_provenance": "api_connection",
        },
        {"_id": 0},
    )
    if not account:
        raise HTTPException(status_code=404, detail={
            "code": "mezan2_ad_account_not_found",
            "message": "الحساب الإعلاني غير موجود ضمن حسابات ميزان 2 المرتبطة.",
        })
    external_id = _text(account.get("external_account_id") or account.get("ad_account_id"))
    now_iso = _now()
    document = {
        "user_id": user_id,
        "mezan_integration_account_id": mezan_integration_account_id,
        "provider": account.get("provider"),
        "external_account_id": external_id,
        "display_name_snapshot": account.get("display_name") or external_id,
        "native_currency": payload.native_currency,
        "exchange_rate_to_sar": 1.0 if payload.native_currency == "SAR" else float(payload.exchange_rate_to_sar),
        "bank_commission_pct": float(payload.bank_commission_pct),
        "apply_bank_commission": bool(payload.apply_bank_commission),
        "source_mode": "mezan2_ad_account_cost_settings_v1",
        "updated_at": now_iso,
    }
    await db[COLLECTION].update_one(
        {"user_id": user_id, "mezan_integration_account_id": mezan_integration_account_id},
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )
    return _effective_item(
        {**account, "external_account_id": external_id},
        document,
    )


def attach_account_cost_settings_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/account-cost-settings")
    async def get_account_cost_settings(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await list_account_cost_settings(db, str(owner["id"]))

    @router.put("/account-cost-settings/{mezan_integration_account_id}")
    async def put_account_cost_settings(
        mezan_integration_account_id: str,
        payload: AccountCostSettingsInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await save_account_cost_settings(
            db,
            str(owner["id"]),
            mezan_integration_account_id,
            payload,
        )


__all__ = [
    "ACCOUNT_COLLECTION",
    "ADVERTISING_PROVIDERS",
    "COLLECTION",
    "AccountCostSettingsInput",
    "attach_account_cost_settings_routes",
    "ensure_account_cost_settings_indexes",
    "list_account_cost_settings",
    "save_account_cost_settings",
]
