"""HTTP -> durable Mongo control plane -> worker -> provider-boundary tests.

Run directly with unittest, avoiding the repository conftest's /app/.env load.
No network credentials; Mongo is mongomock-motor; providers are deterministic.
"""
import asyncio
from dataclasses import replace
from datetime import date, timedelta
import hashlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, AsyncMock

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / ".test-deps"), str(ROOT / "backend")]

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from mongomock_motor import AsyncMongoMockClient
from integrations.qoyod_manual import recovery_campaign as c
from integrations.qoyod_manual.recovery_routes import make_recovery_router
from integrations.qoyod_manual.recovery_404 import Facts, Invoice, Observation
from integrations.qoyod_manual.recovery_adapter import ProductionPorts, invoice_evidence

REFS = [str(900000000 + i) for i in range(199)]
COMPLETED = REFS[:2]
TARGET = REFS[2]
BASE = "/integrations/qoyod/manual/recovery-404"


class Provider:
    def __init__(self):
        self.invoices = {}
        self.markers = {}
        self.invoice_posts = 0
        self.payment_posts = 0
        self.timeout = False
        self.hold = None
        self.overrides = {}
        self.refreshes = 0

    async def authorized(self, identity):
        return identity == "test-release"

    async def facts(self, ref):
        self.refreshes += 1
        return Facts(ref, date(2026, 8, 9), "delivered", True, False, True,
                     "403.11", "403.11", "SAR", True, 404, "GET", "/products", True, True) \
            if ref not in self.overrides else self.overrides[ref]

    async def observe(self, ref):
        return Observation(tuple(self.invoices.get(ref, [])), True, True,
                           self.markers.get(ref), bool(self.markers.get(ref)))

    async def send_guarded(self, ref):
        self.invoice_posts += 1
        self.payment_posts += 1
        invoice = Invoice(ref, f"invoice-{ref}", "403.11", "403.11", "0", "SAR")
        self.invoices[ref] = [invoice]
        self.markers[ref] = invoice.invoice_id
        if self.hold:
            await self.hold.wait()
        if self.timeout:
            raise TimeoutError("response lost after commit")

    async def reconcile_marker(self, ref, invoice_id):
        self.markers[ref] = invoice_id


