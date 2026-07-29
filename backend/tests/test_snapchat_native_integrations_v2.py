from __future__ import annotations

from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from integrations_control_center.catalog import (
    AD_MUTATION_CAPABILITIES,
    PROVIDER_BY_ID,
    build_capability_matrix,
)
from integrations_control_center.snapchat_catalog_native import (
    install_snapchat_native_catalog,
)
from integrations_control_center.snapchat_connections import (
    handle_snapchat_callback,
)
from integrations_control_center import snapchat_discovery
from integrations_control_center import snapchat_oauth_security as oauth
from integrations_control_center import snapchat_projection


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
    def __init__(self, payload, status_code=200, text=""):
        self.payload = payload
        self.status_code = status_code
        self.text = text

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
                "access_token": "snap-access-secret",
                "refresh_token": "snap-refresh-secret",
                "expires_in": 3600,
                "scope": (
                    "snapchat-marketing-api "
                    "snapchat-offline-conversions-api"
                ),
            }
        )


class DiscoveryClient:
    calls = []

    def __init__(self, *args, **kwargs):
        self.headers = kwargs.get("headers") or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        type(self).calls.append((url, deepcopy(kwargs), deepcopy(self.headers)))
        if url.endswith("/me"):
            return FakeResponse(
                {
                    "me": {
                        "id": "snap-user-1",
                        "display_name": "Amasi Snapchat",
                        "email": "support@amasi-sa.com",
                    }
                }
            )
        if url.endswith("/me/organizations"):
            return FakeResponse(
                {
                    "organizations": [
                        {
                            "organization": {
                                "id": "org-1",
                                "name": "Amasi Organization",
                                "type": "BRAND",
                                "ad_accounts": [
                                    {
                                        "adaccount": {
                                            "id": "acc-1",
                                            "name": "Amasi Riyadh",
                                            "currency": "USD",
                                            "timezone": "Asia/Riyadh",
                                            "status": "ACTIVE",
                                        }
                                    }
                                ],
                            }
                        }
                    ]
                }
            )
        raise AssertionError(f"unexpected discovery URL: {url}")


