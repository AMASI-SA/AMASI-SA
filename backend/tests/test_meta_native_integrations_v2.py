from __future__ import annotations

from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from integrations_control_center.catalog import (
    AD_MUTATION_CAPABILITIES,
    PROVIDER_BY_ID,
    build_capability_matrix,
)
from integrations_control_center.meta_catalog_native import install_meta_native_catalog
from integrations_control_center.meta_connections import handle_meta_callback
from integrations_control_center import meta_discovery
from integrations_control_center import meta_oauth_security as oauth
from integrations_control_center import meta_projection


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
            (
                self.name,
                "update_one",
                {"query": deepcopy(query), "update": deepcopy(update)},
            )
        )
        return FakeResult(modified)

    async def delete_many(self, query):
        self.db.rows[self.name] = [
            row for row in self.rows if not _matches(row, query)
        ]
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
        self.text = repr(payload)

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

    async def get(self, url, **kwargs):
        params = deepcopy(kwargs.get("params") or {})
        type(self).calls.append((url, params))
        if url.endswith("/debug_token"):
            return FakeResponse(
                {
                    "data": {
                        "is_valid": True,
                        "app_id": "meta-app-123",
                        "user_id": "meta-user-1",
                        "expires_at": 1999999999,
                        "scopes": [
                            "ads_read",
                            "ads_management",
                            "business_management",
                            "catalog_management",
                        ],
                    }
                }
            )
        if params.get("grant_type") == "fb_exchange_token":
            return FakeResponse(
                {
                    "access_token": "meta-long-secret",
                    "token_type": "bearer",
                    "expires_in": 5184000,
                }
            )
        return FakeResponse(
            {
                "access_token": "meta-short-secret",
                "token_type": "bearer",
                "expires_in": 3600,
            }
        )


class GraphClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        params = deepcopy(kwargs.get("params") or {})
        type(self).calls.append((url, params))
        if url.endswith("/me/businesses"):
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": "biz_1",
                            "name": "Amasi Business",
                            "verification_status": "verified",
                        }
                    ]
                }
            )
        if url.endswith("/me/adaccounts"):
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": "act_1",
                            "account_id": "1",
                            "name": "Amasi Meta Ads",
                            "currency": "SAR",
                            "timezone_name": "Asia/Riyadh",
                            "account_status": 1,
                            "business": {"id": "biz_1", "name": "Amasi Business"},
                            "amount_spent": "1000",
                            "balance": "250",
                            "spend_cap": "50000",
                            "funding_source_details": {"id": "never-store-this"},
                        }
                    ]
                }
            )
        if url.endswith("/me/accounts"):
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": "page_1",
                            "name": "Amasi Page",
                            "instagram_business_account": {
                                "id": "ig_1",
                                "username": "amasi.sa",
                            },
                        }
                    ]
                }
            )
        if url.endswith("/act_1/adspixels"):
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": "pixel_1",
                            "name": "Amasi Pixel",
                            "last_fired_time": "2026-07-29T20:00:00+0000",
                            "is_unavailable": False,
                        }
                    ]
                }
            )
        if url.endswith("/act_1/instagram_accounts"):
            return FakeResponse(
                {"data": [{"id": "ig_1", "username": "amasi.sa"}]}
            )
        if url.endswith("/biz_1/owned_product_catalogs"):
            return FakeResponse(
                {
                    "data": [
                        {
                            "id": "catalog_1",
                            "name": "Amasi Catalog",
                            "vertical": "commerce",
                            "product_count": 550,
                        }
                    ]
                }
            )
        if url.endswith("/me"):
            return FakeResponse(
                {
                    "id": "meta-user-1",
                    "name": "Amasi Admin",
                    "email": "support@amasi-sa.com",
                }
            )
        raise AssertionError(f"unexpected Meta Graph URL: {url}")


def _configure(monkeypatch):
    monkeypatch.setenv("META_BUSINESS_APP_ID", "meta-app-123")
    monkeypatch.setenv("META_BUSINESS_APP_SECRET", "meta-secret-456")
    monkeypatch.setenv("META_TOKEN_ENC_KEY", "unused-by-monkeypatch")
    monkeypatch.setenv("JWT_SECRET", "state-secret")
    monkeypatch.setenv("META_GRAPH_API_VERSION", "v25.0")
    monkeypatch.setenv(
        "META_BUSINESS_REDIRECT_URI",
        "https://mezansalla.com/api/integrations-v2/meta/callback",
    )
    monkeypatch.setenv("FRONTEND_URL", "https://mezansalla.com")
    monkeypatch.delenv("META_BUSINESS_SCOPES", raising=False)


