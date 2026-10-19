import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import OpeningInventory from './OpeningInventory';
import {operationalApi as api} from './api';
jest.mock('./api', () => ({operationalApi: {inventoryOpeningContext: jest.fn(), previewInventoryOpening: jest.fn(), approveInventoryOpening: jest.fn()}, requestId: () => 'request-1', messageFor: () => 'تعذر إتمام العملية'}));
let host, root;
const context = {session_scope: 'owner-1', status: 'draft', can_manage_inventory_opening: true, permissions: {manage: true}};
const product = {id: 'p1', name: 'سلسال', sku: 'S1', options: [{id: 'color', name: 'اللون', type: 'select', values: [{id: 'gold', name: 'ذهبي'}]}]};
const metadata = {products: [product], locations: [{id: 'l1', name: 'رف 1'}], writer_disabled: false, approved: false};
const render = async (c = context) => act(async () => root.render(<OpeningInventory context={c}/>));
const click = async text => act(async () => [...host.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || b.textContent) === text).click());
const change = async (label, value) => act(async () => {
  const el = [...host.querySelectorAll('label')].find(l => l.textContent.startsWith(label)).querySelector('input,select');
  Object.getOwnPropertyDescriptor(el.tagName === 'SELECT' ? HTMLSelectElement.prototype : HTMLInputElement.prototype, 'value').set.call(el, value);
  el.dispatchEvent(new Event(el.tagName === 'SELECT' ? 'change' : 'input', {bubbles: true}));
});
const fill = async () => {await click('إضافة سلسال · S1'); await change('حالة المنتج — 1', 'raw'); await change('الكمية — 1', '3'); await change('تكلفة الوحدة بالريال — 1', '12.50'); await change('موقع التخزين — 1', 'l1'); await change('اللون — 1', 'gold');};
beforeEach(() => {global.IS_REACT_ACT_ENVIRONMENT = true; jest.resetAllMocks(); host = document.createElement('div'); document.body.appendChild(host); root = createRoot(host); api.inventoryOpeningContext.mockResolvedValue(metadata); api.previewInventoryOpening.mockResolvedValue({lines: [{name: 'Product', specifications: {color: 'gold'}, location_name: 'Shelf 1', quantity: 3, unit_cost: '12.50', total_cost: '37.50'}], preview_hash: 'hash', financial_effect: 'none', writer_disabled: false, total_quantity: 3, total_cost: '37.50', currency: 'SAR'}); api.approveInventoryOpening.mockResolvedValue({status: 'approved'});});
afterEach(async () => {await act(async () => root.unmount()); host.remove();});
test('owner can search real catalog during draft without activating accounting', async () => {await render(); expect(host.textContent).toContain('سلسال'); await change('بحث في كتالوج', 'missing'); expect(host.textContent).toContain('لا توجد منتجات مطابقة'); expect(api.approveInventoryOpening).not.toHaveBeenCalled();});
test('preview is read-only and approval requires confirmation with exact preview payload', async () => {await render(); await fill(); await click('معاينة المخزون الافتتاحي'); expect(api.previewInventoryOpening.mock.calls[0][0].lines[0]).toEqual({kind: 'product', item_id: 'p1', quantity: 3, unit_cost: '12.50', location_id: 'l1', purchase_configuration: {state: 'raw', inputs: [], selections: [{option_id: 'color', value_id: 'gold'}]}}); expect(api.approveInventoryOpening).not.toHaveBeenCalled(); expect([...host.querySelectorAll('button')].find(b => b.textContent === 'اعتماد المخزون الافتتاحي').disabled).toBe(true); await act(async () => host.querySelector('[type=checkbox]').click()); await click('اعتماد المخزون الافتتاحي'); expect(api.approveInventoryOpening).toHaveBeenCalledWith({...api.previewInventoryOpening.mock.calls[0][0], preview_hash: 'hash'}); expect(host.textContent).toContain('تم اعتماد المخزون');});
test('changing quantity invalidates preview and confirmation', async () => {await render(); await fill(); await click('معاينة المخزون الافتتاحي'); await act(async () => host.querySelector('[type=checkbox]').click()); await change('الكمية — 1', '4'); expect(host.querySelector('[type=checkbox]')).toBeNull(); expect(api.approveInventoryOpening).not.toHaveBeenCalled();});
test('writer disabled allows preview but never approval', async () => {api.inventoryOpeningContext.mockResolvedValue({...metadata, writer_disabled: true}); await render(); await fill(); await click('معاينة المخزون الافتتاحي'); await act(async () => host.querySelector('[type=checkbox]').click()); await click('اعتماد المخزون الافتتاحي'); expect(api.approveInventoryOpening).not.toHaveBeenCalled(); expect(host.textContent).toContain('كاتب المخزون مغلق');});
test('non owner does not fetch catalog or expose write controls', async () => {await render({...context, can_manage_inventory_opening: false, permissions: {manage: true}}); expect(api.inventoryOpeningContext).not.toHaveBeenCalled(); expect(host.querySelector('button')).toBeNull();});
test('existing approval prevents second opening batch', async () => {api.inventoryOpeningContext.mockResolvedValue({...metadata, approved: true}); await render(); expect(host.textContent).toContain('سبق اعتماد'); expect(host.querySelector('button')).toBeNull();});
test('large catalog caps rendered choices and searches beyond first page', async () => {api.inventoryOpeningContext.mockResolvedValue({...metadata, products: Array.from({length: 100}, (_, i) => ({...product, id: `p${i}`, name: `Product ${i}`, sku: `SKU${i}`}))}); await render(); expect(host.querySelectorAll('.op-stock-results button')).toHaveLength(50); await change('بحث في كتالوج', 'SKU99'); expect(host.querySelectorAll('.op-stock-results button')).toHaveLength(1); expect(host.textContent).toContain('Product 99');});
test('preview renders resolved choices and warns fulfillment remains blocked', async () => {await render(); await fill(); await click('معاينة المخزون الافتتاحي'); expect(host.querySelector('tbody').textContent).toContain('color: gold'); expect(host.querySelector('tbody').textContent).toContain('Shelf 1'); expect(host.textContent).toContain('محجوبة عن تنفيذ الطلبات');});
test('writer closes after context and preview blocks approval', async () => {api.previewInventoryOpening.mockResolvedValue({lines: [], preview_hash: 'hash', financial_effect: 'none', writer_disabled: true}); await render(); await fill(); await click('معاينة المخزون الافتتاحي'); await act(async () => host.querySelector('[type=checkbox]').click()); await click('اعتماد المخزون الافتتاحي'); expect(api.approveInventoryOpening).not.toHaveBeenCalled();});
test('failed approval retains exact idempotency payload for retry', async () => {api.approveInventoryOpening.mockRejectedValueOnce(Error('network')); await render(); await fill(); await click('معاينة المخزون الافتتاحي'); await act(async () => host.querySelector('[type=checkbox]').click()); await click('اعتماد المخزون الافتتاحي'); expect(host.textContent).not.toContain('تم اعتماد المخزون'); await click('اعتماد المخزون الافتتاحي'); expect(api.approveInventoryOpening.mock.calls[0][0]).toEqual(api.approveInventoryOpening.mock.calls[1][0]);});
test('invalid quantity is rejected before requesting preview', async () => {await render(); await fill(); await change('الكمية — 1', '1.5'); await click('معاينة المخزون الافتتاحي'); expect(api.previewInventoryOpening).not.toHaveBeenCalled(); expect(host.textContent).toContain('الكمية الصحيحة');});
test('stale preview response cannot cross owner session', async () => {let resolve; api.previewInventoryOpening.mockImplementation(() => new Promise(r => {resolve = r;})); await render(); await fill(); await click('معاينة المخزون الافتتاحي'); await render({...context, session_scope: 'owner-2'}); await act(async () => resolve({lines: [], preview_hash: 'old', financial_effect: 'none', writer_disabled: false})); expect(host.querySelector('[type=checkbox]')).toBeNull(); expect(api.approveInventoryOpening).not.toHaveBeenCalled();});
test('same product can carry independent color and named rows with exact live totals', async () => {
  const chain={...product,options:[{...product.options[0],values:[{id:'gold',name:'ذهبي'},{id:'silver',name:'فضي'}]}]};
  const dress={id:'dress',name:'فستان',sku:'D1',options:[{id:'name',name:'الاسم',type:'text'}]};
  api.inventoryOpeningContext.mockResolvedValue({...metadata,products:[chain,dress]});await render();
  for(const [index,item,q,c,option,value] of [[1,'سلسال · S1','10','7','اللون','gold'],[2,'سلسال · S1','12','10','اللون','silver'],[3,'فستان · D1','20','60','الاسم','عبير'],[4,'فستان · D1','12','60','الاسم','فاطمة']]){
    await click(`إضافة ${item}`);if(index>2)await change(`حالة المنتج — ${index}`,'ready');await change(`${option} — ${index}`,value);await change(`الكمية — ${index}`,q);await change(`تكلفة الوحدة بالريال — ${index}`,c);await change(`موقع التخزين — ${index}`,'l1');
  }
  expect([...host.querySelectorAll('[aria-label^="إجمالي السطر"]')].map(el=>el.textContent)).toEqual(['إجمالي السطر: 70.00 SAR','إجمالي السطر: 120.00 SAR','إجمالي السطر: 1200.00 SAR','إجمالي السطر: 720.00 SAR']);
  expect(host.querySelector('[aria-label="إجمالي تكلفة المدخلات"]').textContent).toContain('2110.00 SAR');
  const labels=[...host.querySelectorAll('.op-card label')].map(el=>el.textContent);expect(labels.findIndex(t=>t.startsWith('اللون — 1'))).toBeLessThan(labels.findIndex(t=>t.startsWith('الكمية — 1')));
  await click('معاينة المخزون الافتتاحي');const submitted=api.previewInventoryOpening.mock.calls[0][0].lines;expect(submitted).toHaveLength(4);expect(submitted[0].purchase_configuration.selections[0].value_id).toBe('gold');expect(submitted[1].purchase_configuration.selections[0].value_id).toBe('silver');expect(submitted[2].purchase_configuration.inputs[0].value).toBe('عبير');expect(submitted[3].purchase_configuration.inputs[0].value).toBe('فاطمة');
  await change('الاسم — 3','سارة');expect(host.querySelector('[type=checkbox]')).toBeNull();
});
test('canonical component supports decimal meters with independent unit totals and exact rounding',async()=>{
  api.inventoryOpeningContext.mockResolvedValue({...metadata,components:[{id:'fabric',kind:'component',name:'قماش',sku:'M2',unit:'m',quantity_precision:6,component_type:'stock_component'}]});
  api.previewInventoryOpening.mockResolvedValue({lines:[{kind:'component',name:'قماش',quantity:2.5,unit:'m',component_type:'stock_component',unit_cost:'8',total_cost:'20.00'}],quantity_totals:[{unit:'m',quantity:2.5},{unit:'piece',quantity:3}],preview_hash:'hash',writer_disabled:false,financial_effect:'none',total_cost:'20.00',currency:'SAR'});
  await render();await change('نوع الصنف','component');await change('بحث في كتالوج','M2');await click('إضافة قماش · M2');await change('الكمية — 1','2.5');await change('تكلفة الوحدة بالريال — 1','8');await change('موقع التخزين — 1','l1');
  expect(host.querySelector('[aria-label="إجمالي السطر 1"]').textContent).toContain('20.00 SAR');expect(host.textContent).toContain('الوحدة: متر (حسب الكتالوج)');expect(host.textContent).not.toContain('حالة المنتج — 1');
  await click('معاينة المخزون الافتتاحي');expect(api.previewInventoryOpening.mock.calls[0][0].lines[0]).toEqual({kind:'component',item_id:'fabric',location_id:'l1',quantity:2.5,unit_cost:'8',purchase_configuration:{state:'raw',selections:[],inputs:[]}});expect(host.textContent).toContain('2.5 متر / 3 قطعة');
  await change('الكمية — 1','0.5');await change('تكلفة الوحدة بالريال — 1','0.01');expect(host.querySelector('[aria-label="إجمالي السطر 1"]').textContent).toContain('0.01 SAR');expect(host.querySelector('[type=checkbox]')).toBeNull();
});
test('images and SKU remain visible and unsafe or failed images have a placeholder',async()=>{
  api.inventoryOpeningContext.mockResolvedValue({...metadata,products:[{...product,image_url:'https://example.test/chain.png'},{id:'bad',name:'Bad',sku:'BAD1',image_url:'javascript:alert(1)'}]});await render();expect(host.querySelector('img').getAttribute('src')).toBe('https://example.test/chain.png');expect(host.querySelector('[aria-label="لا توجد صورة — Bad"]')).not.toBeNull();await click('إضافة سلسال · S1');expect(host.querySelectorAll('img')).toHaveLength(2);await act(async()=>host.querySelector('img').dispatchEvent(new Event('error')));expect(host.querySelector('[aria-label="لا توجد صورة — سلسال"]')).not.toBeNull();await change('بحث في كتالوج','BAD1');expect(host.querySelectorAll('.op-stock-results button')).toHaveLength(1);
});
test('component precision follows catalog and excessive decimals never reach preview',async()=>{
  api.inventoryOpeningContext.mockResolvedValue({...metadata,components:[{id:'fabric',name:'قماش',sku:'M2',unit:'m',quantity_precision:6}]});await render();await change('نوع الصنف','component');await click('إضافة قماش · M2');await change('الكمية — 1','2.1234567');await change('تكلفة الوحدة بالريال — 1','8');await change('موقع التخزين — 1','l1');await click('معاينة المخزون الافتتاحي');expect(api.previewInventoryOpening).not.toHaveBeenCalled();
});

