"""Read-only MZ2 routing for operational order bank credits; no writer imports."""
import hashlib
import json
import re
from decimal import Decimal
from urllib.parse import urlsplit

NATIVE_BANKS = {
    'bank_rajhi': {'مصرف الراجحي', 'بنك الراجحي', 'الراجحي', 'al rajhi bank', 'al rajhi', 'rajhi'},
    'bank_inma': {'مصرف الإنماء', 'مصرف الانماء', 'بنك الإنماء', 'بنك الانماء', 'الإنماء', 'الانماء', 'alinma bank', 'alinma', 'al inma', 'inma'},
    'bank_ahli': {'البنك الأهلي التجاري', 'البنك الاهلي التجاري', 'البنك الأهلي السعودي', 'البنك الاهلي السعودي', 'البنك الأهلي', 'البنك الاهلي', 'الأهلي', 'الاهلي', 'saudi national bank', 'snb', 'ncb'},
}


def transfer_bank(method):
    text = ' '.join(str(method or '').strip().strip("'").split())
    pattern = r'^(?:حوال[هة]\s*بنكي[هة]|تحويل\s*بنكي|bank(?:$|[ _]*transfer)|wire_transfer)\s*'
    match = re.match(pattern, text, re.I)
    return (True, text[match.end():].strip()) if match else (False, '')


def resolve(data, owner, method, currency, *, raw=None, banks=()):
    if str(method or '').strip().lower() == 'bank':
        native = raw.get('bank') if isinstance(raw, dict) else None
        name = native.get('bank_name') if isinstance(native, dict) else None
        name = ' '.join(name.split()).casefold() if isinstance(name, str) else ''
        code = next((code for code, labels in NATIVE_BANKS.items() if name in labels), None)
        eligible = [bank for bank in banks if bank.get('user_id') == owner
                    and str(bank.get('id') or '').startswith('account:')
                    and bank.get('account_type') == 'bank' and bank.get('status') == 'active'
                    and currency == 'SAR' and bank.get('currency') == currency]
        if not owner or not code or any(not isinstance(bank.get('bank_transfer_aliases', []), list) for bank in eligible):
            raise ValueError('operational_order_bank_binding_incomplete')
        matches = [bank for bank in eligible if code in bank.get('bank_transfer_aliases', [])]
        if len(matches) != 1:
            raise ValueError('operational_order_bank_binding_incomplete')
        return matches[0]
    _, selected = transfer_bank(method)
    source = 'salla.payment_method_bank'
    key = hashlib.sha256(json.dumps([owner, source, selected], ensure_ascii=False).encode()).hexdigest()
    bindings = [b for b in data['mz2_bank_transfer_bindings'] if selected and
        b.get('_id') == key and b.get('upstream_source') == source and b.get('upstream_value') == selected
        and b.get('status') == 'active' and b.get('confirmed') is True
        and b.get('identity_contract_version') == 1 and b.get('bank_account_source') == 'mz2_financial_accounts']
    banks = [b for b in data['mz2_financial_accounts'] if len(bindings) == 1
        and b.get('id') == bindings[0].get('financial_account_id')
        and b.get('account_type') == 'bank' and b.get('status') == 'active'
        and currency == 'SAR' and b.get('currency') == currency]
    if len(banks) != 1:
        raise ValueError('operational_order_bank_binding_incomplete')
    return banks[0]


def native_paid_amount(raw, source, *, owner, order_number, provider_order_id, currency, total, as_of):
    """Prove only the native SAR bank leg against the current customer balance."""
    from operational_payment_evidence import _amount, _identity
    from salla_payment_collection import provider_order_page_balance

    error = 'operational_order_bank_payment_incomplete'
    if not isinstance(raw, dict) or not isinstance(source, dict):
        raise ValueError(error)
    payment = raw.get('payment') or {}
    methods = raw.get('payment_methods')
    if (currency != 'SAR' or not isinstance(payment, dict)
            or raw.get('payment_method') != 'bank' or payment.get('method') not in (None, 'bank')
            or not isinstance(methods, list) or len(methods) not in (1, 2)
            or any(not isinstance(leg, dict) for leg in methods)):
        raise ValueError(error)
    page = provider_order_page_balance(raw, currency, total)
    if not page or not 0 < _amount(page['paid'], currency) <= _amount(total, currency):
        raise ValueError(error)
    legs = {}
    references = set()
    for leg in methods:
        method = leg.get('payment_method')
        if method not in ('bank', 'customer_wallet') or method in legs or leg.get('currency') not in (None, currency):
            raise ValueError(error)
        value = _amount(leg.get('amount'), currency)
        if value <= 0:
            raise ValueError(error)
        legs[method] = value
        if len(methods) > 1:
            reference = _identity(leg.get('transaction_reference'), error)
            if reference in references:
                raise ValueError(error)
            references.add(reference)
    if 'bank' not in legs or sum(legs.values(), Decimal(0)) != _amount(page['paid'], currency):
        raise ValueError(error)
    _require_current(raw, source, owner, order_number, provider_order_id, as_of)
    return legs['bank']


