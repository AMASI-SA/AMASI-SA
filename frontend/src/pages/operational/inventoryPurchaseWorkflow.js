import {inventoryVariant} from './inventoryIdentity';
import {inventoryPersonalizations} from './inventoryPersonalizations';
import {purchaseConfigurationPayload} from './PurchaseConfiguration';

export function purchaseToday() {
  const parts = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Riyadh', year:'numeric', month:'2-digit', day:'2-digit'}).formatToParts(new Date());
  return ['year','month','day'].map(type => parts.find(part => part.type === type).value).join('-');
}
export function purchaseCents(value) {
  if (!/^\d+(\.\d{1,2})?$/.test(String(value))) throw Error('أدخل مبلغًا صحيحًا بمنزلتين عشريتين كحد أقصى.');
  const [whole, fraction=''] = String(value).split('.');
  const amount = Number(whole)*100 + Number(fraction.padEnd(2,'0'));
  if (!Number.isSafeInteger(amount)) throw Error('المبلغ أكبر من الحد المسموح.');
  return amount;
}
export const purchaseMoney = value => (value/100).toFixed(2);
export function purchaseTotals(lines) {
  return lines.reduce((total,line) => {
    const quantity = Number(line.quantity);
    if (!Number.isSafeInteger(quantity) || quantity <= 0 || quantity > 100000) throw Error('الكمية يجب أن تكون عددًا صحيحًا موجبًا.');
    const price = purchaseCents(line.unit_price), tax = purchaseCents(line.tax);
    if (price <= 0) throw Error('أدخل سعر وحدة أكبر من صفر.');
    const net = quantity*price;
    if (!Number.isSafeInteger(total.gross+net+tax)) throw Error('الإجمالي أكبر من الحد المسموح.');
    return {net:total.net+net,tax:total.tax+tax,gross:total.gross+net+tax};
  }, {net:0,tax:0,gross:0});
}
export function purchaseLinePayload(line) {
  purchaseTotals([line]);
  return {item_id:line.item_id || line.id,kind:line.kind,...inventoryVariant(line),
    ...inventoryPersonalizations(line),...purchaseConfigurationPayload(line,{allowMissingLocation:true}),
    quantity:Number(line.quantity),unit_price:line.unit_price,tax:line.tax};
}
export const purchaseSettings = () => ({receipt_mode:'none',receipt_date:purchaseToday(),receipt_quantities:{},
  payment_mode:'credit',payment_account:'',payment_amount:'',payment_reference:'',payment_date:purchaseToday(),movement_id:''});

