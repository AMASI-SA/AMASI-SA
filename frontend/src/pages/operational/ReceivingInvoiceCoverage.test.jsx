import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import ReceivingInvoiceCoverage from './ReceivingInvoiceCoverage';
import {operationalApi as api} from './api';

jest.mock('./api',()=>({operationalApi:{receivingInvoiceCoverage:jest.fn(),receivingCorrectionPreview:jest.fn()},messageFor:()=> 'تعذر التحقق'}));
let host,root;
const scope='test-owner-scope';
const payload={schema:'receiving_component_coverage_v1',session_scope:scope,read_only:true,posting_allowed:false,
  state_revision:7,invoice_count:2,review_count:1,after_snapshot_count:1,saved_balance_as_of:'2026-05-06T00:00:00Z',rows:[
    {invoice_id:'one',invoice_number:'TEST-OK',matched_components:1,component_count:1,invoice_gross_sar:'9.00',differences:[]},
    {invoice_id:'two',invoice_number:'TEST-PARTIAL',matched_components:1,component_count:3,invoice_gross_sar:'15.00',
      differences:[{piece_id:'wrong-piece',component:'product',status:'wrong_currency',source_amount_sar:'5.00',saved_currency:'QAR',saved_gross:'5.00'},
        {piece_id:'missing-piece',component:'service:tailoring',status:'missing',source_amount_sar:'5.00'}]}]};
const click=async name=>act(async()=>[...host.querySelectorAll('button')].find(el=>el.textContent===name).click());
beforeEach(async()=>{global.IS_REACT_ACT_ENVIRONMENT=true;api.receivingInvoiceCoverage.mockReset();api.receivingCorrectionPreview.mockReset();host=document.createElement('div');
  document.body.appendChild(host);root=createRoot(host);await act(async()=>root.render(<ReceivingInvoiceCoverage sessionScope={scope}/>));});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});

test('opens receiving-only differences on demand and keeps each source and saved currency visible',async()=>{
  api.receivingInvoiceCoverage.mockResolvedValue(payload);
  expect(api.receivingInvoiceCoverage).not.toHaveBeenCalled();
  await click('فحص فواتير استلام المورد');
  for(const value of ['TEST-PARTIAL','missing-piece','wrong-piece','5.00 SAR','5.00 QAR','1 فاتورة أحدث']) expect(host.textContent).toContain(value);
  expect(host.textContent).not.toContain('TEST-OK');
  await click('عرض جميع الفواتير'); expect(host.textContent).toContain('TEST-OK');
  expect(host.textContent).not.toContain('اعتماد الفرق');
});
test('mismatched session or a write-capable response does not disclose invoice details',async()=>{
  for(const change of [{session_scope:'other'},{posting_allowed:true}]){
    api.receivingInvoiceCoverage.mockResolvedValue({...payload,...change});
    await click('فحص فواتير استلام المورد');
    expect(host.querySelector('[role="alert"]')).not.toBeNull();expect(host.textContent).not.toContain('TEST-PARTIAL');
  }
});

const previewButton = 'معاينة التصحيح التاريخي';
const preview = {
  schema:'receiving_correction_preview_v1', session_scope:scope, saved_balance_as_of:payload.saved_balance_as_of,
  invoice_id:'two', state_revision:7, read_only:true, posting_allowed:false, status:'review_only', reasons:[],
  legs:[
    {piece_id:'wrong-piece',component:'product',currency:'QAR',before:'5.00',after:'0.00'},
    {piece_id:'wrong-piece',component:'product',currency:'SAR',before:'0.00',after:'5.00'},
  ],
};
async function loadCoverage() {
  api.receivingInvoiceCoverage.mockResolvedValue(payload);
  await click('فحص فواتير استلام المورد');
}

test('requests one invoice on demand and displays separate simulated gross legs', async () => {
  api.receivingCorrectionPreview.mockResolvedValue(preview);
  await loadCoverage();
  expect(api.receivingCorrectionPreview).not.toHaveBeenCalled();
  await click(previewButton);
  expect(api.receivingCorrectionPreview).toHaveBeenCalledWith('two',payload.saved_balance_as_of,7);
  const section = host.querySelector('[aria-label="مقترح التصحيح التاريخي"]');
  expect([...section.querySelectorAll('tbody tr')].map(tr => [...tr.cells].map(td => td.textContent))).toEqual([
    ['wrong-piece','منتج','QAR','5.00','0.00'], ['wrong-piece','منتج','SAR','0.00','5.00'],
  ]);
  expect(section.querySelectorAll('button')).toHaveLength(0);
});

test('explains a blocked invoice without showing any proposed amounts', async () => {
  api.receivingCorrectionPreview.mockResolvedValue({...preview, status:'blocked', legs:[],
    reasons:['supplier_opening_requires_allocation_review']});
  await loadCoverage();
  await click(previewButton);
  const section = host.querySelector('[aria-label="مقترح التصحيح التاريخي"]');
  expect(section.textContent).toContain('الرصيد الافتتاحي');
  expect(section.querySelector('table')).toBeNull();
});

test('rejects responses with another invoice, snapshot or session, or permission to post', async () => {
  await loadCoverage();
  for (const invalid of [{invoice_id:'other'}, {saved_balance_as_of:'stale'}, {state_revision:6}, {session_scope:'other'}, {posting_allowed:true},
      {status:'blocked', reasons:['supplier_opening_requires_allocation_review']}]) {
    api.receivingCorrectionPreview.mockResolvedValue({...preview,...invalid});
    await click(previewButton);
    expect(host.querySelector('[role="alert"]').textContent).toContain('تعذر');
    expect(host.querySelector('[aria-label="مقترح التصحيح التاريخي"]')).toBeNull();
  }
});
