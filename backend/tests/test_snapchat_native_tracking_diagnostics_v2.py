from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from integrations_control_center import snapchat_native_data_common as common
from integrations_control_center import snapchat_native_tracking_diagnostics as tracking
from integrations_control_center.catalog import PROVIDER_BY_ID
from integrations_control_center.snapchat_catalog_native import install_snapchat_native_catalog
from integrations_control_center.snapchat_connections import install_snapchat_connection_actions
from integrations_control_center.snapchat_native_data_routes import install_snapchat_native_data_actions
from integrations_control_center.snapchat_native_tracking_models import (
    SnapchatTrackingDiagnosticsResponse,
)
from integrations_control_center.snapchat_native_tracking_routes import (
    attach_snapchat_native_tracking_routes,
    install_snapchat_native_tracking_actions,
)

NOW = datetime(2026, 7, 30, 14, 30, tzinfo=timezone.utc)


def _matches(row, query):
    for key, condition in query.items():
        value = row.get(key)
        if isinstance(condition, dict):
            for operator, expected in condition.items():
                if operator == "$in" and value not in expected:
                    return False
                if operator == "$gte" and not (value is not None and value >= expected):
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
            self.rows.sort(
                key=lambda row: str(row.get(key) or ""),
                reverse=(order or 1) < 0,
            )
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
        self.name = name
        self.db = db

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

    async def insert_one(self, document):
        safe = deepcopy(document)
        self.rows.append(safe)
        self.db.writes.append((self.name, "insert_one", safe))
        return object()

    async def update_one(self, query, update, upsert=False):
        target = next((row for row in self.rows if _matches(row, query)), None)
        inserted = False
        if target is None and upsert:
            target = {
                key: deepcopy(value)
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            self.rows.append(target)
            inserted = True
        if target is not None:
            if inserted:
                target.update(deepcopy(update.get("$setOnInsert") or {}))
            target.update(deepcopy(update.get("$set") or {}))
            for key, value in (update.get("$addToSet") or {}).items():
                values = target.setdefault(key, [])
                if value not in values:
                    values.append(deepcopy(value))
        self.db.writes.append(
            (
                self.name,
                "update_one",
                {"query": deepcopy(query), "update": deepcopy(update)},
            )
        )
        return FakeResult()


class FakeDB:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or {})
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


class ProviderClient:
    calls = []
    quality_status = 200
    pixel_status = 200

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        type(self).calls.append((url, deepcopy(kwargs)))
        path = urlsplit(url).path
        if path.endswith("/adaccounts/account-1/pixels"):
            if type(self).pixel_status != 200:
                return FakeResponse(
                    {"request_status": "FAILED"},
                    status_code=type(self).pixel_status,
                )
            return FakeResponse({
                "request_status": "SUCCESS",
                "pixels": [{
                    "sub_request_status": "SUCCESS",
                    "pixel": {
                        "id": "pixel-1",
                        "name": "Amasi Pixel",
                        "status": "ACTIVE",
                        "effective_status": "ACTIVE",
                        "ad_account_id": "account-1",
                        "pixel_javascript": "should-not-be-stored",
                    },
                }],
            })
        if path.endswith("/pixels/pixel-1/domains/stats"):
            return FakeResponse({
                "request_status": "SUCCESS",
                "timeseries_stats": [{
                    "timeseries_stat": {
                        "domains": [
                            {"domain_name": "amasi-sa.com", "total_events": 120},
                            {"domain_name": "www.amasi-sa.com", "total_events": 30},
                        ]
                    }
                }],
            })
        if path.endswith("/pixels/pixel-1/stats"):
            domain = kwargs.get("params", {}).get("domain")
            counts = (
                {"PAGE_VIEW": 80, "VIEW_CONTENT": 40, "ADD_CART": 12, "PURCHASE": 4}
                if domain == "amasi-sa.com"
                else {"PAGE_VIEW": 20, "PURCHASE": 1}
            )
            return FakeResponse({
                "request_status": "SUCCESS",
                "timeseries_stats": [{
                    "timeseries_stat": {
                        "timeseries": [{
                            "stats": {"event_type_breakdown": counts}
                        }]
                    }
                }],
            })
        if path.endswith("/pixels/pixel-1/event_quality_scores"):
            if type(self).quality_status != 200:
                return FakeResponse(
                    {"request_status": "FAILED"},
                    status_code=type(self).quality_status,
                )
            return FakeResponse({
                "request_status": "SUCCESS",
                "event_quality_scores": [
                    {
                        "sub_request_status": "SUCCESS",
                        "event_quality_score": {
                            "action_source": "WEB",
                            "event_source": "API",
                            "event_type": "PURCHASE",
                            "recommendations": [{
                                "title": "CAPI - Low identity coverage",
                                "description": "Improve hashed identity coverage.",
                                "recommendation_code": "PII_COVERAGE_CAPI",
                                "priority": "P1",
                                "score": "BAD",
                            }],
                        },
                    },
                    {
                        "sub_request_status": "SUCCESS",
                        "event_quality_score": {
                            "action_source": "WEB",
                            "event_source": "SDK",
                            "event_type": "PURCHASE",
                            "recommendations": [],
                        },
                    },
                ],
            })
        raise AssertionError(f"unexpected provider URL: {url}")


