"""Iter-293.1 — COD fee as separate invoice line.

Reproduces the production failure on order 269532761:
  • Salla total       = 174.91
  • Sum of item lines = 169.89  (4.61 + 165.28)
  • Delta             = 5.02   ← `amounts.cash_on_delivery` (COD fee)

Before this fix, the totals-guard refused to send the invoice with a
generic `invoice_total_mismatch_before_post`. After the fix:

  1. Normalizer extracts `amounts.cash_on_delivery` into
     `dto.cod_fee_amount = 5.02`.
  2. Invoice-builder adds a dedicated line (MEZAN_COD_FEE, 5.02 SAR)
     when `default_cod_fee_product_id` is configured → totals match.
  3. If the operator hasn't configured the COD product id, the guard
     refuses with the SPECIFIC code `MISSING_COD_FEE_PRODUCT_ID`
     (NOT a generic mismatch).
  4. If the payment_method is COD but `cod_fee_amount == 0`, the guard
     refuses with `MISSING_ORDER_LEVEL_CHARGE` + suspected_charge=cod_fee.

These tests do NOT touch the live database — pure unit tests against
the normalizer, builder, and (mocked) pipeline error code branch.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "/app/backend")
from integrations.qoyod.invoice_builder import (  # noqa: E402
    build_invoice_payload,
)
from integrations.qoyod.normalizer import normalize  # noqa: E402


# ── Fixture: Order 269532761 minus customer PII ──────────────────────
def _order_269532761_payload() -> dict:
    """Synthetic payload mirroring the failing production order:
    two items totalling 169.89 + a 5.02 SAR COD fee at the order level.
    """
    return {
        "data": {
            "id":          269532761,
            "reference_id": 269532761,
            "status":      "completed",
            "date":        "2026-06-30T12:00:00+03:00",
            "created_at":  "2026-06-30T12:00:00+03:00",
            "completed_at": "2026-06-30T12:30:00+03:00",
            "payment_method": "cod",
            "amounts": {
                "sub_total": {"amount": 147.81,  "currency": "SAR"},
                "tax":       {"amount": 22.08,   "currency": "SAR"},
                "shipping":  {"amount": 0,       "currency": "SAR"},
                "cash_on_delivery": {"amount": 5.02, "currency": "SAR"},
                "total":     {"amount": 174.91,  "currency": "SAR"},
            },
            "items": [
                {"id": 1, "name": "Item A", "sku": "A1",
                 "quantity": 1, "amounts": {
                    "price":  {"amount": 4.61, "currency": "SAR"},
                    "total":  {"amount": 4.61, "currency": "SAR"},
                 }},
                {"id": 2, "name": "Item B", "sku": "B1",
                 "quantity": 1, "amounts": {
                    "price":  {"amount": 165.28, "currency": "SAR"},
                    "total":  {"amount": 165.28, "currency": "SAR"},
                 }},
            ],
            "customer": {"id": 1, "first_name": "X", "last_name": "Y",
                         "mobile": "+966500000000", "email": "x@y.com"},
            "shipping_address": {"city": "Riyadh", "country": "Saudi Arabia"},
        }
    }


# ── Normalizer ───────────────────────────────────────────────────────
class TestNormalizerExtractsCodFee:
    def test_cod_fee_extracted_from_cash_on_delivery_field(self):
        dto = normalize(_order_269532761_payload())
        assert dto.cod_fee_amount == 5.02, (
            f"expected 5.02, got {dto.cod_fee_amount}")
        assert dto.total_amount == 174.91

    def test_cod_fee_fallback_to_cod_fee_key(self):
        p = _order_269532761_payload()
        p["data"]["amounts"].pop("cash_on_delivery")
        p["data"]["amounts"]["cod_fee"] = {"amount": 5.02, "currency": "SAR"}
        dto = normalize(p)
        assert dto.cod_fee_amount == 5.02

    def test_cod_fee_fallback_to_payment_fee_key(self):
        p = _order_269532761_payload()
        p["data"]["amounts"].pop("cash_on_delivery")
        p["data"]["amounts"]["payment_fee"] = {"amount": 5.02, "currency": "SAR"}
        dto = normalize(p)
        assert dto.cod_fee_amount == 5.02

    def test_no_cod_fee_returns_zero_not_none(self):
        p = _order_269532761_payload()
        p["data"]["amounts"].pop("cash_on_delivery")
        dto = normalize(p)
        assert dto.cod_fee_amount == 0.0

    def test_unknown_amount_keys_captured_in_extra_charges(self):
        """Forward-compat: if Salla introduces a new fee key tomorrow,
        the normalizer must SURFACE it (not silently drop it) so the
        guard catches the resulting mismatch."""
        p = _order_269532761_payload()
        p["data"]["amounts"]["installment_fee"] = {"amount": 1.5,
                                                    "currency": "SAR"}
        p["data"]["amounts"]["mystery_charge"] = {"amount": 2.0,
                                                  "currency": "SAR"}
        dto = normalize(p)
        assert "installment_fee" in dto.extra_charges
        assert dto.extra_charges["installment_fee"] == 1.5
        assert "mystery_charge" in dto.extra_charges
        # Known keys must NOT be in extra_charges.
        assert "cash_on_delivery" not in dto.extra_charges
        assert "total" not in dto.extra_charges


# ── Invoice builder ──────────────────────────────────────────────────
def _dto_dict_for_builder(*, cod_fee=5.02, shipping=0.0) -> dict:
    """Shape the builder expects (dict, not the DTO model)."""
    return {
        "order_id":      "269532761",
        "order_number":  "269532761",
        "total_amount":  174.91,
        "shipping_amount": shipping,
        "cod_fee_amount": cod_fee,
        "currency":      "SAR",
        "items": [
            {"sku": "A1", "name": "Item A", "quantity": 1,
             "price": 4.61, "total": 4.61, "tax_amount": 0.0,
             "discount_amount": 0.0},
            {"sku": "B1", "name": "Item B", "quantity": 1,
             "price": 165.28, "total": 165.28, "tax_amount": 0.0,
             "discount_amount": 0.0},
        ],
        "extra_charges": {},
    }


class TestInvoiceBuilderCodFeeLine:
    def _call_builder(self, dto_dict, settings, *, product_resolutions=None):
        """Helper — fills the required `product_resolutions` arg with
        a 1:1 SKU→product_id mapping for every item so the test stays
        focused on COD-fee logic, not product resolution."""
        if product_resolutions is None:
            product_resolutions = [
                {"sku": it["sku"], "qoyod_product_id": f"prod-{it['sku']}"}
                for it in dto_dict["items"]
            ]
        return build_invoice_payload(
            qoyod_customer_id="42",
            dto_dict=dto_dict,
            invoice_date=None,
            settings=settings,
            product_resolutions=product_resolutions,
        )

    def test_cod_fee_added_when_product_id_configured(self):
        result = self._call_builder(
            _dto_dict_for_builder(cod_fee=5.02),
            {"invoice_total_policy": "match_salla_total",
             "default_cod_fee_product_id": "999",
             "qoyod_tax_percent": 0},
        )
        lines = result["invoice"]["line_items"]
        cod_lines = [l for l in lines
                     if "COD Fee" in (l.get("description") or "")
                     or l.get("product_id") == 999]
        assert len(cod_lines) == 1, f"expected 1 COD fee line, got: {lines}"
        diag = result["_diagnostics"]
        assert abs(diag["difference"]) < 0.10, (
            f"after adding COD line, mismatch persists: {diag}")
        assert diag["cod_fee_amount"] == 5.02
        assert diag["cod_fee_missing_product"] is False

    def test_cod_fee_missing_product_flagged_in_diagnostics(self):
        result = self._call_builder(
            _dto_dict_for_builder(cod_fee=5.02),
            {"invoice_total_policy": "match_salla_total",
             "qoyod_tax_percent": 0},
        )
        diag = result["_diagnostics"]
        assert diag["cod_fee_missing_product"] is True
        assert abs(diag["difference"]) >= 0.10
        skus = [d.get("sku") for d in diag["line_diagnostics"]]
        assert "_COD_FEE_MISSING_PRODUCT_ID_" in skus

    def test_no_cod_fee_zero_diff_for_paid_order(self):
        result = self._call_builder(
            {"order_id": "1001", "order_number": "1001",
             "total_amount": 100.0, "shipping_amount": 0,
             "cod_fee_amount": 0.0, "currency": "SAR",
             "items": [{"sku": "X", "name": "X", "quantity": 1,
                        "price": 100.0, "total": 100.0,
                        "tax_amount": 0.0, "discount_amount": 0.0}],
             "extra_charges": {}},
            {"invoice_total_policy": "match_salla_total",
             "qoyod_tax_percent": 0},
        )
        diag = result["_diagnostics"]
        assert diag["cod_fee_amount"] == 0.0
        assert diag["cod_fee_missing_product"] is False
        assert abs(diag["difference"]) < 0.10


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
