import {makePurchaseCommand,purchaseSettings,purchaseToday,resumePurchase} from './inventoryPurchaseWorkflow';

const scope='a'.repeat(64);
const line={draft_key:'row',id:'p',item_id:'p',kind:'product',name:'فرو',quantity:'3',unit_price:'10',tax:'0',location_id:'shelf',
  options:[],purchase_configuration:{state:'ready',selections:[],inputs:[],deferred_option_ids:[]}};
const form=()=>({supplier_id:'supplier',invoice_number:'INV1',invoice_date:purchaseToday(),lines:[line],note:''});
const invoice={id:'invoice',obligation_id:'debt',invoice_number:'INV1',receipt_tracking_version:1,lines:[{line_id:'saved-line',quantity:3}]};
const account={id:'bank',kind:'bank',name:'البنك'};
function command(settings={}){let id=0;return makePurchaseCommand(form(),{...purchaseSettings(),...settings},scope,[account],[{id:'old',available:'20.00'}],()=> 'request-'+(++id));}
function api(){return {saveInventoryPurchase:jest.fn().mockResolvedValue(invoice),previewInventoryPurchaseReceipt:jest.fn().mockResolvedValue({preview_hash:'h',writer_disabled:false,fulfillment_eligible:false}),
  saveInventoryPurchaseReceipt:jest.fn().mockResolvedValue({status:'approved',id:'receipt',invoice}),movement:jest.fn().mockResolvedValue({id:'payment'}),allocateInventoryPurchasePayment:jest.fn().mockResolvedValue({id:'allocation',invoice})};}
test('unreceived credit invoice creates only supplier invoice and can be received later',async()=>{
  const service=api(),saved=[];const result=await resumePurchase(command(),service,step=>saved.push(step));
  expect(result.phase).toBe('complete');expect(service.saveInventoryPurchase).toHaveBeenCalledTimes(1);
  expect(service.saveInventoryPurchase.mock.calls[0][0]).toEqual(expect.objectContaining({receipt_tracking_version:1,expected_session_scope:scope}));
  expect(service.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();expect(service.movement).not.toHaveBeenCalled();expect(service.allocateInventoryPurchasePayment).not.toHaveBeenCalled();
});
test('later location can remain unset for unreceived invoice, but received quantities require a location',()=>{
  const draft=form();draft.lines=[{...line,location_id:''}];
  expect(()=>makePurchaseCommand(draft,purchaseSettings(),scope,[],[],()=> 'id')).not.toThrow();
  expect(()=>makePurchaseCommand(draft,{...purchaseSettings(),receipt_mode:'all'},scope,[],[],()=> 'id')).toThrow('موقع');
});
test('partial receipt binds to saved line IDs and never resubmits client prices as receipt evidence',async()=>{
  const service=api();await resumePurchase(command({receipt_mode:'partial',receipt_quantities:{row:'2'}}),service,()=>{});
  expect(service.previewInventoryPurchaseReceipt).toHaveBeenCalledWith('invoice',expect.objectContaining({lines:[{line_id:'saved-line',quantity:2,location_id:'shelf'}]}));
  expect(service.saveInventoryPurchaseReceipt.mock.calls[0][1]).toEqual(expect.objectContaining({preview_hash:'h',lines:[{line_id:'saved-line',quantity:2,location_id:'shelf'}]}));
  expect(service.movement).not.toHaveBeenCalled();
});
test('uncertain invoice request resumes with the same identity and body',async()=>{
  const service=api(),original=command();let observed=0;const durable=new Map();
  service.saveInventoryPurchase.mockImplementation(async body=>{durable.set(body.request_id,invoice);if(observed++===0)throw Error('timeout');return durable.get(body.request_id);});
  await expect(resumePurchase(original,service,()=>{})).rejects.toThrow('timeout');
  await resumePurchase(original,service,()=>{});
  expect(durable.size).toBe(1);expect(service.saveInventoryPurchase.mock.calls[1][0]).toEqual(service.saveInventoryPurchase.mock.calls[0][0]);
});
test('uncertain receipt persists its preview and retries identical body without a second invoice',async()=>{
  const service=api();let saved=command({receipt_mode:'all'});
  service.saveInventoryPurchaseReceipt.mockRejectedValueOnce(Error('timeout')).mockResolvedValue({status:'approved',invoice});
  await expect(resumePurchase(saved,service,step=>{saved=step;})).rejects.toThrow('timeout');
  expect(saved.phase).toBe('receipt');expect(saved.receipt_body.preview_hash).toBe('h');
  await resumePurchase(saved,service,step=>{saved=step;});
  expect(service.saveInventoryPurchase).toHaveBeenCalledTimes(1);expect(service.previewInventoryPurchaseReceipt).toHaveBeenCalledTimes(1);
  expect(service.saveInventoryPurchaseReceipt.mock.calls[0]).toEqual(service.saveInventoryPurchaseReceipt.mock.calls[1]);
});
test('payment retry never repeats confirmed invoice or physical receipt',async()=>{
  const service=api();let saved=command({receipt_mode:'all',payment_mode:'full',payment_account:'bank:bank'});
  service.movement.mockRejectedValueOnce(Error('timeout')).mockResolvedValue({id:'payment'});
  await expect(resumePurchase(saved,service,step=>{saved=step;})).rejects.toThrow('timeout');
  expect(saved.phase).toBe('payment');await resumePurchase(saved,service,step=>{saved=step;});
  expect(service.saveInventoryPurchase).toHaveBeenCalledTimes(1);expect(service.saveInventoryPurchaseReceipt).toHaveBeenCalledTimes(1);
  expect(service.movement.mock.calls[0]).toEqual(service.movement.mock.calls[1]);
  expect(service.movement.mock.calls[0][0].allocations).toEqual([{obligation_id:'debt',amount:'30.00'}]);
});
test('existing payment calls allocation-only endpoint and never creates a bank movement',async()=>{
  const service=api();await resumePurchase(command({payment_mode:'existing',movement_id:'old',payment_amount:'15'}),service,()=>{});
  expect(service.allocateInventoryPurchasePayment).toHaveBeenCalledWith('invoice',expect.objectContaining({movement_id:'old',amount:'15.00',expected_session_scope:scope}));
  expect(service.movement).not.toHaveBeenCalled();expect(service.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();
});
test.each([
  [{receipt_mode:'partial',receipt_quantities:{row:'4'}},'المستلمة'],
  [{receipt_mode:'partial',receipt_quantities:{row:'1.5'}},'المستلمة'],
  [{receipt_mode:'partial',receipt_quantities:{row:'0'}},'جزئيًا'],
  [{payment_mode:'partial',payment_amount:'31',payment_account:'bank:bank'},'سداد'],
  [{payment_mode:'existing',movement_id:'old',payment_amount:'21'},'دفعة'],
])('invalid received or paid quantity fails before creating any command', (settings,message)=>{
  expect(()=>command(settings)).toThrow(message);
});
test('paused receipt does not advance to payment and leaves invoice checkpoint recoverable',async()=>{
  const service=api();let saved=command({receipt_mode:'all',payment_mode:'full',payment_account:'bank:bank'});
  service.previewInventoryPurchaseReceipt.mockResolvedValue({preview_hash:'h',writer_disabled:true,fulfillment_eligible:false});
  await expect(resumePurchase(saved,service,step=>{saved=step;})).rejects.toThrow('الاستلام متوقف');
  expect(saved.invoice.id).toBe('invoice');expect(saved.phase).toBe('receipt');expect(service.movement).not.toHaveBeenCalled();
});
