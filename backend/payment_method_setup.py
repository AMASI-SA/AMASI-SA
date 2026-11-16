"""Read-only observed payment methods and explicit, scoped commission setup.

Discovery is not a fee rule, a payment confirmation, or a settlement provider.
Only an explicit save writes canonical ``settings.payment_methods`` terms.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import DuplicateKeyError, PyMongoError

from excel_parser import normalize_name, resolve_payment_setting
from payment_methods import (
    CARD_FALLBACK_SUB_KEYS,
    DEFAULT_PAYMENT_METHODS,
    KNOWN_PAYMENT_SUB_KEYS,
    normalize_payment_method,
)


MAX_OBSERVED_METHODS = 250
MAX_SETTINGS_METHODS = 250
MAX_METHOD_NAME_LENGTH = 200
DISCOVERY_MAX_TIME_MS = 2000
_FEE_FIELDS = ("commission_percent", "fixed_fee", "vat_percent")
_CENT = Decimal(".01")
_MAX_FIXED_FEE = Decimal("999999999999")


def payment_settings_revision(settings: dict | None) -> str:
    """Hash the actual array, preserving order, extra fields, and absence/null."""
    present = isinstance(settings, dict) and "payment_methods" in settings
    content = {"present": present}
    if present:
        content["payment_methods"] = settings["payment_methods"]
    encoded = json.dumps(content, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), default=str).encode("utf-8")
    return sha256(encoded).hexdigest()


class PaymentMethodCommissionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_METHOD_NAME_LENGTH)
    commission_percent: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    fixed_fee: Decimal = Field(ge=0, le=_MAX_FIXED_FEE, decimal_places=2, allow_inf_nan=False)
    vat_percent: Decimal = Field(ge=0, le=100, allow_inf_nan=False)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("name")
    @classmethod
    def meaningful_name(cls, value: str) -> str:
        if not normalize_payment_method(value)[0]:
            raise ValueError("payment_method_name_required")
        return value

    @field_validator(*_FEE_FIELDS, mode="before")
    @classmethod
    def explicit_number(cls, value):
        if value is None or isinstance(value, bool) or (isinstance(value, str) and not value.strip()):
            raise ValueError("payment_method_explicit_fee_required")
        return value


def _fail(code: str, status: int = 409) -> None:
    raise HTTPException(status, detail={"code": code})


def _owner_id(owner: str) -> str:
    if not isinstance(owner, str) or not owner.strip():
        _fail("payment_method_owner_required", 403)
    return owner


def _rows(settings: dict | None) -> list[dict] | None:
    # Reporting uses defaults only when the key is absent. An explicit empty
    # list or null has a different meaning and must not inherit these terms.
    rows = (settings or {}).get("payment_methods", DEFAULT_PAYMENT_METHODS)
    if not isinstance(rows, list) or len(rows) > MAX_SETTINGS_METHODS:
        return None
    if any(not isinstance(row, dict) or not isinstance(row.get("name"), str)
           or not normalize_payment_method(row["name"])[0] for row in rows):
        return None
    return rows


def _terms(row: dict | None) -> dict[str, float] | None:
    """Unknown/missing terms cannot inherit match_settings's legacy zero fallback."""
    if row is None:
        return None
    try:
        raw = [row.get(field) for field in _FEE_FIELDS]
        if any(value is None or isinstance(value, bool) for value in raw):
            return None
        rate, fixed, vat = (Decimal(str(value)) for value in raw)
        if (not all(value.is_finite() for value in (rate, fixed, vat))
                or not 0 <= rate <= 100 or not 0 <= vat <= 100
                or not 0 <= fixed <= _MAX_FIXED_FEE or fixed != fixed.quantize(_CENT)):
            return None
    except (InvalidOperation, TypeError, ValueError):
        return None
    return dict(zip(_FEE_FIELDS, (float(rate), float(fixed), float(vat))))


class _SettingsLookup:
    """Keep the calculator's exact-last/canonical-first precedence and detect ties."""
    def __init__(self, rows: list[dict]):
        self.exact: dict[str, dict] = {}
        self.canonical: dict[str, dict] = {}
        self.exact_groups: dict[str, list[dict]] = {}
        self.canonical_groups: dict[str, list[dict]] = {}
        for row in rows:
            key = normalize_name(row["name"])
            self.exact[key] = row
            self.exact_groups.setdefault(key, []).append(row)
            subkey, _label, _parent = normalize_payment_method(row["name"])
            if subkey in KNOWN_PAYMENT_SUB_KEYS:
                self.canonical.setdefault(subkey, row)
                self.canonical_groups.setdefault(subkey, []).append(row)

    def resolve(self, name: str) -> tuple[dict | None, bool]:
        selected = resolve_payment_setting(name, self.exact, self.canonical)
        key = normalize_name(name)
        subkey, _label, _parent = normalize_payment_method(name)
        if key in self.exact_groups:
            candidates = self.exact_groups[key]
        elif subkey in KNOWN_PAYMENT_SUB_KEYS:
            candidates = self.canonical_groups.get(subkey, [])
            if not candidates and subkey in CARD_FALLBACK_SUB_KEYS:
                candidates = self.canonical_groups.get("credit_card", [])
        else:
            candidates = [row for exact, rows in self.exact_groups.items()
                          if resolve_payment_setting(name, {exact: rows[-1]}, {}) is not None
                          for row in rows]
        return selected, len(candidates) > 1


async def _read_settings(db, owner: str) -> dict | None:
    documents = await db.settings.find(
        {"user_id": owner}, {"_id": 1, "user_id": 1, "payment_methods": 1},
    ).limit(2).to_list(2)
    if len(documents) > 1 or any(document.get("user_id") != owner for document in documents):
        _fail("payment_method_settings_owner_ambiguous")
    return documents[0] if documents else None


