"""Read-only Campaign AI adapter for Mezan's consolidated profit envelope."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from mezan_profit_engine import build_mezan_profit_envelope, build_mezan_profit_totals


def make_mezan_campaign_profit_loader(db: Any) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Expose the legacy loader shape without rebuilding P&L semantics here."""

    async def loader(
        *,
        user: dict[str, Any],
        from_date: str,
        to_date: str,
        payment_methods: str | None = None,
        shipping_companies: str | None = None,
        include_legacy_analyses: bool = False,
        allow_self_heal: bool = False,
    ) -> dict[str, Any]:
        del include_legacy_analyses, allow_self_heal
        user_id = str(user.get("id") or "").strip()
        if not user_id:
            raise ValueError("mezan_profit_loader_user_required")
        envelope = await build_mezan_profit_envelope(
            db,
            user_id,
            from_date=from_date,
            to_date=to_date,
            payment_methods=payment_methods,
            shipping_companies=shipping_companies,
        )
        return {
            "totals": dict(envelope["totals"]),
            "profit_envelope": envelope,
            "dashboard_source": envelope["source"],
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    return loader


__all__ = ["build_mezan_profit_totals", "make_mezan_campaign_profit_loader"]
