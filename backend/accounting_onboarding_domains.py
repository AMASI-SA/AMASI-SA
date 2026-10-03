"""Domain helpers; inventory catalogue is exposed through the onboarding API.

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


def _catalog_variant_id(row):
    value = row.get("id") if isinstance(row, dict) else None
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


def _catalog_count(value):
    return int(value) if isinstance(value, (str, int)) and str(value).isdigit() else 0


def _catalog_source_time(value):
    # Mongo returns stored UTC datetimes without tzinfo by default. External
    # strings, however, must identify their timezone before they can order sources.
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
        except ValueError:
            pass
    return None


def _catalog_provenance(product):
    """Resolve retained detail/light snapshots for this read, never repair history."""
    details, light = product.get("raw_salla_details"), product.get("raw_salla")
    if not isinstance(details, dict) or not isinstance(light, dict) or details == light:
        return product, False
    # Arrival/sync time does not prove a newer source revision: an old response
    # can arrive last. Unordered contradictory source evidence stays fail closed.
    times = [_catalog_source_time(raw.get("updated_at") or raw.get("date_updated"))
             for raw in (details, light)]
    selected = None
    if all(times) and times[0] != times[1]:
        selected = details if times[0] > times[1] else light
    else:
        fields = ("options", "product_options", "variants", "skus", "product_variants")
        if {k: details[k] for k in fields if k in details} == {k: light[k] for k in fields if k in light}:
            return product, False

    if selected is None:
        # Unordered contradictory evidence must not prove customization-only or
        # make a retained variant selectable. Preserve option evidence for the UI.
        options = [*_catalog_options(details.get("options") or details.get("product_options")),
                   *_catalog_options(light.get("options") or light.get("product_options"))]
        required = bool(options or product.get("options") or _catalog_count(product.get("options_count"))
                        or product.get("variants") or _catalog_count(product.get("variants_count"))
                        or any(raw.get(k) for raw in (details, light)
                               for k in ("variants", "skus", "product_variants")))
        return {**product, "options": options or product.get("options"), "variants": [],
                "variants_count": 0}, required

    # Do not combine the winning options with stale normalized variants. Missing
    # combinations in the winning snapshot remain missing, not an empty proof.
    other = light if selected is details else details
    prior_options = _catalog_options(other.get("options") or other.get("product_options"))
    options = _catalog_options(selected.get("options") or selected.get("product_options"))
    option_sources = [selected[k] for k in ("options", "product_options") if k in selected]
    valid_options = bool(option_sources) and all(isinstance(value, list) and all(isinstance(row, dict) for row in value)
                                                for value in option_sources)
    if not valid_options:
        options = prior_options or _catalog_options(product.get("options"))
    variant_sources = [selected[k] for k in ("variants", "skus", "product_variants") if k in selected]
    rows = next((value for value in variant_sources if value), [])
    prior_evidence = bool(prior_options or product.get("options") or _catalog_count(product.get("options_count"))
                          or product.get("variants") or _catalog_count(product.get("variants_count"))
                          or any(other.get(k) for k in ("variants", "skus", "product_variants")))
    # Missing fields in a partial payload do not prove that older stock vanished.
    valid_variants = bool(variant_sources) and all(isinstance(value, list) for value in variant_sources)
    incomplete_removal = (not valid_variants or not valid_options) and prior_evidence
    return {**product, "options": options,
            "options_count": len(options) if valid_options else max(len(options), _catalog_count(product.get("options_count"))),
            "variants": rows, "variants_count": len(rows) if isinstance(rows, (list, dict)) else 0,
            "raw_salla": selected, "raw_salla_details": selected}, incomplete_removal


def _empty_customization_variant_source(product, options):
    # Both sync producers default absent variants to [] / 0. Only retained
    # source data can distinguish that default from an explicitly empty source.
    # _catalog_provenance resolves competing snapshots before this proof is used.
    details = product.get("raw_salla_details")
    raw = details if isinstance(details, dict) else product.get("raw_salla")
    if not isinstance(raw, dict):
        return False
    sources = [raw[key] for key in ("variants", "skus", "product_variants") if key in raw]
    source_options = _catalog_options(raw.get("options") or raw.get("product_options"))
    return (bool(sources) and all(isinstance(value, list) and not value for value in sources)
            and bool(options) and len(source_options) == len(options)
            and _catalog_count(product.get("options_count")) <= len(options)
            and all(option.get("type") == "text" for option in [*options, *source_options]))


async def onboarding_inventory_catalog(db, owner):
    # Product V2 sync persists raw_salla.options; details refresh additionally
    # persists normalized options and variant.selections in this same V2 row.
    products = await _rows(db, "mezan_products_v2", owner,
        {key: 1 for key in ("mezan_product_id", "name", "sku", "barcode", "main_image",
                           "variants", "variants_count", "options", "options_count", "raw_salla", "raw_salla_details")}, {"archived": {"$ne": True}})
    choices, variant_warnings = [], []
    for product in products:
        if not product.get("mezan_product_id"):
            continue
        product, source_uncertain = _catalog_provenance(product)
        image = _catalog_image(product.get("main_image"))
        raw = product.get("raw_salla") if isinstance(product.get("raw_salla"), dict) else {}
        options = _catalog_options(product.get("options") or raw.get("options") or raw.get("product_options"))
        variants = []
        variant_rows = product.get("variants") or []
        if isinstance(variant_rows, dict):
            variant_rows = list(variant_rows.values())
        if not isinstance(variant_rows, list):
            variant_rows = []
        variant_counts = {}
        for row in variant_rows:
            key = _catalog_variant_id(row)
            variant_counts[key] = variant_counts.get(key, 0) + 1
        for variant in variant_rows:
            variant_id = _catalog_variant_id(variant)
            if not variant_id or variant_counts[variant_id] != 1:
                continue
            selections = variant.get("selections") or variant.get("options") or variant.get("values") or variant.get("attributes") or []
            selections = _catalog_options(selections)
            variants.append({"id": variant_id, "name": variant.get("name") or variant.get("sku") or variant_id,
                "sku": variant.get("sku"), "barcode": variant.get("barcode") or variant.get("gtin"),
                "options": selections, "image_url": _catalog_image(variant.get("image") or variant.get("image_url")) or image})
        declared_count = _catalog_count(product.get("variants_count"))
        unresolved = max(len(variant_rows), declared_count) - len(variants)
        if unresolved:
            variant_warnings.append({"code": "inventory_variant_identity_unresolved", "product_v2_id": product["mezan_product_id"], "count": unresolved})
        options_present = bool(options or _catalog_count(product.get("options_count")))
        empty_customization_source = not source_uncertain and _empty_customization_variant_source(product, options)
        variants_required = bool(source_uncertain or variant_rows or declared_count or product.get("variants")
                                 or (options_present and not empty_customization_source))
        source_missing = variants_required and not variant_rows
        if source_missing:
            variant_warnings.append({"code": "inventory_variant_source_missing", "product_v2_id": product["mezan_product_id"]})
        choices.append({"id": product["mezan_product_id"], "product_v2_id": product["mezan_product_id"],
            "name": product.get("name"), "sku": product.get("sku"), "barcode": product.get("barcode"),
            "main_image": image, "image_url": image, "options": options,
            "variants_required": variants_required,
            "variants_source_available": bool(variant_rows or empty_customization_source),
            "variants_source_missing": source_missing,
            "unresolved_variants_count": unresolved, "variants": variants})
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
    warnings = [{"code": "inventory_account_mapping_requires_opening_contract"}, *variant_warnings]
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
