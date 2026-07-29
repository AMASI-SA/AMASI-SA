from __future__ import annotations

from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from integrations_control_center.catalog import (
    AD_MUTATION_CAPABILITIES,
    PROVIDER_BY_ID,
    build_capability_matrix,
)
from integrations_control_center.tiktok_connections import handle_tiktok_callback
from integrations_control_center import tiktok_discovery
from integrations_control_center import tiktok_oauth_security as oauth
from integrations_control_center import tiktok_projection


class FakeResult:
    def __init__(self, modified_count=1):
        self.modified_count = modified_count


def _matches(row, query):
    for key, value in query.items():
        if isinstance(value, dict) and "$gt" in value:
            if row.get(key) is None or not row.get(key) > value["$gt"]:
                return False
        elif row.get(key) != value:
            return False
    return True


class FakeCollection:
    def __init__(self, name: str, db: "FakeDB"):
        self.name = name
        self.db = db

    @property
    def rows(self):
        return self.db.rows.setdefault(self.name, [])

    async def create_index(self, *args, **kwargs):
        self.db.indexes.append((self.name, args, kwargs))
        return kwargs.get("name")

    async def insert_one(self, document):
        self.rows.append(deepcopy(document))
        self.db.writes.append((self.name, "insert_one", deepcopy(document)))
        return object()

    async def insert_many(self, documents):
        docs = deepcopy(list(documents))
        self.rows.extend(docs)
        self.db.writes.append((self.name, "insert_many", docs))
        return object()

    async def update_one(self, query, update, upsert=False):
        target = next((row for row in self.rows if _matches(row, query)), None)
        if target is None and upsert:
            target = {
                key: deepcopy(value)
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            target.update(deepcopy(update.get("$setOnInsert") or {}))
            self.rows.append(target)
        modified = 0
        if target is not None:
            target.update(deepcopy(update.get("$set") or {}))
            modified = 1
        self.db.writes.append(
            (self.name, "update_one", {"query": deepcopy(query), "update": deepcopy(update)})
        )
        return FakeResult(modified)

    async def delete_many(self, query):
        self.db.rows[self.name] = [row for row in self.rows if not _matches(row, query)]
        self.db.writes.append((self.name, "delete_many", deepcopy(query)))
        return object()

    async def find_one(self, query, projection=None):
        row = next((item for item in self.rows if _matches(item, query)), None)
        return deepcopy(row) if row else None

    async def count_documents(self, query):
        return sum(_matches(row, query) for row in self.rows)


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.writes = []
        self.indexes = []

    def __getitem__(self, name):
        return FakeCollection(name, self)

    def __getattr__(self, name):
        return FakeCollection(name, self)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return deepcopy(self.payload)


class TokenClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        type(self).calls.append((url, deepcopy(kwargs)))
        return FakeResponse(
            {
                "code": 0,
                "message": "OK",
                "data": {
                    "access_token": "tiktok-access-secret",
                    "advertiser_ids": ["700000000001"],
                    "scope": [1, 2, 3],
                },
            }
        )


class AdvertiserClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        type(self).calls.append((url, deepcopy(kwargs)))
        return FakeResponse(
            {
                "code": 0,
                "message": "OK",
                "data": {
                    "list": [
                        {
                            "advertiser_id": "700000000001",
                            "name": "Amasi TikTok",
                            "currency": "USD",
                            "timezone": "Asia/Riyadh",
                            "status": "STATUS_ENABLE",
                        }
                    ]
                },
            }
        )


def _configure(monkeypatch):
    monkeypatch.setenv("TIKTOK_MARKETING_APP_ID", "app-123")
    monkeypatch.setenv("TIKTOK_MARKETING_APP_SECRET", "secret-456")
    monkeypatch.setenv("TIKTOK_TOKEN_ENC_KEY", "unused-by-monkeypatch")
    monkeypatch.setenv("JWT_SECRET", "state-secret")
    monkeypatch.setenv(
        "TIKTOK_MARKETING_REDIRECT_URI",
        "https://mezansalla.com/api/integrations-v2/tiktok/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "https://mezansalla.com")
    monkeypatch.delenv("TIKTOK_MARKETING_SCOPE", raising=False)


def test_tiktok_catalog_is_native_and_mutations_remain_approval_gated():
    definition = PROVIDER_BY_ID["tiktok_ads"]
    assert definition.legacy_sources == ()
    assert definition.required_permissions == ("tiktok_marketing_api",)

    matrix = build_capability_matrix(
        definition,
        connection_status="connected",
        has_data=True,
        current_permissions=["tiktok_marketing_api"],
        permissions_observed=True,
        evidence_capabilities={
            "campaigns.read",
            "ads.read",
            "insights.read",
            "conversions.read",
        },
    )
    for capability in AD_MUTATION_CAPABILITIES:
        assert matrix[capability]["state"] == "approval_required"
        assert matrix[capability]["available"] is False
        assert matrix[capability]["blocked_by_policy"] is True
    assert matrix["campaigns.read"]["state"] == "available"
    assert matrix["insights.read"]["state"] == "available"


@pytest.mark.asyncio
async def test_tiktok_start_url_is_signed_one_time_and_requests_app_permissions(
    monkeypatch,
):
    _configure(monkeypatch)
    db = FakeDB()
    result = await oauth.start_tiktok_connection(db, "owner-1")
    parsed = urlsplit(result["authorization_url"])
    assert parsed.scheme == "https"
    assert parsed.hostname == "ads.tiktok.com"
    assert parsed.path == "/marketing_api/auth"
    query = parse_qs(parsed.query)
    assert query["app_id"] == ["app-123"]
    assert query["redirect_uri"] == [
        "https://mezansalla.com/api/integrations-v2/tiktok/callback"
    ]
    assert "scope" not in query
    state = query["state"][0]
    decoded = oauth._decode_state(state)
    assert decoded["user_id"] == "owner-1"
    await oauth._consume_state(db, state)
    with pytest.raises(ValueError, match="expired_or_used"):
        await oauth._consume_state(db, state)


@pytest.mark.asyncio
async def test_tiktok_token_exchange_and_advertiser_discovery_are_exact(
    monkeypatch,
):
    _configure(monkeypatch)
    TokenClient.calls = []
    AdvertiserClient.calls = []
    monkeypatch.setattr(oauth.httpx, "AsyncClient", TokenClient)
    token = await oauth._exchange_code("one-time-auth-code")
    assert token["advertiser_ids"] == ["700000000001"]
    token_url, token_kwargs = TokenClient.calls[0]
    assert token_url == oauth.TIKTOK_TOKEN_URL
    assert token_kwargs["json"] == {
        "app_id": "app-123",
        "secret": "secret-456",
        "auth_code": "one-time-auth-code",
    }

    monkeypatch.setattr(tiktok_discovery.httpx, "AsyncClient", AdvertiserClient)
    accounts = await tiktok_discovery.discover_tiktok_advertisers(
        token["access_token"], token["advertiser_ids"]
    )
    assert accounts == [
        {
            "external_account_id": "700000000001",
            "ad_account_id": "700000000001",
            "display_name": "Amasi TikTok",
            "currency": "USD",
            "timezone": "Asia/Riyadh",
            "account_status": "STATUS_ENABLE",
        }
    ]
    _, advertiser_kwargs = AdvertiserClient.calls[0]
    assert advertiser_kwargs["headers"]["Access-Token"] == "tiktok-access-secret"


@pytest.mark.asyncio
async def test_tiktok_projection_writes_only_v2_and_encrypted_credentials(
    monkeypatch,
):
    db = FakeDB()
    monkeypatch.setattr(
        tiktok_projection,
        "encrypt_tiktok_token",
        lambda value: b"encrypted-tiktok-token" if value else None,
    )
    await tiktok_projection.persist_tiktok_projection(
        db,
        user_id="owner-1",
        token_payload={
            "access_token": "tiktok-access-secret",
            "advertiser_ids": ["700000000001"],
            "scope": [1, 2],
        },
        advertisers=[
            {
                "external_account_id": "700000000001",
                "display_name": "Amasi TikTok",
                "currency": "USD",
                "timezone": "Asia/Riyadh",
            }
        ],
    )
    write_collections = {name for name, _, _ in db.writes}
    assert write_collections <= {
        "mezan_tiktok_oauth_credentials_v2",
        "mezan_integrations_v2",
        "mezan_integration_permissions_v2",
        "mezan_integration_accounts_v2",
        "mezan_integration_health_v2",
        "mezan_integration_errors_v2",
        "mezan_integration_sync_runs_v2",
    }
    assert "tiktok_connections" not in write_collections
    assert "tiktok_ads_daily" not in write_collections
    rendered = repr(db.rows)
    assert "tiktok-access-secret" not in rendered
    assert "encrypted-tiktok-token" in rendered
    integration = db.rows["mezan_integrations_v2"][0]
    assert integration["connection_provenance"] == "api_connection"
    assert integration["has_data"] is True
    run = db.rows["mezan_integration_sync_runs_v2"][0]
    assert run["summary"]["make_data_used"] is False
    assert run["summary"]["legacy_collection_read"] is False


@pytest.mark.asyncio
async def test_tiktok_callback_rejects_browser_mismatch_before_network(monkeypatch):
    _configure(monkeypatch)
    response = await handle_tiktok_callback(
        FakeDB(),
        auth_code="auth-code",
        state_token="state-token",
        provider_error=None,
        browser_binding="wrong-binding",
    )
    assert response.status_code == 302
    assert "tiktok=error" in response.headers["location"]
    assert "browser_binding_mismatch" in response.headers["location"]
