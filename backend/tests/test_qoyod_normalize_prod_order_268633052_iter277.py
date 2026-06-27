"""Iter-277 — verifies Iter-275/276 against user-supplied
production payload for order 268633052 (AMS11980).

Confirms the bug the operator saw on PROD is NOT in the current
preview code, so the gap is purely deploy-state.
"""
from integrations.qoyod.normalizer import _normalize_item


def test_order_268633052_ams11980_full_normalization():
    """Exact raw payload shape Make.com posted to Production for
    order 268633052. Expected canonical:
        unit_price        = 159
        tax_amount        = 11.36
        discount_amount   = 17.01
        total             = 153.35
    """
    raw = {
        "sku": "AMS11980",
        "name": "عباية ستيتش بناتي - تصميم أنيق مع طرحة",
        "quantity": 1,
        "amounts": {
            "price_without_tax": {"amount": 159, "currency": "SAR"},
            "total_discount":    {"amount": 17.01, "currency": "SAR"},
            "tax": {
                "percent": "8.00",
                "amount": {"amount": 11.36, "currency": "SAR"},
            },
            "total": {"amount": 153.35, "currency": "SAR"},
        },
    }
    dto = _normalize_item(raw)
    assert dto.sku             == "AMS11980"
    assert dto.quantity        == 1.0
    assert dto.unit_price      == 159.0,  f"got unit_price={dto.unit_price}"
    assert dto.tax_amount      == 11.36,  f"got tax_amount={dto.tax_amount}"
    assert dto.discount_amount == 17.01,  f"got discount_amount={dto.discount_amount}"
    assert dto.total           == 153.35, f"got total={dto.total}"
    # Line-level math reconciles: 159 − 17.01 + 11.36 = 153.35
    derived = (dto.unit_price * dto.quantity
               - dto.discount_amount
               + dto.tax_amount)
    assert round(derived, 2) == 153.35


def test_order_268633052_tax_node_with_percent_field_doesnt_confuse_money():
    """The `tax` node in the production payload carries BOTH a
    `percent` string and the actual `amount` money node. `_money`
    must ignore `percent` and recurse through the nested `amount`."""
    from integrations.qoyod.normalizer import _money
    tax_node = {
        "percent": "8.00",
        "amount": {"amount": 11.36, "currency": "SAR"},
    }
    assert _money(tax_node) == 11.36
