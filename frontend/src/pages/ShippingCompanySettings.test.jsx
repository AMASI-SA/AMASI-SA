import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import ShippingCompanySettings from './ShippingCompanySettings';
import api from '../lib/api';
jest.mock('../lib/api',()=>({get:jest.fn(),put:jest.fn(),post:jest.fn()}));
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

async function change(input, value) {
  await act(async()=>{
    Object.getOwnPropertyDescriptor(input.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype,'value').set.call(input,value);
    input.dispatchEvent(new Event(input.tagName === 'SELECT' ? 'change' : 'input',{bubbles:true}));
  });
}

test.each(['TestCarrier QZ','DHL Express'])('a new %s carrier can save its original code and choose prepaid mode from shipping settings',async(name)=>{
  jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT=true;
  const prompt=jest.spyOn(window,'prompt').mockReturnValue(name);
  const host=document.createElement('div');document.body.appendChild(host);const root=createRoot(host);
  const code=name==='DHL Express'?'dhl':'carrier-qz';
  api.get.mockResolvedValue({data:{shipping_companies:[],payment_methods:[]}});
  api.put.mockResolvedValue({data:{ok:true}});
  try {
    await act(async()=>root.render(<ShippingCompanySettings mezan2/>));
    await act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent.includes('إضافة شركة جديدة')).click());
    await change(host.querySelector('input[aria-label^="رمز الشركة الأصلي"]'),code);
    await change(host.querySelector(`[data-testid="cost-${name}"]`),'17');
    await act(async()=>host.querySelector(`[data-testid="payment-mode-prepaid-${name}"]`).click());
    expect(api.put).not.toHaveBeenCalled();expect(api.post).not.toHaveBeenCalled();
    await act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent.includes('حفظ الإعدادات')).click());
    expect(api.put).toHaveBeenCalledWith('/settings',expect.objectContaining({shipping_companies:[expect.objectContaining({
      name,salla_carrier_key:code,payment_mode:'prepaid',is_deferred:false,cost_per_order:17,
    })]}));
    expect(api.post).not.toHaveBeenCalled();
    expect(host.textContent).toContain('راجع التعرفة لاعتماد الربط واختيار صندوق الدفع المقدم');
  } finally {await act(async()=>root.unmount());host.remove();prompt.mockRestore();}
});

test('saving a changed carrier code discards an already open tariff preview',async()=>{
  jest.resetAllMocks();global.IS_REACT_ACT_ENVIRONMENT=true;
  const host=document.createElement('div');document.body.appendChild(host);const root=createRoot(host);
  api.get.mockImplementation(path=>Promise.resolve({data:path==='/settings'?{
    shipping_companies:[{name:'TestCarrier QZ',salla_carrier_key:'carrier-qz',cost_per_order:17,is_deferred:false}],payment_methods:[],
  }:{items:[{id:'fixture-party',name:'TestCarrier QZ',keys:['carrier-qz'],salla_carrier_key:'carrier-qz',
    identity_match:'exact',payment_mode:'prepaid',source_hash:'old-preview',terms:{cost_per_order:'17',vat_percent:'15'}}],cash_accounts:[]}}));
  api.put.mockResolvedValue({data:{ok:true}});
  const press=async text=>act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent.includes(text)).click());
  try {
    await act(async()=>root.render(<ShippingCompanySettings mezan2/>));
    await press('مراجعة التعرفات');await change(host.querySelector('select'),'fixture-party');
    expect(host.textContent).toContain('يُعتمد هذا الرمز كما هو');
    await change(host.querySelector('input[aria-label^="رمز الشركة الأصلي"]'),'updated-code');
    await press('حفظ الإعدادات');
    expect(host.querySelector('select')).toBeNull();
    expect(host.textContent).not.toContain('يُعتمد هذا الرمز كما هو');
    expect(api.post).not.toHaveBeenCalled();
  } finally {await act(async()=>root.unmount());host.remove();}
});
