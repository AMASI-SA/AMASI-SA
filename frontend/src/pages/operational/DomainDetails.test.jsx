import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import DomainDetails from './DomainDetails';
import CustomerPaymentTotals from './CustomerPaymentTotals';
jest.mock('./api',()=>({operationalApi:{receiptContent:jest.fn()},messageFor:()=> 'تعذر تحميل المستند'}));
let host,root;
const section = title => [...host.querySelectorAll('details')].find(node => node.querySelector('summary').textContent === title);
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
const render = async report => act(async()=>root.render(<><CustomerPaymentTotals report={report}/><DomainDetails report={report}/></>));
test('empty detail sections explain absence instead of rendering blank panels',async()=>{
 await render({parties:[],obligations:[],summaries:{},details:{}});
 for(const node of host.querySelectorAll('details')) expect(node.textContent).toContain('لا توجد بيانات مسجلة لهذا القسم');
});

test('one provider table keeps missing fees and net unavailable alongside customer payment proof', async () => {
  await render({details: {provider_paid_totals: {SAR: [{provider: 'tamara', name: 'تمارا', orders: 2, paid: '178.45'}]}, provider_balances: {SAR: [
    {provider: 'tamara', name: 'تمارا', orders: 2, gross: '178.45', fees: null, refunded: '0.00', net: null, opening: '10.00', settled: '0.00', complete: false},
    {provider: 'salla', name: 'سلة', orders: 1, gross: '24.10', fees: null, refunded: '0.00', net: null, opening: '0.00', settled: '0.00', complete: false},
  ]}}});
  const paymentSection = section('تفاصيل منصات الدفع');
  expect(paymentSection.querySelectorAll('table')).toHaveLength(1);
  expect([...paymentSection.querySelectorAll('tbody tr')].map(row => [row.querySelector('strong').textContent, ...[...row.cells].slice(1).map(cell => cell.textContent)])).toEqual([
    ['تمارا', '2', '178.45', 'غير متاح', '0.00', 'غير متاح', 'غير متاح'], ['سلة', '1', '24.10', 'غير متاح', '0.00', 'غير متاح', 'غير متاح'],
  ]);
  expect(paymentSection.textContent).toContain('رصيد البداية 10.00');
  expect(paymentSection.textContent).toContain('بيانات غير مكتملة');
  expect(paymentSection.textContent).toContain('SAR');
  expect(host.querySelector('section[aria-label="مدفوعات الطلبات منذ بداية التشغيل"]').textContent)
    .toContain('تمارا · 178.45 SAR · عدد الطلبات المدفوعة 2');
  expect(paymentSection.textContent).not.toContain('لا توجد بيانات مسجلة');
});

test('unassigned bank payments and customer balances stay visible without a bank posting', async () => {
  await render({details: {customer_tender_coverage: {source_order_count: 4, by_currency: {SAR: [
    {method: 'bank', name: 'تحويل بنكي', orders: 1, paid: '25.00', remaining: '0.00', incomplete_orders: 0},
    {method: 'cod', name: 'الدفع عند الاستلام', orders: 2, paid: null, remaining: null, incomplete_orders: 2},
  ]}}}});
  const coverage = host.querySelector('section[aria-label="مدفوعات الطلبات منذ بداية التشغيل"]');
  expect(coverage.textContent).toContain('عدد طلبات سلة في الفترة: 4');
  expect([...coverage.querySelectorAll('tbody tr')].map(row => [...row.cells].map(cell => cell.textContent))).toEqual([
    ['تحويل بنكي', '1', '25.00', '0.00', '0'], ['الدفع عند الاستلام', '2', 'غير متاح', 'غير متاح', '2'],
  ]);
});

