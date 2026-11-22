import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import CustomerRefundAccessDialog from './CustomerRefundAccessDialog';
import { getCustomerRefundAccess, saveCustomerRefundAccess } from '../services/customerRefundAccess';

jest.mock('../services/customerRefundAccess', () => ({ getCustomerRefundAccess: jest.fn(), saveCustomerRefundAccess: jest.fn() }));
const employee = { id: 'employee', name: 'هلال', account: { user_id: 'staff' } };
const current = {
    employee_id: 'employee', user: { id: 'staff', name: 'هلال' },
    access: { mode: 'none', policy_version: 5, page_revision: 8, session_scope: 'a'.repeat(64), sources: [], enabled: false },
    sources: [{ kind: 'bank', id: 'bank', name: 'بنك الرد', currency: 'SAR' },
        { kind: 'provider', id: 'salla', name: 'سلة', currency: 'SAR' }],
};
let host, root;
beforeEach(() => {
    jest.clearAllMocks();
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
    getCustomerRefundAccess.mockResolvedValue(current);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); globalThis.IS_REACT_ACT_ENVIRONMENT = false; });
async function render(props = {}) {
    const onSaved = jest.fn();
    await act(async () => root.render(<CustomerRefundAccessDialog employee={employee} onSaved={onSaved} onClose={jest.fn()} {...props}/>));
    return onSaved;
}
async function chooseMode(mode) {
    await act(async () => { const select = host.querySelector('select'); select.value = mode; select.dispatchEvent(new Event('change', { bubbles: true })); });
}
async function chooseBank() { await act(async () => host.querySelector('input[aria-label="بنك الرد"]').click()); }
async function submit() { await act(async () => host.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))); }

test('saves only the shared mode, explicit source and observed identity/revisions', async () => {
    saveCustomerRefundAccess.mockResolvedValue({ ...current, access: { ...current.access, mode: 'write', enabled: true, sources: [{kind:'bank',id:'bank'}], policy_version: 6, page_revision: 9 } });
    const onSaved = await render();
    expect(getCustomerRefundAccess).toHaveBeenCalledTimes(1);
    expect(getCustomerRefundAccess).toHaveBeenCalledWith('employee', expect.any(AbortSignal));
    expect(host.textContent).toContain('صلاحية مشتركة لتطبيق أماسي وموقع ميزان');
    expect([...host.querySelectorAll('input[type="checkbox"]')].every(input => !input.checked)).toBe(true);
    await chooseMode('write'); await chooseBank(); await submit();
    expect(saveCustomerRefundAccess).toHaveBeenCalledTimes(1);
    expect(saveCustomerRefundAccess).toHaveBeenCalledWith('employee', {
        mode: 'write', sources: [{kind:'bank',id:'bank'}], expected_policy_version: 5,
        expected_page_revision: 8, expected_session_scope: 'a'.repeat(64),
    });
    expect(onSaved).toHaveBeenCalledTimes(1);
});

test('stale grants preserve the draft and require reread without automatic mutation retry', async () => {
    saveCustomerRefundAccess.mockRejectedValue({ response: { status: 409 } });
    const onSaved = await render(); await chooseMode('read'); await chooseBank(); await submit();
    expect(host.querySelector('select').value).toBe('read');
    expect(host.querySelector('input[aria-label="بنك الرد"]').checked).toBe(true);
    expect(host.querySelector('button[type="submit"]').disabled).toBe(true);
    expect(host.textContent).toContain('تغيّر حساب الموظف أو صلاحياته');
    expect(saveCustomerRefundAccess).toHaveBeenCalledTimes(1);
    expect(onSaved).not.toHaveBeenCalled();
});

test('an unknown save outcome requires a fresh grant read', async () => {
    saveCustomerRefundAccess.mockRejectedValue(new Error('network'));
    await render(); await chooseMode('read'); await chooseBank(); await submit();
    expect(host.querySelector('button[type="submit"]').disabled).toBe(true);
    expect(saveCustomerRefundAccess).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain('تعذّر تأكيد الحفظ');
});

test('a different linked account cannot populate or save this grant editor', async () => {
    getCustomerRefundAccess.mockResolvedValue({ ...current, user: { id: 'someone-else' } });
    await render();
    expect(host.querySelector('form')).toBeNull();
    expect(host.textContent).toContain('تعذّر تحميل صلاحيات');
    expect(saveCustomerRefundAccess).not.toHaveBeenCalled();
});

test('an aborted previous employee read cannot replace the current editor', async () => {
    let finish;
    getCustomerRefundAccess.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    await render();
    const other = { id:'second', name:'شهاب', account:{ user_id:'second-account' } };
    getCustomerRefundAccess.mockResolvedValue({ ...current, employee_id:other.id, user:{id:other.account.user_id} });
    await render({employee:other});
    await act(async () => finish(current));
    expect(host.textContent).toContain('شهاب');
    expect(host.querySelector('form')).not.toBeNull();
    expect(getCustomerRefundAccess.mock.calls[0][1].aborted).toBe(true);
});
