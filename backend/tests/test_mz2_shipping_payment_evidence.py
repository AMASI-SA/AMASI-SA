"""Behavioral payment tests using the actual export parser and its aliases.

No tests read source text. Apple Pay and Salla refer to their real provider
reference values; this test does not invent new raw payment-method aliases.
"""
from copy import deepcopy
import unittest

from accounting_salla_order_evidence import parse_salla_order_xlsx, _payment_method
from accounting_shipping_payment_evidence import shipping_order_provider
from accounting_shipping_p02 import ShippingAccountingError
from test_mz2_salla_order_evidence import workbook_bytes, source_rows, payment_ref


def parsed(method, reference_provider=None):
    row = deepcopy(source_rows()[0])
    row["طريقة الدفع"] = method
    row["رقم مرجع عملية الدفع"] = payment_ref(reference_provider, "SYN-REF", 180.55) if reference_provider else ""
    result = parse_salla_order_xlsx(workbook_bytes([row]))
    if result["errors"] or len(result["rows"]) != 1:
        raise AssertionError(result)
    return result["rows"][0]


class ShippingPaymentEvidenceTests(unittest.TestCase):
    def test_actual_applepay_reference_on_mada_is_salla(self):
        self.assertEqual(shipping_order_provider(parsed("مدى", "applepay")), "salla")

    def test_actual_mada_reference_is_salla(self):
        self.assertEqual(shipping_order_provider(parsed("مدى", "mada")), "salla")

    def test_actual_card_visa_mastercard_checkout_aliases(self):
        for provider in ("visa", "mastercard", "checkout"):
            with self.subTest(provider=provider):
                self.assertEqual(shipping_order_provider(parsed("البطاقة الإئتمانية", provider)), "salla")

    def test_salla_and_salla_pay_reference_aliases(self):
        for provider in ("salla", "salla_pay"):
            with self.subTest(provider=provider):
                self.assertEqual(shipping_order_provider(parsed("البطاقة الائتمانية", provider)), "salla")

    def test_bank_transfer_actual_export_value_is_non_cod_not_payment_proof(self):
        self.assertEqual(shipping_order_provider(parsed("حوالة بنكيةمصرف الراجحي")), "bank_transfer")

    def test_tabby_actual_export_value(self):
        self.assertEqual(shipping_order_provider(parsed("تابي", "Tabby")), "tabby")

    def test_tamara_actual_export_value(self):
        self.assertEqual(shipping_order_provider(parsed("تمارا", "Tamara")), "tamara")

    def test_emkan_actual_source_aliases(self):
        for method in ("emkaninstallment", "إمكان", "امكان", "imkan"):
            with self.subTest(method=method):
                self.assertEqual(shipping_order_provider(parsed(method, "emkan")), "emkan")

    def test_cod_actual_source_aliases(self):
        for method in ("دفع عند الإستلام", "cod", "cash on delivery"):
            with self.subTest(method=method):
                self.assertEqual(shipping_order_provider(parsed(method)), "cod")

    def test_unknown_method_is_fail_closed(self):
        with self.assertRaisesRegex(ShippingAccountingError, "unproven"):
            shipping_order_provider(parsed("unknown-wallet", "salla"))

    def test_stored_provider_cannot_override_producer_normalizer(self):
        row = parsed("دفع عند الإستلام")
        row["accounting_provider"] = "salla"
        with self.assertRaisesRegex(ShippingAccountingError, "unproven"):
            shipping_order_provider(row)

    def test_literal_apple_pay_not_in_method_contract_is_not_guessed(self):
        self.assertEqual(_payment_method("Apple Pay"), "unknown")
        with self.assertRaisesRegex(ShippingAccountingError, "unproven"):
            shipping_order_provider(parsed("Apple Pay", "applepay"))

    def test_missing_reference_or_mismatched_reference_fails_closed(self):
        for provider in (None, "tabby"):
            with self.subTest(provider=provider), self.assertRaisesRegex(ShippingAccountingError, "reference_provider_conflict"):
                shipping_order_provider(parsed("مدى", provider))
