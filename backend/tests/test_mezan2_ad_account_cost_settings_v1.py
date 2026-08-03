from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from ads_manager import account_cost_settings as cost_settings


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(list(rows))

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


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            for operator, value in expected.items():
                if operator == "$in" and actual not in value:
                    return False
                if operator == "$nin" and actual in value:
                    return False
        elif actual != expected:
            return False
    return True


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

    def find(self, query, projection=None):
        return FakeCursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query, projection=None):
        row = next((row for row in self.rows if _matches(row, query)), None)
        return deepcopy(row) if row else None

    async def update_one(self, query, update, upsert=False):
        row = next((row for row in self.rows if _matches(row, query)), None)
        if row is None and upsert:
            row = {
                key: deepcopy(value)
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            self.rows.append(row)
        if row is not None:
            for key, value in (update.get("$setOnInsert") or {}).items():
                row.setdefault(key, deepcopy(value))
            row.update(deepcopy(update.get("$set") or {}))
        self.db.writes.append((self.name, deepcopy(query), deepcopy(update)))
        return object()


class FakeDB:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or {})
        self.writes = []
        self.indexes = []

    def __getitem__(self, name):
        return FakeCollection(name, self)

    def __getattr__(self, name):
        return self[name]


def _account(provider, account_id, *, currency="USD", name=None):
    return {
        "user_id": "owner-1",
        "provider": provider,
        "mezan_integration_account_id": f"mezan-{provider}-{account_id}",
        "external_account_id": account_id,
        "ad_account_id": account_id,
        "display_name": name or account_id,
        "currency": currency,
        "timezone": "Asia/Riyadh",
        "connection_status": "connected",
        "connection_provenance": "api_connection",
        "mezan_selected": True,
    }


def _db():
    return FakeDB({
        "mezan_integration_accounts_v2": [
            _account("snapchat_ads", "snap-usd", name="Snap USD"),
            _account("meta_ads", "meta-sar", currency="SAR", name="Meta SAR"),
            _account("google_ads", "google-usd", name="Google USD"),
            {
                **_account("tiktok_ads", "legacy-provenance"),
                "connection_provenance": "data_feed",
            },
        ],
        # Must never be read by the Mezan 2 page.
        "counterparties": [
            {
                "id": "legacy-account",
                "user_id": "owner-1",
                "kind": "ad_account",
                "name": "Legacy Ads Account",
            }
        ],
        "ads_currency_settings": [
            {
                "user_id": "owner-1",
                "usd_to_sar_rate": 9.99,
                "bank_commission_pct": 19.0,
            }
        ],
    })


@pytest.mark.asyncio
async def test_list_uses_only_mezan2_accounts_and_provider_defaults():
    db = _db()
    result = await cost_settings.list_account_cost_settings(db, "owner-1")

    assert [item["display_name"] for item in result["items"]] == [
        "Snap USD",
        "Meta SAR",
        "Google USD",
    ]
    assert all(item["display_name"] != "Legacy Ads Account" for item in result["items"])

    snap = next(item for item in result["items"] if item["provider"] == "snapchat_ads")
    meta = next(item for item in result["items"] if item["provider"] == "meta_ads")
    assert snap["native_currency"] == "USD"
    assert snap["exchange_rate_to_sar"] == 3.7544
    assert snap["bank_commission_pct"] == 2.3
    assert snap["apply_bank_commission"] is True
    assert meta["native_currency"] == "SAR"
    assert meta["exchange_rate_to_sar"] == 1.0
    assert meta["bank_commission_pct"] == 0.0
    assert meta["apply_bank_commission"] is False

    assert result["policy"] == {
        "source": "mezan_integration_accounts_v2",
        "settings_collection": "mezan_ad_account_cost_settings_v2",
        "legacy_counterparties_read": False,
        "legacy_ads_currency_settings_read": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }
    assert db.writes == []


@pytest.mark.asyncio
async def test_save_persists_independent_settings_in_v2_collection_only():
    db = _db()
    account_id = "mezan-snapchat_ads-snap-usd"
    saved = await cost_settings.save_account_cost_settings(
        db,
        "owner-1",
        account_id,
        cost_settings.AccountCostSettingsInput(
            native_currency="USD",
            exchange_rate_to_sar=3.81,
            bank_commission_pct=2.3,
            apply_bank_commission=True,
        ),
    )

    assert saved["exchange_rate_to_sar"] == 3.81
    assert saved["bank_commission_pct"] == 2.3
    assert saved["configured"] is True
    assert {write[0] for write in db.writes} == {
        "mezan_ad_account_cost_settings_v2"
    }
    assert db.rows["counterparties"][0]["name"] == "Legacy Ads Account"
    assert db.rows["ads_currency_settings"][0]["usd_to_sar_rate"] == 9.99

    listed = await cost_settings.list_account_cost_settings(db, "owner-1")
    snap = next(item for item in listed["items"] if item["provider"] == "snapchat_ads")
    assert snap["exchange_rate_to_sar"] == 3.81
    assert snap["configured"] is True


def test_sar_account_rate_is_fixed_to_one():
    with pytest.raises(ValidationError):
        cost_settings.AccountCostSettingsInput(
            native_currency="SAR",
            exchange_rate_to_sar=3.75,
            bank_commission_pct=0,
            apply_bank_commission=False,
        )
