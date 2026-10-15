"""Security and no-leak tests for the short-lived production provisioner."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo.errors import DuplicateKeyError

from customer_intelligence.temporary_whatsapp_provisioning import (
    COMPLETIONS_COLLECTION,
    CONFIRMATION_LITERAL,
    CSRF_COOKIE,
    CSRF_HEADER,
    FEATURE_FLAG_ENV,
    HARD_EXPIRES_AT,
    INTENT_HEADER,
    INTENT_VALUE,
    make_temporary_whatsapp_provisioning_router,
)
from customer_intelligence.whatsapp_provisioning import BINDING_HMAC_ENV


NOW = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)
ORIGIN = "https://mezansalla.com"
PATH = "/api/customer-intelligence/v1/owner/whatsapp-provisioning"
PHONE_NUMBER_ID = "123456789012345"


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
            continue
        actual = document.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif isinstance(expected, dict) and "$lte" in expected:
            if actual is None or actual > expected["$lte"]:
                return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.maximum = len(rows)

    def limit(self, value):
        self.maximum = value
        return self

    async def to_list(self, *, length):
        return deepcopy(self.rows[: min(length, self.maximum)])


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or [])
        self.insert_calls = []

    def find(self, query, _projection=None):
        return FakeCursor([row for row in self.rows if _matches(row, query)])

    async def find_one(self, query, _projection=None):
        row = next((row for row in self.rows if _matches(row, query)), None)
        return deepcopy(row) if row is not None else None

    async def insert_one(self, document):
        if document.get("_id") is not None and any(
            row.get("_id") == document.get("_id") for row in self.rows
        ):
            raise DuplicateKeyError("duplicate _id")
        self.insert_calls.append(deepcopy(document))
        self.rows.append(deepcopy(document))
        return SimpleNamespace(inserted_id=document.get("_id", "new"))

    async def update_one(self, query, update):
        row = next((row for row in self.rows if _matches(row, query)), None)
        if row is None:
            return SimpleNamespace(modified_count=0)
        before = deepcopy(row)
        row.update(deepcopy(update.get("$set", {})))
        for key in update.get("$unset", {}):
            row.pop(key, None)
        return SimpleNamespace(modified_count=int(row != before))

    async def find_one_and_update(
        self,
        query,
        update,
        *,
        upsert=False,
        return_document=None,
    ):
        del return_document
        row = next((row for row in self.rows if _matches(row, query)), None)
        if row is None:
            requested_id = query.get("_id")
            if any(existing.get("_id") == requested_id for existing in self.rows):
                raise DuplicateKeyError("lease already held")
            if not upsert:
                return None
            row = deepcopy(update.get("$setOnInsert", {}))
            self.rows.append(row)
        row.update(deepcopy(update.get("$set", {})))
        return deepcopy(row)

    async def delete_one(self, query):
        for index, row in enumerate(self.rows):
            if _matches(row, query):
                self.rows.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakeDB:
    def __init__(self):
        self.users = FakeCollection(
            [{"id": "owner-1", "role": "owner", "email": "owner@example.test"}]
        )
        self.salla_integrations = FakeCollection(
            [
                {
                    "user_id": "owner-1",
                    "store_id": 945168084,
                    "status": "connected",
                }
            ]
        )
        self.mezan_customer_channels_v1 = FakeCollection()
        self.mezan_customer_channel_provision_locks_v1 = FakeCollection()
        setattr(self, COMPLETIONS_COLLECTION, FakeCollection())

    def serialized(self) -> str:
        collections = {
            key: value.rows
            for key, value in vars(self).items()
            if isinstance(value, FakeCollection)
        }
        return json.dumps(collections, default=str, sort_keys=True)


def _app(db, user, *, clock=lambda: NOW):
    app = FastAPI()
    api = APIRouter(prefix="/api")

    async def current_user(_request):
        return deepcopy(user)

    api.include_router(
        make_temporary_whatsapp_provisioning_router(
            db,
            current_user,
            clock=clock,
        )
    )
    app.include_router(api)
    return app


async def _client(app, *, session="mfa-owner-session"):
    client = AsyncClient(transport=ASGITransport(app=app), base_url=ORIGIN)
    client.cookies.set("access_token", session, domain="mezansalla.com", path="/")
    return client


async def _open_control(client):
    response = await client.get(PATH)
    assert response.status_code == 200
    csrf = client.cookies.get(CSRF_COOKIE)
    assert csrf
    return response, csrf


def _post_headers(csrf):
    return {
        "Origin": ORIGIN,
        "Sec-Fetch-Site": "same-origin",
        "Content-Type": "application/json",
        INTENT_HEADER: INTENT_VALUE,
        CSRF_HEADER: csrf,
    }


@pytest.fixture(autouse=True)
def temporary_route_environment(monkeypatch):
    monkeypatch.setenv(FEATURE_FLAG_ENV, "true")
    monkeypatch.setenv(
        BINDING_HMAC_ENV,
        "test-only-dedicated-binding-key-0123456789",
    )


@pytest.mark.asyncio
async def test_route_is_default_off_and_hidden_before_auth(monkeypatch):
    monkeypatch.delenv(FEATURE_FLAG_ENV, raising=False)
    auth_calls = 0

    async def current_user(_request):
        nonlocal auth_calls
        auth_calls += 1
        return {"id": "owner-1", "role": "owner"}

    app = FastAPI()
    api = APIRouter(prefix="/api")
    api.include_router(
        make_temporary_whatsapp_provisioning_router(FakeDB(), current_user, clock=lambda: NOW)
    )
    app.include_router(api)
    async with await _client(app) as client:
        response = await client.get(PATH)
        schema = (await client.get("/openapi.json")).json()

    assert response.status_code == 404
    assert auth_calls == 0
    assert PATH not in schema["paths"]


@pytest.mark.asyncio
async def test_route_hard_expiry_and_exact_owner_role_fail_closed():
    expired_app = _app(
        FakeDB(),
        {"id": "owner-1", "role": "owner"},
        clock=lambda: HARD_EXPIRES_AT,
    )
    async with await _client(expired_app) as client:
        assert (await client.get(PATH)).status_code == 404

    employee_app = _app(
        FakeDB(),
        {"id": "owner-1", "role": "employee", "is_owner": True},
    )
    async with await _client(employee_app) as client:
        response = await client.get(PATH)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_control_has_strict_cookie_csp_and_absolute_post_paths():
    app = _app(FakeDB(), {"id": "owner-1", "role": "owner"})
    async with await _client(app) as client:
        response, _csrf = await _open_control(client)

    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "fetch('./preview'" not in response.text
    assert f"'{PATH}/preview'" in response.text
    assert f"'{PATH}/apply'" in response.text
    assert response.headers["cache-control"].startswith("no-store")


@pytest.mark.asyncio
async def test_post_requires_same_origin_intent_json_and_bounded_body():
    app = _app(FakeDB(), {"id": "owner-1", "role": "owner"})
    async with await _client(app) as client:
        _response, csrf = await _open_control(client)
        body = {"phone_number_id": PHONE_NUMBER_ID, "allow_additional_channel": False}

        missing_origin = await client.post(
            f"{PATH}/preview",
            json=body,
            headers={INTENT_HEADER: INTENT_VALUE, CSRF_HEADER: csrf},
        )
        missing_intent = await client.post(
            f"{PATH}/preview",
            json=body,
            headers={
                "Origin": ORIGIN,
                "Sec-Fetch-Site": "same-origin",
                CSRF_HEADER: csrf,
            },
        )
        wrong_type = await client.post(
            f"{PATH}/preview",
            content="opaque",
            headers={
                **_post_headers(csrf),
                "Content-Type": "text/plain",
            },
        )
        oversized = await client.post(
            f"{PATH}/preview",
            content=b"x" * 4097,
            headers=_post_headers(csrf),
        )

    assert missing_origin.status_code == 403
    assert missing_intent.status_code == 403
    assert wrong_type.status_code == 415
    assert oversized.status_code == 413
    for response in (missing_origin, missing_intent, wrong_type, oversized):
        assert PHONE_NUMBER_ID not in response.text


@pytest.mark.asyncio
async def test_preview_is_read_only_and_never_returns_provider_id():
    db = FakeDB()
    app = _app(db, {"id": "owner-1", "role": "owner"})
    async with await _client(app) as client:
        _control, csrf = await _open_control(client)
        response = await client.post(
            f"{PATH}/preview",
            json={
                "phone_number_id": PHONE_NUMBER_ID,
                "allow_additional_channel": False,
            },
            headers=_post_headers(csrf),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["plan"]["action"] == "insert"
    assert payload["plan"]["receive_only"] is True
    assert payload["plan"]["send_allowed"] is False
    assert payload["plan"]["ai_auto_reply_allowed"] is False
    assert PHONE_NUMBER_ID not in response.text
    assert db.mezan_customer_channels_v1.insert_calls == []
    assert getattr(db, COMPLETIONS_COLLECTION).insert_calls == []


@pytest.mark.asyncio
async def test_apply_is_session_bound_receive_only_and_latched_once():
    db = FakeDB()
    app = _app(db, {"id": "owner-1", "role": "owner"})
    async with await _client(app) as client:
        _control, csrf = await _open_control(client)
        preview = await client.post(
            f"{PATH}/preview",
            json={
                "phone_number_id": PHONE_NUMBER_ID,
                "allow_additional_channel": False,
            },
            headers=_post_headers(csrf),
        )
        proof = preview.json()["plan_proof"]

        client.cookies.set(
            "access_token",
            "different-mfa-session",
            domain="mezansalla.com",
            path="/",
        )
        _second_control, second_csrf = await _open_control(client)
        wrong_session = await client.post(
            f"{PATH}/apply",
            json={
                "phone_number_id": PHONE_NUMBER_ID,
                "allow_additional_channel": False,
                "plan_proof": proof,
                "confirmation": CONFIRMATION_LITERAL,
            },
            headers=_post_headers(second_csrf),
        )
        assert wrong_session.status_code == 403
        assert db.mezan_customer_channels_v1.insert_calls == []

        fresh_preview = await client.post(
            f"{PATH}/preview",
            json={
                "phone_number_id": PHONE_NUMBER_ID,
                "allow_additional_channel": False,
            },
            headers=_post_headers(second_csrf),
        )
        applied = await client.post(
            f"{PATH}/apply",
            json={
                "phone_number_id": PHONE_NUMBER_ID,
                "allow_additional_channel": False,
                "plan_proof": fresh_preview.json()["plan_proof"],
                "confirmation": CONFIRMATION_LITERAL,
            },
            headers=_post_headers(second_csrf),
        )

        different = await client.post(
            f"{PATH}/preview",
            json={
                "phone_number_id": "987654321098765",
                "allow_additional_channel": True,
            },
            headers=_post_headers(second_csrf),
        )

    assert applied.status_code == 200
    assert applied.json()["result"]["mode"] == "applied"
    inserted = db.mezan_customer_channels_v1.insert_calls
    assert len(inserted) == 1
    assert inserted[0]["status"] == "connected"
    assert inserted[0]["ingress_enabled"] is True
    assert inserted[0]["egress_mode"] == "disabled"
    assert inserted[0]["send_allowed"] is False
    assert inserted[0]["ai_auto_reply_allowed"] is False
    assert inserted[0]["plaintext_credentials_stored"] is False
    assert different.status_code == 409
    assert PHONE_NUMBER_ID not in applied.text
    assert PHONE_NUMBER_ID not in db.serialized()
    latch = getattr(db, COMPLETIONS_COLLECTION).rows
    assert len(latch) == 1
    assert latch[0]["status"] == "completed"
