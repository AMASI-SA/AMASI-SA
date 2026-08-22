from mezan_attribution_ledger_sync import (
    campaign_identities_from_links,
    profit_facts_from_order,
)


def test_campaign_identities_only_use_campaign_rows():
    links = [
        {
            "provider": "snapchat_ads",
            "account_id": "a1",
            "campaign_id": "c1",
            "campaign_name": "Winner",
            "product_id": "p1",
        },
        {
            "provider": "snapchat_ads",
            "account_id": "a1",
            "campaign_id": "c1",
            "campaign_name": "Winner",
            "product_id": "p2",
        },
        {"provider": "snapchat_ads", "account_id": "a1", "campaign_id": None},
    ]
    result = campaign_identities_from_links(links)
    assert result == [
        {
            "provider": "snapchat_ads",
            "account_id": "a1",
            "campaign_id": "c1",
            "campaign_name": "Winner",
        }
    ]


def test_unknown_profit_stays_unknown():
    assert profit_facts_from_order({"order_number": "1"}) is None
    assert profit_facts_from_order({"profit": {"known": False, "net_profit_sar": 0}}) is None


def test_known_order_profit_is_carried_without_reconstruction():
    result = profit_facts_from_order(
        {
            "mezan_profit": {
                "known": True,
                "net_profit_sar": 31.75,
                "revenue_sar": 120,
                "cogs_sar": 40,
                "shipping_sar": 18,
                "fees_sar": 4,
                "allocated_ad_spend_sar": 26.25,
                "source_contract": "mezan_order_profit_v1",
            }
        }
    )
    assert result["net_profit_sar"] == 31.75
    assert result["source_contract"] == "mezan_order_profit_v1"


def test_store_level_profit_totals_are_not_treated_as_order_profit():
    order = {
        "profit_envelope": {
            "totals": {"net_profit": 100000},
            "quality": {"known": True},
        }
    }
    assert profit_facts_from_order(order) is None
