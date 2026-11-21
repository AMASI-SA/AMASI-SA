import React,{act} from 'react';
import {createRoot} from 'react-dom/client';
import WarehouseHierarchyWorkspace,{cabinetLocationLabel,compactWarehouseLocation} from './WarehouseHierarchyWorkspace';
import api from '../lib/api';

jest.mock('../lib/api',()=>({__esModule:true,default:{get:jest.fn(),post:jest.fn()}}));
jest.mock('sonner',()=>({toast:{error:jest.fn(),success:jest.fn(),warning:jest.fn()}}));
jest.mock('../components/inventory/suite/InventorySuiteFrame',()=>({
  __esModule:true,default:({children})=><div>{children}</div>,
  InventoryPageHeader:({title,description,children})=><header><h1>{title}</h1><p>{description}</p>{children}</header>,
  InventorySheet:({open,title,description,children,footer,onClose})=>open?<section role="dialog" aria-label={title}><h2>{title}</h2><p>{description}</p><button type="button" aria-label="إغلاق" onClick={onClose}>إغلاق</button>{children}{footer}</section>:null,
}));

let host,root,branches,sections,locationRows;
const cabinet={id:'cab-1',name:'دولاب الملابس',cabinet_number:1,code:'S01-C01',length:4,width:6,total_locations:24,purpose:'permanent_storage',location_numbering:'cabinet-row-slot'};
const catalog={default_country:'السعودية',default_city:'الرياض',countries:[{name:'السعودية',default_city:'الرياض',cities:['الرياض','جدة']},{name:'الإمارات',default_city:'دبي',cities:['دبي','أبوظبي']}]};
const buttons=(text,scope=host)=>[...scope.querySelectorAll('button')].filter(button=>button.textContent.includes(text));
const button=(text,scope=host)=>buttons(text,scope)[0];
const field=(label,scope=host)=>[...scope.querySelectorAll('label')].find(node=>node.firstElementChild?.textContent===label)?.querySelector('input,select');
async function change(node,value){await act(async()=>{const prototype=node.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(prototype,'value').set.call(node,value);node.dispatchEvent(new Event(node.tagName==='SELECT'?'change':'input',{bubbles:true}));});}
async function click(node){await act(async()=>node.click());}
async function submit(id){await act(async()=>host.querySelector('#'+id).dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));}
async function render(mode=null){await act(async()=>root.render(<WarehouseHierarchyWorkspace accessMode={mode}/>));}

beforeEach(()=>{
  global.IS_REACT_ACT_ENVIRONMENT=true;host=document.createElement('div');document.body.appendChild(host);root=createRoot(host);
  branches=[{id:'wh-1',name:'فرع الرياض',code:'WH01',city:'الرياض',country:'السعودية',location_numbering:'cabinet-row-slot'}];
  sections=[{id:'sec-1',warehouse_id:'wh-1',name:'قسم التخزين',code:'S01',section_number:1,capabilities:['cabinets'],cabinet_count:1,cabinets:[{...cabinet}]}];
  locationRows=Array.from({length:24},(_,index)=>({id:'loc-'+index,number:index+1,row:Math.floor(index/6)+1,column:index%6+1,code:'WH01-S01-C01-'+String(index+1).padStart(3,'0'),barcode_value:'WH01-S01-C01-'+String(index+1).padStart(3,'0'),state:index===16?'disabled':index===8?'occupied':'empty',occupancy:index===8?{items:[{product_id:'product-real-1',product_name:'بلوفر من الخدمة',sku:'NATIVE-SKU-14',quantity:2,unit:'piece',preparation_state:'ready_complete'}]}:null}));
  api.get.mockImplementation(async(path)=>({data:path==='/warehouse-locations/warehouses'?{items:branches}:path==='/warehouse-locations-v2/catalog'?catalog:path==='/warehouse-locations-v2/section-capabilities'?{items:[{value:'cabinets',label:'دواليب وخانات'},{value:'quality_control',label:'فحص جودة'}],recommended:{storage:['cabinets'],shipping:['cabinets','packing','shipping_labeling']}}:path==='/warehouse-locations/locations'?{items:locationRows}:path.endsWith('/sections')?{items:sections}:{} }));
  api.post.mockReset();
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();jest.clearAllMocks();});

test('compact shelf/bin presentation does not alter native location and scanner identity',()=>{
  expect(cabinetLocationLabel(cabinet,8)).toBe('1-2-3');expect(compactWarehouseLocation(cabinet,8)).toBe('د1 / ر2 / خ3');
  expect(cabinetLocationLabel({...cabinet,location_numbering:'legacy'},8)).toBe('009');expect(compactWarehouseLocation({...cabinet,location_numbering:'legacy'},8)).toBe('009');
});

test('real hierarchy and occupancy readers drive selectable compact map, search, list and details',async()=>{
  await render();expect(api.get).toHaveBeenCalledWith('/warehouse-locations-v2/warehouses/wh-1/sections');expect(api.get).toHaveBeenCalledWith('/warehouse-locations/locations',{params:{cabinet_id:'cab-1',limit:1000}});
  expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(24);expect(host.querySelectorAll('.mz-wh-shelf')).toHaveLength(4);
  await change(host.querySelector('[aria-label="بحث خانات المستودع"]'),'NATIVE-SKU-14');expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(1);await click(host.querySelector('.mz-wh-bin'));
  expect(host.querySelector('[aria-label="تفاصيل الخانة المحددة"]').textContent).toContain('د1 / ر2 / خ3');expect(host.textContent).toContain('بلوفر من الخدمة');expect(host.textContent).toContain('WH01-S01-C01-009');
  await click(button('قائمة'));expect(host.querySelectorAll('.mz-wh-bin-list button')).toHaveLength(1);expect(api.post).not.toHaveBeenCalled();
});

