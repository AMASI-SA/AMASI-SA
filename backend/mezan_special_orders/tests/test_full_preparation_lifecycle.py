"""Actual native creation, review, preparation, supplier and assembly lifecycle."""
import os
from pathlib import Path
import httpx
import pytest
from fastapi import FastAPI
import order_review_routes as review
import preparation_file_registry as registry
import preparation_piece_operations as pieces
import reviewed_preparation_batches as batches
import supplier_receiving_routes as receiving
import fulfillment_v2_routes as fulfillment
import preparation_supplier_dispatch as dispatch
import supplier_dispatch_pdf as dispatch_pdf
import supplier_dispatch_share_evidence as share
from preparation_piece_line_services import install_preparation_piece_line_services
from preparation_piece_execution_guard import install_preparation_piece_execution_guard
from preparation_file_failure_safety import install_preparation_finalize_safety
from order_review_forward_stage_guard import install_order_review_forward_stage_guard
from preparation_route_history import install_supplier_dispatch_route_guard
from supplier_receipt_employee_custody import install_supplier_receipt_employee_custody
from mezan_special_orders.tests.test_core import OWNER
from mezan_special_orders.tests.test_financial_integration import run,gl_balance,movement
from mezan_special_orders.tests.test_shared_workflow_integration import setup

pytestmark=pytest.mark.skipif(not os.getenv('MEZAN_SPECIAL_TEST_REPLICA_URI'),reason='Dedicated replica set required')


def client(h):
    user={'id':OWNER.tenant_id,'role':'owner','name':'Synthetic owner','email':'owner@example.invalid'}
    install_order_review_forward_stage_guard()
    pieces.install_preparation_piece_operations()
    install_preparation_piece_line_services()
    install_preparation_piece_execution_guard()
    install_preparation_finalize_safety()
    install_supplier_dispatch_route_guard()
    install_supplier_receipt_employee_custody()
    app=FastAPI()
    for factory in (review.make_order_review_router,registry.make_preparation_file_registry_router,
            batches.make_reviewed_preparation_batches_router,pieces.make_preparation_piece_operations_router,
            dispatch.make_preparation_supplier_dispatch_router,dispatch_pdf.make_supplier_dispatch_pdf_router,
            share.make_supplier_dispatch_share_evidence_router,receiving.make_supplier_receiving_router,
            fulfillment.make_fulfillment_v2_router):
        app.include_router(factory(h.db,lambda:user))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic.test')


async def create_and_review(h,c, *, partial=False):
    await h.raw.users.insert_one({'id':OWNER.tenant_id,'role':'owner','name':'Synthetic owner','email':'owner@example.invalid'})
    order=await setup(h,partial=partial)
    response=await c.post(f'/order-reviews-v1/{order["order_number"]}/complete',json={'expected_revision':0})
    assert response.status_code==200,response.text
    await h.raw[receiving.SUPPLIERS].insert_one({'user_id':OWNER.tenant_id,'id':'supplier-demo',
        'company_name':'Synthetic supplier','status':'active','phone':'','service_links':[],'service_ids':['product-supply']})
    await h.raw[receiving.COST_PROFILES].insert_one({'user_id':OWNER.tenant_id,'salla_product_id':'p-demo','base_cost':21})
    return await h.integrated.get(OWNER,order["order_id"])


async def prepare_file(h,c,order,*,expected_status=200):
    context=await batches.load_reviewed_product_context(h.db,user_id=OWNER.tenant_id,limit=batches.MAX_REVIEWED_ORDERS)
    products=context['catalog'].get('products') or []
    assert len(products)==1,context['catalog']
    chosen=products[0]
    request='full-native-preparation-001'
    result=await c.post('/preparation-file-registry-v1/drafts',json={'client_request_id':request,
        'file_title':'ملف اختبار الطلب الخاص','responsible_employee_id':OWNER.tenant_id,
        'expected_quantity':1,'selected_product_count':1})
    assert result.status_code==200,result.text
    result=await c.post('/reviewed-preparation-batches-v1/batches',json={'client_request_id':request,
        'selections':[{'group_key':chosen['group_key'],'quantity':1,'revision':chosen.get('revision')}]})
    assert result.status_code==expected_status,result.text
    if expected_status!=200:return result
    batch=result.json()
    native=await h.raw[pieces.PIECES].find_one({'order_number':order['order_number']},{'_id':0})
    assert native is not None,batch
    return batch,native


