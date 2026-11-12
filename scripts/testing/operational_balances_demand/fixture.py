"""Generate synthetic money through the actual report projection; no DB or API."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
from operational_balance_store import digest
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
    for code, reason in [('operational_delivery_timestamp_missing', 'تاريخ التسليم المؤكد غير متوفر.'),
                         ('shipping_identity_or_rate_incomplete', 'تعرفة أجرة الطلب تحتاج مطابقة.')]:
        state['engine']['issues'].append({'code': code, 'message': reason, 'source_id': f'pending-{index}',
            'order_id': f'pending-{index}', 'order_number': str(9001 + index), 'currency': 'SAR',
            'party_type': 'store_driver', 'party_id': 'synthetic-driver'})
report = build_financial_position(state)
report.update(session_scope=digest(['synthetic-owner', 'synthetic-actor']), permissions={'reports': True})
(output / 'fixture.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
print('Synthetic report prepared: three priced deliveries and seven distinct pending orders.')