// Freeze all request identities before the first write. An uncertain step is
// resumed with exactly the same body, including its receipt preview hash.
export function makePurchaseCommand(form, settings, scope, accounts, candidates, newId) {
  if (!form.supplier_id || !form.invoice_number.trim() || !form.invoice_date || !form.lines.length) throw Error('اختر المورد وأكمل بيانات الفاتورة وأضف صنفًا إلى المسودة.');
  if (form.invoice_date > purchaseToday()) throw Error('تاريخ الفاتورة لا يكون في المستقبل.');
  const totals = purchaseTotals(form.lines);
  const lines = form.lines.map(purchaseLinePayload);
  if (!['none','partial','all'].includes(settings.receipt_mode)) throw Error('حدد حالة استلام البضاعة.');
  const quantities = form.lines.map(line => settings.receipt_mode === 'all' ? Number(line.quantity) : settings.receipt_mode === 'none' ? 0 : Number(settings.receipt_quantities[line.draft_key] || 0));
  if (quantities.some((quantity,index) => !Number.isSafeInteger(quantity) || quantity < 0 || quantity > lines[index].quantity)) throw Error('الكمية المستلمة يجب ألا تتجاوز الكمية المشتراة.');
  if (settings.receipt_mode === 'partial' && (!quantities.some(Boolean) || quantities.every((quantity,index) => quantity === lines[index].quantity))) throw Error('أدخل كمية مستلمة جزئيًا، أو اختر استلام الكل.');
  if (settings.receipt_mode !== 'none' && (!settings.receipt_date || settings.receipt_date < form.invoice_date || settings.receipt_date > purchaseToday() || lines.some((line,index) => quantities[index] && !line.location_id))) throw Error('أكمل تاريخ الاستلام الصحيح وموقع كل كمية مستلمة.');
  if (!['credit','full','partial','existing'].includes(settings.payment_mode)) throw Error('حدد حالة السداد.');
  let payment = null;
  if (settings.payment_mode !== 'credit') {
    const amount = settings.payment_mode === 'full' ? totals.gross : purchaseCents(settings.payment_amount);
    if (amount <= 0 || amount > totals.gross || (settings.payment_mode === 'partial' && amount === totals.gross)) throw Error('أدخل مبلغ سداد صحيحًا لا يتجاوز إجمالي الفاتورة.');
    if (!settings.payment_date || settings.payment_date < form.invoice_date || settings.payment_date > purchaseToday()) throw Error('راجع تاريخ السداد؛ يجب أن يكون ضمن فترة الفاتورة.');
    if (settings.payment_mode === 'existing') {
      const movement = candidates.find(item => item.id === settings.movement_id);
      if (!movement || amount > purchaseCents(movement.available)) throw Error('اختر دفعة سابقة متاحة ومبلغًا ضمن رصيدها.');
      if (movement.business_date && settings.payment_date < movement.business_date) throw Error('تاريخ ربط الدفعة لا يسبق تاريخ الدفعة المسجلة.');
      payment = {kind:'allocation',body:{movement_id:movement.id,amount:purchaseMoney(amount),business_date:settings.payment_date}};
    } else {
      const account = accounts.find(item => item.kind+':'+item.id === settings.payment_account);
      if (!account) throw Error('اختر مصدر السداد الفعلي.');
      payment = {kind:'movement',body:{party_type:'supplier',party_id:form.supplier_id,kind:'payment',direction:'outgoing',currency:'SAR',
        source_account_type:account.kind,bank_id:account.id,amount:purchaseMoney(amount),reference:settings.payment_reference || '',source:'mezan2',receipt_id:null,business_date:settings.payment_date}};
    }
    payment.body = {...payment.body,request_id:newId(),expected_session_scope:scope};
  }
  return {type:'purchase',workflow_version:1,phase:'invoice',
    body:{supplier_id:form.supplier_id,invoice_number:form.invoice_number.trim(),invoice_date:form.invoice_date,lines,note:form.note || '',
      receipt_tracking_version:1,request_id:newId(),expected_session_scope:scope},
    receipt:settings.receipt_mode === 'none' ? null : {request_id:newId(),expected_session_scope:scope,receipt_date:settings.receipt_date,
      lines:quantities.map((quantity,index) => ({index,quantity,location_id:lines[index].location_id})).filter(line => line.quantity > 0),note:''},
    payment,invoice:null,receipt_result:null,payment_result:null};
}

