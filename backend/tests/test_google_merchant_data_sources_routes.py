"""Real owner wrapper and bound transport; synthetic crypto/provider responses."""
import asyncio
from copy import deepcopy
import inspect

from fastapi import APIRouter, FastAPI
import httpx
import pytest

from integrations_control_center import google_ads_client as ads
from integrations_control_center import google_ads_reporting as reporting
from integrations_control_center import google_merchant_data_sources_routes as route
from integrations_control_center.google_merchant_data_sources_proof import DATA_SOURCE_FIELDS
from tests.test_google_merchant_data_sources import source
from tests.test_google_merchant_observation_routes import (
    DB, USER, OWNER, ACCOUNT, MERCHANT, CREDS, ACCOUNTS, INTEGRATIONS, REAL_CLIENT, native_link,
)
from integrations_control_center.google_merchant_observation_routes import LINK_QUERY


def providers(monkeypatch, *, effect=None, pages=None, links=None, complete=True):
    calls = {"metadata": 0, "links": 0, "merchant": 0, "close": 0, "constructed": 0}
    monkeypatch.setattr(reporting, "decrypt_google_token", lambda value:
        "synthetic-token" if value == "synthetic-ciphertext" else "different-token")
    async def stage(name):
        calls[name] += 1
        if effect:
            result = effect(name, calls[name])
            if inspect.isawaitable(result): await result
    class Ads:
        def __init__(self, token, account):
            assert token == "synthetic-token" and account["external_account_id"] == ACCOUNT
            calls["constructed"] += 1
        async def account_metadata(self):
            await stage("metadata")
            return {"account_id": ACCOUNT, "customer_id": ACCOUNT, "currency_code": "SAR",
                "time_zone": "Asia/Riyadh", "identity_verified": True}
        async def search(self, query, max_rows):
            assert query == LINK_QUERY and max_rows == 200
            await stage("links")
            return deepcopy([native_link()] if links is None else links), complete
        async def close(self): calls["close"] += 1
    monkeypatch.setattr(ads, "GoogleAdsClient", Ads)
    async def merchant(request):
        assert request.method == "GET"
        assert request.url.scheme == "https" and request.url.host == "merchantapi.googleapis.com"
        assert request.url.path == "/datasources/v1/accounts/123/dataSources"
        expected = {"pageSize": "100", "fields": DATA_SOURCE_FIELDS}
        if calls["merchant"]: expected["pageToken"] = "opaque-next"
        assert dict(request.url.params) == expected
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        assert request.headers["Accept-Encoding"] == "identity"
        await stage("merchant")
        payload = {"dataSources": [source()]} if pages is None else pages[calls["merchant"] - 1]
        return payload if isinstance(payload, httpx.Response) else httpx.Response(200, json=payload)
    monkeypatch.setattr(reporting.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(merchant), **kw))
    return calls


async def call(db, *, user=None, params=None, method="GET"):
    app, router = FastAPI(), APIRouter()
    async def current_user(): return USER if user is None else user
    route.attach_google_merchant_data_sources_routes(router, db, current_user)
    app.include_router(router)
    async with REAL_CLIENT(transport=httpx.ASGITransport(app=app), base_url="https://test.invalid", trust_env=False) as client:
        return await client.request(method, "/google_ads/merchant-products/data-sources",
            params={"account_id": ACCOUNT, "merchant_id": MERCHANT} if params is None else params)


@pytest.mark.asyncio
async def test_owner_reads_two_native_pages_with_actual_bound_session_and_unchanged_db(monkeypatch):
    db = DB(); before = deepcopy(db.rows)
    calls = providers(monkeypatch, pages=[{"dataSources": [source()], "nextPageToken": "opaque-next"},
        {"dataSources": [source(source_id="19")]}])
    response = await call(db)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [row["data_source_id"] for row in result["sources"]] == ["18", "19"]
    assert result["scope"] == {"owner_id": OWNER, "ads_customer_id": ACCOUNT, "merchant_id": MERCHANT}
    assert result["pagination_traversal_complete"] is True and result["pages_read"] == 2
    assert result["publisher_identity_verified"] is result["variant_mapping_verified"] is False
    assert result["store_product_mapping_verified"] is result["ad_eligibility_verified"] is False
    assert result["execution_authorized"] is result["atomic_snapshot"] is False
    assert result["provider_writes"] == 0
    assert result["access_observation"]["native_link_resource"] == "customers/5052346701/productLinks/17"
    assert result["access_observation"]["continuous_authorization_proved"] is False
    assert calls == {"metadata": 2, "links": 2, "merchant": 2, "close": 1, "constructed": 1}
    assert db.rows == before and db.transactions == db.sessions_closed == 3
    assert all(item[3] for item in db.reads if item[0] != CREDS)
    assert not any(value in response.text for value in ("synthetic-token", "synthetic-ciphertext", "opaque-next", "fetchUri"))


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{}, {"account_id": ACCOUNT}, {"merchant_id": MERCHANT},
    {"account_id": "01", "merchant_id": MERCHANT}, {"account_id": ACCOUNT, "merchant_id": "0"},
    {"account_id": ACCOUNT, "merchant_id": "9223372036854775808"},
    {"account_id": ACCOUNT, "merchant_id": "123/dataSources"},
    {"account_id": ACCOUNT, "merchant_id": MERCHANT, "fields": "*"},
    {"account_id": ACCOUNT, "merchant_id": MERCHANT, "pageToken": "arbitrary"},
    [("account_id", ACCOUNT), ("merchant_id", MERCHANT), ("merchant_id", "456")]])
