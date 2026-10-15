"""Read-only ad-account FX and bank-fee calculations for Dashboard V2.

The module applies the account settings stored by Mezan 2 to native advertising
facts at report time.  It never mutates provider facts, accounting records, or
Qoyod.  Native provider spend remains the evidence; the effective SAR cost and
bank fee are derived for the requested dashboard period.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ads_manager.account_cost_settings import (
    ACCOUNT_COLLECTION,
    COLLECTION as COST_SETTINGS_COLLECTION,
    DEFAULT_BANK_COMMISSION,
    DEFAULT_USD_TO_SAR,
    PROVIDER_LABELS,
)


PROVIDER_IDS = {
    "snapchat": "snapchat_ads",
    "meta": "meta_ads",
    "tiktok": "tiktok_ads",
}
PROVIDER_ORDER = {provider: index for index, provider in enumerate(PROVIDER_IDS)}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    if parsed != parsed or abs(parsed) == float("inf"):
        return fallback
    return parsed


def _currency(value: Any) -> str:
    normalized = _text(value).upper()
    return normalized if normalized in {"SAR", "USD"} else ""


async def _to_list(cursor: Any, length: int = 1000) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


def apply_cost_settings_to_fact_rows(
    platform_rows: dict[str, list[dict[str, Any]]],
    integration_accounts: list[dict[str, Any]],
    settings_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return effective SAR spend and bank fees without changing source rows."""
    accounts_by_identity: dict[str, dict[str, Any]] = {}
    accounts_by_external: dict[tuple[str, str], dict[str, Any]] = {}
    for account in integration_accounts:
        provider = _text(account.get("provider"))
        external_id = _text(
            account.get("external_account_id") or account.get("ad_account_id")
        )
        identity = _text(account.get("mezan_integration_account_id"))
        if identity:
            accounts_by_identity[identity] = account
        if provider and external_id:
            accounts_by_external[(provider, external_id)] = account

    settings_by_identity: dict[str, dict[str, Any]] = {}
    settings_by_external: dict[tuple[str, str], dict[str, Any]] = {}
    for setting in settings_rows:
        identity = _text(setting.get("mezan_integration_account_id"))
        provider = _text(setting.get("provider"))
        external_id = _text(setting.get("external_account_id"))
        if identity:
            settings_by_identity[identity] = setting
        if provider and external_id:
            settings_by_external[(provider, external_id)] = setting

    adjusted: dict[str, list[dict[str, Any]]] = {}
    account_totals: dict[tuple[str, str], dict[str, Any]] = {}
    coverage = {
        "source_rows": 0,
        "native_spend_rows": 0,
        "stored_sar_fallback_rows": 0,
        "unresolved_rows": 0,
        "configured_accounts": 0,
        "defaulted_accounts": 0,
    }

    for provider_slug, rows in platform_rows.items():
        provider_id = PROVIDER_IDS.get(provider_slug, provider_slug)
        output_rows: list[dict[str, Any]] = []
        for source_row in rows:
            coverage["source_rows"] += 1
            external_id = _text(source_row.get("ad_account_id"))
            source_identity = _text(source_row.get("mezan_integration_account_id"))
            account = (
                accounts_by_identity.get(source_identity)
                if source_identity
                else None
            ) or accounts_by_external.get((provider_id, external_id), {})
            identity = source_identity or _text(
                account.get("mezan_integration_account_id")
            )
            setting = (
                settings_by_identity.get(identity)
                if identity
                else None
            ) or settings_by_external.get((provider_id, external_id))

            native_currency = _currency((setting or {}).get("native_currency"))
            if not native_currency:
                native_currency = _currency(
                    source_row.get("currency_native")
                    or source_row.get("currency")
                    or account.get("currency")
                )

            default_rate = 1.0 if native_currency == "SAR" else DEFAULT_USD_TO_SAR
            exchange_rate = _number(
                (setting or {}).get("exchange_rate_to_sar"),
                default_rate,
            )
            if native_currency == "SAR":
                exchange_rate = 1.0
            elif native_currency == "USD" and exchange_rate <= 0:
                exchange_rate = DEFAULT_USD_TO_SAR

            spend_native_value = source_row.get("spend_native")
            has_native_spend = spend_native_value is not None and native_currency in {"SAR", "USD"}
            if has_native_spend:
                spend_native = max(0.0, _number(spend_native_value))
                effective_spend_sar = spend_native * exchange_rate
                spend_source = "native_spend_x_account_rate"
                coverage["native_spend_rows"] += 1
            else:
                spend_native = None
                effective_spend_sar = max(0.0, _number(source_row.get("spend_sar")))
                spend_source = "stored_sar_fallback"
                if source_row.get("spend_sar") is None:
                    coverage["unresolved_rows"] += 1
                else:
                    coverage["stored_sar_fallback_rows"] += 1

            default_pct = _number(DEFAULT_BANK_COMMISSION.get(provider_id), 0.0)
            commission_pct = max(
                0.0,
                _number((setting or {}).get("bank_commission_pct"), default_pct),
            )
            apply_fee = (setting or {}).get("apply_bank_commission")
            if not isinstance(apply_fee, bool):
                apply_fee = default_pct > 0

            row = {
                **source_row,
                "effective_spend_sar": round(effective_spend_sar, 6),
                "effective_exchange_rate_to_sar": round(exchange_rate, 6),
                "effective_native_currency": native_currency or None,
                "effective_spend_source": spend_source,
                "effective_cost_setting_configured": setting is not None,
            }
            output_rows.append(row)

            key = (provider_id, external_id or identity or "unknown")
            current = account_totals.setdefault(key, {
                "provider": provider_id,
                "provider_label": PROVIDER_LABELS.get(provider_id, provider_id),
                "external_account_id": external_id,
                "mezan_integration_account_id": identity or None,
                "display_name": (
                    account.get("display_name")
                    or source_row.get("display_name")
                    or external_id
                    or "حساب إعلاني غير معروف"
                ),
                "native_currency": native_currency or None,
                "exchange_rate_to_sar": round(exchange_rate, 6),
                "spend_native": 0.0,
                "spend_sar": 0.0,
                "bank_commission_pct": round(commission_pct, 4),
                "apply_bank_commission": bool(apply_fee),
                "configured": setting is not None,
                "native_spend_complete": True,
                "source_rows": 0,
            })
            current["source_rows"] += 1
            current["spend_sar"] += effective_spend_sar
            if spend_native is None:
                current["native_spend_complete"] = False
            else:
                current["spend_native"] += spend_native
        adjusted[provider_slug] = output_rows

    accounts: list[dict[str, Any]] = []
    for current in account_totals.values():
        spend_sar = round(_number(current.get("spend_sar")), 2)
        fee = (
            round(spend_sar * _number(current.get("bank_commission_pct")) / 100, 2)
            if current.get("apply_bank_commission")
            else 0.0
        )
        accounts.append({
            **current,
            "spend_native": (
                round(_number(current.get("spend_native")), 6)
                if current.get("native_spend_complete")
                else None
            ),
            "spend_sar": spend_sar,
            "bank_commission_fee_sar": fee,
            "source_mode": "mezan2_ad_account_cost_settings_v1",
        })

    accounts.sort(key=lambda row: (
        PROVIDER_ORDER.get(
            next(
                (slug for slug, provider_id in PROVIDER_IDS.items()
                 if provider_id == row.get("provider")),
                "",
            ),
            99,
        ),
        _text(row.get("display_name")).casefold(),
        _text(row.get("external_account_id")),
    ))
    coverage["configured_accounts"] = sum(bool(row.get("configured")) for row in accounts)
    coverage["defaulted_accounts"] = len(accounts) - coverage["configured_accounts"]
    coverage.update({
        "complete": coverage["unresolved_rows"] == 0,
        "legacy_ads_currency_settings_read": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    })

    total_fee = round(sum(_number(row.get("bank_commission_fee_sar")) for row in accounts), 2)
    fee_subject_spend = round(sum(
        _number(row.get("spend_sar"))
        for row in accounts
        if row.get("apply_bank_commission")
    ), 2)
    return {
        "platform_rows": adjusted,
        "accounts": accounts,
        "total_fee_sar": total_fee,
        "fee_subject_spend_sar": fee_subject_spend,
        "total_effective_spend_sar": round(sum(
            _number(row.get("spend_sar")) for row in accounts
        ), 2),
        "coverage": coverage,
    }


