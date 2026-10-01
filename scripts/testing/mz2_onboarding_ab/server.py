"""Disposable loopback A+B browser fixture. Never imports the production server.

Requires MZ2_TEST_MONGO_URI pointing at an isolated localhost replica set.
Only UUID-named synthetic test databases are created, then dropped at shutdown.
"""
import os
import sys
from contextlib import asynccontextmanager
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
from tests.test_financial_accounts_real_mongo import mongo_db, OWNER
from tests.test_accounting_onboarding import prepare, section_lines, line, fingerprint
from accounting_onboarding_ssot import FeePolicyCreate, create_fee_policy


@asynccontextmanager
async def lifespan(app):
    fixture = mongo_db.__wrapped__()
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
        try:
            yield
        finally:
            await fixture.aclose()


app = FastAPI(lifespan=lifespan)
