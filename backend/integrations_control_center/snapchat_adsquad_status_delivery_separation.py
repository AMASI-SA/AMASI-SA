"""Keep Snapchat Ad Squad configured status separate from delivery truth.

The status column represents the provider switch (ACTIVE/PAUSED). Payment,
account, parent Campaign, budget, schedule and review blockers belong only in
the delivery column. This projection is read-only and runs after the parent
Campaign delivery projection so its exact delivery reason is preserved.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from . import snapchat_adsquad_performance as adsquad_report

ADSQUAD_STATUS_DELIVERY_SOURCE_MODE = (
    "snapchat_adsquad_configured_status_delivery_v2"
)
ReportBuilder = Callable[..., Awaitable[dict[str, Any]]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def separate_adsquad_status_and_delivery(item: dict[str, Any]) -> dict[str, Any]:
    """Return one Ad Squad row with configured status and delivery separated."""
    configured = _text(
        item.get("configured_status")
        or item.get("provider_configured_status")
        or item.get("status")
        or "UNKNOWN"
    ).upper()
    previous_operational_status = _text(
        item.get("effective_status") or item.get("status") or configured
    ).upper()
    inherited_from_campaign = bool(
        item.get("status_inherited_from_campaign")
        or item.get("delivery_inherited_from_campaign")
    )
    account_block = item.get("account_delivery_block")
    account_block = account_block if isinstance(account_block, dict) else None

    output = dict(item)
    output.update({
        "configured_status": configured,
        "provider_configured_status": configured,
        "status": configured,
        "effective_status": configured,
        "previous_operational_status": previous_operational_status,
        "status_inherited_from_campaign": False,
        "delivery_inherited_from_campaign": inherited_from_campaign,
        "status_column_source": "snapchat_configured_switch",
        "delivery_column_source": "snapchat_effective_delivery",
    })

    if account_block:
        code = _text(account_block.get("code")) or "ACCOUNT_DELIVERY_BLOCKED"
        label = _text(account_block.get("delivery_label")) or (
            "لا تسليم — الحساب الإعلاني يمنع التسليم"
        )
        detail = _text(account_block.get("detail")) or None
        output.update({
            "delivery_state": "NOT_DELIVERING",
            "delivery_reason_code": code,
            "delivery_status": label,
            "delivery_label": label,
            "delivery_detail": detail,
            "deliverable": False,
            "delivery_inherited_from_account": True,
            "delivery_inherited_from_campaign": False,
        })
    else:
        output["delivery_inherited_from_account"] = False

    return output


async def _build_adsquad_report_with_separated_status(
    base_builder: ReportBuilder,
    db: Any,
    user_id: str,
    *args: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    output = dict(await base_builder(db, user_id, *args, **kwargs) or {})
    separated_rows: list[dict[str, Any]] = []
    account_blocked_rows = 0
    campaign_blocked_rows = 0
    for row in output.get("ad_squads") or []:
        if not isinstance(row, dict):
            continue
        separated = separate_adsquad_status_and_delivery(row)
        account_blocked_rows += int(
            separated.get("delivery_inherited_from_account") is True
        )
        campaign_blocked_rows += int(
            separated.get("delivery_inherited_from_campaign") is True
        )
        separated_rows.append(separated)
    output["ad_squads"] = separated_rows

    source = output.setdefault("source", {})
    if isinstance(source, dict):
        source.update({
            "ad_squad_status_delivery_source_mode": (
                ADSQUAD_STATUS_DELIVERY_SOURCE_MODE
            ),
            "ad_squad_status_column_uses_configured_switch": True,
            "ad_squad_delivery_column_uses_effective_delivery": True,
            "ad_squad_status_never_inferred_from_delivery": True,
            "account_delivery_inherited_rows": account_blocked_rows,
            "campaign_delivery_inherited_rows": campaign_blocked_rows,
        })
    return output


def install_snapchat_adsquad_status_delivery_separation() -> None:
    current = adsquad_report.build_account_timezone_adsquad_report
    if getattr(current, "_mezan_adsquad_status_delivery_separation", False):
        return

    async def wrapped(
        db: Any,
        user_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        return await _build_adsquad_report_with_separated_status(
            current,
            db,
            user_id,
            *args,
            **kwargs,
        )

    wrapped._mezan_adsquad_status_delivery_separation = True  # type: ignore[attr-defined]
    wrapped._mezan_adsquad_status_delivery_base = current  # type: ignore[attr-defined]
    adsquad_report.build_account_timezone_adsquad_report = wrapped


__all__ = [
    "ADSQUAD_STATUS_DELIVERY_SOURCE_MODE",
    "install_snapchat_adsquad_status_delivery_separation",
    "separate_adsquad_status_and_delivery",
]
