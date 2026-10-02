"""Summarize completed original runner output; no application/database mutation."""
from pathlib import Path
from collections import Counter
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
p = Path(sys.argv[1]).resolve() if len(sys.argv) == 2 else ROOT / 'backend-88cc9131'
before = json.loads((p / 'started.json').read_text())
after = json.loads((p / 'finished.json').read_text())
assert after['exit_code'] == 0 and after['source_unchanged']
assert before['source_before'] == after['source_after']
assert before['head'] == after['head'] == '88cc9131027fd6783a46e2e00fc6aaec4788b8fa'
xml = ET.parse(p / 'backend.xml').getroot()
cases = []
for row in xml.iter('testcase'):
    status = 'failed' if row.find('failure') is not None else 'error' if row.find('error') is not None else 'skipped' if row.find('skipped') is not None else 'passed'
    module = row.attrib['classname'].split('.')[2]
    cases.append({'module': module, 'class': row.attrib['classname'], 'name': row.attrib['name'], 'status': status})
assert all(c['status'] == 'passed' for c in cases)
selection = json.loads((p / 'selection.json').read_text())
expected_modules = {Path(x).stem for x in selection}
observed = {c['module'] for c in cases}
assert expected_modules == observed, (expected_modules - observed, observed - expected_modules)
assert len(selection) == len(set(selection)) == 147
log = (p / 'backend.log').read_text(encoding='utf-8')
tail = next(line.strip() for line in reversed(log.splitlines()) if re.search(r'\d+ passed', line))
domains = {
    'Supplier native invoice/payment': ['supplier_native_invoice_v2', 'mz2_supplier_payments_v2', 'mz2_supplier_financial_port_integration', 'g47_supplier_payment'],
    'Employee/payroll': ['employee_payroll', 'employee_salary', 'mz2_employee_finance', 'mz2_employee_outgoing'],
    'Shipping/COD/POS': ['mz2_shipping', 'mz2_driver', 'mz2_optional_delivery', 'mz2_late_delivery', 'mz2_receipt_resubmission', 'store_delivery_accounting'],
    'Advertising': ['mz2_advertising', 'mz2_bank_evidence_adapters', 'ad_daily_close'],
    'Bank transfer': ['mz2_bank_transfer', 'mz2_bank_cod', 'mz2_driver_bank', 'mz2_bank_evidence_adapters'],
    'Customer advances/refunds': ['mz2_customer_advances', 'mz2_daily_refunds', 'mz2_refund_entitlements', 'mz2_order_refunds'],
    'Payment providers': ['mz2_settlement', 'mz2_settlements', 'mz2_tabby', 'mz2_recognition', 'mz2_order_recognition'],
    'Daily movements': ['mz2_daily_movements', 'mz2_daily_refunds'],
    'G47/physical inventory contract': ['g47_'],
    'Opening/activation internal engine only': ['financial_accounts_real_mongo'],
    'Public opening quarantine/onboarding': ['accounting_opening_quarantine', 'accounting_onboarding', 'legacy_opening_quarantine'],
    'Native SSOT/report/identity/control': ['track_g_native_reports', 'qoyod_runtime_ssot', 'onboarding_ssot', 'mz2_report_isolation', 'mz2_write_control', 'mz2_write_balance', 'mz2_financial_identity', 'financial_ledger_identity'],
}
matrix = []
for domain, patterns in domains.items():
    selected = [c for c in cases if any(pattern in c['module'] for pattern in patterns)]
    assert selected, domain
    matrix.append({'domain': domain, 'status': 'PASS_TECHNICAL_CONTRACT', 'junit_case_records': len(selected),
                   'modules': sorted({c['module'] for c in selected}), 'cases': selected})
summary = {'head': after['head'], 'tree': after['tree'], 'status': 'PASS_NATIVE_INTEGRATION_BASELINE',
           'pytest_summary': tail, 'selected_files': len(selection), 'observed_files': len(observed),
           'junit_parent_case_records': len(cases), 'counts': dict(Counter(c['status'] for c in cases)),
           'junit_reported_test_count': sum(int(s.attrib['tests']) for s in xml.iter('testsuite')),
           'pytest_subtests_passed': int(re.search(r'(\d+) subtests passed', tail).group(1)),
           'elapsed_seconds': after['elapsed_seconds'], 'source_unchanged': True,
           'junit_sha256': hashlib.sha256((p / 'backend.xml').read_bytes()).hexdigest(),
           'limits': ['147-file native integration baseline, not all historical backend tests',
                      'Synthetic isolated contract/regression evidence, not full final Business UAT',
                      'Internal opening/transition fixture remounts private engines; shipped public gates remain locked'],
           'production_financial_writes_by_this_task': 0}
(p / 'verified-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
(p / 'domain-case-matrix.json').write_text(json.dumps({'head': after['head'], 'overlapping_domains_not_additive': True, 'domains': matrix}, indent=2) + '\n')
print(json.dumps(summary))
