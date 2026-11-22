import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {ResourceCard} from './ApprovedDirectory';
import {CustomerReceiptUpload} from './CustomerAdjustmentReceipt';
import {simpleBalancesApi as api} from './simpleBalancesApi';
jest.mock('react-router-dom',()=>{globalThis.TextEncoder=require('util').TextEncoder;globalThis.TextDecoder=require('util').TextDecoder;return jest.requireActual('react-router');});
jest.mock('./simpleBalancesApi',()=>({simpleBalancesApi:{uploadCustomerReceipt:jest.fn()},simpleBalanceError:error=>error?.localMessage || 'تعذر رفع الإيصال'}));
global.IS_REACT_ACT_ENVIRONMENT=true;
let host,root;
beforeEach(()=>{jest.clearAllMocks();host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);});
afterEach(()=>{act(()=>root.unmount());host.remove();});
const render=async element=>act(async()=>root.render(element));
test('provider adjustments use separate colored amounts and counts without changing the computed balance or fee issue count',async()=>{
  const resource={kind:'provider',id:'salla',name:'سلة',currency:'SAR',access:{balance:true,movements:'all',report:true},balance:{visible:true,amount:'390.00'},order_issues_count:3,
    provider_position:{complete:true,net:'390.00',currency:'SAR',paid_orders:4,uncomputed_principal:'0.00',
      customer_adjustments:{refund_pending_count:1,refund_pending_amount:'100.00',collection_pending_count:2,collection_pending_amount:'30.00',currency:'SAR'}}};
  await render(<ResourceCard resource={resource}/>);
  expect(host.querySelector('.number').textContent).toContain('390.00');
  expect(host.querySelector('.issue-badge').textContent).toContain('3 طلبات');
  expect(host.querySelector('.adjustment-badge.refund').textContent).toContain('100.00');
  expect(host.querySelector('.adjustment-badge.collection').textContent).toContain('2 طلب');
  expect(host.querySelector('.adjustment-badge.collection').textContent).toContain('30.00');
  await render(<ResourceCard resource={{...resource,access:{...resource.access,movements:'own'}}}/>);
  expect(host.querySelector('.adjustment-badge')).toBeNull();
});
test('unsupported or oversized receipt files never call the scoped upload endpoint',async()=>{
  const changed=jest.fn();
  await render(<CustomerReceiptUpload context={{actor:{id:'staff'},session_scope:'scope'}} orderNumber="245" value="" onChange={changed}/>);
  const input=host.querySelector('input[type="file"]');
  for(const file of [new File(['<script>'],'proof.html',{type:'text/html'}),new File([new Uint8Array(5*1024*1024+1)],'large.pdf',{type:'application/pdf'})]){
    await act(async()=>{Object.defineProperty(input,'files',{configurable:true,value:[file]});input.dispatchEvent(new Event('change',{bubbles:true}));});
  }
  expect(api.uploadCustomerReceipt).not.toHaveBeenCalled();expect(changed).not.toHaveBeenCalled();expect(host.querySelector('[role="alert"]').textContent).toContain('5 ميغابايت');
});
