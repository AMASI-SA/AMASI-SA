import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowsClockwise,
    Barcode,
    CheckCircle,
    Cube,
    MagnifyingGlass,
    Package,
    SpinnerGap,
    Storefront,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    getProductsV2Summary,
    listProductsV2,
    syncProductsV2,
} from "../services/mezanProductsV2";

function formatMoney(value) {
    if (value === null || value === undefined || value === "" || Number.isNaN(Number(value))) return "—";
    return `${Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`;
}

function formatQuantity(value, unlimited) {
    if (unlimited) return "غير محدود";
    if (value === null || value === undefined || value === "") return "—";
    return Number(value).toLocaleString("en-US");
}

function statusCopy(value) {
    if (value === "active") return "نشط";
    if (value === "out_of_stock") return "نفد المخزون";
    if (value === "inactive") return "غير نشط";
    return value || "غير معروف";
}

function statusTone(value) {
    if (value === "active") return "border-emerald-200 bg-emerald-50 text-emerald-800";
    if (value === "out_of_stock") return "border-amber-200 bg-amber-50 text-amber-800";
    if (value === "inactive") return "border-slate-200 bg-slate-100 text-slate-700";
    return "border-slate-200 bg-white text-slate-600";
}

function Metric({ label, value, subtitle, Icon, tone = "violet" }) {
    const tones = {
        violet: "border-violet-200 bg-violet-50 text-violet-950",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        slate: "border-slate-200 bg-white text-slate-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
    };
    return (
        <div className={`rounded-2xl border p-4 shadow-sm ${tones[tone] || tones.slate}`}>
            <div className="flex items-center justify-between gap-3">
                <div>
                    <p className="text-xs font-bold opacity-65">{label}</p>
                    <p className="num mt-1 text-2xl font-black">{value}</p>
                    {subtitle && <p className="mt-1 text-[11px] opacity-60">{subtitle}</p>}
                </div>
                <div className="rounded-xl bg-white/80 p-2"><Icon size={22} weight="duotone" /></div>
            </div>
        </div>
    );
}

function ProductImage({ product }) {
    if (product.main_image) {
        return (
            <img
                src={product.main_image}
                alt={product.name}
                className="h-16 w-16 rounded-xl border border-slate-200 object-cover"
                loading="lazy"
            />
        );
    }
    return (
        <div className="flex h-16 w-16 items-center justify-center rounded-xl border border-slate-200 bg-slate-50 text-slate-300">
            <Package size={28} weight="duotone" />
        </div>
    );
}