async def apply_mezan_v2_ad_account_costs(
    db: Any,
    user_id: str,
    platform_rows: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    """Load Mezan 2 account settings and apply them to report facts."""
    provider_ids = list(PROVIDER_IDS.values())
    accounts = await _to_list(db[ACCOUNT_COLLECTION].find(
        {
            "user_id": user_id,
            "provider": {"$in": provider_ids},
            "connection_provenance": "api_connection",
        },
        {"_id": 0},
    ), 500)
    settings = await _to_list(db[COST_SETTINGS_COLLECTION].find(
        {"user_id": user_id, "provider": {"$in": provider_ids}},
        {"_id": 0},
    ), 500)
    return apply_cost_settings_to_fact_rows(platform_rows, accounts, settings)


def bank_commission_payment_breakdown(
    bank_commissions: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Shape ad-account bank fees as one payment-fee group with account rows."""
    value = bank_commissions or {}
    accounts = [
        row for row in (value.get("accounts") or [])
        if _number(row.get("spend_sar")) > 0
    ]
    if not accounts:
        return None
    subject = _number(value.get("fee_subject_spend_sar"))
    total_fee = _number(value.get("total_fee_sar"))
    weighted_pct = round(total_fee / subject * 100, 4) if subject > 0 else 0.0
    return {
        "key": "ad_bank_commissions",
        "name": "عمولات الحسابات الإعلانية",
        "total_sales": round(subject, 2),
        "fee_amount": round(total_fee, 2),
        "vat_amount": 0.0,
        "orders_count": 0,
        "commission_percent": weighted_pct,
        "fixed_fee": 0.0,
        "vat_percent": 0.0,
        "source": "mezan_ad_account_cost_settings_v2",
        "sub_methods": [
            {
                "key": f"ad_bank:{row.get('mezan_integration_account_id') or row.get('external_account_id')}",
                "display": f"{row.get('provider_label')} — {row.get('display_name')}",
                "name": row.get("display_name"),
                "parent_name": "عمولات الحسابات الإعلانية",
                "kind": "ad_bank_commission",
                "provider": row.get("provider"),
                "provider_label": row.get("provider_label"),
                "external_account_id": row.get("external_account_id"),
                "native_currency": row.get("native_currency"),
                "exchange_rate_to_sar": row.get("exchange_rate_to_sar"),
                "spend_native": row.get("spend_native"),
                "total_sales": row.get("spend_sar"),
                "fee_amount": row.get("bank_commission_fee_sar"),
                "orders_count": 0,
                "commission_percent": row.get("bank_commission_pct"),
                "fixed_fee": 0.0,
                "vat_percent": 0.0,
                "apply_bank_commission": row.get("apply_bank_commission") is True,
                "configured": row.get("configured") is True,
            }
            for row in accounts
        ],
    }


def merge_ad_bank_fees_into_dashboard(
    response: dict[str, Any],
    ads: dict[str, Any],
) -> dict[str, Any]:
    """Add ad bank fees once to payment fees, net sales, and net profit."""
    totals = response.setdefault("totals", {})
    bank_commissions = ads.get("bank_commissions") or {}
    bank_fee = round(_number(bank_commissions.get("total_fee_sar")), 2)
    previous_payment_total = _number(totals.get("total_payment_fees"))
    totals["ad_bank_commission_fees"] = bank_fee
    totals["total_payment_fees"] = round(previous_payment_total + bank_fee, 2)
    totals["net_profit"] = round(_number(totals.get("net_profit")) - bank_fee, 2)
    config = response.get("net_sales_config") or {}
    if config.get("deduct_payment_fees", True):
        totals["net_sales"] = round(_number(totals.get("net_sales")) - bank_fee, 2)

    existing = [
        row for row in (response.get("payment_breakdown") or [])
        if row.get("key") != "ad_bank_commissions"
    ]
    ad_entry = bank_commission_payment_breakdown(bank_commissions)
    response["payment_breakdown"] = existing + ([ad_entry] if ad_entry else [])
    return response


__all__ = [
    "PROVIDER_IDS",
    "apply_cost_settings_to_fact_rows",
    "apply_mezan_v2_ad_account_costs",
    "bank_commission_payment_breakdown",
    "merge_ad_bank_fees_into_dashboard",
]
