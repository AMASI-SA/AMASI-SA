"""Regression tests for Plan-B automatic-send payment freshness."""


def test_payment_method_is_a_latest_wins_order_fact():
    # Importing Order Engine installs the shared merge policy used by the
    # authoritative Salla resync that runs immediately before auto-send.
    import order_engine  # noqa: F401
    import orders_db

    assert "payment_method" in orders_db.CRITICAL_FIELDS


def test_existing_collection_and_payment_facts_remain_latest_wins():
    import order_engine  # noqa: F401
    import orders_db

    expected = {
        "payment_method",
        "payment_status",
        "paid_amount",
        "remaining_amount",
        "payment_collection_status",
    }
    assert expected.issubset(orders_db.CRITICAL_FIELDS)
