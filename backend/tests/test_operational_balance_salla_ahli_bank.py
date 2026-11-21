import unittest

from operational_balance_bank_orders import resolve


class SallaAhliReceiptRoutingTests(unittest.TestCase):
    def setUp(self):
        self.account = dict(user_id='tenant', id='account:ahli', account_type='bank',
                            status='active', currency='SAR', bank_transfer_aliases=['bank_ahli'])

    def resolve(self, label, accounts):
        return resolve({}, 'tenant', 'bank', 'SAR', raw={'bank': {'bank_name': label}}, banks=accounts)

    def test_salla_commercial_bank_names_route_to_the_configured_account(self):
        for label in ('البنك الأهلي التجاري', 'البنك الاهلي التجاري'):
            with self.subTest(label=label):
                self.assertIs(self.resolve(label, [self.account]), self.account)

    def test_commercial_label_requires_one_confirmed_tenant_bank_mapping(self):
        for accounts in ([], [dict(self.account, user_id='other')], [dict(self.account, bank_transfer_aliases=[])],
                         [self.account, dict(self.account, id='account:second')]):
            with self.subTest(accounts=accounts), self.assertRaisesRegex(ValueError, 'binding_incomplete'):
                self.resolve('البنك الأهلي التجاري', accounts)

    def test_similar_name_does_not_select_the_ahli_account(self):
        with self.assertRaisesRegex(ValueError, 'binding_incomplete'):
            self.resolve('بنك تجاري آخر', [self.account])
