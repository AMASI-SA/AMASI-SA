"""Native-schema and label-only acceptance. Fixtures only, no live writes."""
import base64
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from order_engine import campaign_hover as hover
from order_engine import campaign_hover_native_sources as native

CID = "12345678901"
DAY = "2026-11-13"
STAMP = "2026-11-13T12:05:00+00:00"
NOW = datetime(2026, 11, 13, 12, 30, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def nested(row, path):
    value = row
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(row, item) for item in expected):
                return False
            continue
        if key == "$and":
            if not all(matches(row, item) for item in expected):
                return False
            continue
        value = nested(row, key)
        if not isinstance(expected, dict):
            if value != expected:
                return False
            continue
        for op, rhs in expected.items():
            assert op in {"$in", "$ne", "$gte", "$lte", "$lt", "$gt", "$type", "$exists"}, op
            if op == "$in" and value not in rhs:
                return False
            if op == "$ne" and value == rhs:
                return False
            if op == "$gte" and (value is None or value < rhs):
                return False
            if op == "$lte" and (value is None or value > rhs):
                return False
            if op == "$lt" and (value is None or value >= rhs):
                return False
            if op == "$gt" and (value is None or value <= rhs):
                return False
            if op == "$type" and rhs == "object" and not isinstance(value, dict):
                return False
            if op == "$exists" and (value is not None) != rhs:
                return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
    def max_time_ms(self, value):
        assert value <= 1500
        return self
    def limit(self, value):
        self.rows = self.rows[:value]
        return self
    async def to_list(self, value):
        return self.rows[:value]


class Collection:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.queries = []
        self.error = None
    def find(self, query, projection):
        self.queries.append(deepcopy(query))
        if self.error:
            raise self.error
        return Cursor([row for row in self.rows if matches(row, query)])


class DB:
    def __init__(self, mapping):
        self.collections = {name: Collection(rows) for name, rows in mapping.items()}
    def __getitem__(self, key):
        return self.collections.setdefault(key, Collection([]))


def order(cid, number="order-1", at="2026-11-13T06:00:00+00:00", user="owner"):
    return {"user_id": user, "order_number": number, "order_date": at, "order_status": "processing",
            "raw_by_source": {"salla_direct": {
                "date": {"date": at}, "utm_source": "snapchat", "utm_campaign": cid,
                "amounts": {"total": {"amount": 375, "currency": "SAR"}},
                "items": [{"id": "p", "type": "product", "quantity": 3},
                          {"id": "p2", "type": "product", "quantity": 1},
                          {"id": "s", "type": "service", "quantity": 1}],
            }}}


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(hover, "datetime", Clock)
    hover._CACHE.clear()
    hover._PENDING.clear()


class ArrayCollection(Collection):
    def aggregate(self, pipeline, **kwargs):
        assert kwargs == {"maxTimeMS": 1500, "allowDiskUse": False}
        query, limit, project = (pipeline[0]["$match"], pipeline[1]["$limit"], pipeline[2]["$project"])
        self.queries.append(deepcopy(query))
        assert query["user_id"] == "owner"
        if self.error:
            raise self.error
        expression, count = project["selected"]["$slice"]
        condition = expression["$filter"]
        array = condition["input"]["$ifNull"][0][1:]
        id_path, allowed = condition["cond"]["$in"]
        field = id_path.removeprefix("$$item.")
        output = []
        for row in [r for r in self.rows if matches(r, query)][:limit]:
            item = {key: deepcopy(row.get(key)) for key, value in project.items() if value == 1}
            item["selected"] = [deepcopy(r) for r in row.get(array, []) if nested(r, field) in allowed][:count]
            output.append(item)
        return Cursor(output)


class NativeDB(DB):
    def __init__(self, mapping):
        self.collections = {name: ArrayCollection(rows) for name, rows in mapping.items()}
    def __getitem__(self, key):
        return self.collections.setdefault(key, ArrayCollection([]))


def fixture(provider="tiktok", currency="SAR", zone="Asia/Riyadh"):
    row = order(CID, at="2026-11-13T10:00:00+00:00")
    row["raw_by_source"]["salla_direct"]["utm_source"] = provider
    return NativeDB({
        "mezan_integration_accounts_v2": [{"user_id": "owner", "provider": provider + "_ads",
            "ad_account_id": "account-1", "currency": currency, "timezone": zone}],
        "unified_orders": [row],
    })


def tiktok(db, amount=75):
    base = {"user_id": "owner", "ad_account_id": "account-1", "entity_type": "campaign",
            "complete": True, "source_mode": "tiktok_native_hierarchy_v2", "observed_at": STAMP}
    db["mezan_tiktok_entities_v2"].rows = [{**base, "entities": [{"entity_id": CID, "entity_name": "Fixture winter"}]}]
    db["mezan_tiktok_entity_daily_v2"].rows = [{**base, "date": DAY, "rows": [{"entity_id": CID, "spend_native": amount}]}]


