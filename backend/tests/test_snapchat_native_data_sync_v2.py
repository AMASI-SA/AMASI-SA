from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest

from integrations_control_center import snapchat_native_data_common as common
from integrations_control_center import snapchat_native_data_sync as native
from integrations_control_center.catalog import PROVIDER_BY_ID
from integrations_control_center.models import SnapchatAnalyticsSyncResponse
from integrations_control_center.snapchat_catalog_native import install_snapchat_native_catalog
from integrations_control_center.snapchat_connections import install_snapchat_connection_actions
from integrations_control_center.snapchat_native_data_routes import (
    attach_snapchat_native_data_routes,
    install_snapchat_native_data_actions,
)

NOW = datetime(2026, 7, 30, 0, 0, tzinfo=timezone.utc)


def _matches(row, query):
    for key, condition in query.items():
        value = row.get(key)
        if isinstance(condition, dict):
            for operator, expected in condition.items():
                if operator == "$gt" and not (value is not None and value > expected):
                    return False
                if operator == "$gte" and not (value is not None and value >= expected):
                    return False
                if operator == "$in" and value not in expected:
                    return False
        elif value != condition:
            return False
    return True


class FakeResult:
    matched_count = 1
    modified_count = 1


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(list(rows))

    def sort(self, key_or_list, direction=None):
        specs = key_or_list if isinstance(key_or_list, list) else [(key_or_list, direction)]
        for key, order in reversed(specs):
            self.rows.sort(key=lambda row: str(row.get(key) or ""), reverse=order < 0)
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows[:length])

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return deepcopy(next(self._iterator))
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCollection:
    def __init__(self, name, db):
        self.name, self.db = name, db

    @property
    def rows(self):
        return self.db.rows.setdefault(self.name, [])

    async def create_index(self, *args, **kwargs):
        self.db.indexes.append((self.name, deepcopy(args), deepcopy(kwargs)))
        return kwargs.get("name")

    async def find_one(self, query, projection=None, sort=None):
        rows = [row for row in self.rows if _matches(row, query)]
        if sort:
            rows = FakeCursor(rows).sort(sort).rows
        return deepcopy(rows[0]) if rows else None

    def find(self, query, projection=None):
        return FakeCursor(row for row in self.rows if _matches(row, query))

    async def count_documents(self, query):
        return sum(_matches(row, query) for row in self.rows)

    async def insert_one(self, document):
        safe = deepcopy(document)
        self.rows.append(safe)
        self.db.writes.append((self.name, "insert_one", safe))
        return object()

    async def update_one(self, query, update, upsert=False):
        target = next((row for row in self.rows if _matches(row, query)), None)
        inserted = False
        if target is None and upsert:
            target = {key: deepcopy(value) for key, value in query.items() if not isinstance(value, dict)}
            self.rows.append(target)
            inserted = True
        if target is not None:
            if inserted:
                target.update(deepcopy(update.get("$setOnInsert") or {}))
            target.update(deepcopy(update.get("$set") or {}))
        self.db.writes.append((self.name, "update_one", {"query": deepcopy(query), "update": deepcopy(update)}))
        return FakeResult()


class FakeDB:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or {})
        self.writes, self.indexes = [], []

    def __getitem__(self, name):
        return FakeCollection(name, self)

    def __getattr__(self, name):
        return FakeCollection(name, self)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload, self.status_code, self.text = payload, status_code, repr(payload)

    def json(self):
        return deepcopy(self.payload)


class ProviderClient:
    calls = []
    omit_purchase_for_second_campaign = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        type(self).calls.append((url, deepcopy(kwargs)))
        path = urlsplit(url).path
        if path.endswith("/campaigns"):
            return FakeResponse({"request_status": "SUCCESS", "campaigns": [{"campaign": {
                "id": "campaign-1", "name": "Amasi Campaign", "status": "ACTIVE",
                "daily_budget_micro": 100_000_000,
            }}]})
        if path.endswith("/adsquads"):
            return FakeResponse({"request_status": "SUCCESS", "adsquads": [{"adsquad": {
                "id": "squad-1", "campaign_id": "campaign-1", "name": "Amasi Squad",
                "status": "ACTIVE", "daily_budget_micro": 50_000_000,
            }}]})
        if path.endswith("/ads"):
            return FakeResponse({"request_status": "SUCCESS", "ads": [{"ad": {
                "id": "ad-1", "ad_squad_id": "squad-1", "creative_id": "creative-1",
                "name": "Amasi Ad", "status": "ACTIVE",
            }}]})
        if path.endswith("/creatives"):
            return FakeResponse({"request_status": "SUCCESS", "creatives": [{"creative": {
                "id": "creative-1", "name": "Amasi Creative", "type": "SNAP_AD",
            }}]})
        if path.endswith("/stats"):
            campaigns = [{
                "id": "campaign-1",
                "timeseries": [{
                    "start_time": "2026-07-29T00:00:00+03:00",
                    "end_time": "2026-07-30T00:00:00+03:00",
                    "stats": {
                        "impressions": 1000, "swipes": 50, "spend": 5_000_000,
                        "video_views": 700, "view_completion": 400,
                        "conversion_purchases": 2,
                        "conversion_purchases_value": 10_000_000,
                    },
                }],
            }]
            if type(self).omit_purchase_for_second_campaign:
                campaigns.append({
                    "id": "campaign-2",
                    "timeseries": [{
                        "start_time": "2026-07-29T00:00:00+03:00",
                        "end_time": "2026-07-30T00:00:00+03:00",
                        "stats": {
                            "impressions": 500, "swipes": 10, "spend": 1_000_000,
                            "video_views": 200, "view_completion": 100,
                        },
                    }],
                })
            return FakeResponse({
                "request_status": "SUCCESS",
                "timeseries_stats": [{"timeseries_stat": {
                    "breakdown_stats": {"campaign": campaigns}
                }}],
            })
        raise AssertionError(f"unexpected provider URL: {url}")


