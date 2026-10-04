"""Request-local product indexes and paginated dashboard-only result rows.

Financial calculators and catalog identity resolution are unchanged. Complete
cohorts live in the bounded temporary spill, never in response-sized RAM lists.
"""
from __future__ import annotations
import json
import uuid
from contextvars import ContextVar
from contextlib import contextmanager

_page_request=ContextVar("dashboard_product_page_request",default=("products",None,50))

@contextmanager
def product_page_request(kind="products",cursor=None,limit=50):
    token=_page_request.set((kind,cursor,limit))
    try: yield
    finally: _page_request.reset(token)

def current_page_request():
    return _page_request.get()

from dashboard_spill import _encode, _decode
from product_catalog_cost_resolution import index_current_catalog_products, normalize_product_name, NAME_ALIAS_PREFIX

BATCH_SIZE=128
MAX_PAGE=50

async def _rows(collection, query, projection):
    cursor=collection.find(query,projection).batch_size(BATCH_SIZE)
    try:
        while True:
            batch=await cursor.to_list(length=BATCH_SIZE)
            if not batch: break
            for row in batch: yield row
    finally:
        await cursor.close()

class BindingRows:
    def __init__(self,store,namespace,kind,product):
        self.store,self.namespace,self.kind,self.product=store,namespace,kind,product
    def __iter__(self):
        cursor=self.store.execute('SELECT payload FROM dashboard_bindings WHERE namespace=? AND kind=? AND product=? ORDER BY ordinal',(self.namespace,self.kind,self.product))
        try:
            while True:
                batch=cursor.fetchmany(BATCH_SIZE)
                if not batch: break
                for (payload,) in batch: yield _decode(payload)
        finally: cursor.close()

class BindingMap:
    def __init__(self,store,namespace,kind):
        self.store,self.namespace,self.kind=store,namespace,kind
    def get(self,product,default=None):
        return BindingRows(self.store,self.namespace,self.kind,product)

async def load_product_context(db,user_id,store,collections,projection):
    products_collection,profiles_collection,options_collection,bindings_collection,resources_collection=collections
    scope=uuid.uuid4().hex
    by_id=store.map(scope+'-ids'); by_variant=store.map(scope+'-variants'); by_sku=store.map(scope+'-skus')
    names=store.map(scope+'-names'); ids=store.set(scope+'-product-ids')
    async for raw in _rows(db[products_collection],{'user_id':user_id},projection):
        indexes=index_current_catalog_products([raw])
        for target,index in zip((by_id,by_variant,by_sku),indexes):
            for key,value in index.items():
                if target is not by_sku or not key.startswith(NAME_ALIAS_PREFIX): target[key]=value
        # Global normalized-name uniqueness cannot be inferred per product.
        enriched=next(iter(indexes[0].values()),None)
        if enriched is None:
            from product_catalog_cost_resolution import enrich_current_salla_cost
            enriched=enrich_current_salla_cost(raw)
        name=normalize_product_name(raw.get('name'))
        identity=str(enriched.get('salla_product_id') or enriched.get('mezan_product_id') or enriched.get('id') or '').strip()
        if name:
            state=names.setdefault(name,{'first':enriched,'identity':'','ambiguous':False})
            if identity:
                if state['identity'] and state['identity']!=identity: state['ambiguous']=True
                elif not state['identity']: state['identity']=identity
        product_id=str(raw.get('salla_product_id') or '').strip()
        if product_id: ids.add(product_id)
    for name,state in names.items():
        if state['identity'] and not state['ambiguous']: by_sku[NAME_ALIAS_PREFIX+name]=state['first']
    profiles=store.map(scope+'-profiles')
    async for row in _rows(db[profiles_collection],{'user_id':user_id},{'_id':0}):
        key=str(row.get('salla_product_id'))
        if isinstance(row.get('salla_product_id'), str) and key in ids: profiles[key]=row
    store.execute('CREATE TABLE IF NOT EXISTS dashboard_bindings(namespace TEXT,kind TEXT,product TEXT,ordinal INTEGER,payload TEXT)')
    store.execute('CREATE INDEX IF NOT EXISTS dashboard_bindings_lookup ON dashboard_bindings(namespace,kind,product,ordinal)')
    resources_needed=store.set(scope+'-resource-ids')
    for kind,collection in (('options',options_collection),('products',bindings_collection)):
        ordinal=0
        async for row in _rows(db[collection],{'user_id':user_id},{'_id':0}):
            key=str(row.get('salla_product_id'))
            if not isinstance(row.get('salla_product_id'), str) or key not in ids: continue
            store.execute('INSERT INTO dashboard_bindings VALUES(?,?,?,?,?)',(scope,kind,key,ordinal,_encode(row)))
            ordinal+=1
            if row.get('resource_id'): resources_needed.add(str(row['resource_id']))
    resources=store.map(scope+'-resources')
    async for row in _rows(db[resources_collection],{'user_id':user_id},{'_id':0}):
        key=str(row.get('id'))
        if isinstance(row.get('id'), str) and key in resources_needed: resources[key]=row
    return by_id,by_variant,by_sku,profiles,BindingMap(store,scope,'options'),BindingMap(store,scope,'products'),resources


