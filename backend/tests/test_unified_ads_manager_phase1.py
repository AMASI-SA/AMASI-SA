"""Hermetic safety contract for the Phase 1 unified Ads Manager.

These tests intentionally avoid ``server.py``, a real MongoDB instance, stored
credentials, and every provider SDK.  The fake database raises immediately on
any mutation attempt so a successful GET proves that the feature is read-only.
"""
from __future__ import annotations

import asyncio
import json
import socket
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from ads_manager import service as ads_manager_service
from ads_manager.models import AdsManagerOverview
from ads_manager.routes import make_ads_manager_router
from ads_manager.service import AdsManagerService, MAX_PERFORMANCE_ROWS


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
OWNER_ID = "owner-a"
OTHER_OWNER_ID = "owner-b"
SECRET_SENTINEL = "ads-manager-must-not-return-this-secret"


def _path_value(document: dict, dotted_path: str) -> tuple[Any, bool]:
    value: Any = document
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None, False
        value = value[part]
    return value, True


def _set_path(document: dict, dotted_path: str, value: Any) -> None:
    target = document
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = deepcopy(value)


def _matches(document: dict, query: dict) -> bool:
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in condition):
                return False
            continue
        if key == "$and":
            if not all(_matches(document, branch) for branch in condition):
                return False
            continue

        value, exists = _path_value(document, key)
        if not isinstance(condition, dict):
            if not exists or value != condition:
                return False
            continue

        for operator, expected in condition.items():
            if operator == "$exists":
                if exists != bool(expected):
                    return False
            elif operator == "$ne":
                if exists and value == expected:
                    return False
            elif operator == "$in":
                if not exists or value not in expected:
                    return False
            elif operator == "$nin":
                if exists and value in expected:
                    return False
            elif operator == "$gte":
                if not exists or value < expected:
                    return False
            elif operator == "$lte":
                if not exists or value > expected:
                    return False
            else:  # A new query operator must be modelled explicitly.
                raise AssertionError(f"Unsupported fake-query operator: {operator}")
    return True


def _project(document: dict, projection: dict | None) -> dict:
    if not projection:
        return deepcopy(document)
    included = [
        key
        for key, enabled in projection.items()
        if enabled and key != "_id"
    ]
    if included:
        result: dict = {}
        for key in included:
            value, exists = _path_value(document, key)
            if exists:
                _set_path(result, key, value)
        return result

    excluded = {key for key, enabled in projection.items() if not enabled}
    return {
        key: deepcopy(value)
        for key, value in document.items()
        if key not in excluded
    }


class FakeCursor:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def sort(self, key_or_list, direction=None):
        specs = (
            key_or_list
            if isinstance(key_or_list, list)
            else [(key_or_list, direction)]
        )
        for field, order in reversed(specs):
            self.rows.sort(
                key=lambda row: (
                    (_path_value(row, field)[0] is not None),
                    str(_path_value(row, field)[0] or ""),
                ),
                reverse=order < 0,
            )
        return self

    def limit(self, value: int):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length: int):
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
    def __init__(self, name: str, db: "FakeDB"):
        self.name = name
        self.db = db

    @property
    def rows(self) -> list[dict]:
        return self.db.rows.setdefault(self.name, [])

    def find(self, query, projection=None):
        self.db.reads.append(
            {
                "collection": self.name,
                "query": deepcopy(query),
                "projection": deepcopy(projection),
            }
        )
        return FakeCursor(
            [
                _project(row, projection)
                for row in self.rows
                if _matches(row, query)
            ]
        )

    async def find_one(self, query, projection=None, sort=None):
        cursor = self.find(query, projection)
        if sort:
            cursor.sort(sort)
        rows = await cursor.to_list(1)
        return rows[0] if rows else None

    def _reject_write(self, operation: str) -> None:
        self.db.write_attempts.append((self.name, operation))
        raise AssertionError(
            f"Phase 1 Ads Manager attempted {operation} on {self.name}"
        )

    async def insert_one(self, *args, **kwargs):
        self._reject_write("insert_one")

    async def insert_many(self, *args, **kwargs):
        self._reject_write("insert_many")

    async def update_one(self, *args, **kwargs):
        self._reject_write("update_one")

    async def update_many(self, *args, **kwargs):
        self._reject_write("update_many")

    async def delete_one(self, *args, **kwargs):
        self._reject_write("delete_one")

    async def delete_many(self, *args, **kwargs):
        self._reject_write("delete_many")

    async def replace_one(self, *args, **kwargs):
        self._reject_write("replace_one")

    async def find_one_and_update(self, *args, **kwargs):
        self._reject_write("find_one_and_update")

    async def bulk_write(self, *args, **kwargs):
        self._reject_write("bulk_write")

    async def create_index(self, *args, **kwargs):
        self._reject_write("create_index")


