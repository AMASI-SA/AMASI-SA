import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import {MemoryRouter} from 'react-router-dom';
import InventorySuiteFrame,{inventoryNavigationForUser,InventoryPageHeader,InventorySheet} from './InventorySuiteFrame';
jest.mock('react-router-dom',()=>{global.TextEncoder=require('util').TextEncoder;return jest.requireActual('react-router');});
jest.mock('../../../context/AuthContext',()=>({useOptionalAuth:()=>({user:{id:'owner',is_owner:true}})}));
let host,root;
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test('owner inventory suite exposes the twelve native workflow entry points',()=>{
  const items=inventoryNavigationForUser({is_owner:true});expect(items).toHaveLength(12);expect(items.find(i=>i.id==='warehouses').to).toBe('/components-v2?workspace=warehouse');expect(items.find(i=>i.id==='purchases').to).toBe('/purchase-invoices');expect(items.some(i=>i.to.includes('127.0.0.1'))).toBe(false);
});
test('stock read grants expose only the existing reader and its report presentation',()=>{
  const user={web_page_access:{pages:[{id:'products.inventory',to:'/inventory-v2',mode:'read'}]}};
  expect(inventoryNavigationForUser(user).map(i=>i.id)).toEqual(['home','catalog','reports']);expect(inventoryNavigationForUser({...user,is_owner:true}).map(i=>i.id)).toEqual(['home','catalog','reports']);
});
test('mobile menu keeps navigation available and closes after a native route is selected',async()=>{
  await act(async()=>root.render(<MemoryRouter><InventorySuiteFrame active="components"><InventoryPageHeader title="المكونات"/></InventorySuiteFrame></MemoryRouter>));
  await act(async()=>host.querySelector('[aria-label="صفحات المخزون"]').click());const dialog=document.querySelector('[role="dialog"]');expect(dialog).not.toBeNull();expect(dialog.querySelectorAll('nav a')).toHaveLength(12);await act(async()=>[...dialog.querySelectorAll('a')].find(a=>a.textContent.includes('المستودعات')).click());expect(document.querySelector('[role="dialog"]')).toBeNull();
});
test('drawer exposes a named accessible dialog and delegates close without any network writer',async()=>{
  const close=jest.fn();await act(async()=>root.render(<InventorySheet open onClose={close} title="إضافة مكوّن" footer={<button>حفظ</button>}><label>الاسم<input/></label></InventorySheet>));const dialog=document.querySelector('[role="dialog"]');expect(dialog.getAttribute('aria-labelledby')).toBeTruthy();expect(dialog.textContent).toContain('إضافة مكوّن');await act(async()=>dialog.querySelector('[aria-label="إغلاق"]').click());expect(close).toHaveBeenCalledTimes(1);
});