def _require_current(raw, source, owner, order_number, provider_order_id, as_of):
    from operational_payment_evidence import _instant
    from salla_payment_collection import provider_order_page_current

    version = _instant(raw.get('updated_at') or raw.get('date_updated'))
    if not _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date')) <= version <= _instant(as_of):
        raise ValueError('operational_order_bank_payment_source_incomplete')
    watermark = source.get('g47_salla_snapshot') or {}
    if (not provider_order_page_current(source, raw, owner=owner, order_number=order_number,
            provider_order_id=provider_order_id, customer_balance_verified=True)
            or (watermark.get('source_updated_at') is not None and _instant(watermark['source_updated_at']) != version)):
        raise ValueError('operational_order_bank_payment_source_incomplete')


def is_bank_split(raw):
    """A direct bank tender alongside another method, regardless of the primary rail."""
    methods = raw.get('payment_methods') if isinstance(raw, dict) else None
    if not isinstance(methods, list) or not 2 <= len(methods) <= 100:
        return False
    return any(isinstance(leg, dict) and leg.get('payment_method') == 'bank' for leg in methods) and any(
        isinstance(leg, dict) and leg.get('payment_method') != 'bank' for leg in methods)


def is_mada_bank_split(raw):
    methods = raw.get('payment_methods') if isinstance(raw, dict) else None
    return (isinstance(methods, list) and len(methods) == 2
            and raw.get('payment_method') in ('mada', 'bank')
            and all(isinstance(leg, dict) for leg in methods)
            and any(leg.get('payment_method') == 'bank' for leg in methods)
            and any(leg.get('payment_method') == 'mada' for leg in methods))


def is_original_mada_split(raw, currency, total):
    """Classify an already proved tender against the earlier fully paid mada contract."""
    from operational_payment_evidence import _amount, _identity

    if not is_mada_bank_split(raw):
        return False
    try:
        action = raw['payment_actions']['remaining_action']
        card = next(leg for leg in raw['payment_methods'] if leg['payment_method'] == 'mada')
        _identity(card.get('transaction_reference'), 'invalid_reference')
        return action.get('has_remaining_amount') is False and _amount(action.get('paid_amount'), currency) == _amount(total, currency)
    except (AttributeError, KeyError, TypeError, ValueError):
        return False


def is_original_wallet_split(raw):
    """The original bank-primary wallet contract required two distinct references."""
    from operational_payment_evidence import _identity

    if not is_bank_split(raw) or raw.get('payment_method') != 'bank':
        return False
    methods = raw['payment_methods']
    if len(methods) != 2 or not any(leg.get('payment_method') == 'customer_wallet' for leg in methods):
        return False
    try:
        return len({_identity(leg.get('transaction_reference'), 'invalid_reference') for leg in methods}) == 2
    except ValueError:
        return False


def is_closed_bank_underpayment(raw, currency, total):
    """Identify a bank-only closed checkout whose explicit paid amount fell below gross."""
    from operational_payment_evidence import _amount

    methods = raw.get('payment_methods')
    actions = raw.get('payment_actions')
    action = actions.get('remaining_action') if isinstance(actions, dict) else None
    return (raw.get('payment_method') == 'bank' and isinstance(methods, list) and len(methods) == 1
            and isinstance(methods[0], dict) and methods[0].get('payment_method') == 'bank'
            and isinstance(action, dict) and action.get('has_remaining_amount') is False
            and action.get('paid_amount') is not None and 0 < _amount(action['paid_amount'], currency) < _amount(total, currency))


def _has_receipt(raw, payment, bank):
    # A primary card's receipt_url cannot prove a different tender. Salla's
    # order-level receipt_image is the customer-uploaded bank transfer slip.
    media_fields = [raw.get('receipt_image'), bank.get('receipt_image'), bank.get('receipt_url')]
    if raw.get('payment_method') == 'bank':
        media_fields.extend((raw.get('payment_receipt_url'), payment.get('receipt_image'), payment.get('receipt_url')))
    for media in media_fields:
        if isinstance(media, dict):
            media = media.get('url') or media.get('original') or media.get('src')
        if isinstance(media, str):
            try:
                parts = urlsplit(media.strip())
                hosted = parts.scheme == 'https' and parts.hostname and parts.path.strip('/') and not parts.username
            except ValueError:
                hosted = False
            if hosted:
                return True
    return False


