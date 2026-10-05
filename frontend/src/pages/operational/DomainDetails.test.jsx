import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import DomainDetails from './DomainDetails';
let host,root;
const section = title => [...host.querySelectorAll('details')].find(node => node.querySelector('summary').textContent === title);
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
const render = async report => act(async()=>root.render(<DomainDetails report={report}/>));

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
 const text=section('الشحن ومناديب المتجر').textContent;expect(text).toContain('عدد الطلبات: 3');expect(text).toContain('التوصيلات المؤكدة: 2');expect(text).toContain('أجرة التوصيل المؤكدة: 20.00');
});
test('supplier covered invoices and accepted returns use evidence once and show payment remaining',async()=>{
 await render({parties:[{party_type:'supplier',party_id:'s',currency:'SAR',name:'مورد',settled:'50.00',outstanding_payable:'40.00',outstanding_receivable:'0.00'}],obligations:[{id:'s1',kind:'supplier',party_id:'s',party_type:'supplier',currency:'SAR',evidence_ids:['receipt:r1']},{id:'s2',kind:'supplier',party_id:'s',party_type:'supplier',currency:'SAR',evidence_ids:['receipt:r1']}],details:{facts:{'receipt:r1':{amount:'100.00'},'return:a':{receipt_id:'receipt:r1',amount:'10.00'},'return:other':{receipt_id:'receipt:other',amount:'999.00'}}}});
 const text=section('تغطية الموردين والمرتجعات والمدفوعات').textContent;expect(text).toContain('قيمة الفواتير المغطاة: 100.00');expect(text).toContain('المرتجعات المقبولة: 10.00');expect(text).toContain('المدفوع المخصص: 50.00');expect(text).toContain('المتبقي علينا: 40.00');expect(text).not.toContain('تأكيد الاستلام');
});
test('provider actual settlement fee remains distinct from estimated fee and cash settlement',async()=>{
 await render({obligations:[{id:'provider:o',party_type:'provider',party_id:'tabby',currency:'SAR'}],parties:[{party_type:'provider',party_id:'tabby',currency:'SAR',name:'تابي'}],details:{provider_reports:{'provider:o':{gross:'100.00',cancelled:'0.00',refunded:'10.00',net:'90.00',estimated_fees:'5.00',actual_fees:'4.00',expected_receivable:'0.00',settled:'86.00',outstanding:'0.00'}}}});
 const text=section('تفاصيل منصات الدفع').textContent;expect(text).toContain('العمولة التقديرية: 5.00');expect(text).toContain('العمولة الفعلية المسوّاة: 4.00');expect(text).toContain('الوارد الفعلي من التسويات: 86.00');expect(text).not.toContain('provider:o');
});
