"""Disposable loopback A+B browser fixture. Never imports the production server.

Requires MZ2_TEST_MONGO_URI pointing at an isolated localhost replica set.
Only UUID-named synthetic test databases are created, then dropped at shutdown.
"""
import os
import sys
from contextlib import asynccontextmanager, AsyncExitStack
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient

uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
if urlparse(uri).hostname not in {"127.0.0.1", "localhost", "::1"}:
    raise RuntimeError("Only an explicitly configured disposable loopback Mongo is allowed")

from financial_provider_apps import make_financial_provider_apps_router
from tests.test_financial_accounts_real_mongo import mongo_db, OWNER, ALL_NEW, _user
from tests.test_accounting_onboarding import prepare, section_lines, line, fingerprint
from accounting_onboarding_ssot import FeePolicyCreate, create_fee_policy


async def expanded_fixture(app, stack):
    """Independent UUID database so original twelve scenarios remain unchanged."""
    fixture = mongo_db.__wrapped__()
    stack.push_async_callback(fixture.aclose)
    db = await anext(fixture)
    child = FastAPI()
    await db.users.insert_one(_user(OWNER, [*ALL_NEW, "accounting.shipping.view", "accounting.rules.manage"], role="owner"))
    async def actor(request: Request):
        return {"id": OWNER}
    child.include_router(make_financial_provider_apps_router(db, actor), prefix="/api")
    async with AsyncClient(transport=ASGITransport(child), base_url="http://test") as client:
        context = SimpleNamespace(db=db, client=client)
        session, evidence, account = await prepare(context, amount="0.00")
        # Existing operational records are synthetic inputs, not created by UAT writes.
        await db.mezan_employees_v2.insert_one({"user_id": OWNER, "id": "uat-employee", "financial_entity_id": "uat-employee", "status": "active", "name": "Synthetic employee"})
        await db.mezan_suppliers_v2.insert_one({"user_id": OWNER, "id": "uat-supplier", "status": "active", "company_name": "Synthetic supplier"})
        await db.store_drivers.insert_one({"user_id": OWNER, "id": "uat-driver", "status": "active", "name": "Synthetic driver"})
        await db.operating_recurring_obligations_v2.insert_one({"user_id": OWNER, "id": "uat-subscription", "status": "active", "title": "Synthetic annual subscription", "expense_type": "subscription"})
        await db.operating_recurring_invoices_v2.insert_one({"user_id": OWNER, "id": "uat-invoice", "obligation_id": "uat-subscription", "payment_status": "paid", "paid_date": "2026-08-21", "period_start": "2026-08-21", "period_end": "2027-08-21", "amount": 3660, "currency": "SAR"})
        # Bind actual Track E setup, with no journal or opening activation.
        from accounting_advertising_contract import Binding
        from accounting_advertising_setup import setup, binding_key
        await db.mezan_integration_accounts_v2.insert_one({"user_id": OWNER, "provider": "meta_ads", "mezan_integration_account_id": "uat-meta", "external_account_id": "uat-platform", "display_name": "Synthetic ad", "currency": "SAR", "timezone": "Asia/Riyadh", "connection_status": "connected"})
        for key, kind in (("uat-wallet", "ad_prepaid_wallet"), ("uat-payable", "ad_payable")):
            await db.mz2_financial_accounts.insert_one({"user_id": OWNER, "id": key, "idempotency_key": key, "name": key, "account_type": kind, "currency": "SAR", "status": "active"})
        await setup(db, OWNER, Binding(platform="meta", integration_account_id="uat-meta", platform_account_id="uat-platform", currency="SAR", funding_mode="hybrid", wallet_financial_account_id="uat-wallet", payable_financial_account_id="uat-payable", hybrid_policy="explicit_split", version=0, evidence="Synthetic signed ad contract"))
        allowed = ("mz2_onboarding_sessions", "mz2_external_persons_v2", "mz2_shipping_setup_v2", "mz2_provider_fee_policies_v2", "mz2_prepaid_selections_v2", "mz2_opening_facts_v2")
        baseline = await fingerprint(context, exclude=allowed)
        @child.get("/__test/proof")
        async def proof():
            return {"session": session, "bank_id": account["id"], "ad_binding_id": binding_key(OWNER, "meta", "uat-meta"), "database": db.name,
                    "non_setup_collections_unchanged": await fingerprint(context, exclude=allowed) == baseline,
                    "shipping_setup_snapshot": await db.mz2_shipping_setup_v2.find_one({"_id": OWNER}, {"_id": 0})}
        app.mount("/expanded", child)
    return fixture


@asynccontextmanager
async def lifespan(app):
    async with AsyncExitStack() as stack:
        expanded = await expanded_fixture(app, stack)
        fixture = mongo_db.__wrapped__()
        stack.push_async_callback(fixture.aclose)
        db = await anext(fixture)
        async def test_user(request: Request):
            return {"id": "full"}
        app.include_router(make_financial_provider_apps_router(db, test_user), prefix="/api")
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            context = SimpleNamespace(db=db, client=client)
            session, evidence, account = await prepare(context, amount="0.00")
            # Track G requires an effective-dated native fee contract for every
            # selected provider. Seed synthetic setup before freezing side effects.
            await create_fee_policy(db, OWNER, "full", FeePolicyCreate(
                provider="tabby", percentage="2.5", fixed_amount="1.00",
                vat_treatment="exclusive", effective_from="2026-01-01",
                effective_to=None, currency="SAR",
                evidence=evidence["providers"]["source_file_id"],
            ))
            rows = [line(session, "providers", "provider_receivable", "tabby", "17.00", "available_to_us")]
            session = await section_lines(context, session, "providers", rows, provider_bindings=[{
                "provider": "tabby", "bank_account_id": account["id"], "evidence_file_id": evidence["providers"]["source_file_id"]}])
            rows = [line(session, "inventory", "inventory_asset", key, value, "available_to_us")
                    for key, value in [("inventory-a", "70.00"), ("inventory-b", "30.00")]]
            session = await section_lines(context, session, "inventory", rows, inventory_valuation={
                "total_sar": "100.00", "account_totals": {"inventory-a": "70.00", "inventory-b": "30.00"},
                "manifest_hash": evidence["inventory"]["sha256"], "evidence_file_id": evidence["inventory"]["source_file_id"]})
            baseline = await fingerprint(context)
            app.state.proof = {"session_id": session["id"], "bank_id": account["id"], "database": db.name}
            @app.get("/__test/proof")
            async def proof():
                return {**app.state.proof, "persisted_sessions": await db.mz2_onboarding_sessions.count_documents({}),
                        "non_session_collections_unchanged": await fingerprint(context) == baseline}
            app.mount("/", StaticFiles(directory=os.environ["MZ2_AB_DIST"], html=True), name="synthetic-ui")
            yield


app = FastAPI(lifespan=lifespan)
