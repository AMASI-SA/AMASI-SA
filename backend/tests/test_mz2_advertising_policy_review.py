"""Regression checks for policy/source/proposal boundaries on isolated Mongo."""
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from accounting_advertising_contract import AutomationPolicy, POLICIES, POSTINGS, FACTS, ADJUSTMENTS
from accounting_advertising_setup import setup
from accounting_advertising_sources import SOURCES, daily_source
from accounting_advertising_routes import make_advertising_accounting_router
from integrations_control_center.ad_daily_close_proof import close_proof
from tests.test_mz2_advertising_v2 import db, OWNER, DAY, binding
from tests.test_mz2_advertising_automation import configure, AS_OF


def revised_policy(policy, **changes):
    return AutomationPolicy(**{**{key: value for key, value in policy.items()
        if key in AutomationPolicy.model_fields}, **changes})


@pytest.mark.asyncio
async def test_close_delay_requires_provider_observation_after_delay(db):
    _, initial = await configure(db)
    policy = await setup(db, OWNER, revised_policy(initial, close_delay_minutes=60))
    collection = db[SOURCES["meta"][1]]
    row = await collection.find_one({})
    # Midnight Riyadh is21:00UTC. A00:02 observation is not a01:00close proof.
    async def observed_at(instant):
        proof = close_proof(account_id="meta-external", business_date=DAY, timezone="Asia/Riyadh",
            currency="SAR", spend=row["spend_native"], provider_row_count=1, complete_response=True,
            explicit_spend_present=True, identity_proven=True, source_mode=row["source_mode"], observed_at=instant)
        await collection.update_one({"_id": row["_id"]}, {"$set": {
            "observed_at": instant, "updated_at": instant, "source_close_proof": proof}})
    await observed_at("2026-01-02T21:02:00Z")
    with pytest.raises(HTTPException, match="ad_source_observed_before_close_contract"):
        await daily_source(db, OWNER, "meta", "meta-v2", DAY, policy=policy, as_of=AS_OF)
    await observed_at("2026-01-02T22:00:00Z")
    fact = await daily_source(db, OWNER, "meta", "meta-v2", DAY, policy=policy, as_of=AS_OF)
    assert fact["original_amount"] == "100"
    assert await db[POSTINGS].count_documents({}) == 0


@pytest.mark.asyncio
async def test_setup_rejects_timezone_conflicting_with_canonical_account(db):
    _, policy = await configure(db)
    before = await db[POLICIES].count_documents({})
    with pytest.raises(HTTPException, match="ad_policy_account_timezone_mismatch"):
        await setup(db, OWNER, revised_policy(policy, business_timezone="UTC"))
    assert await db[POLICIES].count_documents({}) == before
    assert await db.accounting_journal_groups_v2.count_documents({}) == 0


@pytest.mark.asyncio
async def test_adjustment_propose_endpoint_never_creates_initial_posting(db):
    await configure(db)
    app = FastAPI()
    async def owner():
        return {"id": OWNER}
    app.include_router(make_advertising_accounting_router(db, owner))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/accounting-module/advertising-v2/adjustments/propose", json={
            "platform": "meta", "integration_account_id": "meta-v2", "business_date": DAY})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ad_original_posting_required"
    for collection in (POSTINGS, FACTS, ADJUSTMENTS, "accounting_journal_groups_v2", "accounting_general_ledger_v2"):
        assert await db[collection].count_documents({}) == 0


@pytest.mark.asyncio
async def test_stage12_exposes_stale_policy_binding_version(db):
    from accounting_advertising_bridge import stage12_context
    await configure(db)
    before = (await stage12_context(db, OWNER))["items"][0]
    assert before["daily_spend_readiness"] == "AUTOMATIC_POLICY_CONFIGURED"
    await setup(db, OWNER, binding(version=1))
    after = (await stage12_context(db, OWNER))["items"][0]
    assert after["daily_spend_readiness"] == "NOT_READY"
    assert after["daily_spend_gap"] == "ad_policy_binding_version_mismatch"