class UnauthorizedClient(ProviderClient):
    async def get(self, url, **kwargs):
        type(self).calls.append((url, deepcopy(kwargs)))
        return FakeResponse({"request_status": "FAILURE"}, status_code=401)

    async def post(self, url, **kwargs):
        type(self).calls.append((url, deepcopy(kwargs)))
        return FakeResponse({
            "access_token": "refreshed-access",
            "refresh_token": "refreshed-refresh",
            "expires_in": 3600,
        })


def _db():
    return FakeDB({
        "mezan_snapchat_oauth_credentials_v2": [{
            "user_id": "owner-1", "provider": "snapchat_ads",
            "access_token_ciphertext": b"access-ciphertext",
            "refresh_token_ciphertext": b"refresh-ciphertext",
            "access_token_expires_at": NOW + timedelta(hours=1),
            "scope": ["snapchat-marketing-api", "snapchat-offline-conversions-api"],
        }],
        "mezan_integration_accounts_v2": [{
            "user_id": "owner-1", "provider": "snapchat_ads",
            "mezan_integration_account_id": "snap-v2-account-1",
            "external_account_id": "account-1", "ad_account_id": "account-1",
            "display_name": "Amasi Snapchat", "currency": "SAR",
            "timezone": "Asia/Riyadh", "connection_status": "connected",
            "connection_provenance": "api_connection", "last_sync_at": None,
        }],
        "mezan_integrations_v2": [{
            "user_id": "owner-1", "provider": "snapchat_ads",
            "connection_status": "connected", "connection_provenance": "api_connection",
            "has_data": True,
        }],
    })


