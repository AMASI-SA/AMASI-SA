"""Bounded read-only order coverage diagnostic, not a sales aggregate.

The unified_orders date is the repository's normalized date string. Source
sync completeness, cancellation/refund allocation, exact line mapping and
order-level FX are not established by this reader. No empty query becomes a
zero-sales assertion. Caller must derive owner from the trusted session and
as_of from the server clock. No customer details or individual orders returned.
"""
from datetime import date, datetime, timedelta
import re
from zoneinfo import ZoneInfo

PROJECTION = {'_id': 0, 'user_id': 1, 'order_date': 1, 'order_date_inferred': 1,
              'order_number': 1, 'status': 1, 'products.product_id': 1,
              'products.parent_product_id': 1, 'products.quantity': 1}


async def read_product_sales_evidence(db, *, owner_id, product_ids, date_from, date_to, as_of, limit=10000):
    if not isinstance(owner_id, str) or not owner_id or owner_id != owner_id.strip():
        raise ValueError('invalid owner')
    if (not isinstance(product_ids, list) or not 1 <= len(product_ids) <= 100
        or any(not isinstance(p, str) or not re.fullmatch(r'[0-9]{1,30}',p) for p in product_ids)
        or len(set(product_ids)) != len(product_ids)):
        raise ValueError('invalid product identities')
    if (not isinstance(as_of,datetime) or as_of.tzinfo is None or as_of.utcoffset() is None
        or type(limit) is not int or not 1 <= limit <= 10000):
        raise ValueError('invalid read boundary')
    try:
        if not isinstance(date_from,str) or not isinstance(date_to,str): raise ValueError()
        start,end = date.fromisoformat(date_from),date.fromisoformat(date_to)
        if start.isoformat()!=date_from or end.isoformat()!=date_to: raise ValueError()
    except ValueError:
        raise ValueError('invalid date period') from None
    last_closed = as_of.astimezone(ZoneInfo('Asia/Riyadh')).date() - timedelta(days=1)
    if start > end or end > last_closed or (end-start).days > 366:
        raise ValueError('period must be closed and bounded')
    query={'user_id':owner_id,'order_date':{'$gte':date_from,'$lte':date_to}}
    rows=await db['unified_orders'].find(query,PROJECTION).to_list(length=limit+1)
    for row in rows:
        if (not isinstance(row,dict) or row.get('user_id') != owner_id
            or not isinstance(row.get('order_date'),str) or not date_from <= row['order_date'] <= date_to):
            raise ValueError('order scope unproved')
        try:
            if date.fromisoformat(row['order_date']).isoformat() != row['order_date']:
                raise ValueError()
        except ValueError:
            raise ValueError('order date unproved') from None
    truncated=len(rows)>limit
    return {'period':{'from':date_from,'to':date_to,'timezone':'Asia/Riyadh'},
        'completeness':'unproven','observed_documents':None if truncated else len(rows),'truncated':truncated,
        'product_attribution_verified':False,'cancellation_refunds_reconciled':False,
        'source_writes':0,'provider_writes':0,'execution_authorized':False,
        'products':[{'product_id':p,'units_sold':None,'sales_native':None,'profitability':None} for p in product_ids],
        'limitations':['source_sync_completeness_unproven','product_line_identity_unproven',
            'cancellation_and_refunds_unreconciled','order_currency_and_fx_unverified',
            'cost_and_customization_unverified']}
