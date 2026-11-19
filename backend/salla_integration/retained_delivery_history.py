"""Recover a prior delivered Salla order state on the existing visible-refresh budget."""
from datetime import datetime, timedelta, timezone

from operational_balance_store import check_authorization
from operational_delivery_evidence import _text
from operational_delivery_register import HISTORY_CHECK, MARKER, history_version, observed_native_delivery
from salla_integration.service import call_salla


async def acquire(db, owner, candidates, *, limit):
    selected = found = 0
    for candidate in candidates:
        if selected >= limit:
            break
        await check_authorization()
        oid, number = candidate['order_id'], candidate['order_number']
        key = {'user_id': owner, 'order_number': number, MARKER: {'$exists': False}}
        projection = {'raw_by_source.salla_direct.' + name: 1 for name in
                      ('id', 'status', 'date.updated', 'updated_at', 'date_updated')}
        row = await db.unified_orders.find_one(key, {**projection, HISTORY_CHECK: 1})
        raw = ((row or {}).get('raw_by_source') or {}).get('salla_direct') or {}
        if _text(raw.get('id')) != oid or history_version(raw) != candidate['version']:
            continue
        previous = row.get(HISTORY_CHECK) or {}
        state = previous if previous.get('version') == candidate['version'] else {}
        now = datetime.now(timezone.utc)
        if state.get('complete') or str(state.get('retry_after') or '') > now.isoformat():
            continue
        page = state.get('next_page', 1)
        key.update({'raw_by_source.salla_direct.id': raw['id'],
                    'raw_by_source.salla_direct.status': raw.get('status'),
                    HISTORY_CHECK: previous if HISTORY_CHECK in row else {'$exists': False}})
        for clock in ('updated_at', 'date_updated'):
            key['raw_by_source.salla_direct.' + clock] = raw.get(clock)
        date = raw.get('date') if isinstance(raw.get('date'), dict) else {}
        key['raw_by_source.salla_direct.date.updated'] = date.get('updated')
        claim = {**state, 'version': candidate['version'], 'retry_after': (now + timedelta(minutes=5)).isoformat()}
        saved = await db.unified_orders.update_one(key, {'$set': {HISTORY_CHECK: claim}})
        if not saved.matched_count:
            continue
        selected += 1
        response = await call_salla(db, owner, 'GET', f'/orders/{oid}/histories', params={'page': page, 'per_page': 100})
        rows, pages = (response or {}).get('data'), (response or {}).get('pagination') or {}
        if not isinstance(rows, list) or response.get('success') is False or pages.get('currentPage') != page:
            continue
        marker = observed_native_delivery(rows, oid)
        progress = {'version': candidate['version']}
        fields = {HISTORY_CHECK: progress}
        if marker:
            fields[MARKER] = marker
        else:
            total, last = pages.get('total'), pages.get('totalPages')
            if type(total) is not int or type(last) is not int or last < page:
                continue
            if page > 1 and (state.get('total'), state.get('pages')) != (total, last):
                progress.update(next_page=1)
            else:
                seen = state.get('seen', 0) + len(rows)
                if page < last and rows:
                    progress.update(next_page=page + 1, seen=seen, total=total, pages=last)
                elif page == last and seen == total:
                    progress.update(complete=True)
                else:
                    continue
        # A concurrent delivery webhook wins. Never replace the order's status or its executor.
        key[HISTORY_CHECK] = claim
        applied = await db.unified_orders.update_one(key, {'$set': fields})
        found += int(bool(marker and applied.matched_count))
    return {'checked': selected, 'delivered': found}
