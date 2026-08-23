from campaign_ai_gcc_market_evidence import (
    build_first_party_market_evidence,
    merge_market_observations,
)


def _order(number: str, country="SA", status="delivered"):
    return {
        "order_number": number,
        "order_status": status,
        "raw_by_source": {
            "salla_direct": {
                "shipping": {
                    "address": {"country_code": country},
                    "shipped_at": "2026-08-01T10:00:00+00:00",
                    "delivered_at": "2026-08-03T10:00:00+00:00",
                }
            }
        },
    }


def _ledger(number: str, *, known=True, revenue=250, cogs=80, shipping=25, fees=8, ads=45):
    return {
        "order_key": number,
        "profit": {
            "known": known,
            "revenue_sar": revenue,
            "cogs_sar": cogs,
            "shipping_sar": shipping,
            "fees_sar": fees,
            "allocated_ad_spend_sar": ads,
        },
    }


def test_first_party_complete_profit_coverage_builds_measured_economics():
    orders = [_order(str(index), "SA") for index in range(1, 6)]
    ledger = [_ledger(str(index)) for index in range(1, 6)]

    evidence = build_first_party_market_evidence(
        orders=orders,
        ledger_rows=ledger,
        observed_days=30,
    )

    saudi = evidence[0]
    assert saudi["market"] == "Saudi Arabia"
    assert saudi["evidence_status"] == "measured"
    assert saudi["confidence"] == "medium"
    assert saudi["local_price_sar"] == 250
    assert saudi["landed_product_cost_sar"] == 80
    assert saudi["expected_cac_sar"] == 45
    assert saudi["shipping_cost_sar"] == 25
    assert saudi["payment_fee_sar"] == 8
    assert saudi["expected_monthly_orders"] == 5
    assert saudi["delivery_days"] == 2
    assert saudi["first_party"]["profit_coverage_ratio"] == 1.0


def test_incomplete_profit_coverage_does_not_average_known_subset():
    orders = [_order(str(index), "QA") for index in range(1, 6)]
    ledger = [_ledger(str(index)) for index in range(1, 5)]

    evidence = build_first_party_market_evidence(
        orders=orders,
        ledger_rows=ledger,
        observed_days=30,
    )

    qatar = evidence[0]
    assert qatar["evidence_status"] == "partial"
    assert qatar["local_price_sar"] is None
    assert qatar["expected_cac_sar"] is None
    assert qatar["first_party"]["known_profit_orders"] == 4
    assert qatar["first_party"]["profit_coverage_ratio"] == 0.8


def test_small_sample_remains_low_confidence_even_with_complete_profit():
    orders = [_order("1", "AE")]
    ledger = [_ledger("1")]

    evidence = build_first_party_market_evidence(
        orders=orders,
        ledger_rows=ledger,
        observed_days=30,
    )

    uae = evidence[0]
    assert uae["confidence"] == "low"
    assert uae["evidence_status"] == "partial"
    assert uae["local_price_sar"] == 250


def test_return_rate_is_observed_from_first_party_order_status():
    orders = [
        _order("1", "KW", status="delivered"),
        _order("2", "KW", status="refunded"),
        _order("3", "KW", status="returned"),
        _order("4", "KW", status="delivered"),
        _order("5", "KW", status="delivered"),
    ]
    ledger = [_ledger(str(index)) for index in range(1, 6)]

    evidence = build_first_party_market_evidence(
        orders=orders,
        ledger_rows=ledger,
        observed_days=30,
    )

    assert evidence[0]["expected_return_rate"] == 0.4
    assert evidence[0]["return_cost_per_return_sar"] is None


def test_external_observation_fills_unknown_without_overwriting_first_party():
    first_party = build_first_party_market_evidence(
        orders=[_order(str(index), "SA") for index in range(1, 6)],
        ledger_rows=[_ledger(str(index)) for index in range(1, 6)],
        observed_days=30,
    )
    observation = {
        "market": "Saudi Arabia",
        "source_name": "verified returns study",
        "source_type": "first_party_store",
        "reliability": "first_party",
        "observed_at": "2026-08-23T00:00:00+00:00",
        "values": {
            "local_price_sar": 999,
            "return_cost_per_return_sar": 30,
            "competition_score": 55,
            "product_fit_score": 80,
        },
    }

    merged = merge_market_observations(
        first_party=first_party,
        observations=[observation],
    )

    saudi = merged[0]
    assert saudi["local_price_sar"] == 250
    assert saudi["return_cost_per_return_sar"] == 30
    assert saudi["competition_score"] == 55
    assert saudi["product_fit_score"] == 80


def test_weak_or_unproven_external_source_is_ignored():
    observation = {
        "market": "Qatar",
        "source_name": "random social post",
        "source_type": "social_trend",
        "reliability": "contextual",
        "observed_at": "2026-08-23T00:00:00+00:00",
        "values": {"expected_cac_sar": 20},
    }

    merged = merge_market_observations(first_party=[], observations=[observation])

    assert merged == []


def test_supported_external_market_can_create_evidence_required_row():
    observation = {
        "country_code": "BH",
        "source_name": "official shipping tariff",
        "source_type": "official_statistics",
        "reliability": "official",
        "observed_at": "2026-08-23T00:00:00+00:00",
        "values": {"shipping_cost_sar": 35, "delivery_days": 3},
    }

    merged = merge_market_observations(first_party=[], observations=[observation])

    assert merged[0]["market"] == "Bahrain"
    assert merged[0]["shipping_cost_sar"] == 35
    assert merged[0]["delivery_days"] == 3
    assert merged[0]["evidence_status"] == "partial"


def test_non_gcc_market_is_rejected():
    observation = {
        "market": "United States",
        "source_name": "official source",
        "reliability": "official",
        "observed_at": "2026-08-23T00:00:00+00:00",
        "values": {"shipping_cost_sar": 50},
    }

    assert merge_market_observations(first_party=[], observations=[observation]) == []
