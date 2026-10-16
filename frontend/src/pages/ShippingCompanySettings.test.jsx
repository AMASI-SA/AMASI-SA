import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import ShippingCompanySettings from './ShippingCompanySettings';
import api from '../lib/api';
jest.mock('../lib/api',()=>({get:jest.fn(),put:jest.fn()}));
jest.mock('react-router-dom',()=>({Link:({to,children,...props})=><a href={to} {...props}>{children}</a>}));
jest.mock('sonner',()=>({toast:{success:jest.fn(),error:jest.fn()}}));
test('Mezan 2 reuses existing shipping settings without migrating or saving on mount',async()=>{
  global.IS_REACT_ACT_ENVIRONMENT=true;
  const host=document.createElement('div');document.body.appendChild(host);const root=createRoot(host);
  const settings={shipping_companies:[{name:'SMSA',cost:17.25,is_deferred:true,cod_fee_percent:0.01}]};
  const before=JSON.stringify(settings);
  api.get.mockResolvedValue({data:settings});
  try {
    await act(async()=>root.render(<ShippingCompanySettings mezan2/>));
    expect(api.get).toHaveBeenCalledWith('/settings');
    expect(api.put).not.toHaveBeenCalled();
    expect(JSON.stringify(settings)).toBe(before);
    expect(host.textContent).toContain('SMSA');
    expect(host.textContent).toContain('مشتركة مع تقارير الأرباح');
    expect(host.querySelector('[data-testid="settings-back"]').getAttribute('href')).toBe('/operational-balances');
  } finally {await act(async()=>root.unmount());host.remove();}
});
