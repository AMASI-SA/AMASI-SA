"""Isolated ASGI regression coverage for the inherited Tamara boundaries.

Run with --noconftest: repository conftest reads deployment environment files.
Only in-memory Mongo, fake authentication, and fake Tamara clients are used.
"""
import copy
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import APIRouter, FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

import bnpl.audit_routes as audit
from bnpl.clients.tamara import TamaraError
from tamara_fix_plan_dryrun_routes import make_tamara_fix_plan_dryrun_router
from tamara_refund_audit_routes import make_tamara_refund_audit_router
from tamara_refund_backfill_routes import make_tamara_refund_backfill_router


SECRET = "secret-token customer@example.test private-provider-body"
WINDOW = {"date_from": "2026-09-01", "date_to": "2026-09-30"}
ROUTES = (
    ("POST", "/bnpl/tamara/rebuild-refunds", {}),
    ("GET", "/audit/tamara-fix-plan-dryrun", WINDOW),
    ("GET", "/audit/tamara-refund-and-old-capture-forensic", WINDOW),
    ("GET", "/audit/tamara-refund-backfill-dry-run", {}),
    ("POST", "/admin/tamara-refund-backfill-apply", {}),
)


class TracedDatabase:
    def __init__(self):
        self.inner = AsyncMongoMockClient().db
        self.accessed = []
        self.collections = {}

    def __getattr__(self, name):
        self.accessed.append(name)
        if name not in self.collections:
            self.collections[name] = getattr(self.inner, name)
        return self.collections[name]


class TamaraSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = TracedDatabase()
        await self.db.users.insert_many([
            {"id": "owner", "role": "owner", "is_active": True},
            {"id": "other", "role": "owner", "is_active": True},
            {"id": "employee", "role": "employee", "owner_id": "owner", "is_active": True},
            {"id": "admin", "role": "admin", "owner_id": "owner", "is_active": True},
        ])
        self.claims = {"id": "owner", "role": "owner"}

        async def current_user(request: Request):
            if self.claims is None:
                raise HTTPException(401, "authentication_required")
            return copy.deepcopy(self.claims)

        async def bnpl_user(request, db):
            return await current_user(request)

        self.auth_patch = patch.object(audit, "get_current_user_from_db", bnpl_user)
        self.auth_patch.start()
        self.provider = AsyncMock()
        self.provider.get_order_by_id.return_value = {}
        self.client_patch = patch("bnpl.clients.tamara.TamaraClient", return_value=self.provider)
        self.client_factory = self.client_patch.start()
        self.secrets_patch = patch("bnpl.config_store.get_raw_secrets", new=AsyncMock(
            return_value={"api_token": "synthetic-test-token"}))
        self.secrets = self.secrets_patch.start()
        self.env_patch = patch.dict("os.environ", {"BNPL_BRIDGE_CUTOFF_ISO": ""})
        self.env_patch.start()
        app = FastAPI()
        router = APIRouter()
        audit.attach_bnpl_audit_routes(router, self.db)
        app.include_router(router)
        for factory in (make_tamara_fix_plan_dryrun_router,
                        make_tamara_refund_audit_router,
                        make_tamara_refund_backfill_router):
            app.include_router(factory(self.db, current_user))
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.env_patch.stop()
        self.secrets_patch.stop()
        self.client_patch.stop()
        self.auth_patch.stop()

    async def seed(self, owner="owner", suffix=""):
        txn = dict(user_id=owner, provider="tamara", id="txn" + suffix,
                   provider_id="pay" + suffix, order_number="order" + suffix,
                   order_reference_id="order" + suffix, amount=100, captured_amount=100,
                   refunded_amount=25, status="fully_refunded", currency="SAR",
                   effective_settlement_date="2026-09-10T00:00:00Z",
                   created_at_provider="2026-09-01T00:00:00Z",
                   updated_at_provider="2026-09-11T00:00:00Z",
                   customer={"email": SECRET}, api_token=SECRET,
                   raw_payload={"private": SECRET})
        await self.db.payment_transactions.insert_one(txn)
        refund = dict(user_id=owner, provider="tamara", id="refund" + suffix,
                      provider_refund_id="refund-provider" + suffix,
                      provider_payment_id="pay" + suffix, order_reference_id="order" + suffix,
                      order_number="order" + suffix, amount=25, currency="SAR",
                      refunded_at="2026-09-11T00:00:00Z", status="fully_refunded",
                      raw={"private": SECRET}, reason=SECRET, customer={"email": SECRET})
        await self.db.payment_refunds.insert_one(refund)
        await self.db.general_ledger.insert_one(dict(user_id=owner, status="posted",
            metadata={"idempotency_key": "bnpl_sale:tamara:pay" + suffix}))
        return txn, refund

    async def test_denied_actors_never_reach_data_or_provider(self):
        claims = [None, {"id": "employee", "role": "employee"},
                  {"id": "admin", "role": "admin"},
                  {"id": "employee", "role": "owner"},
                  {"id": "owner", "role": "owner", "_mobile_actor_id": "employee"}]
        for actor in claims:
            self.claims = actor
            for method, url, params in ROUTES:
                with self.subTest(actor=actor, url=url):
                    self.db.accessed.clear()
                    response = await self.client.request(method, url, params=params)
                    self.assertEqual(response.status_code, 401 if actor is None else 403, response.text)
                    self.assertEqual([x for x in self.db.accessed if x != "users"], [])
        self.secrets.assert_not_awaited()
        self.client_factory.assert_not_called()

    async def test_owner_controls_and_tenant_isolation(self):
        await self.seed()
        await self.seed("other", "-other")
        for method, url, params in ROUTES[:-1]:
            with self.subTest(url=url):
                response = await self.client.request(method, url, params={**params, "probe_tamara_api": False})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertNotIn("order-other", response.text)
                self.assertNotIn("pay-other", response.text)
        other = await self.db.payment_refunds.find_one({"user_id": "other"})
        self.assertEqual(other["amount"], 25)

    async def test_rebuild_inner_exception_is_fixed_error(self):
        await self.seed()
        await self.db.payment_refunds.delete_many({"user_id": "owner"})
        with patch.object(self.db.payment_refunds, "insert_one", new=AsyncMock(side_effect=RuntimeError(SECRET))):
            response = await self.client.post("/bnpl/tamara/rebuild-refunds")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["failed"], 1)
        self.assertEqual(response.json()["first_failures"][0]["reason"], "operation_failed")
        self.assertNotIn(SECRET, response.text)

    async def test_rebuild_outer_exception_has_no_traceback(self):
        with patch.object(self.db.payment_transactions, "find", side_effect=RuntimeError(SECRET)):
            response = await self.client.post("/bnpl/tamara/rebuild-refunds")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["success"])
        self.assertEqual(response.json()["error"], "operation_failed")
        self.assertNotIn("traceback", response.json())
        self.assertNotIn(SECRET, response.text)

    async def test_owner_rebuild_remains_tenant_scoped_and_idempotent(self):
        await self.seed()
        await self.seed("other", "-other")
        await self.db.payment_refunds.delete_many({"user_id": "owner"})
        first = await self.client.post(ROUTES[0][1])
        self.assertEqual(first.json()["created"], 1)
        self.assertEqual(first.json()["total_amount"], 25)
        second = await self.client.post(ROUTES[0][1])
        self.assertEqual(second.json()["created"], 0)
        self.assertEqual(second.json()["already_had"], 1)
        self.assertEqual(await self.db.payment_refunds.count_documents({"user_id": "owner"}), 1)
        self.assertEqual(await self.db.payment_refunds.count_documents({"user_id": "other"}), 1)
        self.assertEqual(await self.db.general_ledger.count_documents({}), 2)

    async def test_fix_plan_preserves_proposals_totals_and_read_only_data(self):
        transactions = []
        for name, amount, refunded in (("old", 100, 0), ("stale", 250, 0), ("synth", 150, 150)):
            transactions.append(dict(user_id="owner", provider="tamara", provider_id=name,
                order_number=name, order_reference_id=name, amount=amount, captured_amount=amount,
                refunded_amount=refunded, status="fully_refunded" if refunded else "fully_captured",
                created_at_provider="2026-08-01T00:00:00Z",
                effective_settlement_date="2026-09-10T00:00:00Z"))
        await self.db.payment_transactions.insert_many(transactions)
        await self.db.payment_refunds.insert_one(dict(user_id="owner", provider="tamara",
            provider_payment_id="synth", provider_refund_id="synthetic:synth", amount=150,
            refunded_at="2026-08-01T00:00:00Z", synthesised=True, reason=SECRET))
        await self.db.settlement_entries.insert_one(dict(user_id="owner", provider="tamara",
            order_number="old", event_type="sale", settlement_date="2026-08-02", actual_gross_amount=100))

        async def provider_order(pid):
            amount = {"old": 100, "stale": 250, "synth": 150}[pid]
            return {"status": "fully_captured" if pid == "old" else "fully_refunded",
                    "total_amount": amount, "captured_amount": amount,
                    "refunded_amount": 0 if pid == "old" else amount,
                    "transactions": [{"type": "refund", "created_at": "2026-09-11T00:00:00Z"}]}

        self.provider.get_order_by_id.side_effect = provider_order
        collections = ("payment_transactions", "payment_refunds", "settlement_entries", "general_ledger")
        before = [await getattr(self.db, name).find({}).to_list(100) for name in collections]
        response = await self.client.get(ROUTES[1][1], params=WINDOW)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["fix_1_resync_status_and_refunded_amount"]["rows"][0]["order_number"], "stale")
        self.assertEqual(data["fix_2_correct_refunded_at"]["rows"][0]["after_proposed"]["refunded_at"], "2026-09-11T00:00:00Z")
        self.assertEqual(data["fix_3_lock_effective_settlement_date"]["rows"][0]["after_proposed"]["effective_settlement_date"], "2026-08-02")
        totals = data["post_fix_simulated_forensic_compute"]
        self.assertEqual((totals["gross_sales"], totals["refunds"], totals["net_sales"], totals["orders_count"]), (400, 400, 0, 2))
        self.assertEqual(before, [await getattr(self.db, name).find({}).to_list(100) for name in collections])
        self.assertNotIn(SECRET, response.text)

    async def test_provider_initialization_errors_are_fixed(self):
        self.secrets.side_effect = RuntimeError(SECRET)
        for url, params, field in (
            (ROUTES[1][1], {**WINDOW}, "tamara_client"),
            (ROUTES[2][1], {**WINDOW, "order_numbers": "order", "probe_tamara_api": True}, "track_b_targeted_orders"),
        ):
            response = await self.client.get(url, params=params)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("provider_operation_failed", str(response.json()[field]))
            self.assertNotIn(SECRET, response.text)

    async def test_provider_errors_are_fixed_for_both_exception_types(self):
        await self.seed()
        for exc in (TamaraError(500, SECRET), RuntimeError(SECRET)):
            self.provider.get_order_by_id.side_effect = exc
            for url, params in (
                (ROUTES[1][1], {**WINDOW, "dump_raw_for_order_numbers": "order"}),
                (ROUTES[2][1], {**WINDOW, "order_numbers": "order", "probe_tamara_api": True}),
            ):
                response = await self.client.get(url, params=params)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertIn("provider_operation_failed", response.text)
                self.assertNotIn(SECRET, response.text)

    async def test_fix_plan_returns_minimal_nested_provider_dto(self):
        await self.seed()
        money = {"amount": "25.00", "currency": "SAR", "customer": SECRET}
        event = {"transaction_id": "tx-refund", "refund_id": "r1", "type": "refund",
                 "created_at": "2026-09-11T00:00:00Z", "amount": money,
                 "customer": SECRET, "metadata": {"secret": SECRET}}
        self.provider.get_order_by_id.return_value = {
            "status": "partially_refunded", "refunded_amount": money, "captured_amount": money,
            "total_amount": money, "transactions": [event], "refunds": [event],
            "refund_orders": [event], "processing": {"status": "complete", "customer": SECRET},
            "settled_at": {"unexpected_nested_secret": SECRET}, "private_" + SECRET: SECRET,
            "updated_at": {"unexpected_nested_secret": SECRET},
        }
        response = await self.client.get(ROUTES[1][1], params={**WINDOW, "dump_raw_for_order_numbers": "order"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(SECRET, response.text)
        row = response.json()["raw_tamara_payloads_dump"]["rows"][0]
        self.assertEqual(row["raw_transactions"][0]["created_at"], "2026-09-11T00:00:00Z")
        self.assertEqual(row["raw_transactions"][0]["amount"], {"amount": "25.00", "currency": "SAR"})
        self.assertEqual(response.json()["post_fix_simulated_forensic_compute"]["gross_sales"], 100)

    async def test_forensic_returns_minimal_local_and_live_dtos(self):
        await self.seed()
        await self.seed("other", "-other")
        self.provider.get_order_by_id.return_value = {
            "status": "fully_refunded", "total_amount": {"amount": "100", "currency": "SAR", "private": SECRET},
            "settlement": {"settlement_id": "s1", "settled_at": "2026-09-12", "private": SECRET},
            "captures": [{"capture_id": "c1", "created_at": "2026-09-01", "private": SECRET}],
            "private_" + SECRET: SECRET,
        }
        response = await self.client.get(ROUTES[2][1], params={**WINDOW, "order_numbers": "order,order-other", "probe_tamara_api": True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(SECRET, response.text)
        rows = response.json()["track_b_targeted_orders"]["rows"]
        self.assertEqual(rows[0]["payment_transaction"]["amount"], 100)
        self.assertEqual(rows[0]["payment_refunds"][0]["amount"], 25)
        self.assertFalse(rows[1]["found_in_payment_transactions"])
        self.assertEqual(rows[1]["payment_refunds"], [])
        self.provider.get_order_by_id.assert_awaited_once_with("pay")

    async def test_backfill_error_is_fixed_and_token_check_remains(self):
        await self.seed()
        dry = await self.client.get(ROUTES[3][1])
        self.assertEqual(dry.json()["ready_to_backfill_sum"], 25)
        with patch("bnpl.ledger_bridge.post_bnpl_refund_to_ledger", new=AsyncMock(side_effect=RuntimeError(SECRET))) as bridge:
            rejected = await self.client.post(ROUTES[4][1], headers={"X-Apply-Token": "stale"})
            self.assertEqual(rejected.status_code, 401)
            bridge.assert_not_awaited()
            response = await self.client.post(ROUTES[4][1], headers={"X-Apply-Token": dry.json()["apply_token"]})
        self.assertEqual(response.json()["error_count"], 1)
        self.assertEqual(response.json()["results"][0]["error"], "operation_failed")
        self.assertNotIn(SECRET, response.text)

    async def test_backfill_uses_real_managed_bridge_and_durable_refund_ingress(self):
        await self.seed()
        await self.db.mz2_atomic_owners.insert_one({"_id": "owner", "mezan2_managed": True})
        # mongomock_motor's with_options returns a synchronous collection.
        # Keep the fake asynchronous collection at this driver-only boundary.
        inbox = self.db.mz2_ingress_events
        inbox.with_options = lambda **kwargs: inbox
        await self.db.mz2_recognition_events.insert_one({"_id": "original", "user_id": "owner", "status": "posted",
            "proposal": {"event": {"kind": "sale", "order_number": "order", "provider": "tamara", "provider_payment_id": "pay"},
                         "tax": {"gross": "100.00"}}})
        ledger_before = await self.db.general_ledger.find({}).to_list(100)
        dry = await self.client.get(ROUTES[3][1])
        headers = {"X-Apply-Token": dry.json()["apply_token"]}

        async def paused(db, owner, operation):
            self.assertEqual(await db.mz2_ingress_events.count_documents({"user_id": owner, "state": "pending"}), 1)
            raise HTTPException(423, "mz2_writes_paused")

        # Replace only transaction infrastructure; bridge, managed_owner,
        # process_order_refunds, observation and durable ingress are real.
        with patch("accounting_ingress.atomic_owner", paused):
            response = await self.client.post(ROUTES[4][1], headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["applied_count"], 0)
        self.assertEqual(response.json()["results"][0].get("reason"), "daily_movement_approval_required", response.text)
        self.assertEqual(await self.db.mz2_customer_refunds.count_documents({}), 0)

        async def transaction(db, owner, operation):
            return await operation(db)

        with patch("accounting_ingress.atomic_owner", transaction), patch("accounting_refund_drafts.atomic_owner", transaction):
            response = await self.client.post(ROUTES[4][1], headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["applied_count"], 0)
        draft = await self.db.mz2_customer_refunds.find_one({"user_id": "owner"})
        self.assertEqual(draft["amount"], "25.00")
        self.assertEqual(draft["state"], "awaiting_execution_confirmation")
        self.assertFalse(draft["recognized"])
        self.assertEqual(await self.db.general_ledger.find({}).to_list(100), ledger_before)
        self.assertEqual(await self.db.mz2_ingress_events.count_documents({}), 1)
        self.assertEqual(await self.db.mz2_customer_refund_payments.count_documents({}), 0)
        self.provider.get_order_by_id.assert_not_awaited()
