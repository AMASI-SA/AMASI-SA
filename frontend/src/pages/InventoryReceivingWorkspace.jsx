import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowsClockwise, ClipboardText, Cube, MagnifyingGlass, Receipt, Warehouse, WarningCircle } from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";
import StockPreparationOrders from "../components/inventory/StockPreparationOrders";
import SallaInventorySync from "../components/inventory/SallaInventorySync";
import { loadInventoryReceivingCatalog } from "../services/mezanInventoryReceiving";
import InventorySuiteFrame, { InventoryPageHeader } from "../components/inventory/suite/InventorySuiteFrame";
import "../components/inventory/suite/inventoryProjectionViews.css";
import "./inventory-receiving.css";

const EMPTY_CATALOG = {
    actor_workplace: {}, purchase_invoices: [], products: [], warehouses: [],
    locations: [], recent_receipts: [], inventory_health: [], inventory_alerts: [],
};
const invoiceState = (invoice) => invoice.schema_version !== "g47-v1" ? ["legacy", "فاتورة سابقة — للقراءة فقط"] : invoice.state === "approved" ? ["approved", "معتمدة ومستلمة"] : invoice.state === "draft" ? ["draft", "مسودة — بانتظار المراجعة"] : ["review", "عملية اعتماد تحتاج متابعة"];