@pytest.mark.parametrize('partial',[False,True])
def test_native_local_order_reaches_supplier_invoice_and_assembly_without_fake_salla(monkeypatch,tmp_path,partial):
    async def no_salla(*a,**kw):raise AssertionError('A local order must never call Salla')
    async def synthetic_image(*a,**kw):return b'',None
    monkeypatch.setattr(review,'call_salla',no_salla)
    monkeypatch.setattr(batches,'_download_card_image',synthetic_image)
    async def scenario(h):
        async with client(h) as c:
            order=await create_and_review(h,c,partial=partial)
            if partial:
                order,ev=await h.claim(order)
                order=await h.fin_command(order,'bank_collection',{
                    'receipt_claim_id':order['receipt_claims'][0]['claim_id'],'movement':movement(ev)},'full-bank-collection-001')
            batch,piece=await prepare_file(h,c,order)
            response=await c.post(f'/preparation-work-v1/files/{batch["file_number"]}/start',json={})
            assert response.status_code==200,response.text
            workspace=await c.get('/supplier-dispatch-v1/workspace?grain=piece')
            assert workspace.status_code==200,workspace.text
            assert len(workspace.json()['files'])==1,workspace.text
            group=workspace.json()['files'][0]['products'][0]
            result=await c.post('/supplier-dispatch-v1/dispatches',json={
                'client_request_id':'full-dispatch-request-001','file_number':batch['file_number'],
                'supplier_id':'supplier-demo','selections':[{'group_key':group['group_key'],'quantity':1}]})
            assert result.status_code==201,result.text
            sent=result.json()['dispatch']
            response=await c.get(f'/supplier-dispatch-pdf-v1/{sent["id"]}/pdf')
            assert response.status_code==200,response.text
            assert response.content.startswith(b'%PDF-')
            (tmp_path/'native-supplier.pdf').write_bytes(response.content)
            artifact_dir=os.getenv('MEZAN_SPECIAL_TEST_ARTIFACT_DIR')
            if artifact_dir:
                dest=Path(artifact_dir);dest.mkdir(parents=True,exist_ok=True)
                (dest/f'full-supplier-{int(partial)}.pdf').write_bytes(response.content)
            import fitz
            with fitz.open(stream=response.content,filetype='pdf') as pdf:
                text=''.join(page.get_text() for page in pdf)
                assert 'MZ-' in text and order['order_number'][:19] in text
                assert order['order_number'][19:] in text
                assert '58' in text
            assert sent['source_files'][0]['cards'][0]['preparation_note']=='تصوير'
            assert len(sent['source_files'][0]['cards'][0]['specifications'])==3
            response=await c.post(f'/supplier-dispatch-share-v1/{sent["id"]}/evidence',
                files={'file':('synthetic.png',h.png,'image/png')})
            assert response.status_code==200,response.text
            response=await c.post(f'/supplier-dispatch-share-v1/{sent["id"]}/confirm')
            assert response.status_code==200,response.text
            response=await c.post(f'/supplier-dispatch-v1/dispatches/{sent["id"]}/ready',json={})
            assert response.status_code==200,response.text
            response=await c.post('/supplier-receiving-v1/sessions',json={
                'client_request_id':'full-supplier-session-001','supplier_id':'supplier-demo'})
            assert response.status_code==201,response.text
            session=response.json()['session']
            from preparation_piece_barcode import preparation_piece_barcode
            barcode=preparation_piece_barcode(user_id=OWNER.tenant_id,batch_id=batch['batch_id'],
                order_number=order['order_number'],order_item_id=piece['order_item_id'],unit_index=1)
            response=await c.post(f'/supplier-receiving-v1/sessions/{session["id"]}/scan',json={
                'client_request_id':'full-supplier-scan-001','barcode':barcode,'quantity':1})
            assert response.status_code==200,response.text
            assert response.json()['selected_quantity']==1
            response=await c.post(f'/supplier-receiving-v1/sessions/{session["id"]}/close',json={
                'expected_supplier_id':'supplier-demo','confirmed_total_halalas':2100,
                'invoice_lines':[{'piece_ids':[piece['piece_id']],'product_unit_price_halalas':2100,'services':[]}]})
            assert response.status_code==200,response.text
            invoice=response.json()['supplier_invoice']
            assert invoice['total_halalas']==2100
            assert await gl_balance(h,'supplier','supplier-demo','payable')==-2100
            assert await gl_balance(h,'expense','special_orders:marketing')==2100-(5217 if partial else 0)
            response=await c.post(f'/preparation-work-v1/receiving/pieces/{piece["piece_id"]}/receive',json={
                'client_request_id':'full-employee-receiving-001'})
            assert response.status_code==200,response.text
            assert response.json()['piece']['status']==pieces.PIECE_STATUS_READY_FOR_ASSEMBLY
            response=await c.post(f'/preparation-work-v1/assembly/pieces/{piece["piece_id"]}/ready',json={
                'client_request_id':'full-assembly-ready-001'})
            assert response.status_code==200,response.text
            assembled=response.json()
            assert assembled['progress']['order_completed']
            assert assembled['carrier_label']['ready'],assembled['carrier_label']
            printed=assembled['carrier_label']['print_data']
            assert printed['barcode_value']==order['order_number']
            assert printed['purpose_badge']=='تصوير'
            assert printed['remaining_amount']=={'amount':20.0 if partial else 0.0,'currency':'SAR'}
            response=await c.post(f'/fulfillment-v2/completed/{order["order_number"]}/carrier-label/confirm-print',json={"barcode":order["order_number"]})
            assert response.status_code==200,response.text
        # All preceding stages are native requests. No prepared/assembled stage
        # or native financial event has been seeded to shortcut the lifecycle.
        await h.raw.users.insert_one({'id':'native-driver-user','role':'store_driver','created_by':OWNER.tenant_id})
        await h.raw.store_drivers.insert_one({'id':'native-driver','user_id':OWNER.tenant_id,'account_user_id':'native-driver-user',
            'status':'active','name':'Synthetic driver','city':order['recipient']['address']['city'],'delivery_fee':20})
        from mezan_special_orders.tests.test_native_delivery_integration import assign,pick_up,app_client
        assignment=await assign(h,order)
        await pick_up(h,order)
        async with app_client(h,driver=True) as driver:
            response=await driver.post('/store-delivery/app/deliveries/status',json={
                'barcode':order['order_number'],'target_status':'delivered','payment_method':'cash'})
            assert response.status_code==200,response.text
        state=await h.integrated.get(OWNER,order['order_id'])
        assert state['stage']=='delivered' and state['balances']['remaining_minor']==0
        assert state['cost_report']['costs_complete']
        assert state['cost_report']['recognized_cost_minor']==4100
        assert await gl_balance(h,'expense','special_orders:marketing')==4100-(5217 if partial else 0)
        if partial:
            from mezan_special_orders.tests.test_native_settlement_integration import client as settlements,payload as settlement_payload
            doc=await h.doc(order)
            parent=next(p['movement_id'] for p in doc['payments'] if p['kind']=='cod_collection')
            body=await settlement_payload(h,order,parent,amount=0,offset=20,allocated=2000,reference='FULL-NET-001')
            async with settlements(h) as sc:
                response=await sc.post('/store-delivery/settlements/driver/native-driver/net-settlement',json=body)
                assert response.status_code==201,response.text
            state=await h.integrated.get(OWNER,order['order_id'])
            assert state['balances']['custody_minor']==0 and state['balances']['collected_minor']==6000
        assert await h.raw.unified_orders.count_documents({})==0
        assert await h.raw.general_ledger.count_documents({'entity_type':'revenue'})==0
        assert await h.raw[receiving.SUPPLIER_INVOICES].count_documents({})==1
    run(scenario)


def test_piece_backfill_fault_does_not_commit_a_registered_local_file(monkeypatch):
    async def synthetic_image(*a,**kw):return b'',None
    monkeypatch.setattr(batches,'_download_card_image',synthetic_image)
    original=pieces.materialize_preparation_pieces
    async def fail(db,**kw):
        await original(db,**kw)
        raise RuntimeError('Synthetic fault after native piece backfill')
    async def scenario(h):
        async with client(h) as c:
            order=await create_and_review(h,c)
            monkeypatch.setattr(pieces,'materialize_preparation_pieces',fail)
            result=await prepare_file(h,c,order,expected_status=409)
            assert result.json()['detail']['code']=='preparation_employee_assignment_incomplete'
        assert await h.raw[pieces.PIECES].count_documents({})==0
        from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS
        assert await h.raw[PREPARATION_UNIT_ALLOCATIONS].count_documents({'status':'committed'})==0
        assert await h.raw[registry.REGISTRY].count_documents({'status':'ready'})==0
        current=await h.integrated.get(OWNER,order['order_id'])
        assert current['stage']=='reviewed'
    run(scenario)
