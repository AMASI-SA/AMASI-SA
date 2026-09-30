"""Read-only exact-identity mappings for onboarding, never legacy balances."""
from fastapi import HTTPException

from supplier_identity_service import SUPPLIERS_V2, require_supplier_v2

KINDS = ("bank", "provider", "employee", "supplier", "external_person",
         "courier", "store_driver", "ad_account")
PROVIDERS = ("salla", "tabby", "tamara", "emkan")
PROJECTION = {"_id": 0, "id": 1, "employee_id": 1, "name": 1, "company_name": 1, "kind": 1,
              "status": 1, "version": 1, "archived": 1, "deleted": 1,
              "is_archived": 1, "is_deleted": 1, "currency": 1,
              "external_ref": 1, "account_type": 1}


def fail(code="onboarding_identity_invalid"):
    raise HTTPException(409, detail={"code": code})


def usable(row):
    return (row.get("status") not in {"inactive", "archived", "deleted"}
            and not any(row.get(k) is True for k in
                        ("archived", "deleted", "is_archived", "is_deleted")))


async def _rows(db, collection, query):
    rows = await db[collection].find(query, PROJECTION).to_list(1001)
    if len(rows) > 1000:
        fail("onboarding_identity_scope_too_large")
    return [row for row in rows if usable(row)]


async def identities(db, owner, kind):
    if kind not in KINDS:
        raise HTTPException(404, detail={"code": "onboarding_identity_not_found"})
    query = {"user_id": owner}
    if kind == "provider":
        return [{"id": code, "label": code, "kind": kind} for code in PROVIDERS]
    if kind == "courier":
        # P02's financial counterparty contract uses approved courier_id, not
        # a normalized operational shipping name. Reading it does not enable P02.
        policy = await db.mz2_shipping_rate_policies.find_one({"_id": owner, **query}, {"versions": 1}) or {}
        versions = policy.get("versions") or []
        if not isinstance(versions, list) or len(versions) > 1000:
            fail("onboarding_identity_scope_too_large")
        catalog = {}
        for row in versions:
            if isinstance(row, dict) and row.get("verification_status") == "approved":
                key = str(row.get("courier_id") or "").strip()
                if not key:
                    fail()
                catalog[key] = {"id": key, "label": row.get("name") or key, "kind": kind}
        return [catalog[key] for key in sorted(catalog)]
    if kind == "bank":
        rows = await _rows(db, "mz2_financial_accounts", {**query, "account_type": "bank", "status": "active"})
    elif kind == "employee":
        rows = await _rows(db, "operating_salaries", {**query, "category": "employee"})
    elif kind == "store_driver":
        rows = await _rows(db, "store_drivers", query)
    elif kind == "supplier":
        rows = await _rows(db, SUPPLIERS_V2, query)
    else:
        # Production's external-person registry is kind=general. This is an
        # explicit API-to-storage mapping, not fuzzy matching or balance reuse.
        storage_kind = "general" if kind == "external_person" else kind
        rows = await _rows(db, "counterparties", {**query, "kind": storage_kind})
    result = []
    seen = set()
    for row in rows:
        key = str(row.get("id") or (row.get("employee_id") if kind == "employee" else "") or "")
        if not key or key in seen:
            fail()
        seen.add(key)
        result.append({"id": key, "label": row.get("name") or row.get("company_name") or key,
                       "kind": kind, "version": row.get("version"),
                       "currency": row.get("currency"), "external_ref": row.get("external_ref")})
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
            kind, key = "ad_account", str(account.get("external_ref") or "")
        else:
            key = line["entity_id"]
        if kind in {"asset", "liability", "tax"}:
            mappings.append({"kind": kind, "id": key, "sub_account": line["sub_account"],
                             "evidence_file_id": line["evidence_file_id"]})
            continue
        if kind not in cache:
            cache[kind] = {row["id"]: row for row in await identities(db, owner, kind)}
        if key not in cache[kind]:
            fail()
        if kind == "supplier":
            await require_supplier_v2(db, owner, key)
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
        if not bank or binding["evidence_file_id"] != compiled["section_evidence_file_ids"]["providers"]:
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
        rows = await identities(db, owner, kind)
        if any((category, row["id"]) not in facts for row in rows for category in categories):
            fail("onboarding_entity_balance_required")
    ad_facts = {(line["account_snapshot"]["external_ref"], line["account_snapshot"]["account_type"])
                for line in compiled["lines"] if line.get("account_snapshot")
                and line["account_snapshot"]["account_type"] in {"ad_prepaid_wallet", "ad_payable"}}
    if any((row["id"], account_type) not in ad_facts
           for row in await identities(db, owner, "ad_account")
           for account_type in ("ad_prepaid_wallet", "ad_payable")):
        fail("onboarding_entity_balance_required")
    return mappings
