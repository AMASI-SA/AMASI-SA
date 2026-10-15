import React, {useEffect, useState} from 'react';
import {operationalApi as api, messageFor} from './api';

const states = {requires_preparation:'خام غير مخصص بالكامل', ready_complete:'جاهز', standard:'عادي'};
const conditions = {sellable:'صالح', available:'صالح', good:'صالح', quarantined:'بانتظار الفحص', quarantine:'بانتظار الفحص', pending_inspection:'بانتظار الفحص', damaged:'تالف', held:'موقوف', unknown:'غير محدد'};
const display = value => value == null ? 'غير متاح' : String(value);
export const specificationText = specifications => Object.entries(specifications || {}).map(([name,value])=>`${name}: ${value}`).join(' · ') || 'بدون تخصيص';

export default function InventoryAvailability({context}) {
  const allowed = context.permissions?.reports === true;
  const [result,setResult]=useState(null), [error,setError]=useState(''), [loading,setLoading]=useState(false);
  const [version,setVersion]=useState(0), [search,setSearch]=useState(''), [kind,setKind]=useState('');
  useEffect(()=>{
    let alive=true;
    setResult(null);setError('');
    if(!allowed) return ()=>{alive=false;};
    setLoading(true);
    api.inventoryAvailability().then(data=>{
      if(!Array.isArray(data?.items)||data.schema_version!==1||data.read_only!==true||data.source!=='warehouse_locations') throw Error('invalid_inventory_response');
      if(alive)setResult(data);
    }).catch(e=>{if(alive)setError(messageFor(e));}).finally(()=>{if(alive)setLoading(false);});
    return ()=>{alive=false;};
  },[allowed,context.session_scope,version]);
  useEffect(()=>{const refresh=()=>setVersion(v=>v+1);window.addEventListener('focus',refresh);return()=>window.removeEventListener('focus',refresh);},[]);
  if(!allowed)return <p role="alert">لا تتوفر صلاحية عرض المخزون.</p>;
  const items=(result?.items||[]).filter(row=>(!kind||(row.resource_id?'component':'product')===kind)&&`${row.name} ${row.sku||''} ${specificationText(row.specifications)} ${row.location_code||''}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <section dir="rtl" aria-label="مخزون ميزان"><h2>مخزون ميزان</h2>
    <p>الكميات الفعلية في مواقع التخزين، بعد طرح المحجوز من الصالح للبيع. إجمالي المشتريات يظهر في تقرير مستقل.</p>
    <div className="op-segment"><label className="op-field">بحث في المخزون<input value={search} onChange={e=>setSearch(e.target.value)}/></label><button type="button" aria-label="تحديث المخزون" disabled={loading} onClick={()=>setVersion(v=>v+1)}>↻</button></div>
    <label className="op-field">نوع المخزون<select value={kind} onChange={e=>setKind(e.target.value)}><option value="">الكل</option><option value="product">منتجات</option><option value="component">مكونات</option></select></label>
    {loading&&<p role="status">جارٍ قراءة مخزون ميزان…</p>}
    {error&&<p role="alert">{error}</p>}
    {!!result?.warnings?.length&&<p role="alert">توجد بيانات مخزنية تحتاج مراجعة. الكميات غير المثبتة لا تُعرض كمتاحة للبيع.</p>}
    {!loading&&!error&&result&&(!items.length?<p>لا توجد كمية مخزنية خاصة بنظام ميزان لهذا المنتج أو البحث.</p>:<div className="op-table"><table><caption>الأرصدة حسب المنتج والخيارات والموقع</caption><thead><tr>{['المنتج أو المكوّن','الخيارات والتخصيص','الحالة','الموقع','الفعلية','المحجوزة','المتاحة','الموقوفة','الوحدة','تكلفة الوحدة','قيمة المخزون'].map(label=><th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{items.map(row=><tr key={row.id}>
      <th scope="row">{row.name}</th><td>{specificationText(row.specifications)}</td><td>{states[row.preparation_state]||'غير محدد'} · {conditions[row.condition]||'غير محدد'}{!!row.availability_issues?.length&&<span> · يحتاج مراجعة</span>}</td><td>{row.location_code||row.location_name||'غير محدد'}</td><td>{display(row.physical)}</td><td>{display(row.reserved)}</td><td>{display(row.available)}</td><td>{display(row.held)}</td><td>{row.unit==='piece'?'قطعة':row.unit||'غير محددة'}</td><td>{display(row.unit_cost)}</td><td>{display(row.inventory_value)}</td>
    </tr>)}</tbody></table></div>)}
  </section>;
}