class FakeDB:
    def __init__(self, rows: dict[str, list[dict]] | None = None):
        self.rows = deepcopy(rows or {})
        self.reads: list[dict] = []
        self.write_attempts: list[tuple[str, str]] = []

    def __getitem__(self, name: str):
        return FakeCollection(name, self)

    def __getattr__(self, name: str):
        return FakeCollection(name, self)


def _integration_card(provider: str) -> dict:
    return {
        "provider": provider,
        "connection_status": "connected",
        "connection_provenance": (
            "data_feed" if provider == "tiktok_ads" else "api_connection"
        ),
        "health": {"status": "healthy", "score": 100},
        "last_sync_at": "2026-07-28T10:00:00+00:00",
        "data_delay_minutes": 120,
        # The manager must select explicit fields rather than copying this.
        "access_token": SECRET_SENTINEL,
    }


@pytest.fixture(autouse=True)
def isolate_integration_overview(monkeypatch):
    async def local_overview(_service, _user_id):
        return {
            "providers": [
                _integration_card("snapchat_ads"),
                _integration_card("tiktok_ads"),
                _integration_card("meta_ads"),
            ]
        }

    monkeypatch.setattr(
        ads_manager_service.IntegrationsControlCenterService,
        "overview",
        local_overview,
    )


def _service(db: FakeDB) -> AdsManagerService:
    return AdsManagerService(db, now=lambda: NOW)


def _app(db: FakeDB, user: dict) -> FastAPI:
    async def current_user() -> dict:
        return deepcopy(user)

    app = FastAPI()
    app.include_router(
        make_ads_manager_router(db, current_user),
        prefix="/api",
    )
    return app


def _seeded_rows() -> dict[str, list[dict]]:
    return {
        "meta_ads_daily": [
            {
                "user_id": OWNER_ID,
                "date": "2026-07-10",
                "account_id": "act-owner",
                "campaign_id": "campaign-owner",
                "campaign_name": (
                    f"Owner campaign token={SECRET_SENTINEL}"
                ),
                "spend": 120,
                "currency": "SAR",
                "purchase_value": 180,
                "purchases": 3,
                "impressions": 1000,
                "clicks": 50,
                "access_token": SECRET_SENTINEL,
            },
            {
                "user_id": OTHER_OWNER_ID,
                "date": "2026-07-10",
                "account_id": "act-other",
                "campaign_id": "campaign-other",
                "campaign_name": "Other tenant campaign",
                "spend": 999,
                "currency": "SAR",
                "purchase_value": 999,
                "purchases": 99,
                "impressions": 9999,
                "clicks": 999,
            },
        ],
        "general_ledger": [
            {
                "user_id": OWNER_ID,
                "entity_type": "expense",
                "entity_id": "advertising",
                "side": "debit",
                "status": "posted",
                "entry_type": "normal",
                "amount": 100,
                "metadata": {
                    "spend_date": "2026-07-10",
                    "ad_provider": "meta",
                    "ad_account_id": "cp-owner",
                    "refresh_token": SECRET_SENTINEL,
                },
            },
            {
                "user_id": OTHER_OWNER_ID,
                "entity_type": "expense",
                "entity_id": "advertising",
                "side": "debit",
                "status": "posted",
                "entry_type": "normal",
                "amount": 888,
                "metadata": {
                    "spend_date": "2026-07-10",
                    "ad_provider": "meta",
                    "ad_account_id": "cp-other",
                },
            },
        ],
        "counterparties": [
            {
                "user_id": OWNER_ID,
                "id": "cp-owner",
                "kind": "ad_account",
                "ad_provider": "meta",
                "external_account_id": "act-owner",
                "client_secret": SECRET_SENTINEL,
            },
            {
                "user_id": OTHER_OWNER_ID,
                "id": "cp-other",
                "kind": "ad_account",
                "ad_provider": "meta",
                "external_account_id": "act-other",
            },
        ],
        "ads_accounts": [
            {
                "user_id": OWNER_ID,
                "provider": "meta",
                "external_account_id": "act-owner",
                "currency_native": "SAR",
                "fx_to_sar": {"rate": 1},
                "api_key": SECRET_SENTINEL,
            },
            {
                "user_id": OTHER_OWNER_ID,
                "provider": "meta",
                "external_account_id": "act-other",
                "currency_native": "SAR",
                "fx_to_sar": {"rate": 1},
            },
        ],
    }


