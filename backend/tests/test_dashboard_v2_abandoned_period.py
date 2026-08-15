from backend.dashboard_v2_routes import select_abandoned_carts_for_period


def test_select_abandoned_carts_for_period_returns_active_rows_and_counts():
    rows = [
        {
            "cart_id": "active-new",
            "purchased": False,
            "cart_created_at": "2026-08-15T09:00:00+00:00",
            "cart_updated_at": "2026-08-15T10:00:00+00:00",
        },
        {
            "cart_id": "recovered-new",
            "purchased": True,
            "cart_created_at": "2026-08-15T08:00:00+00:00",
            "cart_updated_at": "2026-08-15T11:00:00+00:00",
        },
        {
            "cart_id": "active-old",
            "purchased": False,
            "cart_created_at": "2026-08-14T09:00:00+00:00",
            "cart_updated_at": "2026-08-14T10:00:00+00:00",
        },
    ]

    active, abandoned_count, recovered_count = select_abandoned_carts_for_period(
        rows,
        start="2026-08-15",
        end="2026-08-15",
    )

    assert [row["cart_id"] for row in active] == ["active-new"]
    assert abandoned_count == 2
    assert recovered_count == 1


def test_select_abandoned_carts_for_period_counts_recovery_on_purchase_day():
    rows = [
        {
            "cart_id": "older-cart-recovered-now",
            "purchased": True,
            "cart_created_at": "2026-08-10T09:00:00+00:00",
            "cart_updated_at": "2026-08-15T11:00:00+00:00",
        }
    ]

    active, abandoned_count, recovered_count = select_abandoned_carts_for_period(
        rows,
        start="2026-08-15",
        end="2026-08-15",
    )

    assert active == []
    assert abandoned_count == 0
    assert recovered_count == 1


def test_select_abandoned_carts_for_period_uses_riyadh_business_day():
    rows = [
        {
            "cart_id": "after-midnight-riyadh",
            "purchased": False,
            "cart_created_at": "2026-08-14T21:30:00Z",
            "cart_updated_at": "2026-08-14T21:31:00Z",
        }
    ]

    active, abandoned_count, recovered_count = select_abandoned_carts_for_period(
        rows,
        start="2026-08-15",
        end="2026-08-15",
    )

    assert [row["cart_id"] for row in active] == ["after-midnight-riyadh"]
    assert abandoned_count == 1
    assert recovered_count == 0