test('SAR total uses the customer receipt including foreign orders and keeps original currency in a disclosure', async () => {
  const sar = {paid: '210.19', known_paid: '210.19', missing_orders: 0, by_method: [
    {method: 'tabby', name: 'تابي', orders: 2, paid: '210.19', remaining: '0.00'},
  ]};
  await render({details: {customer_tender_coverage: {source_order_count: 2, sar},
    provider_paid_totals: {KWD: [{provider: 'tabby', name: 'تابي', orders: 1, paid: '9.00'}]}}});
  const panel = host.querySelector('section[aria-label="مدفوعات الطلبات منذ بداية التشغيل"]');
  expect(panel.querySelector('h3').textContent).toContain('210.19 ر.س.');
  expect([...panel.querySelector('tbody tr').cells].map(cell => cell.textContent)).toEqual(['تابي', '2', '210.19', '0.00', 'مكتمل']);
  expect(panel.querySelector('details:not([open])').textContent).toContain('9.00 KWD');
});

test('wallet redemption remains settled order value and is labeled as using a prior customer credit', async () => {
  await render({ details: { customer_tender_coverage: { source_order_count: 1, sar: {
    paid: '100.00', known_paid: '100.00', missing_orders: 0,
    by_method: [
      { method: 'tabby', name: 'تابي', orders: 1, paid: '85.00', remaining: '0.00' },
      { method: 'wallet', name: 'محفظة العميل', orders: 1, paid: '15.00', remaining: '0.00' },
    ],
  } } } });
  expect(host.querySelector('h3').textContent).toContain('إجمالي المبالغ المسددة للطلبات · 100.00');
  const wallet = [...host.querySelectorAll('tbody tr')].find(row => row.textContent.includes('المستخدم من أرصدة محافظ العملاء'));
  expect(wallet.cells[2].textContent).toBe('15.00');
  expect(host.textContent).toContain('يخفض التزامنا له');
});