test('search is a compact list with an explicit add button and independent named silver rows', async () => {
  api.inventoryOpeningContext.mockResolvedValue({...metadata, products: [{...product, options: [
    {id:'color', name:'اللون', type:'select', values:[{id:'silver', name:'فضي'}]},
    {id:'name', name:'الاسم', type:'text'}
  ]}]});
  await render();
  await change('بحث في كتالوج', 'S1');
  expect(host.querySelector('[aria-label="نتائج البحث"]')).not.toBeNull();
  expect(host.querySelector('[aria-label="إضافة سلسال · S1"]').textContent).toBe('إضافة المنتج');
  for (const [index, name, quantity] of [[1,'عبير','10'],[2,'فاطمة','15']]) {
    await click('إضافة سلسال · S1');
    await change(`الاسم — ${index}`, name);
    await change(`اللون — ${index}`, 'silver');
    await change(`الكمية — ${index}`, quantity);
    await change(`تكلفة الوحدة بالريال — ${index}`, '7');
    await change(`موقع التخزين — ${index}`, 'l1');
  }
  await click('معاينة المخزون الافتتاحي');
  const rows = api.previewInventoryOpening.mock.calls[0][0].lines;
  expect(rows.map(r=>[r.item_id, r.quantity, r.purchase_configuration.inputs[0].value])).toEqual([['p1',10,'عبير'],['p1',15,'فاطمة']]);
  expect(rows.every(r=>r.purchase_configuration.state==='ready' && r.purchase_configuration.selections[0].value_id==='silver')).toBe(true);
  expect(host.querySelector('[aria-label="إجمالي تكلفة المدخلات"]').textContent).toContain('175.00 SAR');
});

