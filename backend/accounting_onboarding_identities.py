"""Read-only exact-identity mappings for onboarding, never legacy balances."""
from fastapi import HTTPException

from supplier_identity_service import SUPPLIERS_V2, require_supplier_v2
from accounting_financial_identity import find_financial_account, list_financial_accounts

KINDS = ("bank", "provider", "employee", "supplier", "external_person",
         "courier", "store_driver", "ad_account")
PROVIDERS = ("salla", "tabby", "tamara", "emkan")
PROJECTION = {"_id": 0, "id": 1, "employee_id": 1, "name": 1, "company_name": 1, "kind": 1,
              "display_name": 1, "active": 1, "is_active": 1,
              "status": 1, "version": 1, "archived": 1, "deleted": 1,
              "is_archived": 1, "is_deleted": 1, "currency": 1,
              "external_ref": 1, "account_type": 1, "financial_entity_id": 1}


def fail(code="onboarding_identity_invalid"):
    raise HTTPException(409, detail={"code": code})


def usable(row):
    return (row.get("status") not in {"inactive", "archived", "deleted", "hidden", "suspended"}
            and row.get("active") is not False and row.get("is_active") is not False
            and not any(row.get(k) is True for k in
                        ("archived", "deleted", "is_archived", "is_deleted")))


async def _rows(db, collection, query):
    rows = await db[collection].find(query, PROJECTION).to_list(1001)
    if len(rows) > 1000:
        fail("onboarding_identity_scope_too_large")
    return [row for row in rows if usable(row)]


async def require_ad_financial_binding(db, owner, financial_account_id):
    """Resolve one exact owner-confirmed Track E binding, never external_ref."""
    from accounting_advertising_contract import BINDINGS
    from accounting_advertising_setup import binding_key, confirmed_binding
    if not isinstance(financial_account_id, str) or not financial_account_id:
        fail("onboarding_native_ad_binding_dependency")
    rows = await db[BINDINGS].find({"user_id": owner, "$or": [
        {"wallet_financial_account_id": financial_account_id},
        {"payable_financial_account_id": financial_account_id}]}).to_list(2)
    if len(rows) != 1:
        fail("onboarding_native_ad_binding_ambiguous" if rows else "onboarding_native_ad_binding_dependency")
    row = rows[0]
    platform, integration = row.get("platform"), row.get("integration_account_id")
    if any(not isinstance(row.get(key), str) or not row[key] for key in
           ("platform", "integration_account_id", "platform_account_id", "currency", "funding_mode")):
        fail("onboarding_native_ad_binding_dependency")
    if not platform or not integration or row.get("_id") != binding_key(owner, platform, integration):
        fail("onboarding_native_ad_binding_dependency")
    binding = await confirmed_binding(db, owner, platform, integration)
    # Track E checks type, mode, currency and integration status. Also reject
    # duplicate financial IDs rather than allowing a find_one result to decide.
    for field in ("wallet_financial_account_id", "payable_financial_account_id"):
        identity = binding.get(field)
        if identity:
            accounts = await db.mz2_financial_accounts.find({"user_id": owner, "id": identity}).to_list(2)
            if len(accounts) != 1 or not usable(accounts[0]):
                fail("onboarding_native_ad_financial_identity_invalid")
    return binding


def _ad_identity(binding):
    return {"id": binding["_id"], "kind": "ad_account",
            "label": binding["platform"] + ": " + binding["platform_account_id"],
            **{key: binding.get(key) for key in ("platform", "integration_account_id", "platform_account_id",
                "funding_mode", "currency", "version", "wallet_financial_account_id", "payable_financial_account_id")}}


