"""Actual session authority/JWT/capability on an isolated real Mongo replica set.

No Salla/provider calls, no account records outside this synthetic database.
An absent explicit loopback URI is a SKIP, never evidence of a successful test.
"""
import asyncio
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import jwt
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from starlette.requests import Request
from starlette.responses import Response

import auth
from mobile_app_permissions import MOBILE_APP_CLIENT
from mobile_session_security import _mobile_tokens_from_access_token, MobileSessionSecurityMiddleware
from operational_atomic import operational_owner, OperationalDatabase
import review_session_fence as fence


def request(token, *, refresh=False):
    header = (b"cookie", ("refresh_token=" + token).encode()) if refresh else (
        b"authorization", ("Bearer " + token).encode())
    return Request({"type": "http", "method": "POST" if refresh else "GET",
                    "path": "/api/auth/refresh" if refresh else "/api/auth/me",
                    "headers": [header], "query_string": b"", "scheme": "https",
                    "server": ("isolated.invalid", 443)})


class ReviewSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.client["review_session_test_" + uuid4().hex]
        hello = await self.db.command("hello")
        self.assertTrue(hello.get("setName"))
        self.assertTrue(hello.get("isWritablePrimary"))
        env = patch.dict(os.environ, {"JWT_SECRET": "synthetic-review-session-fence-test-secret", "EMAIL_OTP_ENABLED": "0"})
        env.start()
        self.addCleanup(env.stop)
        await fence.ensure_review_session_storage(self.db)
        await self.db.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 0})
        await self.db.order_review_events.create_index("_id")
        self.actor = {"id": "employee", "created_by": "owner", "role": "viewer", "email": "synthetic@example.invalid"}
        await self.db.users.insert_one(deepcopy(self.actor))
        self.context = await fence.create_review_session(self.db, self.actor)
        self.actor["_review_session"] = self.context
        self.binding = {"actor_id": self.actor["id"], "origin_session_ref": self.context["origin_session_ref"],
                        "origin_session_epoch": self.context["origin_session_epoch"]}

    async def asyncTearDown(self):
        if hasattr(self, "client"):
            self.assertTrue(self.db.name.startswith("review_session_test_"))
            await self.client.drop_database(self.db.name)
            self.client.close()

    def access(self, context=None):
        return auth.create_access_token(self.actor["id"], self.actor["email"], mfa_verified=True,
                                        review_session=context or self.context)

    def refresh(self, context=None, mobile=False):
        return auth.create_refresh_token(self.actor["id"], mfa_verified=True, review_session=context or self.context,
                                         client_type=MOBILE_APP_CLIENT if mobile else None)

    async def complete(self, *, binding=None, callback=None):
        async def accept(scoped):
            await fence.fence_review_session(scoped, user_id="owner", **self.binding)
            await scoped.order_review_events.insert_one({"user_id": "owner", "id": uuid4().hex, "event_type": "synthetic"})
        return await operational_owner(self.db, "owner", callback or accept,
                                       review_session=self.binding if binding is None else binding)

    async def assert_denied(self, coroutine, code, status=409):
        with self.assertRaises(HTTPException) as caught:
            await coroutine
        self.assertEqual(caught.exception.status_code, status)
        self.assertEqual(caught.exception.detail["code"], code)

    async def test_index_and_majority_journal_authority(self):
        await fence.ensure_review_session_storage(self.db)
        options = fence._collection(self.db)
        self.assertEqual(options.write_concern.document, {"w": "majority", "j": True})
        self.assertEqual(options.read_concern.document, {"level": "majority"})
        indexes = await self.db[fence.SESSIONS].index_information()
        self.assertEqual(indexes["review_session_expiry"]["expireAfterSeconds"], 0)

    async def test_signed_context_is_derived_from_verified_user_and_family(self):
        user = await auth.get_current_user_from_db(request(self.access()), self.db)
        self.assertEqual(user["_review_session"], self.context)
        self.assertEqual(await fence.review_session_context(self.db, user_id="owner", actor=user),
                         {k: self.context[k] for k in ("origin_session_ref", "origin_session_epoch")})

    async def test_missing_authority_denies_signed_access_and_refresh(self):
        await self.db[fence.SESSIONS].delete_one({"_id": self.context["origin_session_ref"]})
        await self.assert_denied(auth.get_current_user_from_db(request(self.access()), self.db), "review_session_revoked", 401)
        await self.assert_denied(auth.refresh_browser_session(request(self.refresh(), refresh=True), Response(), self.db), "review_session_revoked", 401)

    async def test_legacy_access_refresh_work_but_cannot_claim_v2_authority(self):
        legacy = auth.create_access_token(self.actor["id"], self.actor["email"], mfa_verified=True)
        actor = await auth.get_current_user_from_db(request(legacy), self.db)
        self.assertIsNone(actor["_review_session"])
        await self.assert_denied(fence.review_session_context(self.db, user_id="owner", actor=actor),
                                 "review_session_reauthentication_required")
        refresh = auth.create_refresh_token(self.actor["id"], mfa_verified=True)
        response = Response()
        self.assertEqual(await auth.refresh_browser_session(request(refresh, refresh=True), response, self.db), {"ok": True})
        self.assertEqual(await self.db[fence.SESSIONS].count_documents({}), 1)
        self.assertEqual((await fence.revoke_review_session(self.db, actor))["server_revocation_confirmed"], False)

    async def test_database_injected_session_field_cannot_override_signed_context(self):
        await self.db.users.update_one({"id": self.actor["id"]}, {"$set": {"_review_session": self.context}})
        legacy = auth.create_access_token(self.actor["id"], self.actor["email"], mfa_verified=True)
        self.assertIsNone((await auth.get_current_user_from_db(request(legacy), self.db))["_review_session"])

    async def test_wrong_actor_owner_epoch_and_partial_claims_reject(self):
        claims = jwt.decode(self.access(), auth.get_jwt_secret(), algorithms=["HS256"])
        cases = ({"review_owner": "other"}, {"review_epoch": 2}, {"review_epoch": True},
                 {"review_sid": "invalid"}, {"review_sid": None})
        for mutation in cases:
            with self.subTest(mutation=mutation):
                token = jwt.encode({**claims, **mutation}, auth.get_jwt_secret(), algorithm="HS256")
                await self.assert_denied(auth.get_current_user_from_db(request(token), self.db), "review_session_invalid", 401)
        other = {**self.actor, "id": "another"}
        await self.assert_denied(fence.validate_review_session(self.db, claims, other), "review_session_invalid", 401)

    async def test_employee_owner_reassignment_invalidates_old_family(self):
        await self.db.users.update_one({"id": self.actor["id"]}, {"$set": {"created_by": "another"}})
        await self.assert_denied(auth.get_current_user_from_db(request(self.access()), self.db), "review_session_invalid", 401)

    async def test_native_employee_bridge_preserves_signed_family_and_real_actor_with_registry_fallback(self):
        import order_review_routes
        await self.db.users.insert_one({"id": "owner", "role": "owner", "email": "owner@example.invalid",
                                        "_review_session": {"origin_session_ref": "FORGED-OWNER-DATA"}})
        await self.db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": "owner", "user_id": self.actor["id"],
            "enabled": True, "permissions": ["app.page.pending_review"]})
        await self.db.mezan_employees_v2.insert_one({"account_user_id": self.actor["id"], "user_id": "owner"})
        path = Path(__file__).parents[1]/"server.py"
        function = next(node for node in ast.parse(path.read_text(encoding="utf-8")).body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == "current_user")
        function.decorator_list=[]; function.returns=None
        for argument in function.args.args: argument.annotation=None
        module=ast.Module(body=[function],type_ignores=[]);ast.fix_missing_locations(module)
        namespace={"db":self.db,"get_current_user_from_db":auth.get_current_user_from_db}
        exec(compile(module,str(path),"exec"),namespace)
        for registry_only in (False, True):
            with self.subTest(registry_only=registry_only):
                actor=deepcopy(self.actor);actor.pop("_review_session")
                if registry_only:
                    actor.pop("created_by")
                    await self.db.users.update_one({"id":actor["id"]},{"$unset":{"created_by":""}})
                context=await fence.create_review_session(self.db,actor)
                token=auth.create_access_token(actor["id"],actor["email"],mfa_verified=True,
                    review_session=context,client_type=MOBILE_APP_CLIENT)
                native=request(token);native.scope["path"]="/api/order-reviews-v1/synthetic"
                principal=await namespace["current_user"](native)
                self.assertEqual(principal["id"],"owner")
                self.assertEqual(principal["_review_session"],context)
                reviewer=order_review_routes._require_reviewer(principal)
                self.assertEqual(reviewer["id"],actor["id"])
                self.assertEqual(reviewer["created_by"],"owner")
                self.assertEqual(reviewer["_review_session"]["actor_id"],actor["id"])
                self.assertEqual((await fence.review_session_context(self.db,user_id="owner",actor=reviewer))["origin_session_ref"],context["origin_session_ref"])
                actor["_review_session"]=context
                self.assertTrue((await fence.revoke_review_session(self.db,actor))["server_revocation_confirmed"])
                await self.assert_denied(namespace["current_user"](native),"review_session_revoked",401)

    async def test_mobile_rewriting_preserves_identical_signed_family(self):
        tokens = _mobile_tokens_from_access_token(self.access())
        self.assertIsNotNone(tokens)
        for token in tokens:
            payload = jwt.decode(token, auth.get_jwt_secret(), algorithms=["HS256"])
            self.assertEqual(payload["client"], MOBILE_APP_CLIENT)
            self.assertEqual({k: payload[k] for k in fence.claims_for_session(self.context)}, fence.claims_for_session(self.context))

    async def test_browser_refresh_preserves_family_without_reactivation(self):
        response = Response()
        await auth.refresh_browser_session(request(self.refresh(), refresh=True), response, self.db)
        self.assertEqual(await self.db[fence.SESSIONS].count_documents({}), 1)
        from http.cookies import SimpleCookie
        for key, value in response.raw_headers:
            if key != b"set-cookie":
                continue
            cookies = SimpleCookie(); cookies.load(value.decode())
            for cookie in cookies.values():
                payload = jwt.decode(cookie.value, auth.get_jwt_secret(), algorithms=["HS256"])
                self.assertEqual(payload["review_sid"], self.context["origin_session_ref"])
        await fence.revoke_review_session(self.db, self.actor)
        await self.assert_denied(auth.refresh_browser_session(request(self.refresh(), refresh=True), Response(), self.db),
                                 "review_session_revoked", 401)
        self.assertEqual((await self.db[fence.SESSIONS].find_one({}))["state"], "revoked")

    async def mobile_refresh(self, token):
        middleware = MobileSessionSecurityMiddleware(AsyncMock(side_effect=AssertionError("unexpected app/provider")), db=self.db)
        messages = [{"type": "http.request", "body": json.dumps({"refresh_token": token}).encode(), "more_body": False}]
        sent = []
        async def receive(): return messages.pop(0) if messages else {"type": "http.disconnect"}
        async def send(message): sent.append(message)
        await middleware({"type": "http", "method": "POST", "path": "/api/auth/mobile/refresh"}, receive, send)
        start = next(m for m in sent if m["type"] == "http.response.start")
        body = json.loads(b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body"))
        return start["status"], body

    async def test_mobile_refresh_preserves_family_then_refuses_revoked(self):
        status, body = await self.mobile_refresh(self.refresh(mobile=True))
        self.assertEqual(status, 200, body)
        for key in ("access_token", "refresh_token"):
            self.assertEqual(jwt.decode(body[key], auth.get_jwt_secret(), algorithms=["HS256"])["review_sid"], self.context["origin_session_ref"])
        await fence.revoke_review_session(self.db, self.actor)
        status, body = await self.mobile_refresh(body["refresh_token"])
        self.assertEqual(status, 401, body)
        self.assertEqual(body["detail"]["code"], "review_session_revoked")

    async def test_old_logout_cannot_revoke_new_login(self):
        new = await fence.create_review_session(self.db, self.actor)
        result = await fence.revoke_review_session(self.db, self.actor)
        self.assertTrue(result["server_revocation_confirmed"])
        await self.assert_denied(auth.get_current_user_from_db(request(self.access()), self.db), "review_session_revoked", 401)
        self.assertEqual((await auth.get_current_user_from_db(request(self.access(new)), self.db))["_review_session"], new)

    async def test_revocation_db_failure_never_claims_server_ack(self):
        with patch.object(fence, "_collection", return_value=SimpleNamespace(update_one=AsyncMock(side_effect=OperationFailure("synthetic storage failure")))):
            with self.assertRaises(OperationFailure):
                await fence.revoke_review_session(self.db, self.actor)
        await self.complete()
        self.assertEqual(await self.db.order_review_events.count_documents({}), 1)

    async def test_no_match_or_unacknowledged_revoke_never_claims_ack(self):
        for acknowledged, matched in ((False, 1), (True, 0)):
            with self.subTest(acknowledged=acknowledged, matched=matched), patch.object(fence, "_collection", return_value=SimpleNamespace(
                update_one=AsyncMock(return_value=SimpleNamespace(acknowledged=acknowledged, matched_count=matched)))):
                await self.assert_denied(fence.revoke_review_session(self.db, self.actor), "review_session_revocation_unconfirmed", 503)

    async def test_local_offline_logout_is_not_server_revocation(self):
        # Local UI state is deliberately not a server signal. This negative
        # control preserves the documented boundary instead of a false promise.
        local_actor = deepcopy(self.actor); local_actor.pop("_review_session")
        self.assertFalse((await fence.revoke_review_session(self.db, local_actor))["server_revocation_confirmed"])
        await self.complete()
        self.assertEqual(await self.db.order_review_events.count_documents({}), 1)

    async def test_revoke_ack_before_acceptance_rejects_zero_effects(self):
        self.assertTrue((await fence.revoke_review_session(self.db, self.actor))["server_revocation_confirmed"])
        await self.assert_denied(self.complete(), "review_session_revoked")
        self.assertEqual(await self.db.order_review_events.count_documents({}), 0)

    async def test_expired_row_rejects_inflight_acceptance_and_backdated_capability(self):
        await self.db[fence.SESSIONS].update_one({"_id": self.context["origin_session_ref"]},
            {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
        await self.assert_denied(self.complete(), "review_session_revoked")
        async def backdated(scoped):
            result = await scoped[fence.SESSIONS].update_one({"_id": self.context["origin_session_ref"],
                "user_id": "owner", "actor_id": self.actor["id"], "epoch": 1, "state": "active",
                "expires_at": {"$gt": datetime(2000, 1, 1, tzinfo=timezone.utc)}}, {"$inc": {"fence": 1}})
            self.assertEqual(result.matched_count, 0)
        await self.complete(callback=backdated)
        self.assertEqual(await self.db.order_review_events.count_documents({}), 0)

    async def test_completion_first_revoke_ack_waits_for_commit(self):
        reserved, release = asyncio.Event(), asyncio.Event()
        async def accept(scoped):
            await fence.fence_review_session(scoped, user_id="owner", **self.binding)
            reserved.set()
            await release.wait()
            await scoped.order_review_events.insert_one({"user_id": "owner", "event_type": "synthetic"})
        completion = asyncio.create_task(self.complete(callback=accept))
        await asyncio.wait_for(reserved.wait(), 5)
        revoke = asyncio.create_task(fence.revoke_review_session(self.db, self.actor))
        try:
            await asyncio.sleep(.05)
            self.assertFalse(revoke.done(), "revoke ACK must not overtake transaction owning row")
        finally:
            release.set()
        await completion
        self.assertTrue((await revoke)["server_revocation_confirmed"])
        self.assertEqual(await self.db.order_review_events.count_documents({}), 1)

    async def test_stale_snapshot_after_revocation_cannot_commit(self):
        async with await self.client.start_session() as session:
            session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority", j=True))
            await self.db[fence.SESSIONS].find_one({"_id": self.context["origin_session_ref"]}, session=session)
            self.assertTrue((await fence.revoke_review_session(self.db, self.actor))["server_revocation_confirmed"])
            state = {"owner": "owner", "failed": False, "profile": "fulfillment", "review_session": self.binding}
            scoped = OperationalDatabase(self.db, session, state, "owner")
            with self.assertRaises(OperationFailure) as caught:
                await fence.fence_review_session(scoped, user_id="owner", **self.binding)
            self.assertEqual(caught.exception.code, 112)
            await session.abort_transaction()
        self.assertEqual(await self.db.order_review_events.count_documents({}), 0)

    async def test_aborted_completion_rolls_back_session_fence_and_effect(self):
        async def fail(scoped):
            await fence.fence_review_session(scoped, user_id="owner", **self.binding)
            await scoped.order_review_events.insert_one({"user_id": "owner", "event_type": "synthetic"})
            raise ValueError("synthetic later failure")
        with self.assertRaisesRegex(ValueError, "synthetic later failure"):
            await self.complete(callback=fail)
        self.assertEqual((await self.db[fence.SESSIONS].find_one({}))["fence"], 0)
        self.assertEqual(await self.db.order_review_events.count_documents({}), 0)

    async def test_refresh_inflight_after_revoke_cannot_return_usable_tokens(self):
        context = await fence.validate_review_session(self.db, jwt.decode(self.refresh(), auth.get_jwt_secret(), algorithms=["HS256"]), self.actor, refresh=True)
        await fence.revoke_review_session(self.db, self.actor)
        await self.assert_denied(auth.get_current_user_from_db(request(self.access(context)), self.db), "review_session_revoked", 401)

    async def test_capability_denies_other_session_actor_owner_and_mutation(self):
        for mutation in ({"_id": "B" * 43}, {"actor_id": "another"}, {"user_id": "another"}, {"epoch": 2}, {"state": "revoked"}):
            with self.subTest(mutation=mutation):
                async def bad(scoped):
                    await scoped[fence.SESSIONS].update_one({"_id": self.context["origin_session_ref"], "user_id": "owner",
                        "actor_id": self.actor["id"], "epoch": 1, "state": "active",
                        "expires_at": {"$gt": datetime.now(timezone.utc)}, **mutation}, {"$inc": {"fence": 1}})
                await self.assert_denied(self.complete(callback=bad), "operational_review_session_scope_conflict")
        for update in ({"$set": {"state": "active"}}, {"$inc": {"fence": 0}}, {"$inc": {"fence": 2}}, {"$unset": {"state": ""}}):
            with self.subTest(update=update):
                async def bad(scoped):
                    await scoped[fence.SESSIONS].update_one({"_id": self.context["origin_session_ref"], "user_id": "owner",
                        "actor_id": self.actor["id"], "epoch": 1, "state": "active",
                        "expires_at": {"$gt": datetime.now(timezone.utc)}}, update)
                await self.assert_denied(self.complete(callback=bad), "operational_review_session_scope_conflict")

    async def test_unbound_or_caught_capability_rejection_rolls_back(self):
        async def bad(scoped):
            try:
                await scoped[fence.SESSIONS].delete_many({"user_id": "owner"})
            except HTTPException:
                pass
            await scoped.order_review_events.insert_one({"user_id": "owner", "event_type": "synthetic"})
        await self.assert_denied(self.complete(callback=bad), "operational_financial_write_forbidden")
        async def unbound(scoped):
            await fence.fence_review_session(scoped, user_id="owner", **self.binding)
        await self.assert_denied(operational_owner(self.db, "owner", unbound), "operational_review_session_scope_conflict")
        self.assertEqual(await self.db.order_review_events.count_documents({}), 0)

    async def test_capability_denies_upsert_insert_delete_other_profiles_and_nested_rebinding(self):
        query = {"_id": self.context["origin_session_ref"], "user_id": "owner", "actor_id": self.actor["id"],
                 "epoch": 1, "state": "active", "expires_at": {"$gt": datetime.now(timezone.utc)}}
        async def upsert(scoped):
            await scoped[fence.SESSIONS].update_one(query, {"$inc": {"fence": 1}}, upsert=True)
        async def insert(scoped):
            await scoped[fence.SESSIONS].insert_one({**query, "fence": 0})
        async def delete(scoped):
            await scoped[fence.SESSIONS].delete_one(query)
        async def rebind(scoped):
            await operational_owner(scoped, "owner", upsert, review_session={**self.binding, "actor_id": "another"})
        for callback in (upsert, insert, delete):
            with self.subTest(method=callback.__name__):
                await self.assert_denied(self.complete(callback=callback), "operational_review_session_scope_conflict")
        await self.assert_denied(self.complete(callback=rebind), "operational_transaction_scope_conflict")
        for profile in ("employee_setup", "driver_cash_delivery", "driver_cash_reconciliation", "driver_late_delivery_evidence"):
            with self.subTest(profile=profile):
                await self.assert_denied(operational_owner(self.db, "owner", upsert, profile=profile, review_session=self.binding),
                                         "operational_review_session_scope_conflict")
        self.assertEqual((await self.db[fence.SESSIONS].find_one({}))["fence"], 0)

    async def test_actual_logout_route_checks_durable_revoke_before_success(self):
        path = Path(__file__).parents[1] / "server.py"
        function = next(node for node in ast.parse(path.read_text(encoding="utf-8")).body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == "logout")
        function.decorator_list=[]; function.returns=None; function.args.defaults=[]
        for argument in function.args.args: argument.annotation=None
        module=ast.Module(body=[function], type_ignores=[]); ast.fix_missing_locations(module)
        namespace={"db":self.db, "clear_auth_cookies":auth.clear_auth_cookies}
        exec(compile(module, str(path), "exec"), namespace)
        new = await fence.create_review_session(self.db, self.actor)
        response=Response()
        result=await namespace["logout"](response, self.actor)
        self.assertTrue(result["ok"] and result["server_revocation_confirmed"])
        self.assertEqual((await self.db[fence.SESSIONS].find_one({"_id": self.context["origin_session_ref"]}))["state"], "revoked")
        self.assertEqual((await self.db[fence.SESSIONS].find_one({"_id": new["origin_session_ref"]}))["state"], "active")
        self.assertFalse(any(key == b"set-cookie" for key,value in response.raw_headers),
                         "old logout response must not erase newer login cookies")
        await self.assert_denied(auth.get_current_user_from_db(request(self.access()), self.db), "review_session_revoked", 401)
        legacy=Response()
        legacy_result=await namespace["logout"](legacy, {"id": self.actor["id"], "role": "viewer", "created_by": "owner"})
        self.assertFalse(legacy_result["server_revocation_confirmed"])
        self.assertEqual(sum(key == b"set-cookie" for key,value in legacy.raw_headers), 2)
        failed=Response()
        with patch.object(fence, "revoke_review_session", AsyncMock(side_effect=OperationFailure("synthetic unavailable"))):
            with self.assertRaises(OperationFailure):
                await namespace["logout"](failed, self.actor)
        self.assertFalse(any(key == b"set-cookie" for key,value in failed.raw_headers))

    async def test_login_mfa_otp_and_passkey_issue_distinct_completed_families(self):
        for module_name in ("mfa_security", "email_otp_security", "passkey_security"):
            with self.subTest(module=module_name):
                module = __import__(module_name)
                response = await module._session_response(self.db, self.actor)
                token = json.loads(response.body)["access_token"]
                user = await auth.get_current_user_from_db(request(token), self.db)
                self.assertNotEqual(user["_review_session"]["origin_session_ref"], self.context["origin_session_ref"])
                self.assertTrue(jwt.decode(token, auth.get_jwt_secret(), algorithms=["HS256"])["mfa"])
        access, refresh = await auth.issue_authenticated_tokens(self.db, self.actor, mfa_verified=True)
        self.assertIsNotNone((await auth.get_current_user_from_db(request(access), self.db))["_review_session"])
        self.assertEqual(jwt.decode(access, auth.get_jwt_secret(), algorithms=["HS256"])["review_sid"], jwt.decode(refresh, auth.get_jwt_secret(), algorithms=["HS256"])["review_sid"])

    async def test_native_driver_password_exception_creates_family_without_weakening_browser_otp(self):
        driver = {"id": "driver", "role": "store_driver", "created_by": "owner", "email": "driver@example.invalid"}
        await self.db.users.insert_one(driver)
        access, _ = await auth.issue_authenticated_tokens(self.db, driver, client_type=MOBILE_APP_CLIENT)
        user = await auth.get_current_user_from_db(request(access), self.db)
        self.assertIsNotNone(user["_review_session"])
        browser, _ = await auth.issue_authenticated_tokens(self.db, driver)
        self.assertNotIn("review_sid", jwt.decode(browser, auth.get_jwt_secret(), algorithms=["HS256"]))

    async def test_pending_mfa_or_otp_never_gets_authority(self):
        before = await self.db[fence.SESSIONS].count_documents({})
        owner = {**self.actor, "role": "owner"}
        access, _ = await auth.issue_authenticated_tokens(self.db, owner)
        self.assertNotIn("review_sid", jwt.decode(access, auth.get_jwt_secret(), algorithms=["HS256"]))
        with patch.dict(os.environ, {"EMAIL_OTP_ENABLED": "1"}):
            access, _ = await auth.issue_authenticated_tokens(self.db, self.actor)
            self.assertNotIn("review_sid", jwt.decode(access, auth.get_jwt_secret(), algorithms=["HS256"]))
        self.assertEqual(await self.db[fence.SESSIONS].count_documents({}), before)