def _configure(monkeypatch):
    monkeypatch.setenv("SNAPCHAT_MARKETING_CLIENT_ID", "snap-client-123")
    monkeypatch.setenv("SNAPCHAT_MARKETING_CLIENT_SECRET", "snap-secret-456")
    monkeypatch.setenv("SNAPCHAT_TOKEN_ENC_KEY", "unused-by-monkeypatch")
    monkeypatch.setenv("JWT_SECRET", "state-secret")
    monkeypatch.setenv(
        "SNAPCHAT_MARKETING_REDIRECT_URI",
        "https://mezansalla.com/api/integrations-v2/snapchat/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "https://mezansalla.com")
    monkeypatch.delenv("SNAPCHAT_MARKETING_SCOPES", raising=False)


def test_snapchat_catalog_is_native_and_mutations_remain_approval_gated():
    install_snapchat_native_catalog()
    definition = PROVIDER_BY_ID["snapchat_ads"]
    assert definition.legacy_sources == ()
    assert definition.required_permissions == (
        "snapchat-marketing-api",
        "snapchat-offline-conversions-api",
    )

    matrix = build_capability_matrix(
        definition,
        connection_status="connected",
        has_data=True,
        current_permissions=list(definition.required_permissions),
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
    assert matrix["conversions.read"]["state"] == "available"


@pytest.mark.asyncio
async def test_snapchat_start_url_is_signed_one_time_and_requests_full_scopes(
    monkeypatch,
):
    _configure(monkeypatch)
    db = FakeDB()
    result = await oauth.start_snapchat_connection(db, "owner-1")
    parsed = urlsplit(result["authorization_url"])
    assert parsed.scheme == "https"
    assert parsed.hostname == "accounts.snapchat.com"
    assert parsed.path == "/login/oauth2/authorize"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["snap-client-123"]
    assert query["redirect_uri"] == [
        "https://mezansalla.com/api/integrations-v2/snapchat/callback"
    ]
    assert query["scope"] == [
        "snapchat-marketing-api snapchat-offline-conversions-api"
    ]
    state = query["state"][0]
    decoded = oauth._decode_state(state)
    assert decoded["user_id"] == "owner-1"
    await oauth._consume_state(db, state)
    with pytest.raises(ValueError, match="expired_or_used"):
        await oauth._consume_state(db, state)


@pytest.mark.asyncio
async def test_snapchat_token_exchange_and_account_discovery_are_exact(monkeypatch):
    _configure(monkeypatch)
    TokenClient.calls = []
    DiscoveryClient.calls = []
    monkeypatch.setattr(oauth.httpx, "AsyncClient", TokenClient)
    token = await oauth._exchange_code("one-time-code")
    assert token["refresh_token"] == "snap-refresh-secret"
    token_url, token_kwargs = TokenClient.calls[0]
    assert token_url == oauth.SNAPCHAT_TOKEN_URL
    assert token_kwargs["data"] == {
        "code": "one-time-code",
        "client_id": "snap-client-123",
        "client_secret": "snap-secret-456",
        "grant_type": "authorization_code",
        "redirect_uri": "https://mezansalla.com/api/integrations-v2/snapchat/callback",
    }

    monkeypatch.setattr(snapchat_discovery.httpx, "AsyncClient", DiscoveryClient)
    discovered = await snapchat_discovery.discover_snapchat_accounts(
        token["access_token"]
    )
    assert discovered["identity"]["external_user_id"] == "snap-user-1"
    assert discovered["accounts"] == [
        {
            "external_account_id": "acc-1",
            "ad_account_id": "acc-1",
            "display_name": "Amasi Riyadh",
            "currency": "USD",
            "timezone": "Asia/Riyadh",
            "account_status": "ACTIVE",
            "organization_id": "org-1",
            "organization_name": "Amasi Organization",
        }
    ]
    assert DiscoveryClient.calls[0][2]["Authorization"] == "Bearer snap-access-secret"


@pytest.mark.asyncio
async def test_snapchat_projection_writes_only_v2_and_encrypted_credentials(
    monkeypatch,
):
    db = FakeDB()
    monkeypatch.setattr(
        snapchat_projection,
        "encrypt_snapchat_token",
        lambda value: f"encrypted:{value}".encode() if value else None,
    )
    await snapchat_projection.persist_snapchat_projection(
        db,
        user_id="owner-1",
        token_payload={
            "access_token": "snap-access-secret",
            "refresh_token": "snap-refresh-secret",
            "expires_in": 3600,
            "scope": (
                "snapchat-marketing-api "
                "snapchat-offline-conversions-api"
            ),
        },
        discovery={
            "identity": {"external_user_id": "snap-user-1"},
            "organizations": [
                {
                    "organization_id": "org-1",
                    "organization_name": "Amasi Organization",
                }
            ],
            "accounts": [
                {
                    "external_account_id": "acc-1",
                    "display_name": "Amasi Riyadh",
                    "currency": "USD",
                    "timezone": "Asia/Riyadh",
                    "organization_id": "org-1",
                    "organization_name": "Amasi Organization",
                }
            ],
        },
    )
    write_collections = {name for name, _, _ in db.writes}
    assert write_collections <= {
        "mezan_snapchat_oauth_credentials_v2",
        "mezan_integrations_v2",
        "mezan_integration_permissions_v2",
        "mezan_integration_accounts_v2",
        "mezan_integration_health_v2",
        "mezan_integration_errors_v2",
        "mezan_integration_sync_runs_v2",
    }
    assert "snapchat_connections" not in write_collections
    assert "snapchat_ad_accounts" not in write_collections
    assert "snapchat_account_daily" not in write_collections
    rendered = repr(db.rows)
    assert "snap-access-secret" not in rendered
    assert "snap-refresh-secret" not in rendered
    assert "encrypted:snap-access-secret" in rendered
    assert "encrypted:snap-refresh-secret" in rendered
    integration = db.rows["mezan_integrations_v2"][0]
    assert integration["connection_provenance"] == "api_connection"
    assert integration["has_data"] is True
    run = db.rows["mezan_integration_sync_runs_v2"][0]
    assert run["summary"]["legacy_collection_read"] is False
    assert run["summary"]["provider_write_reached"] is False


@pytest.mark.asyncio
async def test_snapchat_callback_rejects_browser_mismatch_before_network(monkeypatch):
    _configure(monkeypatch)
    response = await handle_snapchat_callback(
        FakeDB(),
        code="auth-code",
        state_token="state-token",
        provider_error=None,
        browser_binding="wrong-binding",
    )
    assert response.status_code == 302
    assert "snapchat=error" in response.headers["location"]
    assert "browser_binding_mismatch" in response.headers["location"]
