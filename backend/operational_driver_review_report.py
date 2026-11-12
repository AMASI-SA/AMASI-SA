"""Read-only linkage: a sealed approval must match its actual aggregate effect."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from operational_balance_store import digest


def _positive(value):
    try:
        number = Decimal(value) if isinstance(value, str) else None
        return number is not None and number.is_finite() and number > 0 and number == number.quantize(Decimal('.01'))
    except (ValueError, ArithmeticError):
        return False


def validated_review_facts(state):
    valid, issues = [], []
    for identity, row in (state.get('driver_payment_review_facts') or {}).items():
        ok = (isinstance(row, dict) and row.get('id') == identity and row.get('financial_source') == 'operational'
              and row.get('seal') == digest({k: v for k, v in row.items() if k != 'seal'})
              and _positive(row.get('amount')) and row.get('currency') == 'SAR' and row.get('order_id') and row.get('driver_id')
              and state.get('owner_id') and identity == digest([state['owner_id'], 'operational-driver-review',
                  row.get('review_id'), row.get('review_revision')]))
        if ok and row.get('payment_method') == 'bank_transfer':
            matches = [m for m in state.get('movements', []) if m.get('id') == row.get('movement_id')]
            effect = matches[0] if len(matches) == 1 else {}
            ok = (row.get('destination_type') == 'bank' and effect.get('driver_review_event_id') == identity
                  and effect.get('direction') == 'incoming' and effect.get('bank_id') == row.get('destination_id')
                  and effect.get('party_type') == effect.get('bank_kind') == 'bank' and effect.get('allocations') == []
                  and effect.get('party_id') == row.get('destination_id') and effect.get('amount') == row.get('amount')
                  and effect.get('currency') == row.get('currency') and effect.get('order_id') == row.get('order_id')
                  and effect.get('receipt_hash') == row.get('receipt_hash'))
        elif ok and row.get('payment_method') == 'card_terminal':
            effect = (state.get('engine', {}).get('obligations') or {}).get(row.get('obligation_id'), {})
            ok = (row.get('destination_type') == effect.get('party_type') == 'pos_receivable'
                  and effect.get('driver_review_event_id') == identity and effect.get('direction') == 'receivable'
                  and effect.get('party_id') == row.get('destination_id') and effect.get('confirmed') == row.get('amount')
                  and effect.get('currency') == row.get('currency') and effect.get('order_id') == row.get('order_id'))
        else:
            ok = False
        if ok:
            valid.append(deepcopy(row))
        else:
            issues.append({'code': 'operational_driver_review_effect_conflict', 'source_id': identity,
                           'message': 'تعذر مطابقة اعتماد التحصيل بأثره التشغيلي؛ يلزم مراجعة الدليل.'})
    duplicates = {order for order, count in Counter(r['order_id'] for r in valid).items() if count != 1}
    if duplicates:
        issues.extend({'code': 'operational_driver_review_order_ambiguous', 'source_id': oid,
                       'message': 'يوجد أكثر من اعتماد تشغيلي للطلب؛ لم تُخمن جهة التحصيل.'} for oid in sorted(duplicates))
    return [r for r in valid if r['order_id'] not in duplicates], issues


def link_review_destinations(state, deliveries):
    """Never add displayed collection amounts again to the balance totals."""
    result = deepcopy(deliveries)
    coverage_contract = (type(result.get('coverage_contract_version')) is int
                         and result['coverage_contract_version'] == 1
                         and isinstance(result.get('coverage_issues'), list))
    facts, issues = validated_review_facts(state)
    by_order = {}
    for row in result['rows']:
        by_order.setdefault(row.get('order_id'), []).append(row)
    for fact in facts:
        matches = by_order.get(fact['order_id'], [])
        if len(matches) != 1:
            # A review can commit before the worker has captured the fee. Keep
            # that gap explicit; do not invent an entire shipping row or zero fee.
            issues.append({'code': 'operational_driver_review_awaiting_delivery_snapshot', 'source_id': fact['order_id'],
                           'message': 'التحصيل محفوظ؛ تفاصيل أجرة التوصيل تنتظر لقطة الاحتساب التالية.'})
            continue
        row = matches[0]
        if row.get('party_type') != 'store_driver' or row.get('party_id') != fact['driver_id']:
            issues.append({'code': 'operational_driver_review_executor_conflict', 'source_id': fact['order_id'],
                           'message': 'منفذ التوصيل لا يطابق اعتماد التحصيل.'})
            continue
        row.update(collection_status='confirmed', payment_method=fact['payment_method'],
            collection_destination='store_bank_account' if fact['destination_type'] == 'bank' else 'store_pos_receivable',
            destination_id=fact['destination_id'], destination_name=fact['destination_name'],
            collection_approved_at=fact['approved_at'], operational_review_event_id=fact['id'],
            collection_amount=fact['amount'], store_collection=fact['amount'], driver_custody='0.00',
            collection_settled='0.00', collection_outstanding='0.00')
        # Only the existing operational allocations prove that POS funds arrived.
        settled = sum((Decimal(a['amount']) for m in state.get('movements', []) for a in m.get('allocations', [])
                       if fact.get('obligation_id') and a.get('obligation_id') == fact['obligation_id']), Decimal(0))
        row['destination_outstanding'] = format(max(Decimal(fact['amount']) - settled, Decimal(0)), '.2f') if fact['destination_type'] == 'pos_receivable' else '0.00'
        if coverage_contract and isinstance(row.get('incomplete_reasons'), list):
            # Approval proves this noncash destination and its effect only.
            # Missing fee, executor, period or other-order proof stays missing.
            row['incomplete_reasons'] = [reason for reason in row['incomplete_reasons']
                if reason not in {'collection_destination_unresolved', 'collection_unconfirmed'}]
            row['complete'] = not row['incomplete_reasons']
    if coverage_contract:
        result['complete'] = (not issues and not result['coverage_issues']
                              and all(row.get('complete') is True for row in result['rows']))
    elif issues:
        result['complete'] = False
    if facts:
        result['notes'].append('التحصيل التشغيلي المرتبط مثبت بحركة بنك أو ذمة شبكة؛ يعرض للتفصيل ولا يضاف إلى الإجماليات مرة ثانية.')
    if not any(row.get('collection_destination') in {'store_bank_account_unknown', 'store_pos_account_unknown'} for row in result['rows']):
        result['notes'] = [note for note in result.get('notes', [])
                           if not note.startswith('غير متاح: هوية البنك أو جهاز الشبكة المستقبل غير مثبتة')]
    return result, issues