def _table(store):
    store.execute('CREATE TABLE IF NOT EXISTS dashboard_product_pages(namespace TEXT,kind TEXT,units REAL,sales REAL,name TEXT,identity TEXT,ordinal INTEGER,payload TEXT)')
    store.execute('CREATE INDEX IF NOT EXISTS dashboard_product_page_sort ON dashboard_product_pages(namespace,kind,units DESC,sales DESC,name,identity,ordinal)')


def page_rows(store,namespace,kind,*,cursor=None,limit=50):
    if kind not in ('products','missing'): raise ValueError('invalid product detail kind')
    if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=MAX_PAGE: raise ValueError('page limit must be 1..50')
    try: offset=0 if cursor is None else int(cursor)
    except (TypeError,ValueError): raise ValueError('invalid product cursor') from None
    if offset<0 or offset>9223372036854775807 or (cursor is not None and str(offset)!=str(cursor)): raise ValueError('invalid product cursor')
    total=store.execute('SELECT COUNT(*) FROM dashboard_product_pages WHERE namespace=? AND kind=?',(namespace,kind)).fetchone()[0]
    records=store.execute('SELECT payload FROM dashboard_product_pages WHERE namespace=? AND kind=? ORDER BY units DESC,sales DESC,name,identity,ordinal LIMIT ? OFFSET ?',(namespace,kind,limit,offset)).fetchall()
    rows=[json.loads(row[0]) for row in records]
    following=offset+len(rows)
    return rows,dict(limit=limit,offset=offset,total=total,has_more=following<total,next_cursor=str(following) if following<total else None)


def finalize_product_pages(store,raw_rows,missing_rows,canonical_finalize,*,limit=50,cursor=None,kind='products'):
    _table(store)
    scope=uuid.uuid4().hex
    raw_total=0.0
    for ordinal,(identity,raw) in enumerate(raw_rows.items()):
        raw_total+=float(raw.get('total_cost') or 0)
        items,_=canonical_finalize({identity:raw})
        if not items: continue
        row=items[0]
        store.execute('INSERT INTO dashboard_product_pages VALUES(?,?,?,?,?,?,?,?)',(scope,'products',float(row.get('units_sold') or 0),float(row.get('total_sales') or 0),str(row.get('name') or '').casefold(),str(row.get('identity') or ''),ordinal,json.dumps(row,ensure_ascii=False)))
    for ordinal,row in enumerate(missing_rows.values()):
        value={**row,'fallback_sources':sorted(row.get('fallback_sources') or [])}
        store.execute('INSERT INTO dashboard_product_pages VALUES(?,?,?,?,?,?,?,?)',(scope,'missing',0,0,str(row.get('name') or '').casefold(),str(row.get('identity') or ''),ordinal,json.dumps(value,ensure_ascii=False)))
    count=0; units=0.0; sales=0.0; unpriced=False; fallback=False; unconverted=False; priced_profit=0.0; priced_count=0
    records=store.execute('SELECT payload FROM dashboard_product_pages WHERE namespace=? AND kind=? ORDER BY units DESC,sales DESC,name,identity,ordinal',(scope,'products'))
    try:
        while True:
            batch=records.fetchmany(BATCH_SIZE)
            if not batch: break
            for (payload,) in batch:
                row=json.loads(payload); count+=1
                units+=float(row.get('units_sold') or 0); sales+=float(row.get('known_total_sales') or 0)
                unpriced=unpriced or row['cost_status']=='missing'
                fallback=fallback or row['cost_status']=='salla_fallback'
                unconverted=unconverted or not row['sales_currency_conversion_complete']
                if row['net_profit'] is not None:
                    priced_profit+=row['net_profit']; priced_count+=1
    finally: records.close()
    sales=round(sales,2); raw_total=round(raw_total,2)
    summary=dict(product_count=count,total_units=round(units,2),total_sales=None if unconverted else sales,known_total_sales=sales,sales_currency_conversion_complete=not unconverted,total_cost=raw_total,net_profit=None if unpriced or unconverted else round(sales-raw_total,2),has_unpriced_products=unpriced,uses_salla_fallback=fallback,priced_net_profit=round(priced_profit,2),priced_profit_count=priced_count)
    rows,pagination=page_rows(store,scope,kind,cursor=cursor,limit=limit)
    return rows,summary,dict(namespace=scope,pagination=pagination)
