from __future__ import annotations

from typing import Any

import pytest

from integrations_control_center import snapchat_account_delivery_refresh as delivery
from integrations_control_center.snapchat_native_data_common import (
    SnapchatNativeSyncError,
)


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = list(rows)

    async def to_list(self, length: int):
        return list(self.rows[:length])

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCollection:
    def __init__(
        self,
        row: dict[str, Any] | None = None,
        rows: list[dict[str, Any]] | None = None,
    ):
        self.row = row or {}
        self.rows = rows or []
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update))
        self.row.update(update.get("$set") or {})

    async def find_one(self, query, projection=None):
        return dict(self.row)

    def find(self, query, projection=None):
        return FakeCursor(self.rows)


class FakeDB:
    def __init__(
        self,
        account_row: dict[str, Any] | None = None,
        entity_rows: list[dict[str, Any]] | None = None,
    ):
        self.accounts = FakeCollection(account_row)
        self.entities = FakeCollection(rows=entity_rows)

    def __getitem__(self, name: str):
        if name == "mezan_integration_accounts_v2":
            return self.accounts
        if name == "mezan_snapchat_entities_v2":
            return self.entities
        raise AssertionError(name)


class FakeContext:
    def __init__(self, payload: dict[str, Any]):
        self.db = FakeDB()
        self.user_id = "owner-1"
        self.payload = payload
        self.calls: list[str] = []

    def now_iso(self) -> str:
        return "2026-08-04T00:00:00+00:00"

    async def get_json(self, client, url, *, headers, params=None):
        self.calls.append(url)
        return self.payload


def test_normalizes_provider_delivery_shapes() -> None:
    assert delivery.normalize_delivery_status([
        "valid",
        {"code": "invalid_remaining_ad_account_budget"},
        "VALID",
    ]) == ["VALID", "INVALID_REMAINING_AD_ACCOUNT_BUDGET"]


def test_payment_budget_block_is_a_delivery_reason_not_campaign_pause() -> None:
    block = delivery.account_delivery_block(
        "ACTIVE",
        ["INVALID_REMAINING_AD_ACCOUNT_BUDGET"],
    )
    assert block is not None
    assert block["code"] == "ACCOUNT_PAYMENT_BLOCKED"
    assert block["delivery_label"].startswith("لا تسليم")
    assert "الدفع" in block["delivery_label"]


def test_campaign_daily_budget_keeps_active_switch_and_stops_delivery() -> None:
    result = delivery.campaign_delivery_state(
        "ACTIVE",
        ["INVALID_OVER_BUDGET_CAMPAIGN_DAILY_SPEND"],
        account_block=None,
        ad_squads=[],
    )
    assert result["state"] == "NOT_DELIVERING"
    assert result["code"] == "CAMPAIGN_DAILY_BUDGET_EXHAUSTED"
    assert "خارج الميزانية اليومية" in result["label"]


def test_active_campaign_with_all_ad_squads_paused_is_not_delivering() -> None:
    result = delivery.campaign_delivery_state(
        "ACTIVE",
        [],
        account_block=None,
        ad_squads=[
            {"status": "PAUSED", "delivery_status": ["INVALID_NOT_ACTIVE"]},
            {"status": "PAUSED", "delivery_status": ["INVALID_NOT_ACTIVE"]},
        ],
    )
    assert result["state"] == "NOT_DELIVERING"
    assert result["code"] == "NO_ACTIVE_AD_SQUAD"
    assert "لا توجد مجموعة إعلانية نشطة" in result["label"]


def test_active_campaign_with_valid_active_ad_squad_is_delivering() -> None:
    result = delivery.campaign_delivery_state(
        "ACTIVE",
        [],
        account_block=None,
        ad_squads=[
            {"status": "ACTIVE", "delivery_status": ["VALID"]},
        ],
    )
    assert result["state"] == "DELIVERING"
    assert result["deliverable"] is True


@pytest.mark.asyncio
async def test_refresh_reads_ad_account_and_persists_delivery() -> None:
    context = FakeContext({
        "adaccounts": [{
            "sub_request_status": "SUCCESS",
            "adaccount": {
                "id": "account-2",
                "name": "Snap 2",
                "status": "ACTIVE",
                "delivery_status": [
                    "INVALID_REMAINING_AD_ACCOUNT_BUDGET",
                ],
            },
        }],
    })
    account = {"ad_account_id": "account-2"}

    result = await delivery.refresh_snapchat_account_delivery(
        context,
        object(),
        "token",
        account,
    )

    assert context.calls == [
        "https://adsapi.snapchat.com/v1/adaccounts/account-2"
    ]
    assert result["blocked"]["code"] == "ACCOUNT_PAYMENT_BLOCKED"
    assert account["account_delivery_status"] == [
        "INVALID_REMAINING_AD_ACCOUNT_BUDGET"
    ]
    persisted = context.db.accounts.updates[0][1]["$set"]
    assert persisted["account_status"] == "ACTIVE"
    assert persisted["account_delivery_source_mode"] == (
        delivery.ACCOUNT_DELIVERY_SOURCE_MODE
    )


