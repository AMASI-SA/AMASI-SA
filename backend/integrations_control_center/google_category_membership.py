"""Positive membership observations from current owner ProductV2 raw snapshots.

No provider reads, old-details fallback, category-wide coverage or platform feed
mapping. Source revision is a trusted writer marker, not remote re-verification.
"""
from datetime import datetime,timezone
import hashlib
import json
import re
from urllib.parse import urlsplit


def numeric_id(value):
    if type(value) is int:value=str(value)
    return value if isinstance(value,str) and re.fullmatch(r'[1-9][0-9]{0,29}',value) else None


def safe_url(value):
    if not isinstance(value,str) or not 1<=len(value)<=2000 or any(c.isspace() for c in value):return None
    try:
        p=urlsplit(value)
        if p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment:return None
        p.port
    except ValueError:return None
    return value


def _categories(value):
    if isinstance(value,dict):value=[value]
    if not isinstance(value,list) or len(value)>100:return None
    result={}
    for row in value:
        if not isinstance(row,dict):return None
        identity=numeric_id(row.get('id'))
        if identity is None or identity in result:return None
        result[identity]=safe_url(row.get('url'))
    return result


def category_membership(row,*,owner_id,product_id,category_ids,category_url,as_of,max_age_seconds):
    if (not isinstance(owner_id,str) or not owner_id or owner_id!=owner_id.strip()
        or not isinstance(product_id,str) or numeric_id(product_id)!=product_id
        or not isinstance(category_ids,list) or len(category_ids)>40 or len(set(category_ids))!=len(category_ids)
        or any(not isinstance(c,str) or numeric_id(c)!=c for c in category_ids)
        or category_url is not None and safe_url(category_url) is None
        or not isinstance(as_of,datetime) or as_of.tzinfo is None or as_of.utcoffset() is None
        or type(max_age_seconds) is not int or not 60<=max_age_seconds<=604800):raise ValueError('invalid review scope')
    out={'product_id':product_id,'salla_product_id':None,'mezan_product_id':None,
        'membership':'unproved','matched_category_ids':[],'observed_at':None,'source_ref':None,
        'category_url_status':'not_requested' if category_url is None else 'not_verified','category_url_matched_ids':[],
        'catalog_visibility':'unknown','eligible_for_ad_selection':False,
        'remote_category_coverage_complete':False,'source_snapshot_immutable':False,
        'platform_catalog_mapping_verified':False,'execution_authorized':False,'reason':'missing_or_size_excluded'}
    if not category_ids:
        out['reason']='category_scope_not_requested';return out
    if not isinstance(row,dict):return out
    raw=row.get('raw_salla')
    if (row.get('user_id')!=owner_id or row.get('source')!='salla' or row.get('salla_product_id')!=product_id
        or row.get('mezan_product_id')!='mpv2_'+product_id or not isinstance(raw,dict) or numeric_id(raw.get('id'))!=product_id
        or not isinstance(row.get('source_revision'),str) or not re.fullmatch(r'[a-f0-9]{64}',row['source_revision'])):
        out['reason']='source_identity_unproved';return out
    observed=row.get('last_synced_at')
    if isinstance(observed,datetime) and observed.tzinfo is None:observed=observed.replace(tzinfo=timezone.utc)
    if not isinstance(observed,datetime) or observed.utcoffset() is None or not 0<=(as_of-observed).total_seconds()<=max_age_seconds:
        out['reason']='source_facts_stale';return out
    normalized=_categories(row['categories']) if isinstance(row.get('categories'),list) else None
    supplied=[_categories(raw[key]) for key in ('categories','category') if key in raw]
    if (normalized is None or not supplied or any(c is None for c in supplied)
        or any(c!=supplied[0] for c in supplied[1:]) or set(normalized)!=set(supplied[0])):
        out['reason']='current_categories_unproved';return out
    source=supplied[0];matched=[c for c in category_ids if c in source]
    stamp=observed.astimezone(timezone.utc).isoformat()
    out.update(salla_product_id=product_id,mezan_product_id='mpv2_'+product_id,
        membership='observed_in_stored_source' if matched else 'not_observed_in_stored_source',
        matched_category_ids=matched,observed_at=stamp,reason='positive_current_raw_membership' if matched else 'membership_not_observed')
    out['source_ref']='category-observation:'+hashlib.sha256(json.dumps([owner_id,product_id,row['source_revision'],stamp,source,category_ids,category_url],sort_keys=True).encode()).hexdigest()
    if row.get('archived') is True or row.get('status')=='inactive':out['catalog_visibility']='hidden'
    elif row.get('status')=='out_of_stock':out['catalog_visibility']='out_of_stock'
    elif row.get('archived') is False and row.get('status')=='active':out['catalog_visibility']='active'
    if category_url is not None:
        out['category_url_matched_ids']=[c for c in matched if source[c]==category_url]
        if out['category_url_matched_ids']:out['category_url_status']='explicit_url_observed'
    return out
