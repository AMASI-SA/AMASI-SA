import {makePurchaseCommand,purchaseSettings,purchaseToday,resumePurchase,validatePurchaseCommand,purchaseLineFingerprint} from './inventoryPurchaseWorkflow';

const scope='a'.repeat(64);
const previewHash='b'.repeat(64);
const clone=value=>JSON.parse(JSON.stringify(value));
const line={draft_key:'row',id:'p',item_id:'p',kind:'product',name:'فرو',quantity:'3',unit_price:'10',tax:'0',location_id:'shelf',
  options:[],purchase_configuration:{state:'ready',selections:[],inputs:[],deferred_option_ids:[]}};
const form=()=>({supplier_id:'supplier',invoice_number:'INV1',invoice_date:purchaseToday(),lines:[line],note:''});
const invoice={id:'invoice',obligation_id:'debt',supplier_id:'supplier',supplier_name:'المورد',invoice_number:'INV1',invoice_date:purchaseToday(),
  net:'30.00',tax:'0.00',gross:'30.00',adjusted_gross:'30.00',settled:'0.00',outstanding:'30.00',receipt_tracking_version:1,
  receipt_status:'not_received',physical_stock_status:'not_received',receipt_remaining_lines:1,can_receive:true,receipt_history:[],
  lines:[{item_id:'p',kind:'product',name:'فرو',line_id:'saved-line',quantity:3,unit:'piece',unit_price:'10.00',net:'30.00',tax:'0.00',gross:'30.00',
    returned_quantity:0,remaining_quantity:3,location_id:'shelf',purchase_configuration:clone(line.purchase_configuration),
    received_quantity:0,pending_receipt_quantity:3,physical_stock_status:'not_received'}]};
const account={id:'bank',kind:'bank',name:'البنك'};
function command(settings={}){let id=0;return makePurchaseCommand(form(),{...purchaseSettings(),...settings},scope,[account],[{id:'old',available:'20.00',business_date:purchaseToday()}],()=> 'request-'+(++id));}
function financialInvoice(settled='15.00'){
  const result=clone(invoice);result.settled=settled;result.outstanding=(30-Number(settled)).toFixed(2);
  for(const key of ['receipt_status','physical_stock_status','receipt_remaining_lines','can_receive','receipt_history'])delete result[key];
  for(const row of result.lines)for(const key of ['line_id','received_quantity','pending_receipt_quantity','physical_stock_status'])delete row[key];
  return result;
}
function receiptResult(body){
  const received=clone(invoice);
  for(const row of received.lines){const amount=body.lines.find(item=>item.line_id===row.line_id)?.quantity||0;
    Object.assign(row,{received_quantity:amount,pending_receipt_quantity:row.quantity-amount,physical_stock_status:amount===row.quantity?'received':amount?'partial':'not_received'});}
  received.receipt_status=received.lines.every(row=>row.pending_receipt_quantity===0)?'received':'partial';
  received.physical_stock_status=received.receipt_status;received.receipt_remaining_lines=received.lines.filter(row=>row.pending_receipt_quantity>0).length;
  received.can_receive=received.receipt_remaining_lines>0;
  delete received.receipt_history; // The real approval response reconstructs history only on later reads.
  return {status:'approved',receipt_id:'receipt',batch_id:'receipt',invoice_id:received.id,preview_hash:body.preview_hash,receipt_date:body.receipt_date,
    lines:body.lines.map(row=>({...row,invoice_line_id:row.line_id})),invoice:received};
}
function api(){return {saveInventoryPurchase:jest.fn().mockResolvedValue(clone(invoice)),previewInventoryPurchaseReceipt:jest.fn().mockResolvedValue({preview_hash:previewHash,writer_disabled:false,fulfillment_eligible:false}),
  saveInventoryPurchaseReceipt:jest.fn().mockImplementation(async(id,body)=>receiptResult(body)),movement:jest.fn().mockResolvedValue({id:'payment'}),allocateInventoryPurchasePayment:jest.fn().mockResolvedValue({id:'allocation',invoice:financialInvoice()})};}
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
  expect(service.saveInventoryPurchaseReceipt.mock.calls[0][1]).toEqual(expect.objectContaining({preview_hash:previewHash,lines:[{line_id:'saved-line',quantity:2,location_id:'shelf'}]}));
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
  service.saveInventoryPurchaseReceipt.mockRejectedValueOnce(Error('timeout'));
  await expect(resumePurchase(saved,service,step=>{saved=step;})).rejects.toThrow('timeout');
  expect(saved.phase).toBe('receipt');expect(saved.receipt_body.preview_hash).toBe(previewHash);
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
  service.previewInventoryPurchaseReceipt.mockResolvedValue({preview_hash:previewHash,writer_disabled:true,fulfillment_eligible:false});
  await expect(resumePurchase(saved,service,step=>{saved=step;})).rejects.toThrow('الاستلام متوقف');
  expect(saved.invoice.id).toBe('invoice');expect(saved.phase).toBe('receipt');expect(service.movement).not.toHaveBeenCalled();
});

