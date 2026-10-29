from copy import deepcopy
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from marketing_report_orders import classify_source, unique_orders, apply_store_orders


def test_direct_requires_marketing_evidence_not_ingestion_or_missing_campaign():
    assert classify_source({"source": "salla_direct"}) == "unclassified"
    assert classify_source({"source": "store"}) == "unclassified"
    assert classify_source({"source_details": {"source": "direct"}}) == "direct"
    assert classify_source({"utm_source": "snapchat"}) == "snapchat"
    assert classify_source({"utm_source": "whatsapp"}) == "unclassified"
    assert classify_source({"source": "direct", "utm_source": "tiktok"}) == "tiktok"
    assert classify_source({"utm_source": "snapchat", "raw_by_source": {"salla_direct": {"utm_source": "meta"}}}) == "meta"


def test_duplicates_use_one_latest_order_even_when_identity_aliases_differ():
    rows = [
        {"order_number": "R1", "total_amount": 100, "updated_at": "2026-10-01T10:00:00Z"},
        {"order_number": "R1", "raw_by_source": {"salla_direct": {"id": 21}}, "total_amount": 120, "updated_at": "2026-10-01T11:00:00Z"},
        {"order_number": "R2", "raw_by_source": {"salla_direct": {"id": 21}}, "total_amount": 130, "updated_at": "2026-10-01T12:00:00Z"},
    ]
    result = unique_orders(rows)
    assert len(result) == 1
    assert result[0]["total_amount"] == 130
    with pytest.raises(ValueError, match="identity"):
        unique_orders([{"total_amount": 100}])


class Cursor:
    def __init__(self, rows): self.rows = rows
    def limit(self, n): return self
    def max_time_ms(self, n): return self
    async def to_list(self, length): return deepcopy(self.rows)


def order(n, source, total, **extra):
    return {"order_number": str(n), "source": "salla_direct", "utm_source": source,
        "order_date": "2026-10-01", "total_amount": total, "currency": "SAR",
        "order_status": "completed", "shipping_company": "pickup",
        "payment_fee_status": "actual", "actual_payment_fee": 0, **extra}


@pytest.fixture
def setup(monkeypatch):
    import integrations_control_center.snapchat_campaign_profitability as profit
    import shipping_cost_ssot
    monkeypatch.setattr(profit, "_load_cost_context", AsyncMock(return_value={
        "products_by_id": {}, "products_by_variant": {}, "products_by_sku": {},
        "profile_map": {}, "product_binding_map": {}, "option_map": {}, "resources": {}, "policy": {}}))
    monkeypatch.setattr(shipping_cost_ssot, "get_company_configs", AsyncMock(return_value={"pickup": {"cost_per_order": 0}}))
    def build(rows, settings=None):
        collection = SimpleNamespace(find=lambda query, projection: Cursor(rows))
        return SimpleNamespace(unified_orders=collection, settings=SimpleNamespace(find_one=AsyncMock(return_value=settings or {})))
    return build


def providers():
    return [{"provider": p, "status": "available", "rows": []} for p in ("snapchat", "tiktok", "meta", "google", "direct", "unclassified")]


@pytest.mark.asyncio
async def test_partition_includes_unmatched_campaigns_direct_and_unknown_once(setup):
    entries = [order(1,"snapchat",100,campaign_id="known"), order(2,"snapchat",50),
        order(3,"tiktok",60,campaign_id="missing"), order(4,"direct",70), order(5,None,80),
        order(6,"whatsapp",90), order(7,"meta",0), order(8,"google",20,order_status="cancelled")]
    data = providers()
    data[0]["rows"] = [{"id": "snap:c", "provider": "snapchat", "campaign_id": "known", "name": "C", "spend_sar": 10}]
    proof = await apply_store_orders(setup(entries), "owner", date(2026,10,1), date(2026,10,1), data)
    assert proof["orders"] == 8
    assert proof["sales_sar"] == 470
    assert proof["order_delta"] == proof["sales_delta_sar"] == 0
    assert {p["provider"]: sum(r["salla"]["orders"] for r in p["rows"]) for p in data} == {
        "snapchat":2,"tiktok":1,"meta":1,"google":1,"direct":1,"unclassified":2}
    assert data[0]["rows"][0]["salla"] == {"orders":1,"sales_sar":100}
    assert data[0]["rows"][1]["salla"] == {"orders":1,"sales_sar":50}
    assert data[4]["rows"][0]["spend_sar"] == 0