def _assert_secret_free(value: Any) -> None:
    banned_fragments = (
        "token",
        "secret",
        "password",
        "credential",
        "authorization",
        "cookie",
        "api_key",
        "ciphertext",
        "private_key",
    )
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).lower()
            assert not any(
                fragment in normalized for fragment in banned_fragments
            ), f"secret-shaped key escaped: {key}"
            _assert_secret_free(child)
    elif isinstance(value, list):
        for child in value:
            _assert_secret_free(child)


def test_router_exposes_one_get_only_operation():
    async def current_user():
        return {"id": OWNER_ID, "role": "owner"}

    router = make_ads_manager_router(FakeDB(), current_user)
    routes = [route for route in router.routes if isinstance(route, APIRoute)]

    assert len(routes) == 1
    assert routes[0].path == "/ads-manager/overview"
    assert routes[0].methods == {"GET"}


@pytest.mark.asyncio
async def test_get_is_hermetic_and_attempts_zero_writes_or_provider_http(
    monkeypatch,
):
    def blocked_socket(*args, **kwargs):
        raise AssertionError("Provider/network socket attempted during GET")

    async def blocked_async_connection(*args, **kwargs):
        raise AssertionError("Provider/network connection attempted during GET")

    monkeypatch.setattr(socket, "create_connection", blocked_socket)
    monkeypatch.setattr(socket, "socket", blocked_socket)
    monkeypatch.setattr(asyncio, "open_connection", blocked_async_connection)

    db = FakeDB()
    app = _app(db, {"id": OWNER_ID, "role": "owner"})
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/ads-manager/overview",
            params={
                "date_from": "2026-07-01",
                "date_to": "2026-07-28",
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["policy"]["mode"] == "observe_only"
    assert response.json()["policy"]["mutations_allowed"] is False
    assert response.json()["policy"]["advertising_mutations_enabled"] is False
    assert db.reads
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_owner_gate_rejects_employee_before_any_data_access():
    db = FakeDB(_seeded_rows())
    app = _app(db, {"id": "employee-a", "role": "employee"})
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/ads-manager/overview")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "owner_only"
    assert db.reads == []
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_kill_switch_returns_not_found_before_any_data_access(monkeypatch):
    monkeypatch.setenv("MEZAN_ADS_MANAGER_READ_ONLY_ENABLED", "false")
    db = FakeDB(_seeded_rows())
    app = _app(db, {"id": OWNER_ID, "role": "owner"})
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/ads-manager/overview")

    assert response.status_code == 404
    assert db.reads == []
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_every_read_is_tenant_scoped_and_other_tenant_facts_never_leak():
    db = FakeDB(_seeded_rows())
    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-01",
        date_to="2026-07-28",
        provider="meta",
    )

    assert result["metrics"]["provider_reported_spend_sar"] == 120
    assert result["metrics"]["booked_ad_expense_sar"] == 100
    assert result["campaign_pagination"]["total"] == 1
    assert result["campaigns"][0]["campaign_id"] == "campaign-owner"
    assert OTHER_OWNER_ID not in json.dumps(result, ensure_ascii=False)
    assert "campaign-other" not in json.dumps(result, ensure_ascii=False)
    assert db.reads
    for read in db.reads:
        assert read["query"].get("user_id") == OWNER_ID, read
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_provider_fact_and_booked_accounting_fact_stay_distinct():
    db = FakeDB(_seeded_rows())
    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="meta",
    )

    provider = result["providers"][0]
    assert provider["metrics"]["provider_reported_spend_sar"] == 120
    assert provider["metrics"]["booked_ad_expense_sar"] == 100
    assert provider["reconciliation"] == {
        "status": "drift",
        "provider_reported_spend_sar": 120,
        "booked_ad_expense_sar": 100,
        "gap_sar": 20,
        "gap_pct": 16.67,
        "detail": (
            "صرف المنصة لا يطابق المصروف المحاسبي المُرحّل ضمن السماحية."
        ),
    }
    sources = {source["key"]: source for source in result["sources"]}
    assert sources["general_ledger"]["authoritative_for"] == [
        "booked_ad_expense_sar"
    ]
    assert "meta_provider_reported_spend" in sources["meta_ads_daily"][
        "authoritative_for"
    ]
    assert all(read["collection"] != "ad_account_ledger" for read in db.reads)