export async function resumePurchase(command, api, persist) {
  validatePurchaseCommand(command,command.body?.expected_session_scope);
  let current = command;
  async function checkpoint(patch) {
    current = {...current,...patch};
    await persist(current);
  }
  if (current.phase === 'invoice') {
    const invoice = await api.saveInventoryPurchase(current.body);
    if (!invoice?.id || !invoice.obligation_id || !Array.isArray(invoice.lines) || invoice.lines.length !== current.body.lines.length) throw Error('لم يصل تأكيد الفاتورة كاملًا؛ أعد المحاولة بنفس العملية.');
    await checkpoint({invoice,phase:'receipt'});
  }
  if (current.phase === 'receipt') {
    if (current.receipt) {
      if (!current.receipt_body) {
        const body = {...current.receipt,lines:current.receipt.lines.map(line => {
          const saved = current.invoice.lines[line.index];
          if (!saved?.line_id) throw Error('تعذر ربط الاستلام ببند الفاتورة المحفوظة.');
          return {line_id:saved.line_id,quantity:line.quantity,location_id:line.location_id};
        })};
        const preview = await api.previewInventoryPurchaseReceipt(current.invoice.id,body);
        if (!preview?.preview_hash || preview.writer_disabled || preview.fulfillment_eligible !== false) {
          const error=Error('حُفظت الفاتورة؛ الاستلام متوقف أو تعذر التحقق منه. يمكنك متابعة الاستلام من الفاتورة.');error.notApplied=true;throw error;
        }
        await checkpoint({receipt_body:{...body,preview_hash:preview.preview_hash}});
      }
      const result = await api.saveInventoryPurchaseReceipt(current.invoice.id,current.receipt_body);
      if (result?.status !== 'approved') throw Error('حُفظت الفاتورة؛ لم يصل تأكيد الاستلام. أعد المحاولة بنفس العملية.');
      await checkpoint({receipt_result:result,invoice:result.invoice || current.invoice});
    }
    await checkpoint({phase:'payment'});
  }
  if (current.phase === 'payment') {
    if (current.payment) {
      const result = current.payment.kind === 'allocation'
        ? await api.allocateInventoryPurchasePayment(current.invoice.id,current.payment.body)
        : await api.movement({...current.payment.body,allocations:[{obligation_id:current.invoice.obligation_id,amount:current.payment.body.amount}]});
      if (!result?.id) throw Error('حُفظت الفاتورة؛ لم يصل تأكيد السداد. أعد المحاولة بنفس العملية.');
      await checkpoint({payment_result:result,invoice:result.invoice || current.invoice});
    }
    await checkpoint({phase:'complete'});
  }
  return current;
}

export function validatePurchaseCommand(command,scope) {
  const invalid=()=>{throw Error('بيانات العملية المحفوظة غير مكتملة؛ احتفظ بها وراجع الفاتورة قبل المتابعة.');};
  const phases=['invoice','receipt','payment','complete'];
  if(!command||command.type!=='purchase'||command.workflow_version!==1||!phases.includes(command.phase)||!scope)invalid();
  const validBody=body=>body&&typeof body.request_id==='string'&&body.request_id.length>=8&&body.expected_session_scope===scope;
  const body=command.body;
  if(!validBody(body)||!body.supplier_id||!body.invoice_number||!Array.isArray(body.lines)||!body.lines.length||body.receipt_tracking_version!==1)invalid();
  if(command.phase!=='invoice'&&(!command.invoice?.id||!command.invoice.obligation_id||!Array.isArray(command.invoice.lines)||command.invoice.lines.length!==body.lines.length))invalid();
  if(command.invoice?.supplier_id&&command.invoice.supplier_id!==body.supplier_id)invalid();
  if(command.receipt){
    if(!validBody(command.receipt)||!Array.isArray(command.receipt.lines)||!command.receipt.lines.length||!command.receipt.receipt_date)invalid();
    const seen=new Set();
    for(const line of command.receipt.lines){if(!Number.isSafeInteger(line.index)||!body.lines[line.index]||seen.has(line.index)||!Number.isSafeInteger(line.quantity)||line.quantity<=0||line.quantity>body.lines[line.index].quantity||!line.location_id)invalid();seen.add(line.index);}
    if(command.receipt_body&&(!validBody(command.receipt_body)||command.receipt_body.request_id!==command.receipt.request_id||!command.receipt_body.preview_hash||!Array.isArray(command.receipt_body.lines)))invalid();
    if(['payment','complete'].includes(command.phase)&&command.receipt_result?.status!=='approved')invalid();
  }
  if(command.payment){
    if(!['movement','allocation'].includes(command.payment.kind)||!validBody(command.payment.body)||!command.payment.body.business_date)invalid();
    const payment=command.payment.body;
    if(purchaseCents(payment.amount)<=0)invalid();
    if(command.payment.kind==='allocation'&&!payment.movement_id)invalid();
    if(command.payment.kind==='movement'&&(!payment.bank_id||payment.party_id!==body.supplier_id||payment.direction!=='outgoing'||payment.kind!=='payment'||payment.currency!=='SAR'))invalid();
    if(command.phase==='complete'&&!command.payment_result?.id)invalid();
  }
  return command;
}
