"""Native domain discovery and external-person setup helpers.

Callers must enforce fresh actor permissions and supply the resolved owner.
Discovery/catalog functions are read-only. External person creation writes only
native external-person metadata; no opening, ledger or activation writes.
"""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from accounting_settlement_service import PROVIDERS, PROVIDER_LABELS
from component_status_policy import component_is_active
from accounting_onboarding_identities import identities
import re

ACTIVE = {"status": {"$nin": ["inactive", "archived", "deleted", "hidden"]},
          "active": {"$ne": False}, "is_active": {"$ne": False},
          "archived": {"$ne": True}, "is_archived": {"$ne": True},
          "deleted": {"$ne": True}, "is_deleted": {"$ne": True}}


async def _rows(db, collection, owner, projection, query=None, maximum=10000):
    rows = await db[collection].find({**(query or {}), "user_id": owner},
                                    {"_id": 0, **projection}).to_list(maximum + 1)
    if len(rows) > maximum:
        raise HTTPException(409, detail={"code": "onboarding_scope_too_large", "source": collection})
    return rows


IDENTITY_FIELDS = {key: 1 for key in ("id", "employee_id", "name", "employee_name", "company_name", "phone", "notes", "ad_provider", "currency", "account_type", "external_ref")}


def _identity(row, source):
    identity = str(row.get("id") or row.get("employee_id") or "").strip()
    if not identity:
        return None
    return {"id": identity, "entity_id": identity,
            "name": str(row.get("company_name") or row.get("name") or row.get("employee_name") or identity),
            "source": source}


async def onboarding_domains(db, owner):
    entities = {}
    for kind, group in (("employee", "employees"), ("supplier", "suppliers"),
                        ("store_driver", "store_drivers"), ("ad_account", "ad_accounts"),
                        ("external_person", "external_persons"), ("courier", "couriers"),
                        ("provider", "payment_providers")):
        entities[group] = [{**row, "entity_id": row["id"], "name": row["label"]}
                           for row in await identities(db, owner, kind)]
    warnings = []
    entities.update(financial_accounts=[], banks=[], ad_financial_accounts=[])
    rows = await _rows(db, "mz2_financial_accounts", owner, IDENTITY_FIELDS,
                       {**ACTIVE, "status": "active"})
    counts = {}
    for row in rows:
        counts[row.get("id")] = counts.get(row.get("id"), 0) + 1
    rejected = set()
    for row in rows:
        key = row.get("id")
        if not key or counts[key] != 1:
            if key not in rejected:
                warnings.append({"code": "financial_account_identity_ambiguous", "id": key})
                rejected.add(key)
            continue
        choice = {**_identity(row, "mz2_financial_accounts"),
                  **{field: row.get(field) for field in ("currency", "account_type", "external_ref")}}
        if row.get("account_type") in {"bank", "cash", "overdraft"}:
            entities["financial_accounts"].append(choice)
            if row["account_type"] == "bank":
                entities["banks"].append(choice)
        elif row.get("account_type") in {"ad_prepaid_wallet", "ad_payable"}:
            entities["ad_financial_accounts"].append(choice)
            warnings.append({"code": "onboarding_native_ad_binding_dependency", "id": key})
    return {"entities": entities, "warnings": warnings, "identity_only": True,
            "supported_payment_providers": [{"id": key, "name": PROVIDER_LABELS[key]} for key in PROVIDERS],
            "p02_status": "LOCKED"}


async def onboarding_inventory_catalog(db, owner):
    products = await _rows(db, "mezan_products_v2", owner,
        {key: 1 for key in ("mezan_product_id", "name", "sku", "variants", "variants_count", "options")}, {"archived": {"$ne": True}})
    choices = [{"id": p["mezan_product_id"], "name": p.get("name"), "sku": p.get("sku"),
        "options": p.get("options") or [], "variants_required": bool(p.get("variants") or p.get("variants_count")),
        "variants": [{"id": str(v["id"]), "name": v.get("name") or v.get("sku") or str(v["id"]),
                      "sku": v.get("sku"), "options": v.get("options") or []}
                     for v in p.get("variants") or [] if isinstance(v, dict) and v.get("id")]}
        for p in products if p.get("mezan_product_id")]
    resources = await _rows(db, "mezan_cost_resources_v2", owner,
        {key: 1 for key in ("id", "name", "code", "category_ids", "unit", "kind", "status", "is_active", "archived")}, {"track_inventory": True})
    components = [{key: row.get(key) for key in ("id", "name", "code", "category_ids", "unit")}
                  for row in resources if row.get("id") and row.get("kind") != "service" and component_is_active(row)]
    categories = await _rows(db, "mezan_component_categories_v2", owner, {"id": 1, "name": 1})
    cabinets = {row["id"]: row for row in await _rows(db, "warehouse_locations_cabinets", owner, {"id": 1, "purpose": 1}) if row.get("id")}
    locations = await _rows(db, "warehouse_locations", owner,
        {key: 1 for key in ("id", "code", "warehouse_id", "cabinet_id", "purpose", "max_items", "barcode_value")},
        {"state": {"$ne": "disabled"}}, maximum=20000)
    locations = [row for row in locations if row.get("id") and row.get("warehouse_id")
                 and (row.get("purpose") or cabinets.get(row.get("cabinet_id"), {}).get("purpose")) == "permanent_storage"]
    return {"products": choices, "components": components, "categories": categories, "locations": locations,
            "inventory_accounts": [], "warnings": [{"code": "inventory_account_mapping_requires_opening_contract"}],
            "unit_conversion_supported": False, "read_only": True}


class ExternalPersonIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=160)
    phone: str = Field(default="", max_length=40)
    notes: str = Field(default="", max_length=500)
    force: bool = False
    reference: str = Field(default="", max_length=160)
    person_type: str = Field(default="person", pattern=r"^(person|organization)$")


async def create_external_person(db, owner, payload, actor_id=None):
    # A normalized name prevents duplicate setup records only; financial identity
    # is the generated V2 ID, never the name or a legacy contact.
    normalized = re.sub(r"\s+", " ", payload.name).casefold()
    existing = await _rows(db, "mz2_external_persons_v2", owner,
                           {"id": 1, "display_name": 1, "name_lower": 1})
    if any(row.get("name_lower") == normalized for row in existing):
        raise HTTPException(409, detail={"code": "duplicate"})
    stamp = datetime.now(timezone.utc).isoformat()
    actor_id = actor_id or owner
    row = {"id": str(uuid4()), "user_id": owner, "kind": "external_person",
           "display_name": payload.name, "name": payload.name, "name_lower": normalized,
           "reference": payload.reference, "person_type": payload.person_type,
           "phone": payload.phone, "notes": payload.notes, "status": "active", "version": 1,
           "created_at": stamp, "created_by": actor_id, "updated_at": stamp, "updated_by": actor_id,
           "audit": [{"action": "create", "actor_id": actor_id, "at": stamp, "version": 1}]}
    try:
        await db.mz2_external_persons_v2.insert_one(row)
    except DuplicateKeyError as exc:
        raise HTTPException(409, detail={"code": "duplicate"}) from exc
    return {"entity_id": row["id"], **{key: value for key, value in row.items() if key not in {"_id", "user_id", "name_lower"}}}


async def ensure_external_person_indexes(db):
    await db.mz2_external_persons_v2.create_index([("user_id", 1), ("id", 1)], unique=True)
    await db.mz2_external_persons_v2.create_index([("user_id", 1), ("name_lower", 1)], unique=True)
