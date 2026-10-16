from datetime import date
from types import SimpleNamespace

import mongomock
import pytest

from snapchat_v2 import salla_outcomes as outcomes
from tests.test_snapchat_reporting_v2_refresh_visibility import Collection


class Db(SimpleNamespace):
    pass


def fixture_db(orders, catalogs):
    raw = mongomock.MongoClient().db
    if orders:
        raw.unified_orders.insert_many(orders)
    if catalogs:
        raw.mezan_snapchat_entity_facts_v2.insert_many(catalogs)
    raw.settings.insert_one({"user_id": "owner", "report_included_statuses": ["completed"]})
    return Db(**{name: Collection(raw[name]) for name in (
        "unified_orders", "settings", "mezan_snapchat_entity_facts_v2")})


def campaign(account, campaign_id, name="Same name", owner="owner", provider="snapchat_ads"):
    return {"user_id": owner, "provider": provider, "entity_type": "campaign",
            "ad_account_id": account, "external_id": campaign_id, "name": name}


def order(campaign_id=None, name=None, owner="owner", amount=100):
    return {"user_id": owner, "created_at": "2026-10-15T12:00:00+00:00",
            "order_date": "2026-10-15", "source_native": "snapchat", "campaign_id": campaign_id,
            "campaign_name": name, "total_amount": amount, "order_status": "completed"}


async def load(db, identities=None):
    return await outcomes.load_salla_campaign_outcomes(
        db, "owner", account_id="sa", date_from=date(2026, 10, 15), date_to=date(2026, 10, 15),
        timezone_name="Asia/Riyadh", identities=identities or [
            {"account_id": "sa", "campaign_id": "sa-campaign", "campaign_name": "Saudi"}],
        campaign_spend_sar={})


@pytest.mark.asyncio
async def test_foreign_exact_id_excluded_without_changing_own_sales():
    db = fixture_db([order("us-campaign"), order("sa-campaign", amount=50)],
                    [campaign("us", "us-campaign"), campaign("sa", "sa-campaign", "Saudi")])
    result = await load(db)
    proof = result["summary"]["campaign_attribution"]
    assert proof["status"] == "complete"
    assert proof["evaluated_orders"] == proof["matched_orders"] == 1
    assert result["summary"]["campaign_matched_financial_sales_sar"] == 50
    assert result["summary"]["total_salla_created_orders"] == 1
    assert len(result["orders"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unknown", "name", "missing", "duplicate", "tenant", "provider"])
async def test_unproven_foreign_ownership_stays_blocked(kind):
    value = order("us-campaign")
    catalog = [campaign("us", "us-campaign")]
    if kind == "unknown": catalog = []
    if kind == "name": value = order(name="Same name")
    if kind == "missing": value = order()
    if kind == "duplicate": catalog.append(campaign("sa", "us-campaign"))
    if kind == "tenant": catalog = [campaign("us", "us-campaign", owner="another-owner")]
    if kind == "provider": catalog = [campaign("us", "us-campaign", provider="meta_ads")]
    result = await load(fixture_db([value], catalog))
    assert result["summary"]["campaign_attribution"]["status"] == "partial"
    assert result["summary"]["campaign_attribution"]["unmatched_orders"] == 1


@pytest.mark.asyncio
async def test_catalog_overflow_fails_closed(monkeypatch):
    monkeypatch.setattr(outcomes, "MAX_OWNER_CAMPAIGN_ROWS", 1)
    db = fixture_db([order("us-campaign")], [campaign("us", "us-campaign"), campaign("sa", "sa-campaign")])
    with pytest.raises(ValueError, match="catalog"):
        await load(db)


@pytest.mark.asyncio
async def test_shared_name_cannot_certify_target_account():
    db = fixture_db([order(name="Saudi")], [campaign("us", "us-campaign", "Saudi")])
    result = await load(db)
    assert result["summary"]["campaign_attribution"]["reason_counts"] == {"ambiguous_name": 1}
    assert result["by_campaign"] == {}


@pytest.mark.asyncio
async def test_raw_provider_correction_takes_precedence():
    value = order("us-campaign")
    value["raw_by_source"] = {"salla_direct": {"campaign_id": "sa-campaign", "source_native": "snapchat"}}
    result = await load(fixture_db([value], [campaign("us", "us-campaign")]))
    assert result["summary"]["campaign_attribution"]["matched_orders"] == 1
    assert result["summary"]["campaign_matched_financial_sales_sar"] == 100


@pytest.mark.asyncio
async def test_foreign_rows_never_leak_when_caller_supplies_both_catalogs():
    result = await load(fixture_db([order("us-campaign")], []), identities=[
        {"account_id": "us", "campaign_id": "us-campaign", "campaign_name": "US"},
        {"account_id": "sa", "campaign_id": "sa-campaign", "campaign_name": "Saudi"},
    ])
    assert result["summary"]["campaign_matched_financial_sales_sar"] == 0
    assert result["summary"]["campaign_attribution"]["evaluated_orders"] == 0
    assert result["orders"] == []


@pytest.mark.asyncio
async def test_catalog_error_does_not_silently_certify_partial_catalog():
    class BrokenCatalog:
        def find(self, *args):
            raise RuntimeError("catalog unavailable")
    db = fixture_db([order("us-campaign")], [])
    db.mezan_snapchat_entity_facts_v2 = BrokenCatalog()
    with pytest.raises(RuntimeError, match="catalog unavailable"):
        await load(db)
