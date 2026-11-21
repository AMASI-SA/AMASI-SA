import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import { simpleBalancesApi as api } from './simpleBalancesApi';
import ApprovedReviewPage from './ApprovedReviewPage';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('../../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
jest.mock('./simpleBalancesApi', () => ({ simpleBalancesApi: { context: jest.fn() }, simpleBalanceError: () => 'تعذرت قراءة النطاق' }));
jest.mock('./CompletedShippingFeeReview', () => ({ sessionScope }) => <div data-testid="shipping-review">{sessionScope}</div>);
jest.mock('./ProviderHistoryReview', () => ({ sessionScope, providerId }) => <div data-testid="history-review">{sessionScope}:{providerId}</div>);
const owner = { id: 'owner', role: 'owner' };
const context = { actor: { id: 'owner', is_owner: true }, session_scope: 'scope-a', status: 'active', resources: [], operations: [] };
let root, host;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; jest.clearAllMocks(); useOptionalAuth.mockReturnValue({ user: owner, loading: false }); api.context.mockResolvedValue(context); host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const show = kind => act(async () => root.render(<MemoryRouter initialEntries={['/operational-balances/provider-history?provider=paypal']}><ApprovedReviewPage kind={kind} /></MemoryRouter>));
test('mounts the actual provider review with validated active scope and provider', async () => {
  await show('provider');
  expect(host.querySelector('[data-testid="history-review"]').textContent).toBe('scope-a:paypal');
  expect(api.context).toHaveBeenCalledTimes(1);
  expect(api.context.mock.calls[0][1]).toEqual({ homePreview: false, directoryOnly: true });
  expect(host.querySelector('a').getAttribute('href')).toBe('/operational-balances/financial-position');
});
test.each([
  { ...context, status: 'draft' },
  { ...context, session_scope: '' },
  { ...context, actor: { id: 'other', is_owner: true } },
  { ...context, actor: { id: 'owner', is_owner: false } },
])('does not mount approval controls for unavailable or mismatched scope', async value => {
  api.context.mockResolvedValue(value); await show('shipping');
  expect(host.querySelector('[data-testid="shipping-review"]')).toBeNull();
});
test('ungranted employee never requests owner review context', async () => {
  useOptionalAuth.mockReturnValue({ user: { id: 'reader', role: 'user' } }); await show('shipping');
  expect(api.context).not.toHaveBeenCalled(); expect(host.querySelector('[data-testid="shipping-review"]')).toBeNull();
});
test('a late previous-owner read cannot open approval controls in the new session', async () => {
  let resolve; api.context.mockReturnValueOnce(new Promise(done => { resolve = done; }));
  await show('shipping');
  useOptionalAuth.mockReturnValue({ user: { id: 'reader', role: 'user' } }); await show('shipping');
  await act(async () => resolve(context));
  expect(host.querySelector('[data-testid="shipping-review"]')).toBeNull();
});
