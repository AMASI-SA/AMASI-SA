import unittest
from datetime import datetime
from unittest.mock import patch

from excel_parser import parse_salla_excel, _match_col, SUBTOTAL_COLS


class Sheet:
    max_row = 2
    max_column = 9

    def iter_rows(self, **kwargs):
        return iter([
            ['رقم الطلب', 'الضريبة', 'مجموع السلة', 'تاريخ الطلب',
             'إجمالي الطلب بالعملة الأصلية', 'عملة الطلب', 'skus_json', 'حالة الطلب'],
            ['123', 9.53, 100, datetime(2026, 8, 28, 9, 9, 17), 128.60,
             'QAR', '[["Fixture",2,"SKU",50,90]]', 'ملغي'],
        ])


class Workbook:
    active = Sheet()
    def close(self):
        pass


class SourceFidelityTests(unittest.TestCase):
    def test_tax_is_never_subtotal(self):
        self.assertIsNone(_match_col(['الضريبة'], SUBTOTAL_COLS))

    def test_full_timestamp_amount_and_items_survive_parser(self):
        with patch('excel_parser.openpyxl.load_workbook', return_value=Workbook()):
            row = parse_salla_excel(b'fixture')['orders_individual'][0]
        self.assertEqual(row['subtotal'], 100)
        self.assertEqual(row['tax'], 9.53)
        self.assertEqual(row['order_date_raw'], '2026-08-28T09:09:17')
        self.assertEqual(row['currency'], 'QAR')
        self.assertEqual(row['total_amount'], 128.60)
        self.assertEqual(row['skus_json'], '[["Fixture",2,"SKU",50,90]]')


if __name__ == '__main__':
    unittest.main()
