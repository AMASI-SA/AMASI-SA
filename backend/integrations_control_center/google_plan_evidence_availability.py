"""Saved-plan source-date diagnostics through one bounded indexed local read.

Unattached owner-only router: an integrator must attach it to the authenticated
Google router. No provider, source writer, sales aggregate or product matcher is
called. The compound index is declared by server.py bootstrap, not proven live
by this module: a missing/incompatible index fails closed. A successful hinted
read proves neither source coverage nor a consistent multi-row snapshot.
"""
import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

from fastapi import Depends, Query
from pymongo.errors import PyMongoError

from .google_ads_client import problem
from .google_ads_management import principal
from .google_commercial_plan import PLANS
from .google_order_source_binding import bound_salla_order_date

SOURCE_INDEX = [('user_id', 1), ('order_date', -1)]
SOURCE_PROJECTION = {'_id': 0, 'user_id': 1, 'order_id': 1, 'order_number': 1, 'order_date': 1,
    'raw_by_source.salla_direct.id': 1, 'raw_by_source.salla_direct.reference_id': 1,
    'raw_by_source.salla_direct.date': 1, 'raw_by_source.salla_direct.created_at': 1}


def _changed():
    return problem('google_commercial_plan_revision_conflict', 'تغيرت الخطة؛ أعد تحميلها قبل مراجعة الأدلة.', 409)


def _windows(as_of):
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError('aware server clock required')
    try:
        last = as_of.astimezone(ZoneInfo('Asia/Riyadh')).date() - timedelta(days=1)
        return {'last_14_days': (last - timedelta(days=13), last),
            'previous_14_days': (last - timedelta(days=27), last - timedelta(days=14)),
            'last_30_days': (last - timedelta(days=29), last),
            'last_90_days': (last - timedelta(days=89), last)}
    except (OverflowError, ValueError):
        raise ValueError('server clock outside supported range') from None


def _empty_windows(as_of, *, requested):
    return {key: {'period': {'from': first.isoformat(), 'to': last.isoformat(), 'timezone': 'Asia/Riyadh'},
        'observed_bound_source_dates': None, 'status': 'unavailable' if requested else 'not_requested',
        'coverage_complete': False} for key, (first, last) in _windows(as_of).items()}


async def read_plan_source_date_windows(db, *, owner_id, as_of, limit=1000):
    """Classify source dates in a candidate subset, never product sales.

    The +/-1 normalized-day expansion remains candidate discovery. Large valid
    orders are deliberately excluded by BSON size; no excluded count or zero
    sales is inferred. On sentinel truncation all diagnostic counts disappear.
    """
    windows = _windows(as_of)
    if (not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 240 or owner_id != owner_id.strip()
        or type(limit) is not int or not 1 <= limit <= 1000):
        raise ValueError('invalid diagnostic boundary')
    lower = (windows['last_90_days'][0] - timedelta(days=1)).isoformat()
    upper = (windows['last_90_days'][1] + timedelta(days=1)).isoformat()
    query = {'user_id': owner_id, 'order_date': {'$gte': lower, '$lte': upper},
        '$expr': {'$lte': [{'$bsonSize': '$$ROOT'}, 16384]}}
    cursor = db['unified_orders'].find(query, SOURCE_PROJECTION).hint(SOURCE_INDEX).limit(limit + 1).max_time_ms(3000)
    rows = await cursor.to_list(length=limit + 1)
    for row in rows:
        if (not isinstance(row, dict) or row.get('user_id') != owner_id or not isinstance(row.get('order_date'), str)
            or not lower <= row['order_date'] <= upper):
            raise ValueError('query scope unverified')
        try:
            if date.fromisoformat(row['order_date']).isoformat() != row['order_date']:
                raise ValueError()
        except ValueError:
            raise ValueError('candidate normalized date unverified') from None
    truncated = len(rows) > limit
    result = _empty_windows(as_of, requested=True)
    unknown = None if truncated else 0
    if not truncated:
        counts = {key: 0 for key in windows}
        for row in rows:
            evidence = bound_salla_order_date(row, owner_id=owner_id)
            if evidence['status'] != 'observed_bound_source_instant':
                unknown += 1
                continue
            for key, (first, last) in windows.items():
                if first.isoformat() <= evidence['business_date_riyadh'] <= last.isoformat():
                    counts[key] += 1
        for key, value in counts.items():
            result[key].update(observed_bound_source_dates=value, status='source_dates_observed' if value else 'not_observed')
    else:
        for window in result.values(): window['status'] = 'truncated'
    return {'windows': result,
        'read_diagnostic': {'candidate_range': {'from': lower, 'to': upper, 'basis': 'unified_orders.order_date'},
            'normalized_source_timezone': 'unproven', 'source_projection': 'identity_and_source_timestamps_only',
            'compound_index_hint': 'user_id_1_order_date_-1', 'production_index_verified': False,
            'candidate_document_limit': limit, 'document_bson_limit_bytes': 16384, 'max_time_ms': 3000,
            'observed_subset_documents': None if truncated else len(rows), 'observed_unverified_source_dates': unknown,
            'truncated': truncated},
        'coverage_complete': False, 'snapshot_consistent': False, 'product_mapping_verified': False,
        'limitations': ['candidate_query_does_not_prove_coverage', 'normalized_day_drift_may_omit_orders',
            'oversized_documents_excluded_without_count', 'source_snapshot_mutable',
            'diagnostic_source_date_counts_are_not_product_sales', 'product_mapping_unverified',
            'cancellations_refunds_fx_costs_unverified', 'provider_watermark_missing', 'no_production_index_or_performance_claim']}