async def test_canonical_explicit_single_account_query_required(monkeypatch, params):
    calls = providers(monkeypatch)
    assert (await call(DB(), params=params)).status_code == 422
    assert calls["constructed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [{}, {"id": OWNER, "role": "owner", "disabled": True},
    {"id": OWNER, "role": "owner", "is_active": False}, {"id": "staff", "role": "admin"},
    {"id": "staff", "role": "viewer", "owner": True}])
async def test_authentication_owner_required_before_provider(monkeypatch, user):
    calls = providers(monkeypatch)
    assert (await call(DB(), user=user)).status_code in (401, 403)
    assert calls["constructed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner_missing", "duplicate_account", "unconnected_account", "foreign_account",
    "manual_provider", "missing_scope", "unknown_subject", "stale_principal"])
async def test_membership_and_authentication_must_be_current_before_provider(monkeypatch, change):
    db = DB()
    if change == "owner_missing": db.rows["users"] = []
    elif change == "duplicate_account": db.rows[ACCOUNTS].append(deepcopy(db.rows[ACCOUNTS][1]))
    elif change == "unconnected_account": db.rows[ACCOUNTS][1]["connection_status"] = "error"
    elif change == "foreign_account": db.rows[ACCOUNTS][1]["user_id"] = "other"
    elif change == "manual_provider": db.rows[INTEGRATIONS][1]["connection_provenance"] = "manual"
    elif change == "missing_scope": db.rows[CREDS][0]["scope"] = ["https://www.googleapis.com/auth/adwords"]
    elif change == "unknown_subject": db.rows[CREDS][0]["google_subject"] = ""
    elif change == "stale_principal": db.rows["users"][0]["password_updated_at"] = "new-session-stamp"
    calls = providers(monkeypatch)
    assert (await call(db)).status_code in (403, 409)
    assert calls["constructed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["role", "owner_delete", "account_delete", "account_replace", "provider",
    "credential", "scope", "password", "missing_null", "native_link"])
async def test_changes_during_provider_read_discard_all_source_rows(monkeypatch, change):
    db = DB(); links = [native_link()]
    def effect(stage, count):
        if stage != "merchant": return
        if change == "role": db.rows["users"][0]["role"] = "viewer"
        elif change == "owner_delete": db.rows["users"] = []
        elif change == "account_delete": db.rows[ACCOUNTS].pop()
        elif change == "account_replace": db.rows[ACCOUNTS][1]["_id"] = "replacement"
        elif change == "provider": db.rows[INTEGRATIONS][1]["connection_status"] = "error"
        elif change == "credential": db.rows[CREDS][0]["access_token_ciphertext"] = "PRIVATE_CHANGED"
        elif change == "scope": db.rows[CREDS][0]["scope"].pop()
        elif change == "password": db.rows["users"][0]["password_updated_at"] = "changed"
        elif change == "missing_null": db.rows[ACCOUNTS][0]["login_customer_id"] = None
        elif change == "native_link": links[:] = [native_link(link_id="19")]
    calls = providers(monkeypatch, effect=effect, links=links)
    response = await call(db)
    assert response.status_code in (403, 409), response.text
    assert "sources" not in response.json() and "PRIVATE_CHANGED" not in response.text
    assert calls["close"] == 1


@pytest.mark.asyncio
async def test_credential_acquisition_membership_change_refuses_before_native_reads(monkeypatch):
    db = DB(); calls = providers(monkeypatch)
    original = reporting._credential
    async def raced(*args):
        token = await original(*args)
        db.rows[ACCOUNTS].pop()
        return token
    monkeypatch.setattr(reporting, "_credential", raced)
    assert (await call(db)).status_code == 409
    assert calls["metadata"] == calls["merchant"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("links,complete", [([], True), ([native_link(merchant="456")], True), ([native_link()], False)])
async def test_complete_native_link_to_selected_merchant_required(monkeypatch, links, complete):
    calls = providers(monkeypatch, links=links, complete=complete)
    assert (await call(DB())).status_code == 409
    assert calls["merchant"] == 0


@pytest.mark.asyncio
async def test_unrelated_owner_change_does_not_affect_current_owner(monkeypatch):
    db = DB()
    def effect(stage, count):
        if stage == "merchant": db.rows[ACCOUNTS].append({"user_id": "other", "provider": "google_ads"})
    providers(monkeypatch, effect=effect)
    assert (await call(db)).status_code == 200


@pytest.mark.asyncio
async def test_cancellation_propagates_and_closes_bound_client(monkeypatch):
    started = asyncio.Event()
    async def effect(stage, count):
        if stage == "merchant": started.set(); await asyncio.Event().wait()
    calls = providers(monkeypatch, effect=effect)
    task = asyncio.create_task(call(DB()))
    await asyncio.wait_for(started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert calls["close"] == 1


@pytest.mark.asyncio
async def test_no_write_method_is_attached(monkeypatch):
    calls = providers(monkeypatch)
    assert (await call(DB(), method="POST")).status_code == 405
    assert calls["constructed"] == 0
