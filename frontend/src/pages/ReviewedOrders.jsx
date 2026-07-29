import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowSquareOut, Gift, MagnifyingGlass, Package, SpinnerGap, WarningCircle } from "@phosphor-icons/react";

import { listReviewedOrderReviews } from "../services/orderReviewEngine";

function sallaAdminUrl(order) {
    const value = String(order?.salla_admin_url || "").trim();
    if (!value) return "";
    try {
        const url = new URL(value);
        return ["http:", "https:"].includes(url.protocol) ? url.toString() : "";
    } catch {
        return "";
    }
}

function isGiftOrder(item) {
    const texts = [
        ...(item?.items || []).flatMap((product) => [
            ...(product?.options || []).flatMap((option) => [option?.name, option?.value]),
            ...(product?.custom_fields || []).flatMap((field) => [field?.name, field?.label, field?.value, field?.answer]),
        ]),
        ...(item?.operational_items || []).flatMap((row) => [row?.name, ...(row?.linked_specs || []).flatMap((spec) => [spec?.name, spec?.value])]),
    ].map((value) => String(value || "").toLowerCase());
    return texts.some((value) => value.includes("هدية") || value.includes("اهداء") || value.includes("إهداء") || value.includes("gift"));
}

export default function ReviewedOrders() {
    const [rows, setRows] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [search, setSearch] = useState("");

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const result = await listReviewedOrderReviews({ limit: 50 });
            setRows(result.items);
        } catch (loadError) {
            setError(loadError.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const filtered = useMemo(() => {
        const query = search.trim().toLowerCase();
        if (!query) return rows;
        return rows.filter((row) => [row.order_number, row.customer?.name, row.customer?.mobile]
            .some((value) => String(value || "").toLowerCase().includes(query)));
    }, [rows, search]);

    if (loading) return <div className="flex min-h-80 items-center justify-center"><SpinnerGap size={34} className="animate-spin text-violet-600" /></div>;
    if (error) return <div className="rounded-2xl border border-rose-200 bg-rose-50 p-5 text-rose-800"><WarningCircle className="ml-2 inline" />{error}</div>;

    return (
        <section className="space-y-4" dir="rtl" data-testid="reviewed-orders-stage">
            <div className="flex flex-col gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <h2 className="text-xl font-extrabold text-slate-950">الطلبات التي تمت مراجعتها</h2>
                    <p className="mt-1 text-sm text-slate-500">الصور والملاحظات والمنتجات التشغيلية محفوظة ومعتمدة قبل إنشاء ملفات التجهيز.</p>
                </div>
                <label className="relative block w-full sm:max-w-sm">
                    <MagnifyingGlass className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                    <input value={search} onChange={(event) => setSearch(event.target.value)} className="w-full rounded-xl border border-slate-200 py-2.5 pl-3 pr-10 outline-none focus:border-violet-500" placeholder="بحث برقم الطلب أو العميل" />
                </label>
            </div>

            {filtered.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center text-slate-500">لا توجد طلبات في مرحلة تم المراجعة.</div>
            ) : (
                <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
                    {filtered.map((row) => {
                        const adminUrl = sallaAdminUrl(row);
                        const gift = isGiftOrder(row);
                        return (
                            <article key={row.order_number} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" data-testid="reviewed-order-card">
                                <div className="flex items-start justify-between gap-3">
                                    <div>
                                        <div className="text-xs font-bold text-slate-400">رقم الطلب</div>
                                        <div className="mt-1 text-lg font-black text-slate-950">#{row.order_number}</div>
                                    </div>
                                    {gift && <span className="inline-flex items-center gap-1 rounded-full bg-rose-100 px-3 py-1 text-xs font-extrabold text-rose-800"><Gift weight="fill" /> طلب هدية</span>}
                                </div>
                                <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
                                    <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs font-bold text-slate-400">العميل</div><div className="mt-1 font-extrabold text-slate-800">{row.customer?.name || "—"}</div></div>
                                    <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs font-bold text-slate-400">المدينة</div><div className="mt-1 font-extrabold text-slate-800">{row.shipping?.address?.city || "—"}</div></div>
                                    <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs font-bold text-slate-400">المنتجات</div><div className="mt-1 font-extrabold text-slate-800">{row.items?.length || 0}</div></div>
                                    <div className="rounded-xl bg-slate-50 p-3"><div className="text-xs font-bold text-slate-400">منتجات تشغيلية</div><div className="mt-1 font-extrabold text-slate-800">{row.operational_items?.length || 0}</div></div>
                                </div>
                                <div className="mt-4 flex flex-wrap gap-2">
                                    {adminUrl && <a href={adminUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-2 rounded-xl border border-teal-200 bg-teal-50 px-3 py-2 text-sm font-extrabold text-teal-900"><ArrowSquareOut /> فتح الطلب في سلة</a>}
                                    <span className="inline-flex items-center gap-2 rounded-xl bg-violet-50 px-3 py-2 text-sm font-extrabold text-violet-800"><Package /> جاهز لإنشاء ملف التجهيز</span>
                                </div>
                            </article>
                        );
                    })}
                </div>
            )}
        </section>
    );
}
