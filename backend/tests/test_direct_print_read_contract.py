"""Execute real reader functions with synthetic provider I/O, without DB dependencies."""
import ast
import copy
import os
from pathlib import Path
import unittest
import sys
import types
from unittest.mock import patch
from typing import Any

SOURCE = Path(os.environ.get('SHIPPING_SOURCE', Path(__file__).parents[1] / 'order_engine/shipping_label_service.py'))
NAMES = {'ShippingLabelError', '_text', '_status', '_url', '_tracking', '_snapshot',
         '_active_outbound', '_is_store_courier', '_current_print_shipment', '_print_shipment_rows',
         '_resolve_order', '_read_resolved_order', '_current_courier_source', '_refresh_shipping_label', '_recheck_provider_completed'}
class ProviderError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code

class DirectPrintContract(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tree = ast.parse(SOURCE.read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in NAMES]
        self.calls = []
        self.row = dict(id='shipment', order_id='order', status='created', tracking_number='AWB', label_url='https://labels.invalid/current.pdf')
        self.responses = {'/orders/order/shipments': {'data': [self.row]}}
        async def provider(db, owner, method, path, **kwargs):
            self.assertEqual((owner, method), ('owner', 'GET'))
            self.calls.append(path)
            response = self.responses[path]
            if isinstance(response, Exception):
                raise response
            return copy.deepcopy(response)
        self.ns = dict(Any=Any, SallaError=ProviderError, call_salla=provider,
                       _CANCELLED={'cancelled','canceled','void','deleted'}, _PENDING={'pending','creating','processing'},
                       extract_shipping=lambda value: {}, provider_time=lambda value: value)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(SOURCE), 'exec'), self.ns)
    async def read(self):
        return await self.ns['_print_shipment_rows'](None, 'owner', 'order', direct_print=True)
    async def test_ready_link_is_one_order_scoped_call(self):
        self.assertEqual((await self.read())[0]['label_url'], self.row['label_url'])
        self.assertEqual(self.calls, ['/orders/order/shipments'])
    async def test_refresh_ready_external_uses_two_provider_reads_and_local_fences(self):
        self.responses['/shipments'] = {'data':[self.row]}
        self.responses['/shipments/shipment'] = {'data':self.row}
        self.responses['/orders/order'] = {'data': {'id':'order', 'reference_id':'123', 'status':'completed'}}
        class Collection:
            async def find_one(self, *args):
                return {'raw_by_source':{'salla_direct':{'id':'order'}}}
        db = types.SimpleNamespace(unified_orders=Collection())
        fences=[]
        async def fence(*args, **kwargs):
            fences.append(kwargs)
            return {'shipping':{}, 'assignment':{}}
        async def recover(*args): pass
        self.ns.update(_assert_current_print_status=fence, _order_is_completed=lambda order: order['status']=='completed',
                       _current_outbound=lambda rows, baseline: rows)
        with patch.dict(sys.modules, {'shipping_pending_snapshot_recovery':types.SimpleNamespace(recover_pending_print_snapshot=recover)}):
            result = await self.ns['_refresh_shipping_label'](db, 'owner', '123')
        self.assertTrue(result['ready'])
        self.assertEqual(result['label_url'], self.row['label_url'])
        self.assertEqual(self.calls, ['/orders/order', '/orders/order/shipments'])
        self.assertEqual(len(fences), 2)

    async def test_foreign_order_is_rejected(self):
        self.row['order_id'] = 'other'
        with self.assertRaises(self.ns['ShippingLabelError']): await self.read()
        self.assertEqual(len(self.calls), 1)
    async def test_cancelled_and_return_labels_are_not_opened(self):
        for change in ({'status':'cancelled'}, {'type':'return'}):
            self.responses['/orders/order/shipments'] = {'data': [{**self.row, **change}]}
            self.assertEqual(await self.read(), [])
    async def test_ambiguous_active_shipments_are_not_guessed(self):
        self.responses['/orders/order/shipments']['data'].append({**self.row, 'id':'another'})
        self.assertEqual(await self.read(), [])
    async def test_missing_url_reads_only_same_shipment(self):
        detail = dict(self.row)
        self.row.pop('label_url')
        self.responses['/shipments/shipment'] = {'data': detail}
        self.assertEqual((await self.read())[0]['label_url'], detail['label_url'])
        self.assertEqual(self.calls, ['/orders/order/shipments', '/shipments/shipment'])
    async def test_missing_detail_url_uses_same_shipment_tracking(self):
        self.row.pop('label_url')
        self.responses['/shipments/shipment'] = {'data': self.row}
        self.responses['/shipments/shipment/tracking'] = {'data': {'id':'shipment','label_url':'https://labels.invalid/tracking.pdf'}}
        self.assertTrue((await self.read())[0]['label_url'].endswith('tracking.pdf'))
        self.assertEqual(len(self.calls), 3)
    async def test_provider_failure_not_retried(self):
        self.responses['/orders/order/shipments'] = ProviderError(502)
        with self.assertRaises(ProviderError): await self.read()
        self.assertEqual(len(self.calls), 1)
    async def test_permission_compatibility_uses_scoped_global_query(self):
        self.responses['/orders/order/shipments'] = ProviderError(403)
        self.responses['/shipments'] = {'data':[self.row]}
        self.responses['/shipments/shipment'] = {'data':self.row}
        self.assertEqual((await self.read())[0]['id'], 'shipment')
    async def test_partial_page_not_mistaken_for_current(self):
        self.responses['/orders/order/shipments']['pagination'] = {'totalPages':2}
        self.assertEqual(await self.read(), [])

if __name__ == '__main__': unittest.main()
