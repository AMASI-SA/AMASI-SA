"""Read-only exact-identity mappings for onboarding, never legacy balances."""
from fastapi import HTTPException

from accounting_onboarding_source_gaps import courier_identities, provider_identities, integration_ad_identities

KINDS = ("bank", "provider", "employee", "supplier", "external_person",
         "courier", "store_driver", "ad_account")
PROVIDERS = ("salla", "tabby", "tamara", "emkan")
PROJECTION = {"_id": 0, "id": 1, "employee_id": 1, "name": 1, "company_name": 1, "kind": 1,
              "status": 1, "version": 1, "archived": 1, "deleted": 1,
              "is_archived": 1, "is_deleted": 1, "currency": 1,
              "external_ref": 1, "account_type": 1, "active": 1, "is_active": 1, "phone": 1, "notes": 1}


def fail(code="onboarding_identity_invalid"):
    raise HTTPException(409, detail={"code": code})


def usable(row):
    return (row.get("status") not in {"inactive", "archived", "deleted", "hidden"}
            and row.get("active") is not False and row.get("is_active") is not False
            and not any(row.get(k) is True for k in
                        ("archived", "deleted", "is_archived", "is_deleted")))


async def _rows(db, collection, query):
    rows = await db[collection].find(query, PROJECTION).to_list(1001)
    if len(rows) > 1000:
        fail("onboarding_identity_scope_too_large")
    return [row for row in rows if usable(row)]


async def identities(db, owner, kind, *, as_of=None):
    if kind not in KINDS:
        raise HTTPException(404, detail={"code": "onboarding_identity_not_found"})
    query = {"user_id": owner}
    if kind == "provider":
        return await provider_identities(db, owner)
    if kind == "ad_account":
        return await integration_ad_identities(db, owner, as_of=as_of)
    if kind == "courier":
        return await courier_identities(db, owner)
    if kind == "bank":
        rows = await _rows(db, "mz2_financial_accounts", {**query, "account_type": "bank", "status": "active", "currency": "SAR"})
    elif kind == "employee":
        rows = await _rows(db, "mezan_employees_v2", query)
    elif kind == "supplier":
        rows = await _rows(db, "mezan_suppliers_v2", query)
    elif kind == "store_driver":
        rows = await _rows(db, "store_drivers", query)
    else:
        # Explicit contact registries for these domains only. Never supplier or
        # employee fallback, and never a source of financial balances.
        rows = await _rows(db, "counterparties", {**query, "kind": "general" if kind == "external_person" else kind})
    result = []
    seen = set()
    for row in rows:
        key = str(row.get("id") or (row.get("employee_id") if kind == "employee" else "") or "")
        if not key or key in seen:
            fail()
        seen.add(key)
        if kind == "bank" and await db.accounts.find_one({"user_id": owner, "id": key}, {"_id": 0, "id": 1}):
            continue
        item = {"id": key, "label": row.get("name") or row.get("company_name") or key,
                       "kind": kind, "version": row.get("version"),
                       "currency": row.get("currency"), "external_ref": row.get("external_ref")}
        if kind == "employee":
            contract = await db.mezan_employee_salary_contracts_v2.find_one({"user_id": owner, "employee_id": key}, {"_id": 0, "id": 1})
            item.update(source="mezan_employees_v2", salary_contract_status="available" if contract else "missing")
        elif kind == "supplier":
            item["source"] = "mezan_suppliers_v2"
        elif kind == "external_person":
            item.update(source="counterparties", phone=row.get("phone") or "", notes=row.get("notes") or "")
        result.append(item)
    return sorted(result, key=lambda row: row["id"])


async def verify_mappings(db, owner, compiled, provider_bindings):
    mappings = []
    cache = {}
    for line in compiled["lines"]:
        kind = {"payment_gateway": "provider"}.get(line["entity_type"], line["entity_type"])
        if line["financial_account_id"]:
            account = line["account_snapshot"]
            if account["account_type"] not in {"ad_prepaid_wallet", "ad_payable"}:
                mappings.append({"kind": "financial_account", **account})
                continue
            kind = "ad_account"
            if kind not in cache:
                cache[kind] = {row["id"]: row for row in await identities(db, owner, kind, as_of=compiled.get("cutover_at"))}
            binding_field = "prepaid_wallet_account_id" if account["account_type"] == "ad_prepaid_wallet" else "payable_account_id"
            matches = [row for row in cache[kind].values() if row.get(binding_field) == account["id"] and row.get("binding_status") == "valid"]
            if len(matches) != 1:
                fail("onboarding_ad_binding_required")
            key = matches[0]["id"]
        else:
            key = line["entity_id"]
        if kind in {"asset", "liability", "tax"}:
            mappings.append({"kind": kind, "id": key, "sub_account": line["sub_account"],
                             "evidence_file_id": line["evidence_file_id"]})
            continue
        if kind not in cache:
            cache[kind] = {row["id"]: row for row in await identities(db, owner, kind, as_of=compiled.get("cutover_at"))}
        if key not in cache[kind]:
            fail()
        mappings.append(cache[kind][key])
    needed = {line["entity_id"] for line in compiled["lines"] if line["category"] == "provider_receivable"}
    seen = set()
    for binding in provider_bindings:
        provider = binding["provider"]
        if provider not in needed or provider in seen:
            fail("onboarding_provider_binding_required")
        seen.add(provider)
        bank = await db.mz2_financial_accounts.find_one({"user_id": owner, "id": binding["bank_account_id"],
                                                       "status": "active", "account_type": "bank", "currency": "SAR"}, PROJECTION)
        legacy_bank = await db.accounts.find_one({"user_id": owner, "id": binding["bank_account_id"]}, {"_id": 0, "id": 1})
        if not bank or not usable(bank) or legacy_bank or binding["evidence_file_id"] != compiled["section_evidence_file_ids"]["providers"]:
            fail("onboarding_provider_binding_required")
        mappings.append({"kind": "provider_bank_binding", "provider": provider, "bank": bank,
                         "evidence_file_id": binding["evidence_file_id"]})
    if seen != needed:
        fail("onboarding_provider_binding_required")
    # A missing economic fact is not a zero. Independently cover each account
    # of every active entity; an advance never cancels a payable or salary.
    coverage = {
        "employee": ("employee_salary_payable", "employee_advance", "employee_custody"),
        "supplier": ("supplier_payable", "supplier_advance"),
        "external_person": ("customer_receivable",),
        "courier": ("courier_cod_receivable", "courier_payable"),
        "store_driver": ("store_driver_cod_receivable", "store_driver_fee_payable"),
    }
    facts = {(line["category"], line["entity_id"]) for line in compiled["lines"]}
    for kind, categories in coverage.items():
        rows = await identities(db, owner, kind, as_of=compiled.get("cutover_at"))
        if any((category, row["id"]) not in facts for row in rows for category in categories):
            fail("onboarding_entity_balance_required")
    ad_accounts = await identities(db, owner, "ad_account", as_of=compiled.get("cutover_at"))
    financial_facts = {line["financial_account_id"] for line in compiled["lines"] if line.get("financial_account_id")}
    for row in ad_accounts:
        if row.get("binding_status") != "valid":
            fail("onboarding_ad_binding_required")
        if any(row.get(field) not in financial_facts for field in ("prepaid_wallet_account_id", "payable_account_id")):
            fail("onboarding_entity_balance_required")
    return mappings