async def _observed_names(db, owner: str) -> tuple[list[str], bool]:
    """One bounded names-only owner query; no histories, counts, or writes."""
    try:
        rows = await db.unified_orders.aggregate([
            {"$match": {"user_id": owner, "payment_method": {"$type": "string", "$ne": ""}}},
            {"$group": {"_id": "$payment_method"}},
            {"$sort": {"_id": 1}},
            {"$limit": MAX_OBSERVED_METHODS + 1},
        ], maxTimeMS=DISCOVERY_MAX_TIME_MS, allowDiskUse=False).to_list(MAX_OBSERVED_METHODS + 1)
    except (PyMongoError, TimeoutError):
        # Availability is explicit; a failed read is never "no missing methods".
        return [], False
    complete = len(rows) <= MAX_OBSERVED_METHODS
    names: dict[str, str] = {}
    for row in rows[:MAX_OBSERVED_METHODS]:
        raw = row.get("_id")
        if not isinstance(raw, str):
            complete = False
            continue
        raw = raw.strip()
        if not normalize_payment_method(raw)[0]:
            continue
        if len(raw) > MAX_METHOD_NAME_LENGTH:
            complete = False
            continue
        key = normalize_name(raw)
        # Choosing the same representative does not depend on query iteration.
        names[key] = min(names.get(key, raw), raw)
    return [names[key] for key in sorted(names)], complete


def _directory(names: list[str], settings: dict | None, complete: bool, *, settings_available: bool = True) -> dict[str, Any]:
    """Completeness describes read integrity; conflicting fees lock only their row."""
    # Defaults describe established absence, never a failed/foreign settings
    # read. Unknown merchant settings must leave even known rails unpriced.
    rows = _rows(settings) if settings_available else None
    complete = complete and rows is not None
    lookup = _SettingsLookup(rows or [])
    items = []
    for name in names:
        selected, ambiguous = lookup.resolve(name)
        configured = None if ambiguous else _terms(selected)
        exact = selected is None or normalize_name(selected["name"]) == normalize_name(name)
        items.append({
            "key": sha256(normalize_name(name).encode("utf-8")).hexdigest(),
            "name": name,
            "status": "configured" if configured is not None else "needs_commission",
            **(configured or dict.fromkeys(_FEE_FIELDS)),
            "editable": rows is not None and exact and not ambiguous,
        })
    if not complete:
        for item in items:
            item["editable"] = False
    return {"items": items, "pending_count": sum(row["status"] == "needs_commission" for row in items),
            "complete": complete, "revision": payment_settings_revision(settings)}


async def load_payment_method_setup(db, owner, *, settings=None) -> dict:
    owner = _owner_id(owner)
    settings_complete = True
    if settings is None:
        try:
            settings = await _read_settings(db, owner)
        except (PyMongoError, TimeoutError, HTTPException):
            settings, settings_complete = None, False
    elif not isinstance(settings, dict) or settings.get("user_id", owner) != owner:
        settings, settings_complete = None, False
    names, names_complete = await _observed_names(db, owner)
    return _directory(names, settings, names_complete and settings_complete,
                      settings_available=settings_complete)


async def save_payment_method_commission(db, owner, actor_id, payload: PaymentMethodCommissionIn) -> dict:
    """The route supplies fresh owner authorization; this boundary scopes and CASes the write."""
    owner = _owner_id(owner)
    if not isinstance(actor_id, str) or not actor_id.strip():
        _fail("payment_method_actor_required", 403)
    current = await _read_settings(db, owner)
    if payload.expected_revision != payment_settings_revision(current):
        _fail("payment_method_settings_revision_conflict")
    rows = _rows(current)
    if rows is None:
        _fail("payment_method_settings_invalid")
    names, complete = await _observed_names(db, owner)
    if not complete:
        _fail("payment_method_discovery_incomplete", 503)
    observed = next((name for name in names if normalize_name(name) == normalize_name(payload.name)), None)
    if observed is None:
        _fail("payment_method_not_observed", 404)
    selected, ambiguous = _SettingsLookup(rows).resolve(observed)
    if ambiguous or (selected is not None and normalize_name(selected["name"]) != normalize_name(observed)):
        _fail("payment_method_settings_alias_conflict")
    values = {field: float(getattr(payload, field)) for field in _FEE_FIELDS}
    updated = deepcopy(rows)
    if selected is None:
        if len(updated) >= MAX_SETTINGS_METHODS:
            _fail("payment_method_settings_limit_reached")
        updated.append({"name": observed, **values})
    else:
        index = next(index for index, row in enumerate(rows) if row is selected)
        updated[index] = {**updated[index], **values}
    changes = {"payment_methods": updated,
               "updated_at": datetime.now(timezone.utc).isoformat(),
               "payment_method_setup_updated_by": actor_id}
    try:
        if current is None:
            # settings.user_id has a unique index. A concurrent first save loses
            # that claim instead of overwriting another owner's initial terms.
            await db.settings.insert_one({"user_id": owner, **changes})
        else:
            selector = {"user_id": owner, "_id": current["_id"],
                        "payment_methods": current["payment_methods"] if "payment_methods" in current else {"$exists": False}}
            result = await db.settings.update_one(selector, {"$set": changes}, upsert=False)
            if result.matched_count != 1:
                _fail("payment_method_settings_revision_conflict")
    except DuplicateKeyError:
        _fail("payment_method_settings_revision_conflict")
    return {"payment_method_setup": await load_payment_method_setup(db, owner)}
