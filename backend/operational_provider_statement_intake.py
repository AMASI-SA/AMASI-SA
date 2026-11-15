"""Strict, read-only XLSX evidence for the operational statement workflow.

The legacy importer intentionally has permissive display defaults. Those defaults
are not payment proof: this adapter reads original cells, retains missing values,
and checks signed fee/tax/net arithmetic before a row can be reconciled. Nothing
here writes orders, balances, payments or bank transfers.
"""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO
import json
import re

import openpyxl

from settlements_import.parsers import salla, tabby, tamara
from settlements_import.utils import normalize_payment_method

SCHEMA = 'operational_statement_v1'
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 10000
MAX_SHEETS = 20
CENT = Decimal('.01')
ZERO = Decimal(0)


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(',', ':')).encode()).hexdigest()


def _text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _reference(value):
    text = _text(value)
    return re.sub(r'\.0+$', '', text) if text else None


def _amount(value, blocks):
    if value is None or value == '':
        blocks.append('statement_amount_missing')
        return None
    try:
        if isinstance(value, bool):
            raise ValueError()
        text = str(value).strip().replace('\u200e', '').replace('\u200f', '')
        if ',' in text and not re.fullmatch(r'-?\d{1,3}(,\d{3})+(\.\d+)?', text):
            raise ValueError()
        number = Decimal(text.replace(',', ''))
        if not number.is_finite() or abs(number) > Decimal('999999999999.99'):
            raise ValueError()
        rounded = number.quantize(CENT)
        # Excel numeric cells can retain sub-cent binary noise. Do not round
        # source decimal strings or genuinely fractional minor units silently.
        if number != rounded and not (isinstance(value, float) and abs(number - rounded) < Decimal('.000000001')):
            raise ValueError()
        return format(rounded if rounded else ZERO, '.2f')
    except (ValueError, TypeError, InvalidOperation):
        blocks.append('statement_amount_invalid')
        return None


def _currency(value):
    value = _text(value)
    if value and re.fullmatch(r'[A-Za-z]{3}', value):
        return value.upper()
    return None


