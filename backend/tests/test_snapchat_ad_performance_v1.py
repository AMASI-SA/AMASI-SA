from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from integrations_control_center import snapchat_ad_performance as module
from integrations_control_center.snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
)


def _matches(row, query):
    for key, condition in query.items():
        value = row.get(key)
        if isinstance(condition, dict):
            for operator, expected in condition.items():
                if operator == "$in" and value not in expected:
                    return False
                if operator == "$gte" and not (value is not None and value >= expected):
                    return False
                if operator == "$lte" and not (value is not None and value <= expected):
                    return False
        elif value != condition:
            return False
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(list(rows))

    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class FakeCollection:
    def __init__(self, rows, *, name, find_calls):
        self.rows = rows
        self.name = name
        self.find_calls = find_calls

    def find(self, query, projection=None):
        self.find_calls.append({
            "collection": self.name,
            "query": deepcopy(query),
            "projection": deepcopy(projection),
        })
        return FakeCursor(row for row in self.rows if _matches(row, query))


class FakeDB:
    def __init__(self, collections):
        self.collections = deepcopy(collections)
        self.find_calls = []

    def __getitem__(self, name):
        return FakeCollection(
            self.collections.setdefault(name, []),
            name=name,
            find_calls=self.find_calls,
        )

    def __getattr__(self, name):
        return self[name]


def test_extract_ad_hour_rows_preserves_campaign_identity():
    payload = {
        "timeseries_stats": [
            {
                "timeseries_stat": {
                    "breakdown_stats": {
                        "ad": [
                            {
                                "id": "ad-1",
                                "timeseries": [
                                    {
                                        "start_time": "2026-08-04T00:00:00+03:00",
                                        "end_time": "2026-08-04T01:00:00+03:00",
                                        "stats": {
                                            "spend": 5_000_000,
                                            "impressions": 1000,
                                            "swipes": 50,
                                            "conversion_purchases": 2,
                                            "conversion_purchases_value": 10_000_000,
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                }
            }
        ]
    }

    rows, errors, successful = module.extract_ad_hour_rows(
        payload,
        campaign_id="campaign-1",
    )

    assert successful == 1
    assert errors == []
    assert rows[0]["campaign_id"] == "campaign-1"
    assert rows[0]["ad_id"] == "ad-1"
    assert rows[0]["metrics"]["spend"] == 5_000_000


def test_ad_delivery_separates_switch_review_and_parent_blockers():
    paused = module._delivery_for_ad({"status": "PAUSED"}, None)
    assert paused["delivery_reason_code"] == "AD_CONFIGURED_PAUSED"

    rejected = module._delivery_for_ad(
        {"status": "ACTIVE", "review_status": "REJECTED"},
        None,
    )
    assert rejected["delivery_status"] == "لا تسليم — الإعلان مرفوض"

    inherited = module._delivery_for_ad(
        {"status": "ACTIVE", "review_status": "APPROVED"},
        {
            "delivery_state": "NOT_DELIVERING",
            "delivery_status": "لا تسليم — الحساب موقوف بسبب الدفع",
            "delivery_reason_code": "ACCOUNT_PAYMENT_BLOCKED",
        },
    )
    assert inherited["configured_status"] == "ACTIVE"
    assert inherited["delivery_inherited_from_ad_squad"] is True
    assert inherited["delivery_reason_code"] == "ACCOUNT_PAYMENT_BLOCKED"


@pytest.mark.asyncio
async def test_report_includes_zero_spend_ad_and_parent_names(monkeypatch):
    account = {
        "ad_account_id": "account-1",
        "display_name": "سناب الرياض",
        "currency": "SAR",
        "timezone": "Asia/Riyadh",
    }

    async def selected_accounts(db, user_id):
        return [deepcopy(account)]

    async def cost_settings(db, user_id):
        return {"items": []}

    monkeypatch.setattr(module, "_load_selected_accounts", selected_accounts)    import ads_manager.account_cost_settings as account_cost_settings

    monkeypatch.setattr(
        account_cost_settings,
        "list_account_cost_settings",
        cost_settings,
    )

    db = FakeDB({
        module.SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION: [],
        SNAPCHAT_ENTITY_COLLECTION: [
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "entity_type": "campaign",
                "external_id": "campaign-1",
                "display_name": "حملة المبيعات",
                "status": "ACTIVE",
            },
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "entity_type": "ad_squad",
                "external_id": "squad-1",
                "campaign_id": "campaign-1",
                "display_name": "مجموعة الرياض",
                "status": "ACTIVE",
            },
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "entity_type": "ad",
                "external_id": "ad-1",
                "campaign_id": "campaign-1",
                "ad_squad_id": "squad-1",
                "creative_id": "creative-1",
                "display_name": "فيديو المنتج الأول",
                "status": "ACTIVE",
                "review_status": "APPROVED",
            },
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "ad_account_id": "account-1",
                "entity_type": "creative",
                "external_id": "creative-1",
                "display_name": "إبداع المنتج",
                "provider_snapshot": {"type": "SNAP_AD"},
            },
        ],
    })

    report = await module.build_account_timezone_ad_report(
        db,
        "owner-1",
        account_id="account-1",
        from_date="2026-08-04",
        to_date="2026-08-04",
        query=None,
        page=1,
        limit=100,
        active_campaigns_only=True,
        now=lambda: datetime(2026, 8, 4, 12, tzinfo=timezone.utc),
    )

    entity_find = next(
        call for call in db.find_calls
        if call["collection"] == SNAPCHAT_ENTITY_COLLECTION
    )
    assert entity_find["projection"] == module.AD_REPORT_ENTITY_PROJECTION
    assert report["source"]["entity_projection_bounded"] is True
    assert report["source"]["parent_catalog_reused"] is True
    assert report["pagination"]["total"] == 1
    ad = report["ads"][0]
    assert ad["ad_name"] == "فيديو المنتج الأول"
    assert ad["ad_squad_name"] == "مجموعة الرياض"
    assert ad["campaign_name"] == "حملة المبيعات"
    assert ad["creative_name"] == "إبداع المنتج"
    assert ad["creative_type"] == "SNAP_AD"
    assert ad["status"] == "ACTIVE"
    assert ad["delivery_state"] == "DELIVERING"
    assert ad["spend_sar"] is None
    assert report["source"]["salla_results_supported"] is False
    assert report["policy"]["mutations_allowed"] is False
    assert report["provider_write_reached"] is False
