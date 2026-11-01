"""Product-cost policy; independent from customer payment and shipping state.

Only native MZ2 product/service cost sources are SAR. This module never converts
customer receipts, edits openings, or treats workflow assignment as receiving.
"""
from collections.abc import Mapping

COST_CURRENCY = 'SAR'
COST_BASIS = 'mz2_product_service_cost_sar_v1'
_REVIEW = frozenset(('under_review', 'waiting_review', 'waiting_for_review',
    'pending_review', 'awaiting_review', 'انتظار المراجعة', 'بانتظار المراجعة',
    'بإنتظار المراجعة', 'بأنتظار المراجعة'))
_PROGRESS = frozenset(('in_progress', 'قيد التنفيذ', 'جاري التنفيذ'))


def _text(value):
    return ' '.join(str(value or '').strip().lower().split())


def _stage(value):
    value = _text(value)
    if value in _REVIEW:
        return 'awaiting_review'
    if value in _PROGRESS:
        return 'under_fulfillment'
    return None


def unreceived_stage(value):
    """Exact allowlist. Never label reviewed/completed/unknown as in-progress.

    A custom status is effective, not its eligible parent. Conflicting slug/name
    observations fail closed instead of preferring whichever was read first.
    """
    if isinstance(value, Mapping):
        if value.get('customized'):
            return unreceived_stage(value['customized'])
        values = [value[key] for key in ('slug', 'name', 'label') if value.get(key)]
        stages = [unreceived_stage(v) for v in values]
        return stages[0] if stages and stages[0] is not None and len(set(stages)) == 1 else None
    return _stage(value)


def order_unreceived_stage(raw):
    """Use only canonical order observations; an independent delivery update
    must not rewrite the product-estimate eligibility of the actual order.
    """
    if not isinstance(raw, Mapping):
        return None
    status = raw.get('status')
    stage = unreceived_stage(status)
    if raw.get('status_slug') and not (isinstance(status, Mapping) and status.get('customized')):
        other = unreceived_stage(raw['status_slug'])
        return stage if stage is not None and stage == other else None
    return stage


def require_sar_cost(*records):
    """Reject explicitly foreign cost evidence, not foreign sales currency.

    Call only for COST records/amounts. A missing currency in a legacy MZ2 cost
    record uses that collection's established SAR contract, never order currency.
    """
    for record in records:
        if not isinstance(record, Mapping):
            continue
        for name in ('cost_currency', 'currency'):
            code = record.get(name)
            if code is not None and _text(code).upper() != COST_CURRENCY:
                raise ValueError('product_cost_currency_evidence_incomplete')
    return COST_CURRENCY
