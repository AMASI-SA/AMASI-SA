"""Native advertising acceptance tests against a dedicated real replica set."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import os
import uuid

import pytest
import pytest_asyncio
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener
from pymongo.errors import OperationFailure

from accounting_atomic import atomic_owner
from accounting_advertising_contract import *
from accounting_advertising_setup import setup, _SetupDatabase
from accounting_advertising_sources import daily_source, SOURCES
from accounting_advertising_bridge import post_spend, stage12_context, bank_movement
from accounting_advertising_routes import make_advertising_accounting_router
from accounting_ledger_v2 import ensure_accounting_ledger_v2_indexes, post_journal_v2, query_entries_v2
from accounting_module_contract import OPERATION_ID

OWNER = "track-e-owner"
DAY = "2026-01-02"
AT = "2026-01-01T00:00:00.000000Z"
LEGACY = {"general_ledger", "counterparties", "ad_account_ledger", "ad_accounts", "snapchat_ad_accounts", "accounts"}


class Monitor(CommandListener):
    def __init__(self):
        self.collections = []

    def started(self, event):
        if event.command_name in {"find", "insert", "update", "delete", "aggregate", "count", "distinct"}:
            self.collections.append(event.command[event.command_name])

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


@pytest_asyncio.fixture
async def db():
    uri = os.environ.get("MZ2_AD_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Dedicated MZ2_AD_TEST_MONGO_URI replica set required")
    monitor = Monitor()
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, event_listeners=[monitor])
    database = client["mz2_track_e_test_" + uuid.uuid4().hex]
    assert (await client.admin.command("hello")).get("setName")
    await ensure_accounting_ledger_v2_indexes(database)
    await database.users.insert_one({"id": OWNER, "role": "owner", "is_active": True})
    await database.mz2_atomic_owners.insert_one({"_id": OWNER, "revision": 0, "writes_paused": False,
        "ledger_backend_state": "v2_active", "ledger_backend_revision": 1,
        "ledger_backend_contract_revision": 1, "ledger_backend_activation_ref": "isolated-test"})
    # Local fixture manifest only. Never call production activation or Opening Post.
    await database.settings.insert_one({"user_id": OWNER, "mezan2_financial_cutover": {
        "operation_id": OPERATION_ID, "status": "active", "cutover_at": AT,
        "opening_active_txn_group_id": "zero:test", "opening_root_txn_group_id": "zero:test"}})
    await database.mz2_opening_balance_drafts.insert_one({"user_id": OWNER, "id": "test", "status": "posted",
        "zero_only": True, "txn_group_id": "zero:test", "cutover_at": AT,
        "opening_root_txn_group_id": "zero:test", "preview_hash": "test", "approval_hash": "test",
        "evidence_snapshot": {"fixture": True}})
    try:
        yield database
        assert not LEGACY.intersection(monitor.collections), monitor.collections
    finally:
        await client.drop_database(database.name)
        client.close()


async def seed(db, platform="meta", currency="SAR", amount=30):
    provider, collection = SOURCES[platform]
    await db.mezan_integration_accounts_v2.insert_one({"user_id": OWNER, "provider": provider,
        "mezan_integration_account_id": platform + "-v2", "external_account_id": platform + "-external",
        "display_name": platform + " ad account", "currency": currency, "timezone": "Asia/Riyadh",
        "connection_status": "connected"})
    for suffix, kind in (("wallet", "ad_prepaid_wallet"), ("payable", "ad_payable")):
        await db.mz2_financial_accounts.insert_one({"user_id": OWNER, "id": platform + "-" + suffix,
            "account_type": kind, "currency": currency, "status": "active", "version": 1})
    source = dict(user_id=OWNER, provider=provider, ad_account_id=platform + "-external",
        updated_at="2026-01-03T03:00:00+00:00", account_timezone="Asia/Riyadh")
    if platform == "snapchat":
        source.update(report_date=DAY, projection_timezone="Asia/Riyadh", action_report_time="conversion",
            currency=currency, base_spend_native=amount, amount_complete=True, data_state="confirmed_data",
            source_sync_run_ids=["sync-1"], source_fact_count=24, source_latest_updated_at=source["updated_at"])
    else:
        source.update(date=DAY, currency_native=currency, spend_native=amount,
            source_mode={"meta": "meta_marketing_reporting_v2", "tiktok": "tiktok_marketing_reporting_v2",
                         "google_ads": "google_ads_reporting_v1"}[platform],
            source_only=True, accounting_eligible=False, observed_at=source["updated_at"], empty_provider_row=False)
    await db[collection].insert_one(source)


def binding(platform="meta", mode="postpaid", currency="SAR", version=0):
    return Binding(platform=platform, integration_account_id=platform + "-v2",
        platform_account_id=platform + "-external", currency=currency, funding_mode=mode,
        wallet_financial_account_id=platform + "-wallet" if mode != "postpaid" else None,
        payable_financial_account_id=platform + "-payable" if mode != "prepaid" else None,
        hybrid_policy="explicit_split" if mode == "hybrid" else None, version=version, evidence="Owner payment terms")


async def approved(db, platform="meta", mode="postpaid", currency="SAR"):
    await setup(db, OWNER, binding(platform, mode, currency))
    await setup(db, OWNER, Expense(purpose="advertising", entity_id="approved-ad-expense", evidence="Owner expense authority"))
    fact = await daily_source(db, OWNER, platform, platform + "-v2", DAY)
    return await setup(db, OWNER, SpendApproval(platform=platform, integration_account_id=platform + "-v2",
        business_date=DAY, expected_source_revision=fact["source_revision"], evidence="Owner native promotion",
        completeness_evidence="Reviewed provider closed-day statement", timezone_evidence="Provider timezone settings"))


async def seed_wallet(db, amount="100.00", platform="meta"):
    async def commit(scoped):
        return await post_journal_v2(scoped._db, user_id=OWNER, actor_id=OWNER, actor_name=OWNER,
            idempotency_key="fixture-wallet-" + platform, txn_type="test_fixture", source="isolated_test",
            effective_at=AT, entries=[leg("ad_account", platform + "-wallet", "balance", "debit", amount),
                leg("equity", "fixture", None, "credit", amount)], mongo_session=scoped._session)
    await atomic_owner(db, OWNER, commit)


async def posted_legs(db, result):
    return await db.accounting_general_ledger_v2.find({"txn_group_id": result["txn_group_id"]}).to_list(20)


@pytest.mark.asyncio
@pytest.mark.parametrize("platform", list(SOURCES))
async def test_all_v2_platforms_native_postpaid(db, platform):
    await seed(db, platform)
    fact = await approved(db, platform)
    result = await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"]))
    rows = await posted_legs(db, result)
    assert {(r["entity_type"], r["entity_id"], r["sub_account"], r["side"], r["amount"]) for r in rows} == {
        ("expense", "approved-ad-expense", None, "debit", "30.00"),
        ("ad_account", platform + "-payable", "debt", "credit", "30.00")}
    context = await stage12_context(db, OWNER)
    assert context["items"][0]["readiness"] == "SETUP_READY"
    assert context["items"][0]["integration_account_id"] == platform + "-v2"
    if platform != "snapchat":
        raw = await db[SOURCES[platform][1]].find_one({})
        assert raw["accounting_eligible"] is False and fact["source_accounting_eligible"] is False
        assert fact["native_accounting_eligible"] is True


@pytest.mark.asyncio
async def test_prepaid_and_explicit_hybrid_legs(db):
    await seed(db)
    fact = await approved(db, mode="prepaid")
    await seed_wallet(db)
    rows = await posted_legs(db, await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"])))
    assert any(r["entity_id"] == "meta-wallet" and r["side"] == "credit" and r["amount"] == "30.00" for r in rows)
    await seed(db, "tiktok")
    hybrid = await approved(db, "tiktok", mode="hybrid")
    await seed_wallet(db, "10.00", "tiktok")
    with pytest.raises(HTTPException, match="ad_hybrid_explicit_split_required"):
        await post_spend(db, OWNER, SpendPost(snapshot_id=hybrid["id"]))
    rows = await posted_legs(db, await post_spend(db, OWNER, SpendPost(snapshot_id=hybrid["id"], wallet_sar_amount="10.00")))
    assert {(r["sub_account"], r["amount"]) for r in rows if r["side"] == "credit"} == {("balance", "10.00"), ("debt", "20.00")}


@pytest.mark.asyncio
async def test_wallet_cannot_go_negative_even_with_concurrent_days(db):
    await seed(db, amount=60)
    first = await approved(db, mode="prepaid")
    await seed_wallet(db)
    raw = await db[SOURCES["meta"][1]].find_one({})
    raw.pop("_id")
    raw.update(date="2026-01-03", observed_at="2026-01-04T03:00:00+00:00", updated_at="2026-01-04T03:00:00+00:00")
    await db[SOURCES["meta"][1]].insert_one(raw)
    fact = await daily_source(db, OWNER, "meta", "meta-v2", "2026-01-03")
    second = await setup(db, OWNER, SpendApproval(platform="meta", integration_account_id="meta-v2",
        business_date="2026-01-03", expected_source_revision=fact["source_revision"], evidence="approved",
        completeness_evidence="provider statement", timezone_evidence="provider settings"))
    results = await asyncio.gather(*(post_spend(db, OWNER, SpendPost(snapshot_id=f["id"])) for f in (first, second)), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert any(isinstance(r, HTTPException) and r.detail["code"] == "ad_wallet_insufficient_balance" for r in results)
    assert await db[POSTINGS].count_documents({}) == 1


@pytest.mark.asyncio
async def test_retry_concurrency_and_changed_source_never_duplicate(db):
    await seed(db)
    fact = await approved(db)
    payload = SpendPost(snapshot_id=fact["id"])
    results = await asyncio.gather(*(post_spend(db, OWNER, payload) for _ in range(6)))
    assert len({r["txn_group_id"] for r in results}) == 1
    assert sum(r["status"] == "posted" for r in results) == 1
    assert (await post_spend(db, OWNER, payload))["status"] == "already_posted"
    await db[SOURCES["meta"][1]].update_one({}, {"$set": {"spend_native": 31}})
    with pytest.raises(HTTPException, match="ad_adjustment_reconciliation_required"):
        await post_spend(db, OWNER, payload)
    current = await daily_source(db, OWNER, "meta", "meta-v2", DAY)
    newer = await setup(db, OWNER, SpendApproval(platform="meta", integration_account_id="meta-v2", business_date=DAY,
        expected_source_revision=current["source_revision"], evidence="newer evidence",
        completeness_evidence="new provider statement", timezone_evidence="provider settings"))
    with pytest.raises(HTTPException, match="ad_adjustment_reconciliation_required"):
        await post_spend(db, OWNER, SpendPost(snapshot_id=newer["id"]))
    assert await db[POSTINGS].count_documents({}) == 1
    assert await db.accounting_general_ledger_v2.count_documents({}) == 2


@pytest.mark.asyncio
async def test_paused_setup_only_and_financial_423(db):
    await seed(db)
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    before = await db.mz2_atomic_owners.find_one({"_id": OWNER})
    fact = await approved(db)
    assert await db.mz2_atomic_owners.find_one({"_id": OWNER}) == before
    assert await db.accounting_general_ledger_v2.count_documents({}) == 0
    assert await db[AUDIT].count_documents({}) == 3
    with pytest.raises(HTTPException) as raised:
        await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"]))
    assert raised.value.status_code == 423
    assert await db.mz2_atomic_owners.find_one({"_id": OWNER}) == before
    with pytest.raises(HTTPException) as raised:
        await bank_movement(db, OWNER, bank_payload())
    assert raised.value.status_code == 423


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["foreign", "inactive", "wrong_type", "currency", "missing_v2", "disconnected"])
async def test_invalid_financial_or_integration_binding(db, mutation):
    await seed(db)
    if mutation == "missing_v2":
        await db.mezan_integration_accounts_v2.delete_many({})
    elif mutation == "disconnected":
        await db.mezan_integration_accounts_v2.update_one({}, {"$set": {"connection_status": "disconnected"}})
    else:
        change = {"foreign": {"user_id": "other"}, "inactive": {"status": "archived"},
                  "wrong_type": {"account_type": "bank"}, "currency": {"currency": "USD"}}[mutation]
        await db.mz2_financial_accounts.update_one({"id": "meta-payable"}, {"$set": change})
    with pytest.raises(HTTPException):
        await setup(db, OWNER, binding())
    assert await db[BINDINGS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_fx_snapshot_is_required_reproducible_and_tenant_scoped(db):
    await seed(db, currency="USD", amount=10)
    fact = await approved(db, currency="USD")
    with pytest.raises(HTTPException, match="ad_fx_snapshot_required"):
        await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"]))
    fx = await setup(db, OWNER, FxSnapshot(currency="USD", business_date=DAY, fx_rate_to_sar="3.74",
        fx_at="2026-01-02T12:00:00+03:00", fx_source="Bank quote", evidence="Reviewed quote 7"))
    result = await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"], fx_snapshot_id=fx["id"]))
    rows = await posted_legs(db, result)
    assert {r["amount"] for r in rows} == {"37.40"}
    group = await db.accounting_journal_groups_v2.find_one({"txn_group_id": result["txn_group_id"]})
    assert group["metadata"]["ad_fx"]["fx_rate_to_sar"] == "3.74"
    assert group["metadata"]["ad_fx"]["original_amount"] == "10"


def bank_payload(**changes):
    return BankMovement(**dict(dict(platform="meta", integration_account_id="meta-v2", kind="wallet_funding",
        bank_financial_account_id="approved-bank", amount_sar="100.00", bank_evidence_id="statement-line",
        effective_at="2026-01-02T12:00:00+03:00"), **changes))


@pytest.mark.parametrize("kind,sub", [("wallet_funding", "balance"), ("payable_settlement", "debt")])
def test_funding_and_settlement_are_transfers_and_fee_is_separate(kind, sub):
    bind = binding(mode="hybrid").model_dump()
    bank = dict(entity_type="bank", entity_id="approved-bank", sub_account="main")
    rows = bank_movement_legs(bind, bank, bank_payload(kind=kind))
    assert [(r["entity_type"], r["sub_account"], r["side"], r["amount"]) for r in rows] == [
        ("ad_account", sub, "debit", "100.00"), ("bank", "main", "credit", "100.00")]
    with pytest.raises(HTTPException, match="ad_bank_fee_evidence_and_identity_required"):
        bank_movement_legs(bind, bank, bank_payload(kind=kind, bank_fee_sar="2.00"))
    rows = bank_movement_legs(bind, bank, bank_payload(kind=kind, bank_fee_sar="2.00", bank_fee_evidence="statement fee"), "fee-expense")
    assert rows[1]["amount"] == "102.00"
    assert rows[2]["entity_id"] == "fee-expense" and rows[2]["amount"] == "2.00"
    assert all(r["entity_id"] != "approved-ad-expense" for r in rows)


@pytest.mark.asyncio
async def test_track_a_port_fails_closed(db):
    await seed(db)
    await approved(db, mode="prepaid")
    with pytest.raises(HTTPException, match="track_a_require_financial_ledger_identity_not_integrated"):
        await bank_movement(db, OWNER, bank_payload())
    assert await db.accounting_general_ledger_v2.count_documents({}) == 0


@pytest.mark.asyncio
async def test_legacy_only_absent_and_owner_scope(db):
    # Legacy sentinels seeded via a separate unmonitored client. Native path must never read them.
    separate = AsyncIOMotorClient(os.environ["MZ2_AD_TEST_MONGO_URI"])
    try:
        for name in LEGACY:
            await separate[db.name][name].insert_one({"user_id": OWNER, "id": "legacy-only"})
        assert (await stage12_context(db, OWNER))["items"] == []
        await seed(db)
        await db.users.insert_one({"id": "employee", "role": "employee", "created_by": OWNER,
            "accounting_permissions": ["accounting.financial_accounts.view"]})
        with pytest.raises(HTTPException) as raised:
            await stage12_context(db, "employee")
        assert raised.value.status_code == 403
        await db.users.insert_one({"id": "other", "role": "owner"})
        assert (await stage12_context(db, "other"))["items"] == []
    finally:
        separate.close()


@pytest.mark.asyncio
async def test_setup_boundary_forbids_finance_control_and_auth_even_if_caught(db):
    async with await db.client.start_session() as session:
        state = {"failed": False}
        scoped = _SetupDatabase(db, session, state)
        for name in ["accounting_general_ledger_v2", "mz2_atomic_owners", "settings", "banks", "mz2_opening_balance_drafts"]:
            with pytest.raises(HTTPException, match="ad_setup_collection_forbidden"):
                scoped[name]
        for name in ["users", "mz2_financial_accounts", "mezan_integration_accounts_v2"]:
            with pytest.raises(HTTPException, match="ad_setup_operation_forbidden"):
                scoped[name].update_one({}, {"$set": {"unsafe": True}})
        assert state["failed"] is True


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_binding(db):
    await seed(db)
    await db.create_collection(AUDIT, validator={"required_impossible_field": {"$exists": True}})
    with pytest.raises(OperationFailure):
        await setup(db, OWNER, binding())
    assert await db[BINDINGS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_source_revision_is_material_not_refresh_timestamp(db):
    await seed(db)
    fact = await approved(db)
    await db[SOURCES["meta"][1]].update_one({}, {"$set": {"updated_at": "2026-01-04T03:00:00+00:00", "observed_at": "2026-01-04T03:00:00+00:00"}})
    assert (await daily_source(db, OWNER, "meta", "meta-v2", DAY))["source_revision"] == fact["source_revision"]
    retried = await setup(db, OWNER, SpendApproval(platform="meta", integration_account_id="meta-v2",
        business_date=DAY, expected_source_revision=fact["source_revision"], evidence="Owner native promotion",
        completeness_evidence="Reviewed provider closed-day statement", timezone_evidence="Provider timezone settings"))
    assert retried == fact
    assert (await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"])))["status"] == "posted"


@pytest.mark.asyncio
async def test_http_pause_and_context(db):
    app = FastAPI()
    async def user(request: Request):
        return {"id": request.headers.get("x-user", OWNER)}
    app.include_router(make_advertising_accounting_router(db, user), prefix="/api")
    await seed(db)
    await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"writes_paused": True}})
    base = "/api/accounting-module/advertising-v2"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(base + "/binding", json=binding().model_dump(mode="json"))
        assert response.status_code == 200, response.text
        response = await client.get(base + "/stage-12")
        assert response.status_code == 200 and len(response.json()["items"]) == 1
        response = await client.post(base + "/spend-post", json={"snapshot_id": "0" * 64})
        assert response.status_code == 423
        response = await client.get(base + "/stage-12", headers={"x-user": "unknown"})
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_snapchat_actual_bson_datetime_and_projection_scope(db):
    await seed(db, "snapchat")
    collection = db[SOURCES["snapchat"][1]]
    stamp = datetime(2026, 1, 3, 3, tzinfo=timezone.utc)
    await collection.update_one({}, {"$set": {"updated_at": stamp, "source_latest_updated_at": stamp}})
    alternate = await collection.find_one({})
    alternate.pop("_id")
    alternate.update(projection_timezone="America/New_York", base_spend_native=999)
    await collection.insert_one(alternate)
    fact = await approved(db, "snapchat")
    assert fact["original_amount"] == "30"
    assert (await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"])))["status"] == "posted"


@pytest.mark.asyncio
@pytest.mark.parametrize("change,code", [
    ({"empty_provider_row": True}, "ad_daily_empty_provider_row"),
    ({"spend_native": 0}, "ad_zero_requires_separate_reconciliation"),
    ({"currency_native": None}, "ad_source_currency_or_amount_missing"),
    ({"observed_at": "2026-01-02T03:00:00+00:00"}, "ad_source_freshness_invalid"),
    ({"observed_at": "2026-01-03T03:00:00"}, "ad_source_timestamp_invalid"),
    ({"account_timezone": "UTC"}, "ad_source_timezone_mismatch"),
    ({"source_mode": "legacy"}, "ad_source_provenance_missing"),
])
async def test_daily_source_exact_gaps(db, change, code):
    await seed(db)
    await db[SOURCES["meta"][1]].update_one({}, {"$set": change})
    with pytest.raises(HTTPException, match=code):
        await daily_source(db, OWNER, "meta", "meta-v2", DAY)


@pytest.mark.asyncio
async def test_source_approval_and_post_require_current_evidence(db):
    await seed(db)
    await setup(db, OWNER, binding())
    with pytest.raises(HTTPException, match="ad_source_changed_refresh_required"):
        await setup(db, OWNER, SpendApproval(platform="meta", integration_account_id="meta-v2", business_date=DAY,
            expected_source_revision="0" * 64, evidence="approval", completeness_evidence="complete", timezone_evidence="provider"))
    assert await db[FACTS].count_documents({}) == 0
    with pytest.raises(HTTPException, match="ad_source_day_not_closed"):
        await daily_source(db, OWNER, "meta", "meta-v2", "2099-01-01")
    with pytest.raises(HTTPException, match="ad_daily_v2_fact_missing"):
        await daily_source(db, OWNER, "meta", "meta-v2", "2026-01-01")


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["paused_transition", "closed_period", "inactive_binding", "missing_expense", "source_changed", "inactive_owner"])
async def test_post_revalidates_all_gates(db, case):
    await seed(db)
    fact = await approved(db)
    if case == "paused_transition":
        await db.mz2_atomic_owners.update_one({"_id": OWNER}, {"$set": {"ledger_backend_state": "transition_blocked"}})
    elif case == "closed_period":
        await db.mz2_accounting_periods.insert_one({"user_id": OWNER, "month": "2026-01", "closed": True})
    elif case == "inactive_binding":
        await db.mz2_financial_accounts.update_one({"id": "meta-payable"}, {"$set": {"status": "archived"}})
    elif case == "missing_expense":
        await db[EXPENSES].delete_many({})
    elif case == "source_changed":
        await db[SOURCES["meta"][1]].update_one({}, {"$set": {"spend_native": 40}})
    else:
        await db.users.update_one({"id": OWNER}, {"$set": {"is_active": False}})
    with pytest.raises(HTTPException):
        await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"]))
    assert await db.accounting_general_ledger_v2.count_documents({}) == 0
    assert await db[POSTINGS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_binding_cas_immutable_expense_and_explicit_hybrid(db):
    await seed(db)
    await setup(db, OWNER, binding())
    with pytest.raises(HTTPException, match="ad_binding_version_conflict"):
        await setup(db, OWNER, binding())
    invalid = binding(mode="hybrid", version=1).model_copy(update={"hybrid_policy": None})
    with pytest.raises(HTTPException, match="ad_binding_funding_contract_invalid"):
        await setup(db, OWNER, invalid)
    await setup(db, OWNER, Expense(purpose="advertising", entity_id="expense-1", evidence="approved"))
    with pytest.raises(HTTPException, match="ad_immutable_contract_conflict"):
        await setup(db, OWNER, Expense(purpose="advertising", entity_id="expense-2", evidence="changed"))
    with pytest.raises(HTTPException, match="ad_expense_purposes_must_be_separate"):
        await setup(db, OWNER, Expense(purpose="bank_fee", entity_id="expense-1", evidence="fee"))


@pytest.mark.asyncio
async def test_backdated_spend_cannot_hide_negative_interval(db):
    await seed(db, amount=50)
    fact = await approved(db, mode="prepaid")
    await seed_wallet(db)
    async def activity(scoped):
        for key, instant, side, amount in [("spend", "2026-01-03T00:00:00Z", "credit", "90.00"),
                                          ("fund", "2026-01-04T00:00:00Z", "debit", "100.00")]:
            await post_journal_v2(scoped._db, user_id=OWNER, actor_id=OWNER, actor_name=OWNER,
                idempotency_key="fixture-" + key, txn_type="test_fixture", source="isolated_test",
                effective_at=instant, entries=[leg("ad_account", "meta-wallet", "balance", side, amount),
                    leg("equity", "fixture", None, "credit" if side == "debit" else "debit", amount)],
                mongo_session=scoped._session)
    await atomic_owner(db, OWNER, activity)
    with pytest.raises(HTTPException, match="ad_wallet_insufficient_balance"):
        await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"]))
    assert await db[POSTINGS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_fx_foreign_snapshot_and_foreign_wallet_fail_closed(db):
    await seed(db, currency="USD")
    fact = await approved(db, currency="USD")
    fx = await setup(db, OWNER, FxSnapshot(currency="USD", business_date=DAY, fx_rate_to_sar="3.75",
        fx_at="2026-01-02T00:00:00Z", fx_source="explicit quote", evidence="approved quote"))
    await db[FX].update_one({"id": fx["id"]}, {"$set": {"user_id": "other"}})
    with pytest.raises(HTTPException, match="ad_fx_snapshot_invalid"):
        await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"], fx_snapshot_id=fx["id"]))
    await db[FX].update_one({"id": fx["id"]}, {"$set": {"user_id": OWNER}})
    await setup(db, OWNER, binding(mode="prepaid", currency="USD", version=1))
    with pytest.raises(HTTPException, match="ad_wallet_original_opening_evidence_required"):
        await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"], fx_snapshot_id=fx["id"]))
    assert (await stage12_context(db, OWNER))["items"][0]["missing_contract_reason"] == "ad_wallet_original_opening_evidence_required"


@pytest.mark.asyncio
async def test_post_marker_failure_rolls_back_entire_journal(db):
    await seed(db)
    fact = await approved(db)
    await db.create_collection(POSTINGS, validator={"impossible_field": {"$exists": True}})
    with pytest.raises(OperationFailure):
        await post_spend(db, OWNER, SpendPost(snapshot_id=fact["id"]))
    assert await db.accounting_general_ledger_v2.count_documents({}) == 0
    assert await db.accounting_journal_groups_v2.count_documents({}) == 0
    assert await db.accounting_audit_log_v2.count_documents({}) == 0


@pytest.mark.asyncio
async def test_setup_does_not_bootstrap_write_control(db):
    await seed(db)
    await db.mz2_atomic_owners.delete_many({})
    await setup(db, OWNER, binding())
    assert await db.mz2_atomic_owners.count_documents({}) == 0
    assert await db.accounting_general_ledger_v2.count_documents({}) == 0
