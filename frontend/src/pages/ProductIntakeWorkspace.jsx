import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
    ArrowClockwise, CheckCircle, Package, Robot,
    ShieldCheck, Tag, WarningCircle, Wrench,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import { getAiStoreFoundation, listProductIntake } from "../services/aiStoreOperations";
import { getSallaCategoryCatalog } from "../services/mezanProductsV2";
import ProductCreationPanel from "../components/products/ProductCreationPanel";

const LABELS = {
    has_sku: "SKU",
    has_images: "الصور",
    has_description: "الوصف",
    has_category: "التصنيف",
    has_base_cost: "التكلفة الأساسية",
    details_loaded: "تفاصيل سلة",
    option_costs_ready: "تكاليف الخيارات",
};

function ReadinessRing({ score }) {
    const safe = Math.max(0, Math.min(100, Number(score || 0)));
    return (
        <div className="relative grid h-16 w-16 shrink-0 place-items-center rounded-full" style={{ background: `conic-gradient(#7c3aed ${safe * 3.6}deg, #ede9fe 0deg)` }}>
            <div className="grid h-12 w-12 place-items-center rounded-full bg-white text-sm font-black text-violet-800">{safe}%</div>
        </div>
    );
}

function StatCard({ label, value, hint, Icon, tone = "violet" }) {
    const tones = {
        violet: "border-violet-200 bg-violet-50 text-violet-800",
        amber: "border-amber-200 bg-amber-50 text-amber-900",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
        slate: "border-slate-200 bg-white text-slate-800",
    };
    return (
        <div className={`rounded-2xl border p-4 ${tones[tone]}`}>
            <div className="flex items-center justify-between gap-3">
                <div>
                    <div className="text-xs font-bold opacity-70">{label}</div>
                    <div className="mt-1 text-3xl font-black">{value}</div>
                    {hint && <div className="mt-1 text-xs opacity-70">{hint}</div>}
                </div>
                <Icon size={30} weight="duotone" />
            </div>
        </div>
    );
}

const EMPTY_FILTERS = {
    category: "", subcategory: "", components: "", services: "", service_scope: "", option_surcharge: "",
};
const FACETS = [
    ["components", "المكونات", [["true", "مع مكونات"], ["false", "بدون مكونات"]]],
    ["services", "الخدمات", [["true", "مع خدمات"], ["false", "بدون خدمات"]]],
    ["service_scope", "ارتباط الخدمة", [
        ["option_service", "مرتبطة بخيار العميل"],
        ["independent_service", "خدمة مستقلة عن خيار العميل"],
        ["no_option_service", "بلا خدمة مرتبطة بخيار العميل"],
        ["service_surcharge", "مرتبطة بخيار عليه سعر إضافي"],
    ]],
    ["option_surcharge", "السعر الإضافي لخيار العميل", [
        ["true", "مع سعر إضافي"], ["false", "بلا سعر إضافي مسجّل"],
    ]],
];
const LINK_LABELS = {
    components: "مع مكونات",
    services: "مع خدمات",
    option_service: "خدمة مرتبطة بخيار العميل",
    independent_service: "خدمة مستقلة عن خيار العميل",
    option_surcharge: "خيار بسعر إضافي",
    service_surcharge: "خدمة مرتبطة بالخيار المسعّر",
};

function descendants(categories, parentId) {
    if (!parentId) return [];
    const ids = new Set([parentId]);
    let previousSize;
    do {
        previousSize = ids.size;
        for (const row of categories) {
            if (ids.has(String(row.parent_id))) ids.add(String(row.id));
        }
    } while (ids.size > previousSize);
    return Array.from(ids);
}

