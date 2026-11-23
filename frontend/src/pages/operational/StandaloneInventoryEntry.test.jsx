import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import StandaloneInventoryEntry from './StandaloneInventoryEntry';
import {operationalApi as api,requestId} from './api';
jest.mock('react-router-dom', () => ({Link: ({children,to}) => <a href={to}>{children}</a>}));
jest.mock('./api', () => ({operationalApi: {standaloneInventoryContext:jest.fn(),standaloneInventoryControl:jest.fn(),previewStandaloneInventory:jest.fn(),saveStandaloneInventory:jest.fn(),inventoryOptionRoles:jest.fn(),saveInventoryOptionRoles:jest.fn()},requestId:jest.fn(),messageFor:()=> 'تعذر إتمام الحفظ'}));
let host,root;
const context={session_scope:'owner',can_manage_inventory_opening:true};
const metadata={products:[{id:'product',name:'سلسال',sku:'S1',unit:'piece',options:[{id:'color',name:'اللون',type:'select',values:[{id:'silver',name:'فضي'},{id:'gold',name:'ذهبي'}]}]}],components:[],locations:[{id:'shelf',name:'1-2-7',warehouse_name:'الجديد'}],writer_disabled:false,control_revision:1,entries:[]};
const result={status:'approved',batch_id:'entry1',lines:[{name:'سلسال',location_name:'1-2-7',quantity:50,unit:'piece',unit_cost:'12.50',total_cost:'625.00',preparation_state:'ready_complete',specifications:{اللون:'فضي'}}]};
const render=async(c=context)=>act(async()=>root.render(<StandaloneInventoryEntry context={c}/>));
const click=async text=>act(async()=>[...host.querySelectorAll('button')].find(b=>b.textContent===text||b.getAttribute('aria-label')===text).click());
const change=async(label,value)=>act(async()=>{const e=[...host.querySelectorAll('label')].find(l=>l.textContent.startsWith(label)).querySelector('input,select');Object.getOwnPropertyDescriptor(e.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(e,value);e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}));});
const fill=async()=>{await click('اختيار سلسال');await change('موقع التخزين','shelf');await act(async()=>host.querySelector('[aria-label="اللون: فضي"]').click());await change('الكمية','50');await change('تكلفة الوحدة','12.50');};
beforeEach(()=>{global.IS_REACT_ACT_ENVIRONMENT=true;jest.resetAllMocks();requestId.mockReturnValue('entry-request-1');localStorage.clear();host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);api.standaloneInventoryContext.mockResolvedValue(metadata);api.previewStandaloneInventory.mockResolvedValue({preview_hash:'hash',writer_disabled:false,fulfillment_eligible:false});api.saveStandaloneInventory.mockImplementation(async()=>{api.standaloneInventoryContext.mockResolvedValue({...metadata,entries:[{...result,id:'entry1'}]});return result;});});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
test('one save commits exact color and quantity, clears form and displays persisted table',async()=>{await render();await fill();await click('حفظ المنتج في المخزون');expect(api.saveStandaloneInventory).toHaveBeenCalledWith({request_id:'entry-request-1',preview_hash:'hash',lines:[{kind:'product',item_id:'product',location_id:'shelf',quantity:50,unit_cost:'12.50',inventory_option_policy_revision:0,purchase_configuration:{state:'ready',selections:[{option_id:'color',value_id:'silver'}],inputs:[]}}]});expect(host.querySelector('tbody').textContent).toContain('فضي');expect(host.querySelector('tbody').textContent).toContain('625.00');expect([...host.querySelectorAll('button')].some(b=>b.textContent==='حفظ المنتج في المخزون')).toBe(false);expect(localStorage.getItem('mezan.inventory.entry.v1:owner')).toBeNull();});
test('uncertain response survives remount and retries identical command without another preview',async()=>{api.saveStandaloneInventory.mockRejectedValueOnce(new Error('offline'));await render();await fill();await click('حفظ المنتج في المخزون');const original=api.saveStandaloneInventory.mock.calls[0][0];await act(async()=>root.unmount());root=createRoot(host);await render();await click('إعادة محاولة الحفظ');expect(api.saveStandaloneInventory.mock.calls[1][0]).toEqual(original);expect(api.previewStandaloneInventory).toHaveBeenCalledTimes(1);expect(host.querySelector('tbody').textContent).toContain('50');});
test('double-click while save pending sends one receipt command',async()=>{let finish;api.saveStandaloneInventory.mockReturnValue(new Promise(resolve=>{finish=resolve;}));await render();await fill();await click('حفظ المنتج في المخزون');await click('حفظ المنتج في المخزون');expect(api.saveStandaloneInventory).toHaveBeenCalledTimes(1);await act(async()=>finish(result));});
test('default closed input only activates independent writer with its revision',async()=>{api.standaloneInventoryContext.mockResolvedValue({...metadata,writer_disabled:true,control_revision:0});api.standaloneInventoryControl.mockResolvedValue({paused:false,revision:1});await render();expect(host.querySelector('fieldset').disabled).toBe(true);await click('تفعيل إدخال المخزون المستقل');expect(api.standaloneInventoryControl).toHaveBeenCalledWith({paused:false,revision:0});expect(api.saveStandaloneInventory).not.toHaveBeenCalled();});
test('permission denial never loads catalog or activates writer',async()=>{await render({...context,can_manage_inventory_opening:false});expect(api.standaloneInventoryContext).not.toHaveBeenCalled();expect(host.textContent).toContain('للمالك فقط');});
test('missing warehouse keeps input disabled and links warehouse setup',async()=>{api.standaloneInventoryContext.mockResolvedValue({...metadata,locations:[]});await render();expect(host.querySelector('fieldset').disabled).toBe(true);expect(host.querySelector('a').getAttribute('href')).toBe('/components-v2?workspace=warehouse');});
test('invalid quantity does not reach preview or save',async()=>{await render();await fill();await change('الكمية','2.5');await click('حفظ المنتج في المخزون');expect(api.previewStandaloneInventory).not.toHaveBeenCalled();expect(api.saveStandaloneInventory).not.toHaveBeenCalled();});

