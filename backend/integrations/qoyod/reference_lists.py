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


# Each entry: (list_key, api_method, candidate_root_keys, extra_fields)
# `candidate_root_keys` — Qoyod's response wrapper key varies by
# resource (and sometimes between accounts even within one tenant).
# We probe in order until one yields a list.
_LIST_SPECS = [
    ("categories",  "list_product_categories",
     ["product_categories", "product_category", "categories", "category"],
     None),
    ("unit_types",  "list_product_units",
     ["product_units", "product_unit", "units", "unit", "unit_types"],
     None),
    ("inventories", "list_inventories",
     ["inventories", "inventory"], None),
    ("accounts",    "list_accounts",
     ["accounts", "account"],
     ["type", "kind", "code"]),
    ("taxes",       "list_taxes",
     ["taxes", "tax"],
     ["percent", "rate"]),
    ("branches",    "list_branches",
     ["branches", "branch"], None),
    ("customers",   "list_contacts",
     ["customers", "customer", "contacts", "contact"],
     ["phone", "email"]),
]


def _normalise_with_candidates(payload: Any, candidate_keys: list[str],
                               extra_fields: Optional[list[str]] = None
                               ) -> tuple[list[dict], Optional[str]]:
    """Try each candidate key in order. Returns `(rows, used_key)`.
    `used_key` is None if the payload was a bare list or no key matched."""
    items: list[Any] = []
    used_key: Optional[str] = None
    if isinstance(payload, dict):
        for candidate_key in (*candidate_keys, "data"):
            value = payload.get(candidate_key)
            if isinstance(value, list):
                items = value
                used_key = candidate_key
                break
    elif isinstance(payload, list):
        items = payload
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
    return out, used_key


# Kept for backward compatibility with existing tests.
def _normalise_list(payload: Any, *, root_key: str,
                    extra_fields: Optional[list[str]] = None) -> list[dict]:
    candidates = _pluralise_candidates(root_key)
    out, _ = _normalise_with_candidates(payload, candidates, extra_fields)
    return out


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
    fetch_diagnostics: dict[str, dict] = {}
    for list_key, method_name, candidate_keys, extra_fields in _LIST_SPECS:
        diag: dict = {
            "method":            method_name,
            "candidate_keys":    candidate_keys,
            "status":            "skipped",
            "count":             0,
            "used_response_key": None,
            "sample_keys":       [],
            "error":             None,
        }
        fetcher = getattr(api, method_name, None)
        if fetcher is None:
            fetch_errors[list_key] = {
                "code": "method_missing",
                "message": (f"api_client lacks `{method_name}` — "
                            "skipping list."),
            }
            lists[list_key] = []
            diag["status"] = "fail"
            diag["error"] = "method_missing"
            fetch_diagnostics[list_key] = diag
            continue
        try:
            if method_name == "list_contacts":
                raw = await fetcher(page=1, limit=200)
            else:
                raw = await fetcher()
        except QoyodAPIError as exc:
            err = exc.to_log_dict()
            fetch_errors[list_key] = err
            lists[list_key] = []
            diag["status"] = "fail"
            diag["error"] = err
            fetch_diagnostics[list_key] = diag
            continue
        except Exception as exc:  # noqa: BLE001
            err = {"code": "unexpected_error",
                   "message": str(exc)[:300]}
            fetch_errors[list_key] = err
            lists[list_key] = []
            diag["status"] = "fail"
            diag["error"] = err
            fetch_diagnostics[list_key] = diag
            continue
        rows, used_key = _normalise_with_candidates(
            raw, candidate_keys, extra_fields)
        lists[list_key] = rows
        diag["count"] = len(rows)
        diag["used_response_key"] = used_key
        # Sample-keys lets the operator see what قيود actually returned
        # when a list lands empty — invaluable for parser bugs.
        if isinstance(raw, dict):
            diag["sample_keys"] = sorted(list(raw.keys()))[:10]
        elif isinstance(raw, list) and raw and isinstance(raw[0], dict):
            diag["sample_keys"] = sorted(list(raw[0].keys()))[:10]
        if rows:
            diag["status"] = "success"
        else:
            # Endpoint responded OK but no rows — flag as `empty` so
            # the UI doesn't say "fetch failed" (it didn't).
            diag["status"] = "empty"
            if used_key is None and isinstance(raw, dict):
                fetch_errors[list_key] = {
                    "code": "no_matching_key_in_response",
                    "message": (
                        f"رد قيود لا يحتوي أيّاً من المفاتيح المتوقعة "
                        f"({', '.join(candidate_keys)}). المفاتيح الموجودة: "
                        f"{', '.join(diag['sample_keys']) or '∅'}"),
                }
                diag["status"] = "parse_failed"
        fetch_diagnostics[list_key] = diag

    now_iso = datetime.now(timezone.utc).isoformat()
    doc = {
        "user_id":           user_id,
        "updated_at":        now_iso,
        "lists":             lists,
        "fetch_errors":      fetch_errors or None,
        "fetch_diagnostics": fetch_diagnostics,
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
            "fetch_errors":      None,
            "fetch_diagnostics": {},
            "cached":      False,
        }
    return {"ok": True, "cached": True, **doc}