// Construct durable checkpoints with the same fields the real APIs return. All
// corrupted commands below must fail before another financial or stock write.
function checkpoint(phase='receipt',settings={receipt_mode:'all',payment_mode:'full',payment_account:'bank:bank'}){
  const saved=command(settings);saved.phase=phase;saved.invoice=clone(invoice);
  if(saved.receipt){
    saved.receipt_body={...clone(saved.receipt),preview_hash:previewHash,lines:saved.receipt.lines.map(row=>({line_id:saved.invoice.lines[row.index].line_id,quantity:row.quantity,location_id:row.location_id}))};
    if(['payment','complete'].includes(phase)){saved.receipt_result=receiptResult(saved.receipt_body);saved.invoice=clone(saved.receipt_result.invoice);}
  }
  if(phase==='complete'&&saved.payment)saved.payment_result={id:'payment'};
  return clone(saved);
}
async function expectRejectedBeforeCalls(saved){
  const service=api(),persist=jest.fn();
  await expect(resumePurchase(clone(saved),service,persist)).rejects.toThrow();
  for(const method of Object.values(service))expect(method).not.toHaveBeenCalled();
  expect(persist).not.toHaveBeenCalled();
}

test.each(['receipt','payment','complete'])('a valid durable %s checkpoint remains recoverable',phase=>{
  expect(validatePurchaseCommand(checkpoint(phase),scope).phase).toBe(phase);
});
test.each([
  ['unknown phase',saved=>{saved.phase='typo';}],
  ['unsupported workflow version',saved=>{saved.workflow_version=2;}],
  ['missing saved invoice',saved=>{saved.invoice=null;}],
  ['missing obligation checkpoint',saved=>{delete saved.invoice.obligation_id;}],
  ['missing saved lines',saved=>{saved.invoice.lines=[];}],
  ['receipt scope mismatch',saved=>{saved.receipt.expected_session_scope='c'.repeat(64);}],
  ['previewed receipt scope mismatch',saved=>{saved.receipt_body.expected_session_scope='c'.repeat(64);}],
  ['payment scope mismatch',saved=>{saved.payment.body.expected_session_scope='c'.repeat(64);}],
  ['invoice supplier mismatch',saved=>{saved.invoice.supplier_id='other-supplier';}],
  ['invoice number mismatch',saved=>{saved.invoice.invoice_number='OTHER';}],
  ['saved invoice item mismatch',saved=>{saved.invoice.lines[0].item_id='other-product';}],
  ['saved invoice quantity mismatch',saved=>{saved.invoice.lines[0].quantity=4;}],
  ['payment supplier mismatch',saved=>{saved.payment.body.party_id='other-supplier';}],
])('restored %s is rejected before writes or checkpoints',async(label,change)=>{
  const saved=checkpoint();change(saved);await expectRejectedBeforeCalls(saved);
});
test('recovery validation rejects a root command from a different session',()=>{
  expect(()=>validatePurchaseCommand(checkpoint(),'c'.repeat(64))).toThrow();
});
test.each([
  ['receipt index outside the invoice',saved=>{saved.receipt.lines[0].index=1;}],
  ['duplicate receipt index',saved=>{saved.receipt.lines.push(clone(saved.receipt.lines[0]));}],
  ['receipt quantity above ordered quantity',saved=>{saved.receipt.lines[0].quantity=4;}],
  ['previewed line belongs to another invoice',saved=>{saved.receipt_body.lines[0].line_id='another-line';}],
  ['previewed quantity differs from approved quantity',saved=>{saved.receipt_body.lines[0].quantity=1;}],
  ['previewed location differs from approved location',saved=>{saved.receipt_body.lines[0].location_id='other-shelf';}],
  ['previewed date differs from approved date',saved=>{saved.receipt_body.receipt_date='2001-01-01';}],
  ['previewed request identity differs',saved=>{saved.receipt_body.request_id='other-request';}],
  ['previewed lines omit the approved line',saved=>{saved.receipt_body.lines=[];}],
  ['previewed lines duplicate an approved line',saved=>{saved.receipt_body.lines.push(clone(saved.receipt_body.lines[0]));}],
  ['malformed preview hash',saved=>{saved.receipt_body.preview_hash='not-a-preview-hash';}],
])('malformed receipt binding: %s',async(label,change)=>{
  const saved=checkpoint();change(saved);await expectRejectedBeforeCalls(saved);
});
test.each([
  ['payment before receipt confirmation','payment',saved=>{saved.receipt_result=null;}],
  ['completed workflow without receipt confirmation','complete',saved=>{saved.receipt_result=null;}],
  ['completed workflow without payment confirmation','complete',saved=>{saved.payment_result=null;}],
  ['receipt confirmation without durable receipt identity','payment',saved=>{delete saved.receipt_result.receipt_id;delete saved.receipt_result.batch_id;}],
])('missing checkpoint: %s',async(label,phase,change)=>{
  const saved=checkpoint(phase);change(saved);await expectRejectedBeforeCalls(saved);
});

