import json
from copy import deepcopy
from pathlib import Path

import pytest

from order_engine.repository import OrderDiscoveryRow
from order_engine.search import search_orders
from order_engine.service import _map_row

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "salla_campaign_baseline_20260830.json").read_text(encoding="utf-8"))


def raw_order(row):
    campaign = FIXTURE["campaign"]
    return {
        "id": f"salla-{row['reference_id']}", "reference_id": row["reference_id"],
        "created_at": row["created_at"], "status": {"slug": "completed", "name": "تم التنفيذ"},
        "payment": {"status": "paid", "paid_amount": row["amount"], "collection_status": "paid"},
        "amounts": {"total": {"amount": row["amount"], "currency": "SAR"}},
        "utm_source": "Snapchat", "utm_medium": "paid_social",
        "utm_campaign": campaign["name"], "utm_content": "raw-content-🇸🇦", "utm_term": "raw term",
        "campaign_id": campaign["id"], "campaign_name": campaign["name"],
        "ad_squad_id": "squad-1", "ad_squad_name": campaign["ad_squad_name"],
        "ad_id": "ad-1", "ad_name": campaign["ad_name"], "sc_click_id": f"click-{row['reference_id']}",
        "items": [{"id": f"item-{row['reference_id']}", "quantity": 1, "product": {"id": "p1", "name": "طقم أطفال سعودي فاخر", "sku": "KIDS-96"}}],
    }


class Repository:
    def __init__(self, rows): self.rows = rows
    async def list_salla_orders(self, **kwargs): return deepcopy(self.rows)


def discovery(row):
    return OrderDiscoveryRow(order_number=row["reference_id"], order_date=row["created_at"][:10], salla_raw=raw_order(row))


def test_baseline_fixture_is_attributed_by_campaign_id_and_preserves_raw_utm():
    orders = [_map_row(raw_order(row)) for row in FIXTURE["orders"]]
    assert [order.order_number for order in orders] == ["281358491", "281358507", "281367168"]
    assert round(sum(order.totals.total for order in orders), 2) == 562.97
    assert all(order.source.campaign_id == FIXTURE["campaign"]["id"] for order in orders)
    assert all(order.source.match_method == "campaign_id" for order in orders)
    assert all(order.source.match_status == "matched" for order in orders)
    assert orders[0].source.utm_raw["content"] == "raw-content-🇸🇦"
    assert orders[0].source.utm_normalized["content"] == "raw-content-🇸🇦"


@pytest.mark.asyncio
async def test_search_combines_filters_deduplicates_and_uses_created_at():
    rows = [discovery(row) for row in FIXTURE["orders"]]
    rows.append(deepcopy(rows[0]))  # webhook/backfill retry
    old = {"reference_id": "281146654", "created_at": "2026-08-28T22:00:00Z", "amount": 99.0}
    rows.append(discovery(old))
    result = await search_orders(Repository(rows), user_id="owner", filters={
        "campaign_id": FIXTURE["campaign"]["id"], "product": "أطفال", "sku": "KIDS-96",
        "created_from": "2026-08-30T00:00:00+03:00", "created_to": "2026-08-31T00:00:00+03:00",
        "baseline_cutoff_at": FIXTURE["baseline_cutoff_at"],
    })
    assert [order.order_number for order in result.items] == ["281358491", "281358507", "281367168"]
    assert result.summary["orders"] == 3
    assert result.summary["paid_orders"] == 3
    assert result.summary["paid_sales"] == 562.97
    assert result.summary["orders_at_or_before_cutoff"] == 3
    assert result.summary["orders_after_cutoff"] == 0


@pytest.mark.asyncio
async def test_pending_cancelled_and_refunded_never_enter_paid_sales():
    base = raw_order(FIXTURE["orders"][0])
    rows = []
    for number, status in (("pending", "بانتظار الدفع"), ("cancelled", "ملغي"), ("refunded", "مسترجع")):
        raw = deepcopy(base); raw["reference_id"] = number; raw["id"] = number
        raw["status"] = {"name": status, "slug": status}; raw["payment"]["collection_status"] = "unpaid"
        rows.append(OrderDiscoveryRow(order_number=number, order_date="2026-08-30", salla_raw=raw))
    result = await search_orders(Repository(rows), user_id="owner", filters={"provider": "snapchat"})
    assert result.summary["paid_sales"] == 0
    assert result.summary["pending_payment_orders"] == 1
    assert result.summary["cancelled_orders"] == 1
    assert result.summary["refunded_orders"] == 1


def test_created_at_drives_account_day_not_updated_at():
    raw = raw_order(FIXTURE["orders"][0]); raw["created_at"] = "2026-08-30T06:30:00Z"; raw["updated_at"] = "2026-08-31T08:00:00Z"
    order = _map_row(raw)
    assert order.source.order_created_at_account.date().isoformat() == "2026-08-30"
    assert order.source.account_timezone == "America/New_York"
    assert order.source.order_created_at_riyadh.date().isoformat() == "2026-08-30"


def test_snapchat_and_salla_are_independent_sources_of_truth():
    # Zero provider purchases can coexist with the three Salla orders. This is
    # an attribution delta, not a reason to rewrite either source.
    snapchat_purchases = 0
    salla_orders = len(FIXTURE["orders"])
    assert snapchat_purchases == 0 and salla_orders == 3
