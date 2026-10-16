import { useState } from "react";
import { bundleError, bundleMoney, getBundleReport } from "../../services/productBundles";
import { loadMezanSuppliersWorkspace } from "../../services/mezanSuppliersV2";

export default function BundleReportPanel() {
    const [report, setReport] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [suppliers, setSuppliers] = useState([]);
    async function load() {
        setBusy(true); setError("");
        try {
            const [result, catalog] = await Promise.all([getBundleReport(), loadMezanSuppliersWorkspace()]);
            setReport(result); setSuppliers(catalog.suppliers || []);
        }
        catch (err) { setError(bundleError(err)); }
        finally { setBusy(false); }
    }
    return <section className="space-y-3 rounded-2xl border bg-white p-4" dir="rtl">
        <button type="button" className="rounded-lg border px-4 py-2 font-bold disabled:opacity-50" disabled={busy} onClick={load}>{busy ? "جارٍ تحميل التقرير…" : "تقرير البكجات والمكونات"}</button>
        {error && <p role="alert" className="text-red-700">{error}</p>}
        {report && <><p>البكجات المباعة: {report.sales_count} · بانتظار المكونات: {report.waiting_count}</p><div className="overflow-x-auto"><table className="w-full text-right text-sm"><thead><tr>{["الطلب", "البكج", "الكمية", "الإصدار", "التقديرية", "المتوقعة", "الفعلية", "الجاهزية"].map((label) => <th className="p-2" key={label}>{label}</th>)}</tr></thead><tbody>{(report.items || []).map((item) => <tr key={`${item.order_number}:${item.order_line_id}`} className="border-t"><td className="p-2">{item.order_number}</td><td>{item.product_name || item.name || item.product_id}</td><td>{item.quantity}</td><td>{item.revision}</td><td>{bundleMoney(item.estimated_cost)}</td><td>{bundleMoney(item.expected_cost)}</td><td>{bundleMoney(item.actual_cost)}</td><td>{item.state === "cancelled" ? "ملغى" : item.ready === true ? "جاهز" : "بانتظار المكونات"}</td></tr>)}</tbody></table>{!report.items?.length && <p className="p-3">لا توجد بكجات مسجلة في التقرير.</p>}</div></>}
        {report && <><h3 className="font-bold">استهلاك المكونات</h3><div className="overflow-x-auto"><table className="w-full text-right text-sm"><thead><tr>{["المكوّن", "المتغير", "المستهلك", "المحجوز"].map((title) => <th key={title} className="p-2">{title}</th>)}</tr></thead><tbody>{(report.component_usage || []).map((item, index) => <tr key={`${item.kind}:${item.reference_id}:${item.variant_id || index}`} className="border-t"><td className="p-2">{item.name}</td><td>{item.variant_id || "—"}</td><td>{item.consumed_quantity}</td><td>{item.reserved_quantity}</td></tr>)}</tbody></table></div>
            <h3 className="font-bold">احتياجات الموردين حسب الطلب</h3>{(report.supplier_requirements || []).map((group) => <section key={group.supplier_id} className="rounded-lg border p-3"><h4 className="font-bold">{suppliers.find((supplier) => supplier.id === group.supplier_id)?.company_name || suppliers.find((supplier) => supplier.id === group.supplier_id)?.name || group.supplier_id}</h4><div className="overflow-x-auto"><table className="w-full text-right text-sm"><thead><tr>{["الطلب", "المكوّن", "المتغير", "الوحدة", "القطعة والتخصيص", "الكمية المطلوبة", "تكلفة الوحدة المتوقعة"].map((title) => <th className="p-2" key={title}>{title}</th>)}</tr></thead><tbody>{(group.requirements || []).map((item, index) => <tr key={`${item.order_number}:${item.order_line_id}:${item.unit_index}:${item.leaf_id}:${index}`} className="border-t"><td className="p-2">{item.order_number}</td><td>{item.name}</td><td>{item.variant_id || "—"}</td><td>{item.unit_index ?? "—"}</td><td>{item.slot_id || "—"} {Object.entries(item.customization || {}).map(([key, value]) => `${key}: ${value}`).join(" · ")}</td><td>{item.quantity}</td><td>{bundleMoney(item.expected_unit_cost)}</td></tr>)}</tbody></table></div></section>)}</>}
    </section>;
}
