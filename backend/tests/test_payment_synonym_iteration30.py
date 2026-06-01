"""Iteration 30 — Payment-method synonym matching (cross-language).

Reproduces the real merchant bug:
"بطاقة رسوم بوابة الدفع عدا تابي وتمارا وامكان في لوحة التحكم تظهر القيمه
صفر لا يتم احتساب الرسوم وخصمها من بطاقة صافي المدفوعات الإلكترونية".

Root cause: merchant's `payment_methods` settings use Arabic names
(e.g. "مدى", "البطاقة الإئتمانية") but Salla webhooks (or Excel exports)
deliver English / variant spellings (e.g. "Mada", "credit card",
"Visa/MasterCard"). The old `normalize_name` only lowercased + stripped
diacritics, so cross-language pairs never matched → fee_amount = 0 for
every gateway except Tabby/Tamara/Emkan (those happened to have the
exact same name in both places).

Fix: `_payment_synonym_match` resolves both sides through a
bidirectional synonym table, plus a unified Arabic-letter normaliser
that collapses أ/إ/آ→ا, ى→ي, ة→ه, ؤ→و, ئ→ي.

Run:
  pytest /app/backend/tests/test_payment_synonym_iteration30.py -v
"""
from __future__ import annotations

from excel_parser import match_settings, normalize_name, _payment_synonym_match


def _build_parsed(method_name: str, sales: float, orders: int = 5):
    """Minimal parsed dict for match_settings()."""
    return {
        "payment_methods": [{
            "name": method_name,
            "orders_count": orders,
            "total_sales": sales,
        }],
        "shipping_companies": [],
    }


# ──────────────────────────────────────────────────────────────────────────
class TestNormalizeName:

    def test_arabic_letter_unification(self):
        # All variants should collapse to the same key.
        for v in ("الإئتمانية", "الائتمانية", "الإئتمانيه", "الائتمانيه"):
            assert normalize_name(v) == normalize_name("الائتمانيه")

    def test_lowercase_and_strip(self):
        assert normalize_name("  APPLE PAY  ") == "apple pay"

    def test_diacritics_removed(self):
        assert normalize_name("مَدى") == normalize_name("مدى")


# ──────────────────────────────────────────────────────────────────────────
class TestSynonymMatcher:

    def test_mada_cross_language(self):
        a = normalize_name("مدى")
        b = normalize_name("Mada")
        assert _payment_synonym_match(a, b)
        assert _payment_synonym_match(b, a)

    def test_credit_card_variants(self):
        ar = normalize_name("البطاقة الإئتمانية")
        en1 = normalize_name("credit card")
        en2 = normalize_name("Visa/MasterCard")
        en3 = normalize_name("visa")
        assert _payment_synonym_match(ar, en1)
        assert _payment_synonym_match(ar, en2)
        assert _payment_synonym_match(ar, en3)

    def test_apple_pay_variants(self):
        s = normalize_name("Apple Pay")
        o1 = normalize_name("apple pay")
        o2 = normalize_name("ابل باي")
        o3 = normalize_name("ApplePay")
        assert _payment_synonym_match(s, o1)
        assert _payment_synonym_match(s, o2)
        assert _payment_synonym_match(s, o3)

    def test_emkan_with_install_suffix(self):
        # Merchant has settings "EmkanInstallment", Salla sends "إمكان"
        s = normalize_name("EmkanInstallment")
        o = normalize_name("إمكان")
        assert _payment_synonym_match(s, o)
        assert _payment_synonym_match(o, s)

    def test_stc_pay_variants(self):
        s = normalize_name("STC Pay")
        for o in ("stcpay", "STCPay", "stc pay"):
            assert _payment_synonym_match(s, normalize_name(o))

    def test_cod_variants(self):
        s = normalize_name("الدفع عند الاستلام")
        for o in ("Cash on Delivery", "COD", "cash_on_delivery"):
            assert _payment_synonym_match(s, normalize_name(o))

    def test_unrelated_gateways_do_not_match(self):
        # Tabby ≠ Mada (different synonym groups)
        assert not _payment_synonym_match(normalize_name("تابي"),
                                          normalize_name("Mada"))


