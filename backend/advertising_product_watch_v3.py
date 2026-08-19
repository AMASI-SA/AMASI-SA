"""Fast operational product watch, separate from the 5-hour OpenAI cycle.

This is not a marketing-decision engine.  It checks objective operational facts
for products tied to currently spending campaigns: public visibility, stock,
verified promoted variant, and canonical public page health.  Alerts are facts
for operators and later OpenAI diagnosis; they never pause ads or modify Salla.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from integrations_control_center.campaign_product_associations import (
    CAMPAIGN_PRODUCT_LINK_COLLECTION,
)
from integrations_control_center.meta_campaign_reporting import (
    META_CAMPAIGN_REPORTING_COLLECTION,
)
from integrations_control_center.snapchat_account_timezone_manager import (
    SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
)
from product_v2_routes import PRODUCTS as PRODUCT_V2_COLLECTION

from campaign_ai_product_intelligence_v3 import PRODUCT_WATCH_HISTORY
from campaign_ai_public_page_probe_v3 import probe_product_page


ALERT_COLLECTION = "mezan_advertising_product_watch_alerts_v1"
CADENCE_COLLECTION = "mezan_advertising_product_watch_cadence_v1"
CADENCE_ID = "advertising_product_watch_global_v1"
WATCH_INTERVAL_SECONDS = 15 * 60
LEASE_SECONDS = 10 * 60
MAX_LINK_EVENTS = 100_000
MAX_PAGE_PROBES = 30
SALLA_PRODUCT_CACHE = "salla_products"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _parse_dt(value: Any) -> datetime | None:
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


def _active_at(row: dict[str, Any], now: datetime) -> bool:
    start = _parse_dt(row.get("valid_from") or row.get("recorded_at"))
    stop = _parse_dt(row.get("valid_to"))
    if start and start > now:
        return False
    if stop and stop <= now:
        return False
    return row.get("state") == "active"


async def _effective_verified_links(db: Any, user_id: str, now: datetime) -> list[dict[str, Any]]:
    rows = await db[CAMPAIGN_PRODUCT_LINK_COLLECTION].find(
        {"user_id": user_id},
        {"_id": 0},
    ).sort([("recorded_at", 1), ("event_id", 1)]).limit(MAX_LINK_EVENTS).to_list(length=MAX_LINK_EVENTS)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        start = _parse_dt(row.get("valid_from") or row.get("recorded_at"))
        if start and start > now:
            continue
        key = str(row.get("association_key") or "")
        if key:
            latest[key] = row
    return [
        row for row in latest.values()
        if _active_at(row, now)
        and (row.get("evidence") or {}).get("verification_status") == "verified"
        and row.get("campaign_id")
        and row.get("product_id")
    ]


async def _campaign_spend(db: Any, user_id: str, link: dict[str, Any], today: date) -> dict[str, Any] | None:
    provider = str(link.get("provider") or "")
    account_id = str(link.get("account_id") or "")
    campaign_id = str(link.get("campaign_id") or "")
    min_date = (today - timedelta(days=1)).isoformat()
    if provider in {"snapchat_ads", "snapchat"}:
        row = await db[SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION].find_one(
            {
                "user_id": user_id,
                "ad_account_id": account_id,
                "entity_type": "campaign",
                "external_id": campaign_id,
                "date": {"$gte": min_date},
                "action_report_time": "conversion",
            },
            {"_id": 0, "date": 1, "spend_sar": 1, "account_timezone": 1, "source_mode": 1},
            sort=[("date", -1)],
        )
        spend = _number((row or {}).get("spend_sar"))
        if spend and spend > 0:
            return {"provider": "snapchat", "spend_sar": spend, **(row or {})}
        return None
    if provider == "meta":
        row = await db[META_CAMPAIGN_REPORTING_COLLECTION].find_one(
            {
                "user_id": user_id,
                "ad_account_id": account_id,
                "campaign_id": campaign_id,
                "date": {"$gte": min_date},
            },
            {"_id": 0, "date": 1, "spend_sar": 1, "source_mode": 1},
            sort=[("date", -1)],
        )
        spend = _number((row or {}).get("spend_sar"))
        if spend and spend > 0:
            return {"provider": "meta", "spend_sar": spend, **(row or {})}
    return None


def _product_status(product: dict[str, Any]) -> str:
    if product.get("archived") is True:
        return "hidden_or_inactive"
    raw = _text(product.get("status"), 80).casefold()
    if raw in {"active", "sale", "available", "published", "enabled"}:
        return "active"
    if raw in {"out", "out_of_stock", "sold_out"}:
        return "out_of_stock"
    if raw in {"inactive", "hidden", "draft", "disabled", "archived"}:
        return "hidden_or_inactive"
    return raw or "unknown"


def _variant(product: dict[str, Any], variant_id: str | None) -> dict[str, Any] | None:
    if not variant_id:
        return None
    for row in product.get("variants") or []:
        if isinstance(row, dict) and str(row.get("id") or "") == str(variant_id):
            return row
    return None


async def _stock_velocity(db: Any, user_id: str, product_id: str, quantity: float | None, now: datetime) -> dict[str, Any]:
    if quantity is None:
        return {"available": False, "estimated_days_remaining": None}
    previous = await db[PRODUCT_WATCH_HISTORY].find_one(
        {
            "user_id": user_id,
            "product_id": product_id,
            "observed_at": {"$lte": now - timedelta(hours=3), "$gte": now - timedelta(hours=30)},
            "quantity": {"$ne": None},
        },
        {"_id": 0, "quantity": 1, "observed_at": 1},
        sort=[("observed_at", 1)],
    )
    old_quantity = _number((previous or {}).get("quantity"))
    old_time = _parse_dt((previous or {}).get("observed_at"))
    if old_quantity is None or old_time is None or old_quantity <= quantity:
        return {"available": False, "estimated_days_remaining": None}
    elapsed_days = (now - old_time).total_seconds() / 86400.0
    if elapsed_days <= 0:
        return {"available": False, "estimated_days_remaining": None}
    depletion_per_day = (old_quantity - quantity) / elapsed_days
    if depletion_per_day <= 0:
        return {"available": False, "estimated_days_remaining": None}
    days = max(quantity, 0) / depletion_per_day
    return {
        "available": True,
        "observed_from": old_time.isoformat(),
        "previous_quantity": old_quantity,
        "depletion_per_day_estimate": round(depletion_per_day, 3),
        "estimated_days_remaining": round(days, 2),
        "estimate_scope": "inventory_snapshot_depletion_evidence_not_forecast_rule",
    }


def _alert_key(user_id: str, product_id: str, campaign_id: str, code: str, variant_id: str | None = None) -> str:
    raw = "|".join((user_id, product_id, campaign_id, code, variant_id or ""))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _record_alert(
    db: Any,
    *,
    user_id: str,
    link: dict[str, Any],
    spend: dict[str, Any],
    product: dict[str, Any],
    code: str,
    severity: str,
    evidence: dict[str, Any],
    now: datetime,
) -> str:
    product_id = str(link.get("product_id") or "")
    campaign_id = str(link.get("campaign_id") or "")
    variant_id = str(link.get("product_variant_id") or "") or None
    key = _alert_key(user_id, product_id, campaign_id, code, variant_id)
    await db[ALERT_COLLECTION].update_one(
        {"alert_key": key},
        {
            "$set": {
                "user_id": user_id,
                "alert_key": key,
                "status": "active",
                "code": code,
                "severity": severity,
                "provider": spend.get("provider"),
                "account_id": link.get("account_id"),
                "campaign_id": campaign_id,
                "ad_squad_id": link.get("ad_squad_id"),
                "ad_id": link.get("ad_id"),
                "product_id": product_id,
                "product_variant_id": variant_id,
                "product_name": product.get("name") or link.get("product_name"),
                "current_spend_sar": spend.get("spend_sar"),
                "spend_date": spend.get("date"),
                "evidence": evidence,
                "last_seen_at": now,
                "updated_at": now,
                "marketing_decision": False,
                "provider_write_reached": False,
                "salla_write_reached": False,
            },
            "$setOnInsert": {"first_seen_at": now, "created_at": now},
        },
        upsert=True,
    )
    return key


async def scan_user_product_watch(db: Any, user_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    links = await _effective_verified_links(db, user_id, current)
    spending: list[tuple[dict[str, Any], dict[str, Any]]] = []
    spend_cache: dict[tuple[str, str, str], dict[str, Any] | None] = {}
    for link in links:
        key = (
            str(link.get("provider") or ""),
            str(link.get("account_id") or ""),
            str(link.get("campaign_id") or ""),
        )
        if key not in spend_cache:
            spend_cache[key] = await _campaign_spend(db, user_id, link, current.date())
        if spend_cache[key]:
            spending.append((link, spend_cache[key] or {}))
    spending.sort(key=lambda pair: float(pair[1].get("spend_sar") or 0), reverse=True)

    active_keys: set[str] = set()
    probes = 0
    probe_cache: dict[str, dict[str, Any]] = {}
    product_cache: dict[str, dict[str, Any]] = {}
    canonical_cache: dict[str, dict[str, Any]] = {}
    watched_products: set[str] = set()

    for link, spend in spending:
        product_id = str(link.get("product_id") or "")
        if not product_id:
            continue
        if product_id not in product_cache:
            product_cache[product_id] = await db[PRODUCT_V2_COLLECTION].find_one(
                {"user_id": user_id, "salla_product_id": product_id},
                {"_id": 0, "raw_salla": 0, "raw_salla_details": 0},
            ) or {}
            canonical_cache[product_id] = await db[SALLA_PRODUCT_CACHE].find_one(
                {"user_id": user_id, "product_id": product_id},
                {"_id": 0, "url": 1},
            ) or {}
        product = product_cache[product_id]
        canonical_url = _text(canonical_cache[product_id].get("url"), 2000) or None
        status = _product_status(product)
        quantity = _number(product.get("quantity"))
        unlimited = bool(product.get("unlimited_quantity"))
        velocity = await _stock_velocity(db, user_id, product_id, quantity, current)

        page_probe = {"checked": False, "status": "PRODUCT_URL_NOT_PROBED_DUE_TO_BOUND"}
        if canonical_url in probe_cache:
            page_probe = probe_cache[canonical_url]
        elif canonical_url and probes < MAX_PAGE_PROBES:
            page_probe = await probe_product_page(canonical_url, canonical_url=canonical_url)
            probe_cache[canonical_url] = page_probe
            probes += 1

        alerts: list[tuple[str, str, dict[str, Any]]] = []
        if status == "hidden_or_inactive":
            alerts.append((
                "PRODUCT_HIDDEN_WHILE_AD_SPEND",
                "critical",
                {"product_status": status, "visibility": "not_public_or_inactive"},
            ))
        if status == "out_of_stock" or (quantity is not None and quantity <= 0 and not unlimited):
            alerts.append((
                "OUT_OF_STOCK_AD_SPEND",
                "critical",
                {"product_status": status, "quantity": quantity, "unlimited_quantity": unlimited},
            ))
        variant_id = str(link.get("product_variant_id") or "") or None
        promoted_variant = _variant(product, variant_id)
        if promoted_variant:
            variant_quantity = _number(promoted_variant.get("quantity") or promoted_variant.get("stock_quantity"))
            variant_unlimited = bool(promoted_variant.get("unlimited_quantity") or promoted_variant.get("is_infinite"))
            if variant_quantity is not None and variant_quantity <= 0 and not variant_unlimited:
                alerts.append((
                    "PROMOTED_VARIANT_OUT_OF_STOCK",
                    "high",
                    {"variant_id": variant_id, "variant_quantity": variant_quantity},
                ))
        if page_probe.get("status") in {
            "PRODUCT_URL_BROKEN",
            "PRODUCT_URL_WRONG_DESTINATION",
            "PRODUCT_PAGE_UNAVAILABLE",
        }:
            alerts.append((
                "BROKEN_PRODUCT_PAGE_WHILE_AD_SPEND",
                "critical",
                {"page_probe": page_probe},
            ))
        days = _number(velocity.get("estimated_days_remaining"))
        if days is not None and 0 < days < 1:
            alerts.append((
                "LOW_STOCK_CONSTRAINT",
                "high",
                {"quantity": quantity, "inventory_velocity": velocity},
            ))

        for code, severity, evidence in alerts:
            key = await _record_alert(
                db,
                user_id=user_id,
                link=link,
                spend=spend,
                product=product,
                code=code,
                severity=severity,
                evidence=evidence,
                now=current,
            )
            active_keys.add(key)

        if product_id not in watched_products:
            watched_products.add(product_id)
            await db[PRODUCT_WATCH_HISTORY].insert_one({
                "user_id": user_id,
                "product_id": product_id,
                "product_name": product.get("name") or link.get("product_name"),
                "quantity": quantity,
                "unlimited_quantity": unlimited,
                "status": status,
                "canonical_url": canonical_url,
                "page_status": page_probe.get("status"),
                "inventory_velocity": velocity,
                "advertising_spend_sar": round(sum(
                    float(sp.get("spend_sar") or 0)
                    for ln, sp in spending
                    if str(ln.get("product_id") or "") == product_id
                ), 2),
                "observed_at": current,
                "source_mode": "advertising_product_watch_v3",
            })

    selector = {"user_id": user_id, "status": "active"}
    if active_keys:
        selector["alert_key"] = {"$nin": list(active_keys)}
    await db[ALERT_COLLECTION].update_many(
        selector,
        {"$set": {"status": "resolved", "resolved_at": current, "updated_at": current}},
    )
    return {
        "user_id": user_id,
        "verified_links": len(links),
        "spending_links": len(spending),
        "watched_products": len(watched_products),
        "active_alerts": len(active_keys),
        "public_page_probes": probes,
    }


async def ensure_product_watch_indexes(db: Any) -> None:
    await db[ALERT_COLLECTION].create_index("alert_key", unique=True, name="advertising_product_watch_alert_unique")
    await db[ALERT_COLLECTION].create_index([("user_id", 1), ("status", 1), ("severity", 1), ("last_seen_at", -1)], name="advertising_product_watch_user_active")
    await db[PRODUCT_WATCH_HISTORY].create_index([("user_id", 1), ("product_id", 1), ("observed_at", -1)], name="advertising_product_watch_history")
    await db[CADENCE_COLLECTION].create_index("cadence_id", unique=True, name="advertising_product_watch_cadence_unique")


async def claim_watch_cycle(db: Any, *, now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    lease_until = current + timedelta(seconds=LEASE_SECONDS)
    owner = hashlib.sha256(f"{current.isoformat()}|{id(db)}".encode()).hexdigest()[:24]
    from pymongo import ReturnDocument
    row = await db[CADENCE_COLLECTION].find_one_and_update(
        {
            "cadence_id": CADENCE_ID,
            "$or": [
                {"lease_until": {"$lte": current}},
                {"lease_until": {"$exists": False}, "next_run_at": {"$lte": current}},
                {"lease_until": {"$exists": False}, "next_run_at": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "cadence_id": CADENCE_ID,
                "owner": owner,
                "lease_until": lease_until,
                "last_started_at": current,
                "updated_at": current,
            },
            "$setOnInsert": {"created_at": current},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    if row and row.get("owner") == owner:
        return {"claimed": True, "owner": owner}
    existing = await db[CADENCE_COLLECTION].find_one({"cadence_id": CADENCE_ID}, {"_id": 0}) or {}
    return {
        "claimed": False,
        "skip_reason": "running_elsewhere" if _parse_dt(existing.get("lease_until")) and _parse_dt(existing.get("lease_until")) > current else "not_due",
        "next_run_at": existing.get("next_run_at"),
    }


async def finish_watch_cycle(db: Any, owner: str, *, now: datetime | None = None, failed: bool = False) -> None:
    current = now or _now()
    next_run = current + timedelta(seconds=WATCH_INTERVAL_SECONDS if not failed else 5 * 60)
    await db[CADENCE_COLLECTION].update_one(
        {"cadence_id": CADENCE_ID, "owner": owner},
        {"$set": {
            "last_finished_at": current,
            "next_run_at": next_run,
            "last_status": "failed" if failed else "complete",
            "updated_at": current,
        }, "$unset": {"lease_until": "", "owner": ""}},
    )


async def scan_all_product_watch(db: Any) -> dict[str, Any]:
    await ensure_product_watch_indexes(db)
    claim = await claim_watch_cycle(db)
    if not claim.get("claimed"):
        return {"skipped": True, **claim}
    owner = str(claim["owner"])
    try:
        user_ids = await db[CAMPAIGN_PRODUCT_LINK_COLLECTION].distinct("user_id")
        summaries = []
        for user_id in sorted(str(value) for value in user_ids if value):
            summaries.append(await scan_user_product_watch(db, user_id))
        await finish_watch_cycle(db, owner, failed=False)
        return {
            "skipped": False,
            "users": len(summaries),
            "active_alerts": sum(int(row.get("active_alerts") or 0) for row in summaries),
            "watched_products": sum(int(row.get("watched_products") or 0) for row in summaries),
            "summaries": summaries,
        }
    except Exception:
        await finish_watch_cycle(db, owner, failed=True)
        raise


__all__ = [
    "ALERT_COLLECTION",
    "scan_all_product_watch",
    "scan_user_product_watch",
]
