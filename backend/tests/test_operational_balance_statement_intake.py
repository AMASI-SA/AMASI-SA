"""Synthetic XLSX source contracts: no customer or merchant records."""
from io import BytesIO

import openpyxl
import pytest

from operational_provider_statement_intake import normalize_statement


TABBY = ['Order Number', 'Sale/Refund Date', 'Merchant Name', 'Merchant Code',
         'Product Type', 'Type', 'Currency', 'Order Amount', 'Commission Rate',
         'Refundable Commission', 'Non Refundable Commission', 'Fixed Fee',
         'Total Fee', 'VAT Amount', 'VAT Rate', 'Total Deduction',
         'Transferred amount', 'Transfer Date']
TAMARA = ['Transaction Date', 'Tamara Order ID', 'Merchant Order ID', 'Refund Reason',
          'Payment Type', 'Order Status', 'Currency', 'Order Amount', 'Event',
          'Event Amount', 'Event Date', 'Tamara Fixed Fees', 'Tamara Variable Fees %',
          'Tamara Variable Fees', 'Total Fees', 'VAT Collected by Tamara',
          'Total Payable to Merchant', 'Installments']
SALLA = ['رقم الطلب', 'إجمالي الطلب (ر.س)', 'طريقة الدفع', 'الرسوم (ر.س)',
         'المُستحق قبل الضريبة (ر.س)', 'الضريبة', 'المُستحق بعد الضريبة (ر.س)']


def sale(**patch):
    values = dict(zip(TABBY, ['1001', '2026-11-02', 'Synthetic', 'sample-account',
        'Installments: 3 Months', 'sale', 'SAR', 100, 6.99, 4.99, 2, 1,
        7.99, 1.20, .15, 9.19, 90.81, '2026-11-09']))
    values.update(patch)
    return [values.get(k) for k in TABBY]


