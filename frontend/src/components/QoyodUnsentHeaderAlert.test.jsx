import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
jest.mock('react-router-dom', () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
import QoyodUnsentHeaderAlert from './QoyodUnsentHeaderAlert';
import api from '../lib/api';
import { useOptionalAuth } from '../context/AuthContext';
jest.mock('../lib/api', () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock('../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
let container, root;
const payload = count => ({ data: { local_summary: { count, source: 'local_invoice_mirror', updated_at: '2026-11-08T10:00:00Z', latest_invoice_sync_at: null } } });
const countText = () => container.querySelector('[data-testid="qoyod-unsent-header-alert-count"]')?.textContent;
const render = async () => act(async () => root.render(<QoyodUnsentHeaderAlert />));
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers(); jest.resetAllMocks();
    useOptionalAuth.mockReturnValue({ user: { id: 'a', role: 'owner' } });
    api.get.mockResolvedValue(payload(666)); api.post.mockResolvedValue(payload(650));
    container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.useRealTimers(); });
test('employee has no banner or requests', async () => {
    useOptionalAuth.mockReturnValue({ user: { id:'e', role:'employee' } });
    await render(); expect(api.get).not.toHaveBeenCalled(); expect(container.textContent).toBe('');
});
test('one saved read and no timed, focus or visibility refresh', async () => {
    await render();
    await act(async () => { jest.advanceTimersByTime(86400000); window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); });
    expect(api.get).toHaveBeenCalledTimes(1); expect(api.post).not.toHaveBeenCalled();
    expect(api.get.mock.calls[0][0]).toBe('/integrations/qoyod/header-count');
    expect(countText()).toBe((666).toLocaleString('ar-SA'));
    expect(container.textContent).toContain('حسب الفواتير المحفوظة');
    expect(container.querySelector('a').getAttribute('href')).toBe('/integrations-v2/qoyod?tab=invoices');
});
test('manual click uses local endpoint once and displays result without another GET', async () => {
    let finish; api.post.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    await render();
    await act(async () => { container.querySelector('button').click(); container.querySelector('button').click(); });
    expect(api.post).toHaveBeenCalledTimes(1); expect(api.post.mock.calls[0][0]).toBe('/integrations/qoyod/header-count/refresh');
    await act(async () => finish(payload(650)));
    expect(countText()).toBe((650).toLocaleString('ar-SA')); expect(api.get).toHaveBeenCalledTimes(1);
});
test('failed manual count retains prior result', async () => {
    await render(); api.post.mockRejectedValue(new Error('offline'));
    await act(async () => container.querySelector('button').click());
    expect(countText()).toBe((666).toLocaleString('ar-SA')); expect(container.textContent).toContain('آخر نتيجة محفوظة لم تتغير');
});
test('absent snapshot is unknown, never zero', async () => {
    api.get.mockResolvedValue({ data: { local_summary:null } }); await render();
    expect(countText()).toBeUndefined(); expect(container.textContent).toContain('لم يُحسب العدد');
});
test('late initial GET cannot replace clicked result', async () => {
    let finish; api.get.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    await render(); await act(async () => container.querySelector('button').click());
    await act(async () => finish(payload(900)));
    expect(countText()).toBe((650).toLocaleString('ar-SA'));
});
test('owner switching clears old result and fences in-flight manual response', async () => {
    let finish; api.post.mockImplementation(() => new Promise(resolve => { finish = resolve; }));
    await render(); await act(async () => container.querySelector('button').click());
    useOptionalAuth.mockReturnValue({ user:{ id:'b', role:'owner' } }); api.get.mockResolvedValue(payload(2));
    await render(); await act(async () => finish(payload(900)));
    expect(countText()).toBe((2).toLocaleString('ar-SA'));
    expect(api.post.mock.calls[0][2].signal.aborted).toBe(true);
});
test.each([
    { count:null, source:'local_invoice_mirror', updated_at:'2026-11-08T10:00:00Z' },
    { count:-1, source:'local_invoice_mirror', updated_at:'2026-11-08T10:00:00Z' },
    { count:2.5, source:'local_invoice_mirror', updated_at:'2026-11-08T10:00:00Z' },
    { count:0, source:'queue', updated_at:'2026-11-08T10:00:00Z' },
    { count:0, source:'local_invoice_mirror', updated_at:'invalid' },
])('invalid or unrelated evidence remains unknown: %j', async value => {
    api.get.mockResolvedValue({ data:{local_summary:value} }); await render(); expect(countText()).toBeUndefined();
});
test('local zero is labeled as saved data and never as all invoices sent', async () => {
    api.get.mockResolvedValue(payload(0)); await render();
    expect(countText()).toBe((0).toLocaleString('ar-SA'));
    expect(container.textContent).toContain('حسب الفواتير المحفوظة');
    expect(container.textContent).not.toContain('جميع الفواتير');
});
