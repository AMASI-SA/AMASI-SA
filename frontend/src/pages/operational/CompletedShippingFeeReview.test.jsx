import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import api from '../../lib/api';
import CompletedShippingFeeReview from './CompletedShippingFeeReview';
import { requestId } from './api';

jest.mock('../../lib/api', () => ({ __esModule: true, default: { post: jest.fn() } }));
jest.mock('./api', () => ({ requestId: jest.fn() }));
global.IS_REACT_ACT_ENVIRONMENT = true;
const scope = 'a'.repeat(64), previewHash = 'b'.repeat(64);
const fixture = (mode = 'deferred') => ({
    order_number: '10', session_scope: scope, preview_hash: previewHash,
    candidate: { order_id: '100', order_number: '10', party_type: 'courier', party_id: 'aramex',
        party_name: 'شركة الشحن', currency: 'SAR', amount: '34.50', payment_mode: mode,
        cash_account_name: mode === 'prepaid' ? 'الصندوق المعتمد' : null, completed_at: '2026-11-06T09:00:00Z' },
    effects: { expense_increase: '34.50', currency: 'SAR', opening_change: '0.00',
        courier_payable_increase: mode === 'deferred' ? '34.50' : '0.00', cash_outflow: mode === 'prepaid' ? '34.50' : '0.00' },
    openings: [{ id: 'opening', name: 'رصيد الشركة الافتتاحي', amount: '70.00', currency: 'SAR', direction: 'for_party' }],
    payouts: [], issues: [], can_approve: true, already_approved: false, approval_state: 'requires_review',
    period_start: '2026-11-01T00:00:00Z',
});
let host, root;
const render = async (props = {}) => { await act(async () => root.render(<CompletedShippingFeeReview sessionScope={scope} {...props} />)); };
const button = text => [...host.querySelectorAll('button')].find(node => node.textContent.includes(text));
const click = async text => { await act(async () => button(text).click()); };
const input = async (index, value) => {
    const node = host.querySelectorAll('input:not([type="checkbox"])')[index];
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(node, value);
        node.dispatchEvent(new Event('input', { bubbles: true }));
    });
};
const open = async () => { await click('مراجعة أجور'); await input(0, '10'); };
const preview = async () => { await open(); await click('معاينة الأجرة'); };
const consent = async () => { await input(1, 'كشف معتمد'); await act(async () => host.querySelector('[type="checkbox"]').click()); };
const visibility = async value => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value });
    await act(async () => document.dispatchEvent(new Event('visibilitychange')));
};
beforeEach(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
    api.post.mockImplementation(() => Promise.resolve({ data: fixture() }));
    requestId.mockReturnValue('completion-review-request');
});
afterEach(async () => {
    if (root) await act(async () => root.unmount());
    host.remove(); jest.resetAllMocks(); delete document.visibilityState;
});

test('only explicit preview reads one order and shows exact financial effects', async () => {
    await render(); expect(api.post).not.toHaveBeenCalled();
    await open(); expect(api.post).not.toHaveBeenCalled();
    await click('معاينة الأجرة');
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith('/operational-balances/completed-shipping-fees/preview',
        { order_number: '10' }, expect.objectContaining({ signal: expect.any(AbortSignal), timeout: 30000 }));
    expect(host.textContent).toContain('رصيد الشركة الافتتاحي');
    const cells = [...host.querySelectorAll('tbody tr')].map(row => row.cells[1].textContent);
    expect(cells).toEqual(['34.50 SAR', '34.50 SAR', '0.00 SAR', '0.00 SAR']);
    expect(button('اعتماد أجرة').disabled).toBe(true);
});

test('prepaid preview shows the named cash effect and no added payable', async () => {
    api.post.mockResolvedValue({ data: fixture('prepaid') });
    await render(); await preview();
    expect(host.textContent).toContain('الصندوق المعتمد');
    const cells = [...host.querySelectorAll('tbody tr')].map(row => row.cells[1].textContent);
    expect(cells).toEqual(['34.50 SAR', '0.00 SAR', '34.50 SAR', '0.00 SAR']);
});

test('approval requires reviewed evidence, sends one exact token, and never recalculates in the background', async () => {
    await render(); await preview(); await consent();
    api.post.mockResolvedValueOnce({ data: { ok: true, order_number: '10', session_scope: scope, pending_reconciliation: true } });
    await click('اعتماد أجرة');
    expect(api.post).toHaveBeenLastCalledWith('/operational-balances/completed-shipping-fees/approve', {
        request_id: 'completion-review-request', order_number: '10', expected_preview_hash: previewHash,
        expected_session_scope: scope, evidence_ref: 'كشف معتمد', confirm_opening_and_payouts: true,
    }, { timeout: 30000 });
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(button('اعتماد أجرة')).toBeUndefined();
    expect(host.textContent).toContain('اضغط زر تحديث الأرصدة في هذه الصفحة');
});

test('native conflicts show their reason and offer no approval', async () => {
    api.post.mockResolvedValue({ data: { ...fixture(), can_approve: false,
        issues: [{ code: 'native_overlap', message: 'يوجد قيد شحن سابق يحتاج مطابقة' }] } });
    await render(); await preview();
    expect(host.querySelector('[role="alert"]').textContent).toContain('قيد شحن سابق');
    expect(button('اعتماد أجرة')).toBeUndefined();
});

