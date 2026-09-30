"""Unrouted domain helpers pending the Track A API contract.

Callers must enforce fresh actor permissions and supply the resolved owner.
Discovery/catalog functions are read-only. External person creation writes only
counterparty contact metadata; no opening, ledger or activation writes.
"""
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from accounting_settlement_service import PROVIDERS, PROVIDER_LABELS
from component_status_policy import component_is_active
from counterparties_routes import _fuzzy_match, _norm

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
    from accounting_onboarding_identities import identities
    from accounting_onboarding_source_gaps import onboarding_source_gaps
    entities = {}
    for group, kind in (("employees", "employee"), ("suppliers", "supplier"),
                        ("external_persons", "external_person"), ("store_drivers", "store_driver"),
                        ("ad_accounts", "ad_account"), ("couriers", "courier"),
                        ("payment_providers", "provider")):
        entities[group] = [{**row, "entity_id": row["id"], "name": row["label"]}
                           for row in await identities(db, owner, kind)]
    diagnostic = await onboarding_source_gaps(db, owner)
    eligible = {row["id"] for row in diagnostic["items"] if row["selectable"]}
    canonical = await _rows(db, "mz2_financial_accounts", owner, IDENTITY_FIELDS, {**ACTIVE, "status": "active"})
    entities.update(financial_accounts=[], banks=[], ad_financial_accounts=[])
    for row in canonical:
        if row.get("id") not in eligible:
            continue
        choice = {**_identity(row, "mz2_financial_accounts"), **{key: row.get(key) for key in ("currency", "account_type", "external_ref")}}
        if row.get("account_type") in {"bank", "cash", "overdraft"}:
            entities["financial_accounts"].append(choice)
            if row["account_type"] == "bank":
                entities["banks"].append(choice)
        elif row.get("account_type") in {"ad_prepaid_wallet", "ad_payable"}:
            entities["ad_financial_accounts"].append(choice)
    return {"entities": entities, "warnings": diagnostic["gaps"], "source_gaps": diagnostic,
            "identity_only": True,
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


async def create_external_person(db, owner, payload):
    existing = await _rows(db, "counterparties", owner, IDENTITY_FIELDS, {"kind": "general"})
    match = next((row for row in existing if _norm(row.get("name")) == _norm(payload.name)), None)
    similar = match or (None if payload.force else _fuzzy_match(payload.name, existing))
    if similar:
        raise HTTPException(409, detail={"code": "duplicate" if match else "similar_name_exists", "existing": similar})
    stamp = datetime.now(timezone.utc).isoformat()
    row = {"id": str(uuid4()), "user_id": owner, "kind": "general", "name": payload.name,
           "name_lower": _norm(payload.name), "phone": payload.phone, "notes": payload.notes,
           "ad_provider": None, "created_at": stamp, "updated_at": stamp}
    try:
        await db.counterparties.insert_one(row)
    except DuplicateKeyError as exc:
        raise HTTPException(409, detail={"code": "duplicate"}) from exc
    return {**_identity(row, "counterparties"), "phone": row["phone"], "notes": row["notes"]}
