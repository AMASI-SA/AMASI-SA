import { useEffect, useState } from "react";
import { bundleError, bundleMoney, getOrderBundles, refreshOrderBundles, linkBundleReceipt, changeBundleSource } from "../../services/productBundles";
import { loadMezanSuppliersWorkspace } from "../../services/mezanSuppliersV2";

const stateLabel = (state) => ({ needs_source: "يحتاج مصدر توريد", ready: "جاهز", reserved: "محجوز", consumed: "مستهلك", received: "مستلم", awaiting_supplier: "بانتظار المورد", awaiting_stock: "بانتظار المخزون", shortage: "نقص مخزون", partial: "مكتمل جزئيًا", pending: "قيد الانتظار", not_required: "غير مطلوب", skipped: "مكوّن اختياري متجاوز" }[state] || "قيد المراجعة");

export default function BundleOperationalPanel({ orderNumber, canReceive = false, canChangeSource = false }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const [reload, setReload] = useState(0);
    const [busy, setBusy] = useState(false);
    const [receipts, setReceipts] = useState({});
    const [suppliers, setSuppliers] = useState([]);
    const [sources, setSources] = useState({});
    const [supplierError, setSupplierError] = useState("");
    useEffect(() => {
        let live = true;
        loadMezanSuppliersWorkspace().then((result) => { if (live) setSuppliers(result.suppliers || []); }).catch(() => { if (live) setSupplierError("تعذّر تحميل أسماء الموردين؛ أعد المحاولة لعرض أسمائهم."); });
        return () => { live = false; };
    }, []);
    useEffect(() => {
        let live = true;
        setData(null); setError("");
        setSources({}); setReceipts({});
        if (orderNumber) getOrderBundles(orderNumber).then((result) => {
            if (live) setData(result);
        }).catch((err) => { if (live) setError(bundleError(err)); });
        return () => { live = false; };
    }, [orderNumber, reload]);
    async function mutate(payload) {
        setBusy(true); setError("");
        try {
            if (payload) await linkBundleReceipt(orderNumber, payload);
            else await refreshOrderBundles(orderNumber);
            setReload((value) => value + 1);
        } catch (err) { setError(bundleError(err)); } finally { setBusy(false); }
    }
    async function updateSource(bundle, leaf) {
        const source = sources[leaf.leaf_id];
        setBusy(true); setError("");
        try {
            await changeBundleSource(orderNumber, { order_line_id: bundle.order_line_id, leaf_id: leaf.leaf_id, sourcing_mode: source.sourcing_mode, supplier_id: source.sourcing_mode === "supplier" ? source.supplier_id : null });
            setReload((value) => value + 1);
        } catch (err) { setError(bundleError(err)); } finally { setBusy(false); }
    }
    const supplierName = (id) => suppliers.find((item) => String(item.id) === String(id))?.company_name || suppliers.find((item) => String(item.id) === String(id))?.name || "اسم المورد غير متاح";
    const hasSupplier = (leaf) => leaf.sourcing_mode === "supplier" || (leaf.sourcing_mode === "auto" && leaf.supplier_id && ((leaf.sourcing_segments || []).some((segment) => segment.sourcing_mode === "supplier" && Number(segment.quantity) > 0) || Number(leaf.supplier_quantity) > 0));
    if (!orderNumber || (data && !data.bundles?.length)) return null;
    return <section dir="rtl" className="space-y-4 rounded-2xl border bg-white p-5" data-testid="bundle-operational-panel">
        <h2 className="font-black">مكونات البكجات وقت البيع</h2>
        {supplierError && <p className="text-xs text-amber-800">{supplierError}</p>}
        {canReceive && <button type="button" disabled={busy || !data} className="rounded-lg border p-2 disabled:opacity-50" onClick={() => mutate()}>إعادة فحص النواقص وحجز المتاح</button>}
        {error ? <div role="alert">{error}<button type="button" className="mr-2 underline" onClick={() => setReload((value) => value + 1)}>إعادة المحاولة</button></div> : !data ? <p>جارٍ تحميل حالة المكونات…</p> : data.bundles.map((bundle) => <article key={bundle.order_line_id} className="space-y-3 rounded-xl border p-3">
            <h3 className="font-bold">{bundle.product_name || bundle.name || bundle.product_id} × {bundle.quantity} · إصدار التركيب {bundle.revision}</h3>
            {bundle.sale_quantity != null && <p className="rounded-lg bg-violet-50 p-2 text-sm">كمية سلة: {bundle.sale_quantity} × وحدات الاختيار: {bundle.units_per_selection ?? "—"} = كمية التجهيز الفعلية: {bundle.fulfillment_quantity ?? "—"}{bundle.current_product_quantity != null ? ` · وحدات نفس المنتج الحالي: ${bundle.current_product_quantity}` : ""}</p>}
            <p>{bundle.state === "cancelled" ? "الطلب ملغى" : bundle.ready === true ? "جميع المكونات المطلوبة جاهزة" : "المكونات لم تكتمل جاهزيتها"}</p>
            <p className="text-sm">تقديرية: {bundleMoney(bundle.estimated_cost)} · متوقعة: {bundleMoney(bundle.expected_cost)} · فعلية: {bundleMoney(bundle.actual_cost)}</p>
            {canChangeSource && <details className="rounded-lg border p-3"><summary className="cursor-pointer text-sm font-bold">تعديل مصدر توريد مكوّن لهذا الطلب</summary><p className="my-2 text-xs">يُراجع الخادم الحجوزات والتوريد قبل قبول التغيير. تركيب الكتالوج مستقل عن هذا الطلب.</p>{(bundle.leaves || []).filter((leaf) => leaf.leaf_id && !leaf.cost_only).map((leaf) => {
                const source = sources[leaf.leaf_id] || { sourcing_mode: leaf.sourcing_mode, supplier_id: leaf.supplier_id || "" };
                const edit = (patch) => setSources((current) => ({ ...current, [leaf.leaf_id]: { ...source, ...patch } }));
                return <fieldset key={leaf.leaf_id} disabled={busy} className="my-2 flex flex-wrap items-center gap-2"><legend className="text-sm">{leaf.name}</legend><select aria-label={`مصدر ${leaf.name}`} className="rounded-lg border p-2" value={source.sourcing_mode} onChange={(e) => edit({ sourcing_mode: e.target.value })}>{leaf.sourcing_mode === "auto" && <option value="auto" disabled>تلقائي — اختر مصدرًا للتعديل اليدوي</option>}<option value="stock">المخزون</option><option value="supplier">المورد</option></select>{source.sourcing_mode === "supplier" && <select aria-label={`مورد ${leaf.name}`} className="rounded-lg border p-2" value={source.supplier_id} onChange={(e) => edit({ supplier_id: e.target.value })}><option value="">اختر المورد</option>{suppliers.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.company_name || supplier.name}</option>)}</select>}<button type="button" disabled={!sources[leaf.leaf_id] || source.sourcing_mode === "auto" || (source.sourcing_mode === "supplier" && !source.supplier_id)} className="rounded-lg border p-2 disabled:opacity-50" onClick={() => updateSource(bundle, leaf)}>حفظ مصدر التوريد</button></fieldset>;
            })}</details>}
            {canReceive && (bundle.leaves || []).filter((leaf) => hasSupplier(leaf) && leaf.leaf_id).map((leaf) => <div key={leaf.leaf_id} className="flex flex-wrap items-end gap-2 rounded-lg bg-slate-50 p-2"><label className="text-sm">مرجع إيصال شراء مرحّل موجود للمكوّن: {leaf.name}{leaf.sourcing_mode === "auto" ? " — للجزء المطلوب من المورد فقط" : ""}<input className="block rounded-lg border p-2" value={receipts[leaf.leaf_id] || ""} onChange={(e) => setReceipts((current) => ({ ...current, [leaf.leaf_id]: e.target.value }))} /></label><button type="button" disabled={busy || !receipts[leaf.leaf_id]?.trim()} className="rounded-lg border p-2 disabled:opacity-50" onClick={() => mutate({ order_line_id: bundle.order_line_id, leaf_id: leaf.leaf_id, receipt_id: receipts[leaf.leaf_id].trim() })}>ربط الإيصال والتحقق</button></div>)}
            <div className="overflow-x-auto"><table className="w-full text-right text-sm"><thead><tr>{["المكوّن", "الكمية", "المصدر / المورد", "الحالة", "محجوز", "مستهلك", "مستلم", "تكلفة الوحدة", "التكلفة الفعلية"].map((title) => <th className="p-2" key={title}>{title}</th>)}</tr></thead><tbody>{(bundle.leaves || []).map((leaf, index) => <tr key={index} className="border-t"><td className="p-2">{leaf.name}{leaf.slot_id && <span className="block text-xs">القطعة {leaf.slot_id}</span>}{leaf.variant_id && <span className="block text-xs">النسخة: {leaf.variant_name || leaf.variant_display_name || "المحددة وقت البيع"}</span>}{leaf.customization && <span className="block text-xs">{Object.values(leaf.customization).map(String).join("، ")}</span>}</td><td>{leaf.quantity}</td><td>{leaf.sourcing_mode === "auto" ? "توريد تلقائي" : leaf.sourcing_mode === "supplier" ? "مورد" : "مخزون"}{leaf.supplier_id ? ` · ${leaf.supplier_name || supplierName(leaf.supplier_id)}` : ""}{(leaf.sourcing_segments || []).map((segment, segmentIndex) => <p key={segment.leaf_id || segmentIndex} className="text-xs">{segment.sourcing_mode === "supplier" ? "من المورد" : "من المخزون"}: {segment.quantity} · {stateLabel(segment.state)}{segment.supplier_id ? ` · ${supplierName(segment.supplier_id)}` : ""}</p>)}{Number(leaf.unresolved_quantity) > 0 && <p className="text-amber-800">كمية بلا مصدر: {leaf.unresolved_quantity}</p>}</td><td>{stateLabel(leaf.state)}</td><td>{leaf.reserved_quantity ?? "—"}</td><td>{leaf.consumed_quantity ?? "—"}</td><td>{leaf.received_quantity ?? "—"}</td><td>{bundleMoney(leaf.unit_cost)}</td><td>{bundleMoney(leaf.actual_cost)}</td></tr>)}</tbody></table></div>
        </article>)}
    </section>;
}
