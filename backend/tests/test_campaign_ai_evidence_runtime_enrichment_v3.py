from __future__ import annotations

from datetime import datetime, timezone

import pytest

import campaign_ai_evidence_runtime_enrichment_v3 as enrichment


@pytest.mark.asyncio
async def test_runtime_enrichment_attaches_product_health_and_actual_media(monkeypatch):
    async def base_builder(_db, _user_id, _candidates, **_kwargs):
        return {
            "product_intelligence": {
                "entities": {
                    "snapchat|campaign|acct|c1": {
                        "products": [
                            {
                                "product_id": "p1",
                                "product_name": "Product",
                                "visibility": "public_status_expected",
                                "price": 100,
                                "description": "Useful product",
                                "main_image": "https://cdn.example.com/p.jpg",
                                "page_probe": {
                                    "status": "PRODUCT_URL_OK",
                                    "page_title": "Product",
                                    "add_to_cart_marker_present": True,
                                },
                                "inventory": {"status": "in_stock"},
                                "variants": [{"quantity": 4, "unlimited_quantity": False}],
                            }
                        ]
                    }
                }
            },
            "limitations": [],
        }

    async def fake_media(_db, _user_id, _candidates):
        return {
            "schema_version": "campaign_ai_actual_ad_creative_media_v3",
            "entities": {
                "snapchat|campaign|acct|c1": {
                    "provider": "snapchat",
                    "media_available": True,
                    "visuals": [{"image_url": "https://cdn.example.com/ad.jpg"}],
                    "limitations": [],
                }
            },
            "visual_count": 1,
            "limitations": [],
        }

    async def fake_offer(*_args, **_kwargs):
        return None

    async def fake_voice(*_args, **_kwargs):
        return {"available": False, "limitations": []}

    async def fake_history(*_args, **_kwargs):
        return {"products": {}, "limitations": []}

    monkeypatch.setattr(enrichment, "build_actual_ad_creative_media_evidence", fake_media)
    monkeypatch.setattr(enrichment, "_enrich_offer_schedules", fake_offer)
    monkeypatch.setattr(enrichment, "build_customer_voice_evidence", fake_voice)
    monkeypatch.setattr(enrichment, "build_product_change_history_evidence", fake_history)

    builder = enrichment.wrap_evidence_builder(base_builder)
    pack = await builder(
        object(),
        "user-1",
        [{"provider": "snapchat", "entity_level": "campaign", "account_id": "acct", "entity_id": "c1"}],
        current=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    product = pack["product_intelligence"]["entities"]["snapchat|campaign|acct|c1"]["products"][0]
    assert product["product_health_score"]["score"] == 100.0
    assert product["product_health_score"]["coverage_pct"] == 100.0
    assert "No product-health score threshold" in pack["product_health_score_contract"]
    assert pack["actual_creative_media"]["visual_count"] == 1


@pytest.mark.asyncio
async def test_runtime_media_failure_is_explicit_not_total_cycle_failure(monkeypatch):
    async def base_builder(_db, _user_id, _candidates, **_kwargs):
        return {"product_intelligence": {"entities": {}}, "limitations": []}

    async def broken_media(*_args, **_kwargs):
        raise RuntimeError("provider down")

    async def fake_offer(*_args, **_kwargs):
        return None

    async def fake_voice(*_args, **_kwargs):
        return {"available": False, "limitations": []}

    async def fake_history(*_args, **_kwargs):
        return {"products": {}, "limitations": []}

    monkeypatch.setattr(enrichment, "build_actual_ad_creative_media_evidence", broken_media)
    monkeypatch.setattr(enrichment, "_enrich_offer_schedules", fake_offer)
    monkeypatch.setattr(enrichment, "build_customer_voice_evidence", fake_voice)
    monkeypatch.setattr(enrichment, "build_product_change_history_evidence", fake_history)

    pack = await enrichment.wrap_evidence_builder(base_builder)(
        object(),
        "user-1",
        [],
        current=datetime(2026, 8, 19, tzinfo=timezone.utc),
    )

    assert pack["actual_creative_media"]["visual_count"] == 0
    assert "creative_media_unavailable:RuntimeError" in pack["actual_creative_media"]["limitations"]
    assert "creative_media_unavailable:RuntimeError" in pack["limitations"]