# ──────────────────────────────────────────────────────────────────────────
class TestEndToEndFeeMatching:
    """Iteration 30 acceptance: with the merchant's actual settings,
    every common Salla payment-method name maps to a non-zero fee.
    """

    # Mirrors the merchant's real settings (from preview DB diagnostics).
    USER_SETTINGS = [
        {"name": "مدى", "commission_percent": 1.0, "fixed_fee": 1.0, "vat_percent": 15.0},
        {"name": "تمارا", "commission_percent": 6.99, "fixed_fee": 1.5, "vat_percent": 15.0},
        {"name": "تابي", "commission_percent": 6.99, "fixed_fee": 1.5, "vat_percent": 15.0},
        {"name": "البطاقة الإئتمانية", "commission_percent": 1.5, "fixed_fee": 1.0, "vat_percent": 15.0},
        {"name": "الدفع عند الاستلام", "commission_percent": 0.0, "fixed_fee": 0.0, "vat_percent": 0.0},
        {"name": "STC Pay", "commission_percent": 1.3, "fixed_fee": 1.0, "vat_percent": 15.0},
        {"name": "EmkanInstallment", "commission_percent": 99.6, "fixed_fee": 5.1, "vat_percent": 15.0},
        {"name": "Apple Pay", "commission_percent": 0.0, "fixed_fee": 1.5, "vat_percent": 15.0},
    ]

    def _run(self, method_name: str, sales: float = 1000.0):
        result = match_settings(
            _build_parsed(method_name, sales),
            self.USER_SETTINGS, [],
        )
        return result["payment_breakdown"][0]

    def test_mada_english_label_matches(self):
        """Salla → 'Mada'  vs settings → 'مدى'  ➜ must match."""
        row = self._run("Mada", 1000)
        assert row["matched"] is True
        # 1.0% + 5 fixed = 10 + 5 = 15 base ; vat 15% → 17.25
        assert row["commission_percent"] == 1.0
        assert row["fee_amount"] > 0

    def test_visa_mastercard_matches_credit_card_setting(self):
        """Salla → 'Visa/MasterCard'  vs settings → 'البطاقة الإئتمانية'."""
        row = self._run("Visa/MasterCard", 2000)
        assert row["matched"] is True
        assert row["commission_percent"] == 1.5
        assert row["fee_amount"] > 0

    def test_apple_pay_matches(self):
        """Salla → 'apple pay'  vs settings → 'Apple Pay'."""
        row = self._run("apple pay", 500)
        assert row["matched"] is True
        # 0% commission + 1.5 fixed * 5 orders = 7.5 + 15% vat = 8.625
        assert row["fee_amount"] > 0

    def test_stc_pay_no_space(self):
        """Salla → 'stcpay'  vs settings → 'STC Pay'."""
        row = self._run("stcpay", 800)
        assert row["matched"] is True
        assert row["commission_percent"] == 1.3

    def test_emkan_short_form_matches_installment_setting(self):
        """Salla → 'إمكان'  vs settings → 'EmkanInstallment'."""
        row = self._run("إمكان", 1500)
        assert row["matched"] is True
        assert row["commission_percent"] == 99.6  # merchant's value

    def test_credit_card_arabic_variant_with_alif_hamza(self):
        """Salla → 'بطاقة ائتمانية'  vs settings → 'البطاقة الإئتمانية'.
        Note the differing 'ال' prefix and أ/ا/إ/ائ variants — these
        are unified by the new normalize_name logic."""
        row = self._run("بطاقة ائتمانية", 1200)
        assert row["matched"] is True
        assert row["commission_percent"] == 1.5

    def test_unknown_gateway_still_unmatched(self):
        """Sanity: a truly unknown gateway still falls through with 0 fee."""
        row = self._run("Crypto-Pay-XYZ", 100)
        assert row["matched"] is False
        assert row["fee_amount"] == 0.0

    def test_tabby_still_matches(self):
        """Regression: don't break the gateways that already worked."""
        row = self._run("Tabby", 1000)
        assert row["matched"] is True
        assert row["commission_percent"] == 6.99