def _day(value):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    if not text:
        return None
    for form in ('%Y-%m-%d', '%Y-%m-%d %H:%M:%S', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            return datetime.strptime(text, form).date().isoformat()
        except ValueError:
            continue
    return None


def _cell(row, cols, field):
    index = cols.get(field)
    return row[index] if index is not None and index < len(row) else None


def _sniff(workbook):
    found = []
    if len(workbook.worksheets) > MAX_SHEETS:
        raise ValueError('statement_sheet_limit')
    for sheet in workbook.worksheets:
        if sheet.calculate_dimension() in {'A1', 'A1:A1'} and hasattr(sheet, 'reset_dimensions'):
            sheet.reset_dimensions()
        preview = list(sheet.iter_rows(values_only=True, max_row=30, max_col=60))
        providers = set()
        for row in preview:
            cells = {str(v).strip().lower() for v in row if v is not None}
            if {'order number', 'refundable commission', 'transferred amount'} <= cells:
                providers.add('tabby')
            if {'tamara order id', 'merchant order id', 'event amount'} <= cells:
                providers.add('tamara')
            if 'رقم الطلب' in cells and any('إجمالي الطلب' in v for v in cells) and any('الرسوم' in v for v in cells):
                providers.add('salla')
        found.extend((provider, sheet) for provider in providers)
    if not found:
        raise ValueError('statement_provider_unknown')
    if len(found) != 1:
        raise ValueError('statement_provider_ambiguous')
    return found[0]


def _normalize_row(provider, raw, cols, position, *, statement_ref, salla_currency=None):
    get = lambda field: _cell(raw, cols, field)
    blocks = []
    if provider == 'tabby':
        order = _reference(get('order_number'))
        payout = any(str(v or '').strip().lower().startswith('payout fee') for v in raw[:10])
        if not payout and (not order or not order.isdigit()):
            return None
        source_type = str(get('event_type') or '').strip().lower()
        kind = ('settlement_fee' if payout else 'sale' if source_type == 'sale'
                else 'refund' if source_type in {'refund', 'partial refund'} else 'unsupported')
        principal = '0.00' if payout else _amount(get('order_amount'), blocks)
        fee, tax, net = (_amount(get(k), blocks) for k in ('total_fee', 'vat_amount', 'transferred_amount'))
        ccy, stamp = _currency(get('currency')), _day(get('event_date'))
        plan = _text(get('product_type')) if not payout else None
        method, provider_order, event_reference = 'tabby', None, None
        account = _text(get('merchant_code'))
        if not payout:
            components = [_amount(get(k), blocks) for k in ('refundable_commission', 'non_refundable_commission', 'fixed_fee')]
            if fee is not None and all(v is not None for v in components) and sum(map(Decimal, components), ZERO) != Decimal(fee):
                blocks.append('statement_fee_component_mismatch')
        deduction = _amount(get('total_deduction'), blocks) if get('total_deduction') is not None else None
        if not payout and get('total_deduction') is None:
            blocks.append('statement_deduction_missing')
    elif provider == 'tamara':
        order = _reference(get('merchant_order_id'))
        if not order or order.lower() in {'total', 'credit total', 'subtotal'}:
            return None
        source_type = str(get('event') or '').strip().lower()
        kind = ('sale' if source_type in {'captured', 'capture'} else 'refund' if source_type in {'refunded', 'refund'}
                else 'canceled_fee' if source_type in {'canceled', 'cancelled', 'cancel'} else 'unsupported')
        principal = '0.00' if kind == 'canceled_fee' else _amount(get('event_amount'), blocks)
        fee, tax, net = (_amount(get(k), blocks) for k in ('total_fees', 'vat', 'total_payable'))
        ccy = _currency(get('currency'))
        stamp = _day(get('event_date'))
        provider_order = _reference(get('tamara_order_id'))
        # Tamara Order ID identifies the order, not each capture/refund event.
        event_reference = None
        plan, method, account = _text(get('payment_type')), 'tamara', None
        deduction = None
    else:
        order = _reference(get('order_number'))
        if not order or order.lower() in {'total', 'الإجمالي', 'المجموع'}:
            return None
        principal = _amount(get('gross_amount'), blocks)
        fee, tax, net = (_amount(get(k), blocks) for k in ('payment_fee', 'payment_vat', 'net_amount'))
        source_method = _text(get('payment_method')) or ''
        wallet = salla._is_wallet_recharge(source_method, 0, 0)
        kind = 'salla_purchase' if wallet else ('refund' if principal is not None and Decimal(principal) < 0 else 'sale')
        ccy, stamp = salla_currency, None
        method = 'wallet_recharge' if wallet else normalize_payment_method(source_method)
        plan, account, provider_order, event_reference, deduction = None, None, None, None, None

    if not ccy and kind != 'settlement_fee':
        blocks.append('statement_currency_missing')
    if kind == 'unsupported':
        blocks.append('statement_event_unsupported')
    if principal is not None:
        signed_principal = Decimal(principal)
        if kind == 'sale' and signed_principal <= 0 or kind == 'refund' and signed_principal >= 0:
            blocks.append('statement_principal_sign_invalid')
        if kind in {'sale', 'refund'} and not method:
            blocks.append('statement_payment_method_missing')
    else:
        signed_principal = None
    total = format(Decimal(fee) + Decimal(tax), '.2f') if fee is not None and tax is not None else None
    if total is not None and net is not None and signed_principal is not None:
        if signed_principal - Decimal(total) != Decimal(net):
            blocks.append('statement_transfer_mismatch')
        if deduction is not None and Decimal(deduction) != Decimal(total):
            blocks.append('statement_deduction_mismatch')
        if kind == 'sale' and (Decimal(fee) < 0 or Decimal(tax) < 0):
            blocks.append('statement_fee_sign_invalid')
    result = {
        'id': f'row:{position}', 'source_row': position, 'provider': provider,
        'order_number': None if kind == 'settlement_fee' else order,
        'provider_order_id': provider_order, 'event_type': kind,
        'event_reference': event_reference, 'event_date': stamp,
        'date_precision': 'day' if stamp else None, 'native_currency': ccy,
        'principal_amount': format(abs(signed_principal), '.2f') if signed_principal is not None else None,
        'fee_net': fee, 'fee_vat': tax, 'fee_total': total, 'transferred_amount': net,
        'payment_method': method, 'plan_id': plan, 'merchant_account': account,
        'statement_reference': statement_ref, 'blockers': sorted(set(blocks)),
    }
    return result


def normalize_statement(content: bytes) -> dict:
    """Return typed source evidence; row blockers prevent automatic acceptance."""
    if not isinstance(content, bytes) or not content or len(content) > MAX_FILE_BYTES:
        raise ValueError('statement_file_invalid')
    try:
        workbook = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True, keep_links=False)
    except Exception:
        raise ValueError('statement_file_invalid') from None
    try:
        provider, sheet = _sniff(workbook)
        # Bound source rows before materializing a workbook into memory. Blank
        # tail rows do not permit silently truncating a financial statement.
        raw_rows = list(sheet.iter_rows(values_only=True, max_row=MAX_ROWS + 32, max_col=60))
        if len(raw_rows) >= MAX_ROWS + 32:
            raise ValueError('statement_row_limit')
        if provider == 'salla':
            header_index = next(i for i, row in enumerate(raw_rows[:30]) if 'رقم الطلب' in row)
            cols = salla._find_column_indices(list(raw_rows[header_index]))
            ref = salla._extract_invoice_number(sheet.title) or None
            preamble = {}
            money_headers = [str(_cell(raw_rows[header_index], cols, k) or '') for k in ('gross_amount', 'payment_fee', 'net_amount')]
            source_ccy = 'SAR' if all(re.search(r'ر\.?\s*س|\bSAR\b', v, re.IGNORECASE) for v in money_headers) else None
        else:
            parser = tabby if provider == 'tabby' else tamara
            header_index, cols = parser._find_header_row(raw_rows)
            preamble = parser._read_preamble(raw_rows, header_index)
            ref, source_ccy = preamble.get('statement_id') or None, None
        rows = []
        for index, raw in enumerate(raw_rows[header_index + 1:], start=header_index + 2):
            if not any(value not in (None, '') for value in raw):
                continue
            row = _normalize_row(provider, raw, cols, index, statement_ref=ref, salla_currency=source_ccy)
            if row is not None:
                rows.append(row)
        if not rows:
            raise ValueError('statement_rows_missing')
        if len(rows) > MAX_ROWS:
            raise ValueError('statement_row_limit')
        currencies = {r['native_currency'] for r in rows if r['native_currency']}
        currency = next(iter(currencies)) if len(currencies) == 1 else None
        accounts = {r['merchant_account'] for r in rows if r['merchant_account']}
        if preamble.get('tamara_merchant_id'):
            accounts.add(preamble['tamara_merchant_id'])
        merchant = next(iter(accounts)) if len(accounts) == 1 else None
        for row in rows:
            if row['event_type'] == 'settlement_fee' and not row['native_currency']:
                row['native_currency'] = currency
                if not currency:
                    row['blockers'].append('statement_currency_missing')
            if len(currencies) > 1:
                row['blockers'].append('statement_currency_conflict')
            if len(accounts) > 1:
                row['blockers'].append('statement_merchant_conflict')
            # Source position is a locator, not an economic event identity.
            row['row_fingerprint'] = _digest({k: v for k, v in row.items() if k not in {'id', 'source_row', 'blockers'}})
        counts = Counter(r['row_fingerprint'] for r in rows)
        for row in rows:
            if counts[row['row_fingerprint']] > 1:
                row['blockers'].append('statement_duplicate_row_ambiguous')
        totals = {k: ZERO for k in ('capture_principal', 'refund_principal', 'fee_net', 'fee_vat', 'fee_total',
                                   'transferred_amount', 'payout_fee_total', 'wallet_deductions')}
        unavailable = set()
        for row in rows:
            kind = row['event_type']
            fields = {'transferred_amount': 'transferred_amount'}
            if kind == 'settlement_fee':
                fields['payout_fee_total'] = 'fee_total'
            elif kind == 'salla_purchase':
                fields['wallet_deductions'] = 'principal_amount'
            else:
                fields.update({k: k for k in ('fee_net', 'fee_vat', 'fee_total')})
                if kind in {'sale', 'refund'}:
                    fields['capture_principal' if kind == 'sale' else 'refund_principal'] = 'principal_amount'
            for dest, source in fields.items():
                value = row[source]
                if value is None or len(currencies) > 1:
                    unavailable.add(dest)
                else:
                    totals[dest] += Decimal(value)
        result = {'schema_version': SCHEMA, 'provider': provider, 'statement_reference': ref,
                  'merchant_account': merchant, 'currency': currency,
                  'statement_date': _day(preamble.get('statement_date_raw')),
                  'rows': rows, 'totals': {k: None if k in unavailable else format(v, '.2f') for k, v in totals.items()}}
        result['fingerprint'] = _digest({**{k: result[k] for k in ('schema_version', 'provider', 'statement_reference', 'merchant_account', 'currency', 'statement_date')},
                                        'rows': sorted(row['row_fingerprint'] for row in rows)})
        result['file_hash'] = sha256(content).hexdigest()
        return result
    finally:
        workbook.close()