test('advertising keeps latest daily snapshot and groups month by account and currency using exact cents',async()=>{
 const obligations=[
  {id:'a1',party_type:'ad_account',party_id:'a',currency:'USD',business_date:'2026-10-05'},
  {id:'a2',party_type:'ad_account',party_id:'a',currency:'USD',business_date:'2026-10-05'},
  {id:'a3',party_type:'ad_account',party_id:'a',currency:'USD',business_date:'2026-10-05',sar_amount:'1687.50'},
  {id:'a4',party_type:'ad_account',party_id:'a',currency:'USD',business_date:'2026-10-06'},
  {id:'a5',party_type:'ad_account',party_id:'a',currency:'USD',business_date:'2026-10-07'},
  {id:'b1',party_type:'ad_account',party_id:'b',currency:'SAR',business_date:'2026-10-05'},
 ];
 await render({obligations,parties:[{party_type:'ad_account',party_id:'a',currency:'USD',name:'حساب دولار'},{party_type:'ad_account',party_id:'b',currency:'SAR',name:'حساب ريال'}],details:{ad_days:{
  a1:{amount:'200.00',closed:false,observed_at:'2026-10-05T10:00:00Z'},a2:{amount:'300.00',closed:false,observed_at:'2026-10-05T11:00:00Z'},a3:{amount:'450.00',closed:true,observed_at:'2026-10-06T00:00:00Z'},
  a4:{amount:'0.10',closed:false,observed_at:'2026-10-06T10:00:00Z'},a5:{amount:'0.20',closed:false,observed_at:'2026-10-07T10:00:00Z'},b1:{amount:'50.00',closed:true,observed_at:'2026-10-06T00:00:00Z'},
 }}});
 const monthly=section('الصرف الإعلاني الشهري');expect(monthly.querySelectorAll('article')).toHaveLength(2);
 expect(monthly.textContent).toContain('450.00 USD');expect(monthly.textContent).toContain('0.30 USD');expect(monthly.textContent).toContain('50.00 SAR');expect(monthly.textContent).not.toContain('950.00');
 const daily=section('الصرف الإعلاني اليومي');expect(daily.textContent).toContain('المعادل 1687.50 SAR');expect(daily.textContent).not.toContain('300.00 USD');expect(daily.textContent).not.toContain('200.00 USD');
});
test('shipping counts distinct service orders including zero-cost delivered service without counting COD',async()=>{
 const shipped={id:'shipping:one',kind:'shipping',party_type:'store_driver',party_id:'driver',currency:'SAR',confirmed:'0.00'};
 await render({parties:[{party_type:'store_driver',party_id:'driver',currency:'SAR',name:'مندوب'}],obligations:[shipped,shipped,{...shipped,id:'shipping:two',confirmed:'20.00'},{...shipped,id:'shipping:three',confirmed:'0.00',expected:'20.00'},{...shipped,id:'cod:one',kind:'cod',confirmed:'300.00'}],details:{facts:{'shipping:one':{delivered_at:'2026-10-05T10:00:00Z'}}}});
 const text=section('الشحن ومناديب المتجر').textContent;expect(text).toContain('طلبات في كشف الأجور: 3');expect(text).toContain('تواصيل مثبتة: 2');expect(text).toContain('أجرة التوصيل المؤكدة: 20.00');
});
test('prepaid shipping at completion shows paid cost without declaring the order delivered',async()=>{
 const party={party_type:'courier',party_id:'aramex',currency:'SAR',name:'أرامكس',settled:'34.50',outstanding_payable:'0.00',outstanding_receivable:'0.00'};
 const paid={...party,id:'shipping-prepaid:one',kind:'shipping',order_id:'one',payment_mode:'prepaid',recognition_event:'order_completed',completed_at:'2026-11-05T10:00:00Z',confirmed:'34.50'};
 const report={parties:[party],obligations:[paid],details:{facts:{'shipping-prepaid:one':{completed_at:paid.completed_at}}}};
 await render(report);
 let text=section('الشحن ومناديب المتجر').textContent;
 expect(text).toContain('طلبات في كشف الأجور: 1');
 expect(text).toContain('تواصيل مثبتة: 0');
 expect(text).toContain('أجرة الشحن المؤكدة: 34.50');
 expect(text).toContain('المدفوع مقدمًا عند «تم التنفيذ»: 34.50 SAR');
 expect(text).toContain('المتبقي: 0.00');
 await render({...report,details:{facts:{...report.details.facts,'shipping:one':{id:'aramex',party_type:'courier',delivered_at:'2026-11-06T10:00:00Z'}}}});
 text=section('الشحن ومناديب المتجر').textContent;
 expect(text).toContain('طلبات في كشف الأجور: 1');
 expect(text).toContain('تواصيل مثبتة: 1');
 expect(text).toContain('المدفوع مقدمًا عند «تم التنفيذ»: 34.50 SAR');
});
test('prepaid delivery proof belongs to the actual carrier and missing payment stays unknown',async()=>{
 const party={party_type:'courier',party_id:'aramex',currency:'SAR',name:'أرامكس'};
 await render({parties:[party],obligations:[{...party,id:'shipping-prepaid:one',kind:'shipping',order_id:'one',payment_mode:'prepaid',recognition_event:'order_completed',confirmed:'34.50'}],details:{facts:{'shipping:one':{id:'another-courier',party_type:'courier',delivered_at:'2026-11-06T10:00:00Z'}}}});
 const text=section('الشحن ومناديب المتجر').textContent;
 expect(text).toContain('تواصيل مثبتة: 0');
 expect(text).toContain('المدفوع مقدمًا عند «تم التنفيذ»: غير مكتمل SAR');
});
test('shipping fee components count once per order while preserving their total cost',async()=>{
 const party={party_type:'courier',party_id:'courier',currency:'SAR',name:'شركة الشحن'};
 await render({parties:[party],obligations:[
  {...party,id:'shipping:one:base_shipping',kind:'shipping',fee_component:'base_shipping',confirmed:'20.00'},
  {...party,id:'shipping:one:cod_commission',kind:'shipping',fee_component:'cod_commission',confirmed:'5.00'},
 ],details:{facts:{'shipping:one':{id:'courier',party_type:'courier',delivered_at:'2026-11-05T10:00:00Z'}}}});
 const text=section('الشحن ومناديب المتجر').textContent;
 expect(text).toContain('طلبات في كشف الأجور: 1');
 expect(text).toContain('تواصيل مثبتة: 1');
 expect(text).toContain('أجرة التوصيل المؤكدة: 25.00');
});
test.each(['store_driver', 'courier'])('shipping uses the recorded delivered count for %s without recalculating money', async party_type => {
 const party={party_type,party_id:'carrier',currency:'SAR',name:'جهة التوصيل',delivered_orders:3,confirmed_orders:1,
   pending_orders:2,settled:'5.00',outstanding_payable:'20.00',outstanding_receivable:'40.00'};
 const report={parties:[party],obligations:[
  {...party,id:'shipping:one:base_shipping',kind:'shipping',fee_component:'base_shipping',confirmed:'20.00'},
  {...party,id:'shipping:one:cod_commission',kind:'shipping',fee_component:'cod_commission',confirmed:'5.00'},
 ],details:{facts:{'shipping:one':{id:'carrier',party_type,delivered_at:'2026-11-05T10:00:00Z'}}}};
 const before=JSON.stringify(report);
 await render(report);
 let text=section('الشحن ومناديب المتجر').textContent;
 expect(text).toContain('تواصيل مثبتة: 3');
 expect(text).toContain('أجرة التوصيل المؤكدة: 25.00');
 expect(text).toContain('المسدد: 5.00');
 expect(text).toContain('المتبقي: 20.00');
 expect(text).toContain('التحصيل المستحق لنا: 40.00');
 expect(JSON.stringify(report)).toBe(before);
 await render({...report,parties:[{...party,delivered_orders:0}]});
 text=section('الشحن ومناديب المتجر').textContent;
 expect(text).toContain('تواصيل مثبتة: 0');
});
test('delivery count fallback deduplicates status evidence and excludes another executor or estimates', async () => {
 const party={party_type:'store_driver',party_id:'driver',currency:'SAR',name:'مندوب'};
 const fee={...party,kind:'shipping',confirmed:'0.00'};
 await render({parties:[party],obligations:[
  {...fee,id:'shipping:one:base_shipping',fee_component:'base_shipping'},
  {...fee,id:'shipping:one:cod_commission',fee_component:'cod_commission'},
  {...fee,id:'shipping:two'}, {...fee,id:'shipping:three'},
  {...fee,id:'shipping:estimate',expected:'20.00'}, {...fee,id:'cod:one',kind:'cod',confirmed:'300.00'},
 ],details:{facts:{
  'shipping:one':{id:'driver',party_type:'store_driver',recognition_basis:'delivered_order_created_after_start',
    recorded_at:'2026-11-06T10:00:00Z',delivered_at:null},
  'delivery_event:one':{id:'assignment-one',schema:'operational_delivery_status_v1',order_id:'one',party_id:'driver',
    party_type:'store_driver',recognition_basis:'delivered_order_created_after_start',recorded_at:'2026-11-06T10:00:00Z',delivered_at:null},
  'delivery_event:two':{id:'assignment-two',schema:'operational_delivery_status_v1',order_id:'two',party_id:'driver',
    party_type:'store_driver',recognition_basis:'delivered_order_created_after_start',recorded_at:'2026-11-06T10:00:00Z',delivered_at:null},
  'shipping:three':{id:'other-driver',party_type:'store_driver',recognition_basis:'delivered_order_created_after_start',
    recorded_at:'2026-11-06T10:00:00Z',delivered_at:null},
 }}});
 const text=section('الشحن ومناديب المتجر').textContent;
 expect(text).toContain('تواصيل مثبتة: 2');
 expect(text).toContain('أجرة التوصيل المؤكدة: 0.00');
 expect(text).toContain('التحصيل المستحق لنا: غير مكتمل');
});
test('supplier covered invoices and accepted returns use evidence once and show payment remaining',async()=>{
 await render({parties:[{party_type:'supplier',party_id:'s',currency:'SAR',name:'مورد',settled:'50.00',outstanding_payable:'40.00',outstanding_receivable:'0.00'}],obligations:[{id:'s1',kind:'supplier',party_id:'s',party_type:'supplier',currency:'SAR',evidence_ids:['receipt:r1']},{id:'s2',kind:'supplier',party_id:'s',party_type:'supplier',currency:'SAR',evidence_ids:['receipt:r1']}],details:{facts:{'receipt:r1':{amount:'100.00'},'return:a':{receipt_id:'receipt:r1',amount:'10.00'},'return:other':{receipt_id:'receipt:other',amount:'999.00'}}}});
 const text=section('تغطية الموردين والمرتجعات والمدفوعات').textContent;expect(text).toContain('قيمة الفواتير المغطاة: 100.00');expect(text).toContain('المرتجعات المقبولة: 10.00');expect(text).toContain('المدفوع المخصص: 50.00');expect(text).toContain('المتبقي علينا: 40.00');expect(text).not.toContain('تأكيد الاستلام');
});
test('provider totals use the server net and one row instead of repeating claim fee details',async()=>{
 await render({obligations:[{id:'provider:o',party_type:'provider',party_id:'tabby',currency:'SAR'}],parties:[{party_type:'provider',party_id:'tabby',currency:'SAR',name:'تابي'}],details:{
   provider_reports:{'provider:o':{gross:'100.00',estimated_fees:'5.00',actual_fees:'4.00',settled:'86.00'}},
   provider_balances:{SAR:[{provider:'tabby',name:'تابي',orders:1,paid_orders:1,pending_orders:0,gross:'100.00',fees:'4.00',refunded:'10.00',net:'0.00',opening:'0.00',settled:'86.00',complete:true}]}
 }});
 const panel=section('تفاصيل منصات الدفع');
 expect(panel.querySelectorAll('tbody tr')).toHaveLength(1);
 expect(panel.querySelectorAll('thead th')).toHaveLength(7);
 expect([...panel.querySelector('tbody tr').cells].slice(1).map(cell=>cell.textContent)).toEqual(['1','100.00','4.00','10.00','0.00','0']);
 expect(panel.textContent).toContain('المحول للبنك 86.00');
 expect(panel.textContent).not.toContain('5.00');
 expect(panel.textContent).not.toContain('provider:o');
 expect(panel.querySelectorAll('article')).toHaveLength(0);
});