test('classifying gift fields as side additions preserves entered stock amounts and removes supplier deferral',async()=>{
  const product={...metadata.products[0],kind:'product',configuration_contract_version:2,inventory_option_policy_revision:0,options:[
    {...metadata.products[0].options[0],required:true,stock_identity:true,role_locked:true,inventory_role:'physical'},
    {id:'gift',name:'كرت إهداء',type:'radio',required:true,defer_allowed:true,inventory_role:'customization',values:[{id:'yes',name:'نعم'},{id:'no',name:'لا'}]},
    {id:'card',name:'نص الكرت',type:'textarea',required:true,defer_allowed:true,inventory_role:'customization',visibility_rules:[{option_id:'gift',value_id:'yes',operator:'='}],values:[]},
  ]};
  const roles={color:'physical',gift:'customization',card:'customization'};
  const response={product_id:'product',revision:0,catalog_fingerprint:'catalog',roles,options:product.options,product};
  const updated={...product,inventory_option_policy_revision:1,options:product.options.map(option=>option.id==='color'?option:{...option,inventory_role:'informational',defer_allowed:false})};
  api.standaloneInventoryContext.mockResolvedValue({...metadata,products:[product]});
  api.inventoryOptionRoles.mockResolvedValue(response);
  api.saveInventoryOptionRoles.mockResolvedValue({...response,revision:1,roles:{color:'physical',gift:'informational',card:'informational'},product:updated,options:updated.options});
  await render();await fill();await click('تصنيف الخيارات');
  expect([...host.querySelectorAll('button')].find(button=>button.textContent==='حفظ المنتج في المخزون').disabled).toBe(true);
  for(const name of ['كرت إهداء','نص الكرت'])await act(async()=>{
    const element=host.querySelector(`[aria-label="تصنيف ${name}"]`);
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(element,'informational');
    element.dispatchEvent(new Event('change',{bubbles:true}));
  });
  await click('حفظ التصنيف');
  expect(api.previewStandaloneInventory).not.toHaveBeenCalled();
  await click('حفظ المنتج في المخزون');
  expect(api.previewStandaloneInventory.mock.calls[0][0].lines[0]).toMatchObject({location_id:'shelf',quantity:50,unit_cost:'12.50',inventory_option_policy_revision:1,
    purchase_configuration:{state:'ready',selections:[{option_id:'color',value_id:'silver'}],inputs:[],deferred_option_ids:[]}});
  expect(api.saveInventoryOptionRoles).toHaveBeenCalledTimes(1);
});