@pytest.mark.asyncio
async def test_missing_facts_remain_null_instead_of_becoming_zero():
    result = await _service(FakeDB()).overview(
        OWNER_ID,
        date_from="2026-07-28",
        date_to="2026-07-28",
    )

    assert all(value is None for value in result["metrics"].values())
    assert result["coverage"]["unscoped_booked_expense_sar"] is None
    assert result["daily_spend"] == [
        {
            "date": "2026-07-28",
            "snapchat": None,
            "tiktok": None,
            "meta": None,
            "booked_ad_expense_sar": None,
        }
    ]
    for provider in result["providers"]:
        assert provider["metrics"]["provider_reported_spend_sar"] is None
        assert provider["metrics"]["booked_ad_expense_sar"] is None
        assert provider["reconciliation"]["gap_sar"] is None


@pytest.mark.asyncio
async def test_partial_provider_row_does_not_invent_sar_or_zero_performance():
    db = FakeDB(
        {
            "meta_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "unknown-account",
                    "campaign_id": "partial-campaign",
                    "campaign_name": "Partial campaign",
                    "spend": 75,
                    # Currency, FX, revenue, conversions, impressions, and
                    # clicks are deliberately absent.
                }
            ]
        }
    )
    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="meta",
    )

    metrics = result["providers"][0]["metrics"]
    assert metrics["provider_reported_spend_sar"] is None
    assert metrics["platform_attributed_revenue_sar"] is None
    assert metrics["platform_reported_purchases"] is None
    assert metrics["platform_reported_impressions"] is None
    assert metrics["platform_reported_clicks"] is None
    assert metrics["platform_roas"] is None
    campaign = result["campaigns"][0]
    assert campaign["spend_sar_equivalent"] is None
    assert campaign["revenue_reported"] is None
    assert campaign["purchases"] is None
    assert campaign["impressions"] is None
    assert campaign["clicks"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("date_from", "date_to", "error_code"),
    [
        ("not-a-date", "2026-07-28", "invalid_date"),
        ("20260728", "2026-07-28", "invalid_date"),
        ("2026-W31-2", "2026-07-28", "invalid_date"),
        ("2026-07-28", "2026-07-27", "date_to_before_date_from"),
        ("2026-07-29", "2026-07-29", "future_date_not_allowed"),
        ("2026-04-29", "2026-07-28", "range_too_wide"),
    ],
)
async def test_date_range_is_bounded_before_database_access(
    date_from,
    date_to,
    error_code,
):
    db = FakeDB()
    with pytest.raises(ValueError, match=f"^{error_code}$"):
        await _service(db).overview(
            OWNER_ID,
            date_from=date_from,
            date_to=date_to,
        )
    assert db.reads == []
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_response_has_no_secret_keys_or_projected_secret_values():
    db = FakeDB(_seeded_rows())
    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-01",
        date_to="2026-07-28",
    )

    serialized = json.dumps(result, ensure_ascii=False)
    assert SECRET_SENTINEL not in serialized
    _assert_secret_free(result)


