"""Prove stored hierarchy facts against their producing runs, not today's run."""
from datetime import date, timedelta
from typing import Any

from .sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION


async def total_period_complete(
    db: Any, *, user_id: str, account_id: str, entity_type: str,
    date_from: date, date_to: date, action_report_time: str,
    facts: list[dict[str, Any]],
) -> bool:
    expected = {(date_from + timedelta(days=i)).isoformat()
                for i in range((date_to - date_from).days + 1)}
    if not expected or not facts:
        return False
    dates_by_run: dict[str, set[str]] = {}
    for fact in facts:
        run_id = str(fact.get('sync_run_id') or '')
        day = str(fact.get('report_date') or '')
        if not run_id or day not in expected or (fact.get('coverage') or {}).get('status') != 'complete':
            return False
        dates_by_run.setdefault(run_id, set()).add(day)
    if set().union(*dates_by_run.values()) != expected or len(dates_by_run) > 1000:
        return False
    field = {'campaign': 'campaign_sync_status', 'ad_squad': 'ad_squad_sync_status', 'ad': 'ad_sync_status'}[entity_type]
    for run_id, days in dates_by_run.items():
        run = await db[SNAPCHAT_SYNC_RUNS_COLLECTION].find_one({
            'user_id': str(user_id), 'provider': 'snapchat_ads',
            'ad_account_id': str(account_id), 'sync_run_id': run_id,
            'status': 'complete', 'financial_sync_status': 'complete', field: 'complete',
            'request_window.date_from': {'$lte': min(days)},
            'request_window.date_to': {'$gte': max(days)},
            'request_window.action_report_time': action_report_time,
        }, {'_id': 0, 'sync_run_id': 1})
        if not run:
            return False
    return True
