import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import QoyodReconciliation from './QoyodReconciliation';
import api from '../lib/api';

jest.mock('../lib/api', () => ({ get: jest.fn(), post: jest.fn() }));
let root, container;
const saved = { ok: true, counts: { 'مطابق': 1 }, rows: [
  { order_number: 'saved-order', salla_date: '2026-10-01', match: 'مطابق' },
], scan: { updated_at: '2026-11-05T12:00:00Z', running: false, stale: false } };

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  jest.clearAllMocks(); jest.useFakeTimers();
  api.get.mockResolvedValue({ data: saved });
  container = document.createElement('div'); document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.useRealTimers(); });

test('opening the page only displays the saved generation; a failed manual request retains it', async () => {
  await act(async () => root.render(<QoyodReconciliation />));
  expect(api.get).toHaveBeenCalledWith('/integrations/qoyod/reconciliation-report');
  expect(api.post).not.toHaveBeenCalled();
  expect(container.textContent).toContain('saved-order');
  api.post.mockRejectedValueOnce(new Error('offline'));
  await act(async () => container.querySelector('[data-testid="recon-run-btn"]').click());
  expect(container.textContent).toContain('saved-order');
  expect(container.querySelector('[data-testid="recon-error"]').textContent).toContain('offline');
});

test('an active shared scan disables manual restart and polls display status only', async () => {
  await act(async () => root.render(<QoyodReconciliation />));
  api.post.mockResolvedValueOnce({ data: { ...saved, accepted: true,
    scan: { ...saved.scan, running: true } } });
  await act(async () => container.querySelector('[data-testid="recon-run-btn"]').click());
  expect(container.querySelector('[data-testid="recon-run-btn"]').disabled).toBe(true);
  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
  await act(async () => jest.advanceTimersByTime(5000));
  expect(api.get).toHaveBeenCalledTimes(2);
  expect(api.post).toHaveBeenCalledTimes(1);
});