@pytest.mark.asyncio
async def test_campaign_pagination_is_bounded_and_deterministic():
    rows = _seeded_rows()
    rows["meta_ads_daily"] = [
        {
            "user_id": OWNER_ID,
            "date": "2026-07-10",
            "account_id": "act-owner",
            "campaign_id": f"campaign-{index:02d}",
            "campaign_name": f"Campaign {index:02d}",
            "spend": index,
            "currency": "SAR",
            "impressions": index * 100,
            "clicks": index,
        }
        for index in range(1, 13)
    ]
    db = FakeDB(rows)

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-01",
        date_to="2026-07-28",
        provider="meta",
        page=2,
        limit=10,
    )

    assert result["campaign_pagination"] == {
        "page": 2,
        "limit": 10,
        "total": 12,
        "pages": 2,
    }
    assert [row["campaign_id"] for row in result["campaigns"]] == [
        "campaign-02",
        "campaign-01",
    ]
    assert len(result["campaigns"]) == 2
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_http_pagination_limit_is_rejected_before_database_access():
    db = FakeDB(_seeded_rows())
    app = _app(db, {"id": OWNER_ID, "role": "owner"})
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/ads-manager/overview",
            params={"limit": 101},
        )

    assert response.status_code == 422
    assert db.reads == []
    assert db.write_attempts == []


@pytest.mark.asyncio
async def test_source_limit_is_detected_with_limit_plus_one_and_nulls_totals():
    db = FakeDB(
        {
            "meta_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "act-owner",
                    "campaign_id": "bounded-campaign",
                    "campaign_name": "Bounded campaign",
                    "spend": 1,
                    "currency": "SAR",
                    "purchase_value": 2,
                    "purchases": 1,
                    "impressions": 10,
                    "clicks": 1,
                }
                for _ in range(MAX_PERFORMANCE_ROWS + 1)
            ]
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="meta",
    )

    assert result["metrics"]["provider_reported_spend_sar"] is None
    assert result["metrics"]["platform_attributed_revenue_sar"] is None
    assert result["metrics"]["platform_roas"] is None
    assert result["providers"][0]["reconciliation"]["gap_sar"] is None
    assert result["coverage"]["provider_spend_is_partial"] is True
    assert "meta_ads_daily" in result["coverage"]["source_row_limit_reached"]


@pytest.mark.asyncio
async def test_global_fx_is_used_only_when_account_explicitly_inherits_it():
    db = FakeDB(
        {
            "meta_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "act-owner",
                    "campaign_id": "fx-campaign",
                    "campaign_name": "FX campaign",
                    "spend": 10,
                }
            ],
            "ads_accounts": [
                {
                    "user_id": OWNER_ID,
                    "provider": "meta",
                    "external_account_id": "act-owner",
                    "currency_native": "USD",
                    "fx_to_sar": {
                        "mode": "inherit_from_global",
                        "rate": None,
                    },
                }
            ],
            "ads_currency_settings": [
                {
                    "user_id": OWNER_ID,
                    "usd_to_sar_rate": 3.75,
                }
            ],
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="meta",
    )

    assert result["metrics"]["provider_reported_spend_sar"] == 37.5
    assert result["campaigns"][0]["spend_sar_equivalent"] == 37.5


