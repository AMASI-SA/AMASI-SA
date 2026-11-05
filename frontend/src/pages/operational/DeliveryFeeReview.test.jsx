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
    expect(api.post.mock.calls[0][1]).not.toHaveProperty('cash_account_id');
});

const field = text => [...host.querySelectorAll('label')].find(label => label.textContent.startsWith(text))?.querySelector('input,select');
const cashAccounts = [
    {id:'cash-salla-fixture',name:'صندوق سلة التجريبي',kind:'cash',currency:'SAR',ready:true},
    {id:'cash-other-fixture',name:'صندوق آخر',kind:'cash',currency:'SAR',ready:true},
];
const prepaidItem = changes => ({id:'aramex-fixture',name:'أرامكس',keys:['aramex'],currency:'SAR',
    payment_mode:'prepaid',cash_account_id:null,source_hash:'prepaid-preview',same_terms:false,
    terms:{cost_per_order:'30',vat_percent:'15',cod_fee_percent:0,cod_fee_fixed_per_order:0,
        cod_fee_vat_percent:15,cod_fee_vat_included:false},...changes});
async function openPrepaid(item = prepaidItem(), accounts = cashAccounts) {
    api.get.mockResolvedValue({data:{items:[item],cash_accounts:accounts}});
    await act(async()=>root.render(<CourierTariffApproval/>));
    await press('مراجعة التعرفات');
    await change(field('شركة الشحن'),'aramex-fixture');
}

test('prepaid execution fee requires explicit cashbox choice and posts its exact identity', async () => {
    api.post.mockResolvedValue({data:{}});
    await openPrepaid();
    expect(host.textContent).toContain('تم التنفيذ');
    expect(field('صندوق خصم أجرة الشحن')).toBeDefined();
    expect(field('صندوق خصم أجرة الشحن').value).toBe('');
    await change(field('بداية السريان'),'2026-11-01T00:30');
    await change(field('مرجع التعرفة'),'عقد شحن تجريبي');
    expect(button('اعتماد تعرفة').disabled).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
    await change(field('صندوق خصم أجرة الشحن'),'cash-salla-fixture');
    await press('اعتماد تعرفة');
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith('/operational-balances/courier-tariffs',expect.objectContaining({
        party_id:'aramex-fixture',source_hash:'prepaid-preview',cash_account_id:'cash-salla-fixture',
        effective_from:'2026-11-01T00:30:00+03:00',confirmed:true,
    }));
    expect(host.textContent).toContain('حُفظ ربط الصندوق');
});

test('prepaid review cannot select inactive, unready, wrong-kind or foreign-currency accounts', async () => {
    await openPrepaid(prepaidItem(),[
        {id:'cash-not-ready',name:'غير جاهز',kind:'cash',currency:'SAR',ready:false},
        {id:'cash-usd',name:'دولار',kind:'cash',currency:'USD',ready:true},
        {id:'bank-fixture',name:'بنك',kind:'bank',currency:'SAR',ready:true},
        {id:'cash-inactive',name:'غير نشط',kind:'cash',currency:'SAR',ready:true,status:'inactive'},
    ]);
    expect(host.textContent).toContain('لا يوجد صندوق نشط وجاهز بالريال السعودي');
    expect(field('صندوق خصم أجرة الشحن').querySelectorAll('option')).toHaveLength(1);
    await change(field('بداية السريان'),'2026-11-01T00:30');
    await change(field('مرجع التعرفة'),'عقد شحن تجريبي');
    expect(button('اعتماد تعرفة').disabled).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
});

test('an approved prepaid tariff can explicitly bind another cashbox in a new dated version', async () => {
    api.post.mockResolvedValue({data:{}});
    await openPrepaid(prepaidItem({same_terms:true,cash_account_id:'cash-salla-fixture',current_from:'2026-11-01T00:00:00+03:00'}));
    expect(host.textContent).toContain('الصندوق المعتمد: صندوق سلة التجريبي');
    expect(field('بداية السريان')).toBeUndefined();
    await press('تعديل ربط الصندوق');
    expect(field('صندوق خصم أجرة الشحن').value).toBe('');
    await change(field('صندوق خصم أجرة الشحن'),'cash-other-fixture');
    await change(field('بداية السريان'),'2026-11-05T12:00');
    await change(field('مرجع التعرفة'),'تغيير الصندوق للفترة الجديدة');
    await press('اعتماد تعرفة');
    expect(api.post).toHaveBeenCalledWith('/operational-balances/courier-tariffs',expect.objectContaining({
        cash_account_id:'cash-other-fixture',effective_from:'2026-11-05T12:00:00+03:00',
    }));
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
