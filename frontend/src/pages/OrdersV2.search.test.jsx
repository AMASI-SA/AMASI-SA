import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import OrdersV2 from './OrdersV2';
import { useOrders } from '../hooks/useOrders';
import { getOrderFilterSummary } from '../services/orderEngine';
jest.mock('../hooks/useOrders', () => ({ useOrders: jest.fn() }));
jest.mock('../services/orderEngine', () => ({ getOrderFilterSummary: jest.fn() }));
let host, root, searchCombined, reload;
const button = text => [...host.querySelectorAll('button')].find(node => node.textContent.trim() === text);
async function click(node) { await act(async () => node.dispatchEvent(new MouseEvent('click', { bubbles: true }))); }
async function input(placeholder, value) {
    const node = host.querySelector(`[placeholder="${placeholder}"]`);
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(node, value);
        node.dispatchEvent(new Event('input', { bubbles: true }));
    });
}
beforeEach(async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers().setSystemTime(new Date('2026-10-17T12:00:00Z'));
    searchCombined = jest.fn(); reload = jest.fn();
    useOrders.mockReturnValue({ orders: [], hasMore: false, loading: false, initialLoading: false,
        error: '', searchMode: true, resultSummary: null, loadMore: jest.fn(), reload, searchCombined });
    getOrderFilterSummary.mockResolvedValue({ total: 0, statusCards: [] });
    host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host);
    await act(async () => root.render(<MemoryRouter initialEntries={['/orders-v2?campaign_id=123']}><OrdersV2 /></MemoryRouter>));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.useRealTimers(); });

test('URL and typed fields remain drafts until explicit submit', async () => {
    expect(searchCombined).not.toHaveBeenCalled();
    await input('رقم المنتج', '456');
    await input('Campaign ID', '789');
    await act(async () => jest.advanceTimersByTime(60000));
    expect(searchCombined).not.toHaveBeenCalled();
    await click(button('بحث'));
    expect(searchCombined).toHaveBeenCalledTimes(1);
    expect(searchCombined).toHaveBeenCalledWith(expect.objectContaining({ product_id: '456', campaign_id: '789' }));
});

test('one shared dashboard picker changes only draft and includes whole final Saudi day on search', async () => {
    expect(host.querySelectorAll('[data-testid="ads-arabic-date-range-picker"]')).toHaveLength(1);
    expect(host.querySelectorAll('input[type="datetime-local"]')).toHaveLength(0);
    await click(host.querySelector('[data-testid="ads-arabic-date-range-picker"] > button'));
    await click(button('آخر 7 أيام'));
    await click(button('حفظ وتطبيق'));
    expect(searchCombined).not.toHaveBeenCalled();
    await click(button('بحث'));
    expect(searchCombined).toHaveBeenCalledWith(expect.objectContaining({
        created_from: '2026-10-11T00:00:00+03:00', created_to: '2026-10-18T00:00:00+03:00',
    }));
});

test('clear removes every advanced filter as well as the text', async () => {
    await input('رقم المنتج', '456');
    await click(button('مسح'));
    expect(host.querySelector('[placeholder="Campaign ID"]').value).toBe('');
    expect(host.querySelector('[placeholder="رقم المنتج"]').value).toBe('');
    expect(reload).toHaveBeenCalledTimes(1);
    expect(searchCombined).not.toHaveBeenCalled();
});
