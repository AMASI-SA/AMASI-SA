import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
    ArrowClockwise, CheckCircle, Package, Robot,
    ShieldCheck, Tag, WarningCircle, Wrench,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import { getAiStoreFoundation, listProductIntake } from "../services/aiStoreOperations";
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

export default function ProductIntakeWorkspace() {
    const navigate = useNavigate();
    const [status, setStatus] = useState("needs_attention");
    const [items, setItems] = useState([]);
    const [foundation, setFoundation] = useState(null);
    const [loading, setLoading] = useState(true);
    const [query, setQuery] = useState("");

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [intake, base] = await Promise.all([
                listProductIntake({ status, limit: 200 }),
                getAiStoreFoundation(),
            ]);
            setItems(intake.items || []);
            setFoundation(base);
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر تحميل قائمة استقبال المنتجات");
        } finally {
            setLoading(false);
        }
    }, [status]);

    useEffect(() => { load(); }, [load]);

    const filtered = useMemo(() => {
        const needle = query.trim().toLowerCase();
        if (!needle) return items;
        return items.filter((item) => `${item.name || ""} ${item.sku || ""} ${item.salla_product_id || ""}`.toLowerCase().includes(needle));
    }, [items, query]);

    const stats = useMemo(() => {
        const ready = items.filter((item) => item.readiness?.ready).length;
        const avg = items.length ? Math.round(items.reduce((sum, item) => sum + Number(item.readiness?.score || 0), 0) / items.length) : 0;
        const withoutCost = items.filter((item) => !item.readiness?.checks?.has_base_cost).length;
        return { ready, avg, withoutCost, total: items.length };
    }, [items]);

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
                            <p className="mt-2 max-w-3xl text-sm leading-7 text-violet-100">أنشئ المنتجات الجديدة من ميزان إلى سلة، ثم تابع اكتمال بيانات المنتجات وتكاليفها قبل إدارتها أو الترويج لها.</p>
                        </div>
                        <button type="button" onClick={load} disabled={loading} className="inline-flex items-center justify-center gap-2 rounded-xl bg-white px-4 py-3 font-black text-violet-800 shadow-sm disabled:opacity-60"><ArrowClockwise className={loading ? "animate-spin" : ""} /> تحديث القائمة</button>
                    </div>
                </div>
                <div className="border-t border-violet-100 bg-violet-50 px-5 py-3 text-xs font-bold text-violet-900">
                    <ShieldCheck className="ml-1 inline" /> القاعدة: المنتج لا يصبح جاهزًا لإدارة الذكاء الاصطناعي حتى تكتمل بياناته وتكاليفه ومكوناته الأساسية.
                </div>
            </section>

            <ProductCreationPanel onCreated={load} />

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard label="المنتجات المعروضة" value={stats.total} hint="حسب الفلتر الحالي" Icon={Package} />
                <StatCard label="متوسط الجاهزية" value={`${stats.avg}%`} hint="درجة البيانات والتكاليف" Icon={Robot} tone="slate" />
                <StatCard label="بدون تكلفة أساسية" value={stats.withoutCost} hint="لا يمكن حساب الربحية بدقة" Icon={WarningCircle} tone="amber" />
                <StatCard label="جاهزة بالكامل" value={stats.ready} hint="صالحة للتحليل والتنفيذ المقيد" Icon={CheckCircle} tone="emerald" />
            </section>

            <section className="rounded-2xl border bg-white p-4 shadow-sm">
                <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
                    <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث باسم المنتج أو SKU أو رقم سلة…" className="h-12 rounded-xl border px-4 outline-none focus:border-violet-400" />
                    <div className="grid grid-cols-3 overflow-hidden rounded-xl border">
                        {[["needs_attention","تحتاج اهتمامًا"],["ready","جاهزة"],["all","الكل"]].map(([value, label]) => <button key={value} type="button" onClick={() => setStatus(value)} className={`px-4 py-3 text-sm font-black ${status === value ? "bg-violet-700 text-white" : "bg-white text-slate-700 hover:bg-violet-50"}`}>{label}</button>)}
                    </div>
                </div>
            </section>

            <section className="space-y-3">
                {loading && <div className="rounded-2xl border bg-white p-10 text-center text-slate-500">جارٍ تحليل جاهزية المنتجات…</div>}
                {!loading && !filtered.length && <div className="rounded-2xl border border-dashed bg-white p-10 text-center"><CheckCircle size={42} className="mx-auto text-emerald-500" /><h2 className="mt-3 font-black">لا توجد منتجات مطابقة</h2><p className="mt-1 text-sm text-slate-500">غيّر الفلتر أو البحث لعرض منتجات أخرى.</p></div>}
                {!loading && filtered.map((item) => {
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
                                <button type="button" onClick={() => openProduct(item)} className="inline-flex shrink-0 items-center justify-center gap-2 rounded-xl bg-slate-900 px-5 py-3 font-black text-white hover:bg-violet-800"><Wrench /> معالجة المنتج</button>
                            </div>
                        </article>
                    );
                })}
            </section>

            {foundation && <section className="rounded-2xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-950"><Tag className="ml-1 inline" /> وضع التشغيل: <b>{foundation.mode}</b> · مسار الأمان: {(foundation.safety_flow || []).join(" ← ")}</section>}
        </div>
    );
}