test('an existing payment cannot be allocated before its own date',()=>{
  const draft={...form(),invoice_date:'2025-01-01'},settings={...purchaseSettings(),payment_mode:'existing',movement_id:'old',payment_amount:'15',payment_date:'2025-01-02'};
  const newId=jest.fn(()=> 'stable-request');
  expect(()=>makePurchaseCommand(draft,settings,scope,[],[{id:'old',available:'20.00',business_date:'2025-01-03'}],newId)).toThrow('تاريخ');
  expect(newId).not.toHaveBeenCalled();
});
test.each(['2025-01-03','2025-01-04'])('existing allocation at or after the payment date is valid: %s',day=>{
  const draft={...form(),invoice_date:'2025-01-01'},settings={...purchaseSettings(),payment_mode:'existing',movement_id:'old',payment_amount:'15',payment_date:day};
  let id=0;const saved=makePurchaseCommand(draft,settings,scope,[],[{id:'old',available:'20.00',business_date:'2025-01-03'}],()=> 'stable-request-'+(++id));
  expect(saved.payment.body.business_date).toBe(day);expect(saved.payment.body.amount).toBe('15.00');
});

test('allocating an existing payment retains receipt evidence while updating financial amounts',async()=>{
  const service=api(),saved=checkpoint('payment',{receipt_mode:'all',payment_mode:'existing',movement_id:'old',payment_amount:'15'});
  saved.invoice.receipt_history=[{id:'receipt',receipt_date:saved.receipt.receipt_date,lines:[{line_id:'saved-line',quantity:3}]}];
  const original=clone(saved),result=await resumePurchase(saved,service,()=>{});
  expect(result.phase).toBe('complete');
  expect(result.invoice).toEqual(expect.objectContaining({id:'invoice',settled:'15.00',outstanding:'15.00',receipt_status:'received',physical_stock_status:'received',can_receive:false}));
  expect(result.invoice.receipt_history).toEqual(original.invoice.receipt_history);
  expect(result.invoice.lines[0]).toEqual(expect.objectContaining({line_id:'saved-line',received_quantity:3,pending_receipt_quantity:0,physical_stock_status:'received'}));
  expect(saved).toEqual(original);expect(service.saveInventoryPurchase).not.toHaveBeenCalled();expect(service.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();expect(service.movement).not.toHaveBeenCalled();
});
test('a serialized uncertain allocation retries the exact body and never emits a cash movement',async()=>{
  const service=api();let saved=command({payment_mode:'existing',movement_id:'old',payment_amount:'15'});
  service.allocateInventoryPurchasePayment.mockRejectedValueOnce(Error('timeout'));
  await expect(resumePurchase(saved,service,step=>{saved=clone(step);})).rejects.toThrow('timeout');
  const sent=clone(service.allocateInventoryPurchasePayment.mock.calls[0]);
  await resumePurchase(clone(saved),service,step=>{saved=clone(step);});
  expect(service.allocateInventoryPurchasePayment.mock.calls[1]).toEqual(sent);
  expect(service.saveInventoryPurchase).toHaveBeenCalledTimes(1);expect(service.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();expect(service.movement).not.toHaveBeenCalled();
  expect(saved.phase).toBe('complete');
});
test('a lost local payment checkpoint replays the same command instead of making a new payment identity',async()=>{
  const saved=checkpoint('payment'),service=api();
  await expect(resumePurchase(saved,service,()=>{throw Error('storage full');})).rejects.toThrow('storage full');
  await resumePurchase(clone(saved),service,()=>{});
  expect(service.movement).toHaveBeenCalledTimes(2);
  expect(service.movement.mock.calls[1]).toEqual(service.movement.mock.calls[0]);
  expect(service.saveInventoryPurchase).not.toHaveBeenCalled();expect(service.saveInventoryPurchaseReceipt).not.toHaveBeenCalled();
});
test('duplicate item detection uses canonical option identities instead of selection order or text spacing',()=>{
  const one={...line,purchase_configuration:{state:'ready',selections:[{option_id:'size',value_id:'one'},{option_id:'name-choice',value_id:'yes'}],inputs:[{option_id:'name',value:'  فاطمة  علي  '}],deferred_option_ids:[]}};
  const two=clone(one);two.purchase_configuration.selections.reverse();two.purchase_configuration.inputs[0].value='فاطمة علي';
  expect(purchaseLineFingerprint(two)).toBe(purchaseLineFingerprint(one));
  two.purchase_configuration.inputs[0].value='عبير';expect(purchaseLineFingerprint(two)).not.toBe(purchaseLineFingerprint(one));
  two.purchase_configuration={state:'raw',selections:one.purchase_configuration.selections,inputs:[],deferred_option_ids:['name']};
  expect(purchaseLineFingerprint(two)).not.toBe(purchaseLineFingerprint(one));
});