export default function InventoryReceivingWorkspace({ accessMode = null }) {
    const readOnly = accessMode === "read";
    const [activeSection, setActiveSection] = useState("purchase_receiving");
    const [catalog, setCatalog] = useState(EMPTY_CATALOG);
    const [invoices, setInvoices] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [query, setQuery] = useState("");
    const [invoiceFilter, setInvoiceFilter] = useState("all");
    const request = useRef(0);

    const refresh = useCallback(async ({ quiet = false } = {}) => {
        const current = ++request.current;
        if (!quiet) setLoading(true);
        setError("");
        try {
            const [inventory, purchase] = await Promise.all([
                loadInventoryReceivingCatalog(),
                readOnly ? Promise.resolve(null) : api.get("/purchase-invoices?limit=500"),
            ]);
            if (current !== request.current) return;
            setCatalog({ ...EMPTY_CATALOG, ...(inventory || {}) });
            setInvoices(purchase?.data?.items || []);
        } catch (err) {
            if (current !== request.current) return;
            setCatalog(EMPTY_CATALOG); setInvoices([]);
            setError(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تحميل بيانات المخزون والفواتير.");
        } finally { if (current === request.current && !quiet) setLoading(false); }
    }, [readOnly]);
    useEffect(() => {
        setActiveSection("purchase_receiving");
        setCatalog(EMPTY_CATALOG); setInvoices([]);
        refresh();
        return () => { request.current += 1; };
    }, [refresh]);

    const visibleInvoices = invoices.filter((invoice) => (invoiceFilter === "all" || invoiceState(invoice)[0] === invoiceFilter)
        && [invoice.invoice_number,invoice.supplier_name].join(" ").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));

    return <InventorySuiteFrame active="receiving"><div className="mz-receiving-workspace" dir="rtl" data-testid="inventory-receiving-v2-page">
        <InventoryPageHeader title="استلام المشتريات والمخزون" description={readOnly ? "آخر الاستلامات المثبتة ضمن نطاقك." : "راجع الفاتورة وحدد خانات بنودها، ثم اعتمد الاستلام كاملًا."}>
            <button type="button" className="mz-stock-action" onClick={() => refresh()} disabled={loading}><ArrowsClockwise size={18} className={loading?"animate-spin":""}/>تحديث</button>
            {!readOnly && <Link to="/purchase-invoices" className="mz-stock-action is-primary"><Receipt size={18}/>فتح فواتير المشتريات</Link>}
        </InventoryPageHeader>
        {!readOnly && <nav className="mz-receiving-tabs" aria-label="أقسام استلام المخزون">
            {[{ id:"purchase_receiving",label:"استلام فاتورة شراء",Icon:ClipboardText },{ id:"stock_preparation",label:"أمر تجهيز مخزون",Icon:Cube },{ id:"salla_sync",label:"مزامنة سلة",Icon:Warehouse }].map(({ id,label,Icon }) => <button key={id} type="button" aria-current={activeSection===id?"page":undefined} onClick={()=>setActiveSection(id)}><Icon size={19}/>{label}</button>)}
        </nav>}
        {error && <p role="alert" className="mz-stock-notice is-error"><WarningCircle size={21}/>{error}<button onClick={()=>refresh()}>إعادة التحميل</button></p>}
        {loading && <div role="status" className="mz-stock-empty"><ArrowsClockwise className="animate-spin" size={30}/>{readOnly?"جارٍ تحميل الاستلامات…":"جارٍ تحميل المخزون والفواتير…"}</div>}
        {!loading && !error && (!readOnly && activeSection==="salla_sync" ? <div className="mz-receiving-native"><SallaInventorySync/></div> : !readOnly && activeSection==="stock_preparation" ? <div className="mz-receiving-native"><StockPreparationOrders inventoryCatalog={catalog} autoSyncProducts={false} onInventoryChanged={()=>refresh({quiet:true})}/></div> : <>
            <section className="mz-receiving-metrics" aria-label="ملخص الاستلام">
                {!readOnly && <><article><span>فواتير بانتظار المراجعة</span><strong>{invoices.filter(invoice=>invoiceState(invoice)[0]==="draft").length}</strong></article><article><span>معتمدة ومستلمة في القائمة</span><strong>{invoices.filter(invoice=>invoiceState(invoice)[0]==="approved").length}</strong></article></>}
                <article><span>استلامات في القراءة الحالية</span><strong>{catalog.recent_receipts.length}</strong></article><article><span>تنبيهات تحتاج مراجعة</span><strong>{catalog.inventory_alerts.length}</strong></article>
            </section>
            <div className={"mz-receiving-grid"+(readOnly?" is-read-only":"")}>
                {!readOnly && <section className="mz-stock-surface" data-testid="purchase-approval-entry"><header className="mz-stock-section-heading"><div><small>المراجعة قبل الاعتماد</small><h2>فواتير المشتريات</h2></div><span className="mz-stock-count">{visibleInvoices.length}</span></header>
                    <p className="mz-receiving-policy">المسودة لا تغيّر المخزون أو ذمة المورد. الفواتير السابقة للقراءة فقط؛ لا استلام منفرد لبند ولا إعادة استلام فاتورة معتمدة.</p>
                    <div className="mz-receiving-filters"><label><MagnifyingGlass size={18}/><input aria-label="بحث فواتير الاستلام" value={query} onChange={event=>setQuery(event.target.value)} placeholder="رقم الفاتورة أو المورد…"/></label><select aria-label="حالة فاتورة الاستلام" value={invoiceFilter} onChange={event=>setInvoiceFilter(event.target.value)}><option value="all">كل الفواتير</option><option value="draft">بانتظار المراجعة</option><option value="approved">معتمدة ومستلمة</option><option value="legacy">فواتير سابقة</option><option value="review">تحتاج متابعة</option></select></div>
                    {!visibleInvoices.length ? <div className="mz-stock-empty"><Receipt size={40}/><h3>لا توجد فواتير تطابق العرض</h3></div> : <div className="mz-stock-table-wrap"><table className="mz-stock-table mz-receiving-invoice-table"><thead><tr><th>الفاتورة والمورد</th><th>الحالة</th><th>الإجراء</th></tr></thead><tbody>{visibleInvoices.map(invoice=><tr key={invoice.id} data-testid={"receiving-invoice-"+invoice.id}><th scope="row"><strong>{invoice.invoice_number||"فاتورة بلا رقم"}</strong><small>{invoice.supplier_name||"مورد غير محدد"}</small></th><td><span className={"mz-stock-badge "+(invoiceState(invoice)[0]==="approved"?"is-ready":"is-warning")}>{invoiceState(invoice)[1]}</span></td><td><Link to={"/purchase-invoices?invoice="+encodeURIComponent(invoice.id)} className="mz-receiving-review">{invoice.schema_version==="g47-v1"&&invoice.state==="draft"?"مراجعة الفاتورة للاعتماد الكامل":"عرض الفاتورة"}</Link></td></tr>)}</tbody></table></div>}
                </section>}
                <aside className="mz-receiving-side">
                    <section className="mz-stock-surface"><header className="mz-stock-section-heading"><h2>آخر عمليات الاستلام</h2><span className="mz-stock-count">{catalog.recent_receipts.length}</span></header>{catalog.recent_receipts.length ? <div className="mz-receiving-receipts">{catalog.recent_receipts.slice(0,10).map(receipt=><article key={receipt.id}><span className="mz-stock-item-icon"><ClipboardText size={22}/></span><div><strong>{receipt.product_name||"صنف مخزني"}</strong><small><bdi>{receipt.location_code||"خانة غير محددة"}</bdi> · كمية {receipt.quantity??"غير متاحة"}</small></div></article>)}</div> : <div className="mz-stock-empty"><ClipboardText size={35}/><p>لا توجد عمليات استلام ضمن النطاق المتاح.</p></div>}</section>
                    {catalog.inventory_alerts.length>0&&<section className="mz-stock-surface"><header className="mz-stock-section-heading"><h2>تنبيهات المخزون</h2></header><div className="mz-receiving-alerts">{catalog.inventory_alerts.slice(0,12).map((row,index)=><article key={row.mezan_product_id||row.salla_product_id||index}><WarningCircle size={19}/><div><strong>{row.name||row.product_name||row.sku}</strong><p>{row.message||row.inventory_health||row.status||"تحتاج البيانات إلى مراجعة"}</p></div></article>)}</div></section>}
                </aside>
            </div>
        </>)}
    </div></InventorySuiteFrame>;
}
