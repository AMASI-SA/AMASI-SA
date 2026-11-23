import React,{act,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {InventoryProductOptions,InventoryProductPicker} from './InventoryProductFields';
let host,root;
const product={id:'fur',name:'فروة',configuration_contract_version:2,options:[
  {id:'size',name:'المقاس',type:'radio',required:true,stock_identity:true,values:[{id:'two',name:'سنتان'}]},
  {id:'emb',name:'تطريز',type:'radio',required:true,defer_allowed:true,values:[{id:'yes',name:'نعم'},{id:'no',name:'لا'}]},
  {id:'name',name:'الاسم',type:'text',required:true,defer_allowed:true,values:[],visibility_rules:[{option_id:'emb',value_id:'yes',operator:'='}]},
  {id:'note',name:'ملاحظة',type:'textarea',required:false,defer_allowed:true,values:[]},
]};
const initial={state:'ready',selections:[{option_id:'size',value_id:'two'},{option_id:'emb',value_id:'yes'}],inputs:[{option_id:'name',value:'فاطمة'}],deferred_option_ids:[]};
let current;
function Editor(){const [config,setConfig]=useState(initial);current=config;return <InventoryProductOptions product={product} config={config} onChange={setConfig}/>;}
const render=async node=>act(async()=>root.render(node));
const click=async label=>act(async()=>host.querySelector(`[aria-label="${label}"]`).click());
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});

test('no immediately hides and clears conditional name; yes starts with a blank name',async()=>{
  await render(<Editor/>);
  expect(host.querySelector('[aria-label="الاسم · البند 1"]').value).toBe('فاطمة');
  await click('تطريز: لا');
  expect(host.querySelector('[aria-label="الاسم · البند 1"]')).toBeNull();
  expect(current.inputs).toEqual([]);
  await click('تطريز: نعم');
  expect(host.querySelector('[aria-label="الاسم · البند 1"]').value).toBe('');
});

test('individual deferred name keeps literal yes fixed and optional blank note is not deferred',async()=>{
  await render(<Editor/>);await click('الاسم: يُحدد لاحقًا');
  expect(current.deferred_option_ids).toEqual(['name']);
  expect(current.selections).toContainEqual({option_id:'emb',value_id:'yes'});
  expect(current.state).toBe('raw');
  expect(host.querySelector('[aria-label="الاسم · البند 1"]')).toBeNull();
  expect(host.querySelector('[aria-label="ملاحظة · البند 1"]').value).toBe('');
  expect(host.querySelector('[aria-label="المقاس: يُحدد عند طلب العميل"]')).toBeNull();
});

test('deferring parent clears child name and distinguishes it from no',async()=>{
  await render(<Editor/>);await click('تطريز: يُحدد عند طلب العميل');
  expect(current.deferred_option_ids).toEqual(['emb']);expect(current.inputs).toEqual([]);
  expect(current.selections).toEqual([{option_id:'size',value_id:'two'}]);
  expect(host.querySelector('[aria-label="تطريز: لا"]').checked).toBe(false);
});

test('picker keyboard focus is not selection; clicking shows checked selection',async()=>{
  const onPick=jest.fn();
  await render(<InventoryProductPicker items={[product,{id:'second',name:'سلسال'}]} initialOpen keepOpen onPick={onPick}/>);
  expect(host.querySelectorAll('[aria-selected="true"]')).toHaveLength(0);
  await click('اختيار سلسال');
  const selected=host.querySelector('[aria-selected="true"]');
  expect(selected.getAttribute('data-product-id')).toBe('second');
  expect(selected.textContent).toContain('مختار');
  expect(host.querySelector('[data-product-id="fur"]').getAttribute('aria-selected')).toBe('false');
});

test('selected product card also identifies the current choice',async()=>{
  await render(<InventoryProductPicker items={[product]} selected={product} onPick={()=>{}}/>);
  expect(host.querySelector('.mz-product-selected-mark').textContent).toBe('مختار');
});

test('side questions and text are a neutral summary without stock controls',async()=>{
  const definition={...product,options:[...product.options,
    {id:'card',name:'هل تريد كرت إهداء؟',type:'radio',required:true,inventory_role:'informational',values:[{id:'yes',name:'نعم'}]},
    {id:'card-text',name:'نص كرت الإهداء',type:'textarea',required:true,inventory_role:'informational',visibility_rules:[{option_id:'card',value_id:'yes',operator:'='}],values:[]},
  ]};
  await render(<InventoryProductOptions product={definition} config={initial} onChange={()=>{}}/>);
  expect(host.querySelector('[aria-label="هل تريد كرت إهداء؟: نعم"]')).toBeNull();
  expect(host.querySelector('[aria-label="نص كرت الإهداء · البند 1"]')).toBeNull();
  expect(host.querySelector('.mz-product-side-options').textContent).toContain('نص كرت الإهداء');
  expect(host.querySelector('.mz-product-side-options').textContent).toContain('لا تدخل في جاهزية المخزون');
  expect(host.querySelector('[aria-label="الاسم · البند 1"]')).not.toBeNull();
  expect(host.querySelector('[aria-label="المقاس: سنتان"]')).not.toBeNull();
});
