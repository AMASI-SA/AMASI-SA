import {useEffect,useRef,useState} from "react";
import {ArrowsClockwise,ArrowsLeftRight,MagnifyingGlass,WarningCircle} from "@phosphor-icons/react";
import {visibleOperationalRead} from "../../../pages/operational/visibleOperationalRead";
import {messageFor} from "../../../pages/operational/api";
import {InventorySheet} from "./InventorySuiteFrame";
import {inventoryDisplay,inventoryUnit} from "./InventoryProjectionViews";
import "./inventoryProjectionViews.css";
import "./inventoryMovements.css";

const directionLabel=value=>({in:"وارد",out:"صادر",increase:"وارد",decrease:"صادر"}[value]||"غير محدد");
const sourceLabel=value=>({purchase_invoice:"فاتورة شراء",opening_inventory:"مخزون افتتاحي",stock_preparation:"تجهيز مخزون",stock_preparation_order:"تجهيز مخزون",component_consumption:"استهلاك مكوّن",product_consumption:"صرف منتج",inventory_adjustment:"تسوية جرد",inventory_receipt:"استلام مخزون"}[value]||"حركة مخزنية");
const dateLabel=value=>{const date=value?new Date(value):null;return date&&!Number.isNaN(date.getTime())?date.toLocaleString("ar-SA-u-ca-gregory",{dateStyle:"medium",timeStyle:"short",timeZone:"Asia/Riyadh"}):"تاريخ غير متاح";};
const dateKey=value=>{const date=value?new Date(value):null;return date&&!Number.isNaN(date.getTime())?new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Riyadh",year:"numeric",month:"2-digit",day:"2-digit"}).format(date):"";};

export default function InventoryMovementsView({allowed=false,scope,loadMovements}) {
  const [result,setResult]=useState(null),[error,setError]=useState(""),[loading,setLoading]=useState(false),[version,setVersion]=useState(0);
  const [query,setQuery]=useState(""),[direction,setDirection]=useState(""),[warehouse,setWarehouse]=useState(""),[from,setFrom]=useState(""),[to,setTo]=useState("");
  const [selected,setSelected]=useState(null);
  const lane=useRef({});
  useEffect(()=>{
    setResult(null);setError("");setSelected(null);
    if(!allowed){setLoading(false);return;}
    return visibleOperationalRead(async config=>{
      const data=await loadMovements(config);
      if(data?.schema_version!=="g47-stocktake-v1"||data.read_only!==true||!Array.isArray(data.items)||typeof data.window_complete!=="boolean")throw Error("invalid_inventory_movements");
      return data;
    },{onStart:()=>setLoading(true),onData:setResult,onError:reason=>setError(messageFor(reason)),onEnd:()=>setLoading(false)},lane.current);
  },[allowed,scope,loadMovements,version]);
  if(!allowed)return <p role="alert" className="mz-stock-notice is-warning">لا تتوفر صلاحية عرض حركة المخزون.</p>;
  const items=(result?.items||[]).filter(row=>(!direction||row.direction===direction)
    &&(!warehouse||String(row.warehouse_id||row.warehouse_name||"unknown")===warehouse)
    &&(!from||(dateKey(row.occurred_at)&&dateKey(row.occurred_at)>=from))
    &&(!to||(dateKey(row.occurred_at)&&dateKey(row.occurred_at)<=to))
    &&[row.name,row.sku,row.source_id,row.location_code,row.warehouse_name].join(" ").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  const warehouses=[...new Map((result?.items||[]).map(row=>[String(row.warehouse_id||row.warehouse_name||"unknown"),row.warehouse_name||"مستودع غير محدد"])).entries()];
  const directions=[...new Set((result?.items||[]).map(row=>row.direction).filter(Boolean))];
  return <section className="mz-inventory-movements" dir="rtl" data-testid="native-inventory-movements">
    <header className="mz-stock-header"><div><span>ميزان 2 · المخزون</span><h1>حركة المخزون</h1><p>الاستلام والصرف والتسويات المثبتة في المصادر الحالية.</p></div><div className="mz-stock-header-actions"><button type="button" disabled={loading} onClick={()=>setVersion(value=>value+1)}><ArrowsClockwise size={18} className={loading?"animate-spin":""}/>تحديث الحركات</button></div></header>
    <div className="mz-stock-filters"><label className="mz-stock-search"><MagnifyingGlass size={20}/><input aria-label="بحث حركة المخزون" placeholder="الصنف أو الرقم المخزني أو مرجع الحركة…" value={query} onChange={event=>setQuery(event.target.value)}/></label>
      <label>الحركة<select aria-label="اتجاه حركة المخزون" value={direction} onChange={event=>setDirection(event.target.value)}><option value="">كل الحركات</option>{directions.map(value=><option key={value} value={value}>{directionLabel(value)}</option>)}</select></label>
      <label>المستودع<select aria-label="مستودع الحركة" value={warehouse} onChange={event=>setWarehouse(event.target.value)}><option value="">كل المستودعات</option>{warehouses.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
      <label>من<input aria-label="الحركات من تاريخ" type="date" value={from} onChange={event=>setFrom(event.target.value)}/></label><label>إلى<input aria-label="الحركات إلى تاريخ" type="date" value={to} onChange={event=>setTo(event.target.value)}/></label>
    </div>
    {from&&to&&from>to&&<p role="alert" className="mz-stock-notice is-warning">تاريخ البداية يجب أن يسبق تاريخ النهاية.</p>}
    {loading&&<div role="status" className="mz-stock-empty"><ArrowsClockwise size={30} className="animate-spin"/>جارٍ قراءة الحركات…</div>}
    {error&&<p role="alert" className="mz-stock-notice is-error"><WarningCircle size={20}/>{error}</p>}
    {!loading&&!error&&result&&<>
      {!result.window_complete&&<p role="alert" className="mz-stock-notice is-warning"><WarningCircle size={20}/>نافذة الحركات غير مكتملة. القائمة المعروضة لا تمثل سجل المخزون الكامل.</p>}
      {!!result.warnings?.length&&<p role="alert" className="mz-stock-notice is-warning"><WarningCircle size={20}/>بعض مصادر الحركة تحتاج مراجعة؛ تظهر البيانات المثبتة المتاحة فقط.</p>}
      <section className="mz-stock-surface"><header className="mz-stock-section-heading"><h2>الحركات المثبتة <span className="mz-stock-count">{items.length}</span></h2><span>التكلفة المجهولة تبقى غير متاحة.</span></header>
        {!items.length?<div className="mz-stock-empty"><ArrowsLeftRight size={40}/><h3>لا توجد حركات تطابق العرض</h3><p>غيّر البحث أو نطاق التاريخ لعرض الحركات المقروءة.</p></div>:<div className="mz-stock-table-wrap"><table className="mz-stock-table mz-movements-table"><thead><tr><th>الصنف</th><th>التاريخ (الرياض)</th><th>المصدر</th><th>الموقع</th><th>الحركة</th><th>الكمية</th><th>الوحدة</th><th>تكلفة الوحدة</th><th>القيمة</th></tr></thead><tbody>{items.map(row=><tr key={row.id} onClick={()=>setSelected(row)}><th scope="row"><button type="button" className="mz-movement-product" onClick={event=>{event.stopPropagation();setSelected(row);}} aria-label={"عرض حركة "+row.name}><strong>{row.name||"صنف مخزني"}</strong><small><bdi>{row.sku|| (row.kind==="component"?"مكوّن مخزني":"بدون رقم مخزني")}</bdi></small></button></th><td>{dateLabel(row.occurred_at)}</td><td>{sourceLabel(row.source_type)}<small><bdi>{row.source_id||"مرجع غير متاح"}</bdi></small></td><td>{row.warehouse_name||"مستودع غير محدد"}<small><bdi>{row.location_code||"خانة غير محددة"}</bdi></small></td><td><span className={"mz-stock-badge "+(["in","increase"].includes(row.direction)?"is-ready":"is-warning")}>{directionLabel(row.direction)}</span></td><td>{inventoryDisplay(row.quantity)}</td><td>{inventoryUnit(row.unit)}</td><td>{inventoryDisplay(row.unit_cost)}</td><td>{inventoryDisplay(row.value)}</td></tr>)}</tbody></table></div>}
      </section>
    </>}
    <InventorySheet open={Boolean(selected)} onClose={()=>setSelected(null)} title={selected?.name||"تفاصيل حركة المخزون"} description="تفاصيل الحركة المثبتة من مصدرها.">
      {selected&&<div className="mz-movement-detail"><span className="mz-stock-badge is-ready">{directionLabel(selected.direction)}</span><dl>{[["الرقم المخزني",selected.sku||"غير متاح"],["التاريخ",dateLabel(selected.occurred_at)],["المصدر",sourceLabel(selected.source_type)],["مرجع المصدر",selected.source_id||"غير متاح"],["المستودع",selected.warehouse_name||"غير محدد"],["الخانة",selected.location_code||"غير محدد"],["الكمية",inventoryDisplay(selected.quantity)],["الوحدة",inventoryUnit(selected.unit)],["تكلفة الوحدة",inventoryDisplay(selected.unit_cost)],["قيمة الحركة",inventoryDisplay(selected.value)]].map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></div>}
    </InventorySheet>
  </section>;
}