def _closed_order_paid_only(raw, payment, actions, action, currency, total):
    """Validate paid independently when closing checkout did not settle the whole price."""
    from operational_payment_evidence import _amount
    from salla_payment_collection import provider_order_page_balance

    def omit_closed_remainder(row):
        if ((row.get('has_remaining_amount') is not None and row['has_remaining_amount'] is not False)
                or (row.get('remaining_amount') is not None and _amount(row['remaining_amount'], currency) != 0)):
            raise ValueError('operational_order_bank_payment_incomplete')
        return {k: v for k, v in row.items() if k not in ('remaining_amount', 'has_remaining_amount')}

    checked = {**omit_closed_remainder(raw), 'payment': omit_closed_remainder(payment),
               'payment_actions': {**actions, 'remaining_action': omit_closed_remainder(action)}}
    return provider_order_page_balance(checked, currency, total, allow_paid_only=True)


def _check_mixed_tenders(raw, payment, bank, bank_ref, paid, page, rows, *,
                         owner, order_number, provider_order_id, currency, as_of):
    """Reconcile actual tender rows with native paid/due. A COD balance may still be outstanding."""
    from operational_payment_evidence import _amount, _identity, _instant, provider_for
    from operational_split_payment import _transaction_conflicts

    error = 'operational_order_bank_payment_incomplete'
    if not page or page['remaining'] is None or _amount(page['paid'], currency) != paid:
        raise ValueError(error)
    due = _amount(page['remaining'], currency)
    cod_methods = {'cod', 'cash_on_delivery', 'cashondelivery', 'cash on delivery',
                   'الدفع عند الاستلام', 'دفع عند الاستلام', 'دفع عند الإستلام'}
    methods, references, providers = set(), set(), {}
    paid_tenders, outstanding = Decimal(0), Decimal(0)
    for leg in raw['payment_methods']:
        name = _identity(leg.get('payment_method'), error).lower()
        if not isinstance(leg['payment_method'], str) or (leg is not bank and (transfer_bank(name)[0] or name == 'wire transfer')):
            raise ValueError(error)
        methods.add(name)
        amount = _amount(leg.get('amount'), currency)
        if amount <= 0:
            raise ValueError(error)
        reference = leg.get('transaction_reference')
        if leg is bank:
            reference = bank_ref
        elif reference is None and name in cod_methods and due > 0:
            # An unreferenced COD charge can describe only the independently
            # documented amount still due, never proof of received money.
            outstanding += amount
            continue
        elif reference is not None or not bank_ref:
            # With no independent bank reference, insist on referenced co-tenders
            # to isolate the customer-uploaded bank slip from the other payments.
            reference = _identity(reference, error)
        paid_tenders += amount
        if reference is not None:
            if reference in references:
                raise ValueError(error)
            references.add(reference)
            provider = provider_for(name)
            if provider:
                providers[reference] = {'provider': provider, 'amount': str(amount)}
    primary = _identity(raw.get('payment_method'), error).lower()
    if (primary not in methods or paid_tenders != paid or (outstanding and outstanding != due)
            or any(_identity(payment[key], error) not in references
                   for key in ('reference', 'transaction_reference') if payment.get(key) is not None)):
        raise ValueError(error)
    if rows:
        if any(not isinstance(row, dict) or row.get('provider_order_id') != provider_order_id
               or row.get('source') != 'salla_transactions' or row.get('user_id') != owner
               or row.get('order_number') != order_number or not row.get('native_transaction_evidence') for row in rows):
            raise ValueError('provider_transaction_binding_conflict')
        created = _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
        if _transaction_conflicts(rows, providers, owner, order_number, provider_order_id, currency, created, _instant(as_of)):
            raise ValueError('provider_transaction_binding_conflict')


