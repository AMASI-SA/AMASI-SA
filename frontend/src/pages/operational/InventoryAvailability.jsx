import React, {useEffect, useRef, useState} from 'react';
import {operationalApi as api, messageFor} from './api';
import {visibleOperationalRead} from './visibleOperationalRead';
import { ArrowRight, ArrowsClockwise, Cube, MagnifyingGlass, Plus, WarningCircle } from '@phosphor-icons/react';
import { InventoryHomeProjection, InventoryReportsProjection, stockSpecifications } from '../../components/inventory/suite/InventoryProjectionViews';
import '../../components/inventory/suite/inventoryProjectionViews.css';

const states = {requires_preparation:'خام غير مخصص بالكامل', ready_complete:'جاهز', standard:'عادي'};
const conditions = {sellable:'صالح', available:'صالح', good:'صالح', quarantined:'بانتظار الفحص', quarantine:'بانتظار الفحص', pending_inspection:'بانتظار الفحص', damaged:'تالف', held:'موقوف', unknown:'غير محدد'};
const display = value => value == null ? 'غير متاح' : String(value);
export const specificationText = specifications => Object.entries(specifications || {}).map(([name,value])=>`${name}: ${value}`).join(' · ') || 'بدون تخصيص';

export default function InventoryAvailability({context,onOpeningInventory,view='stock',onNavigate,initialSelectedId=''}) {
  const allowed = context.permissions?.reports === true;
  const [result,setResult]=useState(null), [error,setError]=useState(''), [loading,setLoading]=useState(false);
  const [version,setVersion]=useState(0), [search,setSearch]=useState(''), [kind,setKind]=useState('');
  const [warehouse,setWarehouse]=useState(''), [condition,setCondition]=useState('');
  const [selectedId,setSelectedId]=useState(initialSelectedId), [mobileDetail,setMobileDetail]=useState(Boolean(initialSelectedId));
  const readLane=useRef({});
  useEffect(()=>{
    setResult(null);setError('');
    if(!allowed){setLoading(false);return;}
    return visibleOperationalRead(async config=>{
      const data=await api.inventoryAvailability(config);
      if(!Array.isArray(data?.items)||data.schema_version!==1||data.read_only!==true||data.source!=='warehouse_locations') throw Error('invalid_inventory_response');
      return data;
    },{onStart:()=>setLoading(true),onData:setResult,onError:e=>setError(messageFor(e)),onEnd:()=>setLoading(false)},readLane.current);
  },[allowed,context.session_scope,version]);
  if(!allowed)return <p role="alert">لا تتوفر صلاحية عرض المخزون.</p>;
  const allItems=result?.items||[];
  const items=allItems.filter(row=>(!kind||(row.resource_id?'component':'product')===kind)
    &&(!warehouse||String(row.warehouse_id||row.warehouse_name||'unknown')===warehouse)
    &&(!condition||row.condition===condition)
    &&[row.name,row.sku||'',specificationText(row.specifications),row.location_code||'',row.warehouse_name||''].join(' ').toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  const operationalCosts=items.some(row=>row.operational_unit_cost!=null||row.operational_inventory_value!=null);
  const selected=items.find(row=>row.id===selectedId)||items[0]||null;
  const warehouses=[...new Map(allItems.map(row=>[String(row.warehouse_id||row.warehouse_name||'unknown'),row.warehouse_name||'مستودع غير محدد'])).entries()];
  const availableConditions=[...new Set(allItems.map(row=>row.condition).filter(Boolean))];
  function inspect(row){setSelectedId(row.id);setMobileDetail(true);}
  const title=view==='home'?'مركز العمل اليومي':view==='reports'?'تقارير المخزون':'الأصناف والمخزون';
  const description=view==='home'?'الأرصدة المهمة وأعمال المراجعة، في مكان واحد.':view==='reports'?'أرصدة المستودعات وقيمها حسب وحدة القياس، من القراءة الفعلية.':'الكميات الفعلية في مواقع التخزين، بعد طرح المحجوز من الصالح للبيع. إجمالي المشتريات يظهر في تقرير مستقل.';
  return <section dir="rtl" className="mz-stock-workspace" aria-label="مخزون ميزان">
    <header className="mz-stock-header"><div><span>ميزان 2 · المخزون</span><h1>{title}</h1><p>{description}</p></div><div className="mz-stock-header-actions">
      <button type="button" aria-label="تحديث المخزون" disabled={loading} onClick={()=>setVersion(v=>v+1)}><ArrowsClockwise size={17} className={loading?'animate-spin':''}/>تحديث</button>
      {context.can_manage_inventory_opening === true && onOpeningInventory && <button type="button" className="is-primary" onClick={onOpeningInventory}><Plus size={18}/>إدخال المخزون الافتتاحي</button>}
    </div></header>
    {view==='stock'&&<div className="mz-stock-filters"><label className="mz-stock-search"><MagnifyingGlass size={20}/><span className="sr-only">بحث في المخزون</span><input aria-label="بحث في المخزون" placeholder="الصنف، الرقم المخزني، المواصفات أو الخانة…" value={search} onChange={e=>{setSearch(e.target.value);setMobileDetail(false);}}/></label>
      <label>نوع المخزون<select value={kind} onChange={e=>{setKind(e.target.value);setMobileDetail(false);}}><option value="">الكل</option><option value="product">منتجات</option><option value="component">مكونات</option></select></label>
      <label>المستودع<select value={warehouse} onChange={e=>{setWarehouse(e.target.value);setMobileDetail(false);}}><option value="">كل المستودعات</option>{warehouses.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
      <label>حالة المخزون<select value={condition} onChange={e=>{setCondition(e.target.value);setMobileDetail(false);}}><option value="">كل الحالات</option>{availableConditions.map(value=><option key={value} value={value}>{conditions[value]||'غير محدد'}</option>)}</select></label>
    </div>}
    {loading&&<div className="mz-stock-empty" role="status"><ArrowsClockwise size={29} className="animate-spin"/>جارٍ قراءة مخزون ميزان…</div>}
    {error&&<p role="alert" className="mz-stock-notice is-error"><WarningCircle size={21}/>{error}</p>}
    {!!result?.warnings?.length&&<p role="alert" className="mz-stock-notice is-warning"><WarningCircle size={20}/>توجد بيانات مخزنية تحتاج مراجعة. الكميات غير المثبتة لا تُعرض كمتاحة للبيع.</p>}
    {operationalCosts&&<p className="mz-stock-notice">تكلفة الافتتاحية التشغيلية مستقلة عن التقييم المحاسبي. الكميات الموقوفة غير متاحة لتنفيذ الطلبات حتى اكتمال مطابقة التقييم.</p>}
    {!loading&&!error&&result&&(view==='home'?<InventoryHomeProjection result={result} context={context} onNavigate={onNavigate}/>:view==='reports'?<InventoryReportsProjection result={result}/>:!items.length?<div className="mz-stock-surface mz-stock-empty"><Cube size={43}/><h2>لا توجد كمية مخزنية خاصة بنظام ميزان لهذا المنتج أو البحث.</h2><p>غيّر البحث أو الفلاتر لعرض الأرصدة المتاحة ضمن نطاقك.</p></div>:<div className="mz-stock-layout">
      <section className="mz-stock-surface mz-stock-list" data-mobile-hidden={mobileDetail}><header className="mz-stock-section-heading"><h2>الأرصدة الفعلية <span className="mz-stock-count">{items.length}</span></h2><span>حسب الصنف والخيارات والموقع</span></header><div className="mz-stock-table-wrap"><table className="mz-stock-table mz-stock-detail-table"><caption className="sr-only">الأرصدة حسب المنتج والخيارات والموقع</caption><thead><tr>{['المنتج أو المكوّن','الخيارات والتخصيص','الحالة','الموقع','الفعلية','المحجوزة','المتاحة','الموقوفة','الوحدة','تكلفة الوحدة','قيمة المخزون',...(operationalCosts?['تكلفة الوحدة التشغيلية','قيمة الافتتاحية التشغيلية']:[])].map((label,index)=><th scope="col" key={label} data-column={index}>{label}</th>)}</tr></thead><tbody>{items.map(row=><tr key={row.id} className={selected?.id===row.id?'is-selected':''} onClick={()=>inspect(row)}>
        <th scope="row"><button type="button" aria-label={'عرض مخزون '+row.name} onClick={event=>{event.stopPropagation();inspect(row);}} className="mz-stock-item"><span className="mz-stock-item-icon">{row.main_image?<img src={row.main_image} alt=""/>:<Cube size={23}/>}</span><span><strong>{row.name}</strong><small><bdi>{row.sku|| (row.resource_id?'مكوّن مخزني':'بدون رقم مخزني')}</bdi></small></span></button></th>
        <td data-column="1">{specificationText(row.specifications)}</td><td data-column="2">{states[row.preparation_state]||'غير محدد'} · {conditions[row.condition]||'غير محدد'}{!!row.availability_issues?.length&&<span> · يحتاج مراجعة</span>}</td><td data-column="3">{row.location_code||row.location_name||'غير محدد'}{row.warehouse_name ? ' — '+row.warehouse_name : ''}</td><td data-column="4">{display(row.physical)}</td><td data-column="5">{display(row.reserved)}</td><td data-column="6" className="mz-stock-available">{display(row.available)}</td><td data-column="7">{display(row.held)}</td><td data-column="8">{row.unit==='piece'?'قطعة':row.unit||'غير محددة'}</td><td data-column="9">{display(row.unit_cost)}</td><td data-column="10">{display(row.inventory_value)}</td>
        {operationalCosts&&<><td data-column="11">{display(row.operational_unit_cost)}{row.operational_unit_cost!=null?' '+(row.operational_currency||''):''}</td><td data-column="12">{display(row.operational_inventory_value)}{row.operational_inventory_value!=null?' '+(row.operational_currency||''):''}</td></>}
      </tr>)}</tbody></table></div><footer className="mz-stock-panel-footer"><span>{items.length} سجلًا ضمن الفلاتر · الأرصدة غير المحسومة تبقى غير متاحة.</span></footer></section>
      <aside className="mz-stock-surface mz-stock-inspector" data-mobile-hidden={!mobileDetail} aria-label="تفاصيل الرصيد">
        <button type="button" className="mz-stock-mobile-back" onClick={()=>setMobileDetail(false)}><ArrowRight size={18}/>العودة إلى المخزون</button>
        {selected&&<><div className="mz-stock-selected-header"><span className="mz-stock-item-icon">{selected.main_image?<img src={selected.main_image} alt=""/>:<Cube size={33}/>}</span><span><small>{selected.resource_id?'مكوّن مخزني':'منتج مخزني'}</small><h2>{selected.name}</h2><bdi>{selected.sku||'بدون رقم مخزني'}</bdi></span></div>
          <div className="mz-stock-quantity-grid">{[['الفعلية',selected.physical],['المحجوزة',selected.reserved],['المتاحة',selected.available],['الموقوفة',selected.held]].map(([label,value])=><div key={label}><small>{label}</small><strong>{display(value)}</strong></div>)}</div>
          <div className="mz-stock-inspector-body"><section><h3>الموقع والحالة</h3><dl><div><dt>المستودع</dt><dd>{selected.warehouse_name||'غير محدد'}</dd></div><div><dt>الخانة</dt><dd><bdi>{selected.location_code||selected.location_name||'غير محدد'}</bdi></dd></div><div><dt>التجهيز</dt><dd>{states[selected.preparation_state]||'غير محدد'}</dd></div><div><dt>الحالة</dt><dd>{conditions[selected.condition]||'غير محدد'}</dd></div><div><dt>الوحدة</dt><dd>{selected.unit==='piece'?'قطعة':selected.unit||'غير محددة'}</dd></div></dl></section>
          <section><h3>المواصفات والتخصيص</h3><p>{stockSpecifications(selected.specifications)}</p></section>
          <section><h3>تقييم المخزون</h3><dl><div><dt>تكلفة الوحدة</dt><dd>{display(selected.unit_cost)}</dd></div><div><dt>قيمة المخزون</dt><dd>{display(selected.inventory_value)}</dd></div></dl><p>القيمة المجهولة ليست صفرًا.</p></section>
          {(selected.operational_unit_cost!=null||selected.operational_inventory_value!=null)&&<section><h3>الافتتاحية التشغيلية</h3><dl><div><dt>تكلفة الوحدة التشغيلية</dt><dd>{display(selected.operational_unit_cost)} {selected.operational_currency||''}</dd></div><div><dt>قيمة الافتتاحية التشغيلية</dt><dd>{display(selected.operational_inventory_value)} {selected.operational_currency||''}</dd></div></dl></section>}
          {!!selected.availability_issues?.length&&<div className="mz-stock-notice is-warning"><WarningCircle size={20}/><span>الإتاحة تحتاج مراجعة. لا يُفترض توفر كمية غير مثبتة.</span></div>}
          </div></>}
      </aside>
    </div>)}
    {result?.observed_at&&!loading&&!error&&<p className="mz-stock-observed">وقت القراءة: <bdi>{result.observed_at}</bdi></p>}
  </section>;
}