def test_meta_catalog_is_native_and_mutations_remain_approval_gated():
    install_meta_native_catalog()
    definition = PROVIDER_BY_ID["meta_ads"]
    assert definition.legacy_sources == ()
    assert {"ads_read", "ads_management", "business_management"}.issubset(
        definition.required_permissions
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
    assert matrix["campaigns.read"]["state"] == "available"
    assert matrix["conversions.read"]["state"] == "available"


@pytest.mark.asyncio
async def test_meta_start_url_is_signed_one_time_and_requests_business_scopes(
    monkeypatch,
):
    _configure(monkeypatch)
    db = FakeDB()
    result = await oauth.start_meta_connection(db, "owner-1")
    parsed = urlsplit(result["authorization_url"])
    assert parsed.scheme == "https"
    assert parsed.hostname == "www.facebook.com"
    assert parsed.path == "/v25.0/dialog/oauth"
    query = parse_qs(parsed.query)
    assert query["client_id"] == ["meta-app-123"]
    assert query["redirect_uri"] == [
        "https://mezansalla.com/api/integrations-v2/meta/callback"
    ]
    assert "ads_management" in query["scope"][0]
    assert "business_management" in query["scope"][0]
    assert "pages_messaging" in query["scope"][0]
    state = query["state"][0]
    assert oauth._decode_state(state)["user_id"] == "owner-1"
    await oauth._consume_state(db, state)
    with pytest.raises(ValueError, match="expired_or_used"):
        await oauth._consume_state(db, state)


@pytest.mark.asyncio
async def test_meta_token_exchange_long_lived_validation_and_appsecret_proof(
    monkeypatch,
):
    _configure(monkeypatch)
    TokenClient.calls = []
    monkeypatch.setattr(oauth.httpx, "AsyncClient", TokenClient)
    short = await oauth._exchange_code("one-time-code")
    assert short["access_token"] == "meta-short-secret"
    long_lived = await oauth._exchange_long_lived_token(short["access_token"])
    assert long_lived["access_token"] == "meta-long-secret"
    debug = await oauth.debug_meta_token(long_lived["access_token"])
    assert debug["is_valid"] is True
    assert debug["app_id"] == "meta-app-123"
    assert oauth.meta_appsecret_proof("meta-long-secret") == oauth.hmac.new(
        b"meta-secret-456", b"meta-long-secret", oauth.hashlib.sha256
    ).hexdigest()


@pytest.mark.asyncio
async def test_meta_discovery_reads_accounts_pixels_catalogs_and_uses_proof(
    monkeypatch,
):
    _configure(monkeypatch)
    GraphClient.calls = []
    monkeypatch.setattr(meta_discovery.httpx, "AsyncClient", GraphClient)
    discovery = await meta_discovery.discover_meta_assets("meta-long-secret")
    assert discovery["identity"]["external_user_id"] == "meta-user-1"
    assert discovery["accounts"][0]["external_account_id"] == "act_1"
    assert discovery["accounts"][0]["funding_source_present"] is True
    assert discovery["pixels"][0]["pixel_id"] == "pixel_1"
    assert discovery["catalogs"][0]["catalog_id"] == "catalog_1"
    assert discovery["instagram_accounts"][0]["instagram_account_id"] == "ig_1"
    assert discovery["instagram_accounts"][0]["page_id"] == "page_1"
    assert discovery["instagram_accounts"][0]["discovery_source"] == "facebook_page"
    assert all(call[1].get("appsecret_proof") for call in GraphClient.calls)


@pytest.mark.asyncio
async def test_meta_projection_writes_only_v2_and_opaque_credentials(monkeypatch):
    _configure(monkeypatch)
    db = FakeDB()
    monkeypatch.setattr(
        meta_projection,
        "encrypt_meta_token",
        lambda value: b"opaque-meta-ciphertext" if value else None,
    )
    await meta_projection.persist_meta_projection(
        db,
        user_id="owner-1",
        token_payload={
            "access_token": "meta-long-secret",
            "expires_in": 5184000,
        },
        debug_data={
            "is_valid": True,
            "app_id": "meta-app-123",
            "user_id": "meta-user-1",
            "expires_at": 1999999999,
            "scopes": [
                "ads_read",
                "ads_management",
                "business_management",
                "catalog_management",
            ],
        },
        discovery={
            "identity": {"external_user_id": "meta-user-1"},
            "businesses": [
                {
                    "external_asset_id": "biz_1",
                    "business_id": "biz_1",
                    "display_name": "Amasi Business",
                }
            ],
            "accounts": [
                {
                    "external_account_id": "act_1",
                    "display_name": "Amasi Meta Ads",
                    "currency": "SAR",
                    "timezone": "Asia/Riyadh",
                    "business_id": "biz_1",
                    "funding_source_present": True,
                }
            ],
            "pixels": [
                {
                    "external_asset_id": "pixel_1",
                    "pixel_id": "pixel_1",
                    "display_name": "Amasi Pixel",
                    "ad_account_id": "act_1",
                }
            ],
            "catalogs": [
                {
                    "external_asset_id": "catalog_1",
                    "catalog_id": "catalog_1",
                    "display_name": "Amasi Catalog",
                    "business_id": "biz_1",
                }
            ],
            "instagram_accounts": [],
            "errors": [],
        },
    )
    write_collections = {name for name, _, _ in db.writes}
    assert write_collections <= {
        "mezan_meta_oauth_credentials_v2",
        "mezan_meta_assets_v2",
        "mezan_integrations_v2",
        "mezan_integration_permissions_v2",
        "mezan_integration_accounts_v2",
        "mezan_integration_health_v2",
        "mezan_integration_errors_v2",
        "mezan_integration_sync_runs_v2",
    }
    assert "meta_connections" not in write_collections
    assert "meta_ads_daily" not in write_collections
    rendered = repr(db.rows)
    assert "meta-long-secret" not in rendered
    assert "never-store-this" not in rendered
    assert "opaque-meta-ciphertext" in rendered
    assert db.rows["mezan_integrations_v2"][0]["asset_counts"]["pixels"] == 1
    assert db.rows["mezan_integration_accounts_v2"][0][
        "funding_source_present"
    ] is True


@pytest.mark.asyncio
async def test_meta_callback_rejects_browser_mismatch_before_network(monkeypatch):
    _configure(monkeypatch)
    response = await handle_meta_callback(
        FakeDB(),
        code="auth-code",
        state_token="state-token",
        provider_error=None,
        browser_binding="wrong-binding",
    )
    assert response.status_code == 302
    assert "meta=error" in response.headers["location"]
    assert "browser_binding_mismatch" in response.headers["location"]
