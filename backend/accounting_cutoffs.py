"""Iter-149 — Per-provider accounting cutoff date.

The merchant has historical data from before they fully wired the
accounting pipeline.  That data causes systematic discrepancies in
settlements, balances, and reports.  Rather than DELETING it (which
loses search/audit value), this module marks every entity occurring
before a provider's `accounting_start_date` as `is_pre_accounting`
so the rest of the codebase can simply skip it.

Concepts:

  • One cutoff row per (user_id, provider) in `accounting_cutoffs`.
  • Defaults are hard-coded below — populated lazily on first read so
    legacy users don't have to do anything to benefit.
  • Providers covered: tabby, tamara, salla, cod, bank_transfer
    (extensible — add a new key here + a default).

Helpers exported:

  • `get_cutoff(db, user_id, provider)`            → ISO date string
  • `get_all_cutoffs(db, user_id)`                 → dict[provider, date]
  • `set_cutoff(db, user_id, provider, date)`      → idempotent upsert
  • `mongo_cutoff_filter(date)`                    → `{"$gte": date}` clause
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


# Default accounting start dates per provider.  Merchant can override
# from the settings UI.  Edit here to change platform-wide defaults.
DEFAULT_CUTOFFS: Dict[str, str] = {
    "tabby":         "2026-04-27",
    "tamara":        "2026-04-25",
    "salla":         "2026-04-30",
    "cod":           "2026-04-30",
    "bank_transfer": "2026-04-30",
}

SUPPORTED_PROVIDERS: frozenset[str] = frozenset(DEFAULT_CUTOFFS.keys())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_provider(p: str) -> str:
    s = (p or "").strip().lower()
    if s in ("emkan", "imkan"):
        return "emkan"
    return s


def is_supported_provider(p: str) -> bool:
    return _normalize_provider(p) in SUPPORTED_PROVIDERS


async def get_cutoff(db, user_id: str, provider: str) -> Optional[str]:
    """Return the merchant's accounting cutoff for `provider`, falling
    back to `DEFAULT_CUTOFFS[provider]` when the user hasn't customised
    it.  Returns `None` for unknown providers (caller should treat as
    "no cutoff applied")."""
    p = _normalize_provider(provider)
    if p not in SUPPORTED_PROVIDERS:
        return DEFAULT_CUTOFFS.get(p)
    doc = await db.accounting_cutoffs.find_one(
        {"user_id": user_id, "provider": p},
        {"_id": 0, "accounting_start_date": 1},
    )
    if doc and doc.get("accounting_start_date"):
        return str(doc["accounting_start_date"])
    return DEFAULT_CUTOFFS.get(p)


async def get_all_cutoffs(db, user_id: str) -> Dict[str, str]:
    """Snapshot of every supported provider's cutoff for the user.

    Backfills defaults into MongoDB on first call so subsequent reads
    are stable and queryable.  Idempotent.
    """
    out: Dict[str, str] = {}
    existing: Dict[str, str] = {}
    async for d in db.accounting_cutoffs.find(
        {"user_id": user_id},
        {"_id": 0, "provider": 1, "accounting_start_date": 1},
    ):
        if d.get("provider"):
            existing[d["provider"]] = d.get("accounting_start_date") or ""

    # Seed missing providers with their default and persist.
    to_seed = []
    for p in SUPPORTED_PROVIDERS:
        if p in existing and existing[p]:
            out[p] = existing[p]
        else:
            out[p] = DEFAULT_CUTOFFS[p]
            to_seed.append(p)
    if to_seed:
        now = _now()
        await db.accounting_cutoffs.insert_many([
            {
                "user_id":               user_id,
                "provider":              p,
                "accounting_start_date": DEFAULT_CUTOFFS[p],
                "created_at":            now,
                "updated_at":            now,
                "source":                "default",
            }
            for p in to_seed
        ])
    return out


async def set_cutoff(
    db, user_id: str, provider: str, new_date: str,
) -> Dict[str, Any]:
    """Upsert the accounting cutoff for one provider.  Returns
    `(old_date, new_date)` so the recompute endpoint can decide
    whether to do extra work."""
    p = _normalize_provider(provider)
    if p not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider}")
    # Validate date shape (YYYY-MM-DD).
    try:
        datetime.strptime(new_date, "%Y-%m-%d")
    except (TypeError, ValueError) as e:
        raise ValueError(f"Invalid date format (expected YYYY-MM-DD): {new_date}") from e

    prev = await db.accounting_cutoffs.find_one(
        {"user_id": user_id, "provider": p},
        {"_id": 0, "accounting_start_date": 1},
    )
    old_date = (prev or {}).get("accounting_start_date") or DEFAULT_CUTOFFS[p]
    await db.accounting_cutoffs.update_one(
        {"user_id": user_id, "provider": p},
        {
            "$set": {
                "accounting_start_date": new_date,
                "updated_at":            _now(),
                "source":                "merchant",
            },
            "$setOnInsert": {
                "user_id":   user_id,
                "provider":  p,
                "created_at": _now(),
            },
        },
        upsert=True,
    )
    return {"provider": p, "old": old_date, "new": new_date, "changed": old_date != new_date}


def mongo_cutoff_filter(start_date: Optional[str]) -> Dict[str, Any]:
    """Translate a cutoff into a `$gte` Mongo clause.  Returns an
    empty dict when no cutoff is set (no-op filter)."""
    if not start_date:
        return {}
    return {"$gte": start_date}
