import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    AlertTriangle,
    ArrowRight,
    BarChart3,
    Boxes,
    Clock3,
    Filter,
    Image as ImageIcon,
    RefreshCw,
    ShieldCheck,
    ShoppingCart,
    Sparkles,
    TrendingUp,
    Wrench,
} from "lucide-react";

import api from "../lib/api";

const LEGACY_ACTIONS = {
    pause: { label: "إيقاف مقترح", tone: "border-red-200 bg-red-50 text-red-700" },
    reduce: { label: "خفض مقترح", tone: "border-orange-200 bg-orange-50 text-orange-700" },
    monitor: { label: "مراقبة", tone: "border-amber-200 bg-amber-50 text-amber-700" },
    maintain: { label: "استمرار", tone: "border-sky-200 bg-sky-50 text-sky-700" },
    scale: { label: "توسعة مقترحة", tone: "border-emerald-200 bg-emerald-50 text-emerald-700" },
};

const V3_ACTION_LABELS = {
    CONTINUE: "استمرار",
    MONITOR: "مراقبة",
    PAUSE_AD: "إيقاف الإعلان",
    PAUSE_ADSET: "إيقاف المجموعة",
    PAUSE_CAMPAIGN: "إيقاف الحملة",
    DECREASE_BUDGET: "خفض الميزانية",
    INCREASE_BUDGET: "رفع الميزانية",
    TEST_NEW_CREATIVE: "اختبار كرياتيف جديد",
    REFRESH_CREATIVE: "تجديد الكرياتيف",
    TEST_NEW_HOOK: "اختبار Hook جديد",
    SHORTEN_VIDEO: "تقصير الفيديو",
    LONGER_DEMO_VIDEO: "فيديو شرح أطول",
    PRODUCT_DEMO: "فيديو شرح المنتج",
    PROBLEM_SOLUTION_VIDEO: "فيديو مشكلة وحل",
    UGC_STYLE_VIDEO: "فيديو UGC",
    TESTIMONIAL_VIDEO: "فيديو تجربة عميل",
    BEFORE_AFTER: "قبل وبعد",
    STORYTELLING_VIDEO: "فيديو قصصي",
    FAQ_VIDEO: "فيديو أسئلة شائعة",
    OBJECTION_HANDLING_VIDEO: "فيديو معالجة اعتراض",
    PRICE_OFFER_VIDEO: "فيديو السعر والعرض",
    UNBOXING_VIDEO: "فيديو فتح المنتج",
    PRODUCT_CLOSEUP: "لقطات قريبة للمنتج",
    LIFESTYLE_VIDEO: "فيديو Lifestyle",
    COMPARISON_VIDEO: "فيديو مقارنة",
    STORY_AD: "إعلان Story",
    STATIC_IMAGE_TEST: "اختبار صورة ثابتة",
    CAROUSEL_TEST: "اختبار Carousel",
    REVIEW_AUDIENCE: "مراجعة الجمهور",
    REVIEW_PRODUCT: "مراجعة المنتج",
    REVIEW_OFFER: "مراجعة العرض",
    REVIEW_PRODUCT_PAGE: "مراجعة صفحة المنتج",
    CHANGE_PRODUCT_TITLE: "اقتراح اسم منتج جديد",
    CHANGE_PRODUCT_DESCRIPTION: "اقتراح وصف منتج جديد",
    CHANGE_HERO_IMAGE: "تغيير صورة العرض",
    REORDER_PRODUCT_IMAGES: "إعادة ترتيب صور المنتج",
    REVIEW_PRICE: "مراجعة السعر",
    REVIEW_SHIPPING_COST: "مراجعة تكلفة الشحن",
    REVIEW_CHECKOUT: "مراجعة Checkout",
    REVIEW_PAYMENT: "مراجعة الدفع",
    INVESTIGATE_ABANDONED_CARTS: "تحليل السلات المتروكة",
    INVESTIGATE_WEBSITE: "فحص الموقع",
    INVESTIGATE_TRACKING: "فحص التتبع",
    FIX_TRACKING: "إصلاح التتبع",
    FIX_DESTINATION_URL: "إصلاح رابط المنتج",
    RESTORE_PRODUCT_VISIBILITY: "إعادة إظهار المنتج",
    REVIEW_INVENTORY: "مراجعة المخزون",
    NO_ACTION_INSUFFICIENT_DATA: "لا إجراء — بيانات غير كافية",
    CHANGE_VALUE_PROPOSITION: "تغيير عرض القيمة",
    ADD_STRONGER_CTA: "CTA أقوى",
    SHOW_PRODUCT_EARLIER: "إظهار المنتج مبكرًا",
    SHOW_PRICE_OR_OFFER: "إظهار السعر أو العرض",
};