export default function ProductIntakeWorkspace({ accessMode = null, canOpenCatalogue = true }) {
    const navigate = useNavigate();
    const [status, setStatus] = useState("needs_attention");
    const [result, setResult] = useState(null);
    const [foundation, setFoundation] = useState(null);
    const [categories, setCategories] = useState([]);
    const [categoryError, setCategoryError] = useState(false);
    const [categoryRevision, setCategoryRevision] = useState(0);
    const [loading, setLoading] = useState(true);
    const [query, setQuery] = useState("");
    const [filters, setFilters] = useState(EMPTY_FILTERS);
    const [page, setPage] = useState(1);
    const [revision, setRevision] = useState(0);
    const load = () => setRevision((value) => value + 1);
    const subcategories = useMemo(() => {
        const ids = new Set(descendants(categories, filters.category));
        return categories.filter((row) => String(row.id) !== filters.category && ids.has(String(row.id)));
    }, [categories, filters.category]);
    const { category, subcategory } = filters;
    const categoryIds = descendants(categories, subcategory || category).join(",");
    const items = result?.items || [];
    const stats = result?.stats;

    useEffect(() => {
        if (accessMode) return;
        let active = true;
        getAiStoreFoundation().then((data) => { if (active) setFoundation(data); }).catch(() => {});
        return () => { active = false; };
    }, [accessMode]);

    useEffect(() => {
        let active = true;
        setCategoryError(false);
        getSallaCategoryCatalog().then(({ items: rows }) => {
            if (active) setCategories(rows || []);
        }).catch(() => { if (active) setCategoryError(true); });
        return () => { active = false; };
    }, [categoryRevision]);

    useEffect(() => {
        const controller = new AbortController();
        setLoading(true);
        const timer = setTimeout(async () => {
            try {
                const params = {
                    status, page, limit: 50,
                    q: query.trim() || undefined,
                    category_ids: categoryIds || undefined,
                    ...Object.fromEntries(Object.entries(filters).filter(([key, value]) => (
                        key !== "category" && key !== "subcategory" && value
                    ))),
                };
                const data = await listProductIntake(params, { signal: controller.signal });
                if (!controller.signal.aborted) setResult(data);
            } catch {
                if (!controller.signal.aborted) {
                    setResult(null);
                    toast.error("تعذّر تحميل قائمة استقبال المنتجات.");
                }
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        }, 250);
        return () => { clearTimeout(timer); controller.abort(); };
    }, [categoryIds, filters, page, query, revision, status]);

    function updateFilter(key, value) {
        setFilters((current) => ({ ...current, [key]: value, ...(key === "category" ? { subcategory: "" } : {}) }));
        setPage(1);
    }

    function openProduct(item) {
        const id = item.mezan_product_id || item.salla_product_id;
        navigate(`/products-v2?product=${encodeURIComponent(id)}`);
    }

    return (
        <div className="space-y-5" dir="rtl" data-testid="product-intake-workspace">
            <section className="overflow-hidden rounded-3xl border border-violet-200 bg-white shadow-sm">
                <div className="bg-gradient-to-l from-violet-700 to-violet-500 p-6 text-white">
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
                        <div>
                            <div className="flex items-center gap-2 text-sm font-black text-violet-100"><Robot size={22} weight="duotone" /> Mezan AI Store Operations</div>
                            <h1 className="mt-2 text-2xl font-black sm:text-3xl">قائمة استقبال المنتجات</h1>
                            <p className="mt-2 max-w-3xl text-sm leading-7 text-violet-100">ابحث في كتالوج سلة وتابع جاهزية المنتجات وربط المكونات والخدمات.</p>
                        </div>
                        <button type="button" onClick={load} disabled={loading} className="inline-flex items-center justify-center gap-2 rounded-xl bg-white px-4 py-3 font-black text-violet-800 shadow-sm disabled:opacity-60"><ArrowClockwise className={loading ? "animate-spin" : ""} /> تحديث القائمة</button>
                    </div>
                </div>
                <div className="border-t border-violet-100 bg-violet-50 px-5 py-3 text-xs font-bold text-violet-900">
                    <ShieldCheck className="ml-1 inline" /> القاعدة: المنتج لا يصبح جاهزًا لإدارة الذكاء الاصطناعي حتى تكتمل بياناته وتكاليفه ومكوناته الأساسية.
                </div>
            </section>

            {accessMode !== "read" && <ProductCreationPanel onCreated={load} draftOnly={accessMode === "write"} />}

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard label="المنتجات المعروضة" value={result?.total || 0} hint="حسب الفلتر الحالي" Icon={Package} />
                <StatCard label="متوسط الجاهزية" value={`${stats?.average_readiness || 0}%`} hint="درجة البيانات والتكاليف" Icon={Robot} tone="slate" />
                <StatCard label="بدون تكلفة أساسية" value={stats?.without_cost || 0} hint="لا يمكن حساب الربحية بدقة" Icon={WarningCircle} tone="amber" />
                <StatCard label="جاهزة بالكامل" value={stats?.ready || 0} hint="صالحة للتحليل والتنفيذ المقيد" Icon={CheckCircle} tone="emerald" />
            </section>

            <section className="rounded-2xl border bg-white p-4 shadow-sm">
                <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
                    <input value={query} maxLength={160} onChange={(event) => { setQuery(event.target.value); setPage(1); }} placeholder="ابحث باسم المنتج أو SKU أو رقم سلة…" className="h-12 rounded-xl border px-4 outline-none focus:border-violet-400" />
                    <div className="grid grid-cols-3 overflow-hidden rounded-xl border">
                        {[["needs_attention","تحتاج اهتمامًا"],["ready","جاهزة"],["all","الكل"]].map(([value, label]) => <button key={value} type="button" onClick={() => { setStatus(value); setPage(1); }} className={`px-4 py-3 text-sm font-black ${status === value ? "bg-violet-700 text-white" : "bg-white text-slate-700 hover:bg-violet-50"}`}>{label}</button>)}
                    </div>
                </div>
                <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    <label className="text-sm font-bold">القسم الرئيسي في سلة
                        <select value={category} onChange={(event) => updateFilter("category", event.target.value)} className="mt-1 block w-full rounded-xl border p-2.5">
                            <option value="">كل الأقسام</option>
                            {categories.filter((row) => !row.depth).map((row) => (
                                <option key={row.id} value={row.id}>{row.name}</option>
                            ))}
                        </select>
                    </label>
                    <label className="text-sm font-bold">القسم الفرعي
                        <select value={subcategory} disabled={!subcategories.length} onChange={(event) => updateFilter("subcategory", event.target.value)} className="mt-1 block w-full rounded-xl border p-2.5">
                            <option value="">كل الأقسام الفرعية</option>
                            {subcategories.map((row) => <option key={row.id} value={row.id}>{row.path}</option>)}
                        </select>
                    </label>
                    {FACETS.map(([key, label, options]) => <label key={key} className="text-sm font-bold">
                        {label}
                        <select value={filters[key]} onChange={(event) => updateFilter(key, event.target.value)} className="mt-1 block w-full rounded-xl border p-2.5">
                            <option value="">الكل</option>
                            {options.map(([value, name]) => <option key={value} value={value}>{name}</option>)}
                        </select>
                    </label>)}
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                    <button type="button" onClick={() => { setFilters(EMPTY_FILTERS); setQuery(""); setPage(1); }} className="rounded-lg border px-3 py-2 text-sm font-bold">
                        مسح الفلاتر
                    </button>
                    {categoryError && <span role="alert" className="text-sm text-rose-700">تعذّر تحميل تصنيفات سلة. <button type="button" onClick={() => setCategoryRevision((value) => value + 1)} className="underline">إعادة المحاولة</button></span>}
                </div>
            </section>

            <section className="space-y-3">
                {loading && <div className="rounded-2xl border bg-white p-10 text-center text-slate-500">جارٍ تحليل جاهزية المنتجات…</div>}
                {!loading && result && !items.length && <div className="rounded-2xl border border-dashed bg-white p-10 text-center"><CheckCircle size={42} className="mx-auto text-emerald-500" /><h2 className="mt-3 font-black">لا توجد منتجات مطابقة</h2><p className="mt-1 text-sm text-slate-500">غيّر الفلتر أو البحث لعرض منتجات أخرى.</p></div>}
                {!loading && items.map((item) => {
                    const readiness = item.readiness || {};
                    const checks = readiness.checks || {};
                    return (
                        <article key={item.mezan_product_id || item.salla_product_id} className="rounded-2xl border bg-white p-4 shadow-sm transition hover:border-violet-300">
                            <div className="flex flex-col gap-4 xl:flex-row xl:items-center">
                                <div className="flex min-w-0 flex-1 items-center gap-4">
                                    <img src={item.main_image || "/placeholder-product.png"} alt="" className="h-20 w-20 shrink-0 rounded-xl border object-cover" />
                                    <div className="min-w-0">
                                        <h2 className="truncate text-lg font-black text-slate-900">{item.name || "منتج بدون اسم"}</h2>
                                        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500"><span>SKU: {item.sku || "غير موجود"}</span><span>Salla: {item.salla_product_id || "—"}</span><span>الحالة: {item.status || "—"}</span></div>
                                    </div>
                                </div>
                                <ReadinessRing score={readiness.score} />
                                <div className="grid flex-1 gap-2 sm:grid-cols-2 lg:grid-cols-4 xl:max-w-2xl">
                                    {Object.entries(checks).map(([key, passed]) => <div key={key} className={`rounded-xl border px-3 py-2 text-xs font-bold ${passed ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"}`}>{passed ? "✓" : "!"} {LABELS[key] || key}</div>)}
                                </div>
                                {canOpenCatalogue && <button type="button" onClick={() => openProduct(item)} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-slate-900 px-5 py-3 font-black text-white hover:bg-violet-800"><Wrench /> معالجة المنتج</button>}
                            </div>
                            <div className="mt-3 flex flex-wrap gap-1.5 text-xs">
                                {Object.entries(item.links || {}).map(([key, linked]) => linked && LINK_LABELS[key] && (
                                    <span key={key} className="rounded-full bg-slate-100 px-2.5 py-1 font-bold text-slate-700">{LINK_LABELS[key]}</span>
                                ))}
                            </div>
                        </article>
                    );
                })}
            </section>

            {!loading && result?.total_pages > 1 && (
                <nav className="flex items-center justify-center gap-4 text-sm font-bold" aria-label="صفحات نتائج الاستقبال">
                    <button type="button" disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded-lg border p-2 disabled:opacity-50">السابق</button>
                    <span>{page} / {result.total_pages}</span>
                    <button type="button" disabled={page >= result.total_pages} onClick={() => setPage(page + 1)} className="rounded-lg border p-2 disabled:opacity-50">التالي</button>
                </nav>
            )}
            {foundation && <section className="rounded-2xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-950"><Tag className="ml-1 inline" /> وضع التشغيل: <b>{foundation.mode}</b> · مسار الأمان: {(foundation.safety_flow || []).join(" ← ")}</section>}
        </div>
    );
}
