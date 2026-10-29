"""Partial stored raw-line observations, never complete sales or profitability.

The saved Salla raw snapshot can retain old items. Qualifying quantities are a
lower bound only on internally consistent saved observations, NOT net sales,
current demand, complete orders or physical stock. No provider attribution.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import re
from zoneinfo import ZoneInfo

from accounting_currency import CURRENCY_CODES
from .google_order_source_binding import bound_salla_order_date, _id

WINDOW_DAYS={'last_14_days':(13,0),'previous_14_days':(27,14),'last_30_days':(29,0),'last_90_days':(89,0)}
_ALLOWED={'completed','delivered'}
_QUANTITY=re.compile(r'^[0-9]{1,12}(?:\.[0-9]{1,6})?$')


def windows(as_of):
    if not isinstance(as_of,datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError('aware server observation time required')
    last=as_of.astimezone(ZoneInfo('Asia/Riyadh')).date()-timedelta(days=1)
    return {name:{'from':(last-timedelta(days=a)).isoformat(),'to':(last-timedelta(days=b)).isoformat(),'timezone':'Asia/Riyadh'} for name,(a,b) in WINDOW_DAYS.items()}


def _slug(value):
    if isinstance(value,dict):value=value.get('slug')
    return value if isinstance(value,str) else None


def _refund_marker(row,raw):
    # Absence is NOT proof of no returns. Any supplied nonzero/ambiguous
    # return information makes this order ineligible even for gross observation.
    for source in (row,raw):
        for key in ('cancelled_at','canceled_at','refunded_at','refunds','returns','refunded_amount','refund_amount','returned_quantity'):
            if key in source and source[key] not in (None,False,0,'0',[],{}):return True
    return False


def _eligible(row,raw):
    source_status=_slug(raw.get('status'))
    if source_status not in _ALLOWED:return False
    if row.get('order_status_slug') is not None and row['order_status_slug'] != source_status:return False
    if row.get('order_status') is not None:
        display=raw.get('status')
        allowed_labels={source_status}
        if isinstance(display,dict):
            allowed_labels.update(v for v in (display.get('name'),display.get('customized')) if isinstance(v,str) and v)
        if not isinstance(row['order_status'],str) or row['order_status'] not in allowed_labels:return False
    payment=raw.get('payment_status')
    nested=raw.get('payment')
    nested=nested.get('status') if isinstance(nested,dict) else None
    raw_values=[v for v in (payment,nested) if v is not None]
    values=[*raw_values,*([row['payment_status']] if row.get('payment_status') is not None else [])]
    return bool(raw_values) and all(v=='paid' for v in values) and not _refund_marker(row,raw)


def _currency(row,raw):
    currencies=[]
    raw_currency_present=any(key in raw for key in ('currency','original_currency'))
    for source in (row,raw):
        for key in ('currency','original_currency'):
            if key in source:
                if not isinstance(source[key],str) or source[key] not in CURRENCY_CODES:return None
                currencies.append(source[key])
    amounts=raw.get('amounts')
    total=amounts.get('total') if isinstance(amounts,dict) else None
    if isinstance(total,dict) and 'currency' in total:
        raw_currency_present=True
        if not isinstance(total['currency'],str) or total['currency'] not in CURRENCY_CODES:return None
        currencies.append(total['currency'])
    return currencies[0] if raw_currency_present and currencies and len(set(currencies))==1 else None


def _lines(raw):
    items=raw.get('items')
    if not isinstance(items,list) or not 1<=len(items)<=100:return None
    result=[];seen=set()
    for item in items:
        if not isinstance(item,dict):return None
        identity=_id(item.get('id'))
        if identity is None or identity in seen:return None
        seen.add(identity)
        product=item.get('product')
        nested=product.get('id') if isinstance(product,dict) else None
        ids=[_id(v) for v in (nested,item.get('product_id')) if v is not None]
        if not ids or any(v is None for v in ids) or len(set(ids))!=1:return None
        parents=[v for v in ((product.get('parent_id') if isinstance(product,dict) else None),item.get('parent_product_id')) if v is not None]
        if any(_id(parent)!=ids[0] for parent in parents):return None
        quantity=item.get('quantity')
        if type(quantity) not in (str,int,float) or not _QUANTITY.fullmatch(str(quantity)):return None
        quantity=Decimal(str(quantity))
        if quantity<=0:return None
        if any(key in item and item[key] not in (None,False,0,'0',[],{}) for key in ('refunds','returns','refunded_quantity','returned_quantity')):return None
        result.append((identity,ids[0],quantity))
    return result


def observe_product_lines(rows,*,owner_id,product_ids,as_of,truncated=False):
    periods=windows(as_of)
    if (not isinstance(owner_id,str) or not owner_id or owner_id!=owner_id.strip()
        or not isinstance(product_ids,list) or len(product_ids)>100 or len(set(product_ids))!=len(product_ids)
        or any(not isinstance(p,str) or _id(p)!=p for p in product_ids)
        or not isinstance(rows,list) or len(rows)>1001 or type(truncated) is not bool or len(rows)>1000 and not truncated):
        raise ValueError('invalid observation scope')
    diagnostics={'observed_documents':None if truncated else len(rows),'duplicate_order_documents':None if truncated else 0,
        'rejected_documents':None if truncated else 0,'unmatched_lines':None if truncated else 0}
    output={p:{} for p in product_ids}
    # Duplicate either source ID or number rejects every affected document,
    # including divergent aliases. There is no first/newest-writer selection.
    id_counts=Counter();number_counts=Counter()
    for row in rows:
        if isinstance(row,dict):
            for key,counts in (('order_id',id_counts),('order_number',number_counts)):
                value=_id(row.get(key))
                if value:counts[value]+=1
    if not truncated:
        for row in rows:
            if not isinstance(row,dict):diagnostics['rejected_documents']+=1;continue
            if id_counts[_id(row.get('order_id'))]>1 or number_counts[_id(row.get('order_number'))]>1:
                diagnostics['duplicate_order_documents']+=1;continue
            bound=bound_salla_order_date(row,owner_id=owner_id)
            sources=row.get('raw_by_source')
            raw=sources.get('salla_direct') if isinstance(sources,dict) else None
            if bound['status']!='observed_bound_source_instant' or not isinstance(raw,dict):
                diagnostics['rejected_documents']+=1;continue
            currency=_currency(row,raw);lines=_lines(raw)
            if currency is None or lines is None or not _eligible(row,raw):
                diagnostics['rejected_documents']+=1;continue
            day=bound['business_date_riyadh']
            if not periods['last_90_days']['from']<=day<=periods['last_90_days']['to']:
                diagnostics['rejected_documents']+=1;continue
            by_product=defaultdict(list)
            for identity,pid,quantity in lines:
                if pid in output:by_product[pid].append((identity,quantity))
                else:diagnostics['unmatched_lines']+=1
            for pid,matched in by_product.items():
                group=output[pid].setdefault(currency,{name:{'orders':0,'lines':0,'units':Decimal(0)} for name in periods})
                for name,period in periods.items():
                    if period['from']<=day<=period['to']:
                        group[name]['orders']+=1;group[name]['lines']+=len(matched)
                        group[name]['units']+=sum((q for _,q in matched),Decimal(0))
    products=[]
    for pid,groups in output.items():
        products.append({'product_id':pid,'currency_groups':[{'currency_code':currency,
            'account_scope':'owner_store_projection','platform_account_id':None,
            'windows':{name:{'observed_orders':w['orders'] if w['orders'] else None,'observed_lines':w['lines'] if w['lines'] else None,
                             'observed_units':format(w['units'],'f') if w['lines'] else None} for name,w in group.items()}} for currency,group in sorted(groups.items())],
            'sales_native':None,'profitability':None,'growth_14_days_percent':None,'success_classification':'not_established'})
    return {'mode':'stored_product_line_observations','as_of':as_of.isoformat(),'windows':periods,'products':products,
        'diagnostics':diagnostics,'truncated':truncated,'coverage_complete':False,'snapshot_consistent':False,
        'refunds_reconciled':False,'source_snapshot_immutable':False,'platform_conversions_added':False,
        'metric_basis':'lower_bound_on_qualifying_saved_gross_line_observations_not_sales',
        'limitations':['mutable_merged_raw_snapshot','missing_source_orders_and_unmatched_ids','no_source_coverage_watermark',
            'gross_not_net_returns_unreconciled','timezone_declarations_not_supported_by_binding','native_platform_store_account_unverified',
            'historical_cost_unknown','no_platform_order_attribution','no_ranking_or_execution'],
        'source_writes':0,'provider_writes':0,'execution_authorized':False}