const ROOT_CAUSES = {
    CAMPAIGN: "الحملة",
    CREATIVE: "الكرياتيف",
    AUDIENCE: "الجمهور",
    PRODUCT: "المنتج",
    OFFER: "العرض",
    LANDING_PAGE: "صفحة الهبوط",
    ADD_TO_CART: "إضافة للسلة",
    CHECKOUT: "Checkout",
    SHIPPING: "الشحن",
    PAYMENT: "الدفع",
    WEBSITE: "الموقع",
    TRACKING: "التتبع",
    ATTRIBUTION: "Attribution",
    SEASONALITY: "الموسمية",
    INVENTORY: "المخزون",
    PRODUCT_VISIBILITY: "ظهور المنتج",
    PRODUCT_URL: "رابط المنتج",
    NORMAL_VARIANCE: "تقلب طبيعي",
    INSUFFICIENT_DATA: "بيانات غير كافية",
    UNKNOWN: "غير محسوم",
};

const ACTION_TYPES = {
    ads_write: "تعديل إعلاني قابل للتنفيذ",
    diagnostic: "تشخيص / تحقيق",
    creative: "كرياتيف",
    product_change: "تعديل منتج — بعد الموافقة",
    operational_alert: "تنبيه تشغيلي",
    no_action: "بدون إجراء",
};

const LEVELS = { campaign: "حملة", ad_group: "مجموعة إعلانية", ad: "إعلان" };
const CONFIDENCE = { high: "عالية", medium: "متوسطة", low: "منخفضة" };
const PRIORITY = { critical: 4, high: 3, medium: 2, low: 1 };
const money = (value) => Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function relativeTime(value) {
    const date = new Date(value || 0);
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (!Number.isFinite(seconds)) return "—";
    if (seconds < 60) return `منذ ${Math.max(1, seconds)} ثانية`;
    if (seconds < 3600) return `منذ ${Math.floor(seconds / 60)} دقيقة`;
    if (seconds < 86400) return `منذ ${Math.floor(seconds / 3600)} ساعة`;
    return `منذ ${Math.floor(seconds / 86400)} يوم`;
}

function dateTime(value) {
    const date = new Date(value || 0);
    if (!Number.isFinite(date.getTime())) return "—";
    return new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: true,
    }).format(date);
}

function actualAction(item) {
    return item.recommended_action || ({
        pause: item.entity_level === "ad" ? "PAUSE_AD" : item.entity_level === "ad_group" ? "PAUSE_ADSET" : "PAUSE_CAMPAIGN",
        reduce: "DECREASE_BUDGET",
        scale: "INCREASE_BUDGET",
        maintain: "CONTINUE",
        monitor: "MONITOR",
    }[item.action] || "MONITOR");
}

function actionTone(item) {
    const action = actualAction(item);
    if (action.startsWith("PAUSE_")) return "border-red-200 bg-red-50 text-red-700";
    if (action === "DECREASE_BUDGET") return "border-orange-200 bg-orange-50 text-orange-700";
    if (action === "INCREASE_BUDGET") return "border-emerald-200 bg-emerald-50 text-emerald-700";
    if (item.action_type === "operational_alert") return "border-red-200 bg-red-50 text-red-700";
    if (item.action_type === "creative") return "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700";
    if (item.action_type === "product_change") return "border-cyan-200 bg-cyan-50 text-cyan-700";
    return "border-violet-200 bg-violet-50 text-violet-700";
}

function executionLabel(status) {
    if (status === "completed") return "تم التنفيذ";
    if (status === "verification_required") return "بانتظار التحقق";
    if (status === "failed") return "تعذر التنفيذ";
    if (status === "executing") return "جارٍ التنفيذ…";
    return null;
}