@pytest.mark.asyncio
async def test_report_keeps_active_status_and_marks_account_payment_no_delivery() -> None:
    db = FakeDB(account_row={
        "account_status": "ACTIVE",
        "account_delivery_status": [
            "INVALID_REMAINING_AD_ACCOUNT_BUDGET",
        ],
        "account_delivery_updated_at": "2026-08-04T00:00:00+00:00",
    })

    async def base_builder(db_value, user_id, *args, **kwargs):
        return {
            "selected_account_id": "account-2",
            "selected_account": {"account_id": "account-2"},
            "available_accounts": [{"account_id": "account-2"}],
            "accounts": [{"account_id": "account-2"}],
            "campaigns": [{
                "campaign_id": "campaign-1",
                "status": "ACTIVE",
                "delivery_status": ["VALID"],
            }],
            "source": {},
        }

    result = await delivery._build_report_with_effective_delivery(
        base_builder,
        db,
        "owner-1",
    )
    campaign = result["campaigns"][0]
    assert campaign["status"] == "ACTIVE"
    assert campaign["configured_status"] == "ACTIVE"
    assert campaign["effective_status"] == "ACTIVE"
    assert campaign["delivery_state"] == "NOT_DELIVERING"
    assert campaign["delivery_reason_code"] == "ACCOUNT_PAYMENT_BLOCKED"
    assert campaign["delivery_label"].startswith("لا تسليم")
    assert result["source"]["account_delivery_blocked"] is True


@pytest.mark.asyncio
async def test_report_uses_current_ad_squads_for_delivery_truth() -> None:
    db = FakeDB(
        account_row={
            "account_status": "ACTIVE",
            "account_delivery_status": ["VALID"],
            "account_delivery_updated_at": "2026-08-04T00:00:00+00:00",
        },
        entity_rows=[
            {
                "external_id": "squad-1",
                "campaign_id": "campaign-1",
                "status": "PAUSED",
                "delivery_status": ["INVALID_NOT_ACTIVE"],
                "last_observed_at": "2026-08-04T00:00:01+00:00",
            },
        ],
    )

    async def base_builder(db_value, user_id, *args, **kwargs):
        return {
            "selected_account_id": "account-2",
            "selected_account": {"account_id": "account-2"},
            "available_accounts": [{"account_id": "account-2"}],
            "accounts": [{"account_id": "account-2"}],
            "campaigns": [{
                "campaign_id": "campaign-1",
                "status": "ACTIVE",
                "delivery_status": [],
            }],
            "source": {},
        }

    result = await delivery._build_report_with_effective_delivery(
        base_builder,
        db,
        "owner-1",
    )
    campaign = result["campaigns"][0]
    assert campaign["status"] == "ACTIVE"
    assert campaign["delivery_state"] == "NOT_DELIVERING"
    assert campaign["delivery_reason_code"] == "NO_ACTIVE_AD_SQUAD"
    assert result["source"]["ad_squad_delivery_rows"] == 1


@pytest.mark.asyncio
async def test_performance_continues_when_delivery_read_temporarily_fails(monkeypatch) -> None:
    async def failed_delivery(*args, **kwargs):
        raise SnapchatNativeSyncError(
            "snapchat_provider_http_500",
            "temporary provider error",
            status_code=502,
            retryable=True,
        )

    base_called = False

    async def base_refresh(*args, **kwargs):
        nonlocal base_called
        base_called = True
        return {"rows_saved": 3, "errors": [], "errors_count": 0}

    monkeypatch.setattr(delivery, "refresh_snapchat_account_delivery", failed_delivery)
    result = await delivery._refresh_with_account_delivery(
        base_refresh,
        FakeContext({}),
        object(),
        "token",
        {"ad_account_id": "account-2"},
    )

    assert base_called is True
    assert result["rows_saved"] == 3
    assert result["errors_count"] == 1
    assert result["errors"][0]["kind"] == "account_delivery"
