"""Read-only MZ2 onboarding identity diagnostics; legacy rows are never choices."""
from fastapi import HTTPException
from shipping_companies import normalize_shipping_company

FINANCIAL_FIELDS = {key: 1 for key in ("id", "name", "account_type", "currency", "external_ref", "status", "active", "is_active", "archived", "is_archived", "deleted", "is_deleted")}


async def rows(db, collection, owner, fields, query=None):
    result = await db[collection].find({"user_id": owner, **(query or {})}, {"_id": 0, **fields}).to_list(1001)
    if len(result) > 1000:
        raise HTTPException(409, detail={"code": "onboarding_identity_scope_too_large", "source": collection})
    return result


def active(row):
    return row.get("status") not in {"inactive", "archived", "deleted", "hidden"} and not any(row.get(k) is True for k in ("archived", "is_archived", "deleted", "is_deleted")) and row.get("active") is not False and row.get("is_active") is not False


async def provider_identities(db, owner):
    result = []
    for provider in ("salla", "tabby", "tamara", "emkan"):
        binding = await db.accounting_provider_bank_bindings_v2.find_one({"user_id": owner, "provider": provider}, {"_id": 0, "bank_account_id": 1, "verification_status": 1}) or {}
        key = binding.get("bank_account_id")
        banks = await rows(db, "mz2_financial_accounts", owner, FINANCIAL_FIELDS, {"id": key}) if key else []
        bank = banks[0] if len(banks) == 1 else {}
        legacy = await db.accounts.find_one({"user_id": owner, "id": key}, {"_id": 0, "id": 1}) if key else None
        status = "missing" if not key else "noncanonical" if not bank or legacy or bank.get("account_type") != "bank" else "inactive" if bank.get("status") != "active" or not active(bank) else "currency_mismatch" if bank.get("currency") != "SAR" else "valid"
        result.append({"id": provider, "label": provider, "kind": "provider", "source": "accounting_provider_bank_bindings_v2", "bank_account_id": key, "bank_name": bank.get("name"), "binding_status": status, "binding_verification_status": binding.get("verification_status")})
    return result


async def courier_identities(db, owner):
    # P02's read-only workspace consumes this isolated MZ2 order evidence.
    # settings.shipping_companies and legacy counterparties are not fallbacks.
    names = await db.mz2_salla_order_evidence.distinct("shipping_company", {"user_id": owner, "conflict": {"$ne": True}})
    if len(names) > 1000:
        raise HTTPException(409, detail={"code": "onboarding_identity_scope_too_large"})
    catalog = {}
    for raw_name in names:
        key, name = normalize_shipping_company(raw_name)
        if key not in {"unknown", "mandoob", "mandoob_riyadh", "pickup"}:
            catalog[key] = {"id": key, "label": name, "kind": "courier", "source": "mz2_salla_order_evidence", "identity_available": True, "contract_state": "contract_incomplete", "approved_policy_id": None}
    policy = await db.mz2_shipping_rate_policies.find_one({"_id": owner, "user_id": owner}, {"versions": 1}) or {}
    versions = policy.get("versions") or []
    if not isinstance(versions, list) or len(versions) > 1000:
        raise HTTPException(409, detail={"code": "onboarding_identity_scope_too_large"})
    for item in versions:
        if not isinstance(item, dict):
            continue
        key = str(item.get("courier_id") or "").strip()
        if not key or key in {"unknown", "mandoob", "mandoob_riyadh", "pickup"}:
            continue
        entry = catalog.setdefault(key, {"id": key, "label": item.get("name") or key, "kind": "courier", "source": "mz2_shipping_rate_policies", "identity_available": True, "contract_state": "rates_incomplete", "approved_policy_id": None})
        if item.get("verification_status") == "approved":
            entry.update(contract_state="approved", approved_policy_id=item.get("id"))
    return sorted(catalog.values(), key=lambda item: item["id"])


async def ad_identity_metadata(db, owner, profile):
    canonical = await rows(db, "mz2_financial_accounts", owner, FINANCIAL_FIELDS, {"external_ref": profile["id"], "status": "active"})
    metadata, currencies, gaps = {}, set(), []
    for account_type, field in (("ad_prepaid_wallet", "prepaid_wallet_account_id"), ("ad_payable", "payable_account_id")):
        matches = [item for item in canonical if item.get("account_type") == account_type and active(item)]
        matches = [item for item in matches if not await db.accounts.find_one({"user_id": owner, "id": item.get("id")}, {"_id": 0, "id": 1})]
        metadata[field] = matches[0]["id"] if len(matches) == 1 else None
        if len(matches) != 1:
            gaps.append(account_type + ("_ambiguous" if matches else "_missing"))
        else:
            if matches[0].get("currency"):
                currencies.add(matches[0]["currency"])
    if profile.get("currency"):
        currencies.add(profile["currency"])
    metadata["currency"] = next(iter(currencies)) if len(currencies) == 1 else None
    if not metadata["currency"]:
        gaps.append("ad_currency_conflict" if currencies else "ad_currency_unknown")
    metadata["binding_gaps"] = gaps
    metadata["source"] = "counterparties.kind=ad_account"
    return metadata


async def onboarding_source_gaps(db, owner):
    canonical = await rows(db, "mz2_financial_accounts", owner, FINANCIAL_FIELDS)
    legacy = await rows(db, "accounts", owner, FINANCIAL_FIELDS)
    items, gaps = [], []
    for key in sorted({str(item.get("id") or "") for item in canonical + legacy} - {""}):
        new = [item for item in canonical if str(item.get("id")) == key]
        old = [item for item in legacy if str(item.get("id")) == key]
        account = (new or old)[0]
        ambiguous = len(new) > 1 or bool(new and old)
        inactive = bool(new and (new[0].get("status") != "active" or not active(new[0])))
        mismatch = bool(new and new[0].get("account_type") == "bank" and new[0].get("currency") != "SAR")
        row = {"id": key, "name": account.get("name") or key, "legacy_exists": bool(old), "mz2_exists": bool(new), "linked": None, "linkage_status": "no_authoritative_legacy_link_contract", "identity_ambiguous": ambiguous, "inactive": inactive, "currency_mismatch": mismatch, "selectable": bool(new) and not ambiguous and not inactive}
        items.append(row)
        if not row["selectable"]:
            gaps.append({"code": "financial_account_identity_ambiguous" if ambiguous else "financial_account_inactive" if inactive else "financial_account_currency_mismatch" if mismatch else "canonical_financial_account_required", "stage": "02", "entity_id": key, "message": "الحساب يحتاج إنشاء/ربط في الحسابات المالية لميزان 2", "corrective_action": "financial-accounts"})
    bindings = await provider_identities(db, owner)
    for binding in bindings:
        if binding["binding_status"] != "valid":
            gaps.append({"code": "provider_bank_" + binding["binding_status"], "stage": "03", "entity_id": binding["id"], "message": "المزود يحتاج بنك تسوية canonical نشطًا بعملة SAR", "corrective_action": "financial-accounts"})
    fee_capability = {"supported": False, "status": "GAP", "code": "provider_fee_policy_capability_missing", "stage": "11", "message": "لا يوجد عقد رسوم ميزان 2 موثق بتاريخ سريان؛ إعدادات الرسوم القديمة ليست مصدرًا محاسبيًا معتمدًا", "missing_fields": ["authoritative_effective_dated_mz2_policy"], "legacy_diagnostic_source": "settings.payment_methods / financial_provider_apps_legacy", "writer_available": False}
    return {"items": items, "gaps": gaps, "provider_bindings": bindings, "fee_capability": fee_capability, "read_only": True, "legacy_selectable": False}
