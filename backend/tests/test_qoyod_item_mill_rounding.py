from decimal import Decimal, ROUND_HALF_UP

from integrations.qoyod_manual import rounding_lrm_exact


class FakeSendModule:
    @staticmethod
    def _compute_item_line(
        item, line_resolutions, tax_factor, tax_percent
    ):
        payload = {
            "quantity": 1.0,
            "unit_price": 115.74,
            "discount": 7.04,
        }
        row = {
            "sku": "AMS11911",
            "qoyod_unit_price": 115.74,
            "computed_discount": 7.04,
            "line_net_after_discount": 108.70,
            "line_tax_15pct": 16.31,
            "line_gross_after_tax": 125.01,
            "delta_vs_salla_line": 0.01,
        }
        return payload, row, 125.01

    @staticmethod
    def _line_gross(
        *, unit_price, quantity, discount, tax_percent
    ):
        net = (
            Decimal(str(unit_price))
            * Decimal(str(quantity))
            - Decimal(str(discount))
        )
        gross = net * (
            Decimal("1")
            + Decimal(str(tax_percent)) / Decimal("100")
        )
        return float(
            gross.quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )
        )


def test_mill_precision_finds_exact_125_00_representation():
    item = {
        "sku": "AMS11911",
        "quantity": 1,
        "total": 125.00,
    }

    variants = rounding_lrm_exact._line_variants(
        FakeSendModule,
        item,
        {},
        1.15,
        15.0,
    )

    assert -1 in variants

    payload, row, gross, _score = variants[-1]

    assert gross == 125.00
    assert payload["unit_price"] == 115.74
    assert payload["discount"] == 7.041
    assert row["line_gross_after_tax"] == 125.00
    assert (
        row["lrm_payload_adjustment"]["discount_shift"]
        == 0.001
    )
    assert (
        row["lrm_payload_adjustment"]["unit_price_shift"]
        == 0.0
    )