def frozen_bank_provider_conflict(raw, *, owner, order_number, provider_order_id, currency, facts, obligations):
    """Compare prior postings for this order with the separately itemized nonbank shares."""
    from operational_payment_evidence import _amount, _identity, provider_for

    facts, claims = dict(facts), dict(obligations)
    if not facts and not claims:
        return False
    parent = 'provider:' + provider_order_id
    try:
        legs = {}
        for leg in raw['payment_methods']:
            if leg.get('transaction_reference') is None:
                continue
            reference = _identity(leg['transaction_reference'], 'invalid_reference')
            if reference in legs:
                return True
            name = leg['payment_method'].lower()
            legs[reference] = (name, provider_for(name), _amount(leg['amount'], currency))

        def matches(ref, party, amount):
            return (ref in legs and party is not None and legs[ref][1] == party
                    and _amount(amount, currency) == legs[ref][2])

        expected_claims = {}
        split_key = 'provider_split:' + provider_order_id
        if split_key in facts:
            saved = facts[split_key]
            split = saved['evidence']
            if (len(facts) != 1 or (split['owner_id'], split['order_id'], split['order_number'], split['currency'])
                    != (owner, provider_order_id, order_number, currency)):
                return True
            booked = set(saved['booked_providers'])
            totals = {}
            seen = set()
            for tender in split['tenders']:
                ref, name, party = tender['reference'], tender['method'], tender['provider']
                value = _amount(tender['amount'], currency)
                if (ref in seen or name == 'bank' or (not party and name != 'customer_wallet')
                        or legs.get(ref) != (name, party, value)):
                    return True
                seen.add(ref)
                totals[party] = totals.get(party, Decimal(0)) + value
            if (not booked or None in booked or not booked.issubset(totals)
                    or sum(totals.values(), Decimal(0)) != _amount(split['gross'], currency)):
                return True
            for party in booked:
                expected_claims[parent + ':' + party] = (party, totals[party])
        else:
            page = facts.pop('provider_order_balance:' + provider_order_id, None)
            party, captured = None, Decimal(0)
            for key, fact in facts.items():
                if (not key.startswith('capture:') or fact['order_id'] != provider_order_id
                        or (party is not None and fact['provider'] != party)
                        or not matches(key.removeprefix('capture:'), fact['provider'], fact['amount'])):
                    return True
                party = fact['provider']
                captured += _amount(fact['amount'], currency)
            if page is not None:
                if ((page['owner_id'], page['order_id'], page['order_number'], page['currency'])
                        != (owner, provider_order_id, order_number, currency)):
                    return True
                paid = _amount(page['paid'], currency)
                ref = page.get('transaction_reference')
                if ((ref is not None and not matches(ref, page['provider'], paid))
                        or (not captured and ref is None)
                        or (captured and (captured != paid or party != page['provider']))
                        or (captured and ref is not None and 'capture:' + ref not in facts)):
                    return True
                party, captured = page['provider'], paid
            if not captured or not party:
                return True
            expected_claims[parent] = (party, captured)
        if expected_claims.keys() != claims.keys():
            return True
        for key, (party, paid) in expected_claims.items():
            claim = claims[key]
            if ((claim['id'], claim['kind'], claim['party_type'], claim['party_id'], claim['direction'], claim['currency'])
                    != (key, 'provider', 'provider', party, 'receivable', currency)
                    or claim.get('incomplete') or _amount(claim['confirmed'], currency) != paid
                    or _amount(claim['expected'], currency) != 0):
                return True
        return False
    except (ValueError, KeyError, TypeError, AttributeError, ArithmeticError):
        return True


