"""Generate synthetic money through the actual report projection; no DB or API."""
import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
from operational_balance_store import digest
from operational_delivery_confirmation import BASIS, EVENT_SCHEMA, PREFIX, record_confirmation
from operational_financial_position import build_financial_position
from test_operational_balance_financial_position import opening, obligation
from test_operational_balance_provider_known_subtotals import priced_state, add_order

output = Path(sys.argv[1])
assert output.is_absolute()
output.mkdir(parents=True, exist_ok=True)
state = priced_state()
opening(state, 'test-bank', 'bank', '1200.00')
add_order(state, 'pending-fee', paid='50.00', method='unknown')
for index in range(3):
    obligation(state, f'shipping:known-{index}', 'shipping', 'store_driver', '25.00',
               party='synthetic-driver', name='مندوب تجريبي')
for index in range(7):
    reasons = [('operational_delivery_timestamp_missing', 'تاريخ التسليم المؤكد غير متوفر.')]
    if index:
        reasons.append(('shipping_identity_or_rate_incomplete', 'تعرفة أجرة الطلب تحتاج مطابقة.'))
    for code, reason in reasons:
        state['engine']['issues'].append({'code': code, 'message': reason, 'source_id': f'pending-{index}',
            'order_id': f'pending-{index}', 'order_number': str(9001 + index), 'currency': 'SAR',
            'party_type': 'store_driver', 'party_id': 'synthetic-driver'})
report = build_financial_position(state)
report.update(session_scope=digest(['synthetic-owner', 'synthetic-owner']),
              permissions={'reports': True, 'confirm_delivery_after_start': True})
(output / 'fixture.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')

# Both subsequent reports use the real proof factory and financial projection.
# This in-memory state fixture does not substitute for the real-Mongo writer tests.
confirmation_time = (datetime.fromisoformat(state['engine']['as_of'].replace('Z', '+00:00'))
                     + timedelta(minutes=1)).isoformat()
created = (datetime.fromisoformat(state['started_at'].replace('Z', '+00:00'))
           - timedelta(days=1)).isoformat()
event = {'schema': EVENT_SCHEMA, 'recognition_basis': BASIS, 'id': 'store-delivery:synthetic-assignment',
         'order_id': 'pending-0', 'order_number': '9001', 'order_created_at': created,
         'party_type': 'store_driver', 'party_id': 'synthetic-driver', 'currency': 'SAR',
         'assignment_id': 'synthetic-assignment', 'delivered_at': None, 'recorded_at': confirmation_time}
record = record_confirmation(state, event, actor=state['owner_id'], actor_name='مالك تجريبي',
                             confirmed_at=confirmation_time, source_hash=digest(['synthetic-source']),
                             preview_hash='a' * 64)
event['recorded_at'] = record['confirmed_at']
pending_state = deepcopy(state)
pending_state['engine']['facts'][PREFIX + 'pending-0'] = record
pending = build_financial_position(pending_state)
pending.update(session_scope=report['session_scope'], permissions=report['permissions'])
(output / 'fixture-confirmation-pending.json').write_text(json.dumps(pending, ensure_ascii=False), encoding='utf-8')

confirmed = deepcopy(pending_state)
confirmed['engine']['issues'] = [issue for issue in confirmed['engine']['issues']
                                if issue.get('order_id') != 'pending-0']
key = 'shipping:pending-0:base_shipping'
obligation(confirmed, key, 'shipping', 'store_driver', '25.00',
           party='synthetic-driver', name='مندوب تجريبي')
confirmed['engine']['obligations'][key].update(order_id='pending-0', order_number='9001')
confirmed['engine']['facts']['delivery_event:pending-0'] = {
    **event, 'confirmation_id': record['id'], 'confirmation_seal': record['seal']}
confirmed['engine']['facts']['shipping:pending-0'] = {
    'id': 'synthetic-driver', 'party_type': 'store_driver', 'cost': '25.00', 'currency': 'SAR',
    'delivered_at': None, 'recognition_basis': BASIS, 'recorded_at': record['confirmed_at'],
    'business_date_basis': 'recognition_recorded_at'}
confirmed['engine']['as_of'] = confirmation_time
after = build_financial_position(confirmed)
after.update(session_scope=report['session_scope'], permissions=report['permissions'])
(output / 'fixture-confirmed.json').write_text(json.dumps(after, ensure_ascii=False), encoding='utf-8')

print('Synthetic report prepared: three priced deliveries and seven distinct pending orders.')