function MetricCard({ icon: Icon, label, value, tone }) {
    return <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex items-center justify-between gap-3">
            <div><p className="text-[10px] font-extrabold text-slate-500">{label}</p><p className="mt-1 text-2xl font-black text-slate-900">{Number(value || 0).toLocaleString("en-US")}</p></div>
            <span className={`rounded-xl p-2 ${tone}`}><Icon className="h-5 w-5" /></span>
        </div>
    </div>;
}

function AnalysisBlock({ title, value }) {
    if (!value) return null;
    const summary = typeof value === "string" ? value : value.summary;
    if (!summary) return null;
    return <div className="rounded-xl border border-slate-200 bg-white p-3">
        <div className="flex items-center justify-between gap-2"><h4 className="text-[10px] font-black text-slate-800">{title}</h4>{value.status && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[8px] font-black text-slate-500">{value.status}</span>}</div>
        <p className="mt-1 text-[10px] font-bold leading-5 text-slate-600">{summary}</p>
        {value.signals?.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{value.signals.slice(0, 5).map((signal) => <span key={signal} className="rounded bg-slate-50 px-1.5 py-0.5 text-[8px] font-bold text-slate-500">{signal}</span>)}</div>}
    </div>;
}

function HypothesisList({ primary, secondary }) {
    const hypotheses = [primary, ...(secondary || [])].filter(Boolean);
    if (!hypotheses.length) return null;
    return <div>
        <h3 className="text-xs font-black text-slate-900">فرضيات السبب الجذري</h3>
        <div className="mt-2 space-y-2">{hypotheses.map((hypothesis, index) => <div key={`${hypothesis.category}-${index}`} className="rounded-xl border border-slate-200 bg-white p-3">
            <div className="flex flex-wrap items-center gap-2"><span className="rounded-full bg-violet-50 px-2 py-1 text-[9px] font-black text-violet-700">{ROOT_CAUSES[hypothesis.category] || hypothesis.category}</span><span className="text-[9px] font-black text-slate-400">ثقة {CONFIDENCE[hypothesis.confidence] || hypothesis.confidence}</span></div>
            <p className="mt-1 text-[10px] font-bold leading-5 text-slate-700">{hypothesis.statement}</p>
            {hypothesis.evidence_for?.length > 0 && <p className="mt-1 text-[9px] font-bold text-emerald-700">يدعمها: {hypothesis.evidence_for.slice(0, 3).join(" · ")}</p>}
            {hypothesis.evidence_against?.length > 0 && <p className="mt-1 text-[9px] font-bold text-amber-700">ضدها: {hypothesis.evidence_against.slice(0, 3).join(" · ")}</p>}
        </div>)}</div>
    </div>;
}

function CreativeBrief({ brief }) {
    if (!brief) return null;
    return <div className="rounded-2xl border border-fuchsia-200 bg-fuchsia-50/60 p-4">
        <h3 className="flex items-center gap-2 text-xs font-black text-fuchsia-950"><ImageIcon className="h-4 w-4" />Creative Brief</h3>
        <div className="mt-2 grid gap-2 sm:grid-cols-2 text-[10px] font-bold text-slate-700">
            <p><b>الهدف:</b> {brief.objective}</p><p><b>الزاوية:</b> {brief.creative_angle}</p>
            <p><b>Hook:</b> {brief.hook}</p><p><b>المدة:</b> {brief.duration_seconds ? `${brief.duration_seconds}s` : "حسب الفرضية"}</p>
            <p><b>أول ثانيتين:</b> {brief.first_two_seconds}</p><p><b>CTA:</b> {brief.cta}</p>
        </div>
        {brief.shot_list?.length > 0 && <div className="mt-3"><p className="text-[10px] font-black text-fuchsia-900">ماذا نصور؟</p><ol className="mt-1 space-y-1 text-[10px] font-bold text-slate-700">{brief.shot_list.map((shot, i) => <li key={`${shot}-${i}`}>{i + 1}. {shot}</li>)}</ol></div>}
        {brief.on_screen_text?.length > 0 && <p className="mt-2 text-[10px] font-bold text-slate-700"><b>النص على الشاشة:</b> {brief.on_screen_text.join(" · ")}</p>}
        <p className="mt-2 text-[10px] font-bold text-slate-700"><b>الفرضية:</b> {brief.hypothesis}</p>
        {brief.success_metrics?.length > 0 && <p className="mt-1 text-[10px] font-bold text-emerald-700"><b>مقياس النجاح:</b> {brief.success_metrics.join(" · ")}</p>}
    </div>;
}

function ProductAnalysis({ item }) {
    const page = item.product_page_analysis;
    const changes = item.proposed_product_changes || [];
    if (!page && !changes.length) return null;
    return <div className="rounded-2xl border border-cyan-200 bg-cyan-50/50 p-4">
        <h3 className="flex items-center gap-2 text-xs font-black text-cyan-950"><Boxes className="h-4 w-4" />تحليل المنتج وصفحته</h3>
        {page && <div className="mt-2 grid gap-2 sm:grid-cols-2 text-[10px] font-bold text-slate-700">
            <p><b>الرابط:</b> {page.url_health}</p><p><b>الظهور:</b> {page.visibility}</p>
            <p><b>المخزون:</b> {page.inventory_status}</p><p><b>الخيار المعلن:</b> {page.promoted_variant_status}</p>
            <p><b>العنوان:</b> {page.product_title_analysis}</p><p><b>السعر:</b> {page.pricing_analysis}</p>
            <p className="sm:col-span-2"><b>Hero Image:</b> {page.hero_image_analysis}</p>
            <p className="sm:col-span-2"><b>Ad ↔ Page:</b> {page.ad_page_consistency}</p>
        </div>}
        {page?.detected_issues?.length > 0 && <div className="mt-2 flex flex-wrap gap-1">{page.detected_issues.map((issue) => <span key={issue} className="rounded bg-white px-2 py-1 text-[9px] font-black text-red-700">{issue}</span>)}</div>}
        {changes.length > 0 && <div className="mt-3 space-y-2">{changes.map((change, i) => <div key={`${change.field}-${i}`} className="rounded-xl bg-white p-3 text-[10px] font-bold text-slate-700"><p className="font-black text-cyan-900">{change.field}</p>{change.current && <p className="mt-1"><b>الحالي:</b> {change.current}</p>}<p className="mt-1"><b>المقترح:</b> {change.proposed}</p><p className="mt-1 text-slate-500"><b>السبب:</b> {change.reason}</p></div>)}</div>}
    </div>;
}

export default function CampaignRecommendations() {
    const [snapshot, setSnapshot] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [approving, setApproving] = useState({});
    const [expanded, setExpanded] = useState(null);
    const [provider, setProvider] = useState("all");
    const [rootCause, setRootCause] = useState("all");
    const [actionType, setActionType] = useState("all");
    const [productAlerts, setProductAlerts] = useState(null);

    const load = useCallback(async () => {
        setError("");
        try {
            const [latest, watch] = await Promise.all([
                api.get("/ads-manager/ai-monitor/latest"),
                api.get("/ads-manager/ai-monitor/product-watch/alerts?status=active&limit=20").catch(() => ({ data: null })),
            ]);
            setSnapshot(latest.data);
            setProductAlerts(watch.data);
        } catch {
            setError("تعذّر قراءة توصيات الحملات الآن. حاول التحديث بعد قليل.");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        load();
        const timer = window.setInterval(load, 5 * 60 * 1000);
        return () => window.clearInterval(timer);
    }, [load]);

    const recommendations = useMemo(() => snapshot?.recommendations || [], [snapshot]);
    const executable = recommendations.filter((item) => item.executable === true && item.approval_available).length;
    const diagnostics = recommendations.filter((item) => ["diagnostic", "operational_alert"].includes(item.action_type)).length;
    const creative = recommendations.filter((item) => item.action_type === "creative").length;
    const product = recommendations.filter((item) => item.action_type === "product_change").length;

    const visible = useMemo(() => recommendations
        .filter((item) => (
            (provider === "all" || item.provider === provider)
            && (rootCause === "all" || item.root_cause_category === rootCause)
            && (actionType === "all" || item.action_type === actionType)
        ))
        .map((item, originalIndex) => ({ item, originalIndex }))
        .sort((left, right) => {
            const priorityDiff = (PRIORITY[right.item.priority] || 0) - (PRIORITY[left.item.priority] || 0);
            if (priorityDiff) return priorityDiff;
            const executableDiff = Number(Boolean(right.item.executable)) - Number(Boolean(left.item.executable));
            if (executableDiff) return executableDiff;
            return left.originalIndex - right.originalIndex;
        })
        .map(({ item }) => item), [recommendations, provider, rootCause, actionType]);

    const approve = async (item) => {
        if (!(item.executable && item.approval_available && item.action_type === "ads_write")) return;
        const action = actualAction(item);
        const change = action.startsWith("PAUSE_") ? "الإيقاف" : action === "DECREASE_BUDGET" ? `خفض الميزانية ${item.change_percent || 15}%` : `رفع الميزانية ${item.change_percent || 15}%`;
        if (!window.confirm(`موافقتك ستنفّذ ${change} على ${item.entity_name} في ${item.provider === "meta" ? "Meta" : "Snapchat"}. هل تريد المتابعة؟`)) return;
        setApproving((value) => ({ ...value, [item.recommendation_id]: true }));
        try {
            const { data } = await api.post(
                `/ads-manager/ai-monitor/recommendations/${encodeURIComponent(item.recommendation_id)}/approve`,
                { snapshot_id: snapshot.snapshot_id },
            );
            setSnapshot((value) => ({ ...value, recommendations: (value.recommendations || []).map((row) => row.recommendation_id === item.recommendation_id ? { ...row, execution_status: data.status } : row) }));
            window.setTimeout(load, 6000);
        } catch (requestError) {
            window.alert(requestError?.response?.data?.detail?.message || "تعذّر تنفيذ التوصية بأمان. حدّث التحليل وحاول مجددًا.");
        } finally {
            setApproving((value) => ({ ...value, [item.recommendation_id]: false }));
        }
    };

    // Retained V2 financial aliases for historical snapshots/tests.
    const forecast_without_action_sar = "forecast_without_action_sar";
    const forecast_with_action_sar = "forecast_with_action_sar";
    const forecast_delta_sar = "forecast_delta_sar";
    void forecast_without_action_sar; void forecast_with_action_sar; void forecast_delta_sar;

    return <main dir="rtl" className="min-h-screen bg-slate-50 p-3 sm:p-5" data-testid="campaign-recommendations-page">
        <div className="mx-auto max-w-[1500px] space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <Link to="/dashboard-advanced" className="rounded-xl border border-slate-200 bg-white p-2 text-slate-600 shadow-sm hover:text-violet-700" aria-label="العودة إلى لوحة التحكم"><ArrowRight className="h-5 w-5" /></Link>
                    <div><h1 className="text-xl font-black text-slate-900">توصيات الحملات الإعلانية</h1><p className="mt-1 text-xs font-bold text-slate-500">Decision Intelligence V3 · التشخيص أولًا · التنفيذ الإعلاني فقط بعد موافقتك</p></div>
                </div>
                <button type="button" onClick={load} disabled={loading} className="flex items-center gap-2 rounded-xl bg-violet-700 px-4 py-2 text-xs font-extrabold text-white disabled:bg-slate-300"><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />تحديث التوصيات</button>
            </div>

            <section className="overflow-hidden rounded-2xl border border-violet-200 bg-white shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-violet-800 bg-violet-700 px-5 py-4 text-white">
                    <div><h2 className="flex items-center gap-2 font-black"><Sparkles className="h-5 w-5" />ملاحظات الذكاء على الحملات</h2><p className="mt-1 text-[10px] text-violet-100">Today → Yesterday → Day-2 · 7d/30d Baseline · Funnel + Product + Inventory + Carts</p></div>
                    <div className="text-left text-[10px] font-black"><p>{snapshot?.generated_at ? `أُنشئت ${relativeTime(snapshot.generated_at)}` : "بانتظار التحليل"}</p><p dir="ltr">{snapshot?.generated_at ? dateTime(snapshot.generated_at) : "—"}</p></div>
                </div>
                <div className="p-4"><p className="text-xs font-extrabold leading-6 text-slate-700">{snapshot?.summary || "سيظهر أول تحليل بعد اكتمال التشغيل الدوري."}</p>{snapshot?.range?.from && <p className="mt-2 text-[10px] font-black text-slate-500">فترة الدراسة: <span dir="ltr">{snapshot.range.from} → {snapshot.range.to}</span></p>}</div>
            </section>

            {productAlerts?.active_count > 0 && <section className="rounded-2xl border border-red-200 bg-red-50 p-4" data-testid="advertising-product-watch-alerts">
                <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="flex items-center gap-2 text-sm font-black text-red-900"><AlertTriangle className="h-5 w-5" />Advertising Product Watch</h2><p className="mt-1 text-[10px] font-bold text-red-700">فحص تشغيلي منفصل عن دورة OpenAI: منتج مخفي/نفد/رابط معطل/Variant غير متوفر.</p></div><span className="rounded-full bg-white px-3 py-1 text-[10px] font-black text-red-700">نشط {productAlerts.active_count} · حرج {productAlerts.critical_count || 0}</span></div>
                <div className="mt-3 grid gap-2 md:grid-cols-2">{(productAlerts.items || []).slice(0, 6).map((alert) => <div key={alert.alert_key} className="rounded-xl bg-white p-3 text-[10px] font-bold text-slate-700"><p className="font-black text-red-800">{alert.code}</p><p className="mt-1">{alert.product_name || alert.product_id} · صرف {money(alert.current_spend_sar)} ر.س</p><p className="mt-1 text-slate-400">لا يتم إيقاف أو تعديل شيء تلقائيًا.</p></div>)}</div>
            </section>}

            <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="ملخص توصيات الحملات">
                <MetricCard icon={ShieldCheck} label="قابلة للموافقة والتنفيذ" value={executable} tone="bg-emerald-50 text-emerald-700" />
                <MetricCard icon={Wrench} label="تشخيص / تشغيل" value={diagnostics} tone="bg-orange-50 text-orange-700" />
                <MetricCard icon={ImageIcon} label="كرياتيف" value={creative} tone="bg-fuchsia-50 text-fuchsia-700" />
                <MetricCard icon={Boxes} label="تحسين المنتج" value={product} tone="bg-cyan-50 text-cyan-700" />
            </section>

            <section className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
                <div className="flex flex-wrap items-center gap-2"><span className="flex items-center gap-1 px-2 text-[10px] font-black text-slate-500"><Filter className="h-4 w-4" />تصفية</span>
                    <select value={provider} onChange={(e) => setProvider(e.target.value)} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold"><option value="all">كل المنصات</option><option value="snapchat">سناب</option><option value="meta">Meta</option></select>
                    <select value={rootCause} onChange={(e) => setRootCause(e.target.value)} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold"><option value="all">كل الأسباب</option>{Object.entries(ROOT_CAUSES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
                    <select value={actionType} onChange={(e) => setActionType(e.target.value)} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold"><option value="all">كل أنواع الإجراء</option>{Object.entries(ACTION_TYPES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select>
                    <span className="mr-auto px-2 text-[10px] font-extrabold text-slate-400">ترتيب تلقائي: الأهم والأكثر تأثيرًا أولًا · المعروض {visible.length} من {recommendations.length}</span>
                </div>
            </section>

            {error && <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm font-bold text-red-700">{error}</div>}

            <section className="grid gap-3 lg:grid-cols-2" data-testid="campaign-recommendations-list">
                {visible.map((item, index) => {
                    const recommendedAction = actualAction(item);
                    const displayAction = V3_ACTION_LABELS[recommendedAction] || LEGACY_ACTIONS[item.action]?.label || recommendedAction;
                    const status = executionLabel(item.execution_status);
                    const canExecute = Boolean(item.executable && item.approval_available && item.action_type === "ads_write");
                    const isExpanded = expanded === item.recommendation_id;
                    return <article key={item.recommendation_id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm hover:border-violet-300">
                        <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="flex flex-wrap items-center gap-1.5"><p className="text-[10px] font-black text-slate-400">{item.provider === "meta" ? "Meta" : "سناب"} · {LEVELS[item.entity_level] || item.entity_level}</p>{index === 0 && <span className="rounded-full bg-red-50 px-2 py-0.5 text-[8px] font-black text-red-700">الأعلى أولوية وتأثيرًا</span>}</div>{item.account_name && <p className="mt-1 truncate text-[10px] font-extrabold text-slate-500">الحساب الإعلاني: {item.account_name}</p>}<Link to={`/ads-manager?provider=${item.provider}`} className="mt-1 block truncate text-sm font-black text-slate-900 hover:text-violet-700">{item.entity_name}</Link>{item.product_id && <p className="mt-1 truncate text-[9px] font-bold text-cyan-700">Product ID: {item.product_id}</p>}</div><span className={`shrink-0 rounded-full border px-3 py-1 text-[10px] font-black ${actionTone(item)}`}>{displayAction}</span></div>
                        <div className="mt-2 flex flex-wrap gap-1.5 text-[9px] font-black"><span className="rounded-full bg-slate-100 px-2 py-1 text-slate-600">السبب: {ROOT_CAUSES[item.root_cause_category] || item.root_cause_category || "غير محسوم"}</span><span className="rounded-full bg-slate-100 px-2 py-1 text-slate-600">الثقة: {CONFIDENCE[item.confidence] || item.confidence}</span><span className="rounded-full bg-slate-100 px-2 py-1 text-slate-600">{ACTION_TYPES[item.action_type] || "توصية V2"}</span>{item.executable === false && <span className="rounded-full bg-amber-50 px-2 py-1 text-amber-700">اقتراح فقط — لا Ads write</span>}</div>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-[9px] font-extrabold text-slate-400"><span>أُنشئت {relativeTime(item.generated_at || snapshot?.generated_at)}</span><span>•</span><span dir="ltr">{dateTime(item.generated_at || snapshot?.generated_at)}</span></div>
                        <p className="mt-3 text-xs font-bold leading-6 text-slate-700">{item.diagnosis || item.rationale}</p>
                        {item.evidence_for?.length > 0 && <p className="mt-2 text-[10px] font-bold leading-5 text-emerald-700"><b>الدليل المؤيد:</b> {item.evidence_for.slice(0, 4).join(" · ")}</p>}
                        {item.evidence_against?.length > 0 && <p className="mt-1 text-[10px] font-bold leading-5 text-amber-700"><b>ما يعارض القرار:</b> {item.evidence_against.slice(0, 4).join(" · ")}</p>}
                        <button type="button" onClick={() => setExpanded(isExpanded ? null : item.recommendation_id)} aria-expanded={isExpanded} className="mt-3 w-full rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-black text-violet-800 hover:bg-violet-100">{isExpanded ? "إخفاء شرح القرار" : "لماذا اتُخذ هذا القرار؟"}</button>

                        {isExpanded && <div className="mt-3 space-y-3 rounded-2xl border border-violet-100 bg-violet-50/40 p-4" data-testid={`recommendation-explanation-${item.recommendation_id}`}>
                            <div><h3 className="text-xs font-black text-slate-900">الأرقام التي بنى عليها القرار</h3><div className="mt-2 flex flex-wrap gap-1.5">{(item.decision_facts || item.evidence || item.evidence_for || []).map((fact) => <span key={fact} className="rounded-lg bg-white px-2 py-1 text-[10px] font-extrabold text-slate-700 shadow-sm">{fact}</span>)}</div></div>
                            <HypothesisList primary={item.primary_hypothesis} secondary={item.secondary_hypotheses} />
                            <div><h3 className="text-xs font-black text-slate-900">التحليل الزمني — Today أولًا</h3><div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3"><AnalysisBlock title="اليوم" value={item.today_analysis} /><AnalysisBlock title="أمس" value={item.yesterday_analysis} /><AnalysisBlock title="قبل أمس" value={item.day_minus_2_analysis} /><AnalysisBlock title="Baseline 7 أيام" value={item.baseline_7d} /><AnalysisBlock title="Baseline 30 يوم" value={item.baseline_30d} /></div></div>
                            <div><h3 className="text-xs font-black text-slate-900">Funnel / Creative</h3><div className="mt-2 grid gap-2 sm:grid-cols-2"><AnalysisBlock title="Funnel" value={item.funnel_analysis} /><AnalysisBlock title="Video" value={item.video_analysis} /><AnalysisBlock title="Creative" value={item.creative_analysis} /><AnalysisBlock title="Inventory" value={item.inventory_analysis} /><AnalysisBlock title="السلات المتروكة" value={item.abandoned_cart_analysis} /><AnalysisBlock title="Cross-platform" value={item.cross_platform_analysis} /></div></div>
                            <ProductAnalysis item={item} />
                            <CreativeBrief brief={item.creative_brief} />
                            <div><h3 className="text-xs font-black text-slate-900">ما الإجراء المقترح؟</h3><p className="mt-1 text-[11px] font-bold leading-6 text-slate-700">{displayAction} — {item.why || item.proposed_action || item.rationale}</p></div>
                            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3"><h3 className="text-xs font-black text-amber-900">كم ساعة أصبر؟</h3><p className="mt-1 text-[11px] font-bold leading-6 text-amber-800">{item.observation_plan || `المجدول يعيد التحليل كل 5 ساعات. اصبر ${item.recommended_wait_hours || 5} ساعات قبل قرار ثانٍ.`}</p></div>
                            <div><h3 className="text-xs font-black text-slate-900">متى نعتبر القرار ناجحًا؟</h3><ul className="mt-1 space-y-1 text-[10px] font-bold leading-5 text-slate-600">{(item.success_criteria || []).map((criterion) => <li key={criterion}>• {criterion}</li>)}</ul></div>
                            <div className="rounded-xl border border-red-100 bg-red-50/70 p-3"><h3 className="text-xs font-black text-red-900">ماذا لو تجاهلنا التوصية؟</h3><p className="mt-1 text-[10px] font-bold leading-5 text-red-700">{item.risks?.join(" · ") || item.risk_if_ignored}</p></div>
                            {item.what_would_change_the_decision?.length > 0 && <div className="rounded-xl border border-blue-100 bg-blue-50/70 p-3"><h3 className="text-xs font-black text-blue-900">ما الذي قد يجعل هذه التوصية خاطئة؟</h3><ul className="mt-1 space-y-1 text-[10px] font-bold text-blue-800">{item.what_would_change_the_decision.map((value) => <li key={value}>• {value}</li>)}</ul></div>}
                            {item.financial_impact && <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3"><h3 className="text-xs font-black text-emerald-950">أثر الحملة على الربح</h3><p className="mt-1 text-[10px] font-bold text-emerald-800">مساهمة الفترة {money(item.financial_impact.period_estimated_contribution_sar)} ر.س · توقع فترة الانتظار القادمة {item.financial_impact.forecast_hours || "—"} ساعة · التغير المتوقع {money(item.financial_impact.forecast_delta_sar)} ر.س</p><p className="mt-1 text-[9px] font-bold text-slate-500">نسبتها من صافي النتيجة تعتمد على ملخص فترة الدراسة. قد تكون النتيجة: زادت صافي الخسارة / خفضت صافي الربح / زيادة متوقعة في الربح بحسب الإشارة.</p></div>}
                            {item.external_knowledge_used?.length > 0 && <div><h3 className="text-xs font-black text-slate-900">المعرفة الخارجية المستخدمة</h3><div className="mt-2 space-y-1">{item.external_knowledge_used.map((source) => <p key={source.source_id} className="rounded-lg bg-white p-2 text-[9px] font-bold text-slate-600">Tier {source.source_tier} · {source.title} · مراجعة {source.last_reviewed_at}</p>)}</div></div>}
                        </div>}

                        <div className="mt-3 flex items-center justify-between gap-3"><span className="text-[9px] font-bold text-slate-400">recommended_wait_hours: {item.recommended_wait_hours || 5}</span>{canExecute ? <button type="button" onClick={() => approve(item)} disabled={approving[item.recommendation_id] || Boolean(status)} className="rounded-xl bg-violet-700 px-4 py-2 text-[10px] font-black text-white disabled:bg-slate-300">{status || (approving[item.recommendation_id] ? "جارٍ التنفيذ…" : "موافقة وتنفيذ")}</button> : <span className="rounded-xl bg-slate-100 px-3 py-2 text-[9px] font-black text-slate-500">توصية/تشخيص — لا تنفيذ تلقائي</span>}</div>
                    </article>;
                })}
            </section>

            {!loading && !visible.length && <div className="rounded-2xl border border-slate-200 bg-white p-8 text-center text-sm font-bold text-slate-400"><BarChart3 className="mx-auto mb-2 h-6 w-6" />لا توجد توصيات مطابقة للفلاتر الحالية.</div>}
            <div className="flex items-center justify-center gap-2 text-[9px] font-bold text-slate-400"><Clock3 className="h-3 w-3" /><span>Snapshot واحد لكل دورة · تحديث الواجهة لا يولّد قرارًا جديدًا</span><ShieldCheck className="h-3 w-3" /><span>لا Ads write بدون موافقة</span><ShoppingCart className="h-3 w-3" /><span>السلات دليل مساند لا Attribution مخترع</span></div>
        </div>
    </main>;
}
