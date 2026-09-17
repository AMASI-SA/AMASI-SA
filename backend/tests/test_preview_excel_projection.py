import os
import unittest
from copy import deepcopy
from unittest.mock import patch

from order_engine.excel_projection import map_excel_order, discovery_source_query
from order_engine.mapper import OrderMappingError
from order_engine.repository import MongoOrderRepository
from order_engine.service import _map_discovery_row


def record():
    return {'order_number': '123', 'order_id': '456', 'order_date': '2026-08-28',
            'order_date_raw': '2026-08-28 09:09:17', 'currency': 'QAR',
            'total_amount': 128.60, 'order_status': 'ملغي', 'customer_name': 'Fixture',
            'raw_by_source': {'excel': {'order_number': '123', 'currency': 'QAR'}}}


class ExcelProjectionTests(unittest.TestCase):
    def test_original_currency_date_status_and_unknowns(self):
        row = record(); original = deepcopy(row)
        dto = map_excel_order(row)
        self.assertEqual(dto.totals.currency, 'QAR')
        self.assertEqual(dto.totals.total, 128.6)
        self.assertEqual(dto.created_at.isoformat(), '2026-08-28T09:09:17+03:00')
        self.assertEqual(dto.status_native, 'ملغي')
        self.assertEqual(dto.source.source_event, 'excel.import')
        self.assertEqual(dto.items, [])
        self.assertIsNone(dto.totals.total_sar)
        self.assertEqual(dto.payment.collection_status, 'unknown')
        self.assertEqual(row, original)

    def test_opt_in_and_explicit_excel_path(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn('$or', discovery_source_query())
            self.assertIsNone(MongoOrderRepository._to_discovery_row(record()))
        with patch.dict(os.environ, {'MEZAN_PREVIEW_EXCEL_ORDERS': '1'}):
            row = MongoOrderRepository._to_discovery_row(record())
            self.assertEqual(row.salla_raw, {})
            self.assertEqual(_map_discovery_row(row).order_number, '123')
            self.assertIn('$or', discovery_source_query())

    def test_quoted_original_date_keeps_original_time(self):
        row = record(); row['order_date_raw'] = "'2026-06-05 00:07:29'"
        self.assertEqual(map_excel_order(row).created_at.isoformat(), '2026-06-05T00:07:29+03:00')

    def test_invalid_and_simulated_do_not_become_real_orders(self):
        for change in ({'currency': ''}, {'total_amount': 'NaN'}, {'order_date_raw': 'bad'}):
            row = record(); row.update(change)
            with self.assertRaises(OrderMappingError): map_excel_order(row)
        row = record(); row['raw_by_source']['excel']['simulated'] = 'fixture'
        with self.assertRaises(OrderMappingError): map_excel_order(row)


if __name__ == '__main__':
    unittest.main()
