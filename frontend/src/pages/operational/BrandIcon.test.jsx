import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import BrandIcon from './BrandIcon';
import { ResourceIcon } from './ApprovedFinanceUI';
import { ResourceCard } from './ApprovedDirectory';
import ApprovedHome from './ApprovedHome';

jest.mock('react-router-dom', () => { global.TextEncoder = require('util').TextEncoder; return jest.requireActual('react-router'); });
global.IS_REACT_ACT_ENVIRONMENT = true;
let host, root;
beforeEach(() => { host=document.createElement('div'); document.body.appendChild(host); root=createRoot(host); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const render = async node => act(async () => root.render(<MemoryRouter>{node}</MemoryRouter>));
const resource = (kind, id, name, amount='50.00') => ({kind,id,name,currency:'SAR',access:{balance:true,movements:'all'},balance:{visible:true,side:'for_us',amount},operations:[]});

test('card keeps the exact name, amount and route with a decorative local logo', async () => {
  await render(<ResourceCard resource={resource('provider','paypal','باي بال')} href="/operational-balances?view=ledger&kind=provider&id=paypal"/>);
  expect(host.querySelector('.resource').getAttribute('href')).toContain('kind=provider&id=paypal');
  expect(host.querySelector('strong').textContent).toBe('باي بال');
  expect(host.querySelector('.number').textContent).toContain('50.00');
  const icon=host.querySelector('[data-brand="paypal"]');
  expect(icon.getAttribute('aria-hidden')).toBe('true');
  expect(icon.querySelector('img').getAttribute('src')).toBe('/operational-ui/brands/paypal.png');
  expect(icon.querySelector('img').alt).toBe('');
});

test('image failure uses the correct kind fallback and another identity renders normally', async () => {
  await render(<ResourceIcon resource={resource('bank','id','بنك الإنماء')} size="lg"/>);
  await act(async () => host.querySelector('img').dispatchEvent(new Event('error')));
  expect(host.querySelector('img')).toBeNull();
  expect(host.querySelector('use').getAttribute('href')).toBe('/operational-ui/icons.svg#landmark');
  expect(host.querySelector('.mz-resource-icon--lg')).not.toBeNull();
  await render(<ResourceIcon resource={resource('provider','salla','سلة')} size="sm"/>);
  expect(host.querySelector('img').getAttribute('src')).toBe('/operational-ui/brands/salla.png');
  expect(host.querySelector('img').width).toBe(28);
});

test('missing marks use name badges; unknown brands remain generic', async () => {
  await render(<ResourceIcon resource={resource('provider','Emkan','إمكان')}/>);
  expect(host.querySelector('img')).toBeNull();
  expect(host.querySelector('[data-brand="emkan"]').textContent).toBe('إمكان');
  await render(<BrandIcon brand="constructor" size="constructor"/>);
  expect(host.querySelector('[data-brand]')).toBeNull();
  expect(host.querySelector('.mz-resource-icon--md use')).not.toBeNull();
});

test('home inherits brand cards while prepaid shipping and movement arrows remain distinct', async () => {
  const bank=resource('bank','b','بنك الإنماء','90.00');
  const cash={...resource('cash','w','محفظة سلة','500.00'),prepaid_shipping_cash:true};
  const provider=resource('provider','tabby','تابي');
  const context={actor:{is_owner:true},resources:[bank,cash,provider],recent_movements:[
    {id:'r1',movement_type:'in',amount:'20.00',currency:'SAR',from_name:'تابي',to_name:'بنك الإنماء'},
    {id:'r2',movement_type:'out',amount:'10.00',currency:'SAR',from_name:'بنك الإنماء',to_name:'مورد'},
  ]};
  await render(<ApprovedHome context={context} to={view=>'/?view='+view} onDetail={()=>{}}/>);
  expect(host.querySelector('.hero .big').textContent).toContain('90.00');
  expect([...host.querySelectorAll('.resource [data-brand]')].map(x=>x.dataset.brand)).toEqual(['alinma','tabby']);
  const wallet=[...host.querySelectorAll('.resource')].find(x=>x.textContent.includes('محفظة سلة'));
  expect(wallet.querySelector('[data-brand]')).toBeNull();
  expect(wallet.querySelector('use').getAttribute('href')).toBe('/operational-ui/icons.svg#package-check');
  expect(host.querySelector('.tablelink .c-in use').getAttribute('href')).toContain('#arrow-down-left');
  expect(host.querySelector('.tablelink .c-out use').getAttribute('href')).toContain('#arrow-up-right');
});