def workbook(provider='tabby', rows=None, headers=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    if provider == 'tabby':
        ws.title = 'SR'
        ws.append(['Statement #', 'SYNTHETIC-WEEK-1'])
        ws.append(['Date', '2026-11-09'])
        ws.append(headers or TABBY)
        for row in rows or [sale()]: ws.append(row)
    elif provider == 'tamara':
        ws.title = 'Statement'
        ws.append(['Statement ID', 'SYNTHETIC-WEEK-2'])
        ws.append(['Tamara Merchant ID', 'synthetic-account'])
        ws.append(headers or TAMARA)
        for row in rows or [['02/11/2026', 'provider-order-1', '1001', None,
            'PAY_BY_INSTALMENTS', 'partially_captured', 'SAR', 100, 'Captured', 60,
            '02/11/2026', 1.5, 7, 4.2, 5.7, .86, 53.44, 4]]: ws.append(row)
    else:
        ws.title = 'Invoice # 9001'
        ws.append(headers or SALLA)
        for row in rows or [['1001', 100, 'مدى', 3, 97, .45, 96.55]]: ws.append(row)
    return wb


def content(wb):
    out = BytesIO()
    wb.save(out)
    wb.close()
    return out.getvalue()


@pytest.mark.parametrize('provider', ['tabby', 'tamara', 'salla'])
def test_auto_detects_the_actual_columns_and_preserves_exact_fee_evidence(provider):
    result = normalize_statement(content(workbook(provider)))
    row = result['rows'][0]
    assert result['provider'] == provider
    assert row['native_currency'] == 'SAR'
    assert row['blockers'] == []
    assert row['event_type'] == 'sale'
    assert row['principal_amount'] == ('60.00' if provider == 'tamara' else '100.00')
    assert row['fee_total'] == {'tabby': '9.19', 'tamara': '6.56', 'salla': '3.45'}[provider]
    assert result['totals']['fee_total'] == row['fee_total']
    assert row['installments'] == (4 if provider == 'tamara' else None)


def test_tabby_plan_is_source_label_and_payout_is_never_a_customer_payment():
    payout = [None] * len(TABBY)
    for key, value in {'Product Type': 'Payout fee', 'Total Fee': 6,
                       'VAT Amount': .9, 'Transferred amount': -6.9}.items():
        payout[TABBY.index(key)] = value
    result = normalize_statement(content(workbook(rows=[sale(), payout])))
    assert result['rows'][0]['plan_id'] == 'Installments: 3 Months'
    row = result['rows'][1]
    assert row['event_type'] == 'settlement_fee' and row['principal_amount'] == '0.00'
    assert row['order_number'] is None
    assert row['fee_total'] == '6.90'
    assert result['totals']['fee_total'] == '9.19'
    assert result['totals']['payout_fee_total'] == '6.90'


def test_refund_signed_components_survive_and_are_not_guessed_from_a_rate():
    refund = sale(**{'Type': 'partial refund', 'Order Amount': -25,
        'Refundable Commission': -1.25, 'Non Refundable Commission': 0,
        'Fixed Fee': 0, 'Total Fee': -1.25, 'VAT Amount': -.19,
        'Total Deduction': -1.44, 'Transferred amount': -23.56})
    row = normalize_statement(content(workbook(rows=[refund])))['rows'][0]
    assert row['event_type'] == 'refund' and row['principal_amount'] == '25.00'
    assert row['fee_net'] == '-1.25' and row['fee_vat'] == '-0.19'
    assert row['fee_total'] == '-1.44' and row['blockers'] == []


def test_tamara_refund_uses_event_amount_and_preserves_source_fee_credit():
    row = ['02/11/2026', 'provider-order-1', '1001', None, 'PAY_BY_INSTALMENTS',
           'partially_refunded', 'SAR', 100, 'Refunded', -20, '03/11/2026',
           0, 0, -1, -1, -.15, -18.85, 4]
    parsed = normalize_statement(content(workbook('tamara', [row])))['rows'][0]
    assert parsed['principal_amount'] == '20.00'
    assert parsed['fee_total'] == '-1.15' and parsed['blockers'] == []


def test_salla_wallet_deduction_is_separate_from_customer_refund():
    result = normalize_statement(content(workbook('salla', [
        ['1001', -10, 'مدى', 0, -10, 0, -10],
        ['1001', -6, 'order.payment_method.', 0, -6, 0, -6]])))
    assert [r['event_type'] for r in result['rows']] == ['refund', 'salla_purchase']
    assert result['totals']['refund_principal'] == '10.00'
    assert result['totals']['wallet_deductions'] == '6.00'
    assert result['rows'][0]['event_date'] is None


@pytest.mark.parametrize('change,code', [
    ({'Currency': None}, 'statement_currency_missing'),
    ({'VAT Amount': None}, 'statement_amount_missing'),
    ({'Total Fee': 'invalid'}, 'statement_amount_invalid'),
    ({'Total Fee': 7.991}, 'statement_amount_invalid'),
    ({'Transferred amount': 91}, 'statement_transfer_mismatch'),
    ({'Total Deduction': 8}, 'statement_deduction_mismatch'),
    ({'Refundable Commission': 5}, 'statement_fee_component_mismatch'),
    ({'Type': 'chargeback'}, 'statement_event_unsupported'),
    ({'Sale/Refund Date': 'not-a-date'}, 'statement_event_date_invalid'),
    ({'Sale/Refund Date': None}, 'statement_event_date_missing'),
    ({'Order Number': None}, 'statement_order_reference_missing'),
])
def test_incomplete_or_conflicting_rows_are_reviewable_never_zero_filled(change, code):
    row = normalize_statement(content(workbook(rows=[sale(**change)])))['rows'][0]
    assert code in row['blockers']
    if change == {'VAT Amount': None}:
        assert row['fee_vat'] is None and row['fee_total'] is None


def test_native_non_sar_currency_is_preserved_not_relabelled():
    result = normalize_statement(content(workbook(rows=[sale(Currency='AED')])))
    assert result['currency'] == 'AED'
    assert result['rows'][0]['native_currency'] == 'AED'


def test_non_numeric_merchant_reference_is_retained_for_exact_owner_matching():
    result = normalize_statement(content(workbook(rows=[sale(**{'Order Number': 'SYNTHETIC-A'})])))
    assert result['rows'][0]['order_number'] == 'SYNTHETIC-A'
    assert result['rows'][0]['blockers'] == []


def test_salla_currency_is_not_assumed_when_headers_do_not_name_it():
    headers = [v.replace(' (ر.س)', '') for v in SALLA]
    row = normalize_statement(content(workbook('salla', headers=headers)))['rows'][0]
    assert row['native_currency'] is None and 'statement_currency_missing' in row['blockers']


def test_repeated_indistinguishable_rows_need_review_but_keep_both_source_locations():
    result = normalize_statement(content(workbook(rows=[sale(), sale()])))
    assert len(result['rows']) == 2
    assert len({r['id'] for r in result['rows']}) == 2
    assert all('statement_duplicate_row_ambiguous' in r['blockers'] for r in result['rows'])


def test_ambiguous_multiple_statement_sheets_are_rejected():
    wb = workbook()
    ws = wb.create_sheet('Invoice # 99')
    ws.append(SALLA)
    ws.append(['1001', 100, 'مدى', 3, 97, .45, 96.55])
    with pytest.raises(ValueError, match='statement_provider_ambiguous'):
        normalize_statement(content(wb))


def test_repackaging_or_row_reordering_does_not_change_economic_fingerprint():
    a, b = sale(), sale(**{'Order Number': '1002'})
    first = normalize_statement(content(workbook(rows=[a, b])))
    second = normalize_statement(content(workbook(rows=[b, a])))
    assert first['fingerprint'] == second['fingerprint']
    assert first['file_hash'] != second['file_hash']


def test_invalid_workbook_and_unknown_format_have_safe_errors():
    with pytest.raises(ValueError, match='statement_file_invalid'):
        normalize_statement(b'not an xlsx')
    wb = openpyxl.Workbook()
    wb.active.append(['unrelated'])
    with pytest.raises(ValueError, match='statement_provider_unknown'):
        normalize_statement(content(wb))


def test_evidence_over_storage_budget_is_rejected_without_silent_truncation(monkeypatch):
    monkeypatch.setattr('operational_provider_statement_intake.MAX_EVIDENCE_BYTES', 100)
    with pytest.raises(ValueError, match='statement_evidence_size_limit'):
        normalize_statement(content(workbook()))


@pytest.mark.parametrize('provider,headers,duplicate', [
    ('tabby', TABBY, 'Currency'), ('tabby', TABBY, 'Total Fee'),
    ('tamara', TAMARA, 'Merchant Order ID'), ('salla', SALLA, 'الضريبة'),
])
def test_duplicate_financial_headers_are_not_silently_first_wins(provider, headers, duplicate):
    with pytest.raises(ValueError, match='statement_header_ambiguous'):
        normalize_statement(content(workbook(provider, headers=[*headers, duplicate])))
