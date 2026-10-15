"""Synthetic shared calculator tests. SMSA examples are NOT runtime defaults."""
from copy import deepcopy
from decimal import Decimal
import unittest
from courier_cod_fee_rules import (
    calculate_courier_cod_fee, calculate_courier_cod_fee_decimal,
    calculate_courier_charges, split_tax_amount, validate_courier_cod_fee_tiers,
)

SMSA_TIERS = [
    dict(min_amount=50, max_amount=1000, commission_percent=.01, fixed_fee=2, vat_percent=15, vat_included=True),
    dict(min_amount=1000, max_amount=3000, min_inclusive=False, commission_percent=.02, fixed_fee=5, vat_percent=15, vat_included=True),
    dict(min_amount=3000, max_amount=None, min_inclusive=False, commission_percent=.03, fixed_fee=0, vat_percent=15, vat_included=True),
]
SMSA_CONTRACT = dict(commission_vat_inclusive=True, shipping_cost_vat_inclusive=True, cod_fee_tiers=SMSA_TIERS)
BOUNDARIES = [
    ('50', '2.17', '0.33', '2.50'),
    ('1000', '10.43', '1.57', '12.00'),
    ('1000.01', '21.74', '3.26', '25.00'),
    ('3000', '56.52', '8.48', '65.00'),
    ('3000.01', '78.26', '11.74', '90.00'),
]


