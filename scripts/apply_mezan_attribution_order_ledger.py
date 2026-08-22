from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

module = r'''"""Tenant-scoped Mezan attribution order ledger.

This module builds a deterministic internal ledger from order facts plus explicit
marketing attribution evidence.  It never allocates direct/manual/WhatsApp/gift
or ambiguous orders to campaigns, never uses provider purchase totals to invent
order attribution, and never converts missing profit facts to zero.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from salla_marketing_attribution import (
    campaign_id_candidates,
    campaign_name_candidates,
    canonical_marketing_source,
)

CONTRACT_VERSION = "mezan_attribution_order_ledger_v1"
LEDGER_COLLECTION = "mezan_attribution_order_ledger_v1"
NON_CAMPAIGN_SOURCES = {"direct", "whatsapp", "manual", "gift"}


def _text(value: Any, limit: int = 240) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _first_text(row: dict[str, Any], fields: tuple[str, ...]) -> str:
    for field in fields:
        value = _text(row.get(field), 240)
        if value:
            return value
    return ""


def _order_key(order: dict[str, Any]) -> str:
    return _first_text(order, ("order_id", "id", "reference_id", "order_number"))


def _line_items(order: dict[str, Any]) -> list[dict[str, Any]]:
    for field in ("items", "products", "order_items", "lines"):
        rows = order.get(field)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    raw = order.get("raw_by_source")
    if isinstance(raw, dict):
        salla = raw.get("salla_direct")
        if isinstance(salla, dict):
            for field in ("items", "products", "order_items", "lines"):
                rows = salla.get(field)
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
    return []


def _campaign_identity_maps(identities: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in identities:
        if not isinstance(row, dict):
            continue
        cid = _text(row.get("campaign_id"), 160)
        name = _text(row.get("campaign_name"), 300).casefold()
        if cid:
            by_id[cid] = row
        if name:
            by_name.setdefault(name, []).append(row)
    return by_id, by_name


def resolve_campaign_attribution(
    order: dict[str, Any],
    *,
    campaign_identities: list[dict[str, Any]],
) -> dict[str, Any]:
    """Resolve campaign attribution from explicit commerce evidence only."""
    source = canonical_marketing_source(order)
    if source in NON_CAMPAIGN_SOURCES:
        return {
            "source": source,
            "classification": "non_campaign",
            "campaign_id": None,
            "campaign_name": None,
            "confidence": "high",
            "decision_safe": False,
            "match_method": "explicit_non_campaign_source",
        }

    by_id, by_name = _campaign_identity_maps(campaign_identities)
    for candidate in campaign_id_candidates(order):
        key = _text(candidate, 160)
        if key and key in by_id:
            identity = by_id[key]
            return {
                "source": source,
                "classification": "campaign_matched",
                "campaign_id": key,
                "campaign_name": _text(identity.get("campaign_name") or key, 300),
                "confidence": "high",
                "decision_safe": True,
                "match_method": "exact_campaign_id",
            }

    matched_names: list[dict[str, Any]] = []
    for candidate in campaign_name_candidates(order):
        key = _text(candidate, 300).casefold()
        if key and len(by_name.get(key, [])) == 1:
            matched_names.append(by_name[key][0])
    unique = {(_text(row.get("campaign_id"), 160), _text(row.get("campaign_name"), 300)): row for row in matched_names}
    if len(unique) == 1:
        identity = next(iter(unique.values()))
        return {
            "source": source,
            "classification": "campaign_inferred",
            "campaign_id": _text(identity.get("campaign_id"), 160) or None,
            "campaign_name": _text(identity.get("campaign_name"), 300) or None,
            "confidence": "medium",
            "decision_safe": False,
            "match_method": "unique_campaign_name",
        }
    if matched_names:
        return {
            "source": source,
            "classification": "ambiguous",
            "campaign_id": None,
            "campaign_name": None,
            "confidence": "low",
            "decision_safe": False,
            "match_method": "ambiguous_campaign_name",
        }
    return {
        "source": source,
        "classification": "unattributed",
        "campaign_id": None,
        "campaign_name": None,
        "confidence": "low",
        "decision_safe": False,
        "match_method": "no_campaign_evidence",
    }


def _verified_product_links(
    product_links: list[dict[str, Any]],
    *,
    campaign_id: str | None,
) -> list[dict[str, Any]]:
    if not campaign_id:
        return []
    result = []
    for row in product_links:
        if not isinstance(row, dict):
            continue
        if _text(row.get("campaign_id"), 160) != campaign_id:
            continue
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        status = _text(evidence.get("verification_status") or row.get("verification_status"), 40).lower()
        if status == "verified":
            result.append(row)
    return result


def build_order_ledger_entry(
    order: dict[str, Any],
    *,
    campaign_identities: list[dict[str, Any]],
    product_links: list[dict[str, Any]] | None = None,
    profit_fact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attribution = resolve_campaign_attribution(order, campaign_identities=campaign_identities)
    links = _verified_product_links(product_links or [], campaign_id=attribution.get("campaign_id"))
    items = []
    for raw in _line_items(order):
        product_id = _first_text(raw, ("product_id", "id", "product.id", "sku")) or None
        variant_id = _first_text(raw, ("variant_id", "option_id", "variant.id", "sku")) or None
        items.append({
            "product_id": product_id,
            "variant_id": variant_id,
            "name": _first_text(raw, ("name", "product_name", "title")) or None,
            "quantity": _number(raw.get("quantity") or raw.get("qty")),
            "line_total_sar": _number(raw.get("total") or raw.get("amount") or raw.get("price_total")),
        })

    profit = profit_fact if isinstance(profit_fact, dict) else {}
    net_profit = _number(profit.get("net_profit_sar"))
    profit_known = profit.get("known") is True and net_profit is not None

    return {
        "contract_version": CONTRACT_VERSION,
        "order_key": _order_key(order),
        "order_number": _first_text(order, ("order_number", "reference_id", "order_id", "id")) or None,
        "order_status": _first_text(order, ("order_status", "status", "status_native")) or None,
        "order_total_sar": _number(order.get("total_amount") or order.get("total")),
        "created_at": _first_text(order, ("created_at", "order_created_at", "created_at_utc")) or None,
        "attribution": attribution,
        "provider": attribution.get("source") if attribution.get("source") in {"snapchat", "meta", "tiktok", "google"} else None,
        "campaign_id": attribution.get("campaign_id"),
        "campaign_name": attribution.get("campaign_name"),
        "items": items,
        "verified_campaign_product_links": [
            {
                "product_id": _text(row.get("product_id"), 160) or None,
                "product_variant_id": _text(row.get("product_variant_id"), 160) or None,
            }
            for row in links[:20]
        ],
        "profit": {
            "known": profit_known,
            "net_profit_sar": net_profit if profit_known else None,
            "source": _text(profit.get("source"), 120) or None,
        },
        "decision_safe_campaign_attribution": attribution.get("decision_safe") is True,
        "guardrails": [
            "Provider purchase totals are not order attribution evidence.",
            "Direct/manual/WhatsApp/gift orders are never distributed to campaigns.",
            "Unique-name matches are inferred and not decision-safe.",
            "Unknown profit remains null, never zero.",
        ],
    }


def build_order_ledger_batch(
    orders: list[dict[str, Any]],
    *,
    campaign_identities: list[dict[str, Any]],
    product_links: list[dict[str, Any]] | None = None,
    profit_by_order_key: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    profits = profit_by_order_key or {}
    result = []
    for order in orders:
        if not isinstance(order, dict):
            continue
        key = _order_key(order)
        if not key:
            continue
        result.append(build_order_ledger_entry(
            order,
            campaign_identities=campaign_identities,
            product_links=product_links,
            profit_fact=profits.get(key),
        ))
    return result


async def ensure_indexes(db: Any) -> None:
    await db[LEDGER_COLLECTION].create_index(
        [("user_id", 1), ("order_key", 1)], unique=True,
        name="mezan_attribution_order_ledger_user_order_unique",
    )
    await db[LEDGER_COLLECTION].create_index(
        [("user_id", 1), ("campaign_id", 1), ("created_at", -1)],
        name="mezan_attribution_order_ledger_campaign_recent",
    )


async def upsert_order_ledger_entries(db: Any, user_id: str, entries: list[dict[str, Any]]) -> dict[str, int]:
    await ensure_indexes(db)
    now_iso = datetime.now(timezone.utc).isoformat()
    upserted = 0
    skipped = 0
    for entry in entries:
        key = _text(entry.get("order_key"), 240)
        if not key:
            skipped += 1
            continue
        document = {**entry, "user_id": user_id, "updated_at": now_iso}
        await db[LEDGER_COLLECTION].update_one(
            {"user_id": user_id, "order_key": key},
            {"$set": document, "$setOnInsert": {"created_at_ledger": now_iso}},
            upsert=True,
        )
        upserted += 1
    return {"upserted": upserted, "skipped": skipped}


__all__ = [
    "CONTRACT_VERSION",
    "LEDGER_COLLECTION",
    "build_order_ledger_batch",
    "build_order_ledger_entry",
    "ensure_indexes",
    "resolve_campaign_attribution",
    "upsert_order_ledger_entries",
]
'''