test('hybrid daily spend and FX aggregate wallet and credit once and keep balances separate',async()=>{
 const base={kind:'advertising',party_type:'ad_account',party_id:'hybrid',currency:'USD',business_date:'2026-10-05',ad_day_id:'advertising:hybrid:2026-10-05'};
 await render({parties:[{party_type:'ad_account',party_id:'hybrid',currency:'USD',name:'حساب هجين',funding_mode:'hybrid',ad_wallet_balance:'100.00',ad_wallet_spent:'60.00',ad_payable:'40.00'}],obligations:[{...base,id:'day:wallet',funding_type:'prepaid',sar_amount:'225.00'},{...base,id:'day:credit',funding_type:'postpaid',sar_amount:'150.00'}],details:{ad_days:{'advertising:hybrid:2026-10-05':{amount:'100.00',closed:true,observed_at:'2026-10-06T00:00:00Z'}}}});
 expect(section('الصرف الإعلاني الشهري').textContent).toContain('الصرف النهائي للأيام المغلقة: 100.00 USD');expect(section('الصرف الإعلاني اليومي').textContent).toContain('المعادل 375.00 SAR');
 const text=section('المحافظ الإعلانية والالتزامات').textContent;expect(text).toContain('رصيد المحفظة: 100.00');expect(text).toContain('المستحق الآجل: 40.00');
});
test('custody balance, receipts, expense flow and withdrawal flow are separate',async()=>{
 await render({obligations:[],parties:[{party_id:'expense',party_type:'operating_expense',name:'وقود',currency:'SAR',expense_paid:'500.00'},{party_id:'owner',party_type:'owner_withdrawal',name:'المالك',currency:'SAR',owner_withdrawals:'2000.00'}],summaries:{SAR:{operating_expenses_paid:'500.00',owner_withdrawals:'2000.00'}},details:{employee_custody:[{party_id:'employee',name:'أحمد',currency:'SAR',custody_funded:'5000.00',custody_spent:'500.00',custody_returned:'0.00',custody_remaining:'4500.00',receipts:[{id:'technical-movement',receipt_id:'technical-receipt',amount:'500.00'}]}]}});
 const custody=section('عهد الموظفين').textContent;expect(custody).toContain('الممول: 5000.00');expect(custody).toContain('المصروف: 500.00');expect(custody).toContain('الرصيد المتبقي في العهدة: 4500.00');expect(custody).toContain('إيصال 1');expect(custody).not.toContain('technical');
 const flow=section('المصروفات التشغيلية وسحوبات المالك/المدير').textContent;expect(flow).toContain('إجمالي المصروفات التشغيلية: 500.00');expect(flow).toContain('إجمالي سحوبات المالك/المدير: 2000.00');expect(flow).not.toContain('2500.00');
});

