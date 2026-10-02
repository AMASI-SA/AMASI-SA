"""Explicit synthetic C5 fixture; shipped JWT verifier, routers and Mongo.

No production server/startup, schedules, external provider, activation or journal
writer. The initial control states are fixture facts and are never toggled.
"""
import hashlib
import ipaddress
import json
import os
import secrets
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from bson import json_util
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))
URI = os.environ.get("MZ2_TEST_MONGO_URI", "")
if (urlparse(URI).hostname not in {"127.0.0.1", "localhost", "::1"}
        or os.environ.get("MZ2_C5_EXECUTE") != "isolated-authorized"
        or os.environ.get("PYTHON_DOTENV_DISABLED") != "1"):
    raise RuntimeError("Explicit isolated fixture authority and loopback Mongo required")
EVIDENCE = Path(os.environ["MZ2_C5_EVIDENCE"])
if not EVIDENCE.is_absolute() or not Path(os.environ["MZ2_C5_DIST"]).is_absolute():
    raise RuntimeError("External absolute evidence/build paths required")
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(64))


def network_audit(event, args):
    address = None
    if event == "socket.getaddrinfo":
        address = args[0]
    elif event == "socket.connect":
        address = args[1][0] if isinstance(args[1], tuple) else args[1]
    elif event == "socket.sendto":
        address = args[2][0] if isinstance(args[2], tuple) else args[2]
    if address is None:
        return
    try:
        local = ipaddress.ip_address(address).is_loopback
    except ValueError:
        local = str(address) == "localhost"
    if not local:
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        with (EVIDENCE / "denied-network.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, "host": str(address)}) + "\n")
        raise PermissionError("C5 fixture refuses non-loopback network")


sys.addaudithook(network_audit)

from auth import create_access_token, get_current_user_from_db
from accounting_financial_accounts import ensure_financial_account_indexes, PERMISSIONS
from accounting_ledger_v2 import ensure_accounting_ledger_v2_indexes
from accounting_shipping_native_setup import save_setup, ensure_shipping_native_indexes
from accounting_shipping_native_contract import CourierInput
from accounting_advertising_setup import setup, binding_key
from accounting_advertising_contract import Binding
from financial_provider_apps import make_financial_provider_apps_router

ALLOWED_SETUP = {
    "mz2_onboarding_sessions", "mz2_external_persons_v2", "mz2_shipping_setup_v2",
    "mz2_provider_fee_policies_v2", "mz2_prepaid_selections_v2", "mz2_opening_facts_v2",
    "accounting_source_files", "mz2_opening_evidence",
}


def digest(value):
    return hashlib.sha256(json_util.dumps(value, sort_keys=True).encode()).hexdigest()


def git(*arguments):
    return subprocess.check_output(["git", "-C", str(ROOT), *arguments], text=True).strip()


async def fingerprints(db):
    result = {}
    for name in sorted(await db.list_collection_names()):
        rows = await db[name].find({}).to_list(None)
        # Existing transactional serialization may advance revision. Preserve
        # every control flag/value in the comparison, with no exclusion by name.
        if name == "mz2_atomic_owners":
            rows = [{k: v for k, v in row.items() if k != "revision"} for row in rows]
        result[name] = digest(sorted(json_util.dumps(row, sort_keys=True) for row in rows))
    return result


@asynccontextmanager
async def lifespan(app):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    client = AsyncIOMotorClient(URI, serverSelectionTimeoutMS=5000)
    hello = await client.admin.command("hello")
    if not hello.get("setName"):
        raise RuntimeError("Actual replica set required")
    name = "mz2_c5_business_" + uuid4().hex
    if name in await client.list_database_names():
        raise RuntimeError("Refusing existing database")
    db = client[name]
    owner, paused_owner = "c5-owner-" + uuid4().hex, "c5-paused-" + uuid4().hex
    try:
        permissions = sorted(set(PERMISSIONS.values()) | {
            "accounting.settlements.view", "accounting.shipping.view",
            "accounting.rules.manage", "accounting.shipping.contracts.review",
        })
        for identity, paused in [(owner, False), (paused_owner, True)]:
            await db.users.insert_one({"id": identity, "role": "owner", "name": "C5 Synthetic Owner",
                "email": identity + "@synthetic.invalid", "accounting_permissions": permissions,
                "is_active": True, "disabled": False})
            await db.mz2_atomic_owners.insert_one({"_id": identity, "revision": 0,
                "writes_paused": paused, "control_revision": 0,
                "control_reason": "Declared synthetic initial fixture; never toggle"})
        await ensure_accounting_ledger_v2_indexes(db)
        await ensure_financial_account_indexes(db)
        await ensure_shipping_native_indexes(db)
        await db.mezan_employees_v2.insert_one({"user_id": owner, "id": "c5-employee",
            "financial_entity_id": "c5-employee", "status": "active", "name": "C5 Synthetic Employee"})
        await db.mezan_suppliers_v2.insert_one({"user_id": owner, "id": "c5-supplier",
            "status": "active", "company_name": "C5 Synthetic Supplier"})
        await db.store_drivers.insert_one({"user_id": owner, "id": "c5-driver", "status": "active",
            "name": "C5 Synthetic Driver"})
        await db.operating_recurring_obligations_v2.insert_one({"user_id": owner,
            "id": "c5-subscription", "status": "active", "title": "C5 Synthetic annual subscription",
            "expense_type": "subscription"})
        await db.operating_recurring_invoices_v2.insert_one({"user_id": owner, "id": "c5-invoice",
            "obligation_id": "c5-subscription", "payment_status": "paid", "paid_date": "2026-08-21",
            "period_start": "2026-08-21", "period_end": "2027-08-21", "amount": 3660, "currency": "SAR"})
        ad_id = binding_key(owner, "meta", "c5-meta")
        for identity, kind in [("c5-bank", "bank"), ("c5-cash", "cash"), ("c5-overdraft", "overdraft"),
                               ("c5-wallet", "ad_prepaid_wallet"), ("c5-ad-payable", "ad_payable")]:
            await db.mz2_financial_accounts.insert_one({"user_id": owner, "id": identity,
                "idempotency_key": identity, "name": "Synthetic " + identity, "account_type": kind,
                "currency": "SAR", "status": "active", "version": 1,
                "external_ref": ad_id if kind.startswith("ad_") else "c5-explicit-register:" + identity})
        await db.mezan_integration_accounts_v2.insert_one({"user_id": owner, "provider": "meta_ads",
            "mezan_integration_account_id": "c5-meta", "external_account_id": "c5-platform",
            "display_name": "C5 Synthetic Ads", "currency": "SAR", "timezone": "Asia/Riyadh",
            "connection_status": "connected"})
        await setup(db, owner, Binding(platform="meta", integration_account_id="c5-meta",
            platform_account_id="c5-platform", currency="SAR", funding_mode="hybrid",
            wallet_financial_account_id="c5-wallet", payable_financial_account_id="c5-ad-payable",
            hybrid_policy="explicit_split", version=0, evidence="Declared C5 synthetic ad contract"))
        await save_setup(db, owner, owner, CourierInput(request_id="c5-courier-prerequisite", version=0,
            confirmed=True, reason="Declared C5 canonical courier", courier_key="c5-courier",
            name="C5 Synthetic Courier", salla_carrier_keys=["c5-synthetic-carrier"]))
        await db.mezan_products_v2.insert_one({"user_id": owner, "mezan_product_id": "c5-product",
            "name": "C5 عباية اختبار", "sku": "C5-ABAYA", "barcode": "C5001", "main_image": "/fixture.svg",
            "options": [{"id": "color", "name": "اللون", "values": [{"id": "black", "name": "أسود"}, {"id": "blue", "name": "أزرق"}]}],
            "variants": [{"id": "black-54", "sku": "C5-BLACK", "barcode": "C5002", "selections": [{"name": "اللون", "value": "أسود"}]},
                         {"id": "blue-54", "sku": "C5-BLUE", "barcode": "C5003", "selections": [{"name": "اللون", "value": "أزرق"}]}]})
        await db.mezan_cost_resources_v2.insert_one({"user_id": owner, "id": "fabric", "name": "C5 قماش",
            "code": "C5-FAB", "unit": "meter", "category_ids": ["fabric-cat"], "status": "active",
            "track_inventory": True, "kind": "material"})
        await db.mezan_component_categories_v2.insert_one({"user_id": owner, "id": "fabric-cat", "name": "C5 أقمشة"})
        baseline = await fingerprints(db)
        initial_controls = await db.mz2_atomic_owners.find({}).to_list(None)
        source = {"head": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "database": name, "owner": owner, "paused_guard_owner": paused_owner,
            "replica_set": hello["setName"], "source_register_sha256": hashlib.sha256((HERE / "source-register.json").read_bytes()).hexdigest(),
            "initial_controls": initial_controls, "prerequisites": "Synthetic identities/catalogue/paid invoice and confirmed setup; no sessions, policy, facts, evidence, journals or opening",
            "auth": "Declared synthetic signed session; existing JWT verifier and fresh stored permissions"}
        (EVIDENCE / "fixture-manifest.json").write_text(json_util.dumps(source, indent=2), encoding="utf-8")
        (EVIDENCE / "baseline-fingerprints.json").write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        async def authenticated(request: Request):
            return await get_current_user_from_db(request, db)
        app.include_router(make_financial_provider_apps_router(db, authenticated), prefix="/api")
        @app.get("/__test/bootstrap")
        async def bootstrap():
            # Fixture-only secrets returned to the isolated runner; never persisted.
            return {"owner": owner, "paused_owner": paused_owner, "ad_binding_id": ad_id,
                "token": create_access_token(owner, owner + "@synthetic.invalid", mfa_verified=True),
                "paused_token": create_access_token(paused_owner, paused_owner + "@synthetic.invalid", mfa_verified=True)}
        @app.get("/__test/proof")
        async def proof():
            after = await fingerprints(db)
            unchanged = all(before == after.get(key) for key, before in baseline.items() if key not in ALLOWED_SETUP)
            unexpected = sorted(set(after) - set(baseline) - ALLOWED_SETUP)
            controls = await db.mz2_atomic_owners.find({}).to_list(None)
            money = {collection: await db[collection].count_documents({}) for collection in (
                "accounting_journal_groups_v2", "accounting_general_ledger_v2", "general_ledger",
                "mz2_opening_balance_drafts", "mz2_opening_inventory_initializations")}
            return {"source": source, "financial_counts": money, "non_setup_unchanged": unchanged,
                "no_external_network_attempts": not (EVIDENCE / "denied-network.jsonl").exists(),
                "unexpected_collections": unexpected, "fingerprints": after,
                "controls": json.loads(json_util.dumps(controls)),
                "current_head": git("rev-parse", "HEAD"),
                "sessions": await db.mz2_onboarding_sessions.find({}, {"_id": 0, "requests": 0}).to_list(None),
                "evidence": await db.mz2_opening_evidence.find({}, {"_id": 0}).to_list(None)}
        @app.get("/fixture.svg")
        async def product_image():
            return Response('<svg xmlns="http://www.w3.org/2000/svg" width="140" height="140"><rect width="140" height="140" fill="#d1fae5"/><text x="15" y="70">C5 SYNTHETIC</text></svg>', media_type="image/svg+xml")
        app.mount("/", StaticFiles(directory=os.environ["MZ2_C5_DIST"], html=True))
        yield
    finally:
        try:
            final = await proof() if "proof" in locals() else {"fixture_setup_incomplete": True, "database": name}
            (EVIDENCE / "final-proof.json").write_text(json_util.dumps(final, indent=2), encoding="utf-8")
        except Exception as error:
            (EVIDENCE / "final-proof-error.json").write_text(json.dumps({"kind": type(error).__name__}), encoding="utf-8")
        if db.name != name or not name.startswith("mz2_c5_business_"):
            raise RuntimeError("Cleanup database identity mismatch")
        await client.drop_database(name)
        removed = name not in await client.list_database_names()
        (EVIDENCE / "cleanup-proof.json").write_text(json.dumps({"database": name, "removed": removed}), encoding="utf-8")
        client.close()


app = FastAPI(lifespan=lifespan)