def _product_scope(fields):
    pinned, excluded = fields.get('pinned_product_ids'), fields.get('excluded_product_ids')
    if (not isinstance(pinned, list) or not isinstance(excluded, list) or len(pinned) > 100 or len(excluded) > 100
        or any(not isinstance(value, str) or not re.fullmatch(r'[0-9]{1,30}', value) for value in pinned + excluded)
        or len(set(pinned)) != len(pinned) or len(set(excluded)) != len(excluded)):
        raise problem('google_plan_evidence_product_scope_unproved', 'راجع معرفات المنتجات المثبتة والمستبعدة أولًا.', 422)
    return [value for value in pinned if value not in excluded]


def attach_google_plan_evidence_availability_routes(router, db, current_user, *, clock=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    async def owner(user=Depends(current_user)):
        actor = await principal(db, user, write=False)
        if not actor.get('owner'):
            raise problem('google_ads_owner_required', 'مراجعة أدلة الخطة متاحة للمالك فقط.', 403)
        return actor

    @router.get('/google_ads/commercial-plans/{plan_id}/evidence-availability')
    async def evidence_availability(plan_id: str, expected_revision: int = Query(ge=1, le=2147483647), actor=Depends(owner)):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', plan_id):
            raise problem('google_commercial_plan_id_invalid', 'معرف الخطة غير صالح.', 422)
        scope = {'user_id': actor['user_id'], 'plan_id': plan_id}
        try:
            async with asyncio.timeout(6):
                prior = await db[PLANS].find_one(scope, {'_id': 0, 'revision': 1, 'fields': 1}, max_time_ms=1000)
                if not prior: raise problem('google_commercial_plan_not_found', 'الخطة غير موجودة.', 404)
                if type(prior.get('revision')) is not int or prior['revision'] != expected_revision or not isinstance(prior.get('fields'), dict):
                    raise _changed()
                fields = deepcopy(prior['fields']); ids = _product_scope(fields)
                as_of = clock()
                if ids:
                    result = await read_plan_source_date_windows(db, owner_id=actor['user_id'], as_of=as_of)
                else:
                    result = {'windows': _empty_windows(as_of, requested=False), 'read_diagnostic': {'requested': False},
                        'coverage_complete': False, 'snapshot_consistent': False, 'product_mapping_verified': False,
                        'limitations': ['no_unexcluded_pinned_products', 'product_sales_not_established']}
                current = await db[PLANS].find_one(scope, {'_id': 0, 'revision': 1, 'fields': 1}, max_time_ms=1000)
                if not current or type(current.get('revision')) is not int or current['revision'] != expected_revision or current.get('fields') != fields:
                    raise _changed()
        except (TimeoutError, PyMongoError, ValueError):
            raise problem('google_plan_evidence_read_unavailable', 'تعذرت قراءة أدلة الخطة ضمن حدود القراءة الآمنة. لا توجد نتيجة مبيعات مثبتة.', 503) from None
        return {'mode': 'source_date_availability_diagnostic', 'plan_id': plan_id, 'plan_revision': expected_revision,
            'as_of': as_of.isoformat(), 'pinned_product_ids_reviewed': ids, **result,
            'products': [{'product_id': value, 'sales': None, 'units_sold': None, 'profitability': None} for value in ids],
            'source_writes': 0, 'provider_writes': 0, 'execution_authorized': False}
