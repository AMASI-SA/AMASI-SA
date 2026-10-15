"""Independent examples for manual, tax-inclusive Mezan 2 accounting."""
import unittest
from decimal import Decimal

from accounting_sales_tax import TaxError, split_gross, refund_split, select_version


class ManualSalesTaxTests(unittest.TestCase):
    def test_manual_rate_not_source_rate(self):
        result = split_gross("115.00", "15")
        self.assertEqual(result["net"], "100.00")
        self.assertEqual(result["tax"], "15.00")
        self.assertEqual(result["gross"], "115.00")

    def test_missing_is_not_explicit_zero(self):
        with self.assertRaises(TaxError):
            split_gross("115", None)
        self.assertEqual(split_gross("115", "0")["tax"], "0.00")
        self.assertEqual(split_gross("115", "0")["net"], "115.00")

    def test_bad_inputs(self):
        for value in ("NaN", "Infinity", "-1", "", "15.00001", True):
            with self.subTest(value=value), self.assertRaises(TaxError):
                split_gross("115", value)
        for gross in ("0", "-1", "1.001", "NaN", None):
            with self.subTest(gross=gross), self.assertRaises(TaxError):
                split_gross(gross, "15")

    def test_effective_date_and_immutable_original(self):
        versions = [
            {"id": "a", "rate": "15", "effective_at": "2026-09-19T00:00:00+00:00", "revision": 1},
            {"id": "b", "rate": "20", "effective_at": "2026-09-20T00:00:00+00:00", "revision": 2},
        ]
        original = split_gross("115", select_version(versions, "2026-09-19T12:00:00Z")["rate"])
        self.assertEqual(select_version(versions, "2026-09-20T01:00:00Z")["id"], "b")
        self.assertEqual(original["tax"], "15.00")
        with self.assertRaises(TaxError):
            select_version(versions, "2026-09-18T00:00:00Z")
        with self.assertRaises(TaxError):
            select_version(versions, "2026-09-19")

    def test_partial_and_final_refund(self):
        original = split_gross("115", "15")
        first = refund_split(original, [], "57.50")
        final = refund_split(original, [first], "57.50")
        self.assertEqual(first["net"], "50.00")
        self.assertEqual(first["tax"], "7.50")
        self.assertEqual(final, first)
        self.assertEqual(refund_split(original, [], "115"), original)
        with self.assertRaises(TaxError):
            refund_split(original, [first], "57.51")

    def test_rounding_many_small_refunds(self):
        original = split_gross("1.00", "15")
        rows = []
        for _ in range(100):
            rows.append(refund_split(original, rows, "0.01"))
        self.assertEqual(sum(Decimal(r["tax"]) for r in rows), Decimal(original["tax"]))
        self.assertEqual(sum(Decimal(r["net"]) for r in rows), Decimal(original["net"]))
        self.assertTrue(all(Decimal(r["net"]) >= 0 and Decimal(r["tax"]) >= 0 for r in rows))


if __name__ == "__main__":
    unittest.main()
