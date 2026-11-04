import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import CourierTariffApproval from './CourierTariffApproval';
import HistoricalShippingFeeReview from './HistoricalShippingFeeReview';
import api from '../../lib/api';
import {refreshOrderActivity} from '../../services/orderActivity';

jest.mock('../../lib/api', () => ({get:jest.fn(), post:jest.fn()}));
jest.mock('../../services/orderActivity', () => ({refreshOrderActivity:jest.fn()}));
let host, root;
const button = text => [...host.querySelectorAll('button')].find(b => b.textContent.includes(text));
const press = async text => { await act(async () => button(text).click()); };
async function change(input,value) {
    await act(async () => {
        Object.getOwnPropertyDescriptor(input.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype,'value').set.call(input, value);
        input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input',{bubbles:true}));
    });
}
beforeEach(() => {
    jest.resetAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(global, 'crypto', {configurable:true, value:{randomUUID:()=>'test-request-id'}});
    host=document.createElement('div'); document.body.appendChild(host); root=createRoot(host);
});
afterEach(async () => { await act(async()=>root.unmount()); host.remove(); });

test('saved deferred tariff and hash require explicit dates and review before sending approval', async () => {
    api.get.mockResolvedValue({data:{items:[{id:'smsa', name:'سمسا', keys:['smsa'], source_hash:'abc',
        terms:{cost_per_order:'15',vat_percent:'15',cod_fee_tiers:[]}}]}});
    api.post.mockResolvedValue({data:{}});
    await act(async()=>root.render(<CourierTariffApproval/>));
    expect(api.get).not.toHaveBeenCalled();
    await press('مراجعة التعرفات');
    await change(host.querySelector('select'),'smsa');
    expect(host.textContent).toContain('رسوم التحصيل غير مكتملة');
    expect(button('اعتماد تعرفة').disabled).toBe(true);
    await change(host.querySelector('input[type=datetime-local]'),'2026-11-01T00:30');
    await change(host.querySelector('input:not([type])'),'فاتورة عقد اختبار');
    await press('اعتماد تعرفة');
    expect(api.post).toHaveBeenCalledWith('/operational-balances/courier-tariffs',
        expect.objectContaining({party_id:'smsa',source_hash:'abc',confirmed:true,effective_from:'2026-11-01T00:30:00+03:00'}));
});
test('one dated historical delivery requires matching review, owner evidence and a separate confirmation', async () => {
    const preview = {order_number:'123',preview_hash:'abc',session_scope:'scope',can_approve:true,
        candidate:{amount:'17.25',party_id:'smsa',currency:'SAR'}, issues:[],openings:[{amount:'301',currency:'SAR'}],payouts:[]};
    api.post.mockResolvedValueOnce({data:preview}).mockResolvedValueOnce({data:{ok:true,order_number:'123',pending_reconciliation:true}});
    await act(async()=>root.render(<HistoricalShippingFeeReview orders={[{order_number:'123',name:'سمسا'}]}/>));
    await press('مطابقة أجرة'); await change(host.querySelector('input'),'123');
    await press('معاينة الأجرة');
    expect(host.textContent).toContain('17.25');expect(host.textContent).toContain('301');expect(button('اعتماد أجرة').disabled).toBe(true);
    await change(host.querySelector('input[maxlength="300"]'),'كشف الفترة');
    await act(async()=>host.querySelector('[type=checkbox]').click());
    await press('اعتماد أجرة');
    expect(api.post.mock.calls[1]).toEqual(['/operational-balances/historical-shipping-fees/approve',expect.objectContaining({
        expected_preview_hash:'abc',expected_session_scope:'scope',order_number:'123',confirm_opening_and_payouts:true})]);
    expect(refreshOrderActivity).not.toHaveBeenCalled();
});
test('requesting evidence for a missing date loads only the selected order then reopens its preview', async () => {
    api.post.mockResolvedValue({data:{order_number:'456',candidate:null,issues:['وقت التسليم مفقود'],can_approve:false}});
    await act(async()=>root.render(<HistoricalShippingFeeReview/>));
    await press('مطابقة أجرة'); await change(host.querySelector('input'),'456'); await press('معاينة الأجرة');
    expect(button('اعتماد أجرة')).toBeUndefined();
    await press('جلب سجل'); expect(refreshOrderActivity).toHaveBeenCalledWith('456');
    expect(api.post.mock.calls.every(([p])=>p.endsWith('/preview'))).toBe(true);
});