class ShippingCalculatorTests(unittest.TestCase):
    def test_49_99_needs_review(self):
        self.assertTrue(calculate_courier_cod_fee_decimal('49.99', SMSA_CONTRACT)['needs_review'])

    def test_all_boundaries_inclusive_tax_exact_decimal_and_no_double_tax(self):
        for amount, net, vat, gross in BOUNDARIES:
            with self.subTest(amount=amount):
                result = calculate_courier_cod_fee_decimal(amount, SMSA_CONTRACT)
                self.assertEqual([result[k] for k in ('fee_net', 'fee_vat', 'fee_total')],
                                 list(map(Decimal, (net, vat, gross))))
                self.assertEqual(result['fee_net'] + result['fee_vat'], result['fee_total'])
                self.assertIsInstance(result['fee_net'], Decimal)
                self.assertEqual(result['fee_total'], Decimal(gross))
                self.assertEqual(calculate_courier_cod_fee(amount, SMSA_CONTRACT)['fee_total'], float(gross))

    def test_overlap_rejected_gap_reviewed_and_empty_tier_rejected(self):
        tiers = deepcopy(SMSA_TIERS); tiers[1]['min_inclusive'] = True
        with self.assertRaises(ValueError):
            validate_courier_cod_fee_tiers(tiers)
        tiers[0]['max_inclusive'] = False; tiers[1]['min_inclusive'] = False
        self.assertTrue(calculate_courier_cod_fee(1000, {**SMSA_CONTRACT, 'cod_fee_tiers': tiers})['needs_review'])
        with self.assertRaises(ValueError):
            validate_courier_cod_fee_tiers([dict(min_amount=50, max_amount=50, min_inclusive=False, commission_percent=0)])

    def test_unlimited_tier_count(self):
        tiers = [dict(min_amount=i, max_amount=i+1, max_inclusive=False, commission_percent=.01) for i in range(2500)]
        self.assertEqual(len(validate_courier_cod_fee_tiers(tiers)), 2500)

    def test_shipping_17_25_is_15_plus_2_25_and_commission_12_stays_12(self):
        result = calculate_courier_charges('17.25', 15, cod_amount=1000, contract=SMSA_CONTRACT)
        keys = ('shipping_net', 'shipping_vat', 'shipping_gross', 'cod_commission', 'cod_commission_vat', 'cod_commission_gross', 'payable_total')
        self.assertEqual([result[k] for k in keys], list(map(Decimal, ('15', '2.25', '17.25', '10.43', '1.57', '12', '29.25'))))

    def test_iMile_and_other_carriers_never_inherit_smsa_policy(self):
        other = dict(name='iMile', shipping_cost_vat_inclusive=True, commission_vat_inclusive=False,
                     cod_fee_tiers=[dict(min_amount=0, max_amount=None, commission_percent=.01, fixed_fee=2, vat_percent=15)])
        result = calculate_courier_charges('17.25', 15, cod_amount=1000, contract=other)
        self.assertEqual(result['shipping_gross'], Decimal('17.25'))
        self.assertEqual(result['cod_commission_gross'], Decimal('13.80'))
        self.assertEqual(calculate_courier_charges('17.25', 15, cod_amount=1000, contract=SMSA_CONTRACT)['cod_commission_gross'], Decimal('12'))
        for name in ('SMSA', 'iMile', 'Other'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                calculate_courier_charges('17.25', 15, cod_amount=1000, contract={'name': name})

    def test_shipping_and_commission_flags_are_independent(self):
        result = calculate_courier_charges('15', 15, cod_amount=1000,
            contract={**SMSA_CONTRACT, 'shipping_cost_vat_inclusive': False})
        self.assertEqual(result['shipping_gross'], Decimal('17.25'))
        self.assertEqual(result['cod_commission_gross'], Decimal('12.00'))

    def test_conflicting_contract_tier_tax_flags_fail_closed(self):
        with self.assertRaises(ValueError):
            calculate_courier_cod_fee_decimal(1000, {**SMSA_CONTRACT, 'commission_vat_inclusive': False})

    def test_prepaid_has_no_cod_even_without_tiers(self):
        result = calculate_courier_charges('17.25', 15, cod_amount=None, contract=SMSA_CONTRACT)
        self.assertEqual((result['cod_gross'], result['cod_commission'], result['payable_total']),
                         (Decimal(0), Decimal(0), Decimal('17.25')))
        self.assertFalse(result['needs_review'])
        self.assertTrue(calculate_courier_charges('17.25', 15, cod_amount=1000,
            contract={**SMSA_CONTRACT, 'cod_fee_tiers': []})['needs_review'])

    def test_driver_20_at_explicit_zero_tax_never_gets_vat(self):
        result = calculate_courier_charges(20, 0, cod_amount=None,
            contract={'shipping_cost_vat_inclusive': False, 'commission_vat_inclusive': False})
        self.assertEqual((result['shipping_net'], result['shipping_vat'], result['payable_total']), (Decimal(20), Decimal(0), Decimal(20)))

    def test_all_small_inclusive_amounts_preserve_halala_identity(self):
        for halalas in range(1, 20001):
            gross = Decimal(halalas) / 100
            result = split_tax_amount(gross, 15, inclusive=True)
            self.assertEqual(result['net'] + result['vat'], gross)
            self.assertEqual(result['gross'], gross)
            self.assertEqual(result['vat'].quantize(Decimal('.01')), result['vat'])

    def test_nonfinite_and_negative_inputs_fail_closed(self):
        for value in ('NaN', 'Infinity', '-Infinity', -1, None):
            with self.subTest(amount=value), self.assertRaises(ValueError):
                calculate_courier_cod_fee_decimal(value, SMSA_CONTRACT)
        for field in ('min_amount', 'max_amount', 'commission_percent', 'fixed_fee', 'vat_percent'):
            tier = deepcopy(SMSA_TIERS[0]); tier[field] = float('inf')
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_courier_cod_fee_tiers([tier])

    def test_legacy_flat_caller_keeps_shared_math_not_smsa_defaults(self):
        result = calculate_courier_cod_fee(1000, dict(cod_fee_percent=.01, cod_fee_fixed_per_order=2, cod_fee_vat_percent=15))
        self.assertEqual((result['fee_net'], result['fee_vat']), (12, 1.8))
        result = calculate_courier_cod_fee(1000, dict(cod_fee_percent=.01, cod_fee_fixed_per_order=2, cod_fee_vat_percent=15, cod_fee_vat_included=True))
        self.assertEqual((result['fee_net'], result['fee_vat'], result['fee_total']), (10.43, 1.57, 12))


if __name__ == '__main__':
    unittest.main()