async def identities(db, owner, kind):
    if kind not in KINDS:
        raise HTTPException(404, detail={"code": "onboarding_identity_not_found"})
    query = {"user_id": owner}
    if kind == "provider":
        return [{"id": code, "label": code, "kind": kind} for code in PROVIDERS]
    if kind == "ad_account":
        from accounting_advertising_contract import BINDINGS
        rows = await db[BINDINGS].find(query, {key: 1 for key in ("_id", "platform", "integration_account_id",
            "wallet_financial_account_id", "payable_financial_account_id", "status", "confirmed_by", "confirmed_at",
            "active", "is_active", "archived", "deleted", "is_archived", "is_deleted")}).to_list(1001)
        if len(rows) > 1000:
            fail("onboarding_identity_scope_too_large")
        result = []
        for row in rows:
            if not usable(row) or not row.get("confirmed_by") or not row.get("confirmed_at"):
                continue
            identity = row.get("wallet_financial_account_id") or row.get("payable_financial_account_id")
            binding = await require_ad_financial_binding(db, owner, identity)
            result.append(_ad_identity(binding))
        return sorted(result, key=lambda row: row["id"])
    if kind == "courier":
        # P02's financial counterparty contract uses approved courier_id, not
        # a normalized operational shipping name. Reading it does not enable P02.
        policy = await db.mz2_shipping_rate_policies.find_one({"_id": owner, **query}, {"versions": 1}) or {}
        versions = policy.get("versions") or []
        if not isinstance(versions, list) or len(versions) > 1000:
            fail("onboarding_identity_scope_too_large")
        catalog = {}
        # Track F confirms exact identities without importing Legacy settings.
        from accounting_shipping_native_setup import read_setup
        setup = await read_setup(db, owner)
        for row in setup["couriers"]:
            if row.get("status") == "active" and row.get("confirmed_by") and row.get("confirmed_at"):
                key = row["courier_key"]
                catalog[key] = {"id": key, "label": row["name"], "kind": kind}
        for row in versions:
            if isinstance(row, dict) and row.get("verification_status") == "approved":
                key = str(row.get("courier_id") or "").strip()
                if not key:
                    fail()
                catalog.setdefault(key, {"id": key, "label": row.get("name") or key, "kind": kind})
        return [catalog[key] for key in sorted(catalog)]
    if kind == "bank":
        rows = await list_financial_accounts(db, owner, account_types=("bank",), currency="SAR")
    elif kind == "employee":
        rows = await _rows(db, "mezan_employees_v2", {**query, "status": "active"})
    elif kind == "store_driver":
        rows = await _rows(db, "store_drivers", query)
    elif kind == "supplier":
        rows = await _rows(db, SUPPLIERS_V2, query)
    elif kind == "external_person":
        rows = await _rows(db, "mz2_external_persons_v2", {**query, "status": "active"})
    elif kind == "supplier":
        rows = await _rows(db, SUPPLIERS_V2, {**query, "status": "active"})
    else:
        return []
    result = []
    seen = set()
    for row in rows:
        key = str(row.get("id") or "")
        if not key or key in seen:
            fail()
        seen.add(key)
        result.append({"id": key, "label": row.get("display_name") or row.get("name") or row.get("company_name") or key,
                       "kind": kind, "version": row.get("version"),
                       **({"financial_identity_ready": row.get("financial_entity_id") == key} if kind == "employee" else {}),
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
            binding = await require_ad_financial_binding(db, owner, line["financial_account_id"])
            field = "wallet_financial_account_id" if account["account_type"] == "ad_prepaid_wallet" else "payable_financial_account_id"
            if binding.get(field) != line["financial_account_id"] or account.get("currency") != binding["currency"]:
                fail("onboarding_native_ad_financial_identity_invalid")
            mappings.append({**_ad_identity(binding), "financial_account_id": line["financial_account_id"],
                             "account_type": account["account_type"]})
            continue
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
        if kind == "employee" and not cache[kind][key].get("financial_identity_ready"):
            fail("onboarding_employee_financial_identity_dependency")
        mappings.append(cache[kind][key])
    needed = {line["entity_id"] for line in compiled["lines"] if line["category"] == "provider_receivable"}
    seen = set()
    for binding in provider_bindings:
        provider = binding["provider"]
        if provider not in needed or provider in seen:
            fail("onboarding_provider_binding_required")
        seen.add(provider)
        bank = await find_financial_account(db, owner, binding["bank_account_id"],
                                            account_types=("bank",), currency="SAR")
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
        if kind == "employee" and any(not item.get("financial_identity_ready") for item in rows):
            fail("onboarding_employee_financial_identity_dependency")
        if any((category, row["id"]) not in facts for row in rows for category in categories):
            fail("onboarding_entity_balance_required")
    ad_facts = {line["financial_account_id"] for line in compiled["lines"]
                if line.get("account_snapshot") and line["account_snapshot"]["account_type"]
                in {"ad_prepaid_wallet", "ad_payable"}}
    if any(identity not in ad_facts
           for row in await identities(db, owner, "ad_account")
           for identity in (row.get("wallet_financial_account_id"), row.get("payable_financial_account_id"))
           if identity):
        fail("onboarding_entity_balance_required")
    return mappings