def google(db, amount=75):
    db["mezan_google_ads_campaign_factsets_v1"].rows = [{
        "user_id": "owner", "account_id": "account-1", "complete": True,
        "source": "google_ads_api", "date_from": DAY, "date_to": DAY, "observed_at": STAMP,
        "campaigns": [{"campaign_id": CID, "account_id": "account-1", "source": "google_ads_api",
            "name": "Fixture Google", "currency_code": "SAR", "time_zone": "Asia/Riyadh",
            "metrics": {"spend_native": amount}}],
    }]


def snap(db):
    db["mezan_snapchat_performance_account_day_v3"].rows = [{
        "user_id": "owner", "ad_account_id": "account-1", "entity_type": "campaign",
        "external_id": CID, "date": DAY, "source_mode": native.SNAP_SOURCE,
        "action_report_time": "conversion", "stored_granularity": "ACCOUNT_LOCAL_DAY",
        "date_timezone": "America/Los_Angeles", "account_timezone": "America/Los_Angeles",
        "spend_native": 20, "spend_sar": 75, "currency": "USD", "updated_at": STAMP,
        "provider_window_start": "2026-11-13T08:00:00+00:00",
        "provider_window_end": "2026-11-13T12:00:00+00:00",
    }]


async def read(db, cid=CID):
    return (await hover.read_campaign_hover(db, "owner", [cid]))["campaigns"][cid]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,seed", [("tiktok", tiktok), ("google", google)])
async def test_real_native_schema_joins_ids_to_salla_and_spend(provider, seed):
    db = fixture(provider); seed(db)
    row = await read(db)
    assert (row["orders"], row["pieces"], row["sales_sar"]) == (1, 4, 375)
    assert row["spend_sar"] == row["cpa_sar"] == 75 and row["roas"] == 5
    assert row["campaign_name"].startswith("Fixture")
    assert row["status"] == "available"


@pytest.mark.asyncio
async def test_scheduled_snap_account_day_v3_is_read_without_a_new_sync():
    db = fixture("snapchat", "USD", "America/Los_Angeles"); snap(db)
    row = await read(db)
    assert row["orders"] == 1 and row["spend_sar"] == row["cpa_sar"] == 75
    assert row["timezone"] == "America/Los_Angeles"
    assert row["period_start"] == "2026-11-13T08:00:00+00:00"
    assert row["spend_warning"] == "snapchat_hour_coverage_unverified"
    assert row["roas"] == 5
    assert row["updated_at"] == STAMP


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"date_timezone": "Asia/Riyadh"}, {"account_timezone": "America/New_York"},
    {"currency": "SAR"}, {"provider_window_start": "2026-11-13T07:00:00+00:00"},
    {"provider_window_end": "2026-11-14T08:00:00+00:00"},
    {"updated_at": "2026-11-13T13:00:00+00:00"}, {"date": "2026-11-12"},
    {"source_mode": "other_source"}, {"action_report_time": "impression"},
])
async def test_invalid_snap_scope_cannot_supply_spend(changes):
    db = fixture("snapchat", "USD", "America/Los_Angeles"); snap(db)
    db["mezan_snapchat_performance_account_day_v3"].rows[0].update(changes)
    row = await read(db)
    assert row["orders"] == 1 and row["spend_sar"] is None


@pytest.mark.asyncio
async def test_tiktok_foreign_currency_uses_native_account_day_fx_only():
    db = fixture("tiktok", "USD"); tiktok(db, 20)
    assert (await read(db))["spend_sar"] is None
    fx = {"user_id": "owner", "ad_account_id": "account-1", "date": DAY,
          "source_mode": "tiktok_marketing_reporting_v2", "account_timezone": "Asia/Riyadh",
          "currency_native": "USD", "fx_rate_to_sar": 3.75}
    db["mezan_tiktok_performance_daily_v2"].rows = [fx]
    assert (await read(db))["spend_sar"] == 75
    fx["account_timezone"] = "America/Los_Angeles"
    assert (await read(db))["spend_sar"] is None


@pytest.mark.asyncio
async def test_google_period_fact_is_identity_only_not_today_spend():
    db = fixture("google"); google(db)
    db["mezan_google_ads_campaign_factsets_v1"].rows[0]["date_from"] = "2026-11-01"
    row = await read(db)
    assert row["campaign_name"] == "Fixture Google" and row["orders"] == 1
    assert row["spend_sar"] is row["roas"] is None


@pytest.mark.asyncio
async def test_other_tenant_spend_and_matching_names_cannot_be_joined():
    db = fixture("google"); google(db)
    db["mezan_google_ads_campaign_factsets_v1"].rows[0]["user_id"] = "different-owner"
    assert (await read(db))["spend_sar"] is None
    db = fixture("tiktok"); tiktok(db)
    db["mezan_tiktok_entity_daily_v2"].rows[0]["rows"][0]["entity_id"] = "unrelated"
    row = await read(db)
    assert row["campaign_name"] == "Fixture winter" and row["spend_sar"] is None


