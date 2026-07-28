"""Read-only accounting projection for booked advertising expense.

Advertising platforms own delivery and spend facts. Mezan's
``general_ledger`` owns the accounting view. This module reads only the posted
``expense.advertising`` debit legs and keeps them distinct from provider facts.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date
from typing import Any


_ALIASES = {
    "snap": "snapchat",
    "snapchat": "snapchat",
    "tiktok": "tiktok",
    "meta": "meta",
    "facebook": "meta",
    "instagram": "meta",
}
MAX_BOOKED_ROWS = 2_000
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _provider(value: Any) -> str | None:
    return _ALIASES.get(str(value or "").strip().lower())


async def booked_ad_expense_by_provider_and_date(
    db: Any,
    user_id: str,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    """Return posted advertising expense grouped by provider and spend date.

    Rows without a defensible provider identity remain in ``unscoped_by_date``;
    they are never allocated across providers by inference.
    """

    counterparties = await db.counterparties.find(
        {
            "user_id": user_id,
            "kind": "ad_account",
        },
        {
            "_id": 0,
            "id": 1,
            "ad_provider": 1,
            "external_account_id": 1,
        },
    ).sort([("id", 1)]).limit(501).to_list(length=501)
    account_mapping_limit_reached = len(counterparties) > 500
    counterparties = counterparties[:500]
    account_details = {
        str(row.get("id")): {
            "provider": provider,
            "external_account_id": (
                str(row.get("external_account_id") or "").strip() or None
            ),
        }
        for row in counterparties
        if row.get("id") is not None
        and (provider := _provider(row.get("ad_provider")))
    }

    rows = await db.general_ledger.find(
        {
            "user_id": user_id,
            "entity_type": "expense",
            "entity_id": "advertising",
            "side": "debit",
            "status": "posted",
            "entry_type": {"$ne": "reversal"},
            "metadata.legacy_orphan": {"$ne": True},
            "metadata.spend_date": {"$gte": date_from, "$lte": date_to},
        },
        {
            "_id": 0,
            "amount": 1,
            "metadata": 1,
        },
    ).sort(
        [("metadata.spend_date", 1), ("metadata.ad_account_id", 1)]
    ).limit(MAX_BOOKED_ROWS + 1).to_list(length=MAX_BOOKED_ROWS + 1)
    row_limit_reached = len(rows) > MAX_BOOKED_ROWS
    rows = rows[:MAX_BOOKED_ROWS]

    by_provider: dict[str, dict[str, float]] = {
        "snapchat": defaultdict(float),
        "tiktok": defaultdict(float),
        "meta": defaultdict(float),
    }
    by_provider_account_date: dict[str, dict[str, float]] = {
        "snapchat": defaultdict(float),
        "tiktok": defaultdict(float),
        "meta": defaultdict(float),
    }
    unscoped_by_date: dict[str, float] = defaultdict(float)
    invalid_rows_count = 0
    for row in rows:
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            invalid_rows_count += 1
            continue
        spend_date = str(metadata.get("spend_date") or "").strip()
        if not ISO_DATE_RE.fullmatch(spend_date):
            invalid_rows_count += 1
            continue
        try:
            date.fromisoformat(spend_date)
        except ValueError:
            invalid_rows_count += 1
            continue
        if not date_from <= spend_date <= date_to:
            invalid_rows_count += 1
            continue
        provider = _provider(metadata.get("ad_provider"))
        account_id = str(metadata.get("ad_account_id") or "").strip()
        account = account_details.get(account_id)
        if provider is None:
            provider = (account or {}).get("provider")
        if row.get("amount") is None or isinstance(row.get("amount"), bool):
            invalid_rows_count += 1
            continue
        try:
            amount = float(row["amount"])
        except (TypeError, ValueError, OverflowError):
            invalid_rows_count += 1
            continue
        if not math.isfinite(amount) or amount < 0:
            invalid_rows_count += 1
            continue
        if provider is None:
            unscoped_by_date[spend_date] += amount
        else:
            by_provider[provider][spend_date] += amount
            external_id = (account or {}).get("external_account_id")
            if account_id and external_id:
                canonical_account = external_id.removeprefix("act_")
                pair = f"{canonical_account}\u241f{spend_date}"
                by_provider_account_date[provider][pair] += amount

    return {
        "by_provider": {
            provider: {
                spend_date: round(amount, 2)
                for spend_date, amount in sorted(daily.items())
            }
            for provider, daily in by_provider.items()
        },
        "by_provider_account_date": {
            provider: {
                pair: round(amount, 2)
                for pair, amount in sorted(account_daily.items())
            }
            for provider, account_daily in by_provider_account_date.items()
        },
        "unscoped_by_date": {
            spend_date: round(amount, 2)
            for spend_date, amount in sorted(unscoped_by_date.items())
        },
        "rows_count": len(rows),
        "row_limit_reached": row_limit_reached,
        "account_mapping_limit_reached": account_mapping_limit_reached,
        "invalid_rows_count": invalid_rows_count,
    }
