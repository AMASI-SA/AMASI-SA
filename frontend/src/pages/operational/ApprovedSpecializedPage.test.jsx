import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { useOptionalAuth } from '../../context/AuthContext';
import ApprovedSpecializedPage, { financeReturnTarget } from './ApprovedSpecializedPage';
import OperationalContextRoute from './OperationalContextRoute';
import ApprovedFinanceRoute from './ApprovedFinanceRoute';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
jest.mock('../../context/AuthContext', () => ({ useOptionalAuth: jest.fn() }));
jest.mock('../../components/Layout', () => ({ children }) => <div data-testid="other-workspace">{children}</div>);
jest.mock('./OperationalBalances', () => () => <div data-testid="real-settings" />);
const owner = { id: 'owner', name: 'مالك المتجر', role: 'owner' };
let host, root;
beforeEach(() => { global.IS_REACT_ACT_ENVIRONMENT = true; window.scrollTo = jest.fn(); useOptionalAuth.mockReturnValue({ user: owner }); host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
function Child({ accessMode }) { const location = useLocation(); return <div data-testid="source-page" data-mode={accessMode}>{location.pathname + location.search + location.hash}</div>; }
const show = (url, element = <ApprovedSpecializedPage><Child /></ApprovedSpecializedPage>) => act(async () => root.render(<MemoryRouter key={JSON.stringify(url)} initialEntries={[url]}>{element}</MemoryRouter>));

test.each([
  ['/operational-balances/provider-statements?provider=tabby#history', 'tools'],
  ['/operational-balances/provider-fees', 'tools'],
  ['/operational-balances/financial-position?view=providers', 'position'],
  ['/operational-balances/customer-returns?order=123', 'tools'],
  ['/operational-balances/customer-exchanges', 'tools'],
  ['/operational-balances/access', 'tools'],
  ['/operational-balances/provider-history?provider=paypal', 'reviews'],
  ['/operational-balances/shipping-review', 'reviews'],
])('keeps the canonical specialist %s and selected sidebar in the shared shell', async (url, active) => {
  await show(url);
  expect(host.querySelectorAll('.shell')).toHaveLength(1);
  expect(host.querySelector('[data-testid="source-page"]').textContent).toBe(url);
  expect(host.querySelector('nav[aria-label="صفحات الأرصدة"] a[aria-current="page"]').getAttribute('href')).toBe('/operational-balances?view=' + active);
  expect(host.querySelector('nav a').getAttribute('href')).toBe('/operational-balances?view=home');
  expect(host.querySelector('[data-testid="other-workspace"]')).toBeNull();
});

test('a scoped ledger return is preserved and navigation never appends home to the specialist path', async () => {
  const back = '/operational-balances?view=ledger&kind=provider&id=tabby';
  await show({ pathname: '/operational-balances/provider-statements', search: '?provider=tabby', hash: '#upload', state: { financeReturnTo: back } });
  expect(host.querySelector('.mz-specialized-return').getAttribute('href')).toBe(back);
  const home = host.querySelector('nav[aria-label="صفحات الأرصدة"] a');
  await act(async () => home.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 })));
  expect(host.querySelector('[data-testid="source-page"]').textContent).toBe('/operational-balances?view=home');
});

test('employee report grants do not reveal owner reports, writes or unrelated links', async () => {
  const pages = [{ id: 'balances.reports', section: 'balances', to: '/operational-balances/financial-position', mode: 'read' }];
  useOptionalAuth.mockReturnValue({ user: { id: 'reader', role: 'user', web_page_access: { pages } } });
  await show('/operational-balances/financial-position?view=providers');
  const links = [...host.querySelectorAll('nav[aria-label="صفحات الأرصدة"] a')];
  expect(links.map(link => link.getAttribute('href'))).toEqual(['/operational-balances/financial-position']);
  expect(links[0].getAttribute('aria-current')).toBe('page');
  expect(host.querySelector('a[href*="view=reports"],a[href*="view=entry"]')).toBeNull();
  expect(financeReturnTarget('/operational-balances?view=entry', pages, '/operational-balances/financial-position')).toBe('/operational-balances/financial-position');
});

test.each(['https://evil.invalid/operational-balances', '//evil.invalid/operational-balances', '/operational-balances/../dashboard-v2', '/operational-balances-other'])('rejects nonfinance return %s', target => {
  expect(financeReturnTarget(target, null, '/operational-balances?view=tools')).toBe('/operational-balances?view=tools');
});

test('specialized settings reuse the actual component in the same finance shell', async () => {
  await show('/operational-balances?view=specialized&section=openings', <ApprovedFinanceRoute />);
  expect(host.querySelector('[data-testid="real-settings"]')).not.toBeNull();
  expect(host.querySelectorAll('.shell')).toHaveLength(1);
  expect(host.querySelector('[data-testid="other-workspace"]')).toBeNull();
});

test('inventory keeps its original workspace unless entered explicitly from finance and preserves reader mode', async () => {
  const element = <OperationalContextRoute><Child /></OperationalContextRoute>;
  await show('/inventory-v2?tab=stock', element);
  expect(host.querySelector('[data-testid="other-workspace"]')).not.toBeNull();
  useOptionalAuth.mockReturnValue({ user: { id: 'reader', web_page_access: { pages: [{ id: 'products.inventory', section: 'products', to: '/inventory-v2?tab=stock', mode: 'read' }] } } });
  await show('/inventory-v2?from=operational-tools&tab=stock', element);
  expect(host.querySelector('.shell')).not.toBeNull();
  expect(host.querySelector('[data-testid="source-page"]').dataset.mode).toBe('read');
});

test('embedded inventory remounts its scoped readers when actor or grant mode changes', async () => {
  const mounted=jest.fn(),closed=jest.fn();
  function ScopedChild() { React.useEffect(()=>{mounted();return closed;},[]); return <Child/>; }
  const render=()=>act(async()=>root.render(<MemoryRouter initialEntries={['/inventory-v2?from=operational-tools&tab=stock']}><OperationalContextRoute><ScopedChild/></OperationalContextRoute></MemoryRouter>));
  await render(); expect(mounted).toHaveBeenCalledTimes(1);
  useOptionalAuth.mockReturnValue({user:{...owner,id:'another-owner',tenant_id:'next-tenant'}});
  await render(); expect(closed).toHaveBeenCalledTimes(1); expect(mounted).toHaveBeenCalledTimes(2);
});