@pytest.mark.asyncio
async def test_invalid_persisted_global_fx_does_not_fall_back_or_invent_value():
    db = FakeDB(
        {
            "meta_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "act-owner",
                    "campaign_id": "invalid-fx-campaign",
                    "campaign_name": "Invalid FX campaign",
                    "spend": 10,
                }
            ],
            "ads_accounts": [
                {
                    "user_id": OWNER_ID,
                    "provider": "meta",
                    "external_account_id": "act-owner",
                    "currency_native": "USD",
                    "fx_to_sar": {
                        "mode": "inherit_from_global",
                        "rate": 9.99,
                    },
                }
            ],
            "ads_currency_settings": [
                {
                    "user_id": OWNER_ID,
                    "usd_to_sar_rate": "invalid",
                }
            ],
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="meta",
    )

    assert result["metrics"]["provider_reported_spend_sar"] is None
    assert result["campaigns"][0]["spend_sar_equivalent"] is None


@pytest.mark.asyncio
async def test_approved_global_fx_default_supports_legacy_usd_account():
    db = FakeDB(
        {
            "meta_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "legacy-account",
                    "campaign_id": "legacy-fx-campaign",
                    "campaign_name": "Legacy FX campaign",
                    "spend": 10,
                }
            ],
            "counterparties": [
                {
                    "user_id": OWNER_ID,
                    "id": "cp-owner",
                    "kind": "ad_account",
                    "ad_provider": "meta",
                    "external_id": "act_legacy-account",
                    "currency": "USD",
                }
            ],
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="meta",
    )

    assert result["metrics"]["provider_reported_spend_sar"] == 37.54
    assert result["campaigns"][0]["currency_evidence"].endswith(
        "approved_default"
    )


@pytest.mark.asyncio
async def test_campaign_spend_share_uses_sar_equivalents_across_currencies():
    db = FakeDB(
        {
            "meta_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "sar-account",
                    "campaign_id": "sar-campaign",
                    "campaign_name": "SAR campaign",
                    "spend": 100,
                },
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "usd-account",
                    "campaign_id": "usd-campaign",
                    "campaign_name": "USD campaign",
                    "spend": 100,
                },
            ],
            "ads_accounts": [
                {
                    "user_id": OWNER_ID,
                    "provider": "meta",
                    "external_account_id": "sar-account",
                    "currency_native": "SAR",
                    "fx_to_sar": {"mode": "manual", "rate": 1},
                },
                {
                    "user_id": OWNER_ID,
                    "provider": "meta",
                    "external_account_id": "usd-account",
                    "currency_native": "USD",
                    "fx_to_sar": {"mode": "manual", "rate": 3.75},
                },
            ],
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="meta",
    )
    campaigns = {
        row["campaign_id"]: row for row in result["campaigns"]
    }

    assert campaigns["sar-campaign"]["spend_sar_equivalent"] == 100
    assert campaigns["usd-campaign"]["spend_sar_equivalent"] == 375
    assert campaigns["sar-campaign"]["spend_share_pct"] == 21.05
    assert campaigns["usd-campaign"]["spend_share_pct"] == 78.95


@pytest.mark.asyncio
async def test_campaign_rename_keeps_one_identity_and_latest_name():
    db = FakeDB(
        {
            "meta_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-09",
                    "account_id": "act-owner",
                    "campaign_id": "renamed-campaign",
                    "campaign_name": "Old name",
                    "spend": 10,
                    "currency": "SAR",
                    "purchase_value": 20,
                    "purchases": 1,
                    "impressions": 100,
                    "clicks": 10,
                },
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "act-owner",
                    "campaign_id": "renamed-campaign",
                    "campaign_name": "New name",
                    "spend": 15,
                    "currency": "SAR",
                    "purchase_value": 30,
                    "purchases": 2,
                    "impressions": 150,
                    "clicks": 15,
                },
            ]
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-09",
        date_to="2026-07-10",
        provider="meta",
    )

    assert result["campaign_pagination"]["total"] == 1
    assert result["campaigns"][0]["campaign_name"] == "New name"
    assert result["campaigns"][0]["spend_sar_equivalent"] == 25


