"""The order's first Salla delivery survives subsequent sale-status changes."""
import unittest
from datetime import datetime, timezone

from operational_delivery_register import (PREFIX, first_observation, history_delivery, notice,
                                            observe, observed_native_delivery, service_event)

START = datetime(2026, 10, 31, 21, 30, 1, 284707, timezone.utc)
END = datetime(2026, 11, 18, tzinfo=timezone.utc)
CREATED = '2026-11-01T00:00:00+00:00'
CARRIER = {'company_code': 'aramex', 'company_name': 'أرامكس'}
SETUP = {'couriers': [{'courier_key': 'aramex', 'salla_carrier_keys': ['aramex']}]}


class RetainedDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.raw = {'id': '3', 'reference_id': '11', 'status': {'slug': 'delivered', 'name': 'تم التوصيل'},
                    'created_at': CREATED, 'currency': 'QAR'}
        self.source = {'user_id': 'owner', 'salla_shipping_current': CARRIER,
                       'g47_salla_snapshot': {'requires_authoritative_refresh': True}}
        self.facts = {}

    def refresh(self, assignments=(), history=()):
        record = observe(self.source, self.raw, START, END, history, assignments, SETUP, self.facts)
        if record:
            self.facts[PREFIX + record['order_id']] = record
        return record

    def test_delivered_cancelled_returned_and_restored(self):
        first = self.refresh()
        self.assertEqual(first['party_id'], 'aramex')
        self.assertIsNone(service_event(first, self.raw, self.facts)['delivered_at'])
        self.source['salla_shipping_current'] = {'company_name': 'SMSA'}
        for key in ('canceled', 'returned'):
            self.raw['status'] = key
            self.assertEqual(self.refresh()['party_id'], first['party_id'])
            summary = notice(self.facts, 'couriers')
            self.assertEqual((summary['count'], len(summary['changed'])), (1, 1))
            self.assertEqual(summary['changed'][0]['order_number'], '11')
            self.assertEqual(notice(self.facts, 'drivers')['changed'], [])
        for key in ('restoring', 'unlisted_custom_state', 'completed'):
            self.raw['status'] = key
            self.refresh()
            self.assertEqual(notice(self.facts, 'couriers')['changed'], [])
        self.raw['status'] = 'delivered'
        self.refresh()
        self.assertEqual(notice(self.facts, 'couriers')['changed'], [])
        self.assertEqual(len(self.facts), 1)

    def test_alert_has_distinct_counts_for_cancelled_and_returned(self):
        for oid, status in [('1', 'canceled'), ('2', 'restored'), ('3', 'restoring')]:
            self.raw.update(id=oid, reference_id='10' + oid, status='delivered')
            self.refresh()
            self.raw['status'] = status
            self.refresh()
        report = notice(self.facts, 'couriers')
        self.assertEqual(report['count'], 3)
        self.assertEqual([r['order_number'] for r in report['groups']['canceled']], ['101'])
        self.assertEqual([r['order_number'] for r in report['groups']['restored']], ['102'])

    def test_eligibility_uses_creation_clock_only(self):
        for created in (START.isoformat(), '2026-10-31T21:30:01.284706+00:00'):
            self.raw['created_at'] = created
            self.assertIsNone(self.refresh())
        self.raw['created_at'] = '2026-10-31T21:30:01.284708+00:00'
        self.assertIsNotNone(self.refresh())

    def test_cancellation_before_any_delivery(self):
        self.raw['status'] = 'returned'
        self.assertIsNone(self.refresh())
        wrong = [{'order_number': '11', 'provider_order_id': 'different',
                  'native_status_transition': [{'delivered': True}]}]
        self.assertFalse(history_delivery(wrong, '3', '11'))
        self.assertIsNone(self.refresh(history=wrong))

    def test_history_before_first_refresh_and_duplicate_notifications(self):
        self.raw['status'] = {'customized': {'slug': 'restored', 'name': 'مسترجع'}}
        hit = {'order_number': '11', 'provider_order_id': '3', 'native_status_transition': [{'delivered': True}]}
        self.assertEqual(self.refresh(history=[hit, hit])['party_id'], 'aramex')
        self.refresh(history=[hit])
        self.assertEqual(notice(self.facts, 'couriers')['changed'][0]['current_status_name'], 'مسترجع')

    def test_driver_earning_currency_and_original_assignment(self):
        first = {'driver_id': 'one', 'driver_name_snapshot': 'المندوب الأول', 'order_id': '3',
                 'id': 'assignment-1', 'status': 'delivered', 'active': True}
        record = self.refresh([first])
        self.assertEqual((record['party_type'], record['party_id']), ('store_driver', 'one'))
        self.assertEqual(service_event(record, self.raw, self.facts)['currency'], 'QAR')
        self.raw['status'] = 'canceled'
        self.assertEqual(self.refresh([{**first, 'driver_id': 'two'}])['party_id'], 'one')

    def test_ingestion_snapshot_precedes_balance_refresh(self):
        self.source['salla_first_delivered'] = first_observation(self.raw, CARRIER)
        self.source['salla_shipping_current'] = {'company_name': 'SMSA'}
        self.raw['status'] = 'returned'
        self.assertEqual(self.refresh()['party_id'], 'aramex')

    def test_history_uses_status_even_when_the_delivery_has_no_note_or_waybill(self):
        for event in ({'type': 'activity', 'status': 'تم التوصيل', 'note': ''},
                      {'type': 'activity', 'status': 'تم التوصيل', 'note': 'تم التسليم'},
                      {'type': 'activity', 'customized': {'name': 'تم التوصيل'}, 'status': 'completed'}):
            self.assertEqual(observed_native_delivery([event], '3')['order_id'], '3')
        self.assertIsNone(observed_native_delivery([{'type': 'activity', 'status': 'مسترجع', 'note': 'تم التوصيل'}], '3'))
        self.assertIsNone(observed_native_delivery([{'type': 'activity', 'status': 'تم التوصيل', 'order_id': '4'}], '3'))

    def test_missing_party_is_counted(self):
        self.source.pop('salla_shipping_current')
        self.assertFalse(self.refresh().get('party_id'))
        self.assertEqual(notice(self.facts, 'summary')['unassigned'], 1)
        self.source['salla_shipping_current'] = CARRIER
        self.assertEqual(self.refresh()['party_id'], 'aramex')
        self.assertEqual(notice(self.facts, 'summary')['count'], 1)
