"""Growth Intelligence season and liquidity intelligence.

Recommendation-only context layer. It never triggers campaigns, inventory,
pricing, publishing, or purchasing actions.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

CONTRACT_VERSION = "season_liquidity_intelligence_v1"
COLLECTION = "growth_intelligence_season_liquidity_v1"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def build_season_liquidity_intelligence(
    *,
    as_of: datetime,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Create preparation windows from verified events only."""
    horizon = as_of + timedelta(days=120)
    opportunities = []
    limitations = []

    for event in events:
        if not isinstance(event, dict):
            continue
        starts = _date(event.get("starts_at") or event.get("event_date"))
        if not starts or starts < as_of or starts > horizon:
            continue
        confidence = _text(event.get("confidence")).lower() or "low"
        evidence = event.get("evidence") if isinstance(event.get("evidence"), list) else []
        if not evidence:
            limitations.append(f"missing_evidence:{_text(event.get('name'))}")
        opportunities.append({
            "event_id": _text(event.get("event_id")),
            "country": _text(event.get("country")),
            "name": _text(event.get("name")),
            "starts_at": starts.isoformat(),
            "preparation_start": (starts - timedelta(days=int(event.get("lead_days") or 30))).isoformat(),
            "confidence": confidence,
            "evidence": evidence,
            "product_themes": event.get("product_themes") if isinstance(event.get("product_themes"), list) else [],
            "requires_owner_review": True,
        })

    opportunities.sort(key=lambda item: item["starts_at"])
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": as_of.isoformat(),
        "read_only": True,
        "events": opportunities,
        "limitations": limitations,
        "guardrails": [
            "No automatic campaigns are launched from seasonal context.",
            "No salary/liquidity assumption without evidence.",
            "External events require provenance and freshness.",
            "Recommendations require owner approval before commercial action.",
        ],
    }


async def ensure_indexes(db: Any) -> None:
    await db[COLLECTION].create_index(
        [("user_id", 1), ("generated_at", -1)],
        name="season_liquidity_user_recent",
    )


async def save_season_liquidity_intelligence(db: Any, user_id: str, payload: dict[str, Any]) -> None:
    await ensure_indexes(db)
    await db[COLLECTION].insert_one({"user_id": user_id, **payload})


__all__ = [
    "CONTRACT_VERSION",
    "COLLECTION",
    "build_season_liquidity_intelligence",
    "ensure_indexes",
    "save_season_liquidity_intelligence",
]
