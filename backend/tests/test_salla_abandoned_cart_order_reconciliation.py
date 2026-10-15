from datetime import datetime, timedelta, timezone

from salla_integration.cart_order_reconciliation import _select_cart_candidate


def _cart(cart_id: str, *, when: datetime, total: float = 120.0, product_id: str = "p1"):
    return {
        "cart_id": cart_id,
        "purchased": False,
        "cart_updated_at": when.isoformat(),
        "total": total,
        "items": [{"product_id": product_id, "quantity": 1}],
    }


def test_exact_salla_cart_id_is_authoritative_even_when_total_changes():
    now = datetime(2026, 8, 18, 17, 0, tzinfo=timezone.utc)
    carts = [_cart("3046569285908586162", when=now - timedelta(minutes=1))]

    selected, evidence = _select_cart_candidate(
        carts,
        order={"cart_id": "3046569285908586162", "total": 156.60},
        order_at=now,
        exact_cart_id="3046569285908586162",
    )

    assert selected["cart_id"] == "3046569285908586162"
    assert evidence == "order_cart_id"


def test_recent_same_customer_checkout_recovers_cart_when_shipping_changes_total():
    now = datetime(2026, 8, 18, 17, 0, tzinfo=timezone.utc)
    carts = [_cart("cart-recent", when=now - timedelta(seconds=35), total=120.0)]

    selected, evidence = _select_cart_candidate(
        carts,
        order={"total": 156.60, "items": []},
        order_at=now,
        exact_cart_id=None,
    )

    assert selected["cart_id"] == "cart-recent"
    assert evidence == "customer_recent_checkout"


def test_product_overlap_recovers_older_cart_without_amount_equality():
    now = datetime(2026, 8, 18, 17, 0, tzinfo=timezone.utc)
    carts = [_cart("cart-product", when=now - timedelta(hours=2), product_id="991")]

    selected, evidence = _select_cart_candidate(
        carts,
        order={"total": 250.0, "items": [{"product": {"id": "991"}}]},
        order_at=now,
        exact_cart_id=None,
    )

    assert selected["cart_id"] == "cart-product"
    assert evidence == "customer_product_overlap"


def test_old_unrelated_cart_is_not_marked_purchased():
    now = datetime(2026, 8, 18, 17, 0, tzinfo=timezone.utc)
    carts = [_cart("cart-old", when=now - timedelta(days=2), total=120.0, product_id="old")]

    selected, evidence = _select_cart_candidate(
        carts,
        order={"total": 300.0, "items": [{"product_id": "new"}]},
        order_at=now,
        exact_cart_id=None,
    )

    assert selected is None
    assert evidence is None


def test_already_purchased_cart_is_never_selected():
    now = datetime(2026, 8, 18, 17, 0, tzinfo=timezone.utc)
    cart = _cart("cart-done", when=now - timedelta(seconds=10))
    cart["purchased"] = True

    selected, evidence = _select_cart_candidate(
        [cart],
        order={"cart_id": "cart-done"},
        order_at=now,
        exact_cart_id="cart-done",
    )

    assert selected is None
    assert evidence is None