test('missing product option definitions are explained before preview',async()=>{
  api.inventoryOpeningContext.mockResolvedValue({...metadata,products:[{...product,options:[],configuration_issues:['خيارات المنتج غير مكتملة في ميزان 2']}]});
  await render(); await click('إضافة سلسال · S1');
  expect(host.textContent).toContain('خيارات المنتج غير مكتملة في ميزان 2');
});

test('add another row starts fresh while preserving the original customization',async()=>{
  api.inventoryOpeningContext.mockResolvedValue({...metadata,products:[{...product,options:[{id:'name',name:'الاسم',type:'text'}]}]});
  await render(); await click('إضافة سلسال · S1'); await change('الاسم — 1','عبير');
  await change('الكمية — 1','10'); await change('تكلفة الوحدة بالريال — 1','7');
  await click('إضافة سطر آخر من نفس الصنف');
  const input=label=>[...host.querySelectorAll('label')].find(l=>l.textContent.startsWith(label)).querySelector('input');
  expect(input('الاسم — 1').value).toBe('عبير'); expect(input('الاسم — 2').value).toBe('');
  expect(input('الكمية — 2').value).toBe('1'); expect(input('تكلفة الوحدة بالريال — 2').value).toBe('');
  await change('الاسم — 2','فاطمة'); await click('إزالة السطر 1');
  expect(input('الاسم — 1').value).toBe('فاطمة');
  await change('حالة المنتج — 1','raw');expect(input('الاسم — 1').disabled).toBe(true);expect(input('الاسم — 1').value).toBe('');
});