test('COD separates customer due, physical custody and remittance from delivery fees by currency',async()=>{
 const party={party_type:'store_driver',party_id:'d',currency:'SAR',name:'مندوب'};
 await render({parties:[party],obligations:[{...party,id:'cod:cash',kind:'cod'},{...party,id:'cod:card',kind:'cod'}],details:{cod_reports:{'cod:cash':{party_id:'d',currency:'SAR',gross:'300.00',collected:'200.00',customer_outstanding:'100.00',custody_amount:'200.00',confirmed_custody:'200.00',settled:'50.00',outstanding:'150.00'},'cod:card':{party_id:'d',currency:'SAR',gross:'100.00',collected:'100.00',customer_outstanding:'0.00',custody_amount:'0.00',confirmed_custody:'0.00',settled:'0.00',outstanding:'0.00'},'cod:usd':{party_id:'d',currency:'USD',gross:'999.00'}}}});
 const text=section('الشحن ومناديب المتجر').textContent;expect(text).toContain('إجمالي قيمة طلبات التحصيل: 400.00');expect(text).toContain('المحصل من العملاء: 300.00');expect(text).toContain('المتبقي لدى العملاء: 100.00');expect(text).toContain('عهدة التحصيل المؤكدة: 200.00');expect(text).toContain('المورد لأماسي: 50.00');expect(text).toContain('المتبقي لدى المنفذ: 150.00');expect(text).not.toContain('999.00');
});
test('supplier invoice net tax gross remain distinct and expected residual is not reduced by tax',async()=>{
 const p={party_type:'supplier',party_id:'s',currency:'SAR',name:'مورد',expected_payable:'20.00'};
 await render({parties:[p],obligations:[{...p,id:'s1',kind:'supplier',evidence_ids:['receipt:r']},{...p,id:'s2',kind:'supplier',evidence_ids:['receipt:r']}],details:{facts:{'receipt:r':{amount:'92.00',net_amount:'80.00',tax_amount:'12.00',gross_amount:'92.00'}}}});
 const text=section('تغطية الموردين والمرتجعات والمدفوعات').textContent;expect(text).toContain('صافي الفواتير: 80.00');expect(text).toContain('ضريبة الفواتير المثبتة: 12.00');expect(text).toContain('إجمالي الفواتير بالضريبة: 92.00');expect(text).toContain('التقديري المتبقي: 20.00');
});
