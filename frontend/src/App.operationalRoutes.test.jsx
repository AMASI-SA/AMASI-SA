import React, { act, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, useLocation, useNavigationType } from 'react-router-dom';
import { useAuth, useOptionalAuth } from './context/AuthContext';
import { AppRoutes } from './App';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('./context/AuthContext', () => ({ useAuth: jest.fn(), useOptionalAuth: jest.fn(), AuthProvider: ({ children }) => children }));
jest.mock('./components/Layout', () => ({ children }) => <div data-testid="legacy-layout">{children}</div>);
jest.mock('./pages/Login', () => () => null);
jest.mock('./pages/Register', () => () => null);
jest.mock('./components/ui/sonner', () => ({ Toaster: () => null }));
jest.mock('./pages/AmasiDeliveryApp', () => () => <div data-testid="driver-only" />);
jest.mock('./pages/operational/ApprovedFinanceRoute', () => () => <div data-testid="balances-entry" />);
jest.mock('./pages/FinancialPositionLedger', () => () => <div data-testid="accounting-ledger" />);
jest.mock('./pages/operational/ProviderFeeSetup', () => () => <div data-testid="actual-fee-page" />);
jest.mock('./pages/operational/OperationalFinancialPosition', () => () => <div data-testid="owner-position" />);
jest.mock('./pages/operational/EmployeeBalanceSpecialist', () => props => <div data-testid="employee-reader" data-page={props.pageId} data-mode={props.page.mode}>{props.search}</div>);
let host, root;
function Probe() { const { pathname, search } = useLocation(); const action = useNavigationType(); return <output data-testid="route" data-action={action}>{pathname + search}</output>; }
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; window.scrollTo = jest.fn(); const auth = { user: { id: 'owner', role: 'owner' }, loading: false }; useAuth.mockReturnValue(auth); useOptionalAuth.mockReturnValue(auth); host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const show = url => act(async () => root.render(<MemoryRouter initialEntries={[url]}><Suspense fallback={<p>loading</p>}><AppRoutes /></Suspense><Probe /></MemoryRouter>));

test('legacy financial center replaces its route with approved operational center', async () => {
  await show('/financial-position');
  expect(host.querySelector('[data-testid="route"]').textContent).toBe('/operational-balances?view=position');
  expect(host.querySelector('[data-testid="route"]').dataset.action).toBe('REPLACE');
  expect(host.querySelector('[data-testid="balances-entry"]')).not.toBeNull();
  expect(host.querySelector('[data-testid="accounting-ledger"]')).toBeNull();
});
test('explicit accounting ledger remains accessible as its distinct service', async () => {
  await show('/financial-position-ledger');
  expect(host.querySelector('[data-testid="accounting-ledger"]')).not.toBeNull();
  expect(host.querySelector('[data-testid="route"]').textContent).toBe('/financial-position-ledger');
});
test('canonical owner fee route mounts its actual page inside the finance shell', async () => {
  await show('/operational-balances/provider-fees');
  expect(host.querySelector('[data-testid="actual-fee-page"]')).not.toBeNull();
  expect(host.querySelectorAll('.shell')).toHaveLength(1);
  expect(host.querySelector('[data-testid="legacy-layout"]')).toBeNull();
});
test('employee report interception keeps the restricted reader, query and read mode in the same shell', async () => {
  const auth = { user: { id: 'reader', role: 'user', web_page_access: { pages: [{ id: 'balances.reports', section: 'balances', to: '/operational-balances/financial-position', mode: 'read' }] } }, loading: false };
  useAuth.mockReturnValue(auth); useOptionalAuth.mockReturnValue(auth);
  await show('/operational-balances/financial-position?view=providers');
  expect(host.querySelector('[data-testid="owner-position"]')).toBeNull();
  const reader = host.querySelector('[data-testid="employee-reader"]');
  expect(reader.dataset.page).toBe('balances.reports'); expect(reader.dataset.mode).toBe('read');
  expect(reader.textContent).toBe('?view=providers');
  expect(host.querySelectorAll('.shell')).toHaveLength(1);
  expect(host.querySelectorAll('nav[aria-label="صفحات الأرصدة"] a')).toHaveLength(1);
});
test('new owner review route does not create a grant for a report-only employee', async () => {
  const auth = { user: { id: 'reader', role: 'user', web_page_access: { pages: [{ id: 'balances.reports', section: 'balances', to: '/operational-balances/financial-position', mode: 'read' }] } }, loading: false };
  useAuth.mockReturnValue(auth); useOptionalAuth.mockReturnValue(auth);
  await show('/operational-balances/shipping-review');
  expect(host.querySelector('[data-testid="owner-position"], [data-testid="employee-reader"], .mz-specialized')).toBeNull();
  expect(host.textContent).toContain('غير متاحة');
});