class UnauthorizedClient(ProviderClient):
    pixel_status = 401

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
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "access_token_ciphertext": b"access-ciphertext",
            "refresh_token_ciphertext": b"refresh-ciphertext",
            "access_token_expires_at": NOW + timedelta(hours=1),
            "scope": ["snapchat-marketing-api", "snapchat-offline-conversions-api"],
        }],
        "mezan_integration_accounts_v2": [{
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "mezan_integration_account_id": "snap-v2-account-1",
            "external_account_id": "account-1",
            "ad_account_id": "account-1",
            "display_name": "Amasi Snapchat",
            "organization_id": "org-1",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
        }],
        "mezan_integrations_v2": [{
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
        }],
    })


def _configure(monkeypatch):
    monkeypatch.setenv(common.SNAPCHAT_NATIVE_SYNC_ENABLED_ENV, "true")
    monkeypatch.setenv("SNAPCHAT_MARKETING_CLIENT_ID", "client-id")
    monkeypatch.setenv("SNAPCHAT_MARKETING_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "SNAPCHAT_MARKETING_REDIRECT_URI",
        "https://mezansalla.com/api/integrations-v2/snapchat/callback",
    )
    monkeypatch.setenv("SNAPCHAT_TOKEN_ENC_KEY", "unused-in-test")
    monkeypatch.setattr(
        common,
        "decrypt_snapchat_token",
        lambda value: (
            "access-secret"
            if value == b"access-ciphertext"
            else "refresh-secret"
        ),
    )
    monkeypatch.setattr(
        common,
        "encrypt_snapchat_token",
        lambda value: b"opaque-ciphertext" if value else None,
    )


@pytest.mark.asyncio
async def test_tracking_diagnostics_reads_pixel_domains_and_signal_quality(monkeypatch):
    _configure(monkeypatch)
    ProviderClient.calls = []
    ProviderClient.quality_status = 200
    ProviderClient.pixel_status = 200
    monkeypatch.setattr(tracking.httpx, "AsyncClient", ProviderClient)
    db = _db()

    result = await tracking.execute_snapchat_tracking_diagnostics(
        db,
        "owner-1",
        tracking.SnapchatTrackingDiagnosticsInput(days=7),
        now=lambda: NOW,
    )

    SnapchatTrackingDiagnosticsResponse.model_validate(result)
    assert result["status"] == "complete"
    assert result["accounts_complete"] == 1
    assert result["pixels_found"] == 1
    assert result["pixels_complete"] == 1
    assert result["domains_observed"] == 2
    assert result["diagnostics_saved"] == 4
    assert result["recommendations_count"] == 1
    assert result["errors_count"] == 0
    assert result["provider_write_reached"] is False
    assert result["event_write_reached"] is False
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False

    asset = db.rows[tracking.TRACKING_ASSET_COLLECTION][0]
    assert asset["pixel_id"] == "pixel-1"
    assert asset["ad_account_ids"] == ["account-1"]
    assert asset["total_events_7d"] == 150
    assert "pixel_javascript" not in repr(asset)
    diagnostics = db.rows[tracking.EVENT_DIAGNOSTIC_COLLECTION]
    domain = next(
        row
        for row in diagnostics
        if row["diagnostic_type"] == "domain_event_stats"
        and row["domain_name"] == "amasi-sa.com"
    )
    assert domain["event_counts"]["PURCHASE"] == 4
    quality = next(
        row
        for row in diagnostics
        if row["diagnostic_type"] == "signal_readiness"
        and row["event_source"] == "API"
    )
    assert quality["event_type"] == "PURCHASE"
    assert quality["recommendations"][0]["score"] == "BAD"
    assert "access-secret" not in repr(db.rows)
    assert "refresh-secret" not in repr(db.rows)

    write_collections = {name for name, _, _ in db.writes}
    assert not {
        "snapchat_connections",
        "snapchat_ad_accounts",
        "snapchat_account_daily",
        "snapchat_daily_stats",
        "daily_costs",
        "ad_account_ledger",
        "campaigns",
        "orders",
    } & write_collections