@pytest.mark.asyncio
async def test_malformed_numeric_fact_remains_unknown_not_zero():
    db = FakeDB(
        {
            "meta_ads_daily": [
                {
                    "user_id": OWNER_ID,
                    "date": "2026-07-10",
                    "account_id": "act-owner",
                    "campaign_id": "bad-number",
                    "campaign_name": "Bad number",
                    "spend": "not-a-number",
                    "currency": "SAR",
                    "purchase_value": 10,
                    "purchases": 1,
                    "impressions": 100,
                    "clicks": 10,
                }
            ]
        }
    )

    result = await _service(db).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
        provider="meta",
    )

    assert result["metrics"]["provider_reported_spend_sar"] is None
    assert result["metrics"]["platform_roas"] is None
    assert result["campaigns"][0]["spend_reported"] is None
    assert result["campaigns"][0]["spend_sar_equivalent"] is None


@pytest.mark.asyncio
async def test_combined_ratios_require_complete_aligned_provider_coverage():
    rows = _seeded_rows()
    rows["snapchat_account_daily"] = [
        {
            "user_id": OWNER_ID,
            "date": "2026-07-10",
            "ad_account_id": "snap-owner",
            "spend_sar": 80,
        }
    ]
    result = await _service(FakeDB(rows)).overview(
        OWNER_ID,
        date_from="2026-07-10",
        date_to="2026-07-10",
    )

    assert result["metrics"]["provider_reported_spend_sar"] == 200
    assert result["metrics"]["platform_attributed_revenue_sar"] == 180
    assert result["metrics"]["platform_roas"] is None
    assert result["metrics"]["platform_cpa_sar"] is None
    assert result["metrics"]["platform_cpc_sar"] is None
    assert result["metrics"]["platform_cpm_sar"] is None
    assert result["metrics"]["platform_ctr_pct"] is None
    meta = next(row for row in result["providers"] if row["provider"] == "meta")
    assert meta["metrics"]["platform_roas"] == 1.5


@pytest.mark.asyncio
async def test_invalid_source_dates_and_boolean_ledger_amounts_fail_closed():
    rows = _seeded_rows()
    rows["meta_ads_daily"].append(
        {
            "user_id": OWNER_ID,
            "date": "2026-07-11-extra",
            "account_id": "act-owner",
            "campaign_id": "invalid-date-campaign",
            "campaign_name": "Invalid date campaign",
            "spend": 500,
            "currency": "SAR",
            "purchase_value": 900,
            "purchases": 10,
            "impressions": 5000,
            "clicks": 500,
        }
    )
    rows["general_ledger"].append(
        {
            "user_id": OWNER_ID,
            "entity_type": "expense",
            "entity_id": "advertising",
            "side": "debit",
            "status": "posted",
            "entry_type": "normal",
            "amount": True,
            "metadata": {
                "spend_date": "2026-07-11",
                "ad_provider": "meta",
                "ad_account_id": "cp-owner",
            },
        }
    )
    result = await _service(FakeDB(rows)).overview(
        OWNER_ID,
        date_from="2026-07-01",
        date_to="2026-07-28",
        provider="meta",
    )

    assert result["metrics"]["provider_reported_spend_sar"] is None
    assert result["metrics"]["booked_ad_expense_sar"] is None
    assert result["campaign_pagination"]["total"] == 1
    assert result["campaigns"][0]["campaign_id"] == "campaign-owner"
    assert all(point["meta"] is None for point in result["daily_spend"])
    warnings = " ".join(result["coverage"]["source_warnings"])
    assert "meta_ads_daily" in warnings
    assert "غير صالحة" in warnings


@pytest.mark.asyncio
async def test_response_contract_validates_after_full_seeded_projection():
    result = await _service(FakeDB(_seeded_rows())).overview(
        OWNER_ID,
        date_from="2026-07-01",
        date_to="2026-07-28",
    )

    validated = AdsManagerOverview.model_validate(result)
    assert validated.policy.mutations_allowed is False
    assert validated.policy.advertising_mutations_enabled is False
