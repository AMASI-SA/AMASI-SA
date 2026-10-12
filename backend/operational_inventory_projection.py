"""Read-only projection of existing MZ2 physical inventory authority.
No balance is stored here. No invoice quantities, accounting or fulfillment
writers are invoked. Unknown identity/cost/reservation evidence stays unknown.
"""
from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
from operational_balance_store import digest, now, fail
from fulfillment_v2_routes import _inventory_eligibility, _load_inventory_evidence
from pymongo.read_concern import ReadConcern

SOURCES=frozenset({'warehouse_locations','warehouse_locations_warehouses','mezan_products_v2','mezan_cost_resources_v2','mezan_inventory_reservations_v2','mezan_component_consumption_units_v1','mz2_inventory_cost_states'})
LIMIT=20000
POLICY='moving-weighted-average-v1'

async def records(db,owner,collection):
    if collection not in SOURCES:raise ValueError('inventory_projection_source_not_allowed')
    result=await db[collection].find({'user_id':owner}).to_list(LIMIT+1)
    if len(result)>LIMIT:fail('inventory_projection_incomplete','تجاوزت البيانات حد القراءة؛ لا يمكن إثبات الرصيد',409)
    return result


def number(value):
    if value is None or isinstance(value,bool):return None
    try:result=Decimal(str(value))
    except (ValueError,InvalidOperation):return None
    return result if result.is_finite() and 0<=result<=Decimal('1000000000000000') else None


def identity(item,products,resources):
    if item.get('resource_id') or item.get('item_type')=='stock_component':
        resource=resources.get(item.get('resource_id'))
        if not resource or not resource.get('name') or resource.get('kind')=='service' or resource.get('track_inventory') is not True:return None
        return resource,'component',None,resource['id'],None
    keys={str(item.get(k)) for k in ('mezan_product_id','product_id') if item.get(k)}
    matches=[p for k,p in products.items() if k in keys]
    if len(matches)!=1:return None
    product=matches[0]
    if not product.get('name'):return None
    variant=item.get('variant_id') or item.get('salla_variant_id') or None
    if variant and str(variant) not in {str(v.get('id')) for v in (product.get('variants') or []) if isinstance(v,dict)}:return None
    return product,'product',product['mezan_product_id'],None,str(variant) if variant else None


class _SnapshotReads:
    """Expose only find: projection cannot mutate through this adapter."""
    def __init__(self, db, session):
        self.db, self.session = db, session

    def __getitem__(self, name):
        if name not in SOURCES | {"mezan_inventory_receipts_v2"}:
            raise ValueError("inventory_projection_source_not_allowed")
        collection, session = self.db[name], self.session
        class ReadCollection:
            def find(self, *args, **kwargs):
                return collection.find(*args, **kwargs, session=session)
        return ReadCollection()


async def projection(db,owner):
    # One read-only snapshot: receipts, occupancy and reservations must describe
    # the same instant even when consumption or receipt status commits midway.
    async with await db.client.start_session() as session:
        async with session.start_transaction(read_concern=ReadConcern("snapshot")):
            return await _projection(_SnapshotReads(db,session),owner)