@pytest.mark.asyncio
async def test_shared_pixel_preserves_every_linked_ad_account():
    db = FakeDB()
    context = SimpleNamespace(
        db=db,
        user_id="owner-1",
        now_iso=lambda: NOW.isoformat(),
    )
    pixel = {
        "id": "shared-pixel",
        "name": "Self Service Pixel",
        "status": "ACTIVE",
        "effective_status": "ACTIVE",
    }

    await tracking._upsert_asset(
        context,
        account={"ad_account_id": "self-service"},
        pixel=pixel,
        domains=[],
        total_events=4,
        diagnostics_status="complete",
    )
    await tracking._upsert_asset(
        context,
        account={"ad_account_id": "saudi"},
        pixel=pixel,
        domains=[],
        total_events=4,
        diagnostics_status="complete",
    )

    rows = db.rows[tracking.TRACKING_ASSET_COLLECTION]
    assert len(rows) == 1
    assert rows[0]["ad_account_id"] == "saudi"
    assert rows[0]["ad_account_ids"] == ["self-service", "saudi"]


@pytest.mark.asyncio
async def test_optional_signal_readiness_403_is_partial_without_false_zero(monkeypatch):
    _configure(monkeypatch)
    ProviderClient.calls = []
    ProviderClient.quality_status = 403
    ProviderClient.pixel_status = 200
    monkeypatch.setattr(tracking.httpx, "AsyncClient", ProviderClient)
    db = _db()

    result = await tracking.execute_snapchat_tracking_diagnostics(
        db,
        "owner-1",
        tracking.SnapchatTrackingDiagnosticsInput(days=7),
        now=lambda: NOW,
    )

    assert result["status"] == "partial"
    assert result["pixels_found"] == 1
    assert result["pixels_complete"] == 0
    assert result["domains_observed"] == 2
    assert result["diagnostics_saved"] == 2
    assert result["recommendations_count"] == 0
    asset = db.rows[tracking.TRACKING_ASSET_COLLECTION][0]
    assert asset["diagnostics_status"] == "partial"
    assert asset["total_events_7d"] == 150
    assert db.rows["mezan_integrations_v2"][0]["connection_status"] == "connected"


@pytest.mark.asyncio
async def test_required_pixel_401_raises_reauth_and_writes_no_tracking_facts(monkeypatch):
    _configure(monkeypatch)
    UnauthorizedClient.calls = []
    monkeypatch.setattr(tracking.httpx, "AsyncClient", UnauthorizedClient)
    db = _db()

    with pytest.raises(tracking.SnapchatNativeSyncError) as exc:
        await tracking.execute_snapchat_tracking_diagnostics(
            db,
            "owner-1",
            tracking.SnapchatTrackingDiagnosticsInput(days=7),
            now=lambda: NOW,
        )

    assert exc.value.code == "snapchat_needs_reauth"
    assert db.rows.get(tracking.TRACKING_ASSET_COLLECTION, []) == []
    assert db.rows.get(tracking.EVENT_DIAGNOSTIC_COLLECTION, []) == []
    run = db.rows["mezan_integration_sync_runs_v2"][0]
    assert run["status"] == "failed"
    assert run["error"]["code"] == "snapchat_needs_reauth"


def test_tracking_action_and_owner_route_are_native_only(monkeypatch):
    _configure(monkeypatch)
    install_snapchat_native_catalog()
    install_snapchat_connection_actions()
    install_snapchat_native_data_actions()
    install_snapchat_native_tracking_actions()
    from integrations_control_center import service as service_module

    definition = PROVIDER_BY_ID["snapchat_ads"]
    enabled = service_module._actions(definition, {
        "connection_status": "connected",
        "connection_provenance": "api_connection",
        "accounts": [{"external_account_id": "account-1"}],
    })
    assert enabled["tracking_diagnostics"]["enabled"] is True
    legacy = service_module._actions(definition, {
        "connection_status": "connected",
        "connection_provenance": "legacy_integration",
        "accounts": [{"external_account_id": "account-1"}],
    })
    assert legacy["tracking_diagnostics"]["enabled"] is False

    from fastapi import APIRouter, HTTPException

    router = APIRouter(prefix="/integrations-v2")

    async def current_user():
        return {"id": "employee-1", "role": "admin"}

    def require_owner(user):
        if user.get("role") != "owner":
            raise HTTPException(status_code=403, detail={"code": "owner_only"})
        return user

    attach_snapchat_native_tracking_routes(
        router,
        _db(),
        current_user,
        require_owner,
    )
    route = next(
        row
        for row in router.routes
        if row.name == "diagnose_snapchat_native_tracking"
    )
    assert route.path == "/integrations-v2/snapchat_ads/tracking-diagnostics"
