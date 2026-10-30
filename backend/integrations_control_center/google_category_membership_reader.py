"""Bounded owner/product index read of CURRENT raw categories only."""
from .google_category_membership import category_membership,numeric_id

PROJECTION={'_id':0,'user_id':1,'salla_product_id':1,'mezan_product_id':1,'source':1,'source_revision':1,
 'last_synced_at':1,'status':1,'archived':1,'categories':1,'raw_salla.id':1,'raw_salla.categories':1,'raw_salla.category':1}


async def read_category_memberships(db,*,owner_id,product_ids,category_ids,category_url,as_of,max_age_seconds=86400):
    if (not isinstance(product_ids,list) or len(product_ids)>100 or len(set(product_ids))!=len(product_ids)
        or any(not isinstance(p,str) or numeric_id(p)!=p for p in product_ids)):raise ValueError('invalid product scope')
    # Validate common scope before any database query, including empty plans.
    category_membership(None,owner_id=owner_id,product_id='1',category_ids=category_ids,category_url=category_url,as_of=as_of,max_age_seconds=max_age_seconds)
    rows=[]
    if product_ids and category_ids:
        query={'user_id':owner_id,'salla_product_id':{'$in':product_ids},'$expr':{'$lte':[{'$bsonSize':'$$ROOT'},32768]}}
        rows=await (db['mezan_products_v2'].find(query,PROJECTION).hint([('user_id',1),('salla_product_id',1)])
            .limit(len(product_ids)+1).max_time_ms(2000).to_list(length=len(product_ids)+1))
    by_id={}
    for row in rows:
        if not isinstance(row,dict) or row.get('user_id')!=owner_id or row.get('salla_product_id') not in product_ids or row['salla_product_id'] in by_id:raise ValueError('catalog identity ambiguous')
        by_id[row['salla_product_id']]=row
    return {'products':[category_membership(by_id.get(pid),owner_id=owner_id,product_id=pid,category_ids=category_ids,category_url=category_url,as_of=as_of,max_age_seconds=max_age_seconds) for pid in product_ids],
        'scope':'pinned_products_current_stored_category_membership','category_ids_reviewed':category_ids,'category_url':category_url,
        'remote_category_coverage_complete':False,'platform_catalog_mapping_verified':False,
        'source_writes':0,'provider_writes':0,'execution_authorized':False,
        'limitations':['positive_current_raw_snapshot_only','no_old_details_fallback','oversized_product_documents_omitted_unquantified',
          'not_full_category_enumeration','no_platform_feed_mapping','inventory_size_preparation_profit_season_not_established']}