def _configure(monkeypatch):
    monkeypatch.setenv(common.SNAPCHAT_NATIVE_SYNC_ENABLED_ENV, "true")
    monkeypatch.setenv("SNAPCHAT_MARKETING_CLIENT_ID", "client-id")
    monkeypatch.setenv("SNAPCHAT_MARKETING_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("SNAPCHAT_MARKETING_REDIRECT_URI", "https://mezansalla.com/api/integrations-v2/snapchat/callback")
    monkeypatch.setenv("SNAPCHAT_TOKEN_ENC_KEY", "unused-in-test")
    monkeypatch.setenv("JWT_SECRET", "state-secret")
    monkeypatch.setattr(common, "decrypt_snapchat_token", lambda value: "access-secret" if value == b"access-ciphertext" else "refresh-secret")
    monkeypatch.setattr(common, "encrypt_snapchat_token", lambda value: b"opaque-ciphertext" if value else None)


@pytest.mark.asyncio
async def test_native_sync_reads_v2_and_writes_only_native_analytics(monkeypatch):
    _configure(monkeypatch)
    ProviderClient.calls = []
    ProviderClient.omit_purchase_for_second_campaign = False
    monkeypatch.setattr(native.httpx, "AsyncClient", ProviderClient)
    db = _db()
    result = await native.SnapchatNativeDataSync(db, now=lambda: NOW).run(
        "owner-1", native.SnapchatNativeSyncInput(from_date="2026-07-29", to_date="2026-07-29")
    )
    assert result["sync_status"] == "complete"
    assert result["rows_saved"] == 6
    assert result["provider_calls"] == 5
    assert result["provider_write_reached"] is False
    assert result["campaign_write_reached"] is False
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False
    write_collections = {name for name, _, _ in db.writes}
    assert write_collections <= {
        "mezan_snapchat_entities_v2",
        "mezan_snapchat_performance_daily_v2",
        "mezan_integration_accounts_v2",
    }
    assert not {
        "snapchat_connections", "snapchat_ad_accounts", "snapchat_account_daily",
        "snapchat_daily_stats", "daily_costs", "ad_account_ledger", "campaigns",
    } & write_collections
    facts = db.rows["mezan_snapchat_performance_daily_v2"]
    campaign = next(row for row in facts if row["entity_type"] == "campaign")
    account = next(row for row in facts if row["entity_type"] == "ad_account")
    assert campaign["spend_sar"] == 5
    assert campaign["purchase_value_sar"] == 10
    assert campaign["computed"]["roas"] == 2
    assert account["metrics"]["conversion_purchases"] == 2
    assert "access-secret" not in repr(db.rows)
    assert "refresh-secret" not in repr(db.rows)


@pytest.mark.asyncio
async def test_account_aggregate_keeps_missing_conversions_unknown(monkeypatch):
    _configure(monkeypatch)
    ProviderClient.omit_purchase_for_second_campaign = True
    monkeypatch.setattr(native.httpx, "AsyncClient", ProviderClient)
    db = _db()
    await native.SnapchatNativeDataSync(db, now=lambda: NOW).run(
        "owner-1",
        native.SnapchatNativeSyncInput(
            from_date="2026-07-29",
            to_date="2026-07-29",
        ),
    )
    account = next(
        row
        for row in db.rows["mezan_snapchat_performance_daily_v2"]
        if row["entity_type"] == "ad_account"
    )
    assert account["metrics"]["spend"] == 6_000_000
    assert account["metrics"]["conversion_purchases"] is None
    assert account["metrics"]["conversion_purchases_value"] is None
    assert account["computed"]["roas"] is None


@pytest.mark.asyncio
async def test_executor_audits_native_provenance_and_safe_contract(monkeypatch):
    _configure(monkeypatch)
    ProviderClient.omit_purchase_for_second_campaign = False
    monkeypatch.setattr(native.httpx, "AsyncClient", ProviderClient)
    db = _db()
    result = await native.execute_snapchat_native_sync(
        db, "owner-1",
        native.SnapchatNativeSyncInput(from_date="2026-07-29", to_date="2026-07-29"),
        now=lambda: NOW,
    )
    SnapchatAnalyticsSyncResponse.model_validate(result)
    assert result["status"] == "complete"
    assert result["source_only"] is True
    run = db.rows["mezan_integration_sync_runs_v2"][0]
    assert run["summary"]["legacy_collection_read"] is False
    assert run["summary"]["legacy_collection_write"] is False
    assert run["summary"]["campaign_write_reached"] is False
    integration = db.rows["mezan_integrations_v2"][0]
    assert integration["connection_provenance"] == "api_connection"
    assert integration["source_mode"] == common.SNAPCHAT_NATIVE_SYNC_SOURCE_MODE


@pytest.mark.asyncio
async def test_401_marks_reauth_and_writes_no_native_facts(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(native.httpx, "AsyncClient", UnauthorizedClient)
    db = _db()
    with pytest.raises(native.SnapchatNativeSyncError) as exc:
        await native.execute_snapchat_native_sync(
            db, "owner-1", native.SnapchatNativeSyncInput(days=1), now=lambda: NOW
        )
    assert exc.value.code == "snapchat_needs_reauth"
    assert db.rows.get("mezan_snapchat_entities_v2", []) == []
    assert db.rows.get("mezan_snapchat_performance_daily_v2", []) == []
    assert db.rows["mezan_integrations_v2"][0]["connection_status"] == "needs_reauth"


def test_native_action_requires_proven_api_connection(monkeypatch):
    _configure(monkeypatch)
    install_snapchat_native_catalog()
    install_snapchat_connection_actions()
    install_snapchat_native_data_actions()
    from integrations_control_center import service as service_module
    definition = PROVIDER_BY_ID["snapchat_ads"]
    enabled = service_module._actions(definition, {
        "connection_status": "connected", "connection_provenance": "api_connection",
        "accounts": [{"external_account_id": "account-1"}],
    })
    assert enabled["sync_data"]["enabled"] is True
    legacy = service_module._actions(definition, {
        "connection_status": "connected", "connection_provenance": "legacy_integration",
        "accounts": [{"external_account_id": "account-1"}],
    })
    assert legacy["sync_data"]["enabled"] is False


def test_native_route_has_exact_sync_contract(monkeypatch):
    _configure(monkeypatch)
    from fastapi import APIRouter, HTTPException
    router = APIRouter(prefix="/integrations-v2")
    async def current_user():
        return {"id": "employee-1", "role": "admin"}
    def require_owner(user):
        if user.get("role") != "owner":
            raise HTTPException(status_code=403, detail={"code": "owner_only"})
        return user
    attach_snapchat_native_data_routes(router, _db(), current_user, require_owner)
    route = next(row for row in router.routes if row.name == "sync_snapchat_native_data")
    assert route.path == "/integrations-v2/snapchat_ads/sync"