@pytest.mark.asyncio
async def test_one_store_timezone_and_same_status_scope_for_counts_and_sales(setup):
    entries = [order(1,"direct",10,created_at="2026-09-30T21:00:00Z"),
        order(2,"direct",20,created_at="2026-10-01T21:00:00Z"),
        order(3,"direct",30,order_status="cancelled")]
    data = providers()
    proof = await apply_store_orders(setup(entries,{"report_included_statuses":["completed"]}), "owner",date(2026,10,1),date(2026,10,1),data,status_scope="report")
    assert proof["orders"] == 1
    assert proof["sales_sar"] == 10
    assert proof["timezone"] == "Asia/Riyadh"


@pytest.mark.asyncio
async def test_missing_currency_is_not_zero_and_no_orders_is_proven_zero(setup):
    data = providers()
    proof = await apply_store_orders(setup([order(1,"direct",10,currency="XYZ")]),"owner",date(2026,10,1),date(2026,10,1),data)
    assert proof["orders"] == 1
    assert proof["sales_sar"] is None
    assert proof["sales_delta_sar"] is None
    assert data[4]["rows"][0]["salla"]["sales_sar"] is None
    assert data[0]["rows"][0]["salla"] == {"orders":0,"sales_sar":0}


@pytest.mark.asyncio
async def test_conflicting_campaign_ids_stay_with_platform_not_arbitrarily_distributed(setup):
    data = providers()
    data[0]["rows"] = [{"id": f"snap:{a}","campaign_id":"same","name":"same","spend_sar":5} for a in ("a","b")]
    await apply_store_orders(setup([order(1,"snapchat",100,campaign_id="same")]),"owner",date(2026,10,1),date(2026,10,1),data)
    assert [r["salla"]["orders"] for r in data[0]["rows"]] == [0,0,1]


def test_all_sources_http_totals_match_store_and_no_provider_conversion_is_added(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import marketing_reports as reports
    async def snap(*args, **kwargs):
        assert kwargs["include_commerce"] is False
        return {"provider":"snapchat","status":"available","rows":[],
            "source_totals":[{"spend_sar":100,"purchases":999,"sales_sar":10000}]}
    async def other(db, user, provider, start, end, **kwargs):
        assert kwargs["include_commerce"] is False
        return {"provider":provider,"status":"available","rows":[],
            "source_totals":{"spend_sar":40,"conversions":777} if provider == "tiktok" else {}}
    monkeypatch.setattr(reports,"snapchat_report",snap)
    monkeypatch.setattr(reports,"other_report",other)
    app = FastAPI()
    app.include_router(reports.make_marketing_reports_router(setup([order(1,"direct",10),order(2,None,20),order(3,"snapchat",30)]),lambda:{"id":"owner","role":"owner"}))
    result = TestClient(app).get("/marketing-reports?provider=all&source=salla&date_from=2026-10-01&date_to=2026-10-01")
    assert result.status_code == 200
    body = result.json()
    assert len(body["reports"]) == 6
    assert body["totals"]["orders"] == 3
    assert body["totals"]["sales_sar"] == 60
    assert body["totals"]["spend_sar"] == 140
    assert body["reconciliation"]["order_delta"] == body["reconciliation"]["sales_delta_sar"] == 0


def test_direct_http_uses_salla_without_contacting_ad_readers(setup, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import marketing_reports as reports
    read = AsyncMock(side_effect=AssertionError("advertising reader must not run"))
    monkeypatch.setattr(reports,"snapchat_report",read)
    monkeypatch.setattr(reports,"other_report",read)
    app = FastAPI()
    app.include_router(reports.make_marketing_reports_router(setup([order(1,"direct",10),order(2,None,20)]),lambda:{"id":"owner","role":"owner"}))
    result = TestClient(app).get("/marketing-reports?provider=direct&source=platform&date_from=2026-10-01&date_to=2026-10-01")
    assert result.status_code == 200
    assert result.json()["source"] == "salla"
    assert result.json()["totals"]["orders"] == 1
    assert result.json()["reports"][0]["totals"]["cpa_sar"] is None
    assert result.json()["reconciliation"]["outside_selection"] == 1
    read.assert_not_called()
