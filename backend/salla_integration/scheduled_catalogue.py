"""Resumable provider-only refresh of the Products V2 page; no deletions."""
import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import uuid

from product_v2_routes import PRODUCTS, SYNC_RUNS, ensure_product_v2_indexes
from product_v2_details_routes import _details_patch, _unwrap
from product_cost_revision import bump_product_cost_revision, salla_cost_fingerprint
from report_option_identity import retain_option_identity_labels
from resource_governor import governor, ResourcePressure
from .service import call_salla
from .sync import _pagination_number


def _provider_updated_at(row):
    """Only an explicit provider timestamp establishes the 24-hour scope."""
    value = row.get('updated_at') or row.get('date_updated')
    zone = None
    if isinstance(value, dict):
        zone, value = value.get('timezone'), value.get('date')
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            if not zone:
                return None
            stamp = stamp.replace(tzinfo=ZoneInfo(zone))
        return stamp.astimezone(timezone.utc)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        return None


async def refresh_catalogue(db, user_id, *, run_slot, refresh_window=None, existing_only=False):
    if type(existing_only) is not bool:
        raise ValueError('existing_only must be boolean')
    if existing_only and (not refresh_window or len(refresh_window) != 2
                          or any(value.tzinfo is None for value in refresh_window)
                          or refresh_window[1] - refresh_window[0] != timedelta(hours=24)):
        raise ValueError('existing-only catalogue refresh requires an aware 24-hour window')
    await ensure_product_v2_indexes(db)
    run_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    counters = dict(updated=0, unchanged=0, errors_count=0, pages_fetched=0)
    if existing_only:
        counters.update(skipped=0, missing_existing=0, incomplete_count=0)
    await db[SYNC_RUNS].insert_one({'id': run_id, 'user_id': user_id,
        'trigger': 'scheduled_daily_10_riyadh' if existing_only else 'scheduled_catalogue',
        'mode': 'update_only' if existing_only else 'import',
        'status': 'running', 'started_at': now, **counters})
    seen = set()
    previous_total = None
    exhausted = False
    try:
        for page in range(1, 251):
            if governor.peek()[0] in {'blocked', 'cancel'}:
                raise ResourcePressure('resource_pressure')
            response = await call_salla(db, user_id, 'GET', '/products',
                                       params={'page': page, 'per_page': 30, 'format': 'light'})
            rows = response.get('data') if isinstance(response, dict) else None
            if not isinstance(rows, list):
                raise ValueError('invalid_catalogue_page')
            pagination = response.get('pagination') or {}
            if not isinstance(pagination, dict):
                raise ValueError('invalid_catalogue_pagination')
            total = _pagination_number(pagination, ('totalPages', 'total_pages', 'last_page'), minimum=0)
            current = _pagination_number(pagination, ('currentPage', 'current_page', 'page'), minimum=1)
            if current is not None and current != page:
                raise ValueError('incorrect_catalogue_page')
            if page > 1 and previous_total != total:
                raise ValueError('catalogue_page_count_changed')
            previous_total = total
            counters['pages_fetched'] += 1
            if not rows:
                exhausted = not total or page > total
                break
            page_ids = set()
            for row in rows:
                if not isinstance(row, dict) or not row.get('id'):
                    counters['errors_count'] += 1
                    continue
                pid = str(row['id'])
                if pid in seen or pid in page_ids:
                    raise ValueError('repeated_catalogue_product')
                page_ids.add(pid)
                selector = {'user_id': user_id, 'salla_product_id': pid}
                existing = await db[PRODUCTS].find_one(selector) or {}
                if existing_only:
                    if not existing:
                        counters['missing_existing'] += 1
                        counters['skipped'] += 1
                        continue
                    changed_at = _provider_updated_at(row)
                    if changed_at is None:
                        counters['incomplete_count'] += 1
                        counters['skipped'] += 1
                        continue
                    if not refresh_window[0] <= changed_at <= refresh_window[1]:
                        counters['skipped'] += 1
                        continue
                if existing.get('scheduled_catalogue_slot') == run_slot:
                    counters['unchanged'] += 1
                    continue
                try:
                    details = _unwrap(await call_salla(db, user_id, 'GET', f'/products/{pid}'))
                    if not details or str(details.get('id')) != pid:
                        raise ValueError('invalid_catalogue_details')
                    if existing_only:
                        detail_time = _provider_updated_at(details)
                        if detail_time is None:
                            counters['incomplete_count'] += 1
                            counters['skipped'] += 1
                            continue
                        if not refresh_window[0] <= detail_time <= refresh_window[1]:
                            counters['skipped'] += 1
                            continue
                    patch = _details_patch(details, user_id=user_id)
                    retain_option_identity_labels(existing, patch)
                    if existing_only:
                        if not any(key in details for key in ('options', 'product_options')):
                            for key in ('options', 'options_count'):
                                if key in existing:
                                    patch[key] = existing[key]
                        if not any(key in details for key in ('variants', 'skus', 'product_variants')):
                            for key in ('variants', 'variants_count'):
                                if key in existing:
                                    patch[key] = existing[key]
                    # Do not clobber a concurrent edit/refresh after provider I/O.
                    guard = {**selector, 'updated_at': existing.get('updated_at')}
                    persisted_at = datetime.now(timezone.utc)
                    if existing:
                        result = await db[PRODUCTS].update_one(guard, {'$set': {
                            **patch, 'updated_at': persisted_at,
                        }})
                        if not result.matched_count:
                            raise RuntimeError('concurrent_catalogue_change')
                    elif not existing_only:
                        # Unique merchant+provider identity makes a race retryable.
                        await db[PRODUCTS].insert_one({**patch, 'id': uuid.uuid4().hex,
                            'created_at': now, 'updated_at': persisted_at})
                    if (salla_cost_fingerprint(existing) != salla_cost_fingerprint(patch)
                            or existing.get('options') != patch.get('options')):
                        await bump_product_cost_revision(db, user_id)
                    stamped = await db[PRODUCTS].update_one({**selector, 'updated_at': persisted_at},
                        {'$set': {'scheduled_catalogue_slot': run_slot}})
                    if not stamped.matched_count:
                        raise RuntimeError('concurrent_catalogue_change')
                    counters['updated'] += 1
                except Exception:
                    counters['errors_count'] += 1
                await asyncio.sleep(0.25)
            seen.update(page_ids)
            if total is not None and page >= total:
                exhausted = page == total
                break
            await asyncio.sleep(0.25)
        result = {'run_id': run_id, **counters, 'truncated': not exhausted}
        await db[SYNC_RUNS].update_one({'id': run_id}, {'$set': {
            **result, 'status': 'completed' if exhausted and not counters['errors_count'] and not counters.get('incomplete_count') else 'partial',
            'ended_at': datetime.now(timezone.utc)}})
        return result
    except BaseException as exc:
        await db[SYNC_RUNS].update_one({'id': run_id}, {'$set': {
            **counters, 'status': 'interrupted' if isinstance(exc, asyncio.CancelledError) else 'failed',
            'error_type': type(exc).__name__, 'ended_at': datetime.now(timezone.utc)}})
        raise
