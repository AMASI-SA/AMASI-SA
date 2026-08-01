"""Hermetic contract for the read-only Snapchat marketing workspace."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest

from ads_manager.snapchat_workspace import SnapchatMarketingWorkspaceService
from integrations_control_center.service import IntegrationsControlCenterService
from integrations_control_center.snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PERFORMANCE_COLLECTION,
)

OWNER_ID = "owner-a"
NOW = datetime(2026, 8, 1, 17, 0, tzinfo=timezone.utc)


def _path(document: dict, dotted: str) -> tuple[Any, bool]:
    value: Any = document
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None, False
        value = value[part]
    return value, True


def _matches(document: dict, query: dict) -> bool:
    for key, condition in query.items():
        value, exists = _path(document, key)
        if not isinstance(condition, dict):
            if not exists or value != condition:
                return False
            continue
        for operator, expected in condition.items():
            if operator == "$gte":
                if not exists or value < expected:
                    return False
            elif operator == "$lte":
                if not exists or value > expected:
                    return False
            else:
                raise AssertionError(f"Unsupported query operator: {operator}")
    return True


def _project(document: dict, projection: dict | None) -> dict:
    if not projection:
        return deepcopy(document)
    output = {}
    for key, enabled in projection.items():
        if not enabled or key == "_id":
            continue
        value, exists = _path(document, key)
        if exists:
            output[key] = deepcopy(value)
    return output


class FakeCursor:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def sort(self, specs, direction=None):
        fields = specs if isinstance(specs, list) else [(specs, direction)]
        for key, order in reversed(fields):
            self.rows.sort(
                key=lambda row: str(_path(row, key)[0] or ""),
                reverse=order < 0,
            )
        return self

    def limit(self, amount: int):
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length: int):
        return deepcopy(self.rows[:length])

    def __aiter__(self):
        self._iter = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return deepcopy(next(self._iter))
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
        self.db.reads.append(self.name)
        return FakeCursor([
            _project(row, projection)
            for row in self.rows
            if _matches(row, query)
        ])

    def _reject_write(self, operation: str):
        self.db.write_attempts.append((self.name, operation))
        raise AssertionError(f"Snapchat workspace attempted {operation} on {self.name}")

    async def insert_one(self, *args, **kwargs):
        self._reject_write("insert_one")

    async def update_one(self, *args, **kwargs):
        self._reject_write("update_one")

    async def delete_one(self, *args, **kwargs):
        self._reject_write("delete_one")

    async def bulk_write(self, *args, **kwargs):
        self._reject_write("bulk_write")


class FakeDB:
    def __init__(self, rows: dict[str, list[dict]]):
        self.rows = deepcopy(rows)
        self.reads: list[str] = []
        self.write_attempts: list[tuple[str, str]] = []

    def __getitem__(self, name: str):
        return FakeCollection(name, self)

    def __getattr__(self, name: str):
        return FakeCollection(name, self)


@pytest.fixture(autouse=True)
def local_integration_card(monkeypatch):
    async def overview(_service, _user_id):
        return {
            "providers": [
                {
                    "provider": "snapchat_ads",
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                    "last_sync_at": "2026-08-01T16:55:00+00:00",
                    "data_delay_minutes": 5,
                    "health": {"status": "healthy", "score": 100},
                    "accounts": [{"ad_account_id": "account-1"}],
                }
            ]
        }

    monkeypatch.setattr(IntegrationsControlCenterService, "overview", overview)


def _performance(
    campaign_id: str,
    *,
    spend_sar: float,
    sales_sar: float | None,
    orders: int,
    impressions: int,
    swipes: int,
) -> dict:
    return {
        "user_id": OWNER_ID,
        "provider": "snapchat_ads",
        "ad_account_id": "account-1",
        "entity_type": "campaign",
        "external_id": campaign_id,
        "campaign_id": campaign_id,
        "date": "2026-08-01",
        "currency": "SAR",
        "attribution_model": ATTRIBUTION_MODEL,
        "metrics": {
            "conversion_purchases": orders,
            "impressions": impressions,
            "swipes": swipes,
            "video_views": impressions // 2,
        },
        "spend_sar": spend_sar,
        "purchase_value_sar": sales_sar,
        "updated_at": "2026-08-01T16:55:00+00:00",
    }


def _entity(campaign_id: str, name: str, budget: int) -> dict:
    return {
        "user_id": OWNER_ID,
        "provider": "snapchat_ads",
        "ad_account_id": "account-1",
        "entity_type": "campaign",
        "external_id": campaign_id,
        "campaign_id": campaign_id,
        "display_name": name,
        "status": "ACTIVE",
        "delivery_status": "ACTIVE",
        "objective": "WEB_CONVERSIONS",
        "daily_budget_micro": budget * 1_000_000,
        "last_observed_at": "2026-08-01T16:55:00+00:00",
    }


def test_workspace_aggregates_campaign_spend_orders_sales_and_ratios():
    db = FakeDB({
        SNAPCHAT_PERFORMANCE_COLLECTION: [
            _performance(
                "campaign-1",
                spend_sar=100,
                sales_sar=400,
                orders=4,
                impressions=1000,
                swipes=50,
            ),
            _performance(
                "campaign-2",
                spend_sar=50,
                sales_sar=100,
                orders=1,
                impressions=500,
                swipes=20,
            ),
        ],
        SNAPCHAT_ENTITY_COLLECTION: [
            _entity("campaign-1", "حملة أغسطس الرئيسية", 700),
            _entity("campaign-2", "حملة إعادة الاستهداف", 300),
        ],
        "mezan_integration_accounts_v2": [
            {
                "user_id": OWNER_ID,
                "provider": "snapchat_ads",
                "external_account_id": "account-1",
                "ad_account_id": "account-1",
                "display_name": "أماسي الرياض",
                "currency": "SAR",
                "timezone": "Asia/Riyadh",
                "connection_status": "connected",
                "last_sync_at": "2026-08-01T16:55:00+00:00",
            }
        ],
    })

    result = asyncio.run(
        SnapchatMarketingWorkspaceService(db, now=lambda: NOW).overview(
            OWNER_ID,
            date_from="2026-08-01",
            date_to="2026-08-01",
        )
    )

    assert result["totals"]["spend_sar"] == 150
    assert result["totals"]["sales_sar"] == 500
    assert result["totals"]["orders"] == 5
    assert result["totals"]["roas"] == 3.33
    assert result["totals"]["cpa_sar"] == 30
    assert result["totals"]["ctr_pct"] == 4.67
    assert result["campaign_pagination"]["total"] == 2
    assert result["campaigns"][0]["campaign_name"] == "حملة أغسطس الرئيسية"
    assert result["campaigns"][0]["budget"]["daily_native"] == 700
    assert result["source"]["identity_coverage_pct"] == 100
    assert result["ai_readiness"]["ai_analysis_ready"] is True
    assert result["ai_readiness"]["campaign_creation_enabled"] is False
    assert result["ai_readiness"]["campaign_management_enabled"] is False
    assert result["policy"]["mutations_allowed"] is False
    assert result["policy"]["provider_network_called"] is False
    assert db.write_attempts == []


def test_workspace_hides_sales_ratios_when_purchase_value_is_missing():
    db = FakeDB({
        SNAPCHAT_PERFORMANCE_COLLECTION: [
            _performance(
                "campaign-1",
                spend_sar=100,
                sales_sar=None,
                orders=4,
                impressions=1000,
                swipes=50,
            )
        ],
        SNAPCHAT_ENTITY_COLLECTION: [
            _entity("campaign-1", "حملة بقيمة تحويل ناقصة", 700)
        ],
        "mezan_integration_accounts_v2": [
            {
                "user_id": OWNER_ID,
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "external_account_id": "account-1",
                "display_name": "أماسي الرياض",
                "currency": "SAR",
            }
        ],
    })

    result = asyncio.run(
        SnapchatMarketingWorkspaceService(db, now=lambda: NOW).overview(
            OWNER_ID,
            date_from="2026-08-01",
            date_to="2026-08-01",
        )
    )

    assert result["totals"]["spend_sar"] == 100
    assert result["totals"]["orders"] == 4
    assert result["totals"]["sales_sar"] is None
    assert result["totals"]["roas"] is None
    assert result["ai_readiness"]["sales_ready"] is False
    assert result["ai_readiness"]["ratios_ready"] is False
    assert db.write_attempts == []