class Integration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = AsyncMongoMockClient(tz_aware=True).db
        self.provider = Provider()
        self.factory = lambda db, campaign: self.provider
        self.patches = [patch.object(c, "COHORT_DIGEST", hashlib.sha256("\n".join(REFS).encode()).hexdigest()),
                        patch.object(c, "COMPLETED_DIGESTS", {hashlib.sha256(x.encode()).hexdigest() for x in COMPLETED})]
        for item in self.patches: item.start()
        app = FastAPI()
        self.user = {"id": "store", "role": "owner"}
        async def user(): return self.user
        app.include_router(make_recovery_router(self.db, user, identity_fn=lambda: "test-release",
            external_factory=self.factory), prefix="/integrations/qoyod/manual")
        self.http = AsyncClient(transport=ASGITransport(app=app), base_url="http://isolated.test")

    async def asyncTearDown(self):
        await self.http.aclose()
        for item in reversed(self.patches): item.stop()

    async def prepare(self):
        response = await self.http.post(BASE + "/prepare", json={"order_numbers": REFS})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def activate(self):
        doc = await self.prepare()
        response = await self.http.post(BASE + "/activate", json={
            "fingerprint": doc["fingerprint"], "confirmation": "ACTIVATE_REVIEWED_404_COHORT"})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_prepare_is_read_only_to_providers_and_exact_scope(self):
        doc = await self.prepare()
        self.assertEqual((doc["total"], doc["verified"], doc["remaining"]), (199, 2, 197))
        self.assertEqual(len(doc["results"]), 199)
        self.assertFalse(await c.tick(self.db, self.factory))
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (0, 0))
        bad = await self.http.post(BASE + "/prepare", json={"order_numbers": REFS[:-1]})
        self.assertEqual(bad.status_code, 409)

    async def test_audit_includes_blocked_unknown_without_retry_or_claim_removal(self):
        await self.prepare()
        await self.db.qoyod_404_outcomes.update_one(
            {"reference": TARGET}, {"$set": {"state": "blocked", "reason": "outcome_unknown"}})
        await self.db.qoyod_404_attempts.insert_one({"_id": f"main:{TARGET}", "proof": "keep"})
        failure = RuntimeError("private provider message")
        failure._recovery_read_diagnostic = {"stage": "observe", "error_type": "RuntimeError"}
        with patch.object(self.provider, "observe", AsyncMock(side_effect=failure)) as observe:
            result = await self.http.post(BASE + "/audit", json={})
        self.assertEqual(result.status_code, 200)
        observe.assert_awaited_once_with(TARGET)
        row = next(r for r in result.json()["results"] if r["reference"] == TARGET)
        self.assertEqual((row["state"], row["reason"]), ("review", "outcome_unknown"))
        self.assertEqual(row["read_diagnostic"]["stage"], "observe")
        self.assertFalse(result.json()["can_activate"])
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (0, 0))
        self.assertEqual((await self.db.qoyod_404_attempts.find_one({"_id": f"main:{TARGET}"}))["proof"], "keep")

    async def test_audit_requeues_only_preclaim_provider_page_404(self):
        await self.prepare()
        await self.db.qoyod_404_outcomes.update_one(
            {"reference": TARGET},
            {"$set": {
                "state": "review",
                "reason": "outcome_unknown",
                "read_diagnostic": {
                    "stage": "provider_page",
                    "error_type": "ManualQoyodError",
                    "http_status": 404,
                    "page": 65,
                },
            }},
        )

        result = await self.http.post(BASE + "/audit", json={})

        self.assertEqual(result.status_code, 200, result.text)
        row = next(
            item for item in result.json()["results"]
            if item["reference"] == TARGET
        )
        self.assertEqual(
            (row["state"], row["reason"]),
            ("pending", "pre_send_read_recovered"),
        )
        self.assertTrue(result.json()["can_activate"])
        self.assertEqual(
            await self.db.qoyod_404_attempts.count_documents({}), 0
        )
        self.assertEqual(
            (self.provider.invoice_posts, self.provider.payment_posts),
            (0, 0),
        )

    async def test_worker_preserves_read_diagnostic_before_financial_attempt(self):
        await self.activate()
        failure = RuntimeError("private provider message")
        failure._recovery_read_diagnostic = {"stage": "observe", "error_type": "RuntimeError"}
        with patch.object(self.provider, "observe", AsyncMock(side_effect=failure)):
            await c.tick(self.db, self.factory)
        row = await self.db.qoyod_404_outcomes.find_one({"reference": TARGET})
        self.assertEqual((row["state"], row["reason"]), ("blocked", "outcome_unknown"))
        self.assertEqual(row["read_diagnostic"]["stage"], "observe")
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (0, 0))
        self.assertEqual(await self.db.qoyod_404_attempts.count_documents({}), 0)

    async def test_activation_binds_scope_and_explicit_confirmation(self):
        doc = await self.prepare()
        for confirmation, fingerprint in [("", doc["fingerprint"]), ("ACTIVATE_REVIEWED_404_COHORT", "wrong")]:
            response = await self.http.post(BASE + "/activate", json={"fingerprint": fingerprint, "confirmation": confirmation})
            self.assertEqual(response.status_code, 409)

    async def test_control_mutations_require_actual_owner_not_merchant_membership(self):
        doc = await self.prepare()
        original = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
        denied = [
            {"id": "viewer", "role": "employee", "created_by": "store", "permissions": ["orders.view"]},
            {"id": "store"},
            {"id": "store", "role": "admin"},
            {"id": "", "role": "owner"},
            {"id": "other-store", "role": "owner"},
        ]
        payloads = {"prepare": {"order_numbers": REFS}, "review-release": {"fingerprint": doc["fingerprint"]}, "activate": {
            "fingerprint": doc["fingerprint"], "confirmation": "ACTIVATE_REVIEWED_404_COHORT"},
            "pause": {}, "audit": {}}
        with patch.object(c, "prepare", AsyncMock(return_value={})) as prepare, \
             patch.object(c, "activate", AsyncMock(return_value={})) as activate, \
             patch.object(c, "pause", AsyncMock(return_value=None)) as pause, \
             patch.object(c, "audit_pending", AsyncMock(return_value={})) as audit, \
             patch.object(self.provider, "authorized", AsyncMock()) as ready:
            for actor in denied:
                self.user = actor
                for action, payload in payloads.items():
                    with self.subTest(actor=actor, action=action):
                        response = await self.http.post(BASE + "/" + action, json=payload)
                        self.assertEqual(response.status_code, 403, response.text)
            for boundary in (prepare, activate, pause, audit, ready):
                boundary.assert_not_awaited()
        self.assertEqual(await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN}), original)
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (0, 0))
        # Existing same-store display access is intentionally retained.
        self.user = denied[0]
        self.assertEqual((await self.http.get(BASE)).status_code, 200)
        self.user = denied[-1]
        self.assertEqual((await self.http.get(BASE)).status_code, 403)
        self.user = {"id": "store", "role": "owner"}
        response = await self.http.post(BASE + "/activate", json=payloads["activate"])
        self.assertEqual(response.status_code, 200, response.text)

    async def test_employee_cannot_prepare_unowned_campaign(self):
        self.user = {"id": "viewer", "role": "employee", "created_by": "store"}
        response = await self.http.post(BASE + "/prepare", json={"order_numbers": REFS})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(await self.db.qoyod_404_campaigns.count_documents({}), 0)
        self.assertEqual(await self.db.qoyod_404_outcomes.count_documents({}), 0)

    async def test_activated_worker_sends_one_then_reports_all_199(self):
        await self.activate()
        await c.tick(self.db, self.factory)
        doc = (await self.http.get(BASE)).json()
        self.assertEqual((doc["verified"], doc["remaining"]), (3, 196))
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (1, 1))
        self.assertGreaterEqual(self.provider.refreshes, 2)
        for ref in COMPLETED: self.assertNotIn(ref, self.provider.invoices)

    async def test_existing_paid_invoice_repairs_marker_without_financial_write(self):
        self.provider.invoices[TARGET] = [Invoice(TARGET, "existing", "403.11", "403.11", "0", "SAR")]
        await self.activate(); await c.tick(self.db, self.factory)
        doc = await c.report(self.db)
        self.assertEqual(doc["counts"]["verified_existing"], 1)
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (0, 0))
        self.assertEqual(self.provider.markers[TARGET], "existing")

    async def test_rounding_isolation_resumes_all_other_eligible_without_resending(self):
        await self.activate()
        self.provider.invoices[TARGET] = [Invoice(TARGET, "existing", "403.12", "403.11", "0.01", "SAR")]
        claim = {"_id": f"main:{TARGET}", "reference": TARGET, "fingerprint": "original-attempt"}
        await self.db.qoyod_404_attempts.insert_one(claim)
        await c.tick(self.db, self.factory)
        doc = await c.report(self.db)
        self.assertEqual(doc["state"], "active")
        self.assertEqual((doc["verified"], doc["remaining"], doc["rounding_unsettled"]), (2, 197, 1))
        result = next(r for r in doc["results"] if r["reference"] == TARGET)
        self.assertEqual((result["invoice_total"], result["paid_amount"], result["remaining"]), ("403.12", "403.11", "0.01"))
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (0, 0))
        for _ in range(197):
            await c.tick(self.db, self.factory)
        doc = await c.report(self.db)
        self.assertEqual((doc["state"], doc["verified"], doc["remaining"]), ("review_complete", 198, 1))
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (196, 196))
        self.assertEqual(await self.db.qoyod_404_attempts.find_one({"_id": claim["_id"]}), claim)
        self.assertEqual(self.provider.invoices[TARGET][0].remaining, "0.01")

    async def test_release_review_is_local_only_explicit_and_preserves_scope_and_claims(self):
        original = await self.prepare()
        await c.pause(self.db)
        claim = {"_id": f"main:{TARGET}", "reference": TARGET, "fingerprint": "old-claim"}
        await self.db.qoyod_404_attempts.insert_one(claim)
        await c.review_release(self.db, original["fingerprint"], "store", "store", "old-runtime")
        response = await self.http.get(BASE)
        self.assertTrue(response.json()["release_review_required"])
        self.assertFalse(response.json()["can_activate"])
        rebound = await self.http.post(BASE + "/review-release", json={"fingerprint": response.json()["fingerprint"]})
        self.assertEqual(rebound.status_code, 200, rebound.text)
        self.assertEqual(rebound.json()["state"], "paused")
        self.assertFalse(rebound.json()["release_review_required"])
        self.assertEqual(rebound.json()["references"], original["references"])
        self.assertEqual(rebound.json()["excluded"], original["excluded"])
        self.assertEqual(rebound.json()["fingerprint"], original["fingerprint"])
        self.assertEqual(await self.db.qoyod_404_attempts.find_one({"_id": claim["_id"]}), claim)
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (0, 0))

    async def test_release_review_rejects_busy_changed_scope_and_old_fingerprint(self):
        doc = await self.prepare()
        for changes in ({"busy": True}, {"cursor": TARGET}, {"references": REFS[:-1]}, {"state": "active"}):
            before = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
            await self.db.qoyod_404_campaigns.update_one({"_id": c.CAMPAIGN}, {"$set": changes})
            response = await self.http.post(BASE + "/review-release", json={"fingerprint": doc["fingerprint"]})
            self.assertEqual(response.status_code, 409, changes)
            await self.db.qoyod_404_campaigns.replace_one({"_id": c.CAMPAIGN}, before)
        response = await self.http.post(BASE + "/review-release", json={"fingerprint": "wrong"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (0, 0))

    async def test_response_loss_and_restart_audit_never_repeat_invoice_or_payment(self):
        await self.activate(); self.provider.timeout = True
        await c.tick(self.db, self.factory)
        self.assertEqual((await c.report(self.db))["state"], "paused")
        # A new route/worker instance shares the durable DB and provider ledger.
        result = await self.http.post(BASE + "/audit")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["verified"], 3)
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (1, 1))
        self.assertEqual(await self.db.qoyod_404_attempts.count_documents({}), 1)

    async def test_restart_with_stale_cursor_only_audits(self):
        await self.activate()
        ports = c.DurablePorts(self.db, await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN}), self.provider)
        await ports.claim_once(TARGET, "test")
        await self.db.qoyod_404_campaigns.update_one({"_id": c.CAMPAIGN}, {"$set": {
            "cursor": TARGET, "busy": True, "lease_until": c.now() - timedelta(seconds=1)}})
        await self.db.qoyod_404_outcomes.update_one({"reference": TARGET}, {"$set": {"state": "running"}})
        await c.tick(self.db, self.factory)
        self.assertEqual((await c.report(self.db))["state"], "paused")
        self.assertEqual(self.provider.invoice_posts, 0)
        self.assertEqual((await self.db.qoyod_404_outcomes.find_one({"reference": TARGET}))["reason"],
                         "submitted_invoice_not_found_do_not_retry")

    async def test_two_workers_cannot_write_twice(self):
        await self.activate(); self.provider.hold = asyncio.Event()
        first = asyncio.create_task(c.tick(self.db, self.factory))
        for _ in range(50):
            if self.provider.invoice_posts: break
            await asyncio.sleep(0)
        await c.tick(self.db, self.factory)
        self.assertEqual(self.provider.invoice_posts, 1)
        self.provider.hold.set(); await first
        self.assertEqual(self.provider.payment_posts, 1)

    async def test_live_worker_renews_lease_before_original_deadline(self):
        await self.activate(); self.provider.hold = asyncio.Event()
        clock = [c.now()]
        with patch.object(c, "now", side_effect=lambda: clock[0]), \
             patch.object(c, "LEASE_HEARTBEAT_SECONDS", 0.001, create=True):
            first = asyncio.create_task(c.tick(self.db, self.factory))
            try:
                for _ in range(50):
                    if self.provider.invoice_posts:
                        break
                    await asyncio.sleep(0)
                self.assertEqual(self.provider.invoice_posts, 1)
                original = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
                original_token = original["lease_token"]

                # Move beyond the originally acquired ten-minute lease while
                # the provider call is still alive. The heartbeat must renew
                # the same fenced token before another worker can take over.
                clock[0] += timedelta(minutes=11)
                for _ in range(50):
                    current = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
                    if current["lease_until"] > clock[0]:
                        break
                    await asyncio.sleep(0.001)

                await c.tick(self.db, self.factory)
                during = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
                self.assertEqual(during["lease_token"], original_token)
                self.assertGreater(during["lease_until"], clock[0])
                self.assertEqual(self.provider.invoice_posts, 1)
            finally:
                self.provider.hold.set()
                await first
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (1, 1))

    async def test_worker_stops_without_stale_persistence_after_fence_loss(self):
        from integrations.qoyod_manual.recovery_404 import EvidenceError

        await self.activate(); self.provider.hold = asyncio.Event()
        with patch.object(c, "LEASE_HEARTBEAT_SECONDS", 0.001):
            worker = asyncio.create_task(c.tick(self.db, self.factory))
            try:
                for _ in range(50):
                    if self.provider.invoice_posts:
                        break
                    await asyncio.sleep(0)
                self.assertEqual(self.provider.invoice_posts, 1)
                await self.db.qoyod_404_campaigns.update_one(
                    {"_id": c.CAMPAIGN},
                    {"$set": {"lease_token": "replacement-worker",
                              "lease_until": c.now() + timedelta(minutes=10)}},
                )
                with self.assertRaisesRegex(EvidenceError, "recovery_lease_fencing_lost"):
                    await worker
            finally:
                self.provider.hold.set()
                if not worker.done():
                    worker.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await worker

        campaign = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
        row = await self.db.qoyod_404_outcomes.find_one({"reference": TARGET})
        self.assertEqual(campaign["lease_token"], "replacement-worker")
        self.assertEqual(campaign["state"], "active")
        self.assertEqual(row["state"], "running")
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (1, 1))

    async def test_live_audit_renews_lease_before_original_deadline(self):
        await self.prepare()
        await self.db.qoyod_404_outcomes.update_one(
            {"reference": TARGET}, {"$set": {"state": "review", "reason": "outcome_unknown"}})
        release = asyncio.Event()
        original_facts = self.provider.facts

        async def hold_facts(ref):
            await release.wait()
            return await original_facts(ref)

        self.provider.facts = hold_facts
        clock = [c.now()]
        with patch.object(c, "now", side_effect=lambda: clock[0]), \
             patch.object(c, "LEASE_HEARTBEAT_SECONDS", 0.001, create=True):
            auditing = asyncio.create_task(c.audit_pending(self.db, self.factory))
            try:
                for _ in range(50):
                    current = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
                    if current.get("busy"):
                        break
                    await asyncio.sleep(0)
                original_token = current["lease_token"]

                clock[0] += timedelta(minutes=11)
                for _ in range(50):
                    current = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
                    if current["lease_until"] > clock[0]:
                        break
                    await asyncio.sleep(0.001)

                state = await c.report(self.db)
                self.assertEqual(state["lease_token"], original_token)
                self.assertGreater(state["lease_until"], clock[0])
                self.assertFalse(state["can_audit"])
                self.assertEqual(state["audit_block_reason"], "operation_in_progress")
            finally:
                release.set()
                await auditing
        self.assertEqual((self.provider.invoice_posts, self.provider.payment_posts), (0, 0))

    async def test_cod_sku_cancelled_or_unpaid_have_specific_results_and_no_send(self):
        await self.activate()
        changes = [dict(is_cod=True), dict(skus_complete=False), dict(status="cancelled"), dict(payment_eligible=False)]
        for ref, change in zip(REFS[2:6], changes):
            self.provider.overrides[ref] = replace(await self.provider.facts(ref), **change)
        for _ in changes: await c.tick(self.db, self.factory)
        self.assertEqual((await c.report(self.db))["counts"]["blocked"], 4)
        self.assertEqual(self.provider.invoice_posts, 0)

    async def test_duplicate_or_unpaid_existing_invoice_never_creates_payment(self):
        await self.activate()
        inv = Invoice(TARGET, "existing", "403.11", "0", "403.11", "SAR")
        self.provider.invoices[TARGET] = [inv]
        await c.tick(self.db, self.factory)
        self.assertEqual((await c.report(self.db))["state"], "paused")
        self.assertEqual(self.provider.payment_posts, 0)

    async def test_production_adapter_uses_fresh_raw_total_not_merged_old_value(self):
        await self.prepare()
        campaign = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
        await self.db.unified_orders.insert_one({"user_id": "store", "order_number": TARGET,
            "total_amount": 665.58, "raw_by_source": {"salla_direct": {"reference_id": TARGET}}})
        await self.db.qoyod_manual_auto_quarantines.insert_one({"_id": f"main:{TARGET}", "status": "open",
            "last_seen_at": c.now() - timedelta(days=1), "detail": {"status_code": 404, "endpoint": "GET /products"}})
        doc = {"order_date": "2026-08-09", "order_status_slug": "delivered", "total_amount": 403.11,
               "payment_method": "tabby_installment"}
        with patch("salla_integration.sync.resync_single_order", AsyncMock(return_value={"ok": True, "found": True})) as refresh, \
             patch("salla_integration.sync._salla_order_to_doc", return_value=doc), \
             patch("integrations.qoyod_manual.send._find_salla_accounting_node", return_value={"reference_id": TARGET}), \
             patch("integrations.qoyod_manual.send._find_unified_salla_accounting_canon", AsyncMock(return_value={
                 "total_amount":403.11,"currency":"SAR","items":[{"sku":"test"}]})), \
             patch("integrations.qoyod_manual.send._preflight_qoyod_invoice", return_value={"qoyod_predicted_total":403.11}), \
             patch("integrations.qoyod.candidate_orders.payment_eligibility", return_value="eligible"):
            facts = await ProductionPorts(self.db, campaign).facts(TARGET)
        refresh.assert_awaited_once()
        self.assertEqual(facts.total, "403.11")

    def test_provider_zero_balance_is_preserved(self):
        invoice = invoice_evidence({"id": 7, "reference": TARGET, "total": 403.11, "outstanding": 0})
        self.assertEqual(invoice.paid, "403.11")
        self.assertEqual(invoice.remaining, "0")

    async def test_live_observer_preserves_duplicates_and_uses_get_values(self):
        await self.prepare()
        campaign = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
        async def request(client, method, endpoint, **kwargs):
            self.assertEqual(method, "GET")
            if endpoint == "/invoices":
                return {"invoices": [{"id": 1, "reference": TARGET}, {"id": 2, "reference": TARGET}]}
            return {"invoice": {"id": int(endpoint.rsplit('/',1)[1]), "reference": TARGET,
                                "total":403.11,"paid_amount":403.11,"outstanding":0}}
        with patch("integrations.qoyod.credentials.get_api_key", AsyncMock(return_value="synthetic")), \
             patch.dict("os.environ", {"QOYOD_API_BASE":"https://qoyod.invalid"}), \
             patch("integrations.qoyod_manual.client.ManualQoyodClient._request", request):
            result = await ProductionPorts(self.db, campaign).observe(TARGET)
        self.assertEqual(len(result.invoices), 2)
        self.assertTrue(result.complete_reference_lookup)
        self.assertTrue(all(i.total == "403.11" for i in result.invoices))

    async def test_live_observer_generic_404_is_not_complete_absence(self):
        from integrations.qoyod_manual.client import ManualQoyodError
        await self.prepare()
        campaign = await self.db.qoyod_404_campaigns.find_one({"_id": c.CAMPAIGN})
        with patch("integrations.qoyod.credentials.get_api_key", AsyncMock(return_value="synthetic")), \
             patch.dict("os.environ", {"QOYOD_API_BASE":"https://qoyod.invalid"}), \
             patch("integrations.qoyod_manual.client.ManualQoyodClient._request", AsyncMock(side_effect=
                   ManualQoyodError(status_code=404, endpoint="GET /invoices", response_excerpt="URL not found"))):
            with self.assertRaises(ManualQoyodError):
                await ProductionPorts(self.db, campaign).observe(TARGET)

    async def test_paused_process_crash_with_expired_lease_can_audit(self):
        await self.activate()
        await self.db.qoyod_404_campaigns.update_one({"_id":c.CAMPAIGN}, {"$set":{
            "state":"paused","busy":True,"cursor":TARGET,"lease_until":c.now()-timedelta(seconds=1)}})
        await self.db.qoyod_404_outcomes.update_one({"reference":TARGET},{"$set":{"state":"running"}})
        claim = {"_id": f"main:{TARGET}", "reference": TARGET, "fingerprint": "durable"}
        await self.db.qoyod_404_attempts.insert_one(claim)
        state = (await self.http.get(BASE)).json()
        self.assertTrue(state["busy"])
        self.assertTrue(state["can_audit"])
        self.assertIsNone(state["audit_block_reason"])
        response = await self.http.post(BASE + "/audit")
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()["busy"])
        self.assertEqual((self.provider.invoice_posts,self.provider.payment_posts),(0,0))
        self.assertEqual(await self.db.qoyod_404_attempts.find_one({"_id": claim["_id"]}), claim)
        self.assertEqual(response.json()["verified"], 2)

    async def test_audit_eligibility_blocks_active_and_live_or_unknown_lease(self):
        await self.activate()
        for state, busy, lease, reason in [
            ("active", False, c.now()-timedelta(seconds=1), "campaign_active"),
            ("paused", True, c.now()+timedelta(minutes=5), "operation_in_progress"),
            ("paused", True, None, "operation_in_progress"),
        ]:
            await self.db.qoyod_404_campaigns.update_one({"_id": c.CAMPAIGN}, {"$set": {
                "state": state, "busy": busy, "lease_until": lease}})
            doc = (await self.http.get(BASE)).json()
            self.assertFalse(doc["can_audit"])
            self.assertEqual(doc["audit_block_reason"], reason)
            response = await self.http.post(BASE + "/audit")
            self.assertEqual(response.status_code, 409)
        self.assertEqual((self.provider.invoice_posts,self.provider.payment_posts),(0,0))

    async def test_adapter_refreshes_again_and_blocks_changed_total_before_sender(self):
        from integrations.qoyod_manual.recovery_404 import EvidenceError
        await self.activate()
        campaign = await self.db.qoyod_404_campaigns.find_one({"_id":c.CAMPAIGN})
        adapter = ProductionPorts(self.db,campaign)
        facts = await self.provider.facts(TARGET)
        adapter.latest_facts[TARGET] = facts
        adapter.facts = AsyncMock(return_value=replace(facts,total="420.00",expected_invoice_total="420.00"))
        with patch("integrations.qoyod_manual.send.manual_send_one", AsyncMock()) as sender:
            with self.assertRaisesRegex(EvidenceError,"live_total_changed"):
                await adapter.send_guarded(TARGET)
            sender.assert_not_awaited()
        adapter.facts.assert_awaited_once_with(TARGET)

    async def test_adapter_passes_fresh_total_to_real_sender_boundary(self):
        await self.activate()
        campaign = await self.db.qoyod_404_campaigns.find_one({"_id":c.CAMPAIGN})
        adapter = ProductionPorts(self.db,campaign)
        facts = await self.provider.facts(TARGET)
        adapter.latest_facts[TARGET] = facts
        adapter.facts = AsyncMock(return_value=facts)
        adapter.authorized = AsyncMock(return_value=True)
        with patch("integrations.qoyod_manual.send.manual_send_one", AsyncMock()) as sender:
            await adapter.send_guarded(TARGET)
            self.assertEqual(sender.await_args.kwargs["recovery_expected_total"],"403.11")
            self.assertEqual(sender.await_args.kwargs["order_number"],TARGET)

    async def test_disarmed_worker_does_not_create_phantom_attempt(self):
        await self.activate()
        self.provider.authorized = AsyncMock(return_value=False)
        await c.tick(self.db,self.factory)
        doc = await c.report(self.db)
        self.assertEqual(doc["state"],"paused")
        self.assertEqual(doc["counts"]["pending"],197)
        self.assertIsNone(doc["cursor"])
        self.assertEqual(await self.db.qoyod_404_attempts.count_documents({}),0)
        doc = await self.http.post(BASE + "/activate",json={"fingerprint":doc["fingerprint"],
                "confirmation":"ACTIVATE_REVIEWED_404_COHORT"})
        self.assertEqual(doc.status_code,409)

    async def test_audit_lock_prevents_activation_until_readback_finishes(self):
        await self.activate(); self.provider.timeout=True
        await c.tick(self.db,self.factory)
        release = asyncio.Event()
        original_facts = self.provider.facts
        async def hold_facts(ref):
            await release.wait()
            return await original_facts(ref)
        self.provider.facts = hold_facts
        auditing = asyncio.create_task(c.audit_pending(self.db,self.factory))
        for _ in range(50):
            if (await c.report(self.db)).get("busy"): break
            await asyncio.sleep(0)
        state = await c.report(self.db)
        self.assertFalse(state["can_audit"])
        second_audit = await self.http.post(BASE + "/audit")
        self.assertEqual(second_audit.status_code, 409)
        # Even a stale optimistic precheck cannot bypass the atomic lease.
        with patch.object(c, "audit_eligibility", return_value={"can_audit": True}):
            with self.assertRaisesRegex(ValueError, "audit_already_running"):
                await c.audit_pending(self.db, self.factory)
        response = await self.http.post(BASE + "/activate",json={"fingerprint":state["fingerprint"],
            "confirmation":"ACTIVATE_REVIEWED_404_COHORT"})
        self.assertEqual(response.status_code,409)
        release.set(); await auditing
        self.assertEqual(self.provider.payment_posts,1)
        self.assertEqual(self.provider.invoice_posts,1)

    async def test_entire_cohort_has_one_disposition_per_reference(self):
        await self.activate()
        deferred = REFS[42]
        self.provider.overrides[deferred] = replace(await self.provider.facts(deferred),is_cod=True)
        for _ in range(198):
            await c.tick(self.db,self.factory)
        doc = await c.report(self.db)
        self.assertEqual(doc["state"],"review_complete")
        self.assertEqual((doc["verified"],doc["remaining"],len(doc["results"])),(198,1,199))
        self.assertEqual({x["reference"] for x in doc["results"]},set(REFS))
        unresolved = [x for x in doc["results"] if x["state"] not in c.VERIFIED]
        self.assertEqual([(x["reference"],x["reason"]) for x in unresolved],[(deferred,"cod_deferred")])
        self.assertEqual((self.provider.invoice_posts,self.provider.payment_posts),(196,196))
        self.assertEqual(await self.db.qoyod_404_attempts.count_documents({}),196)


if __name__ == "__main__": unittest.main(verbosity=2)
