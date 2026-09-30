"""HTTP contracts against a disposable localhost replica, never live data."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

from bson import json_util
from fastapi import FastAPI, Request, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest
import pytest_asyncio

import accounting_onboarding as onboarding
from accounting_onboarding_contract import SECTION_IDS
from financial_provider_apps import make_financial_provider_apps_router
from tests.test_financial_accounts_real_mongo import (
    mongo_db, OWNER, _headers, _user, ALL_NEW,
)

BASE = "/api/accounting-module/onboarding"
FINANCIAL = "/api/financial-provider-apps/accounting-module/financial-accounts"
OPENING = FINANCIAL + "/opening-balances"
CREATE = {"idempotency_key": "setup-session-0001", "cutover_at": "2026-10-01T00:00:00+03:00",
          "cutover_timezone": "Asia/Riyadh"}


@pytest_asyncio.fixture
async def api(mongo_db):
    app = FastAPI()
    async def current_user(request: Request):
        return {"id": request.headers.get("X-Test-User", "")}
    # Use the real router: setup routes must remain usable outside its broad
    # financial wrapper while every existing financial mutation stays guarded.
    app.include_router(make_financial_provider_apps_router(mongo_db, current_user), prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=True), base_url="http://test") as client:
        yield SimpleNamespace(db=mongo_db, client=client)


async def request(api, method, path, payload=None, user="full", status=200):
    response = await api.client.request(method, BASE + path, headers=_headers(user), json=payload)
    assert response.status_code == status, response.text
    return response.json()


async def pause(api, value=True):
    await api.db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": value}})


async def fingerprint(api, exclude=("mz2_onboarding_sessions",)):
    return {name: sorted(json_util.dumps(row, sort_keys=True) for row in await api.db[name].find({}).to_list(None))
            for name in await api.db.list_collection_names() if name not in exclude}


async def prepare(api, amount="115.00", *, user="full"):
    sections = {}
    for key in (*SECTION_IDS, "cutover"):
        data = {"purpose": "cutover"} if key == "cutover" else {"purpose": "opening_balance", "section_id": key}
        response = await api.client.post(OPENING + "/evidence", headers=_headers("full"), data=data,
            files={"file": ("synthetic.txt", ("synthetic:" + key).encode(), "application/octet-stream")})
        assert response.status_code == 200, response.text
        sections[key] = response.json()
    cutover = sections.pop("cutover")
    response = await api.client.post(FINANCIAL, headers=_headers("full"), json={
        "name": "Synthetic bank", "account_type": "bank", "currency": "SAR", "idempotency_key": "bank-setup-0001"})
    assert response.status_code == 200, response.text
    account = response.json()
    await pause(api)
    row = await request(api, "POST", "/sessions", CREATE, user=user)
    row = await request(api, "PUT", f"/sessions/{row['id']}/cutover", {
        **CREATE, "version": row["version"], "idempotency_key": "save-cutover-0001",
        "cutover_evidence_file_id": cutover["source_file_id"],
    }, user=user)
    for key in SECTION_IDS:
        lines = [{"category": "financial_account", "financial_account_id": account["id"],
                  "meaning": "zero" if amount == "0.00" else "available_to_us",
                  "original_amount": amount, "original_currency": "SAR", "fx_rate_to_sar": "1",
                  "evidence_file_id": sections[key]["source_file_id"]}] if key == "banks_cash" else []
        row = await request(api, "PUT", f"/sessions/{row['id']}/sections/{key}", {
            "version": row["version"], "idempotency_key": "save-section-" + key,
            "status": "complete" if lines else "not_applicable", "reason": "Owner evidence: no applicable balance",
            "evidence_file_id": sections[key]["source_file_id"], "data": {"lines": lines},
        }, user=user)
    return row, sections, account


async def action(api, row, verb, *, status=200, user="full", key=None):
    return await request(api, "POST", f"/sessions/{row['id']}/{verb}", {
        "version": row["version"], "idempotency_key": key or "action-key-" + verb,
        "note": "Explicit isolated test review",
    }, status=status, user=user)


@pytest.mark.asyncio
async def test_metadata_entire_flow_during_pause_has_zero_other_effects(api):
    row, _, _ = await prepare(api)
    before = await fingerprint(api)
    row = await action(api, row, "preview")
    assert row["preview"]["balanced"]
    assert row["preview"]["debit_total"] == row["preview"]["credit_total"] == "115.00"
    row = await action(api, row, "review")
    resumed = await request(api, "GET", f"/sessions/{row['id']}")
    assert resumed == row
    ready = await request(api, "GET", f"/sessions/{row['id']}/readiness")
    assert ready["source_ready"] and not ready["ready_for_live_post"]
    assert ready["financial_writes_paused"] and not ready["opening_verified"]
    assert ready["live_gates"]["smoke_b"] == "BLOCKED_BY_ENVIRONMENT"
    assert ready["writer_transition"]["state"] == "legacy_active"
    assert not ready["p02_activation_allowed"] and not ready["g47_activation_allowed"]
    denied = await action(api, row, "opening-draft", status=423)
    assert denied["detail"]["code"] == "mz2_writes_paused"
    assert await fingerprint(api) == before
    assert await api.db.mz2_opening_balance_drafts.count_documents({}) == 0


@pytest.mark.asyncio
async def test_missing_control_also_allows_metadata_only_and_blocks_financial_handoff(api):
    row, _, _ = await prepare(api)
    await api.db.mz2_atomic_owners.delete_one({"_id": OWNER})
    before = await fingerprint(api)
    row = await action(api, row, "preview")
    row = await action(api, row, "review")
    await action(api, row, "opening-draft", status=423)
    assert await fingerprint(api) == before


@pytest.mark.asyncio
async def test_create_resume_idempotency_and_concurrent_cas(api):
    await pause(api)
    first, second = await asyncio.gather(*[request(api, "POST", "/sessions", CREATE) for _ in range(2)])
    assert first["id"] == second["id"]
    assert await api.db.mz2_onboarding_sessions.count_documents({}) == 1
    changed = {**CREATE, "cutover_at": "2026-10-02T00:00:00+03:00"}
    result = await request(api, "POST", "/sessions", changed, status=409)
    assert result["detail"]["code"] == "onboarding_idempotency_conflict"
    payload = {"version": 1, "idempotency_key": "incomplete-save-001", "status": "incomplete",
               "data": {"lines": [{"category": "supplier_payable"}]}}
    url = f"/sessions/{first['id']}/sections/suppliers"
    results = await asyncio.gather(*[request(api, "PUT", url, payload) for _ in range(2)])
    assert all(row["version"] == 2 for row in results)
    assert len(results[0]["audit"]) == 2
    row = await request(api, "PUT", url, {**payload, "version": 2, "idempotency_key": "second-edit-002"})
    replay = await request(api, "PUT", url, payload)
    assert replay["existing"] and replay["version"] == row["version"]
    conflict = await request(api, "PUT", url, {**payload, "idempotency_key": "stale-edit-002"}, status=409)
    assert conflict["detail"]["code"] == "onboarding_version_conflict"
    different = {**payload, "version": row["version"]}
    replies = await asyncio.gather(*[api.client.put(BASE + url, headers=_headers("full"),
        json={**different, "idempotency_key": f"concurrent-distinct-{i}"}) for i in range(2)])
    assert sorted(r.status_code for r in replies) == [200, 409]


@pytest.mark.asyncio
async def test_permissions_owner_scope_and_fresh_revocation(api):
    row = await request(api, "POST", "/sessions", CREATE)
    await api.db.users.insert_one(_user("foreign", ALL_NEW, owner="other-owner"))
    await request(api, "GET", f"/sessions/{row['id']}", user="foreign", status=404)
    foreign = await request(api, "POST", "/sessions", CREATE, user="foreign")
    assert foreign["id"] != row["id"]
    for user in ("viewer", "reviewer", "no-new-permissions"):
        await request(api, "POST", "/sessions", CREATE, user=user, status=403)
    await action(api, row, "review", user="manager", status=403)
    await api.db.users.update_one({"id": "full"}, {"$set": {"disabled": True}})
    await request(api, "GET", f"/sessions/{row['id']}", status=403)


@pytest.mark.asyncio
@pytest.mark.parametrize("cutover", [None, "not-a-date", "2026-10-01T00:00:00", "2026-09-30T21:00:00Z"])
async def test_cutover_timestamp_never_assumed(api, cutover):
    await request(api, "POST", "/sessions", {**CREATE, "cutover_at": cutover}, status=422)
    assert await api.db.mz2_onboarding_sessions.count_documents({}) == 0


@pytest.mark.asyncio
async def test_explicit_zero_not_missing_and_review_is_locked(api):
    row, _, _ = await prepare(api, "0.00")
    before = row["version"]
    section = row["sections"]["banks_cash"]
    base = {**section, "version": before, "idempotency_key": "zero-check-001"}
    url = f"/sessions/{row['id']}/sections/banks_cash"
    for data in ({"lines": []}, {"lines": [{k: v for k, v in section["data"]["lines"][0].items() if k != "original_amount"}]}):
        await request(api, "PUT", url, {**base, "data": data}, status=422)
    row = await action(api, row, "preview")
    assert row["preview"]["zero_accounts"] and row["preview"]["entries"] == []
    row = await action(api, row, "review")
    result = await request(api, "PUT", url, {**base, "version": row["version"]}, status=409)
    assert result["detail"]["code"] == "onboarding_session_locked"


@pytest.mark.asyncio
async def test_account_mapping_and_evidence_snapshot_cannot_change_after_preview(api):
    row, sections, account = await prepare(api)
    row = await action(api, row, "preview")
    await api.db.mz2_financial_accounts.update_one({"id": account["id"]}, {"$inc": {"version": 1}})
    result = await action(api, row, "review", status=409)
    assert result["detail"]["code"] == "onboarding_snapshot_changed"
    # Original evidence bytes are verified again, not trusted from metadata.
    await api.db.accounting_source_files.update_one({"file_id": sections["banks_cash"]["source_file_id"]},
                                                   {"$set": {"content": b"tampered"}})
    result = await action(api, row, "review", status=409)
    assert result["detail"]["code"] == "opening_evidence_contract_mismatch"
    assert (await request(api, "GET", f"/sessions/{row['id']}"))["status"] == "previewed"


@pytest.mark.asyncio
async def test_existing_entities_cannot_be_silently_absent_or_netted(api):
    row, _, _ = await prepare(api)
    await api.db.operating_salaries.insert_one({"user_id": OWNER, "id": "employee-1", "category": "employee", "name": "Synthetic"})
    result = await action(api, row, "preview", status=409)
    assert result["detail"]["code"] == "onboarding_entity_balance_required"
    section = row["sections"]["payroll_obligations"]
    lines = [{"category": cat, "entity_id": "employee-1", "meaning": "zero", "original_amount": "0.00",
              "evidence_file_id": section["evidence_file_id"]} for cat in
             ("employee_salary_payable", "employee_advance", "employee_custody")]
    row = await request(api, "PUT", f"/sessions/{row['id']}/sections/payroll_obligations", {
        "version": row["version"], "idempotency_key": "explicit-employee-001", "status": "complete",
        "evidence_file_id": section["evidence_file_id"], "data": {"lines": lines}})
    row = await action(api, row, "preview")
    assert len(row["preview"]["zero_accounts"]) == 3


@pytest.mark.asyncio
async def test_handoff_atomic_retry_no_post_activation_or_legacy_leakage(api):
    row, _, _ = await prepare(api)
    row = await action(api, row, "preview")
    row = await action(api, row, "review")
    await pause(api, False)  # Synthetic isolated control only.
    settings = await api.db.settings.find({}).to_list(None)
    handoff = await action(api, row, "opening-draft")
    assert handoff["status"] == "handed_off"
    assert handoff["opening_draft"]["status"] == "reviewed"
    replay = await action(api, row, "opening-draft")
    assert replay["existing"] and replay["opening_draft"] == handoff["opening_draft"]
    assert await api.db.mz2_opening_balance_drafts.count_documents({}) == 1
    assert await api.db.settings.find({}).to_list(None) == settings
    for collection in ("general_ledger", "mz2_journals", "mz2_ledger_entries", "liabilities", "warehouse_locations"):
        assert await api.db[collection].count_documents({}) == 0
    post = await api.client.post(f"{OPENING}/drafts/{handoff['opening_draft']['id']}/post", headers=_headers("full"),
        json={"version": handoff["opening_draft"]["version"], "idempotency_key": "synthetic-post-blocked", "note": "Must reject legacy writer"})
    assert post.status_code == 423, post.text
    assert post.json()["detail"]["code"] == "accounting_v2_not_active"
    assert await api.db.settings.find({}).to_list(None) == settings


@pytest.mark.asyncio
async def test_handoff_failure_rolls_back_draft_evidence_and_session(api, monkeypatch):
    row, _, _ = await prepare(api)
    row = await action(api, row, "preview")
    row = await action(api, row, "review")
    await pause(api, False)
    before = await fingerprint(api, exclude=())
    original = onboarding._save
    async def failed_save(*args, **kwargs):
        if args[4] == "opening-draft":
            raise HTTPException(409, detail={"code": "injected_before_commit"})
        return await original(*args, **kwargs)
    monkeypatch.setattr(onboarding, "_save", failed_save)
    result = await action(api, row, "opening-draft", status=409)
    assert result["detail"]["code"] == "injected_before_commit"
    assert await fingerprint(api, exclude=()) == before


async def section_lines(api, row, key, lines, **extra):
    return await request(api, "PUT", f"/sessions/{row['id']}/sections/{key}", {
        "version": row["version"], "idempotency_key": f"section-edit-{key}-{row['version']}", "status": "complete",
        "evidence_file_id": row["sections"][key]["evidence_file_id"], "data": {"lines": lines, **extra}})


def line(row, section, category, entity_id, amount="0.00", meaning="zero", **extra):
    return {"category": category, "entity_id": entity_id, "original_amount": amount, "meaning": meaning,
            "evidence_file_id": row["sections"][section]["evidence_file_id"], **extra}


@pytest.mark.asyncio
async def test_inventory_requires_each_account_and_manifest_evidence(api):
    row, _, _ = await prepare(api)
    lines = [line(row, "inventory", "inventory_asset", "warehouse-inventory-a", "70.00", "available_to_us"),
             line(row, "inventory", "inventory_asset", "warehouse-inventory-b", "30.00", "available_to_us")]
    value = {"total_sar": "100.00", "account_totals": {"warehouse-inventory-a": "50.00", "warehouse-inventory-b": "50.00"},
             "manifest_hash": "a" * 64, "evidence_file_id": row["sections"]["inventory"]["evidence_file_id"]}
    row = await section_lines(api, row, "inventory", lines, inventory_valuation=value)
    result = await action(api, row, "preview", status=409)
    assert result["detail"]["code"] == "onboarding_inventory_value_mismatch"
    value["account_totals"] = {"warehouse-inventory-a": "70.00", "warehouse-inventory-b": "30.00"}
    row = await section_lines(api, row, "inventory", lines, inventory_valuation=value)
    row = await action(api, row, "preview")
    assert row["preview"]["inventory_reconciliation"]["verified"]
    assert not row["preview"]["inventory_reconciliation"]["physical_inventory_verified"]
    assert await api.db.warehouse_locations.count_documents({}) == 0


@pytest.mark.asyncio
async def test_provider_requires_explicit_bank_binding_never_inferred_from_payment_method(api):
    row, _, account = await prepare(api)
    lines = [line(row, "providers", "provider_receivable", "tabby", "17.00", "available_to_us")]
    row = await section_lines(api, row, "providers", lines)
    result = await action(api, row, "preview", status=409)
    assert result["detail"]["code"] == "onboarding_provider_binding_required"
    binding = {"provider": "tabby", "bank_account_id": "foreign-bank", "evidence_file_id": lines[0]["evidence_file_id"]}
    await api.db.mz2_financial_accounts.insert_one({"user_id": "another-owner", "id": "foreign-bank", "account_type": "bank", "status": "active", "currency": "SAR"})
    row = await section_lines(api, row, "providers", lines, provider_bindings=[binding])
    await action(api, row, "preview", status=409)
    binding["bank_account_id"] = account["id"]
    row = await section_lines(api, row, "providers", lines, provider_bindings=[binding])
    row = await action(api, row, "preview")
    assert row["preview"]["lines"][1]["original_amount"] == "17.00"
    assert any(mapping.get("kind") == "provider_bank_binding" for mapping in row["preview"]["mappings"])
    assert await api.db.provider_settlement_configs.count_documents({}) == 0


@pytest.mark.asyncio
async def test_supplier_requires_exact_link_and_separate_advance_and_payable(api):
    row, _, _ = await prepare(api)
    await api.db.suppliers.insert_one({"user_id": OWNER, "id": "supplier-exact", "name": "Synthetic"})
    result = await action(api, row, "preview", status=409)
    assert result["detail"]["code"] == "onboarding_supplier_link_required"
    await api.db.counterparties.insert_one({"user_id": OWNER, "id": "supplier-exact", "kind": "supplier", "name": "Synthetic"})
    lines = [line(row, "suppliers", "supplier_payable", "supplier-exact", "25.00", "owed_by_us"),
             line(row, "suppliers", "supplier_advance", "supplier-exact", "10.00", "available_to_us")]
    row = await section_lines(api, row, "suppliers", lines)
    row = await action(api, row, "preview")
    entries = [item for item in row["preview"]["entries"] if item["entity_type"] == "supplier"]
    assert {(item["sub_account"], item["side"], item["sar_amount"]) for item in entries} == {
        ("advance", "debit", "10.00"), ("payable", "credit", "25.00")}


@pytest.mark.asyncio
async def test_duplicate_entity_and_wrong_debit_credit_meaning_reject(api):
    row, _, _ = await prepare(api)
    lines = [line(row, "equity", "prepaid_expense", "lease-asset", "20.00", "available_to_us")]
    row = await section_lines(api, row, "equity", lines * 2)
    result = await action(api, row, "preview", status=409)
    assert result["detail"]["code"] == "opening_duplicate_account"
    lines[0]["meaning"] = "owed_by_us"
    row = await section_lines(api, row, "equity", lines)
    result = await action(api, row, "preview", status=409)
    assert result["detail"]["code"] == "opening_accounting_meaning_mismatch"


@pytest.mark.asyncio
async def test_fx_snapshot_and_original_evidence_are_required_and_locked(api):
    row, _, _ = await prepare(api)
    base_line = line(row, "equity", "prepaid_expense", "lease-asset", "20.00", "available_to_us",
                     original_currency="USD", fx_rate_to_sar="3.75")
    await request(api, "PUT", f"/sessions/{row['id']}/sections/equity", {
        "version": row["version"], "idempotency_key": "invalid-fx-save", "status": "complete",
        "evidence_file_id": base_line["evidence_file_id"], "data": {"lines": [base_line]}}, status=422)
    # Evidence upload still uses the financial barrier; only this synthetic
    # fixture may explicitly unpause to populate immutable evidence.
    await pause(api, False)
    response = await api.client.post(OPENING + "/evidence", headers=_headers("full"),
        data={"purpose": "fx_rate", "section_id": "equity"},
        files={"file": ("fx.txt", b"Synthetic USD SAR rate", "application/octet-stream")})
    assert response.status_code == 200, response.text
    await pause(api)
    base_line.update(fx_at="2026-10-01T00:00:00+03:00", fx_source="Synthetic central rate evidence",
                     fx_evidence_file_id=response.json()["source_file_id"])
    row = await section_lines(api, row, "equity", [base_line])
    row = await action(api, row, "preview")
    asset = next(item for item in row["preview"]["lines"] if item["category"] == "prepaid_expense")
    assert asset["sar_amount"] == "75.00" and asset["fx_snapshot"]["rate_to_sar"] == "3.75"


@pytest.mark.asyncio
async def test_unknown_fields_cannot_mutate_control_or_later_phases(api):
    await pause(api)
    before = await fingerprint(api, exclude=())
    for field in ("writes_paused", "p02_shipping_cod_enabled", "component_lifecycle_starts_at", "ledger_backend_state"):
        await request(api, "POST", "/sessions", {**CREATE, field: False}, status=422)
    assert await fingerprint(api, exclude=()) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [[], {}, 1, None, "unknown"])
async def test_binding_bad_types_fail_at_http_validation(api, value):
    row = await request(api, "POST", "/sessions", CREATE)
    await request(api, "PUT", f"/sessions/{row['id']}/sections/providers", {
        "version": 1, "idempotency_key": "invalid-binding-001", "status": "incomplete",
        "data": {"provider_bindings": [{"provider": value, "bank_account_id": "bank", "evidence_file_id": "evidence"}]}}, status=422)


@pytest.mark.parametrize("value", ["1e100", "1e999999", "NaN", "Infinity", "-1", "1.001", "bad", None])
def test_inventory_decimal_errors_are_stable(value):
    with pytest.raises(HTTPException) as error:
        onboarding._amount(value)
    assert error.value.detail == {"code": "onboarding_inventory_value_mismatch"}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["provider", "bank_account_id", "evidence_file_id"])
async def test_mongo_selectors_are_not_entity_identities(api, field):
    row = await request(api, "POST", "/sessions", CREATE)
    binding = {"provider": "tabby", "bank_account_id": "bank", "evidence_file_id": "evidence"}
    binding[field] = {"$ne": None}
    await request(api, "PUT", f"/sessions/{row['id']}/sections/providers", {
        "version": 1, "idempotency_key": "selector-injection-001", "status": "incomplete",
        "data": {"provider_bindings": [binding]}}, status=422)
    assert (await request(api, "GET", f"/sessions/{row['id']}"))["version"] == 1


@pytest.mark.asyncio
async def test_employee_and_courier_catalogs_match_financial_ssot_without_activation(api):
    await api.db.operating_salaries.insert_many([
        {"user_id": OWNER, "employee_id": "canonical-employee", "category": "employee"},
        {"user_id": OWNER, "id": "rent-is-not-employee", "category": "rent"},
    ])
    await api.db.settings.insert_one({"user_id": OWNER, "shipping_companies": [{"name": "aramex"}]})
    await api.db.mz2_shipping_rate_policies.insert_one({"_id": OWNER, "user_id": OWNER, "versions": [
        {"courier_id": "financial-courier-id", "name": "Synthetic Courier", "verification_status": "approved"},
        {"courier_id": "unapproved", "verification_status": "draft"},
    ]})
    before = await fingerprint(api, exclude=())
    employees = await request(api, "GET", "/identities/employee")
    couriers = await request(api, "GET", "/identities/courier")
    assert [row["id"] for row in employees["items"]] == ["canonical-employee"]
    assert [row["id"] for row in couriers["items"]] == ["financial-courier-id"]
    assert await fingerprint(api, exclude=()) == before


@pytest.mark.parametrize("created,allowed", [
    ("2026-09-30T23:59:59+03:00", False), ("2026-10-01T00:00:00+03:00", True),
    ("2026-10-01T00:00:01+03:00", True), (None, False), ("invalid", False),
    ("2026-10-01T00:00:00", False),
])
def test_exact_owner_target_creation_boundary(created, allowed):
    from accounting_order_cutover import require_order_created_on_or_after_cutover, OrderCutoverError
    if allowed:
        assert require_order_created_on_or_after_cutover(created, CREATE["cutover_at"])
    else:
        with pytest.raises(OrderCutoverError):
            require_order_created_on_or_after_cutover(created, CREATE["cutover_at"])


@pytest.mark.asyncio
async def test_ad_asset_and_payable_are_separate_per_exact_ad_account(api):
    row, _, _ = await prepare(api)
    await api.db.counterparties.insert_one({"user_id": OWNER, "kind": "ad_account", "id": "ad-exact", "name": "Synthetic Ad"})
    await action(api, row, "preview", status=409)
    await pause(api, False)
    accounts = []
    for account_type in ("ad_prepaid_wallet", "ad_payable"):
        response = await api.client.post(FINANCIAL, headers=_headers("full"), json={
            "name": account_type, "account_type": account_type, "currency": "SAR",
            "external_ref": "ad-exact", "idempotency_key": "new-" + account_type})
        assert response.status_code == 200, response.text
        accounts.append(response.json())
    await pause(api)
    lines = [{"category": "financial_account", "financial_account_id": account["id"],
              "meaning": "available_to_us" if account["account_type"] == "ad_prepaid_wallet" else "owed_by_us",
              "original_amount": "15.00", "evidence_file_id": row["sections"]["providers"]["evidence_file_id"]}
             for account in accounts]
    row = await section_lines(api, row, "providers", lines)
    row = await action(api, row, "preview")
    ads = [item for item in row["preview"]["lines"] if item["entity_type"] == "ad_account"]
    assert {(item["sub_account"], item["side"]) for item in ads} == {("balance", "debit"), ("debt", "credit")}
    assert len({item["entity_id"] for item in ads}) == 2
    assert all(item["account_snapshot"]["external_ref"] == "ad-exact" for item in ads)


@pytest.mark.asyncio
async def test_courier_driver_external_receivable_exact_mappings_and_coverage(api):
    row, _, _ = await prepare(api)
    await api.db.mz2_shipping_rate_policies.insert_one({"_id": OWNER, "user_id": OWNER, "versions": [
        {"courier_id": "courier-fin", "name": "Synthetic", "verification_status": "approved"}]})
    await api.db.store_drivers.insert_one({"user_id": OWNER, "id": "driver-fin", "status": "active"})
    await api.db.counterparties.insert_one({"user_id": OWNER, "id": "person-fin", "kind": "general"})
    shipping = [line(row, "couriers_cod", category, entity) for category, entity in (
        ("courier_cod_receivable", "courier-fin"), ("courier_payable", "courier-fin"),
        ("store_driver_cod_receivable", "driver-fin"), ("store_driver_fee_payable", "driver-fin"))]
    row = await section_lines(api, row, "couriers_cod", shipping)
    external = [line(row, "suppliers", "customer_receivable", "person-fin")]
    row = await section_lines(api, row, "suppliers", external)
    row = await action(api, row, "preview")
    assert len(row["preview"]["zero_accounts"]) == 5
    # A foreign identity cannot substitute for an owner identity, even if the
    # caller knows its exact identifier and provides otherwise valid evidence.
    await api.db.counterparties.insert_one({"user_id": "foreign", "id": "other-person", "kind": "general"})
    external[0]["entity_id"] = "other-person"
    row = await section_lines(api, row, "suppliers", external)
    result = await action(api, row, "preview", status=409, key="foreign-preview-002")
    assert result["detail"]["code"] == "onboarding_identity_invalid"


@pytest.mark.asyncio
async def test_identity_catalog_never_returns_other_owner_or_legacy_money(api):
    await api.db.counterparties.insert_many([
        {"id": "person-1", "user_id": OWNER, "kind": "general", "name": "Synthetic", "balance": 9999},
        {"id": "person-2", "user_id": "foreign", "kind": "general", "name": "Private"},
    ])
    before = await fingerprint(api, exclude=())
    rows = await request(api, "GET", "/identities/external_person")
    assert [row["id"] for row in rows["items"]] == ["person-1"]
    assert "balance" not in rows["items"][0]
    assert await fingerprint(api, exclude=()) == before


@pytest.mark.asyncio
async def test_owner_without_grants_can_setup_while_financial_writes_stay_paused(api):
    # All data, including supporting evidence, exists only in the disposable local DB.
    owner = _user(OWNER, [], role="owner")
    owner.pop("accounting_permissions")
    await api.db.users.insert_one(owner)
    access = await api.client.get("/api/financial-provider-apps/accounting-module/access", headers=_headers(OWNER))
    assert access.status_code == 200, access.text
    assert access.json()["is_owner"]
    assert "accounting.opening_balances.view" in access.json()["permissions"]
    assert "accounting.financial_accounts.view" in access.json()["permissions"]
    assert "accounting.opening_balances.post" not in access.json()["permissions"]
    await request(api, "GET", "/definitions", user=OWNER)
    row, _, _ = await prepare(api, user=OWNER)
    before = await fingerprint(api)
    assert (await request(api, "GET", f"/sessions/{row['id']}", user=OWNER))["id"] == row["id"]
    row = await action(api, row, "preview", user=OWNER)
    assert row["preview"]["balanced"]
    row = await action(api, row, "review", user=OWNER)
    assert row["status"] == "reviewed"
    ready = await request(api, "GET", f"/sessions/{row['id']}/readiness", user=OWNER)
    assert ready["financial_writes_paused"] and not ready["ready_for_live_post"]
    assert not ready["p02_activation_allowed"] and not ready["g47_activation_allowed"]
    denied = await action(api, row, "opening-draft", user=OWNER, status=423)
    assert denied["detail"]["code"] == "mz2_writes_paused"
    # Even the existing explicitly granted financial poster cannot bypass pause.
    response = await api.client.post(OPENING + "/drafts/missing/post", headers=_headers("full"),
        json={"version": 1, "idempotency_key": "paused-owner-post-test", "note": "Local fixture only"})
    assert response.status_code == 423, response.text
    assert response.json()["detail"]["code"] == "mz2_writes_paused"
    assert await fingerprint(api) == before
