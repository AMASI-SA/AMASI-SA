"""Owner route + actual bound helper/observer, synthetic crypto and transports."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import inspect

from fastapi import APIRouter, FastAPI
from google.ads.googleads.v25.services.types.google_ads_service import GoogleAdsRow
from google.protobuf.json_format import MessageToDict, ParseDict
import httpx
import pytest

from integrations_control_center import google_ads_client as ads
from integrations_control_center import google_ads_reporting as reporting
from integrations_control_center import google_merchant_observation_routes as route

OWNER = "synthetic-owner"
ACCOUNT, MERCHANT = "5052346701", "123"
USER = {"id": OWNER, "role": "owner"}
REAL_CLIENT = httpx.AsyncClient
CREDS = reporting.GOOGLE_CREDENTIALS_COLLECTION
ACCOUNTS = "mezan_integration_accounts_v2"
INTEGRATIONS = "mezan_integrations_v2"


def fixture_rows():
    now = datetime.now(timezone.utc)
    return {"users": [{"_id": "owner-row", **USER}],
        INTEGRATIONS: [{"_id": provider, "user_id": OWNER, "provider": provider,
            "connection_status": "connected", "connection_provenance": "api_connection"} for provider in route.PROVIDERS],
        ACCOUNTS: [{"_id": "ads", "user_id": OWNER, "provider": route.ADS,
            "external_account_id": ACCOUNT, "ad_account_id": ACCOUNT,
            "connection_status": "connected", "connection_provenance": "api_connection"},
            {"_id": "merchant", "user_id": OWNER, "provider": route.MERCHANT,
             "external_account_id": MERCHANT, "ad_account_id": None,
             "connection_status": "connected", "connection_provenance": "api_connection"}],
        CREDS: [{"user_id": OWNER, "access_token_ciphertext": "synthetic-ciphertext",
            "refresh_token_ciphertext": "synthetic-refresh", "google_subject": "synthetic-subject",
            "scope": ["https://www.googleapis.com/auth/adwords", "https://www.googleapis.com/auth/content"],
            "token_type": "Bearer", "expires_at": now + timedelta(hours=1), "updated_at": now}]}


def native_link(merchant=MERCHANT, link_id="17", account=ACCOUNT):
    # An actual installed v25 protobuf validates the shape; no invented status.
    raw = {"customer": {"id": account, "resourceName": "customers/" + account},
        "productLink": {"resourceName": f"customers/{account}/productLinks/{link_id}",
            "productLinkId": link_id, "type": "MERCHANT_CENTER", "merchantCenter": {"merchantCenterId": merchant}}}
    message = GoogleAdsRow.pb(GoogleAdsRow())
    ParseDict(raw, message)
    return MessageToDict(message)


def product():
    return {"name": "accounts/123/products/ar~SA~SKU1", "offerId": "SKU1", "contentLanguage": "ar",
        "feedLabel": "SA", "dataSource": "accounts/123/dataSources/18",
        "productAttributes": {"link": "https://shop.example.invalid/product?id=1",
            "canonicalLink": "https://shop.example.invalid/product", "itemGroupId": "parent"}}


class Cursor:
    def __init__(self, rows): self.rows = rows
    def max_time_ms(self, value): assert value == 2000; return self
    def limit(self, value): self.rows = self.rows[:value]; return self
    async def to_list(self, length): return deepcopy(self.rows[:length])


class Collection:
    def __init__(self, db, name): self.db, self.name = db, name
    def with_options(self, **options): self.db.options.append(options); return self
    def find(self, query, projection, session=None):
        self.db.reads.append((self.name, deepcopy(query), deepcopy(projection), session is not None))
        if self.db.fail:
            raise RuntimeError("PRIVATE_DATABASE_SENTINEL")
        source = session.rows if session else self.db.rows
        def matches(row):
            return all(row.get(k) in v["$in"] if isinstance(v, dict) else row.get(k) == v for k, v in query.items())
        return Cursor([{k: deepcopy(v) for k, v in row.items() if projection.get(k) == 1}
            for row in source.get(self.name, []) if matches(row)])
    async def find_one(self, query, projection):
        rows = await self.find(query, projection).limit(1).to_list(length=1)
        return rows[0] if rows else None


class Session:
    def __init__(self, db): self.db = db
    async def __aenter__(self): return self
    async def __aexit__(self, *args): self.db.sessions_closed += 1
    @asynccontextmanager
    async def start_transaction(self, **options):
        assert options["read_concern"].level == "snapshot"
        assert options["read_preference"] == route.ReadPreference.PRIMARY
        assert options["write_concern"].document == {"w": "majority", "wtimeout": 2000}
        self.db.transactions += 1
        if self.db.tx_fail: raise RuntimeError("PRIVATE_TRANSACTION_SENTINEL")
        self.rows = deepcopy(self.db.rows)
        yield


class DB:
    def __init__(self):
        self.rows, self.options, self.reads = fixture_rows(), [], []
        self.client = self
        self.fail = self.tx_fail = False
        self.transactions = self.sessions_closed = 0
    def __getitem__(self, name): return Collection(self, name)
    def __getattr__(self, name): return self[name]
    async def start_session(self): return Session(self)


def providers(monkeypatch, *, effect=None, links=None, metadata=None, payload=None, complete=True):
    calls = {"metadata": 0, "links": 0, "merchant": 0, "close": 0, "constructed": 0}
    monkeypatch.setattr(reporting, "decrypt_google_token", lambda value: "synthetic-token" if value == "synthetic-ciphertext" else "different-token")
    async def stage(name):
        calls[name] += 1
        if effect:
            result = effect(name, calls[name])
            if inspect.isawaitable(result): await result
    class Ads:
        def __init__(self, token, account):
            assert token == "synthetic-token" and account["external_account_id"] == ACCOUNT
            assert set(account) <= {"external_account_id", "ad_account_id", "login_customer_id"}
            calls["constructed"] += 1
        async def account_metadata(self):
            await stage("metadata")
            return deepcopy(metadata if metadata is not None else {"account_id": ACCOUNT, "customer_id": ACCOUNT,
                "currency_code": "SAR", "time_zone": "Asia/Riyadh", "identity_verified": True})
        async def search(self, query, max_rows):
            assert query == route.LINK_QUERY and max_rows == 200
            await stage("links")
            return deepcopy(links if links is not None else [native_link()]), complete
        async def close(self): calls["close"] += 1
    monkeypatch.setattr(ads, "GoogleAdsClient", Ads)
    async def merchant(request):
        assert request.method == "GET"
        assert request.url.host == "merchantapi.googleapis.com"
        assert request.url.path == "/products/v1/accounts/123/products"
        assert request.headers["Authorization"] == "Bearer synthetic-token"
        await stage("merchant")
        return httpx.Response(200, json=payload if payload is not None else {"products": [product()]})
    monkeypatch.setattr(reporting.httpx, "AsyncClient", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(merchant), **kw))
    return calls


async def call(db, *, user=None, params=None, method="GET"):
    app, router = FastAPI(), APIRouter()
    async def current_user(): return USER if user is None else user
    route.attach_google_merchant_observation_routes(router, db, current_user)
    app.include_router(router)
    async with REAL_CLIENT(transport=httpx.ASGITransport(app=app), base_url="https://test.invalid", trust_env=False) as client:
        return await client.request(method, "/google_ads/merchant-products/observation",
            params={"account_id": ACCOUNT, "merchant_id": MERCHANT} if params is None else params)


@pytest.mark.asyncio
async def test_owner_reads_actual_observer_with_bound_session_and_no_projection_mutation(monkeypatch):
    db = DB(); before = deepcopy(db.rows); calls = providers(monkeypatch)
    response = await call(db)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["products"][0]["offer_id"] == "SKU1" and data["scope"]["owner_id"] == OWNER
    assert data["mapping_status"] == "unverified" and data["store_product_mapping_verified"] is False
    assert data["atomic_snapshot"] is False and data["execution_authorized"] is False and data["provider_writes"] == 0
    assert data["access_observation"]["native_link_resource"] == "customers/5052346701/productLinks/17"
    assert data["access_observation"]["continuous_authorization_proved"] is False
    assert calls == {"metadata": 2, "links": 2, "merchant": 1, "close": 1, "constructed": 1}
    assert db.rows == before and db.transactions == db.sessions_closed == 3
    assert all(item[3] for item in db.reads if item[0] != CREDS)
    assert "synthetic-token" not in response.text and "synthetic-ciphertext" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{}, {"account_id": ACCOUNT}, {"merchant_id": MERCHANT},
    {"account_id": "505-234-6701", "merchant_id": MERCHANT}, {"account_id": "01", "merchant_id": MERCHANT},
    {"account_id": ACCOUNT, "merchant_id": "9223372036854775808"}, {"account_id": ACCOUNT, "merchant_id": "0"}])
async def test_explicit_canonical_selection_required_before_provider(monkeypatch, params):
    calls = providers(monkeypatch)
    assert (await call(DB(), params=params)).status_code == 422
    assert calls["constructed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [{}, {"id": OWNER, "role": "owner", "disabled": True},
    {"id": OWNER, "role": "owner", "is_active": False}, {"id": "staff", "role": "admin"},
    {"id": "staff", "role": "viewer", "owner": True}])
async def test_authentication_disabled_and_staff_denied(monkeypatch, user):
    calls = providers(monkeypatch)
    assert (await call(DB(), user=user)).status_code in (401, 403)
    assert calls["constructed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner_missing", "owner_duplicate", "owner_disabled", "owner_role",
    "provider_missing", "provider_duplicate", "provider_error", "account_missing", "account_duplicate",
    "account_alias", "merchant_alias", "account_foreign", "account_error", "account_manual", "invalid_other_account", "oversized_set"])
async def test_local_membership_failures_refuse_before_credential_or_provider(monkeypatch, change):
    db = DB(); rows = db.rows
    if change == "owner_missing": rows["users"] = []
    elif change == "owner_duplicate": rows["users"].append(deepcopy(rows["users"][0]))
    elif change == "owner_disabled": rows["users"][0]["disabled"] = True
    elif change == "owner_role": rows["users"][0]["role"] = "admin"
    elif change == "provider_missing": rows[INTEGRATIONS].pop()
    elif change == "provider_duplicate": rows[INTEGRATIONS].append(deepcopy(rows[INTEGRATIONS][0]))
    elif change == "provider_error": rows[INTEGRATIONS][0]["connection_status"] = "error"
    elif change == "account_missing": rows[ACCOUNTS].pop()
    elif change == "account_duplicate": rows[ACCOUNTS].append(deepcopy(rows[ACCOUNTS][0]))
    elif change == "account_alias": rows[ACCOUNTS][0]["ad_account_id"] = "987"
    elif change == "merchant_alias": rows[ACCOUNTS][1]["ad_account_id"] = MERCHANT
    elif change == "account_foreign": rows[ACCOUNTS][0]["user_id"] = "another-owner"
    elif change == "account_error": rows[ACCOUNTS][0]["connection_status"] = "error"
    elif change == "account_manual": rows[ACCOUNTS][0]["connection_provenance"] = "manual"
    elif change == "invalid_other_account": rows[ACCOUNTS].append({"provider": route.ADS, "user_id": OWNER})
    elif change == "oversized_set": rows[ACCOUNTS] += [dict(rows[ACCOUNTS][0], external_account_id=str(100000+i), ad_account_id=str(100000+i)) for i in range(400)]
    calls = providers(monkeypatch)
    assert (await call(db)).status_code in (403, 409, 422)
    assert calls["constructed"] == 0 and not [r for r in db.reads if r[0] == CREDS]


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner_disable", "account_add", "account_remove", "account_status", "provider_status", "missing_to_null", "credential_revoke"])
async def test_membership_and_credential_changes_during_read_discard_all_products(monkeypatch, change):
    db = DB()
    def effect(stage, count):
        if stage != "merchant": return
        if change == "owner_disable": db.rows["users"][0]["disabled"] = True
        elif change == "account_add": db.rows[ACCOUNTS].append(dict(db.rows[ACCOUNTS][0], _id="new", external_account_id="456", ad_account_id="456"))
        elif change == "account_remove": db.rows[ACCOUNTS].pop()
        elif change == "account_status": db.rows[ACCOUNTS][0]["connection_status"] = "error"
        elif change == "provider_status": db.rows[INTEGRATIONS][1]["connection_status"] = "not_connected"
        elif change == "missing_to_null": db.rows[ACCOUNTS][0]["login_customer_id"] = None
        elif change == "credential_revoke": db.rows[CREDS] = []
    providers(monkeypatch, effect=effect)
    response = await call(db)
    assert response.status_code in (403, 409) and "products" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["anonymous", "foreign_customer", "foreign_resource", "wrong_link_id", "wrong_type", "missing_merchant", "duplicate_link", "duplicate_merchant", "missing_selected", "overflow", "extra_union", "empty", "too_many"])
async def test_every_native_link_is_validated_without_dropping_bad_rows(monkeypatch, bad):
    links = [native_link()]
    if bad == "anonymous": links.append({})
    elif bad == "foreign_customer": links[0]["customer"]["id"] = "456"
    elif bad == "foreign_resource": links[0]["productLink"]["resourceName"] = "customers/456/productLinks/17"
    elif bad == "wrong_link_id": links[0]["productLink"]["productLinkId"] = "18"
    elif bad == "wrong_type": links[0]["productLink"]["type"] = "UNKNOWN"
    elif bad == "missing_merchant": links[0]["productLink"]["merchantCenter"] = {}
    elif bad == "duplicate_link": links.append(deepcopy(links[0]))
    elif bad == "duplicate_merchant": links.append(native_link(link_id="18"))
    elif bad == "missing_selected": links = [native_link(merchant="456")]
    elif bad == "overflow": links[0]["productLink"]["merchantCenter"]["merchantCenterId"] = "9223372036854775808"
    elif bad == "extra_union": links[0]["productLink"]["dataPartner"] = {"dataPartnerId": "2"}
    elif bad == "empty": links = []
    elif bad == "too_many": links *= 201
    calls = providers(monkeypatch, links=links)
    assert (await call(DB())).status_code == 409
    assert calls["merchant"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, None, 1, "true"])
async def test_incomplete_native_link_inventory_refuses_before_merchant(monkeypatch, complete):
    calls = providers(monkeypatch, complete=complete)
    assert (await call(DB())).status_code == 409 and calls["merchant"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["link_set", "currency", "timezone", "foreign_metadata"])
async def test_fresh_native_evidence_drift_refuses_after_merchant(monkeypatch, change):
    links = [native_link()]
    metadata = {"account_id": ACCOUNT, "customer_id": ACCOUNT, "currency_code": "SAR", "time_zone": "Asia/Riyadh", "identity_verified": True}
    def effect(stage, count):
        if stage != "merchant": return
        if change == "link_set": links.append(native_link(merchant="456", link_id="18"))
        elif change == "currency": metadata["currency_code"] = "USD"
        elif change == "timezone": metadata["time_zone"] = "UTC"
        else: metadata["customer_id"] = "456"
    providers(monkeypatch, links=links, metadata=metadata, effect=effect)
    assert (await call(DB())).status_code == 409


@pytest.mark.asyncio
async def test_link_order_changes_and_multiple_distinct_links_are_not_drift(monkeypatch):
    links = [native_link(), native_link(merchant="456", link_id="18")]
    def effect(stage, count):
        if stage == "merchant": links.reverse()
    providers(monkeypatch, links=links, effect=effect)
    assert (await call(DB())).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["db", "transaction", "provider", "invalid_product"])
async def test_safe_failures_have_no_partial_response_or_sentinel(monkeypatch, failure):
    db = DB(); db.fail = failure == "db"; db.tx_fail = failure == "transaction"
    def effect(stage, count):
        if failure == "provider": raise RuntimeError("PRIVATE_PROVIDER_SENTINEL")
    providers(monkeypatch, effect=effect, payload={"products": [product(), {}]} if failure == "invalid_product" else None)
    response = await call(db)
    assert response.status_code == 409 and "SENTINEL" not in response.text and "SKU1" not in response.text


@pytest.mark.asyncio
async def test_cancelled_provider_read_propagates_and_closes_bound_clients(monkeypatch):
    started = asyncio.Event()
    async def effect(stage, count):
        if stage == "merchant": started.set(); await asyncio.Event().wait()
    calls = providers(monkeypatch, effect=effect)
    task = asyncio.create_task(call(DB()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await asyncio.wait_for(task, 1)
    assert calls["close"] == 1


@pytest.mark.asyncio
async def test_route_is_get_only_and_extra_owner_input_cannot_change_scope(monkeypatch):
    calls = providers(monkeypatch)
    assert (await call(DB(), method="POST")).status_code == 405 and calls["constructed"] == 0
    response = await call(DB(), params={"account_id": ACCOUNT, "merchant_id": MERCHANT, "owner_id": "other", "verified": "true"})
    assert response.status_code == 200 and response.json()["scope"]["owner_id"] == OWNER


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value", [("disabled", 0), ("disabled", "false"), ("is_active", 1), ("is_active", "true")])
async def test_ambiguous_owner_state_is_not_boolean_coerced(monkeypatch, key, value):
    db = DB(); db.rows["users"][0][key] = value
    calls = providers(monkeypatch)
    assert (await call(db)).status_code == 403 and calls["constructed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["account_id", "merchant_id"])
async def test_explicit_unowned_selection_cannot_use_existing_default(monkeypatch, key):
    db = DB()
    for row in db.rows[ACCOUNTS]: row["mezan_selected"] = True
    calls = providers(monkeypatch)
    params = {"account_id": ACCOUNT, "merchant_id": MERCHANT, key: "999"}
    assert (await call(db, params=params)).status_code == 409 and calls["constructed"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [{}, {"account_id": ACCOUNT},
    {"account_id": ACCOUNT, "customer_id": "999", "currency_code": "SAR", "time_zone": "UTC", "identity_verified": True},
    {"account_id": ACCOUNT, "customer_id": ACCOUNT, "currency_code": "SAR", "time_zone": "UTC", "identity_verified": 1},
    {"account_id": ACCOUNT, "customer_id": ACCOUNT, "currency_code": "unknown", "time_zone": "UTC", "identity_verified": True}])
async def test_unproved_native_account_metadata_refuses_before_links_and_merchant(monkeypatch, metadata):
    calls = providers(monkeypatch, metadata=metadata)
    assert (await call(DB())).status_code == 409 and calls["links"] == calls["merchant"] == 0
