from __future__ import annotations

from typing import Any

import pytest

from integrations_control_center import snapchat_account_delivery_refresh as delivery
from integrations_control_center.snapchat_native_data_common import (
    SnapchatNativeSyncError,
)


class FakeCollection:
    def __init__(self, row: dict[str, Any] | None = None):
        self.row = row or {}
        self.updates: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update))
        self.row.update(update.get("$set") or {})

    async def find_one(self, query, projection=None):
        return dict(self.row)


class FakeDB:
    def __init__(self, row: dict[str, Any] | None = None):
        self.accounts = FakeCollection(row)

    def __getitem__(self, name: str):
        assert name == "mezan_integration_accounts_v2"
        return self.accounts


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


def test_payment_budget_block_has_explicit_effective_reason() -> None:
    block = delivery.account_delivery_block(
        "ACTIVE",
        ["INVALID_REMAINING_AD_ACCOUNT_BUDGET"],
    )
    assert block is not None
    assert block["code"] == "ACCOUNT_PAYMENT_BLOCKED"
    assert block["label"] == "متوقفة بسبب الدفع"
    assert "الدفع" in block["delivery_label"]


def test_active_valid_account_is_not_blocked() -> None:
    assert delivery.account_delivery_block("ACTIVE", ["VALID"]) is None
    assert delivery.account_delivery_block("ENABLED", ["DELIVERING"]) is None


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
async def test_report_preserves_configured_status_but_exposes_payment_block() -> None:
    db = FakeDB({
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
    assert campaign["effective_status"] == "ACCOUNT_PAYMENT_BLOCKED"
    assert campaign["effective_status_label"] == "متوقفة بسبب الدفع"
    assert result["source"]["account_delivery_blocked"] is True


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
