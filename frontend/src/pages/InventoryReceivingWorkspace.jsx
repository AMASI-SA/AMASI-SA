import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ClipboardText, Cube, Warehouse } from "@phosphor-icons/react";
import api, { formatApiErrorDetail } from "../lib/api";
import StockPreparationOrders from "../components/inventory/StockPreparationOrders";
import SallaInventorySync from "../components/inventory/SallaInventorySync";
import { loadInventoryReceivingCatalog } from "../services/mezanInventoryReceiving";

const EMPTY_CATALOG = {
    actor_workplace: {}, purchase_invoices: [], products: [], warehouses: [],
    locations: [], recent_receipts: [], inventory_health: [], inventory_alerts: [],
};

export default function InventoryReceivingWorkspace() {
    const [activeSection, setActiveSection] = useState("purchase_receiving");
    const [catalog, setCatalog] = useState(EMPTY_CATALOG);
    const [invoices, setInvoices] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const refresh = async ({ quiet = false } = {}) => {
        if (!quiet) setLoading(true);
        setError("");
        try {
            const [inventory, purchase] = await Promise.all([
                loadInventoryReceivingCatalog(),
                api.get("/purchase-invoices?limit=500"),
            ]);
            setCatalog({ ...EMPTY_CATALOG, ...(inventory || {}) });
            setInvoices(purchase.data.items || []);
        } catch (err) {
            setError(formatApiErrorDetail(err.response?.data?.detail) || "تعذر تحميل بيانات المخزون والفواتير.");
        } finally { if (!quiet) setLoading(false); }
    };
    useEffect(() => { refresh(); }, []);

    return <div className="space-y-5" dir="rtl" data-testid="inventory-receiving-v2-page">
        <header className="rounded-3xl border border-violet-200 bg-white p-6 shadow-sm">
            <h1 className="flex items-center gap-3 text-2xl font-black text-slate-950"><ClipboardText size={28} />استلام المشتريات والمخزون</h1>
            <p className="mt-2 text-sm leading-6 text-slate-600">استلام المشتريات يتم عند اعتماد كامل الفاتورة، مع تحديد خانات جميع البنود. راجع المسودة في فواتير المشتريات.</p>
        </header>
        <nav className="grid gap-2 rounded-2xl border bg-white p-2 sm:grid-cols-3" aria-label="أقسام استلام المخزون">
            {[
                { id: "purchase_receiving", label: "استلام فاتورة شراء", Icon: ClipboardText },
                { id: "stock_preparation", label: "أمر تجهيز مخزون", Icon: Cube },
                { id: "salla_sync", label: "مزامنة سلة", Icon: Warehouse },
            ].map(({ id, label, Icon }) => <button key={id} type="button" onClick={() => setActiveSection(id)}
                className={"flex items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-black " + (activeSection === id ? "bg-violet-700 text-white" : "text-slate-600")}>
                <Icon size={20} />{label}
            </button>)}
        </nav>
        {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-rose-800">{error}<button className="mr-3 underline" onClick={() => refresh()}>إعادة التحميل</button></p>}
        {activeSection === "salla_sync" ? <SallaInventorySync /> : activeSection === "stock_preparation" ? (
            loading ? <p>جارٍ تحميل المخزون…</p> : <StockPreparationOrders inventoryCatalog={catalog} onInventoryChanged={() => refresh({ quiet: true })} />
        ) : <section className="space-y-4 rounded-2xl border bg-white p-5" data-testid="purchase-approval-entry">
            <h2 className="text-lg font-black">اعتماد الفاتورة واستلام جميع الكميات</h2>
            <p className="text-sm text-slate-600">المسودة لا تغيّر المخزون أو ذمة المورد. الفواتير السابقة للقراءة فقط؛ لا استلام منفرد لبند ولا إعادة استلام فاتورة معتمدة.</p>
            <Link to="/purchase-invoices" className="inline-block rounded-lg bg-violet-700 px-4 py-2 text-sm font-bold text-white">فتح فواتير المشتريات</Link>
            {loading ? <p>جارٍ تحميل الفواتير…</p> : <ul className="divide-y rounded-xl border">
                {invoices.map((invoice) => <li key={invoice.id} className="flex flex-wrap items-center justify-between gap-3 p-4" data-testid={"receiving-invoice-" + invoice.id}>
                    <div><h3 className="font-bold">{invoice.invoice_number || "فاتورة بلا رقم"} · {invoice.supplier_name || "—"}</h3>
                        <p className="mt-1 text-xs text-slate-500">{invoice.schema_version !== "g47-v1" ? "فاتورة سابقة — للقراءة فقط" : invoice.state === "approved" ? "معتمدة ومستلمة" : invoice.state === "draft" ? "مسودة — بانتظار المراجعة" : "عملية اعتماد تحتاج متابعة"}</p></div>
                    <Link to={"/purchase-invoices?invoice=" + encodeURIComponent(invoice.id)} className="rounded-lg border px-3 py-2 text-sm font-bold text-violet-700">
                        {invoice.schema_version === "g47-v1" && invoice.state === "draft" ? "مراجعة الفاتورة للاعتماد الكامل" : "عرض الفاتورة"}
                    </Link>
                </li>)}
                {!invoices.length && <li className="p-4 text-sm text-slate-500">لا توجد فواتير متاحة.</li>}
            </ul>}
            {catalog.inventory_alerts.length > 0 && <aside className="space-y-2 rounded-xl border border-amber-200 bg-amber-50 p-4">
                <h3 className="font-bold">تنبيهات المخزون</h3>
                {catalog.inventory_alerts.slice(0, 12).map((row, index) => <p key={row.mezan_product_id || row.salla_product_id || index} className="text-sm">{row.name || row.product_name || row.sku} · {row.message || row.inventory_health || row.status || "تحتاج البيانات إلى مراجعة"}</p>)}
            </aside>}
            {catalog.recent_receipts.length > 0 && <aside className="space-y-2 rounded-xl border p-4">
                <h3 className="font-bold">آخر عمليات الاستلام</h3>
                {catalog.recent_receipts.slice(0, 10).map((receipt) => <p key={receipt.id} className="text-sm">{receipt.product_name} · {receipt.location_code} · كمية {receipt.quantity}</p>)}
            </aside>}
        </section>}
    </div>;
}