(ROOT / "backend/mezan_attribution_order_ledger.py").write_text(module, encoding="utf-8")

tests = r'''from mezan_attribution_order_ledger import (
    build_order_ledger_entry,
    resolve_campaign_attribution,
)


IDENTITIES = [
    {"campaign_id": "c1", "campaign_name": "Saudi Winner"},
    {"campaign_id": "c2", "campaign_name": "Other Campaign"},
]


def test_exact_campaign_id_is_decision_safe():
    order = {"utm_source": "snapchat", "campaign_id": "c1"}
    result = resolve_campaign_attribution(order, campaign_identities=IDENTITIES)
    assert result["campaign_id"] == "c1"
    assert result["match_method"] == "exact_campaign_id"
    assert result["decision_safe"] is True


def test_unique_campaign_name_is_inferred_not_safe():
    order = {"utm_source": "snapchat", "campaign_name": "Saudi Winner"}
    result = resolve_campaign_attribution(order, campaign_identities=IDENTITIES)
    assert result["campaign_id"] == "c1"
    assert result["classification"] == "campaign_inferred"
    assert result["decision_safe"] is False


def test_direct_order_is_never_distributed():
    order = {"source": "direct", "campaign_name": "Saudi Winner"}
    result = resolve_campaign_attribution(order, campaign_identities=IDENTITIES)
    assert result["classification"] == "non_campaign"
    assert result["campaign_id"] is None


def test_whatsapp_order_is_never_distributed():
    order = {"source": "whatsapp", "campaign_id": "c1"}
    result = resolve_campaign_attribution(order, campaign_identities=IDENTITIES)
    assert result["classification"] == "non_campaign"
    assert result["campaign_id"] is None


def test_unknown_order_stays_unattributed():
    result = resolve_campaign_attribution({}, campaign_identities=IDENTITIES)
    assert result["classification"] == "unattributed"
    assert result["campaign_id"] is None


def test_unknown_profit_remains_null():
    order = {"order_id": "o1", "utm_source": "snapchat", "campaign_id": "c1"}
    entry = build_order_ledger_entry(order, campaign_identities=IDENTITIES)
    assert entry["profit"]["known"] is False
    assert entry["profit"]["net_profit_sar"] is None


def test_known_profit_is_carried_without_recalculation():
    order = {"order_id": "o1", "utm_source": "snapchat", "campaign_id": "c1"}
    entry = build_order_ledger_entry(
        order,
        campaign_identities=IDENTITIES,
        profit_fact={"known": True, "net_profit_sar": 42.5, "source": "mezan_profit_engine"},
    )
    assert entry["profit"]["known"] is True
    assert entry["profit"]["net_profit_sar"] == 42.5


def test_line_items_keep_product_and_variant_identity():
    order = {
        "order_id": "o1",
        "items": [{"product_id": "p1", "variant_id": "v1", "name": "Item", "quantity": 2}],
    }
    entry = build_order_ledger_entry(order, campaign_identities=IDENTITIES)
    assert entry["items"][0]["product_id"] == "p1"
    assert entry["items"][0]["variant_id"] == "v1"


def test_verified_product_link_enriches_only_after_campaign_match():
    order = {"order_id": "o1", "utm_source": "snapchat", "campaign_id": "c1"}
    links = [{
        "campaign_id": "c1",
        "product_id": "p9",
        "product_variant_id": "v9",
        "evidence": {"verification_status": "verified"},
    }]
    entry = build_order_ledger_entry(order, campaign_identities=IDENTITIES, product_links=links)
    assert entry["verified_campaign_product_links"][0]["product_id"] == "p9"


def test_provider_purchase_totals_are_not_accepted_as_order_evidence():
    order = {"order_id": "o1", "provider_purchases": 999}
    entry = build_order_ledger_entry(order, campaign_identities=IDENTITIES)
    assert entry["campaign_id"] is None
    assert entry["decision_safe_campaign_attribution"] is False
'''

(ROOT / "backend/tests/test_mezan_attribution_order_ledger.py").write_text(tests, encoding="utf-8")

print("wrote backend/mezan_attribution_order_ledger.py")
print("wrote backend/tests/test_mezan_attribution_order_ledger.py")
