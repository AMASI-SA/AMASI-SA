"""Strict consumer of the piece-grain supplier workspace; safe diagnostics only."""
from acceptance_controller import check

CHECKS = ('SUPPLIER_HTTP', 'SUPPLIER_SCHEMA', 'SUPPLIER_PRESENT', 'SUPPLIER_FILE',
          'SUPPLIER_PRODUCTS', 'SUPPLIER_SELECTIONS', 'SUPPLIER_IDENTITIES')
METRICS = ('HTTP_STATUS', 'SUPPLIERS', 'SUPPLIER_MATCHES', 'FILES', 'FILE_MATCHES',
           'PIECES', 'AVAILABLE', 'IDENTITY_MATCH')

def require(value):
    if not value:
        raise AssertionError('SUPPLIER_CONTRACT_FAILED')

def evidence_lines(records):
    if type(records) is not list or len(records)>64:
        return ['SUPPLIER unavailable']
    if any(type(r) not in (tuple,list) or len(r)!=2 or type(r[0]) is not str or
           r[0] not in METRICS or type(r[1]) is not int or not 0<=r[1]<=10000 for r in records):
        return ['SUPPLIER unavailable']
    return ['SUPPLIER '+name+' '+str(value) for name,value in records]

def workspace_payload(call, file, pieces, employee, state):
    def report(name,value):state.setdefault('_supplier_evidence',[]).append((name,value))
    with check('SUPPLIER_HTTP'):
        response=call('GET','/api/supplier-dispatch-v1/workspace',actor='employee',params={'grain':'piece'})
        report('HTTP_STATUS',response.status_code)
        require(response.status_code==200)
    with check('SUPPLIER_SCHEMA'):
        result=response.json()
        require(type(result) is dict and result.get('ok') is True and result.get('employee_id')==employee)
        require(result.get('mezan_only') is True and result.get('salla_updated') is False and result.get('qoyod_updated') is False)
        require(result.get('external_supplier_login_enabled') is False)
        require(all(type(result.get(k)) is list for k in ('suppliers','files','supplier_accounts')))
        require(type(result.get('summary')) is dict)
        require(all(type(row) is dict for key in ('suppliers','files') for row in result[key]))
    with check('SUPPLIER_PRESENT'):
        matches=[s for s in result['suppliers'] if s.get('id')=='exit2d-supplier']
        report('SUPPLIERS',len(result['suppliers']));report('SUPPLIER_MATCHES',len(matches))
        require(len(matches)==1)
        supplier=matches[0]
        require(type(supplier.get('service_ids')) is list and bool(supplier['service_ids']))
    with check('SUPPLIER_FILE'):
        matches=[f for f in result['files'] if f.get('file_number')==file['file_number']]
        report('FILES',len(result['files']));report('FILE_MATCHES',len(matches))
        require(len(matches)==1)
        source=matches[0]
        require(source.get('batch_id')==file['batch_id'])
    with check('SUPPLIER_PRODUCTS'):
        products=source.get('products')
        require(type(products) is list and bool(products))
        require(all(type(p) is dict and all(type(p.get(k)) is str and p[k] for k in ('piece_id','group_key','order_item_id'))
                    and type(p.get('unit_index')) is int and p['unit_index']>0
                    and type(p.get('quantity')) is int and p['quantity']==1
                    and type(p.get('available_quantity')) is int and p['available_quantity'] in (0,1) for p in products))
        report('PIECES',len(products))
    with check('SUPPLIER_SELECTIONS'):
        available=sum(p['available_quantity'] for p in products);report('AVAILABLE',available)
        require(available==len(products) and source.get('available_quantity')==available)
        require(len({p['group_key'] for p in products})==len(products))
        selections=[{'group_key':p['group_key'],'quantity':1} for p in products]
    with check('SUPPLIER_IDENTITIES'):
        expected=[p for p in pieces if p['file_number']==file['file_number']]
        actual={(p['piece_id'],p['order_item_id'],p['unit_index']) for p in products}
        wanted={(p['piece_id'],p['order_item_id'],p['unit_index']) for p in expected}
        matched=actual==wanted and len(actual)==len(products)==len(expected)
        report('IDENTITY_MATCH',int(matched));require(matched)
    return {'client_request_id':'dispatch-'+file['request_id'],'supplier_id':supplier['id'],
            'files':[{'file_number':file['file_number'],'selections':selections}]}
