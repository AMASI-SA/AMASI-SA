"""Regression coverage for invoice-derived Salla payment fees (2026-08)."""
from __future__ import annotations

import pytest

from excel_parser import match_settings
import payment_gateway_metrics as gateway_metrics
from payment_gateway_metrics import (
    _configured_fee_rules,
    resolve_canonical,
)
from payment_methods import (
    DEFAULT_PAYMENT_METHODS,
    PAYMENT_FEE_DEFAULTS_VERSION,
    migrate_payment_method_defaults,
    normalize_payment_method,
)


def _defaults_by_name() -> dict[str, dict]:
    return {row["name"]: row for row in DEFAULT_PAYMENT_METHODS}


def test_invoice_derived_defaults_and_card_fallbacks():
    rows = _defaults_by_name()

    assert rows["مدى"] == {
        "name": "مدى", "commission_percent": 1.0,
        "fixed_fee": 1.0, "vat_percent": 15.0,
    }
    assert rows["STC Pay"]["commission_percent"] == 1.3
    assert rows["بطاقة ائتمانية"]["commission_percent"] == 2.2

    # These rails were not separate labels in the supplied invoices.  Their
    # explicit estimate inherits the observed generic credit-card rule.
    for name in ("Apple Pay", "Google Pay", "Visa", "MasterCard", "بطاقة بنكية"):
        assert rows[name]["commission_percent"] == 2.2
        assert rows[name]["fixed_fee"] == 1.0
        assert rows[name]["vat_percent"] == 15.0


@pytest.mark.parametrize("raw", ["Google Pay", "google_pay", "GooglePay", "جوجل باي", "قوقل باي"])
def test_google_pay_is_a_salla_subrail(raw):
    key, display, parent = normalize_payment_method(raw)
    assert (key, display, parent) == ("google_pay", "Google Pay", "salla")


def test_central_gateway_metrics_use_the_same_unified_defaults():
    rules = _configured_fee_rules({"payment_methods": DEFAULT_PAYMENT_METHODS})

    assert rules["mada"] == {
        "estimated_fee_rate": 1.0,
        "estimated_fixed_fee": 1.0,
        "estimated_vat_rate": 15.0,
    }
    assert rules["stcpay"]["estimated_fee_rate"] == 1.3
    assert rules["credit_card"]["estimated_fee_rate"] == 2.2
    assert rules["googlepay"]["estimated_fee_rate"] == 2.2
    assert resolve_canonical("Google Pay") == "googlepay"
    assert resolve_canonical("Visa") == "visa"
    assert resolve_canonical("MasterCard") == "mastercard"


@pytest.mark.asyncio
async def test_central_metrics_apply_salla_rounding_and_configured_rules(monkeypatch):
    class AsyncRows:
        def __init__(self, rows):
            self._rows = iter(rows)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._rows)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class UnifiedOrders:
        def aggregate(self, _pipeline):
            return AsyncRows([
                {
                    "order_status": "completed", "total_amount": 940.90,
                    "payment_method": "البطاقة الائتمانية",
                    "actual_payment_method": "", "payment_fee_status": "estimated",
                },
                {
                    "order_status": "completed", "total_amount": 127.57,
                    "payment_method": "أس تي سي باي",
                    "actual_payment_method": "", "payment_fee_status": "estimated",
                },
            ])

    class FakeDb:
        unified_orders = UnifiedOrders()

    async def fake_settings(_db, _user_id):
        return {"payment_methods": DEFAULT_PAYMENT_METHODS, "report_included_statuses": []}

    async def fake_policy(_db, _user_id):
        return {}

    monkeypatch.setattr(gateway_metrics, "ensure_user_settings", fake_settings)
    import order_status_policy
    monkeypatch.setattr(order_status_policy, "get_policy_map", fake_policy)

    result = await gateway_metrics.compute_metrics(FakeDb(), "merchant")
    rows = {row["key"]: row for row in result["rows"]}

    assert rows["credit_card"]["fees"] == 21.70
    assert rows["credit_card"]["fees_vat"] == 3.25
    assert rows["stcpay"]["fees"] == 2.66
    assert rows["stcpay"]["fees_vat"] == 0.40


def test_default_migration_updates_only_untouched_legacy_rows():
    current = [
        {"name": "STC Pay", "commission_percent": 2.5, "fixed_fee": 1.0, "vat_percent": 15.0},
        # Merchant edit: must survive even though the new invoice-derived
        # credit-card default is 2.2%.
        {"name": "بطاقة ائتمانية", "commission_percent": 1.5, "fixed_fee": 1.0, "vat_percent": 15.0},
    ]

    migrated, changed = migrate_payment_method_defaults(current, current_version=None)
    by_name = {row["name"]: row for row in migrated}

    assert changed is True
    assert by_name["STC Pay"]["commission_percent"] == 1.3
    assert by_name["بطاقة ائتمانية"]["commission_percent"] == 1.5
    assert by_name["Google Pay"]["commission_percent"] == 2.2
    assert PAYMENT_FEE_DEFAULTS_VERSION == (
        "salla-tamara-tabby-emkan-statements-2026-08-v4"
    )


def test_known_card_rails_do_not_steal_each_others_settings():
    parsed = {
        "payment_methods": [{
            "name": "البطاقة الائتمانية", "orders_count": 1, "total_sales": 1000.0,
        }],
        "shipping_companies": [],
    }
    settings = [
        {"name": "Visa", "commission_percent": 9.0, "fixed_fee": 9.0, "vat_percent": 15.0},
        {"name": "بطاقة ائتمانية", "commission_percent": 2.2, "fixed_fee": 1.0, "vat_percent": 15.0},
    ]

    row = match_settings(parsed, settings, [])["payment_breakdown"][0]

    assert row["matched"] is True
    assert row["commission_percent"] == 2.2
    assert row["base_commission"] == 23.0


def test_salla_rounds_fee_and_vat_per_positive_order():
    parsed = {
        "payment_methods": [
            {"name": "مدى", "orders_count": 2, "total_sales": 0.0},
            {"name": "البطاقة الائتمانية", "orders_count": 1, "total_sales": 940.90},
            {"name": "أس تي سي باي", "orders_count": 1, "total_sales": 127.57},
        ],
        "shipping_companies": [],
        "orders_individual": [
            {"order_number": "M1", "total_amount": 126.57, "payment_method": "مدى"},
            {"order_number": "M1-R", "total_amount": -126.57, "payment_method": "مدى"},
            {"order_number": "C1", "total_amount": 940.90, "payment_method": "البطاقة الائتمانية"},
            {"order_number": "S1", "total_amount": 127.57, "payment_method": "أس تي سي باي"},
        ],
    }

    result = match_settings(parsed, DEFAULT_PAYMENT_METHODS, [])
    rows = {row["name"]: row for row in result["payment_breakdown"]}

    assert rows["مدى"]["base_commission"] == 2.27
    assert rows["مدى"]["vat_amount"] == 0.34
    assert rows["مدى"]["fee_calculation_basis"] == "per_order_salla_rounding"

    # 940.90 × 2.2% + 1 = 21.6998.  Fee displays as 21.70, while VAT is
    # 21.6998 × 15% = 3.25497 → 3.25 (not 21.70 × 15% → 3.26).
    assert rows["البطاقة الائتمانية"]["base_commission"] == 21.70
    assert rows["البطاقة الائتمانية"]["vat_amount"] == 3.25

    assert rows["أس تي سي باي"]["base_commission"] == 2.66
    assert rows["أس تي سي باي"]["vat_amount"] == 0.40
