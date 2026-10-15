"""Project parent Campaign delivery truth onto Snapchat Ad Squads.

An Ad Squad can be configured ACTIVE while its parent Campaign cannot deliver
because the Campaign is paused, its daily/lifetime budget is exhausted, or the
Ad Account is blocked. Ads Manager should preserve the provider switch for
audit, but show the child as operationally stopped and inherit the exact parent
reason. No provider or accounting mutation is performed.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Awaitable, Callable

from . import snapchat_adsquad_performance as adsquad_report
from .snapchat_account_delivery_refresh import (
    ACTIVE_CAMPAIGN,
    ACTIVE_SQUAD,
    POSITIVE_CODES,
    SQUAD_REASONS,
    account_delivery_block,
    campaign_delivery_state,
    normalize_delivery_status,
)
from .snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    _collection,
)

ADSQUAD_PARENT_DELIVERY_SOURCE_MODE = (
    "snapchat_adsquad_parent_campaign_delivery_v1"
)
ReportBuilder = Callable[..., Awaitable[dict[str, Any]]]


def _text(value: Any) -> str:
    return str(value or "").strip()


async def _rows(cursor: Any, limit: int = 50_000) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=limit)
    return [row async for row in cursor]


def _state(
    state: str,
    code: str,
    label: str,
    detail: str | None,
    *,
    configured_status: str,
    effective_status: str,
    inherited_from_campaign: bool,
    provider_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "code": code,
        "label": label,
        "detail": detail,
        "deliverable": state == "DELIVERING",
        "configured_status": configured_status,
        "effective_status": effective_status,
        "inherited_from_campaign": inherited_from_campaign,
        "provider_codes": list(provider_codes or []),
    }


def _mapped_squad_reason(codes: list[str]) -> tuple[str, str, str] | None:
    for provider_code in codes:
        mapped = SQUAD_REASONS.get(provider_code)
        if mapped:
            code, label, detail = mapped
            return code, label, detail
    generic = next(
        (
            code
            for code in codes
            if code.startswith("INVALID_") or code.startswith("NOT_")
        ),
        None,
    )
    if generic:
        return (
            "AD_SQUAD_PROVIDER_DELIVERY_BLOCKED",
            "لا تسليم — يوجد مانع على المجموعة الإعلانية",
            generic,
        )
    return None


def ad_squad_effective_delivery_state(
    configured_status: Any,
    provider_delivery_status: Any,
    *,
    parent_campaign_status: Any,
    parent_campaign_delivery: dict[str, Any],
) -> dict[str, Any]:
    """Return operational status while preserving the configured switch."""
    configured = _text(configured_status).upper() or "UNKNOWN"
    parent_configured = _text(parent_campaign_status).upper() or "UNKNOWN"
    codes = normalize_delivery_status(provider_delivery_status)

    if configured not in ACTIVE_SQUAD:
        return _state(
            "NOT_DELIVERING",
            "AD_SQUAD_NOT_ACTIVE",
            "غير نشط",
            "المجموعة الإعلانية متوقفة من مفتاح الحالة داخل Snapchat.",
            configured_status=configured,
            effective_status="PAUSED",
            inherited_from_campaign=False,
            provider_codes=codes,
        )

    parent_state = _text(parent_campaign_delivery.get("state")).upper()
    parent_code = _text(parent_campaign_delivery.get("code"))
    parent_label = _text(parent_campaign_delivery.get("label"))
    parent_detail = _text(parent_campaign_delivery.get("detail"))
    if parent_configured not in ACTIVE_CAMPAIGN or parent_state == "NOT_DELIVERING":
        reason = parent_label or "الحملة الأم لا تسلّم"
        if reason.startswith("لا تسليم — "):
            reason = reason.removeprefix("لا تسليم — ")
        return _state(
            "NOT_DELIVERING",
            f"PARENT_{parent_code or 'CAMPAIGN_NOT_DELIVERING'}",
            f"لا تسليم — الحملة {reason}",
            parent_detail or "المجموعة مفعلة، لكن الحملة الأم لا تسمح بالتسليم.",
            configured_status=configured,
            effective_status="PAUSED",
            inherited_from_campaign=True,
            provider_codes=codes,
        )

    mapped = _mapped_squad_reason(codes)
    if mapped:
        code, label, detail = mapped
        return _state(
            "NOT_DELIVERING",
            code,
            label,
            detail,
            configured_status=configured,
            effective_status="PAUSED",
            inherited_from_campaign=False,
            provider_codes=codes,
        )
    if any(code in POSITIVE_CODES for code in codes):
        learning = any(
            code in {"LEARNING PHASE", "LEARNING_PHASE"} for code in codes
        )
        return _state(
            "DELIVERING",
            "DELIVERING",
            "يتم التسليم",
            "قد تكون في مرحلة التعلم" if learning else None,
            configured_status=configured,
            effective_status="ACTIVE",
            inherited_from_campaign=False,
            provider_codes=codes,
        )
    if "PENDING" in codes or parent_state == "PENDING":
        return _state(
            "PENDING",
            "PENDING",
            "قيد التحقق من التسليم",
            "Snapchat ما زالت تتحقق من صلاحية تسليم المجموعة.",
            configured_status=configured,
            effective_status="ACTIVE",
            inherited_from_campaign=False,
            provider_codes=codes,
        )
    return _state(
        "UNKNOWN",
        "AD_SQUAD_DELIVERY_UNKNOWN",
        "حالة التسليم غير متاحة",
        "لم ترجع Snapchat حالة تسليم مؤكدة لهذه المجموعة بعد.",
        configured_status=configured,
        effective_status="ACTIVE",
        inherited_from_campaign=False,
        provider_codes=codes,
    )


async def _account_delivery_row(
    db: Any,
    user_id: str,
    account_id: str,
) -> dict[str, Any]:
    return await _collection(db, "mezan_integration_accounts_v2").find_one(
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
    ) or {}


async def _build_adsquad_report_with_parent_delivery(
    base_builder: ReportBuilder,
    db: Any,
    user_id: str,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    output = dict(await base_builder(db, user_id, *args, **kwargs) or {})
    account_id = _text(output.get("selected_account_id"))
    if not account_id:
        return output

    entity_cursor = _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
            "entity_type": {"$in": ["campaign", "ad_squad"]},
        },
        {
            "_id": 0,
            "entity_type": 1,
            "external_id": 1,
            "campaign_id": 1,
            "status": 1,
            "delivery_status": 1,
        },
    )
    entity_rows = await _rows(entity_cursor)
    campaigns = {
        _text(row.get("external_id")): row
        for row in entity_rows
        if row.get("entity_type") == "campaign"
        and _text(row.get("external_id"))
    }
    squads = {
        _text(row.get("external_id")): row
        for row in entity_rows
        if row.get("entity_type") == "ad_squad"
        and _text(row.get("external_id"))
    }
    squads_by_campaign: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in squads.values():
        campaign_id = _text(row.get("campaign_id"))
        if campaign_id:
            squads_by_campaign[campaign_id].append(row)

    account_row = await _account_delivery_row(db, user_id, account_id)
    account_codes = normalize_delivery_status(
        account_row.get("account_delivery_status")
    )
    account_block = account_delivery_block(
        account_row.get("account_status"),
        account_codes,
    )

    inherited_count = 0
    for item in output.get("ad_squads") or []:
        if not isinstance(item, dict):
            continue
        squad_id = _text(item.get("ad_squad_id"))
        squad = squads.get(squad_id, {})
        campaign_id = _text(
            item.get("campaign_id") or squad.get("campaign_id")
        )
        campaign = campaigns.get(campaign_id, {})
        parent_delivery = campaign_delivery_state(
            campaign.get("status"),
            campaign.get("delivery_status"),
            account_block=account_block,
            ad_squads=squads_by_campaign.get(campaign_id, []),
        )
        presentation = ad_squad_effective_delivery_state(
            squad.get("status") or item.get("status"),
            squad.get("delivery_status") or item.get("delivery_status"),
            parent_campaign_status=campaign.get("status"),
            parent_campaign_delivery=parent_delivery,
        )
        inherited_count += int(presentation["inherited_from_campaign"])
        item.update({
            "configured_status": presentation["configured_status"],
            "effective_status": presentation["effective_status"],
            "status": presentation["effective_status"],
            "provider_delivery_status": squad.get("delivery_status"),
            "provider_delivery_status_codes": presentation["provider_codes"],
            "delivery_state": presentation["state"],
            "delivery_reason_code": presentation["code"],
            "delivery_status": presentation["label"],
            "delivery_label": presentation["label"],
            "delivery_detail": presentation["detail"],
            "deliverable": presentation["deliverable"],
            "status_inherited_from_campaign": presentation[
                "inherited_from_campaign"
            ],
            "parent_campaign_configured_status": _text(
                campaign.get("status")
            ).upper() or "UNKNOWN",
            "parent_campaign_delivery_state": parent_delivery.get("state"),
            "parent_campaign_delivery_code": parent_delivery.get("code"),
            "parent_campaign_delivery_label": parent_delivery.get("label"),
            "parent_campaign_delivery_detail": parent_delivery.get("detail"),
            "account_delivery_block": account_block,
        })

    source = output.setdefault("source", {})
    if isinstance(source, dict):
        source.update({
            "ad_squad_parent_delivery_source_mode": (
                ADSQUAD_PARENT_DELIVERY_SOURCE_MODE
            ),
            "parent_campaign_delivery_inherited_rows": inherited_count,
            "ad_squad_configured_status_preserved": True,
            "ad_squad_effective_status_includes_parent_campaign": True,
            "account_delivery_status": account_codes,
            "account_delivery_blocked": bool(account_block),
        })
    return output


def install_snapchat_adsquad_parent_delivery_report() -> None:
    current = adsquad_report.build_account_timezone_adsquad_report
    if getattr(current, "_mezan_adsquad_parent_delivery", False):
        return

    async def wrapped(
        db: Any,
        user_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return await _build_adsquad_report_with_parent_delivery(
            current,
            db,
            user_id,
            *args,
            **kwargs,
        )

    wrapped._mezan_adsquad_parent_delivery = True  # type: ignore[attr-defined]
    wrapped._mezan_adsquad_parent_delivery_base = current  # type: ignore[attr-defined]
    adsquad_report.build_account_timezone_adsquad_report = wrapped


__all__ = [
    "ADSQUAD_PARENT_DELIVERY_SOURCE_MODE",
    "ad_squad_effective_delivery_state",
    "install_snapchat_adsquad_parent_delivery_report",
]