export default function MezanProducts() {
    const [items, setItems] = useState([]);
    const [summary, setSummary] = useState(null);
    const [pagination, setPagination] = useState({ page: 1, per_page: 30, total: 0, total_pages: 1 });
    const [query, setQuery] = useState("");
    const [appliedQuery, setAppliedQuery] = useState("");
    const [status, setStatus] = useState("");
    const [loading, setLoading] = useState(true);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState("");

    const load = useCallback(async ({ page = 1, search = appliedQuery, productStatus = status } = {}) => {
        setLoading(true);
        setError("");
        try {
            const [listResult, summaryResult] = await Promise.all([
                listProductsV2({ page, perPage: pagination.per_page, query: search, status: productStatus }),
                getProductsV2Summary(),
            ]);
            setItems(listResult.items || []);
            setPagination(listResult.pagination || { page: 1, per_page: 30, total: 0, total_pages: 1 });
            setSummary(summaryResult || null);
        } catch (loadError) {
            const detail = loadError?.response?.data?.detail;
            const message = typeof detail === "string" ? detail : detail?.message;
            setError(message || loadError?.message || "تعذّر تحميل كتالوج المنتجات الجديد.");
        } finally {
            setLoading(false);
        }
    }, [appliedQuery, pagination.per_page, status]);

    useEffect(() => {
        load({ page: 1 });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const lastSync = summary?.last_sync || null;
    const lastSyncLabel = useMemo(() => {
        const value = lastSync?.ended_at || lastSync?.started_at;
        if (!value) return "لم تُشغّل مزامنة V2 بعد";
        try {
            return new Date(value).toLocaleString("en-GB");
        } catch {
            return String(value);
        }
    }, [lastSync]);

    async function runSync() {
        setSyncing(true);
        try {
            const result = await syncProductsV2();
            toast.success(`اكتملت مزامنة V2: ${result.seen_products || 0} منتج، أخطاء ${result.errors_count || 0}`);
            await load({ page: 1 });
        } catch (syncError) {
            const detail = syncError?.response?.data?.detail;
            toast.error((typeof detail === "string" ? detail : detail?.message) || "تعذّرت مزامنة المنتجات إلى قاعدة V2");
        } finally {
            setSyncing(false);
        }
    }

    function submitSearch(event) {
        event.preventDefault();
        const next = query.trim();
        setAppliedQuery(next);
        load({ page: 1, search: next });
    }

    function changeStatus(value) {
        setStatus(value);
        load({ page: 1, productStatus: value });
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="mezan-products-v2-page">
            <section className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
                <div className="flex flex-col gap-4 p-5 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex items-start gap-3">
                        <div className="rounded-2xl bg-violet-100 p-3 text-violet-700"><Package size={30} weight="fill" /></div>
                        <div>
                            <div className="flex flex-wrap items-center gap-2">
                                <h1 className="text-2xl font-black text-slate-950">منتجات Mezan OS</h1>
                                <span className="rounded-full bg-emerald-100 px-2.5 py-1 text-[11px] font-black text-emerald-800">قاعدة V2 مستقلة</span>
                                <span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-black text-slate-700">قراءة فقط</span>
                            </div>
                            <p className="mt-1 max-w-3xl text-sm leading-6 text-slate-500">
                                المصدر المباشر هو سلة، والحفظ في <code className="num rounded bg-slate-100 px-1">mezan_products_v2</code>. لا تعتمد هذه الصفحة على جداول المنتجات القديمة أو بيانات الطلبات.
                            </p>
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={runSync}
                        disabled={syncing}
                        className="flex items-center justify-center gap-2 rounded-xl bg-violet-700 px-5 py-3 text-sm font-extrabold text-white shadow-sm disabled:opacity-50"
                    >
                        {syncing ? <SpinnerGap size={19} className="animate-spin" /> : <ArrowsClockwise size={19} weight="bold" />}
                        {syncing ? "تتم المزامنة إلى V2…" : "مزامنة قاعدة V2 من سلة"}
                    </button>
                </div>
                <div className="border-t border-emerald-200 bg-emerald-50 px-5 py-3 text-sm text-emerald-900">
                    <div className="flex items-center gap-2 font-bold"><CheckCircle size={20} weight="fill" /> تشغيل متوازٍ آمن: النظام القديم لم يُحذف ولم يتحول أي مستهلك إليه بعد.</div>
                </div>
            </section>

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <Metric label="إجمالي منتجات V2" value={summary?.total ?? 0} subtitle="غير المؤرشفة" Icon={Package} />
                <Metric label="المنتجات النشطة" value={summary?.active ?? 0} subtitle="حسب حالة سلة" Icon={Storefront} tone="emerald" />
                <Metric label="المؤرشفة" value={summary?.archived ?? 0} subtitle="لا تُحذف نهائيًا" Icon={Cube} tone="slate" />
                <Metric label="آخر مزامنة" value={lastSync?.seen_products ?? 0} subtitle={lastSyncLabel} Icon={ArrowsClockwise} tone="amber" />
            </section>

            <section className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
                    <form onSubmit={submitSearch} className="flex min-w-0 flex-1 gap-2">
                        <div className="relative min-w-0 flex-1">
                            <MagnifyingGlass size={19} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                            <input
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder="ابحث بالاسم أو SKU أو الباركود أو رقم سلة…"
                                className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pr-10 pl-3 text-sm outline-none focus:border-violet-400 focus:ring-2 focus:ring-violet-100"
                            />
                        </div>
                        <button className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-bold text-white">بحث</button>
                    </form>
                    <label className="block min-w-[190px]">
                        <span className="mb-1 block text-xs font-bold text-slate-500">الحالة</span>
                        <select
                            value={status}
                            onChange={(event) => changeStatus(event.target.value)}
                            className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-violet-400"
                        >
                            <option value="">كل الحالات</option>
                            <option value="active">نشط</option>
                            <option value="out_of_stock">نفد المخزون</option>
                            <option value="inactive">غير نشط</option>
                            <option value="unknown">غير معروف</option>
                        </select>
                    </label>
                </div>
            </section>

            {error && (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-800">
                    <div className="flex items-start gap-2"><WarningCircle size={21} weight="fill" /> {error}</div>
                </div>
            )}

            <section className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm">
                <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4">
                    <div>
                        <h2 className="font-black text-slate-950">كتالوج المنتجات الجديد</h2>
                        <p className="mt-1 text-xs text-slate-500">{pagination.total || 0} منتج مطابق للفلاتر الحالية</p>
                    </div>
                    <span className="rounded-full bg-violet-50 px-3 py-1 text-xs font-bold text-violet-800">صفحة {pagination.page} من {pagination.total_pages}</span>
                </div>

                {loading ? (
                    <div className="flex min-h-[340px] items-center justify-center gap-3 font-bold text-violet-700">
                        <SpinnerGap size={28} className="animate-spin" /> جاري تحميل قاعدة المنتجات الجديدة…
                    </div>
                ) : !items.length ? (
                    <div className="flex min-h-[340px] flex-col items-center justify-center p-8 text-center">
                        <Package size={52} weight="duotone" className="text-slate-300" />
                        <h3 className="mt-3 text-lg font-black text-slate-900">لا توجد منتجات في قاعدة V2</h3>
                        <p className="mt-1 text-sm text-slate-500">شغّل المزامنة من سلة لإنشاء الكتالوج المستقل لأول مرة.</p>
                    </div>
                ) : (
                    <div className="divide-y divide-slate-100">
                        {items.map((product) => (
                            <article key={product.id || product.mezan_product_id} className="grid gap-4 p-4 transition hover:bg-slate-50/70 sm:grid-cols-[72px_minmax(0,1fr)_repeat(4,minmax(100px,auto))] sm:items-center sm:px-5">
                                <ProductImage product={product} />
                                <div className="min-w-0">
                                    <h3 className="truncate font-black text-slate-950" title={product.name}>{product.name}</h3>
                                    <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500">
                                        <span className="num">Salla: {product.salla_product_id}</span>
                                        <span className="num">Mezan: {product.mezan_product_id}</span>
                                    </div>
                                    {!!product.categories?.length && <p className="mt-1 truncate text-[11px] text-violet-600">{product.categories.map((row) => row.name).filter(Boolean).join("، ")}</p>}
                                </div>
                                <div className="text-sm"><span className="block text-[10px] text-slate-400">SKU</span><strong className="num break-all">{product.sku || "—"}</strong></div>
                                <div className="text-sm"><span className="block text-[10px] text-slate-400">الباركود</span><strong className="num flex items-center gap-1 break-all"><Barcode size={14} />{product.barcode || "—"}</strong></div>
                                <div className="text-sm"><span className="block text-[10px] text-slate-400">السعر / الكمية</span><strong>{formatMoney(product.sale_price ?? product.price)}</strong><span className="num mt-0.5 block text-xs text-slate-500">{formatQuantity(product.quantity, product.unlimited_quantity)}</span></div>
                                <div><span className={`inline-flex rounded-full border px-2.5 py-1 text-[11px] font-bold ${statusTone(product.status)}`}>{statusCopy(product.status)}</span></div>
                            </article>
                        ))}
                    </div>
                )}

                <div className="flex items-center justify-between border-t border-slate-100 px-5 py-4">
                    <button
                        type="button"
                        disabled={loading || pagination.page <= 1}
                        onClick={() => load({ page: pagination.page - 1 })}
                        className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 disabled:opacity-40"
                    >السابق</button>
                    <span className="num text-xs font-bold text-slate-500">{pagination.page} / {pagination.total_pages}</span>
                    <button
                        type="button"
                        disabled={loading || pagination.page >= pagination.total_pages}
                        onClick={() => load({ page: pagination.page + 1 })}
                        className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-bold text-slate-700 disabled:opacity-40"
                    >التالي</button>
                </div>
            </section>
        </div>
    );
}