test.each(['other_scope', 'wrong_order', 'wrong_effect'])('invalid %s preview never enables approval', async reason => {
    const data = fixture();
    if (reason === 'other_scope') data.session_scope = 'c'.repeat(64);
    else if (reason === 'wrong_order') data.order_number = 'another';
    else data.effects.cash_outflow = '100.00';
    api.post.mockResolvedValue({ data });
    await render(); await preview();
    expect(button('اعتماد أجرة')).toBeUndefined();
    expect(host.textContent).not.toContain('34.50');
    expect(host.textContent).toContain('أعد المعاينة');
});

test('account change aborts the old preview and ignores its late result', async () => {
    let resolve;
    api.post.mockImplementation(() => new Promise(done => { resolve = done; }));
    await render(); await preview();
    const { signal } = api.post.mock.calls[0][2];
    await render({ sessionScope: 'd'.repeat(64) });
    expect(signal.aborted).toBe(true);
    await act(async () => resolve({ data: fixture() }));
    expect(host.textContent).not.toContain('34.50');
    expect(button('اعتماد أجرة')).toBeUndefined();
});

test('repeat click cannot submit a second in-flight preview or approval', async () => {
    let resolve;
    api.post.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await render(); await open();
    await act(async () => { const node = button('معاينة الأجرة'); node.click(); node.click(); });
    expect(api.post).toHaveBeenCalledTimes(1);
    await act(async () => resolve({ data: fixture() }));
    await consent();
    api.post.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await act(async () => { const node = button('اعتماد أجرة'); node.click(); node.click(); });
    expect(api.post).toHaveBeenCalledTimes(2);
    await act(async () => resolve({ data: { ok: true, order_number: '10', session_scope: scope, pending_reconciliation: true } }));
});

test('hidden preview intent waits for visibility and a completed preview never automatically retries', async () => {
    await visibility('hidden');
    await render(); await preview();
    expect(api.post).not.toHaveBeenCalled();
    await visibility('visible');
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain('34.50 SAR');
    await visibility('hidden'); await visibility('visible');
    await act(async () => window.dispatchEvent(new Event('focus')));
    expect(api.post).toHaveBeenCalledTimes(1);
});

test('hiding an unfinished preview aborts it and ignores its late result before resuming visibly', async () => {
    let resolve;
    api.post.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await render(); await preview();
    const { signal } = api.post.mock.calls[0][2];
    await visibility('hidden');
    expect(signal.aborted).toBe(true);
    await act(async () => resolve({ data: fixture() }));
    expect(host.textContent).not.toContain('34.50 SAR');
    expect(button('اعتماد أجرة')).toBeUndefined();
    expect(api.post).toHaveBeenCalledTimes(1);
    await visibility('visible');
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(api.post.mock.calls[1][2].signal).not.toBe(signal);
    expect(host.textContent).toContain('34.50 SAR');
    await visibility('hidden'); await visibility('visible');
    expect(api.post).toHaveBeenCalledTimes(2);
});

test('unmount cancels a pending preview without admitting its late financial result', async () => {
    let resolve;
    api.post.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await render(); await preview();
    const { signal } = api.post.mock.calls[0][2];
    await act(async () => root.unmount()); root = null;
    expect(signal.aborted).toBe(true);
    await act(async () => resolve({ data: fixture() }));
    expect(api.post).toHaveBeenCalledTimes(1);
});

test.each(['unmount', 'scope'])('%s does not abort a submitted approval and its late response cannot update another scope', async change => {
    await render(); await preview(); await consent();
    let resolve;
    api.post.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await click('اعتماد أجرة');
    const [, payload, config] = api.post.mock.calls[1];
    expect(payload.request_id).toBe('completion-review-request');
    expect(config).toEqual({ timeout: 30000 });
    expect(config.signal).toBeUndefined();
    if (change === 'unmount') { await act(async () => root.unmount()); root = null; }
    else await render({ sessionScope: 'd'.repeat(64) });
    await act(async () => resolve({ data: { ok: true, order_number: '10', session_scope: scope, pending_reconciliation: true } }));
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(host.textContent).not.toContain('حُفظ اعتماد أجرة');
    expect(host.textContent).not.toContain('34.50 SAR');
    expect(requestId).toHaveBeenCalledTimes(1);
});

test('approval resolves while hidden without another preview or automatic retry', async () => {
    await render(); await preview(); await consent();
    let resolve;
    api.post.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
    await click('اعتماد أجرة'); await visibility('hidden');
    expect(api.post.mock.calls[1][2].signal).toBeUndefined();
    await act(async () => resolve({ data: { ok: true, order_number: '10', session_scope: scope, pending_reconciliation: true } }));
    await visibility('visible');
    expect(api.post).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain('حُفظ اعتماد أجرة');
    expect(button('اعتماد أجرة')).toBeUndefined();
});

test('an uncertain approval retry preserves the same request id and exact reviewed payload', async () => {
    await render(); await preview(); await consent();
    api.post.mockRejectedValueOnce(new Error('انقطع الاتصال'));
    await click('اعتماد أجرة');
    const first = api.post.mock.calls[1];
    api.post.mockResolvedValueOnce({ data: { ok: true, order_number: '10', session_scope: scope, pending_reconciliation: true } });
    await click('اعتماد أجرة');
    expect(api.post.mock.calls[2]).toEqual(first);
    expect(requestId).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain('حُفظ اعتماد أجرة');
});