test('read-only delegate does not request manager occupancy or expose native writers and never marks unknown bins empty',async()=>{
  await render('read');expect(api.get.mock.calls.some(([path])=>path==='/warehouse-locations/locations')).toBe(false);expect(host.querySelectorAll('.mz-wh-bin.is-unknown')).toHaveLength(24);expect(host.querySelectorAll('.mz-wh-bin.is-empty')).toHaveLength(0);expect(buttons('إضافة')).toHaveLength(0);expect(buttons('فرع جديد')).toHaveLength(0);expect(host.textContent).toContain('الإشغال غير مثبت');expect(api.post).not.toHaveBeenCalled();
});

test('country/city dependency and native branch creation payload remain intact',async()=>{
  await render();await click(button('فرع جديد'));await change(field('اسم الفرع'),'فرع الإمارات');await change(field('الدولة'),'الإمارات');expect(field('المدينة').value).toBe('دبي');await change(field('الحي'),'الحي');await change(field('الشارع'),'الشارع');
  api.post.mockImplementation(async(path,payload)=>{branches=[...branches,{...payload,id:'wh-new',code:'WH02'}];sections=[];return {data:{id:'wh-new'}};});await submit('warehouse-branch-form');expect(api.post).toHaveBeenCalledWith('/warehouse-locations-v2/warehouses',{name:'فرع الإمارات',country:'الإمارات',city:'دبي',district:'الحي',street:'الشارع',is_primary:false,location_numbering:'cabinet-row-slot',inventory_scope:'standalone'});expect(host.querySelector('[role="dialog"]')).toBeNull();
});

test('section templates preserve mandatory cabinets and canonical capability payload',async()=>{
  await render();await click(button('قسم جديد'));await change(field('اسم القسم'),'قسم شحن');await change(field('قالب سريع'),'shipping');const fixed=[...host.querySelectorAll('.mz-wh-capability-options input')][0];expect(fixed.disabled).toBe(true);expect(fixed.checked).toBe(true);
  api.post.mockResolvedValue({data:{id:'sec-1'}});await submit('warehouse-section-form');expect(api.post).toHaveBeenCalledWith('/warehouse-locations-v2/sections',{warehouse_id:'wh-1',name:'قسم شحن',capabilities:['cabinets','packing','shipping_labeling'],notes:null});
});

test('cabinet form validates capacity and preserves native dimensions, purpose, limits and generated result',async()=>{
  await render();await click(button('إضافة دولاب'));await change(field('عدد الرفوف'),'2');await change(field('خانات لكل رف'),'3');await change(field('سعة كل خانة (اختياري)'),'1.5');await submit('warehouse-cabinet-form');expect(api.post).not.toHaveBeenCalled();expect(host.querySelector('[role="alert"]').textContent).toContain('عدد صحيح');await change(field('سعة كل خانة (اختياري)'),'12');await change(field('اسم الدولاب (اختياري)'),'دولاب جديد');
  api.post.mockImplementation(async()=>{sections=[{...sections[0],cabinets:[...sections[0].cabinets,{...cabinet,id:'cab-new',name:'دولاب جديد',cabinet_number:2,length:2,width:3,total_locations:6}]}];locationRows=[];return {data:{cabinet:{id:'cab-new'},locations_created:6}};});await submit('warehouse-cabinet-form');expect(api.post).toHaveBeenCalledWith('/warehouse-locations-v2/sections/sec-1/cabinets',{cabinet_name:'دولاب جديد',length:2,width:3,purpose:'permanent_storage',max_items_per_location:12});expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(6);expect(host.querySelector('[aria-label="تفاصيل الخانة المحددة"]').textContent).toContain('د2 / ر1 / خ1');
});

test('native Code39 print pack keeps immutable barcode codes',async()=>{
  await render('read');const write=jest.fn(),close=jest.fn(),open=jest.spyOn(window,'open').mockReturnValue({document:{write,close}});try{await click(button('طباعة ملصقات الدولاب'));expect(write).toHaveBeenCalledTimes(1);const html=write.mock.calls[0][0];expect(html).toContain('WH01-S01-C01-009');expect(html).toContain('<svg class="barcode"');expect(html).toContain('1-2-3');expect(close).toHaveBeenCalledTimes(1);expect(api.post).not.toHaveBeenCalled();}finally{open.mockRestore();}
});

test('failed occupancy reader leaves unknown bins and visible error rather than inventing empty stock',async()=>{
  const original=api.get.getMockImplementation();api.get.mockImplementation((path,...args)=>path==='/warehouse-locations/locations'?Promise.reject(Error('offline')):original(path,...args));await render();expect(host.querySelectorAll('.mz-wh-bin.is-unknown')).toHaveLength(24);expect(host.querySelectorAll('.mz-wh-bin.is-empty')).toHaveLength(0);expect(host.textContent).toContain('تعذر قراءة إشغال الخانات');expect(api.post).not.toHaveBeenCalled();
});

test('hierarchy failure remains an error, offers retry and does not suggest an empty warehouse',async()=>{
  const original=api.get.getMockImplementation();api.get.mockImplementation((path,...args)=>path==='/warehouse-locations/warehouses'?Promise.reject(Error('offline')):original(path,...args));await render();expect(host.textContent).toContain('تعذر تحميل الفروع');expect(host.textContent).toContain('أعد القراءة قبل إنشاء');expect(button('إضافة فرع').disabled).toBe(true);api.get.mockImplementation(original);await click(button('إعادة المحاولة'));expect(host.querySelectorAll('.mz-wh-bin')).toHaveLength(24);expect(api.post).not.toHaveBeenCalled();
});