def bank_ledger_paid_amount(raw, source, *, owner, order_number, provider_order_id, currency, total,
                            as_of, payment_issues=(), transaction_rows=(), frozen_provider=False):
    """Recognize only the separately evidenced bank tender; never recognize a provider here."""
    from operational_payment_evidence import _amount, _identity, _instant, provider_for
    from salla_payment_collection import provider_order_page_balance

    error = 'operational_order_bank_payment_incomplete'
    payment = raw.get('payment') or {}
    actions = raw.get('payment_actions') or {}
    if not isinstance(payment, dict) or not isinstance(actions, dict):
        raise ValueError(error)
    action, refund = actions.get('remaining_action') or {}, actions.get('refund_action') or {}
    if not isinstance(action, dict) or not isinstance(refund, dict):
        raise ValueError(error)
    mixed = is_bank_split(raw)
    methods = raw.get('payment_methods')
    if any(isinstance(row, dict) and row.get('bank_tender_reference_conflict') is True for row in transaction_rows):
        raise ValueError('provider_transaction_binding_conflict')
    if ((not mixed and not is_closed_bank_underpayment(raw, currency, total))
            or (is_original_wallet_split(raw) and not payment_issues and not transaction_rows and not frozen_provider)):
        return native_paid_amount(raw, source, owner=owner, order_number=order_number, provider_order_id=provider_order_id,
                                  currency=currency, total=total, as_of=as_of)

    # The provider reader cannot describe a direct-bank leg, even when the bank is independently proved.
    allowed = {'provider_split_payment_review_required', 'provider_payment_reference_missing'} if mixed else set()
    if (mixed and not provider_for(raw.get('payment_method'))
            and not any(isinstance(leg, dict) and provider_for(leg.get('payment_method')) for leg in methods)):
        # A wallet or an unregistered co-tender can be the primary label. Its
        # explicit row and reference still have to reconcile independently.
        allowed.add('provider_payment_method_unknown')
    if frozen_provider:
        raise ValueError('operational_order_bank_credit_conflict')
    _require_current(raw, source, owner, order_number, provider_order_id, as_of)
    issue_codes = {issue.get('code') for issue in payment_issues}
    watermark = source.get('g47_salla_snapshot') or {}
    if (mixed and provider_for(raw.get('payment_method')) and watermark.get('component_pending') is True
            and issue_codes == {'provider_order_page_identity_conflict'}
            and any(isinstance(leg, dict) and leg.get('transaction_reference') is None for leg in methods)):
        # Both identity and exact source version have been checked against the real
        # snapshot above. Isolate the stricter card-reader gate on a temporary view:
        # it must still withhold the provider split for a known bank-leg reason.
        from operational_split_payment import split_payment_evidence
        probe_source = {**source, 'g47_salla_snapshot': {**watermark, 'component_pending': False}}
        probe = split_payment_evidence(raw, currency, total, provider_order_page_balance(raw, currency, total),
            owner=owner, order_number=order_number, provider_order_id=provider_order_id, rows=transaction_rows,
            source=probe_source, created_at=_instant(raw.get('date') or raw.get('created_at') or raw.get('order_date')),
            as_of=_instant(as_of))
        if probe and probe[0] is None and probe[1]:
            issue_codes = {issue.get('code') for issue in probe[1]}
    if not issue_codes.issubset(allowed):
        raise ValueError('operational_order_bank_credit_conflict')
    if (currency != 'SAR' or payment.get('method') not in (None, raw.get('payment_method'))
            or type(action.get('has_remaining_amount')) is not bool
            or any(not isinstance(leg, dict) or leg.get('currency') not in (None, currency) for leg in methods)):
        raise ValueError(error)
    bank_rows = [leg for leg in methods if leg.get('payment_method') == 'bank']
    if len(bank_rows) != 1:
        raise ValueError(error)
    bank = bank_rows[0]
    paid = _amount(action.get('paid_amount'), currency)
    bank_paid = _amount(bank.get('amount'), currency)
    if (bank_paid <= 0 or not isinstance(action.get('paid_amount'), dict)
            or not isinstance(refund.get('paid_amount'), dict) or _amount(refund['paid_amount'], currency) != paid):
        raise ValueError(error)
    bank_ref = bank.get('transaction_reference')
    if bank_ref is not None:
        bank_ref = _identity(bank_ref, error)
    if not bank_ref and not _has_receipt(raw, payment, bank):
        raise ValueError('operational_order_bank_receipt_evidence_missing')

    status = raw.get('status')
    names = (status.get('slug'), status.get('name')) if isinstance(status, dict) else (status,)
    waiting = {'payment_pending', 'awaiting_payment', 'pending_payment', 'waiting_payment', 'بانتظار الدفع', 'بإنتظار الدفع'}
    payment_states = (payment.get('status'), payment.get('collection_status'),
                      raw.get('payment_status'), raw.get('payment_collection_status'))
    if (raw.get('is_pending_payment') is True or any(str(name).strip().lower() in waiting for name in names)
            or any(state not in (None, '', 'paid', 'captured', 'partial', 'مدفوع', 'تم الدفع') for state in payment_states)
            or any(row.get(key) for row in (raw, payment, refund) for key in ('refunds', 'cancellations', 'refunded_transactions'))
            or any(_amount(row[key], currency) for row in (raw, payment, refund) for key in ('pending_refund_amount', 'cancelled')
                   if row.get(key) is not None)):
        raise ValueError(error)
    if mixed:
        page = provider_order_page_balance(raw, currency, total)
        _check_mixed_tenders(raw, payment, bank, bank_ref, paid, page, transaction_rows,
                             owner=owner, order_number=order_number, provider_order_id=provider_order_id,
                             currency=currency, as_of=as_of)
    else:
        page = _closed_order_paid_only(raw, payment, actions, action, currency, total)
        if not page or page['remaining'] is not None or _amount(page['paid'], currency) != bank_paid:
            raise ValueError(error)
    return bank_paid