test('a pending inventory command locks classification until its result is known',async()=>{
  let finish;api.saveStandaloneInventory.mockReturnValue(new Promise(resolve=>{finish=resolve;}));
  await render();await fill();await click('حفظ المنتج في المخزون');await click('تصنيف الخيارات');
  expect(api.inventoryOptionRoles).not.toHaveBeenCalled();
  await act(async()=>finish(result));
});

test('a policy change after preview releases only the rejected command and requires review before a fresh save',async()=>{
  const product={...metadata.products[0],kind:'product',configuration_contract_version:2,inventory_option_policy_revision:0,
    options:metadata.products[0].options.map(option=>({...option,required:true,inventory_role:'physical',stock_identity:true}))};
  const updated={...product,inventory_option_policy_revision:1};
  api.standaloneInventoryContext.mockResolvedValueOnce({...metadata,products:[product]}).mockResolvedValue({...metadata,products:[updated]});
  api.inventoryOptionRoles.mockResolvedValue({product_id:'product',revision:1,catalog_fingerprint:'current',roles:{color:'physical'},options:updated.options,product:updated});
  api.saveStandaloneInventory.mockRejectedValueOnce({response:{status:409,data:{detail:{code:'inventory_option_policy_changed'}}}}).mockResolvedValue(result);
  requestId.mockReturnValueOnce('first-attempt').mockReturnValueOnce('second-attempt');
  await render();await fill();await click('حفظ المنتج في المخزون');
  expect(localStorage.getItem('mezan.inventory.entry.v1:owner')).toBeNull();
  expect(api.previewStandaloneInventory).toHaveBeenCalledTimes(1);expect(api.saveStandaloneInventory).toHaveBeenCalledTimes(1);
  expect(host.querySelector('fieldset').disabled).toBe(false);
  const save=[...host.querySelectorAll('button')].find(button=>button.textContent==='حفظ المنتج في المخزون');
  expect(save.disabled).toBe(true);
  await click('مراجعة التصنيف الحالي');
  expect(api.inventoryOptionRoles).toHaveBeenCalledWith('product');expect(api.saveInventoryOptionRoles).not.toHaveBeenCalled();
  expect(api.saveStandaloneInventory).toHaveBeenCalledTimes(1);
  await click('حفظ المنتج في المخزون');
  expect(api.previewStandaloneInventory).toHaveBeenCalledTimes(2);
  expect(api.previewStandaloneInventory.mock.calls[1][0]).toMatchObject({request_id:'second-attempt',lines:[{
    item_id:'product',location_id:'shelf',quantity:50,unit_cost:'12.50',inventory_option_policy_revision:1,
    purchase_configuration:{state:'ready',selections:[{option_id:'color',value_id:'silver'}],inputs:[],deferred_option_ids:[]},
  }]});
  expect(api.saveStandaloneInventory).toHaveBeenCalledTimes(2);
});

test('an unrelated conflict keeps the original pending stock command for investigation',async()=>{
  api.saveStandaloneInventory.mockRejectedValueOnce({response:{status:409,data:{detail:{code:'standalone_inventory_request_conflict'}}}});
  await render();await fill();await click('حفظ المنتج في المخزون');
  expect(localStorage.getItem('mezan.inventory.entry.v1:owner')).not.toBeNull();
  expect(host.querySelector('fieldset').disabled).toBe(true);
  expect(host.textContent).not.toContain('مراجعة التصنيف الحالي');
});
