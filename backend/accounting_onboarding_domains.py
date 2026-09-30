"""Domain helpers; inventory catalogue is exposed through the onboarding API.

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
from counterparties_routes import _fuzzy_match, _norm
from shipping_companies import normalize_shipping_company
from payment_methods import normalize_payment_method

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
    entities, warnings = {}, []
    specs = {
        "employees": ("operating_salaries", {"category": "employee"}),
        "suppliers": ("suppliers", {}), "store_drivers": ("store_drivers", {}),
        "ad_accounts": ("counterparties", {"kind": "ad_account"}),
        "external_persons": ("counterparties", {"kind": "general"}),
    }
    for group, (collection, query) in specs.items():
        choices = []
        for row in await _rows(db, collection, owner, IDENTITY_FIELDS, {**ACTIVE, **query}):
            choice = _identity(row, collection)
            if not choice:
                warnings.append({"code": "identity_missing", "source": collection})
                continue
            if group == "external_persons":
                choice.update(phone=row.get("phone") or "", notes=row.get("notes") or "")
            if group == "ad_accounts":
                choice["ad_provider"] = row.get("ad_provider")
            choices.append(choice)
        entities[group] = choices
    # Canonical financial identities stay separate from counterparty profiles.
    # external_ref is untyped free text today: never assume it names a profile.
    entities.update(financial_accounts=[], banks=[], ad_financial_accounts=[])
    canonical = await _rows(db, "mz2_financial_accounts", owner, IDENTITY_FIELDS, {**ACTIVE, "status": "active"})
    identity_counts = {}
    for row in canonical:
        key = str(row.get("id") or "").strip()
        identity_counts[key] = identity_counts.get(key, 0) + 1
    rejected = set()
    for row in canonical:
        choice = _identity(row, "mz2_financial_accounts")
        if not choice:
            warnings.append({"code": "identity_missing", "source": "mz2_financial_accounts"})
            continue
        identity = choice["id"]
        if identity in rejected:
            continue
        legacy = await db.accounts.find_one({"user_id": owner, "id": identity}, {"_id": 1})
        if identity_counts[identity] != 1 or legacy:
            code = "bank_identity_ambiguous" if row.get("account_type") == "bank" else "financial_account_identity_ambiguous"
            warnings.append({"code": code, "id": identity})
            rejected.add(identity)
            continue
        choice.update({key: row.get(key) for key in ("currency", "account_type", "external_ref")})
        if row.get("account_type") in {"bank", "cash", "overdraft"}:
            entities["financial_accounts"].append(choice)
            if row["account_type"] == "bank":
                entities["banks"].append(choice)
        elif row.get("account_type") in {"ad_prepaid_wallet", "ad_payable"}:
            entities["ad_financial_accounts"].append(choice)
            warnings.append({"code": "ad_financial_account_mapping_unverified", "id": identity})
    providers = {}
    for collection in ("accounting_provider_bank_bindings_v2", "accounting_settlements_v2", "financial_provider_tax_invoices_v2"):
        for row in await _rows(db, collection, owner, {"provider": 1, "provider_id": 1}):
            key = str(row.get("provider") or row.get("provider_id") or "").removeprefix("payment:")
            if key in PROVIDERS:
                providers[key] = {"id": key, "entity_id": key, "name": PROVIDER_LABELS[key], "source": collection}
    couriers = {}
    settings = await db.settings.find_one({"user_id": owner}, {"_id": 0, "shipping_companies": 1, "payment_methods": 1}) or {}
    for row in settings.get("payment_methods") or []:
        if not isinstance(row, dict) or row.get("active") is False:
            continue
        sub_key, _, parent = normalize_payment_method(row.get("name") or "")
        key = parent or sub_key
        if key in PROVIDERS:
            providers.setdefault(key, {"id": key, "entity_id": key, "name": PROVIDER_LABELS[key], "source": "settings.payment_methods"})
    entities["payment_providers"] = list(providers.values())
    for row in settings.get("shipping_companies") or []:
        if not isinstance(row, dict) or row.get("active") is False:
            continue
        key, name = normalize_shipping_company(row.get("name"))
        if key not in {"unknown", "mandoob", "mandoob_riyadh", "pickup"}:
            couriers[key] = {"id": key, "entity_id": key, "name": name, "source": "settings.shipping_companies"}
    policy = await db.mz2_shipping_rate_policies.find_one({"_id": owner, "user_id": owner}, {"versions": 1}) or {}
    for row in policy.get("versions") or []:
        key = str(row.get("courier_id") or "").strip()
        if key and key not in {"mandoob", "mandoob_riyadh", "pickup"} and row.get("verification_status") == "approved":
            couriers.setdefault(key, {"id": key, "entity_id": key, "name": row.get("name") or key, "source": "mz2_shipping_rate_policies"})
    entities["couriers"] = list(couriers.values())
    return {"entities": entities, "warnings": warnings, "identity_only": True,
            "supported_payment_providers": [{"id": key, "name": PROVIDER_LABELS[key]} for key in PROVIDERS],
            "p02_status": "LOCKED"}


def _catalog_image(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _catalog_image(value.get("url") or value.get("original") or value.get("src"))
    if isinstance(value, list):
        return next((image for image in map(_catalog_image, value) if image), None)
    return None


def _catalog_options(value):
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        if any(key in value for key in ("name", "label", "title")):
            return [value]
        return [{"name": key, "value": item} for key, item in value.items()]
    return []


async def onboarding_inventory_catalog(db, owner):
    # Product V2 sync persists raw_salla.options; details refresh additionally
    # persists normalized options and variant.selections in this same V2 row.
    products = await _rows(db, "mezan_products_v2", owner,
        {key: 1 for key in ("mezan_product_id", "name", "sku", "barcode", "main_image",
                           "variants", "variants_count", "options", "options_count", "raw_salla")}, {"archived": {"$ne": True}})
    choices = []
    for product in products:
        if not product.get("mezan_product_id"):
            continue
        image = _catalog_image(product.get("main_image"))
        raw = product.get("raw_salla") if isinstance(product.get("raw_salla"), dict) else {}
        options = _catalog_options(product.get("options") or raw.get("options") or raw.get("product_options"))
        variants = []
        variant_rows = product.get("variants") or []
        if isinstance(variant_rows, dict):
            variant_rows = list(variant_rows.values())
        if not isinstance(variant_rows, list):
            variant_rows = []
        for variant in variant_rows:
            if not isinstance(variant, dict) or variant.get("id") is None:
                continue
            selections = variant.get("selections") or variant.get("options") or variant.get("values") or variant.get("attributes") or []
            selections = _catalog_options(selections)
            variants.append({"id": str(variant["id"]), "name": variant.get("name") or variant.get("sku") or str(variant["id"]),
                "sku": variant.get("sku"), "barcode": variant.get("barcode") or variant.get("gtin"),
                "options": selections, "image_url": _catalog_image(variant.get("image") or variant.get("image_url")) or image})
        choices.append({"id": product["mezan_product_id"], "product_v2_id": product["mezan_product_id"],
            "name": product.get("name"), "sku": product.get("sku"), "barcode": product.get("barcode"),
            "main_image": image, "image_url": image, "options": options,
            "variants_required": bool(product.get("variants") or product.get("variants_count") or options or product.get("options_count")), "variants": variants})
    resources = await _rows(db, "mezan_cost_resources_v2", owner,
        {key: 1 for key in ("id", "name", "code", "category_ids", "unit", "kind", "status", "track_inventory")},
        {**ACTIVE, "status": "active", "track_inventory": True, "kind": {"$ne": "service"}})
    components = [{key: row.get(key) for key in ("id", "name", "code", "category_ids", "unit", "kind", "status", "track_inventory")}
                  for row in resources if row.get("id")]
    categories = await _rows(db, "mezan_component_categories_v2", owner, {"id": 1, "name": 1})
    cabinets = {row["id"]: row for row in await _rows(db, "warehouse_locations_cabinets", owner, {"id": 1, "purpose": 1}) if row.get("id")}
    locations = await _rows(db, "warehouse_locations", owner,
        {key: 1 for key in ("id", "code", "warehouse_id", "cabinet_id", "purpose", "max_items", "barcode_value")},
        {"state": {"$ne": "disabled"}}, maximum=20000)
    # Both V1 and V2 call generate_location_rows and write these collections.
    # Neither persists producer identity: collection/number/barcode is no proof.
    locations = [{**row, "provenance": "AMBIGUOUS", "physical_approval_verified": False}
                 for row in locations if row.get("id") and row.get("warehouse_id")
                 and (row.get("purpose") or cabinets.get(row.get("cabinet_id"), {}).get("purpose")) == "permanent_storage"]
    warnings = [{"code": "inventory_account_mapping_requires_opening_contract"}]
    if locations:
        warnings.append({"code": "warehouse_location_provenance_ambiguous", "count": len(locations)})
    return {"products": choices, "components": components, "categories": categories, "locations": locations,
            "counts": {"products": len(choices), "components": len(components), "locations": len(locations)},
            "inventory_accounts": [], "warnings": warnings,
            "unit_conversion_supported": False, "physical_approval_verified": False, "read_only": True}


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
