import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
let container;
let root;
function render(element) { act(() => root.render(element)); return { unmount: () => act(() => root.unmount()) }; }
const screen = {
    getByText: (text) => Array.from(container.querySelectorAll('*')).find(el => el.textContent === text),
    queryByText: (text) => Array.from(container.querySelectorAll('*')).find(el => el.textContent === text) || null,
    getByRole: (role) => container.querySelector(`[role="${role}"]`),
};
global.IS_REACT_ACT_ENVIRONMENT = true;
import api from '../lib/api';
import MezanLiveSourcesCard from './MezanLiveSourcesCard';

jest.mock('../lib/api', () => ({ __esModule: true, default: { get: jest.fn() } }));
const data = {total_sessions: 2, known_source_percent: 100,
    generated_at: '2026-11-18T12:00:00Z', last_event_at: '2026-11-18T11:59:00Z',
    sources: [{key: 'snapchat', label: 'سناب شات', sessions: 2, percent: 100, paid_evidence_sessions: 1}]};
beforeEach(() => { container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container); jest.useFakeTimers(); jest.clearAllMocks(); Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'}); });
afterEach(() => { if (container.hasChildNodes()) act(() => root.unmount()); container.remove(); jest.useRealTimers(); });
const flush = async () => { await act(async () => { await Promise.resolve(); }); };

test('renders sessions from Mezan and suspends polling in hidden tabs and after unmount', async () => {
    api.get.mockResolvedValue({data});
    const view = render(<MezanLiveSourcesCard />); await flush();
    expect(screen.getByText('إجمالي الجلسات: 2')).toBeTruthy();
    expect(container.textContent).toContain('سناب شات');
    expect(api.get.mock.calls[0][0]).toBe('/first-party-attribution/v1/live-sources');
    Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'hidden'});
    await act(async () => { jest.advanceTimersByTime(120000); });
    expect(api.get).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, 'visibilityState', {configurable: true, value: 'visible'});
    await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
    expect(api.get).toHaveBeenCalledTimes(2);
    view.unmount();
    await act(async () => { jest.advanceTimersByTime(120000); });
    expect(api.get).toHaveBeenCalledTimes(2);
});

test('failure does not masquerade as zero and stale results are labelled', async () => {
    api.get.mockRejectedValueOnce(new Error('offline'));
    render(<MezanLiveSourcesCard />); await flush();
    expect(screen.getByRole('alert').textContent).toContain('لا تعني صفر زيارات');
    expect(screen.queryByText('إجمالي الجلسات: 0')).toBeNull();
    api.get.mockResolvedValueOnce({data});
    await act(async () => { jest.advanceTimersByTime(60000); });
    api.get.mockRejectedValueOnce(new Error('offline'));
    await act(async () => { jest.advanceTimersByTime(60000); });
    expect(screen.getByRole('alert').textContent).toContain('آخر قراءة ناجحة');
    expect(screen.getByText('إجمالي الجلسات: 2')).toBeTruthy();
});


test('malformed responses are unavailable rather than crashing the dashboard', async () => {
    api.get.mockResolvedValue({data: {}});
    render(<MezanLiveSourcesCard />); await flush();
    expect(screen.getByRole('alert').textContent).toContain('البيانات غير متاحة');
});

test('slow reads do not overlap and unmount cancels the request', async () => {
    api.get.mockReturnValue(new Promise(() => {}));
    const view = render(<MezanLiveSourcesCard />); await flush();
    await act(async () => { jest.advanceTimersByTime(180000); });
    expect(api.get).toHaveBeenCalledTimes(1);
    const signal = api.get.mock.calls[0][1].signal;
    view.unmount();
    expect(signal.aborted).toBe(true);
});

test('pages through five sources locally without another request', async () => {
    api.get.mockResolvedValue({data: {...data, sources: Array.from({length: 10}, (_, i) => ({key: String(i), label: `مصدر ${i}`, sessions: i, percent: i}))}});
    render(<MezanLiveSourcesCard />); await flush();
    expect(container.querySelectorAll('tbody tr')).toHaveLength(5);
    expect(container.textContent).toContain('مصدر 0');
    expect(container.textContent).not.toContain('مصدر 5');
    const buttons = () => [...container.querySelectorAll('nav button')];
    expect(buttons()[0].disabled).toBe(true);
    act(() => buttons()[1].click());
    expect(container.textContent).toContain('مصدر 9');
    expect(container.textContent).not.toContain('مصدر 0');
    expect(buttons()[1].disabled).toBe(true);
    act(() => buttons()[0].click());
    expect(container.textContent).toContain('مصدر 0');
    expect(api.get).toHaveBeenCalledTimes(1);
});