@pytest.mark.asyncio
async def test_duplicate_campaign_across_accounts_is_not_guessed():
    db = fixture("google"); google(db)
    second = deepcopy(db["mezan_integration_accounts_v2"].rows[0]); second["ad_account_id"] = "account-2"
    db["mezan_integration_accounts_v2"].rows.append(second)
    fact = deepcopy(db["mezan_google_ads_campaign_factsets_v1"].rows[0]); fact["account_id"] = "account-2"
    fact["campaigns"][0]["account_id"] = "account-2"
    db["mezan_google_ads_campaign_factsets_v1"].rows.append(fact)
    row = await read(db)
    assert row["status"] == "ambiguous_account"
    assert row["orders"] is row["spend_sar"] is None


def label_key(label="حملة الشتاء التجريبية", provider="tiktok"):
    return "salla:" + provider + ":" + base64.urlsafe_b64encode(label.encode()).decode().rstrip("=")


@pytest.mark.asyncio
async def test_literal_salla_label_survives_missing_ad_accounts_never_gets_spend():
    label = "حملة الشتاء التجريبية"
    first = order(label, at="2026-11-12T22:00:00+00:00")
    first["raw_by_source"]["salla_direct"]["utm_source"] = "tiktok"
    different = deepcopy(first); different["order_number"] = "other-provider"
    different["raw_by_source"]["salla_direct"]["utm_source"] = "snapchat"
    db = NativeDB({"unified_orders": [first, first, different]})
    row = await read(db, label_key(label))
    assert row["orders"] == 1 and row["pieces"] == 4 and row["sales_sar"] == 375
    assert row["spend_sar"] is row["cpa_sar"] is row["roas"] is row["account_id"] is row["campaign_id"] is None
    assert row["commerce_scope"] == "salla_utm_label" and row["period_basis"] == "salla_store_day"
    assert row["timezone"] == "Asia/Riyadh"
    for collection in ("mezan_tiktok_entity_daily_v2", "mezan_google_ads_campaign_factsets_v1"):
        assert not db[collection].queries


@pytest.mark.asyncio
async def test_raw_salla_nested_label_and_source_are_used_not_promoted_display_name():
    db = fixture("tiktok"); tiktok(db)
    raw = db["unified_orders"].rows[0]["raw_by_source"]["salla_direct"]
    raw.pop("utm_campaign"); raw.pop("utm_source")
    raw["source_details"] = {"utm_source": "tiktok", "utm_campaign": "حملة الشتاء التجريبية"}
    raw["campaign"] = {"source": "tiktok", "campaign": "حملة الشتاء التجريبية"}
    row = await read(db, label_key())
    assert row["orders"] == 1 and row["spend_sar"] is None
    raw["source_details"]["utm_campaign"] = "اسم آخر"
    assert (await read(db, label_key()))["orders"] == 0


@pytest.mark.asyncio
async def test_placeholder_is_not_a_campaign_or_label_group():
    value = "Using the CLICKID gad_source provided by google"
    db = fixture("google")
    db["unified_orders"].rows[0]["raw_by_source"]["salla_direct"]["utm_campaign"] = value
    row = await read(db, value)
    assert row["status"] == "campaign_identity_missing" and row["orders"] is None
    with pytest.raises(HTTPException):
        await read(db, label_key(value, "google"))


@pytest.mark.asyncio
async def test_label_quantity_and_currency_gaps_remain_unknown_and_timeout_not_zero():
    db = fixture("tiktok")
    raw = db["unified_orders"].rows[0]["raw_by_source"]["salla_direct"]
    raw["utm_campaign"] = "حملة الشتاء التجريبية"
    raw.pop("items"); raw["amounts"]["total"]["currency"] = "QAR"
    row = await read(db, label_key())
    assert row["orders"] == 1 and row["pieces"] is row["sales_sar"] is None
    db["unified_orders"].error = TimeoutError()
    assert (await read(db, label_key()))["orders"] is None


@pytest.mark.asyncio
async def test_array_snapshot_limit_is_a_failure_not_partial_financial_success():
    db = fixture("google"); google(db)
    db["mezan_google_ads_campaign_factsets_v1"].rows *= 251
    row = await read(db)
    assert row["orders"] == 1 and row["spend_sar"] is None


@pytest.mark.asyncio
async def test_explicit_zero_native_spend_not_confused_with_missing():
    db = fixture("tiktok"); tiktok(db, 0)
    row = await read(db)
    assert row["spend_sar"] == row["cpa_sar"] == 0 and row["roas"] is None
    db["mezan_tiktok_entity_daily_v2"].rows[0]["rows"][0]["spend_native"] = None
    assert (await read(db))["spend_sar"] is None
