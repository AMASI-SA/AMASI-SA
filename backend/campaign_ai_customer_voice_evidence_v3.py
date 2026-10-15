"""Privacy-safe Voice of Customer evidence for Campaign AI Decision Intelligence V3.

The future Customer Service integration may publish normalized aggregate signals
into ``mezan_customer_voice_signals_v1``. Campaign AI never reads raw messages,
customer names, phone numbers, emails, usernames, addresses, or conversation
transcripts from this module.

Customer voice is corroborating evidence. Product/store-level feedback must not
become campaign attribution unless the upstream adapter explicitly verified a
campaign link. No signal in this module selects a marketing action.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


CUSTOMER_VOICE_COLLECTION = "mezan_customer_voice_signals_v1"
MAX_SIGNAL_ROWS = 2_000
ALLOWED_SIGNAL_TYPES = {
    "price_objection",
    "offer_confusion",
    "discount_confusion",
    "product_expectation_mismatch",
    "product_quality_concern",
    "size_or_fit_question",
    "variant_availability",
    "shipping_cost_objection",
    "delivery_time_objection",
    "checkout_friction",
    "payment_friction",
    "trust_objection",
    "creative_expectation_mismatch",
    "product_question",
    "positive_purchase_intent",
    "other",
}
VERIFIED_CAMPAIGN_ATTRIBUTION = "verified_explicit_campaign"


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _count(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return 1
    return max(1, min(parsed, 10_000))


def _safe_signal(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return only normalized non-PII fields accepted by the decision pack."""
    signal_type = str(row.get("signal_type") or "").strip().lower()
    if signal_type not in ALLOWED_SIGNAL_TYPES:
        return None
    observed_at = _utc(row.get("observed_at"))
    if observed_at is None:
        return None
    attribution = str(row.get("attribution_status") or "store_or_product_only")
    return {
        "observed_at": observed_at,
        "signal_type": signal_type,
        "count": _count(row.get("count")),
        "source_channel": str(row.get("source_channel") or "unknown")[:60],
        "product_id": str(row.get("product_id") or "")[:160] or None,
        "campaign_id": str(row.get("campaign_id") or "")[:160] or None,
        "attribution_status": attribution[:80],
    }


def _bucket(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    total = 0
    by_type: Counter[str] = Counter()
    by_channel: Counter[str] = Counter()
    for row in rows:
        count = int(row["count"])
        total += count
        by_type[row["signal_type"]] += count
        by_channel[row["source_channel"]] += count
    return {
        "signal_count": total,
        "signal_types": dict(by_type.most_common(12)),
        "source_channels": dict(by_channel.most_common(8)),
    }


def aggregate_customer_voice_rows(
    rows: Iterable[dict[str, Any]],
    *,
    now: datetime,
    relevant_campaign_ids: set[str] | None = None,
    relevant_product_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Aggregate normalized customer signals without preserving raw customer data."""
    current = _utc(now) or datetime.now(timezone.utc)
    campaign_ids = set(relevant_campaign_ids or set())
    product_ids = set(relevant_product_ids or set())
    clean = []
    for raw in rows:
        row = _safe_signal(raw)
        if row is None or row["observed_at"] < current - timedelta(days=30):
            continue
        clean.append(row)

    windows: dict[str, dict[str, Any]] = {}
    for name, delta in (("last_24h", timedelta(hours=24)), ("last_7d", timedelta(days=7)), ("last_30d", timedelta(days=30))):
        windows[name] = _bucket(row for row in clean if row["observed_at"] >= current - delta)

    product_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    campaign_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    store_rows: list[dict[str, Any]] = []
    for row in clean:
        product_id = row.get("product_id")
        campaign_id = row.get("campaign_id")
        if product_id and (not product_ids or product_id in product_ids):
            product_rows[product_id].append(row)
        else:
            store_rows.append(row)
        if (
            campaign_id
            and row.get("attribution_status") == VERIFIED_CAMPAIGN_ATTRIBUTION
            and (not campaign_ids or campaign_id in campaign_ids)
        ):
            campaign_rows[campaign_id].append(row)

    return {
        "schema_version": "campaign_ai_customer_voice_evidence_v3",
        "available": bool(clean),
        "windows": windows,
        "product_corroboration": {
            product_id: _bucket(values)
            for product_id, values in product_rows.items()
        },
        "verified_campaign_corroboration": {
            campaign_id: _bucket(values)
            for campaign_id, values in campaign_rows.items()
        },
        "store_level_corroboration": _bucket(store_rows),
        "contracts": {
            "raw_conversations_included": False,
            "pii_included": False,
            "individual_customer_profile_allowed": False,
            "store_or_product_feedback_becomes_campaign_attribution": False,
            "campaign_attribution_requires": VERIFIED_CAMPAIGN_ATTRIBUTION,
            "single_complaint_forces_marketing_action": False,
            "openai_remains_final_marketing_judgment": True,
        },
        "limitations": ([] if clean else ["customer_voice_not_connected_or_no_recent_normalized_signals"]),
    }


async def build_customer_voice_evidence(
    db: Any,
    user_id: str,
    candidates: list[dict[str, Any]],
    product_ids: list[str],
    *,
    current: datetime,
) -> dict[str, Any]:
    campaign_ids = {
        str(row.get("campaign_id") or (row.get("entity_id") if row.get("entity_level") == "campaign" else ""))
        for row in candidates
    }
    campaign_ids.discard("")
    product_set = {str(value) for value in product_ids if value}
    rows = await db[CUSTOMER_VOICE_COLLECTION].find(
        {
            "user_id": user_id,
            "observed_at": {"$gte": (current - timedelta(days=30)).astimezone(timezone.utc)},
        },
        {
            "_id": 0,
            "user_id": 0,
            "signal_type": 1,
            "count": 1,
            "observed_at": 1,
            "source_channel": 1,
            "product_id": 1,
            "campaign_id": 1,
            "attribution_status": 1,
        },
    ).sort("observed_at", -1).limit(MAX_SIGNAL_ROWS).to_list(length=MAX_SIGNAL_ROWS)
    return aggregate_customer_voice_rows(
        rows,
        now=current,
        relevant_campaign_ids=campaign_ids,
        relevant_product_ids=product_set,
    )


__all__ = [
    "ALLOWED_SIGNAL_TYPES",
    "CUSTOMER_VOICE_COLLECTION",
    "VERIFIED_CAMPAIGN_ATTRIBUTION",
    "aggregate_customer_voice_rows",
    "build_customer_voice_evidence",
]
