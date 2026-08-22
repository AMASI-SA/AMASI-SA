from mezan_attribution_order_ledger import build_order_attribution_ledger_row


def _identities():
    return [
        {"provider": "snapchat_ads", "account_id": "acc-1", "campaign_id": "cmp-1", "campaign_name": "National Day"},
        {"provider": "snapchat_ads", "account_id": "acc-1", "campaign_id": "cmp-2", "campaign_name": "Mugs"},
    ]


def test_exact_campaign_id_is_confirmed_and_decision_safe():
    order = {"id": "o1", "source_details": {"source": "snapchat", "campaign_id": "cmp-1"}}
    row = build_order_attribution_ledger_row(order=order, campaign_identities=_identities())
    assert row["attribution"]["quality"] == "confirmed"
    assert row["attribution"]["decision_safe"] is True
    assert row["attribution"]["campaign_id"] == "cmp-1"
    assert row["attribution"]["match_method"] == "exact_campaign_id"


def test_unique_campaign_name_is_inferred_not_decision_safe():
    order = {"id": "o2", "source_details": {"source": "snapchat", "campaign_name": "Mugs"}}
    row = build_order_attribution_ledger_row(order=order, campaign_identities=_identities())
    assert row["attribution"]["quality"] == "inferred"
    assert row["attribution"]["decision_safe"] is False
    assert row["attribution"]["campaign_id"] == "cmp-2"


def test_ambiguous_campaign_name_is_not_distributed():
    identities = _identities() + [
        {"provider": "snapchat", "account_id": "acc-2", "campaign_id": "cmp-3", "campaign_name": "Mugs"}
    ]
    order = {"id": "o3", "source_details": {"source": "snapchat", "campaign_name": "Mugs"}}
    row = build_order_attribution_ledger_row(order=order, campaign_identities=identities)
    assert row["attribution"]["quality"] == "ambiguous"
    assert row["attribution"]["campaign_id"] is None
    assert row["attribution"]["decision_safe"] is False


def test_direct_order_remains_non_campaign():
    order = {"id": "o4", "source_details": {"source": "direct"}}
    row = build_order_attribution_ledger_row(order=order, campaign_identities=_identities())
    assert row["attribution"]["quality"] == "unattributed"
    assert row["attribution"]["campaign_id"] is None
    assert row["attribution"]["match_method"] == "explicit_non_campaign_direct"


def test_provider_purchase_count_cannot_create_attribution():
    order = {"id": "o5", "provider_purchases": 12, "source": "salla_direct"}
    row = build_order_attribution_ledger_row(order=order, campaign_identities=_identities())
    assert row["attribution"]["quality"] == "unattributed"
    assert row["evidence"]["provider_purchase_counts_used_for_attribution"] is False


def test_line_items_keep_product_and_variant_identity():
    order = {
        "id": "o6",
        "items": [
            {"product_id": "p1", "variant_id": "v1", "sku": "SKU-1", "name": "Cup", "quantity": 2, "total": 90}
        ],
    }
    row = build_order_attribution_ledger_row(order=order)
    assert row["line_items"][0]["product_id"] == "p1"
    assert row["line_items"][0]["product_variant_id"] == "v1"
    assert row["line_items"][0]["quantity"] == 2


def test_unknown_profit_remains_unknown_not_zero():
    row = build_order_attribution_ledger_row(order={"id": "o7"})
    assert row["profit"]["known"] is False
    assert row["profit"]["net_profit_sar"] is None
    assert row["profit"]["cogs_sar"] is None


def test_known_profit_is_preserved_without_reconstruction():
    row = build_order_attribution_ledger_row(
        order={"id": "o8"},
        profit_facts={"known": True, "net_profit_sar": 55.5, "revenue_sar": 200, "source_contract": "mezan_profit_engine_v2"},
    )
    assert row["profit"]["known"] is True
    assert row["profit"]["net_profit_sar"] == 55.5
    assert row["profit"]["source_contract"] == "mezan_profit_engine_v2"


def test_verified_campaign_product_link_enriches_only_after_campaign_match():
    order = {
        "id": "o9",
        "source_details": {"source": "snapchat", "campaign_id": "cmp-1"},
        "items": [{"product_id": "p1", "variant_id": "v1", "quantity": 1}],
    }
    links = [{
        "campaign_id": "cmp-1",
        "product_id": "p1",
        "product_variant_id": "v1",
        "association_id": "link-1",
        "evidence": {"verification_status": "verified", "source": "campaign_creation"},
    }]
    row = build_order_attribution_ledger_row(
        order=order,
        campaign_identities=_identities(),
        campaign_product_links=links,
    )
    assert len(row["verified_campaign_product_links"]) == 1
    assert row["verified_campaign_product_links"][0]["association_id"] == "link-1"
