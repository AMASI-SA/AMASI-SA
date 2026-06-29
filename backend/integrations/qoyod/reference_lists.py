"""Iter-290i — Reference-Lists fetcher + cache for Qoyod pickers.

Why this exists
───────────────
Operators were typing Qoyod numeric ids (category_id=1, account_id=17,
unit_type_id=6, …) by hand into the settings page, which meant opening
قيود in a second tab, searching for the right resource, copying the id
back. This module powers a name-first picker UX:

  1. `refresh_reference_lists(db, user_id)` fetches every list from
     قيود and stores a normalised `{id, name, raw}` array under
     `qoyod_reference_lists` (one document per tenant).

  2. `get_reference_lists(db, user_id)` reads back the cached lists
     for the UI to render dropdowns.

The lists are CACHED so the operator doesn't pay a Qoyod round-trip
every time the settings page renders. They click a "Refresh" button
to repopulate.

Strictly READ-ONLY against قيود. No POST/PUT/DELETE.

Cached document shape
─────────────────────
    {
      "user_id":     "<tenant>",
      "updated_at":  "<ISO-8601 UTC>",
      "lists": {
        "categories":   [{"id": "1", "name": "..."}, ...],
        "unit_types":   [...],
        "inventories":  [...],
        "accounts":     [...],
        "taxes":        [...],
        "branches":     [...],
        "customers":    [{"id": "...", "name": "...", "phone": "..."}, ...]
      },
      "fetch_errors": {"taxes": "...", ...}   # may be empty
    }
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Optional

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.credentials import get_api_key


# ─── Normaliser helpers ──────────────────────────────────────────────
def _safe_name(item: dict, *fallback_keys: str) -> str:
    """قيود's lists use different keys for the human label across
    resources (`name`, `name_ar`, `title`, …). Try them in order
    until one yields a non-empty string."""
    for k in ("name_ar", "name", "title", "label", *fallback_keys):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # Some resources nest the name inside `attributes`.
    attrs = item.get("attributes") or {}
    if isinstance(attrs, dict):
        for k in ("name_ar", "name", "title"):
            v = attrs.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return f"#{item.get('id', '?')}"


def _safe_id(item: dict) -> str:
    """Always coerce to string so the UI can match against settings
    values that may have been stored as either str or int."""
    raw_id = item.get("id")
    if raw_id is None:
        return ""
    return str(raw_id)


def _pluralise_candidates(root: str) -> list[str]:
    """Generate every reasonable English-plural variant of `root` so
    the normaliser can extract list items regardless of how قيود
    wraps them (`product_category` vs `product_categories`,
    `inventory` vs `inventories`, `tax` vs `taxes`, …)."""
    out = [root]
    if root.endswith("y"):
        out.append(root[:-1] + "ies")
    if root.endswith(("s", "x", "ch", "sh")):
        out.append(root + "es")
    out.append(root + "s")
    return out


def _normalise_list(payload: Any, *, root_key: str,
                    extra_fields: Optional[list[str]] = None) -> list[dict]:
    """Qoyod responses can be either `{"<root>": [...]}` or a bare
    list. Coerce to a flat list of `{id, name, ...extras}` dicts.

    Returns `[]` for any unexpected shape — the picker handles empty
    lists gracefully."""
    items: list[Any] = []
    if isinstance(payload, dict):
        for candidate_key in (*_pluralise_candidates(root_key), "data"):
            value = payload.get(candidate_key)
            if isinstance(value, list):
                items = value
                break
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = {"id": _safe_id(item), "name": _safe_name(item)}
        if extra_fields:
            for f in extra_fields:
                if f in item:
                    row[f] = item[f]
        if row["id"]:
            out.append(row)
    return out


# ─── The fetch contract ──────────────────────────────────────────────
# Each entry: (list_key, api_method_name, root_key_in_response, extra_fields)
_LIST_SPECS = [
    ("categories",  "list_product_categories", "product_category", None),
    ("unit_types",  "list_product_units",      "product_unit",     None),
    ("inventories", "list_inventories",        "inventory",        None),
    ("accounts",    "list_accounts",           "account",
     ["type", "kind", "code"]),
    ("taxes",       "list_taxes",              "tax",
     ["percent", "rate"]),
    ("branches",    "list_branches",           "branch",           None),
    ("customers",   "list_contacts",           "customer",
     ["phone", "email"]),
]


async def refresh_reference_lists(
    db, *, user_id: str,
    client_factory: Callable[[str], Any] = QoyodAPIClient,
) -> dict:
    """Pull every reference list from Qoyod and overwrite the
    tenant's cached document. Returns the same shape as
    `get_reference_lists`. Strictly READ-ONLY against Qoyod.

    `client_factory` is a seam for unit tests; production passes the
    default `QoyodAPIClient` constructor.
    """
    api_key = await get_api_key(db, user_id)
    if not api_key:
        return {
            "ok": False,
            "code": "qoyod_api_key_missing",
            "message": "مفتاح API قيود غير مُهيّأ في الإعدادات.",
        }

    api = client_factory(api_key)

    lists: dict[str, list[dict]] = {}
    fetch_errors: dict[str, dict] = {}
    for list_key, method_name, root_key, extra_fields in _LIST_SPECS:
        fetcher = getattr(api, method_name, None)
        if fetcher is None:
            fetch_errors[list_key] = {
                "code": "method_missing",
                "message": (f"api_client lacks `{method_name}` — "
                            "skipping list."),
            }
            lists[list_key] = []
            continue
        try:
            # `list_contacts` is paginated — pull a generous first
            # page; operators with >50 customers get the most recent
            # ones (good enough for the default-customer picker).
            if method_name == "list_contacts":
                raw = await fetcher(page=1, limit=200)
            else:
                raw = await fetcher()
        except QoyodAPIError as exc:
            fetch_errors[list_key] = exc.to_log_dict()
            lists[list_key] = []
            continue
        except Exception as exc:  # noqa: BLE001
            fetch_errors[list_key] = {
                "code": "unexpected_error",
                "message": str(exc)[:300],
            }
            lists[list_key] = []
            continue
        lists[list_key] = _normalise_list(
            raw, root_key=root_key, extra_fields=extra_fields)

    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        "user_id":      user_id,
        "updated_at":   now_iso,
        "lists":        lists,
        "fetch_errors": fetch_errors or None,
    }
    await db.qoyod_reference_lists.update_one(
        {"user_id": user_id},
        {"$set": doc},
        upsert=True,
    )
    return {"ok": True, **doc}


async def get_reference_lists(db, *, user_id: str) -> dict:
    """Return the last cached document. If none exists yet, return an
    `ok: true` envelope with empty lists so the UI can still render
    (and prompt the operator to click Refresh)."""
    doc = await db.qoyod_reference_lists.find_one(
        {"user_id": user_id}, {"_id": 0})
    if not doc:
        return {
            "ok":          True,
            "user_id":     user_id,
            "updated_at":  None,
            "lists":       {key: [] for key, *_ in _LIST_SPECS},
            "fetch_errors": None,
            "cached":      False,
        }
    return {"ok": True, "cached": True, **doc}
