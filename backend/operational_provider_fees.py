"""Evidence-configured provider fee estimates. No DB, network or financial writes.

Version 1 preserves the existing aggregate-policy contract. Version 2 is opt-in
and splits refundable/nonrefundable/fixed components with independent VAT.
No merchant rates are hardcoded; statement fees still supersede estimates.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP

CENT = Decimal('0.01')
ZERO = Decimal('0')
EXECUTED = frozenset(('executed', 'settled', 'Refund Executed', 'Settled'))
LIMIT = Decimal('999999999999.99')


def money(value):
    if value is None or isinstance(value, bool):
        raise ValueError('amount_missing')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError('amount_invalid') from None
    if not result.is_finite() or result < 0:
        raise ValueError('amount_invalid')
    return result


def amount(value):
    return format(value.quantize(CENT, rounding=ROUND_HALF_UP), '.2f')


def day(value):
    return date.fromisoformat(str(value)[:10])


def _q(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _cash(value):
    value = money(value)
    if value > LIMIT or value != _q(value):
        raise ValueError('provider_amount_precision_invalid')
    return value


def _events(rows, currency, kind):
    """Count executed references once; contradictory duplicates are never ignored."""
    if not isinstance(rows, list):
        raise ValueError('provider_event_shape_invalid')
    seen = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('provider_event_shape_invalid')
        if row.get('status') not in EXECUTED:
            continue
        identity = row.get('id')
        if not isinstance(identity, (str, int)) or isinstance(identity, bool) or not str(identity).strip():
            raise ValueError(kind + '_identity_missing')
        identity = str(identity).strip()
        value = _cash(row.get('amount'))
        if value <= 0 or row.get('currency', currency) != currency:
            raise ValueError('provider_event_amount_or_currency_invalid')
        if kind == 'cancellation' and row.get('stage') != 'before_capture':
            raise ValueError('provider_cancellation_execution_evidence_required')
        if identity in seen and seen[identity] != value:
            raise ValueError(kind + '_identity_conflict')
        seen[identity] = value
    return {key: seen[key] for key in sorted(seen)}


def _select(payment, policies, transaction_date):
    if payment.get('fee_plan_issue'):
        raise ValueError('provider_payment_plan_conflict')
    candidates = [p for p in policies if p.get('provider') == payment['provider']
        and p.get('currency') == payment['currency'] and p.get('status') == 'active'
        and p.get('event_type', 'capture') == payment.get('event_type', 'capture')
        and day(p['effective_from']) <= transaction_date
        and (not p.get('effective_to') or transaction_date <= day(p['effective_to']))]
    plans = {str(p['plan_id']) for p in candidates if p.get('plan_id') not in (None, '')}
    if plans and payment.get('plan_id') not in (None, '') and str(payment['plan_id']) not in plans:
        raise ValueError('provider_fee_policy_incomplete_or_ambiguous')
    qualifiers = ('payment_method', 'plan_id')
    for key in qualifiers:
        value = payment.get(key)
        if value not in (None, ''):
            candidates = [p for p in candidates if p.get(key) in (None, '') or str(p[key]) == str(value)]
    for key, error in zip(qualifiers, ('provider_payment_method_required_for_fee', 'provider_payment_plan_required')):
        if payment.get(key) in (None, '') and any(p.get(key) not in (None, '') for p in candidates):
            raise ValueError(error)
    if candidates:
        def specificity(policy):
            return sum(policy.get(k) not in (None, '') for k in qualifiers)
        best = max(map(specificity, candidates))
        candidates = [p for p in candidates if specificity(p) == best]
    if len(candidates) != 1:
        raise ValueError('provider_fee_policy_incomplete_or_ambiguous')
    return candidates[0]


def _tax_parts(raw_fee, component, *, unrounded_fee=None):
    rounding_name = component.get('vat_rounding', 'half_up')
    if rounding_name not in {'half_up', 'half_even'}:
        raise ValueError('provider_fee_tax_rounding_invalid')
    rounding = ROUND_HALF_EVEN if rounding_name == 'half_even' else ROUND_HALF_UP
    basis = component.get('vat_basis', 'rounded_fee')
    if basis not in {'rounded_fee', 'unrounded_fee'}:
        raise ValueError('provider_fee_tax_basis_invalid')
    tax_base = unrounded_fee if basis == 'unrounded_fee' and unrounded_fee is not None else raw_fee
    treatment = component.get('vat_treatment')
    if treatment in {'exclusive', 'inclusive'}:
        rate = money(component.get('vat_rate'))
        if rate > 100:
            raise ValueError('provider_fee_tax_rate_invalid')
        if treatment == 'inclusive':
            if basis == 'unrounded_fee':
                tax = (tax_base * rate / (100 + rate)).quantize(CENT, rounding=rounding)
                return raw_fee - tax, tax
            net = (raw_fee / (1 + rate / 100)).quantize(CENT, rounding=rounding)
            return net, raw_fee - net
        return raw_fee, (tax_base * rate / 100).quantize(CENT, rounding=rounding)
    if treatment in {'exempt', 'not_applicable'}:
        return raw_fee, ZERO
    raise ValueError('fee_tax_policy_incomplete')


def _components_projection(payment, policy):
    if not all(policy.get(k) for k in ('id', 'confirmed_by', 'confirmed_at', 'evidence')):
        raise ValueError('provider_fee_policy_evidence_required')
    try:
        stamp = datetime.fromisoformat(str(policy['confirmed_at']).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
    except (TypeError, ValueError):
        raise ValueError('provider_fee_policy_evidence_required') from None
    if any(policy.get(k) is not None for k in ('minimum', 'maximum')):
        # Allocation of aggregate caps to refundable components needs an explicit contract.
        raise ValueError('provider_component_fee_cap_unsupported')
    components = policy.get('components')
    if not isinstance(components, list) or not components:
        raise ValueError('provider_fee_components_required')
    gross = _cash(payment['gross'])
    cancelled = _cash(payment.get('cancelled', '0'))
    voided = sum(_events(payment.get('cancellations', []), payment['currency'], 'cancellation').values(), ZERO)
    if cancelled != voided:
        raise ValueError('provider_cancellation_execution_evidence_required')
    refund_events = _events(payment.get('refunds', []), payment['currency'], 'refund')
    refunded = sum(refund_events.values(), ZERO)
    if cancelled + refunded > gross:
        raise ValueError('cancellation_refund_overlap')
    captures = {}
    for capture in payment.get('captures', []):
        identity = str(capture.get('id') or '').strip()
        value = _cash(capture.get('amount'))
        if not identity or value <= 0:
            raise ValueError('provider_capture_evidence_incomplete')
        if identity in captures and captures[identity] != value:
            raise ValueError('provider_capture_identity_conflict')
        captures[identity] = value
    captured = sum(captures.values(), ZERO)
    if captured + cancelled > gross:
        raise ValueError('provider_capture_cancellation_overlap')
    if captures and refunded > captured:
        raise ValueError('provider_refund_exceeds_capture')
    active = gross - cancelled
    ids, details = set(), []
    totals = {k: ZERO for k in ('charged_net', 'charged_tax', 'refunded_net', 'refunded_tax')}
    for component in components:
        if not isinstance(component, dict):
            raise ValueError('provider_fee_component_shape_invalid')
        identity = component.get('id')
        if not isinstance(identity, str) or not identity.strip() or identity in ids:
            raise ValueError('provider_fee_component_identity_invalid')
        ids.add(identity)
        rate = money(component.get('percentage'))
        fixed = _cash(component.get('fixed_amount'))
        if rate > 100:
            raise ValueError('provider_fee_rate_invalid')
        refund_rule = component.get('refund_fee_treatment')
        cancel_rule = component.get('cancellation_fee_treatment')
        if refunded and refund_rule not in {'retain', 'recalculate', 'full_only'}:
            raise ValueError('refund_fee_treatment_incomplete')
        if cancelled and cancel_rule not in {'retain', 'recalculate'}:
            raise ValueError('cancellation_fee_treatment_incomplete')
        base = active if cancel_rule == 'recalculate' else gross
        percentage_fee = _q(base * rate / 100)
        fixed_fee = fixed if base else ZERO
        charged = percentage_fee + fixed_fee
        unrounded_charged = base * rate / 100 + fixed_fee
        charged_net, charged_tax = _tax_parts(charged, component, unrounded_fee=unrounded_charged)
        credit_net = credit_tax = used_percentage = used_fixed = used_unrounded = ZERO
        if refunded and refund_rule != 'retain' and (refund_rule != 'full_only' or refunded == active):
            for value in refund_events.values():
                percentage_credit = min(percentage_fee - used_percentage, _q(value * rate / 100))
                fixed_credit = min(fixed_fee - used_fixed, _q(fixed_fee * value / active))
                raw_credit = min(unrounded_charged - used_unrounded, value * rate / 100 + fixed_fee * value / active)
                net_credit, tax_credit = _tax_parts(percentage_credit + fixed_credit, component, unrounded_fee=raw_credit)
                credit_net += min(net_credit, charged_net - credit_net)
                credit_tax += min(tax_credit, charged_tax - credit_tax)
                used_percentage += percentage_credit
                used_fixed += fixed_credit
                used_unrounded += raw_credit
            if refunded == active and base == active:
                # The final refund returns the remaining cent of the original fee and VAT.
                credit_net, credit_tax = charged_net, charged_tax
        for k, v in (('charged_net', charged_net), ('charged_tax', charged_tax),
                     ('refunded_net', credit_net), ('refunded_tax', credit_tax)):
            totals[k] += v
        details.append({'id': identity, 'refund_fee_treatment': refund_rule,
            'cancellation_fee_treatment': cancel_rule,
            'charged_fee_net': amount(charged_net), 'charged_fee_tax': amount(charged_tax),
            'refunded_fee_net': amount(credit_net), 'refunded_fee_tax': amount(credit_tax),
            'remaining_fee_net': amount(charged_net-credit_net),
            'remaining_fee_tax': amount(charged_tax-credit_tax)})
    net_fee = totals['charged_net'] - totals['refunded_net']
    tax_fee = totals['charged_tax'] - totals['refunded_tax']
    fees = net_fee + tax_fee
    net = gross - cancelled - refunded
    return {'gross': amount(gross), 'cancelled': amount(cancelled), 'refunded': amount(refunded),
        'net': amount(net), 'estimated_fees': amount(fees),
        'expected_receivable': amount(net-fees), 'policy_id': policy['id'],
        'plan_id': payment.get('plan_id'), 'fee_model_version': 2,
        'fee_recognition': 'estimated', 'estimated_fee_net': amount(net_fee),
        'estimated_fee_tax': amount(tax_fee),
        'refunded_fee_net': amount(totals['refunded_net']),
        'refunded_fee_tax': amount(totals['refunded_tax']),
        'fee_components': sorted(details, key=lambda r: r['id'])}


def independent_capture_without_fee_policy(payment, policies, transaction_date, error, *, created, as_of):
    """Only an independently verified positive capture; never guess a fee or refund."""
    reason = str(error)
    if reason not in {'provider_fee_policy_incomplete_or_ambiguous', 'provider_payment_method_required_for_fee',
                      'provider_fee_transaction_date_required'}:
        return False
    # Ambiguous active contracts and plan selection errors require their own review.
    if reason == 'provider_fee_policy_incomplete_or_ambiguous' and any(
           p.get('provider') == payment['provider'] and p.get('currency') == payment['currency']
           and (not p.get('payment_method') or p.get('payment_method') == payment.get('payment_method'))
           and p.get('status') == 'active' and day(p['effective_from']) <= transaction_date
           and (not p.get('effective_to') or transaction_date <= day(p['effective_to'])) for p in policies):
        return False
    captures = payment.get('captures')
    if not isinstance(captures, list) or len(captures) != 1 or payment.get('refunds') or payment.get('cancellations'):
        return False
    if _cash(payment.get('cancelled', '0')) != ZERO:
        return False
    c = captures[0]
    if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not c['id'].strip():
        return False
    from operational_payment_evidence import _instant
    return (created <= _instant(c.get('captured_at')) <= as_of
            and ZERO < _cash(c.get('amount')) <= _cash(payment.get('gross')))


def _refund_processing_fees(payment, policies, result, transaction_date, earliest_date, as_of_date):
    """Price each executed refund once, separately from credits of capture fees."""
    if payment.get('event_type', 'capture') != 'capture' or not any(
            row.get('provider') == payment['provider'] and row.get('currency') == payment['currency']
            and row.get('status') == 'active' and row.get('event_type') == 'refund' for row in policies):
        return result
    events = _events(payment.get('refunds', []), payment['currency'], 'refund')
    charges = []
    net_fee = tax_fee = total_fee = ZERO
    for identity, value in events.items():
        dates = set()
        for row in payment['refunds']:
            if str(row.get('id')) != identity or row.get('status') not in EXECUTED:
                continue
            stamp = row.get('refunded_at') or row.get('executed_at')
            if stamp:
                stamp = datetime.fromisoformat(str(stamp).replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    raise ValueError('provider_refund_fee_event_date_required')
                dates.add(stamp.astimezone(timezone(timedelta(hours=3))).date())
        if len(dates) > 1:
            raise ValueError('provider_refund_fee_event_date_conflict')
        first_day = earliest_date or transaction_date
        if dates:
            event_day = dates.pop()
            if event_day < first_day or (as_of_date and event_day > as_of_date):
                raise ValueError('provider_refund_fee_event_date_invalid')
            first_day = event_day
        elif as_of_date:
            event_day = as_of_date
        else:
            raise ValueError('provider_refund_fee_event_date_required')
        event_payment = {key: payment.get(key) for key in ('provider', 'currency', 'payment_method', 'plan_id', 'fee_plan_issue')}
        event_payment.update(gross=amount(value), event_type='refund')
        charge = provider_projection(event_payment, policies, event_day, earliest_date=first_day)
        if charge.get('estimated_fee_net') is None or charge.get('estimated_fee_tax') is None:
            raise ValueError('fee_tax_policy_incomplete')
        net_fee += Decimal(charge['estimated_fee_net'])
        tax_fee += Decimal(charge['estimated_fee_tax'])
        total_fee += Decimal(charge['estimated_fees'])
        charges.append({'refund_id': identity, 'policy_id': charge['policy_id'], 'fee_recognition': 'estimated',
                        'fee_net': charge['estimated_fee_net'], 'fee_tax': charge['estimated_fee_tax']})
    if charges:
        result['estimated_fees'] = amount(Decimal(result['estimated_fees']) + total_fee)
        result['expected_receivable'] = amount(Decimal(result['expected_receivable']) - total_fee)
        for key, value in (('estimated_fee_net', net_fee), ('estimated_fee_tax', tax_fee)):
            if result.get(key) is not None:
                result[key] = amount(Decimal(result[key]) + value)
        result['refund_processing_fees'] = charges
    return result


def provider_projection(payment, policies, transaction_date, *, earliest_date=None, as_of_date=None):
    """An undated cumulative proof needs the same applicable contract throughout its window."""
    policy = _select(payment, policies, transaction_date)
    if earliest_date and earliest_date < transaction_date:
        boundaries = {earliest_date}
        for row in policies:
            if (row.get("provider") != payment["provider"] or row.get("currency") != payment["currency"]
                    or row.get("status") != "active"
                    or row.get('event_type', 'capture') != payment.get('event_type', 'capture')):
                continue
            since = day(row["effective_from"])
            if earliest_date < since < transaction_date:
                boundaries.add(since)
            if row.get("effective_to"):
                until = day(row["effective_to"])
                if earliest_date <= until < transaction_date:
                    boundaries.add(until + timedelta(days=1))
        if any(_select(payment, policies, point) != policy for point in boundaries):
            raise ValueError("provider_fee_transaction_date_required")
    version = policy.get('fee_model_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('provider_fee_model_version_unsupported')
    if version == 2:
        result = _components_projection(payment, policy)
    else:
        result = _legacy_projection(payment, [policy], transaction_date)
    return _refund_processing_fees(payment, policies, result, transaction_date, earliest_date, as_of_date)


def fee_policy_preview(policy, value, transaction_date):
    """Calculate a saved tariff without creating or claiming an executed event."""
    payment = {'provider': policy['provider'], 'currency': policy['currency'], 'gross': str(_cash(value)),
               'payment_method': policy.get('payment_method'), 'plan_id': policy.get('plan_id'),
               'event_type': policy.get('event_type', 'capture')}
    result = provider_projection(payment, [policy], day(transaction_date))
    return {'preview': True, 'event_type': payment['event_type'], 'policy_id': result['policy_id'],
            'base_amount': result['gross'], 'fee_net': result.get('estimated_fee_net'),
            'fee_vat': result.get('estimated_fee_tax'), 'fee_total': result['estimated_fees'],
            'net_amount': result['expected_receivable'], 'currency': policy['currency']}


def _legacy_projection(payment, policies, transaction_date):
    """Expected receivable only. Actual settlement fees belong to the movement.

    Explicit return-fee terms are required whenever cancellation/refund occurs;
    historical contracts lacking these fields remain visibly incomplete.
    """
    gross, cancelled = money(payment["gross"]), money(payment.get("cancelled", "0"))
    refunds, seen = ZERO, {}
    for refund in payment.get("refunds", []):
        if refund.get("status") not in {"executed", "settled", "Refund Executed", "Settled"}:
            continue
        key = str(refund["id"])
        value = money(refund["amount"])
        if key in seen and seen[key] != value:
            raise ValueError("refund_identity_conflict")
        seen[key] = value
    refunds = sum(seen.values(), ZERO)
    if cancelled + refunds > gross:
        raise ValueError("cancellation_refund_overlap")
    matches = [p for p in policies if p.get("provider") == payment["provider"]
               and p.get("currency") == payment["currency"] and p.get("status") == "active"
               and day(p["effective_from"]) <= transaction_date
               and (not p.get("effective_to") or transaction_date <= day(p["effective_to"]))]
    if len(matches) != 1:
        raise ValueError("provider_fee_policy_incomplete_or_ambiguous")
    policy = matches[0]
    base = gross
    for value, field in ((cancelled, "cancellation_fee_treatment"), (refunds, "refund_fee_treatment")):
        if value:
            treatment = policy.get(field)
            if treatment not in {"retain", "recalculate"}:
                raise ValueError(field + "_incomplete")
            if treatment == "recalculate":
                base -= value
    fees = ZERO if base == 0 else base * money(policy["percentage"]) / 100 + money(policy["fixed_amount"])
    if base and policy.get("minimum") is not None:
        fees = max(fees, money(policy["minimum"]))
    if base and policy.get("maximum") is not None:
        fees = min(fees, money(policy["maximum"]))
    fee_net = _q(fees)
    vat = policy.get("vat_treatment")
    if vat == "exclusive":
        fees *= 1 + money(policy.get("vat_rate")) / 100
    elif vat not in {"inclusive", "exempt", "not_applicable"}:
        raise ValueError("fee_tax_policy_incomplete")
    if vat == 'inclusive':
        fee_net = _q(fees / (1 + money(policy['vat_rate']) / 100)) if policy.get('vat_rate') is not None else None
    fee_tax = _q(fees) - fee_net if fee_net is not None else None
    net = gross - cancelled - refunds
    # Fees exceeding the receivable are a payable; do not silently clamp debt.
    expected = net - Decimal(amount(fees))
    return {"gross": amount(gross), "cancelled": amount(cancelled), "refunded": amount(refunds),
            "net": amount(net), "estimated_fees": amount(fees), "expected_receivable": amount(expected),
            "estimated_fee_net": amount(fee_net) if fee_net is not None else None,
            "estimated_fee_tax": amount(fee_tax) if fee_tax is not None else None,
            "policy_id": policy["id"]}



def with_recorded_refund(payment, snapshot, reference, value, *, refunded_at=None):
    """Attach owner-recorded executed refund without treating Salla cancellation as a void.

    This is a pure source-normalization step, not a provider refund API call.
    Only explicit provider cancellation events remain cancellation amounts.
    """
    from copy import deepcopy
    result = deepcopy(payment)
    for key in ('provider', 'currency', 'gross'):
        if key not in snapshot or key not in result:
            raise ValueError('provider_refund_payment_snapshot_incomplete')
        equal = (_cash(result[key]) == _cash(snapshot[key])) if key == 'gross' else result[key] == snapshot[key]
        if not equal:
            raise ValueError('provider_refund_payment_snapshot_conflict')
    rail = snapshot.get('payment_method')
    if rail and result.get('payment_method') not in (None, '', rail):
        raise ValueError('provider_refund_payment_method_conflict')
    if rail:
        result['payment_method'] = rail
    else:
        result.pop('payment_method', None)  # The original snapshot did not prove a fee rail.
    plan = snapshot.get('plan_id')
    if plan not in (None, ''):
        if result.get('plan_id') not in (None, '', plan):
            raise ValueError('provider_refund_payment_plan_conflict')
        result['plan_id'] = plan
    # `cancelled` in the Salla source adapter is derived from order status.
    # A recorded refund is a separate post-capture event; do not subtract both.
    if not result.get('cancellations'):
        result['cancelled'] = '0'
    captures = result.setdefault('captures', [])
    for saved in snapshot.get('captures', []):
        matches = [c for c in captures if str(c.get('id')) == str(saved.get('id'))]
        if matches and any(_cash(c.get('amount')) != _cash(saved.get('amount'))
                           or c.get('captured_at') != saved.get('captured_at') for c in matches):
            raise ValueError('provider_capture_identity_conflict')
        if not matches:
            captures.append(deepcopy(saved))
    refunds = result.setdefault('refunds', [])
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError('refund_identity_missing')
    reference = reference.strip()
    value = _cash(value)
    if value <= 0:
        raise ValueError('provider_event_amount_or_currency_invalid')
    matches = [r for r in refunds if str(r.get('id')) == reference]
    if matches:
        if any(_cash(r.get('amount')) != value or r.get('status') not in EXECUTED for r in matches):
            raise ValueError('refund_identity_conflict')
    else:
        refunds.append({'id':reference, 'amount':amount(value), 'status':'executed'})
    if refunded_at:
        for refund in refunds:
            if str(refund.get('id')) == reference:
                if refund.get('refunded_at') not in (None, refunded_at):
                    raise ValueError('provider_refund_fee_event_date_conflict')
                refund['refunded_at'] = refunded_at
    return result
