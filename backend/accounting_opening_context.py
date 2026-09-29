"""Owner-scoped identities for the existing V2 opening workflow; no postings."""
from fastapi import HTTPException

from accounting_settlement_service import PROVIDERS, PROVIDER_LABELS
from supplier_identity_service import require_linked_supplier
from shipping_companies import normalize_shipping_company


ACTIVE = {"status": {"$nin": ["inactive", "archived", "deleted", "hidden"]},
          "archived": {"$ne": True}, "is_archived": {"$ne": True},
          "deleted": {"$ne": True}, "is_deleted": {"$ne": True}}
EMPLOYEE_CATEGORIES = ("employee_salary_payable", "employee_advance", "employee_custody")
COURIER_CATEGORIES = ("courier_cod_receivable", "courier_payable")
DRIVER_CATEGORIES = ("store_driver_cod_receivable", "store_driver_fee_payable")
ENTITY_CATEGORIES = frozenset(("provider_receivable", "supplier_payable", "customer_receivable",
                               *EMPLOYEE_CATEGORIES, *COURIER_CATEGORIES, *DRIVER_CATEGORIES))


async def _rows(db, collection, owner, query=None):
    rows = await db[collection].find({"user_id": owner, **ACTIVE, **(query or {})}, {
        "_id": 0, "id": 1, "employee_id": 1, "name": 1, "company_name": 1,
        "status": 1, "kind": 1, "account_type": 1, "currency": 1, "version": 1,
    }).to_list(1001)
    if len(rows) > 1000:
        raise HTTPException(409, detail={"code": "opening_entity_scope_too_large"})
    return rows


def _identity(row, source):
    identity = str(row.get("id") or row.get("employee_id") or "").strip()
    if not identity:
        raise HTTPException(409, detail={"code": "opening_entity_identity_missing"})
    return {"id": identity, "name": str(row.get("company_name") or row.get("name") or identity),
            "source": source, "status": str(row.get("status") or "active")}


async def canonical_opening_bank(db, owner, bank_id):
    bank = await db.mz2_financial_accounts.find_one({
        "user_id": owner, "id": bank_id, "account_type": "bank", "status": "active",
    }, {"_id": 0, "id": 1, "name": 1, "currency": 1, "account_type": 1, "version": 1})
    if not bank:
        raise HTTPException(409, detail={"code": "opening_provider_canonical_bank_required"})
    if await db.accounts.find_one({"user_id": owner, "id": bank_id}, {"_id": 1}):
        raise HTTPException(409, detail={"code": "opening_bank_identity_ambiguous"})
    return bank


async def opening_entity_context(db, owner):
    entities = {category: [] for category in ENTITY_CATEGORIES}
    warnings = [{"code": "supplier_advance_unsupported",
                 "message": "دفعات المورد المقدمة تحتاج عقدًا معتمدًا؛ لا تُصافى مع مستحق المورد."}]
    banks = await _rows(db, "mz2_financial_accounts", owner, {"account_type": "bank", "status": "active"})
    bindings = []
    for provider in PROVIDERS:
        binding = await db.accounting_provider_bank_bindings_v2.find_one({
            "user_id": owner, "provider": provider,
        }, {"_id": 0}) or {}
        bank = None
        reason = "provider_bank_mapping_required"
        if binding.get("bank_account_id"):
            try:
                bank = await canonical_opening_bank(db, owner, binding["bank_account_id"])
            except HTTPException:
                reason = "requires_v2_bank_mapping"
        verified = bool(bank and binding.get("verification_status") == "verified"
                        and binding.get("evidence_ref"))
        if bank and not verified:
            reason = "provider_bank_confirmation_required"
        view = {
            "provider": provider, "bank_account_id": binding.get("bank_account_id"),
            "bank_account_name": (bank or {}).get("name"), "configured": bool(bank),
            "verification_status": "verified" if verified else "unverified" if binding else "missing",
            "needs_confirmation": not verified, "reason": None if verified else reason,
            "evidence_ref": binding.get("evidence_ref"), "revision": binding.get("revision", 0),
            "bank_snapshot": bank if verified else None,
        }
        bindings.append(view)
        entities["provider_receivable"].append({
            "id": provider, "name": PROVIDER_LABELS[provider], "source": "accounting_provider_contract",
            "status": "active", "provider_binding": view,
        })
    employees = [_identity(row, "operating_salaries") for row in await _rows(
        db, "operating_salaries", owner, {"category": "employee"})]
    for category in EMPLOYEE_CATEGORIES:
        entities[category] = employees
    for row in await _rows(db, "suppliers", owner):
        try:
            linked = await require_linked_supplier(db, owner, str(row.get("id") or ""))
        except HTTPException:
            warnings.append({"code": "supplier_verified_identity_required", "id": row.get("id"),
                             "name": row.get("company_name") or row.get("name")})
            continue
        entities["supplier_payable"].append(_identity(linked, "suppliers+counterparties"))
    entities["customer_receivable"] = [_identity(row, "counterparties") for row in await _rows(
        db, "counterparties", owner, {"kind": "general"})]
    drivers = [_identity(row, "store_drivers") for row in await _rows(db, "store_drivers", owner)]
    for category in DRIVER_CATEGORIES:
        entities[category] = drivers
    couriers = {row["id"]: row for row in [
        _identity(row, "counterparties") for row in await _rows(db, "counterparties", owner, {"kind": "courier"})]}
    settings = await db.settings.find_one({"user_id": owner}, {"_id": 0, "shipping_companies": 1}) or {}
    for configured in settings.get("shipping_companies") or []:
        identity, name = normalize_shipping_company(configured.get("name"))
        if identity != "unknown":
            couriers.setdefault(identity, {"id": identity, "name": name,
                "source": "settings.shipping_companies", "status": "active"})
    policy = await db.mz2_shipping_rate_policies.find_one({"_id": owner, "user_id": owner}) or {}
    for version in policy.get("versions") or []:
        identity = str(version.get("courier_id") or "").strip()
        if identity and version.get("verification_status") == "approved":
            couriers.setdefault(identity, {"id": identity, "name": version.get("name") or identity,
                "source": "mz2_shipping_rate_policies", "status": "active"})
    for category in COURIER_CATEGORIES:
        entities[category] = list(couriers.values())
    return {"entities": entities, "provider_bindings": bindings, "banks": banks,
            "warnings": warnings, "supplier_advance_supported": False,
            "manual_account_categories": ["inventory_asset", "sales_vat_payable", "input_vat",
                                          "other_receivable", "other_payable"]}


def opening_entity_snapshot(context, category, entity_id, *, nonzero, financial_account_ids):
    if category not in ENTITY_CATEGORIES:
        return None  # Existing owner-declared inventory/tax/other account contract.
    matches = [row for row in context["entities"][category] if row["id"] == entity_id]
    if len(matches) != 1:
        raise HTTPException(409, detail={"code": "opening_entity_missing_or_ambiguous", "category": category})
    snapshot = matches[0]
    if category == "provider_receivable" and nonzero:
        binding = snapshot["provider_binding"]
        if binding["needs_confirmation"] or not binding["configured"]:
            raise HTTPException(409, detail={"code": "opening_provider_bank_mapping_required"})
        if binding["bank_account_id"] not in financial_account_ids:
            raise HTTPException(409, detail={"code": "opening_provider_bank_balance_or_zero_required"})
    return snapshot
