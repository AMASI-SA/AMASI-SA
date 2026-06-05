"""One-shot, idempotent migration that cleans up legacy `shipping_company`
values in `unified_orders` (iter-72).

Background: Excel exports prefix text cells with an apostrophe (`'`) to
force text-mode. The pre-iter72 parser stored those apostrophes verbatim
so the DB ended up with rows like `'iMile للتوصيل'` (count=1799) while
any future webhook with the clean spelling `iMile للتوصيل` would create
a SECOND row → duplicates everywhere (dashboard, reports, deferred
shipping balances).

This migrator scans the collection once, scrubs each value through
`shipping_companies.scrub_shipping_company()`, and updates only the
documents whose stored value differs from the cleaned one. Safe to call
on every startup — no-op on a clean DB.
"""
from __future__ import annotations

import logging

from shipping_companies import scrub_shipping_company

logger = logging.getLogger(__name__)


async def migrate_shipping_company_values(db) -> dict:
    """Scrub all stored `shipping_company` values across collections.

    Returns a summary dict the caller can log:
      {"unified_orders": {"scanned": N, "updated": M}, ...}
    """
    summary: dict[str, dict[str, int]] = {}

    # ── unified_orders (the big one) ───────────────────────────────────
    scanned = updated = 0
    cursor = db.unified_orders.find(
        {"shipping_company": {"$exists": True, "$ne": None}},
        {"_id": 1, "shipping_company": 1},
    )
    async for doc in cursor:
        scanned += 1
        raw = doc.get("shipping_company") or ""
        cleaned = scrub_shipping_company(raw)
        if cleaned != raw:
            await db.unified_orders.update_one(
                {"_id": doc["_id"]},
                {"$set": {"shipping_company": cleaned}},
            )
            updated += 1
    summary["unified_orders"] = {"scanned": scanned, "updated": updated}

    # ── shipping_accounts / settings (just in case) ────────────────────
    # `shipping_accounts` config lives under settings.deferred_shipping_companies
    # — those are user-entered names; we scrub them too so the matcher
    # works against a clean comparison string.
    settings_cursor = db.user_settings.find(
        {"deferred_shipping_companies": {"$exists": True, "$ne": None}},
        {"_id": 1, "user_id": 1, "deferred_shipping_companies": 1},
    )
    s_scanned = s_updated = 0
    async for s in settings_cursor:
        s_scanned += 1
        companies = s.get("deferred_shipping_companies") or []
        if not isinstance(companies, list):
            continue
        cleaned_list = []
        changed = False
        for c in companies:
            if isinstance(c, dict):
                raw = c.get("name") or c.get("company") or ""
                cleaned = scrub_shipping_company(raw)
                if cleaned != raw:
                    c = {**c}
                    if "name" in c:
                        c["name"] = cleaned
                    if "company" in c:
                        c["company"] = cleaned
                    changed = True
                cleaned_list.append(c)
            elif isinstance(c, str):
                cleaned = scrub_shipping_company(c)
                if cleaned != c:
                    changed = True
                cleaned_list.append(cleaned)
            else:
                cleaned_list.append(c)
        if changed:
            await db.user_settings.update_one(
                {"_id": s["_id"]},
                {"$set": {"deferred_shipping_companies": cleaned_list}},
            )
            s_updated += 1
    summary["user_settings.deferred_shipping_companies"] = {
        "scanned": s_scanned, "updated": s_updated,
    }

    return summary