async def _projection(db,owner):
    data={name:await records(db,owner,name) for name in sorted(SOURCES)}
    warnings=[]
    def unique(rows,key):
        counts=Counter(r.get(key) for r in rows if r.get(key))
        if any(n>1 for n in counts.values()):warnings.append('هوية مكررة في مصدر ميزان 2؛ استبعدت البيانات غير المحسومة')
        return {r[key]:r for r in rows if r.get(key) and counts[r[key]]==1}
    products=unique(data['mezan_products_v2'],'mezan_product_id');resources=unique(data['mezan_cost_resources_v2'],'id')
    warehouses=unique(data['warehouse_locations_warehouses'],'id');locations=unique(data['warehouse_locations'],'id')
    evidence=await _load_inventory_evidence(db,owner,list(locations.values()))
    result=[];internal=[]
    for location in locations.values():
        occupancy=location.get('occupancy') or {};items=occupancy.get('items') or []
        if not isinstance(items,list):warnings.append('بيانات الموقع غير مكتملة');continue
        for index,item in enumerate(items):
            if not isinstance(item,dict):warnings.append('بند مخزون غير صالح');continue
            resolved=identity(item,products,resources);q=number(item.get('quantity'))
            if not resolved or q is None:
                warnings.append('استبعد بند لا تثبت هويته في ميزان 2 أو كميته');continue
            source,kind,product_id,resource_id,variant=resolved
            receipt=str(item.get('receipt_id') or '');lot=str(item.get('receipt_id') or item.get('lot_id') or item.get('id') or '')
            row_key='receipt:'+receipt if receipt else str(location['id'])+':'+str(index)
            condition=str(item.get('condition') or item.get('saleability_status') or 'unspecified')
            warehouse=warehouses.get(location.get('warehouse_id'));issues=[]
            witnesses=evidence.get((str(location['id']),str(item.get('receipt_id') or item.get('lot_id') or '')),[])
            financially_pending=any(str(row.get('valuation_status') or '').strip() for row in (location,item,*witnesses))
            reason=_inventory_eligibility(location,item,evidence)
            blocked=bool(reason)
            if reason:issues.append(reason)
            # Additional catalog visibility restrictions do not authorize stock.
            if source.get('archived') or source.get('deleted_at') or source.get('active') is False or not warehouse or warehouse.get('status')=='disabled':
                blocked=True;issues.append('inventory_catalog_unavailable')
            unknown=False
            record={'id':digest(['mz2-stock-row',owner,location['id'],lot or index]),'kind':kind,'product_id':product_id,'resource_id':resource_id,'variant_id':variant,'name':source['name'],'sku':source.get('sku') if kind=='product' else None,'unit':source.get('unit') or 'piece','location_id':location['id'],'location_code':location.get('code') or location['id'],'warehouse_id':location.get('warehouse_id'),'preparation_state':item.get('preparation_state') or 'unspecified','condition':condition,'specifications':deepcopy(item.get('specifications') or {}),'configuration_key':item.get('configuration_key'),'physical':float(q),'reserved':0.0,'held':float(q) if blocked else None if unknown else 0.0,'available':0.0 if blocked or unknown else float(q),'unit_cost':None,'inventory_value':None,'cost_basis':None,'availability_issues':issues}
            matches=[]
            for cost in data['mz2_inventory_cost_states']:
                ci=cost.get('inventory_identity') or {}
                match=(kind=='component' and ci.get('item_type')=='stock_component' and ci.get('resource_id')==resource_id) or (kind=='product' and ci.get('item_type')=='product' and ci.get('product_id')==product_id and (ci.get('variant_id') or None)==variant)
                if match and cost.get('authoritative') is True and cost.get('cost_policy_version')==POLICY:matches.append(cost)
            if len(matches)==1 and number(matches[0].get('average_cost')) is not None:
                unit=number(matches[0]['average_cost'])
                with localcontext() as ctx:
                    ctx.prec=60
                    value=format((unit*q).quantize(Decimal('.01')),'f')
                if not financially_pending:
                    record.update(unit_cost=format(unit,'f'),inventory_value=value,cost_basis=POLICY)
            if not lot:record['availability_issues'].append('هوية دفعة المخزون غير مثبتة؛ لا يمكن تأكيد الإتاحة')
            result.append(record);internal.append({'record':record,'key':row_key,'lot':lot,'blocked':blocked or unknown,'quantity':q,'reserved':Decimal(0),'ambiguous':not bool(lot)})
    duplicate_keys=Counter(r['key'] for r in internal)
    for row in internal:
        if duplicate_keys[row['key']]>1:row['ambiguous']=True
    seen_allocations=set()
    def apply(allocation,component=False,unit_id=None):
        if not isinstance(allocation,dict):
            for row in internal:row['ambiguous']=True
            return
        if component:
            candidates=[r for r in internal if r['record']['kind']=='component' and r['record']['location_id']==allocation.get('location_id') and r['lot']==allocation.get('lot_id') and r['record']['resource_id']==allocation.get('resource_id')]
            allocation_id=(unit_id,allocation.get('id'))
        else:
            candidates=[r for r in internal if r['record']['kind']=='product' and r['key']==allocation.get('inventory_row_key')]
            allocation_id=(unit_id,allocation.get('inventory_row_key'))
            if len(candidates)==1 and allocation.get('location_id') and allocation['location_id']!=candidates[0]['record']['location_id']:candidates=[]
        q=number(allocation.get('quantity'))
        if len(candidates)!=1 or q is None or q<=0 or allocation_id in seen_allocations:
            affected=candidates or [r for r in internal if (r['record']['kind']=='component')==component]
            for row in affected:row['ambiguous']=True
            warnings.append('حجز غير محسوم؛ لم يتم افتراض كمية متاحة')
        else:candidates[0]['reserved']+=q
        seen_allocations.add(allocation_id)
    for reservation in data['mezan_inventory_reservations_v2']:
        if reservation.get('status')=='active':
            allocations=reservation.get('allocations')
            if not isinstance(allocations,list):apply(None);continue
            for allocation in allocations:apply(allocation,unit_id=reservation.get('id') or reservation.get('order_number'))
    for unit in data['mezan_component_consumption_units_v1']:
        if unit.get('state')=='reserved':
            for allocation in unit.get('allocations') or []:apply(allocation,True,str(unit.get('_id') or unit.get('id') or unit.get('order_line_id') or ''))
    for row in internal:
        record=row['record']
        if row['reserved']>row['quantity']:row['ambiguous']=True
        if row['ambiguous']:
            record.update(reserved=None,held=None,available=None if not row['blocked'] else 0.0)
            record['availability_issues'].append('الحجز أو هوية الدفعة غير محسومة')
        else:
            record['reserved']=float(row['reserved'])
            record['held']=float(row['quantity']-row['reserved']) if row['blocked'] else 0.0
            record['available']=0.0 if row['blocked'] else float(row['quantity']-row['reserved'])
    return {'schema_version':1,'source':'warehouse_locations','read_only':True,'observed_at':now(),'items':result,'warnings':list(dict.fromkeys(warnings))}
