"""Iter-293.2 — Preflight must NOT block COD orders for missing account.

Reproduces the production bug from order 269547100:
  • payment_method = cod
  • cod_fee_amount = 0 (Salla didn't send the fee for this order)
  • payment_method_mapping does NOT contain a 'cod' row
  • Order failed preflight with `payment_method_mapping_missing`

After fix, preflight resolves posting_mode FIRST, and SKIPS the account
check for any row whose effective mode is credit_invoice_only or
disabled (or whose method is in the COD family — defense in depth).
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "/app/backend")
from integrations.qoyod.preflight import run as preflight_run  # noqa: E402


def _base_settings(**overrides) -> dict:
    s = {
        "qoyod_api_key":           "fake_key",
        "qoyod_base_url":          "https://api.qoyod.example",
        "default_inventory_id":    "1",
        "default_unit_type_id":    "1",
        "tax_mode":                "customer_first",
        "invoice_trigger_statuses": ["completed"],
        # NO 'cod' row in payment_method_mapping — that's the whole point.
        "payment_method_mapping":  [
            {"salla_method": "mada", "qoyod_account_id": "17",
             "posting_mode": "paid_receipt"},
        ],
    }
    s.update(overrides)
    return s


def _base_dto(**overrides) -> dict:
    d = {
        "order_id":      "269547100",
        "order_number":  "269547100",
        "order_status":  "completed",
        "payment_method": "cod",
        "currency":      "SAR",
        "total_amount":  213.78,
        "items": [
            {"sku": "A", "name": "A", "quantity": 1,
             "price": 5.13, "total": 5.13, "tax_amount": 0.0,
             "discount_amount": 0.0},
            {"sku": "B", "name": "B", "quantity": 1,
             "price": 183.65, "total": 183.65, "tax_amount": 0.0,
             "discount_amount": 0.0},
        ],
        "shipping_amount": 23.15,
        "cod_fee_amount": 0.0,
        "cod_fee_source_path": None,
        "cod_fee_source_type": None,
        "extra_charges": {},
        "customer": {"name": "X", "phone": "+966500000000",
                     "email": "x@y.com"},
    }
    d.update(overrides)
    return d


def _resolutions(dto):
    """Stub product resolutions matching every item in the dto so the
    products check passes — the test is about payment_method, not
    products."""
    out = []
    for it in dto["items"]:
        sku = it["sku"]
        out.append({"sku": sku, "qoyod_product_id": "prod-" + sku})
    return out


def _run(dto, settings):
    return preflight_run(
        dto_dict=dto,
        settings=settings,
        qoyod_customer_id="cust-1",
        product_resolutions=_resolutions(dto),
        existing_invoice_row=None,
    )


class TestCodPreflight:
    def test_cod_without_account_passes_preflight(self):
        """The bug from production: COD + no 'cod' row in mapping +
        no cod_fee must NOT fail preflight on payment_method check."""
        pf = _run(_base_dto(), _base_settings())
        failures = getattr(pf, "failures", None) or []
        for f in failures:
            assert f.get("code") != "payment_method_mapping_missing", (
                f"COD wrongly blocked by preflight: {f}")

    def test_cod_with_explicit_mapping_still_passes(self):
        settings = _base_settings(payment_method_mapping=[
            {"salla_method": "mada", "qoyod_account_id": "17"},
            {"salla_method": "cod",  "qoyod_account_id": None,
             "posting_mode": "credit_invoice_only"},
        ])
        pf = _run(_base_dto(), settings)
        failures = getattr(pf, "failures", None) or []
        for f in failures:
            assert f.get("code") != "payment_method_mapping_missing", (
                f"COD with explicit mapping wrongly blocked: {f}")

    def test_cod_arabic_payment_method_passes(self):
        pf = _run(_base_dto(payment_method="الدفع عند الاستلام"),
                  _base_settings())
        failures = getattr(pf, "failures", None) or []
        for f in failures:
            assert f.get("code") != "payment_method_mapping_missing", (
                f"Arabic COD wrongly blocked: {f}")

    def test_non_cod_without_mapping_still_fails(self):
        """Regression: paid methods (e.g. apple_pay when not mapped)
        must STILL be caught by the missing-mapping check."""
        pf = _run(_base_dto(payment_method="apple_pay"), _base_settings())
        failures = getattr(pf, "failures", None) or []
        codes = [f.get("code") for f in failures]
        assert "payment_method_mapping_missing" in codes, (
            f"non-COD missing-mapping no longer caught: {failures}")

    def test_disabled_posting_mode_skips_account_check(self):
        settings = _base_settings(payment_method_mapping=[
            {"salla_method": "mada", "qoyod_account_id": "17"},
            {"salla_method": "tamara", "posting_mode": "disabled"},
        ])
        pf = _run(_base_dto(payment_method="tamara"), settings)
        failures = getattr(pf, "failures", None) or []
        for f in failures:
            assert f.get("code") != "payment_method_mapping_missing", (
                f"disabled-mode method wrongly required an account: {f}")


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
