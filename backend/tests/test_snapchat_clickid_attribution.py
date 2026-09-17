"""A Salla click-reference description is not a campaign identity."""
from copy import deepcopy

import pytest

from order_engine.service import _map_row
from order_engine.campaign_enrichment import enrich_order_campaigns


PLACEHOLDER = "Using the CLICKID sccid provided by snapchat"


def raw_order(**fields):
    return {
        "id": "synthetic-order", "reference_id": "synthetic-reference",
        "created_at": "2026-09-15T18:47:30Z",
        "amounts": {"total": {"amount": 185.92, "currency": "SAR"}},
        "utm_source": "snapchat", "utm_campaign": PLACEHOLDER,
        "utm_term": "sccid: synthetic-click-reference",
        **fields,
    }


@pytest.mark.parametrize("value", [PLACEHOLDER, "  USING the CLICKID  sccid provided by snapchat  "])
def test_click_reference_is_unattributed_and_preserves_raw_evidence(value):
    raw = raw_order(utm_campaign=value)
    original = deepcopy(raw)
    result = _map_row(raw)
    assert result.source.source == "snapchat"
    assert result.source.campaign_id is None
    assert result.source.campaign_name is None
    assert result.source.match_status == "unattributed"
    assert result.source.match_confidence == 0
    assert result.source.entity_url is None
    assert result.source.utm_raw["campaign"] == value.strip()
    assert result.source.utm_raw["term"] == raw["utm_term"]
    assert result.totals.total == 185.92
    assert raw == original


def test_explicit_campaign_id_survives_click_reference_description():
    result = _map_row(raw_order(campaign_id="campaign-proven", campaign_name=PLACEHOLDER))
    assert result.source.campaign_id == "campaign-proven"
    assert result.source.campaign_name is None
    assert result.source.match_status == "matched"
    assert result.source.match_method == "campaign_id"


@pytest.mark.asyncio
async def test_enrichment_does_not_reintroduce_placeholder_as_long_campaign_id():
    class NoCatalogRead:
        def __getitem__(self, name):
            raise AssertionError("A click-reference label must not trigger campaign lookup")

    result = await enrich_order_campaigns(
        NoCatalogRead(), user_id="synthetic-owner", orders=[_map_row(raw_order())],
    )
    assert result[0].source.campaign_id is None
    assert result[0].source.campaign_name is None
    assert result[0].source.match_status == "unattributed"
    assert result[0].source.utm_raw["campaign"] == PLACEHOLDER
