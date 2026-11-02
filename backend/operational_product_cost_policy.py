"""Shared remaining-work rules; sale currency never establishes cost currency."""
UNRECEIVED_STATUSES = frozenset({
    'under_review', 'waiting_review', 'waiting_for_review', 'pending_review',
    'in_progress', 'processing', 'انتظار المراجعة', 'بانتظار المراجعة',
    'بإنتظار المراجعة', 'قيد التنفيذ', 'جاري التنفيذ',
})


def unreceived_eligible(status):
    if isinstance(status, dict):
        status = status.get('slug') or status.get('name')
    return str(status or '').strip().lower() in UNRECEIVED_STATUSES


def require_sar_cost(record):
    """The existing MZ2 cost catalog's default is SAR, not order currency.

    Explicit foreign cost needs its own approved valuation; it must not be
    relabelled SAR or converted using the customer's payment exchange rate.
    """
    if str((record or {}).get('cost_currency') or 'SAR').upper() != 'SAR':
        raise ValueError('mz2_product_cost_currency_requires_review')
