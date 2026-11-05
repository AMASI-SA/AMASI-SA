"""Constant-size banner reads; never start an audit, provider call or worker.

Full, explicitly requested reports may publish their existing evidence here.
The banner only reads the snapshot-bound cache and fails closed on expiry.
"""
from datetime import datetime, timezone
import logging

from integrations.qoyod.invoice_presence import verified_unsent_summary

_FIELDS = ('count', 'verification_required', 'verified_at', 'expires_at', 'complete')
_PROJECTION = {'_id': 0, 'invoice_presence_summary': 1,
               'invoice_presence_snapshot.started_at': 1,
               'invoice_presence_snapshot.complete': 1}


def _unknown():
    return {key: value for key, value in verified_unsent_summary([], None).items()
            if key in _FIELDS}


async def read_header_summary(db, *, user_id, orders_user_id, now=None):
    # Exactly one settings read, with a bounded projection; no order collections.
    settings = await db.qoyod_settings.find_one({'user_id': str(user_id)}, _PROJECTION) or {}
    cached = settings.get('invoice_presence_summary') or {}
    snapshot = settings.get('invoice_presence_snapshot') or {}
    now = now or datetime.now(timezone.utc)
    try:
        valid = (snapshot.get('complete') is True
                 and cached.get('snapshot_started_at') == snapshot.get('started_at')
                 and cached.get('orders_user_id') == str(orders_user_id)
                 and cached.get('complete') is True
                 and datetime.fromisoformat(cached['verified_at']) <= now
                 < datetime.fromisoformat(cached['expires_at'])
                 and type(cached.get('count')) is int and cached['count'] >= 0)
    except (KeyError, TypeError, ValueError):
        valid = False
    return {'invoice_presence': {k: cached.get(k) for k in _FIELDS} if valid else _unknown()}


async def cache_report_summary(db, *, user_id, orders_user_id, proofs,
                               from_date, to_date=None, search=None, salla_status=None,
                               scan_truncated=False, now=None, settings=None):
    """Reuse an existing full audit. Never scan to populate a missing cache."""
    now = now or datetime.now(timezone.utc)
    if (str(from_date) != '2026-07-01' or search or salla_status
            or (to_date is not None and str(to_date)[:10] != now.date().isoformat())):
        return
    collection = getattr(db, 'qoyod_settings', None)
    if collection is None:
        return
    try:
        if settings is None:
            settings = await collection.find_one({'user_id': str(user_id)},
                {'_id': 0, 'invoice_presence_snapshot': 1}) or {}
        snapshot = settings.get('invoice_presence_snapshot') or {}
        if not snapshot.get('started_at'):
            return
        presence = verified_unsent_summary(proofs, snapshot, now=now,
                                          scan_truncated=scan_truncated)
        summary = {k: presence[k] for k in _FIELDS}
        summary.update(orders_user_id=str(orders_user_id),
                       snapshot_started_at=snapshot['started_at'])
        # A newer sync invalidates this result, even if it completed mid-report.
        await collection.update_one({'user_id': str(user_id),
            'invoice_presence_snapshot.started_at': snapshot['started_at']},
            {'$set': {'invoice_presence_summary': summary}})
    except Exception:
        # Optional UI cache must never break reconciliation or sending.
        logging.getLogger(__name__).warning('Qoyod header summary cache unavailable')
