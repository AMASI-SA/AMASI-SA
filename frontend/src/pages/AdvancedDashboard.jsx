import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
    AlertTriangle, ArrowRight, BarChart3, BriefcaseBusiness, ChevronDown, ChevronLeft,
    CircleDollarSign, Clock3, CreditCard, Instagram, Megaphone, PackageOpen,
    RefreshCw, ShieldCheck, ShoppingBag, ShoppingCart, Sparkles, TrendingUp, Truck, Trophy, UsersRound,
} from "lucide-react";
import { User } from "@phosphor-icons/react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import api from "../lib/api";
import AdvancedFilters, { defaultFilters, filtersToQueryString } from "../components/AdvancedFilters";
import AdsExecutiveBreakdownTable from "../components/AdsExecutiveBreakdownTable";
import { buildPaymentFeeRows } from "../components/ProfitSummaryCard";
import { useOrders } from "../hooks/useOrders";
import { buildMissingMezanCostHref } from "../lib/mezanV2CostLinks";
import {
    DASHBOARD_AUTO_REFRESH_MS,
    dashboardOrdersSignature,
    shouldRefreshDashboardForOrders,
} from "../lib/dashboardLiveRefresh";

const PLATFORM_META = [
    { key: "snapchat", label: "سناب شات", color: "#f59e0b" },
    { key: "tiktok", label: "تيك توك", color: "#111827" },
    { key: "meta", label: "Meta", color: "#2563eb" },
    { key: "google", label: "Google Ads", color: "#059669" },
];

const money = (value) => Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const integer = (value) => Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });
const CARTS_AUTO_REFRESH_MS = 15_000;

function finiteFinancialValue(value, { nonnegative = false } = {}) {
    if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || (nonnegative && parsed < 0)) return null;
    return parsed;
}

function optionalMoney(value) {
    const parsed = finiteFinancialValue(value);
    return parsed === null ? "—" : money(parsed);
}

export function dashboardSpendDisplay(value, dataState = "") {
    const parsed = finiteFinancialValue(value, { nonnegative: true });
    const state = String(dataState || "");
    if (state === "confirmed_no_data") return parsed === null ? "لا توجد بيانات" : "غير مكتمل";
    if (state === "unknown_incomplete" || state === "incomplete") return "غير مكتمل";
    if (state === "not_connected") return parsed === 0 ? "غير متصل" : "غير مكتمل";
    if (state === "confirmed_zero") return parsed === 0 ? money(0) : "غير مكتمل";
    if (state === "confirmed_data") return parsed !== null && parsed > 0 ? money(parsed) : "غير مكتمل";
    return parsed === null ? "غير مكتمل" : money(parsed);
}

export function aggregateDashboardAdsHistoryByMonth(daily = []) {
    const grouped = {};
    daily.forEach((row) => {
        const key = String(row?.date || "").slice(0, 7);
        if (!key) return;
        grouped[key] ||= { label: key, snapchat: 0, tiktok: 0, meta: 0, google: 0 };
        PLATFORM_META.forEach(({ key: provider }) => {
            const amount = finiteFinancialValue(row?.[provider], { nonnegative: true });
            if (amount === null || grouped[key][provider] === null) {
                grouped[key][provider] = null;
            } else {
                grouped[key][provider] += amount;
            }
        });
    });
    return Object.values(grouped);
}

function Panel({ children, className = "", testid }) {
    return <section data-testid={testid} className={`overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm ${className}`}>{children}</section>;
}

const AI_ACTIONS = {
    CONTINUE: { label: "استمرار", tone: "border-sky-200 bg-sky-50 text-sky-700" },
    MONITOR: { label: "مراقبة", tone: "border-amber-200 bg-amber-50 text-amber-700" },
    NO_ACTION_INSUFFICIENT_DATA: { label: "بيانات غير كافية", tone: "border-slate-200 bg-slate-50 text-slate-600" },
    PAUSE_AD: { label: "إيقاف الإعلان", tone: "border-red-200 bg-red-50 text-red-700" },
    PAUSE_ADSET: { label: "إيقاف المجموعة", tone: "border-red-200 bg-red-50 text-red-700" },
    PAUSE_CAMPAIGN: { label: "إيقاف الحملة", tone: "border-red-200 bg-red-50 text-red-700" },
    DECREASE_BUDGET: { label: "خفض الميزانية", tone: "border-orange-200 bg-orange-50 text-orange-700" },
    INCREASE_BUDGET: { label: "زيادة الميزانية", tone: "border-emerald-200 bg-emerald-50 text-emerald-700" },
    TEST_NEW_CREATIVE: { label: "اختبار إبداع جديد", tone: "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700" },
    REFRESH_CREATIVE: { label: "تحديث الإبداع", tone: "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700" },
    TEST_NEW_HOOK: { label: "اختبار خطاف جديد", tone: "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700" },
    STORY_AD: { label: "إعلان ستوري", tone: "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700" },
    REVIEW_AUDIENCE: { label: "مراجعة الجمهور", tone: "border-violet-200 bg-violet-50 text-violet-700" },
    REVIEW_PRODUCT: { label: "مراجعة المنتج", tone: "border-violet-200 bg-violet-50 text-violet-700" },
    REVIEW_OFFER: { label: "مراجعة العرض", tone: "border-violet-200 bg-violet-50 text-violet-700" },
    REVIEW_PRODUCT_PAGE: { label: "مراجعة صفحة المنتج", tone: "border-violet-200 bg-violet-50 text-violet-700" },
    CHANGE_PRODUCT_TITLE: { label: "تغيير اسم المنتج", tone: "border-indigo-200 bg-indigo-50 text-indigo-700" },
    CHANGE_PRODUCT_DESCRIPTION: { label: "تغيير وصف المنتج", tone: "border-indigo-200 bg-indigo-50 text-indigo-700" },
    CHANGE_HERO_IMAGE: { label: "تغيير صورة العرض", tone: "border-indigo-200 bg-indigo-50 text-indigo-700" },
    REORDER_PRODUCT_IMAGES: { label: "ترتيب صور المنتج", tone: "border-indigo-200 bg-indigo-50 text-indigo-700" },
    REVIEW_PRICE: { label: "مراجعة السعر", tone: "border-indigo-200 bg-indigo-50 text-indigo-700" },
    REVIEW_SHIPPING_COST: { label: "مراجعة تكلفة الشحن", tone: "border-violet-200 bg-violet-50 text-violet-700" },
    REVIEW_CHECKOUT: { label: "مراجعة إتمام الطلب", tone: "border-violet-200 bg-violet-50 text-violet-700" },
    REVIEW_PAYMENT: { label: "مراجعة الدفع", tone: "border-violet-200 bg-violet-50 text-violet-700" },
    INVESTIGATE_ABANDONED_CARTS: { label: "فحص السلات المتروكة", tone: "border-violet-200 bg-violet-50 text-violet-700" },
    INVESTIGATE_WEBSITE: { label: "فحص المتجر", tone: "border-rose-200 bg-rose-50 text-rose-700" },
    INVESTIGATE_TRACKING: { label: "فحص التتبع", tone: "border-violet-200 bg-violet-50 text-violet-700" },
    FIX_TRACKING: { label: "إصلاح التتبع", tone: "border-rose-200 bg-rose-50 text-rose-700" },
    FIX_DESTINATION_URL: { label: "إصلاح رابط المنتج", tone: "border-rose-200 bg-rose-50 text-rose-700" },
    RESTORE_PRODUCT_VISIBILITY: { label: "إظهار المنتج", tone: "border-rose-200 bg-rose-50 text-rose-700" },
    REVIEW_INVENTORY: { label: "مراجعة المخزون", tone: "border-teal-200 bg-teal-50 text-teal-700" },
    RESTOCK_PRODUCT: { label: "إعادة توريد المنتج", tone: "border-teal-200 bg-teal-50 text-teal-700" },
    EXTEND_PROMOTION: { label: "تمديد العرض المقترح", tone: "border-indigo-200 bg-indigo-50 text-indigo-700" },
};

const AI_LEVELS = { campaign: "حملة", ad_group: "مجموعة", ad: "إعلان" };
const AI_ROOT_CAUSES = {
    CAMPAIGN: "الحملة", CREATIVE: "الإبداع", AUDIENCE: "الجمهور", PRODUCT: "المنتج",
    OFFER: "العرض", LANDING_PAGE: "صفحة الهبوط", ADD_TO_CART: "الإضافة للسلة",
    CHECKOUT: "إتمام الطلب", SHIPPING: "الشحن", PAYMENT: "الدفع", WEBSITE: "المتجر",
    TRACKING: "التتبع", ATTRIBUTION: "الإسناد", SEASONALITY: "الموسمية", INVENTORY: "المخزون",
    PRODUCT_VISIBILITY: "ظهور المنتج", PRODUCT_URL: "رابط المنتج", NORMAL_VARIANCE: "تذبذب طبيعي",
    INSUFFICIENT_DATA: "بيانات غير كافية", UNKNOWN: "غير محسوم",
};

function aiActionMeta(item) {
    const key = String(item?.recommended_action || "MONITOR");
    return AI_ACTIONS[key] || {
        label: key.replaceAll("_", " "),
        tone: "border-violet-200 bg-violet-50 text-violet-700",
    };
}

export function CampaignAdvisorCard() {
    const [snapshot, setSnapshot] = useState(null);
    const [unifiedShadow, setUnifiedShadow] = useState(null);
    const [loading, setLoading] = useState(true);
    const [approving, setApproving] = useState({});
    useEffect(() => {
        let active = true;
        const load = async () => {
            try {
                const [latestResult, shadowResult] = await Promise.allSettled([
                    api.get("/ads-manager/ai-monitor/latest"),
                    api.get("/ads-manager/ai-monitor/unified-shadow?days=1"),
                ]);
                if (active && latestResult.status === "fulfilled") setSnapshot(latestResult.value.data);
                if (active && shadowResult.status === "fulfilled") setUnifiedShadow(shadowResult.value.data);
            } catch {
                if (active) setSnapshot(null);
            } finally {
                if (active) setLoading(false);
            }
        };
        load();
        const timer = window.setInterval(load, 5 * 60 * 1000);
        return () => { active = false; window.clearInterval(timer); };
    }, []);
    const recommendations = snapshot?.recommendations || [];
    const visible = recommendations.slice(0, 5);
    const urgent = recommendations.filter((item) => ["PAUSE_AD", "PAUSE_ADSET", "PAUSE_CAMPAIGN", "DECREASE_BUDGET"].includes(item.recommended_action)).length;
    const scale = recommendations.filter((item) => item.recommended_action === "INCREASE_BUDGET").length;
    const approve = async (item) => {
        const canApprove = item.action_type === "ads_write" && item.executable && item.approval_available;
        if (!canApprove) return;
        const action = item.recommended_action;
        const change = action?.startsWith("PAUSE_") ? "الإيقاف" : action === "DECREASE_BUDGET" ? `خفض الميزانية ${item.change_percent || 15}%` : `رفع الميزانية ${item.change_percent || 15}%`;
        if (!window.confirm(`موافقتك ستنفّذ ${change} على ${item.entity_name} في ${item.provider === "meta" ? "Meta" : "Snapchat"}. هل تريد المتابعة؟`)) return;
        setApproving((value) => ({ ...value, [item.recommendation_id]: true }));
        try {
            const { data } = await api.post(`/ads-manager/ai-monitor/recommendations/${encodeURIComponent(item.recommendation_id)}/approve`, { snapshot_id: snapshot.snapshot_id });
            setSnapshot((value) => ({ ...value, recommendations: (value.recommendations || []).map((row) => row.recommendation_id === item.recommendation_id ? { ...row, execution_status: data.status } : row) }));
            window.setTimeout(async () => {
                try {
                    const latest = await api.get("/ads-manager/ai-monitor/latest");
                    setSnapshot(latest.data);
                } catch { /* the regular five-minute refresh remains available */ }
            }, 6000);
        } catch (error) {
            window.alert(error?.response?.data?.detail?.message || "تعذّر تنفيذ التوصية بأمان. حدّث التحليل وحاول مجددًا.");
        } finally {
            setApproving((value) => ({ ...value, [item.recommendation_id]: false }));
        }
    };
    return <Panel className="border-violet-200" testid="advanced-campaign-ai-advisor">
        <div className="flex min-h-14 flex-wrap items-center justify-between gap-2 border-b border-violet-800 bg-violet-700 px-4 py-3 text-white">
            <div>
                <h2 className="flex items-center gap-2 font-extrabold"><Sparkles className="h-5 w-5" />ملاحظات الذكاء على الحملات</h2>
                <p className="mt-1 text-[10px] text-violet-100">سناب وMeta · قرار مستقل كل 5 ساعات · التشخيص منفصل عن التنفيذ</p>
            </div>
            <div className="flex items-center gap-2 text-[10px] font-bold">
                <span className="rounded-full bg-white/15 px-2 py-1">هدر محتمل {integer(urgent)}</span>
                <span className="rounded-full bg-white/15 px-2 py-1">فرص توسعة {integer(scale)}</span>
            </div>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-violet-50/60 px-4 py-2 text-[10px] text-violet-900">
            <span className="font-bold">{loading ? "جارٍ قراءة آخر تحليل…" : (snapshot?.summary || "سيظهر أول تحليل بعد اكتمال التشغيل الدوري.")}</span>
            <div className="flex items-center gap-3 whitespace-nowrap text-violet-600">
                <span className="flex items-center gap-1"><Clock3 className="h-3 w-3" />{snapshot?.generated_at ? relativeTime(snapshot.generated_at) : "بانتظار أول تشغيل"}</span>
                <span className="flex items-center gap-1"><ShieldCheck className="h-3 w-3" />Ads write فقط بعد موافقتك</span>
            </div>
        </div>
        {unifiedShadow && <div className={`border-b px-4 py-2 text-[10px] font-extrabold ${unifiedShadow.shadow_passed ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-amber-200 bg-amber-50 text-amber-800"}`} data-testid="campaign-ai-unified-shadow-status">
            {unifiedShadow.shadow_passed
                ? unifiedShadow.acceptance_basis === "provider_total_facts_fallback_v1_observer_drift"
                    ? "Snapchat AI Shadow معتمد عبر TOTAL V2 المصالح مع Snapchat · V1 مراقب أقدم · القرارات غير مفعلة"
                    : "Snapchat AI Shadow متطابق مع المصدر الموحد V2 · التحليل والقرارات ما زالت معزولة حتى اعتماد التحويل"
                : "Snapchat AI Shadow قيد المطابقة مع المصدر الموحد V2 · لا توجد قرارات أو كتابات من مسار Shadow"}
        </div>}
        {visible.length ? <div className="grid gap-2 p-3 lg:grid-cols-5">
            {visible.map((item) => {
                const action = aiActionMeta(item);
                const provider = item.provider === "meta" ? "Meta" : "سناب";
                const executionLabel = item.execution_status === "completed" ? "تم التنفيذ" : item.execution_status === "verification_required" ? "بانتظار التحقق" : item.execution_status === "failed" ? "تعذر التنفيذ" : item.execution_status === "executing" ? "جارٍ التنفيذ…" : null;
                const blocked = Boolean(executionLabel);
                const canApprove = item.action_type === "ads_write" && item.executable && item.approval_available;
                const rootCause = AI_ROOT_CAUSES[item.root_cause_category] || item.root_cause_category;
                return <div key={item.recommendation_id} className="min-w-0 rounded-xl border border-slate-200 bg-white p-3 hover:border-violet-300 hover:bg-violet-50/30">
                    <div className="flex items-center justify-between gap-2"><span className="text-[9px] font-black text-slate-400">{provider} · {AI_LEVELS[item.entity_level] || item.entity_level}</span><span className={`shrink-0 rounded-full border px-2 py-0.5 text-[9px] font-black ${action.tone}`}>{action.label}</span></div>
                    {item.account_name && <p className="mt-1 truncate text-[9px] font-extrabold text-slate-500">الحساب: {item.account_name}</p>}
                    <Link to={`/ads-manager?provider=${item.provider}`} className="mt-2 block truncate text-xs font-extrabold text-slate-900 hover:text-violet-700">{item.provider === "meta" && Number(item.campaign_ad_group_count) === 1 && Number(item.campaign_ad_count) === 1 && item.campaign_name ? item.campaign_name : item.entity_name}</Link>
                    {item.provider === "meta" && item.entity_level === "ad" && <p className="mt-1 truncate text-[9px] font-bold text-slate-400">الإعلان: {item.entity_name}</p>}
                    {rootCause && <p className="mt-1 truncate text-[9px] font-extrabold text-rose-700">السبب الجذري: {rootCause}</p>}
                    <p className="mt-1 line-clamp-2 text-[10px] leading-5 text-slate-600">{item.diagnosis || item.rationale}</p>
                    <div className="mt-2 flex items-center justify-between gap-2"><p className="text-[9px] font-bold text-violet-700">الثقة: {item.confidence === "high" ? "عالية" : item.confidence === "medium" ? "متوسطة" : "منخفضة"}</p>{canApprove && <button type="button" disabled={approving[item.recommendation_id] || blocked} onClick={() => approve(item)} className="rounded-lg bg-violet-700 px-2 py-1 text-[9px] font-extrabold text-white disabled:bg-slate-300">{executionLabel || (approving[item.recommendation_id] ? "جارٍ التنفيذ…" : "موافقة وتنفيذ")}</button>}</div>
                </div>;
            })}
        </div> : <div className="p-5 text-center text-xs text-slate-400">{loading ? "جارٍ التحميل…" : "لا توجد الآن توصيات جديدة موثوقة."}</div>}
        {recommendations.length > 0 && <div className="border-t px-4 py-2 text-left"><Link to="/ads-manager/recommendations" className="text-[10px] font-extrabold text-violet-700">عرض جميع التوصيات ←</Link></div>}
    </Panel>;
}

function relativeTime(value, now = Date.now()) {
    if (!value) return "—";
    const date = new Date(value || 0);
    const seconds = Math.max(0, Math.floor((now - date.getTime()) / 1000));
    if (!Number.isFinite(seconds)) return "—";
    if (seconds < 60) return `منذ ${Math.max(1, seconds)} ثانية`;
    if (seconds < 3600) return `منذ ${Math.floor(seconds / 60)} دقيقة`;
    if (seconds < 86400) return `منذ ${Math.floor(seconds / 3600)} ساعة`;
    if (seconds < 2592000) return `منذ ${Math.floor(seconds / 86400)} يوم`;
    if (seconds < 31536000) return `منذ ${Math.floor(seconds / 2592000)} شهر`;
    return `منذ ${Math.floor(seconds / 31536000)} سنة`;
}

export function AbandonedCartsCard({ carts, summary = {} }) {
    const [visibleCount, setVisibleCount] = useState(5);
    const [clock, setClock] = useState(() => Date.now());
    const cartRows = carts || [];
    const visibleCarts = cartRows.slice(0, visibleCount);
    const hasMore = visibleCount < cartRows.length;
    useEffect(() => { setVisibleCount(5); }, [carts]);
    useEffect(() => {
        const timer = window.setInterval(() => setClock(Date.now()), 1_000);
        return () => window.clearInterval(timer);
    }, []);
    return (
        <Panel className="border-teal-200" testid="advanced-abandoned-carts">
            <div className="flex h-14 items-center justify-between border-b border-slate-900 bg-slate-800 px-4 text-white">
                <h2 className="flex items-center gap-2 font-extrabold"><ShoppingCart className="h-5 w-5" />السلات المتروكة</h2>
                <div className="flex items-center gap-1.5 text-[10px] font-black">
                    <span className="rounded-full bg-teal-400/20 px-2 py-1 text-teal-50">متروكة {integer(summary.abandoned_count)}</span>
                    <span className="rounded-full bg-white/10 px-2 py-1 text-slate-100">مكتملة {integer(summary.recovered_count)}</span>
                </div>
            </div>
            <div className="h-[410px] overflow-y-auto overscroll-contain" data-testid="advanced-abandoned-carts-scroll">
            {visibleCarts.length ? visibleCarts.map((cart) => {
                const item = Array.isArray(cart.items) ? cart.items[0] : null;
                const productCount = (cart.items || []).reduce((sum, product) => sum + Math.max(1, Number(product?.quantity || 1)), 0);
                return <div key={cart.cart_id} className="flex min-h-[82px] items-center gap-3 border-b border-slate-100 px-4 py-3 last:border-0 odd:bg-teal-50/30">
                    {item?.image_url
                        ? <img src={item.image_url} alt="" className="h-11 w-11 shrink-0 rounded-xl object-cover" loading="lazy" />
                        : <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-teal-50 text-xl">🛒</div>}
                    <div className="min-w-0 flex-1"><p className="truncate text-xs font-extrabold text-slate-800">{cart.customer_name || "عميل سلة"}</p><p className="mt-1 text-[10px] font-bold text-teal-700">{integer(productCount)} {productCount === 1 ? "منتج" : "منتجات"}</p><p className="mt-0.5 truncate text-[9px] text-slate-400">سلة #{cart.cart_id}</p></div>
                    <div className="text-left"><p className="num text-xs font-black text-teal-700">{money(cart.total)} {cart.currency || "SAR"}</p><p className="mt-1 text-[10px] text-slate-400">{relativeTime(cart.activity_at || cart.cart_updated_at || cart.updated_at || cart.created_at, clock)}</p></div>
                </div>;
            }) : <div className="p-8 text-center text-xs text-slate-400">لا توجد سلات متروكة نشطة.</div>}
            </div>
            {cartRows.length > 5 && <button type="button" onClick={() => hasMore ? setVisibleCount((value) => Math.min(value + 5, cartRows.length)) : setVisibleCount(5)} className="w-full border-t border-teal-200 bg-teal-50/70 px-4 py-3 text-xs font-extrabold text-teal-800 hover:bg-teal-100">{hasMore ? "المزيد" : "عرض أقل"}</button>}
        </Panel>
    );
}

export function TopProductsCard({ rows, summary = {}, loading = false }) {
    const [visibleCount, setVisibleCount] = useState(5);
    const products = [...(rows || [])].sort((a, b) => Number(b.units_sold || 0) - Number(a.units_sold || 0));
    const productCount = Math.max(Number(summary?.product_profit_summary?.product_count || 0), products.length);
    const visibleProducts = products.slice(0, visibleCount);
    const hasMore = visibleCount < products.length;
    useEffect(() => { setVisibleCount(5); }, [rows]);
    return (
        <Panel className="border-indigo-200" testid="advanced-top-products">
            <div className="flex h-14 items-center justify-between border-b border-indigo-800 bg-indigo-700 px-4 text-white"><h2 className="flex items-center gap-2 font-extrabold"><Trophy className="h-5 w-5" />المنتجات الأكثر مبيعًا</h2><div className="text-left text-[9px] font-bold leading-4"><p>{loading && !rows ? "—" : integer(productCount)} منتجًا خلال الفترة</p><p className="text-indigo-100">بتكلفة سلة {loading && !rows ? "—" : integer(summary.salla_fallback_products_count)} · بدون تكلفة {loading && !rows ? "—" : integer(summary.missing_all_cost_products_count)}</p></div></div>
            <div className="grid grid-cols-[minmax(0,1fr)_58px_94px] gap-2 border-b px-3 py-2 text-[9px] font-bold text-slate-400"><span>المنتج</span><span>الوحدات</span><span>المبيعات</span></div>
            <div className="h-[330px] overflow-y-auto overscroll-contain" data-testid="advanced-top-products-scroll">
            {visibleProducts.length ? visibleProducts.map((item) => <div key={item.identity} className="grid min-h-[66px] grid-cols-[minmax(0,1fr)_58px_94px] items-center gap-2 border-b px-3 py-2 last:border-0">
                <div className="flex min-w-0 items-center gap-2">{item.image_url ? <img src={item.image_url} alt="" className="h-10 w-10 rounded-lg object-cover" /> : <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-50">📦</div>}<p className="line-clamp-2 text-[10px] font-bold">{item.name}</p></div>
                <span className="num text-xs font-bold">{integer(item.units_sold)}</span><span className="num whitespace-nowrap text-[10px] font-black text-blue-600">{money(item.total_sales)} ر.س</span>
            </div>) : <div className="p-8 text-center text-xs text-slate-400">{loading ? "جارٍ مزامنة المنتجات المباعة…" : "لا توجد منتجات مباعة في الفترة."}</div>}
            </div>
            {products.length > 5 && <button type="button" onClick={() => hasMore ? setVisibleCount((value) => Math.min(value + 5, products.length)) : setVisibleCount(5)} className="w-full border-t border-indigo-200 bg-indigo-50/60 px-4 py-3 text-xs font-extrabold text-indigo-700 hover:bg-indigo-100">{hasMore ? "المزيد" : "عرض أقل"}</button>}
        </Panel>
    );
}

function Metric({ label, value, Icon, tone, className = "", valueClassName = "" }) {
    return <div className={`flex min-w-0 items-center justify-between gap-2 rounded-xl border bg-white px-3 py-3 shadow-sm ${className}`}><div className="min-w-0"><p className="line-clamp-2 text-[11px] font-bold text-slate-500">{label}</p><p className={`num mt-1 whitespace-nowrap text-[17px] font-black leading-tight ${valueClassName}`}>{value}</p></div><span className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl ${tone}`}><Icon className="h-[18px] w-[18px]" /></span></div>;
}

function PlatformPeriodSummary({ ads }) {
    const providers = ads?.executive_breakdown?.providers || {};
    const meta = {
        snapchat: { label: "سناب", mark: "👻", tone: "bg-yellow-300 text-slate-950" },
        tiktok: { label: "تيك توك", mark: "♪", tone: "bg-slate-950 text-white" },
        meta: { label: "Meta", mark: "∞", tone: "bg-blue-500 text-white" },
        google: { label: "Google", mark: "G", tone: "bg-white text-blue-600 ring-1 ring-blue-200" },
    };
    return <div className="col-span-2 grid min-h-[78px] grid-cols-2 overflow-hidden rounded-xl border bg-white shadow-sm min-[1180px]:col-span-1 min-[1180px]:grid-cols-4" data-testid="advanced-platform-period-summary">
        {PLATFORM_META.map(({ key }) => {
            const row = providers[key] || {};
            const orderCount = row.platform_reported_orders == null ? null : Number(row.platform_reported_orders || 0);
            const average = row.platform_cost_per_order_sar == null ? null : Number(row.platform_cost_per_order_sar || 0);
            const provider = meta[key];
            return <div key={key} className="flex min-w-0 items-center justify-center gap-2 border-l px-2 py-2 last:border-l-0">
                <span className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-black ${provider.tone}`}>{provider.mark}</span>
                <div className="min-w-0 text-[9px] font-bold leading-4 text-slate-500"><p className="truncate text-slate-700">{provider.label}: <b className="num text-[11px] text-slate-950">{orderCount == null ? "—" : integer(orderCount)}</b></p><p className="whitespace-nowrap">متوسط: <b className="num text-[10px] text-slate-950">{average == null ? "—" : `${money(average)} ر.س`}</b></p></div>
            </div>;
        })}
    </div>;
}

export function SummaryStrip({ data, filters, loading = false }) {
    const totals = data?.totals || {};
    const monthTotals = data?.month_kpis || {};
    const missing = Number(data?.product_cost_v2?.missing_products_count || totals.missing_product_cost_count || 0);
    return <div dir="ltr" className="grid gap-3 min-[1180px]:grid-cols-[minmax(0,1.75fr)_minmax(260px,.7fr)]" data-testid="advanced-date-summary">
        <div dir="rtl" className="grid grid-cols-2 gap-2 min-[1180px]:grid-cols-[minmax(118px,.46fr)_minmax(180px,.78fr)_minmax(0,2fr)]">
            <Metric label="طلبات الشهر" value={loading && !data ? "—" : integer(monthTotals.total_orders)} Icon={ShoppingCart} tone="bg-teal-50 text-teal-700" />
            <Metric label="مبيعات الشهر" value={loading && !data ? "—" : `${money(monthTotals.total_sales)} ر.س`} Icon={CircleDollarSign} tone="bg-cyan-50 text-cyan-700" className="px-4" valueClassName="text-[19px]" />
            <PlatformPeriodSummary ads={data?.ads_v2} />
        </div>
        <Link to={buildMissingMezanCostHref(data?.product_cost_v2, filters)} dir="rtl" className="flex min-h-[78px] items-center justify-center gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 text-center text-amber-900"><AlertTriangle className="h-5 w-5 text-amber-500" /><p className="text-xs font-extrabold">{loading && !data ? "جارٍ مزامنة تكاليف المنتجات…" : `${integer(missing)} منتجًا مبيعًا بدون تكلفة ميزان`}<span className="block text-amber-700">{loading && !data ? "" : "أضف التكلفة لاعتماد الأرباح"}</span></p></Link>
    </div>;
}

function AdsCard({ ads, unifiedShadow }) {
    const [monthly, setMonthly] = useState(false);
    const rows = useMemo(() => {
        const daily = ads?.history || [];
        if (!monthly) return daily.map((row) => ({ ...row, label: row.date?.slice(5) }));
        return aggregateDashboardAdsHistoryByMonth(daily);
    }, [ads?.history, monthly]);
    const plotRows = rows;
    const breakdown = ads?.breakdown || {};
    const spendQuality = ads?.spend_quality || {};
    const totalComplete = spendQuality.status === "complete" && spendQuality.amount_complete === true;
    const chartTotal = totalComplete
        ? finiteFinancialValue(ads?.total, { nonnegative: true })
        : null;
    const shadowPassed = unifiedShadow?.shadow_passed === true;
    const shadowComparison = unifiedShadow?.comparison?.spend_sar || {};
    const shadowDelta = finiteFinancialValue(shadowComparison.delta);
    const shadowLegacy = finiteFinancialValue(shadowComparison.legacy);
    const shadowUnified = finiteFinancialValue(shadowComparison.unified);
    const shadowCoverageComplete = unifiedShadow?.comparison?.coverage_complete === true;
    const shadowAcceptanceBasis = unifiedShadow?.acceptance_basis;
    const shadowDetails = [
        shadowLegacy === null ? null : `الحالي ${money(shadowLegacy)}`,
        shadowUnified === null ? null : `V2 ${money(shadowUnified)}`,
        shadowDelta === null ? null : `الفرق ${money(shadowDelta)}`,
        unifiedShadow?.comparison && !shadowCoverageComplete ? "التغطية غير مكتملة" : null,
    ].filter(Boolean).join(" · ");
    return <Panel className="border-amber-200" testid="advanced-ads-chart">
        <div className="flex h-14 items-center justify-between border-b border-amber-700 bg-amber-600 px-4 text-white"><h2 className="flex items-center gap-2 font-extrabold"><CircleDollarSign className="h-5 w-5" />مصروفات منصات الإعلانات</h2><div className="rounded-lg border border-white/30 bg-white/15 p-1 text-[10px] font-bold"><button onClick={() => setMonthly(false)} className={`rounded-md px-2 py-1 ${!monthly ? "bg-white text-amber-800" : ""}`}>يومي</button><button onClick={() => setMonthly(true)} className={`rounded-md px-2 py-1 ${monthly ? "bg-white text-amber-800" : ""}`}>شهري</button></div></div>
        <div data-testid="advanced-snapchat-unified-shadow" className={`border-b px-3 py-2 text-[10px] font-extrabold ${!unifiedShadow ? "bg-slate-50 text-slate-500" : shadowPassed ? "bg-emerald-50 text-emerald-800" : "bg-amber-50 text-amber-900"}`}>
            {!unifiedShadow
                ? "Snapchat V2 Shadow · جارٍ التحقق من التطابق"
                : shadowPassed
                    ? shadowAcceptanceBasis === "provider_reconciliation_fallback"
                        ? "Snapchat V2 Shadow معتمد · مصالح مباشرة مع Snapchat · V1 غير مكتمل · القرارات غير مفعلة"
                        : `Snapchat V2 Shadow مطابق${shadowDelta === null ? "" : ` · الفرق ${money(shadowDelta)} ر.س`} · القرارات غير مفعلة`
                    : `Snapchat V2 Shadow غير معتمد · ${shadowDetails || unifiedShadow.reason || "يوجد اختلاف أو نقص تغطية"}`}
        </div>
        <div className="h-[190px] px-2 pt-3" dir="ltr"><ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}><LineChart data={plotRows} margin={{ top: 6, right: 6, left: 0, bottom: 4 }}><CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" /><XAxis dataKey="label" tick={{ fontSize: 9 }} minTickGap={8} interval="preserveStartEnd" /><YAxis tick={{ fontSize: 9 }} width={38} /><Tooltip formatter={(value, name) => [`${money(value)} ر.س`, name]} labelFormatter={(value) => `الوقت: ${value}`} contentStyle={{ direction: "rtl", borderRadius: 10, fontFamily: "Cairo" }} />{PLATFORM_META.map((p) => <Line key={p.key} type="monotone" dataKey={p.key} name={p.label} stroke={p.color} strokeWidth={2.5} dot={false} activeDot={{ r: 3 }} connectNulls={false} />)}</LineChart></ResponsiveContainer></div>
        <div className="grid grid-cols-4 gap-1 p-2">{PLATFORM_META.map((p) => { const value = p.key === "google" ? (breakdown.google ?? breakdown.google_transitional) : breakdown[p.key]; const state = spendQuality?.[p.key]?.data_state; return <div key={p.key} className="rounded-lg border p-2 text-center"><p className="text-[9px] font-bold" style={{ color: p.color }}>{p.label}</p><p className="num mt-1 text-[10px] font-black">{dashboardSpendDisplay(value, state)}</p></div>; })}</div>
        <div className="flex h-11 items-center justify-between border-t bg-amber-50 px-4 font-extrabold"><span>إجمالي المصروفات</span><span className="num">{chartTotal === null ? "غير مكتمل" : `${optionalMoney(chartTotal)} ر.س`}</span></div>
    </Panel>;
}

function ProfitDetailBox({ children, testid }) {
    return <div data-testid={testid} className="mx-2 mb-3 max-h-72 overflow-auto rounded-xl border border-slate-200 bg-white p-3 shadow-sm">{children}</div>;
}

function ShippingProfitDetails({ rows = [], total = 0 }) {
    const visible = rows.filter((row) => Number(row?.total_cost || 0) > 0);
    return <ProfitDetailBox testid="advanced-profit-shipping-details"><DetailTitle title="🚚 تفاصيل تكاليف الشحن (لكل شركة)" count={`${integer(visible.length)} شركة`} tone="text-sky-900" />{visible.length === 0 ? <EmptyDetails text="لا توجد بيانات شحن في هذه الفترة" /> : <div className="overflow-x-auto"><table className="w-full min-w-[620px] text-[11px]"><thead className="bg-slate-50"><tr><th className="p-2 text-right">الشركة</th><th>الشحنات</th><th>سعر الوحدة</th><th>ضريبة الوحدة</th><th>الإجمالي</th></tr></thead><tbody>{visible.map((row, index) => { const count = Number(row.orders_count || 0); const base = Number(row.cost_per_unit ?? row.cost_per_order ?? 0); const tax = Number(row.tax_per_unit ?? (count > 0 ? Number(row.vat_amount || 0) / count : 0)); return <tr key={`${row.name}-${index}`} className="border-t"><td className="p-2 font-bold">{row.name}{row.is_deferred && <span className="mr-1 rounded bg-amber-100 px-1 py-0.5 text-[9px] text-amber-700">آجل</span>}</td><td className="text-center num">{integer(count)}</td><td className="text-center num">{money(base)}</td><td className="text-center num text-violet-700">{money(tax)}</td><td className="text-center num font-black text-sky-700">{money(row.total_cost)}</td></tr>; })}<tr className="border-t-2 border-sky-200 bg-sky-50"><td colSpan="4" className="p-2 font-black">الإجمالي</td><td className="text-center num font-black text-sky-800">{money(total)}</td></tr></tbody></table></div>}</ProfitDetailBox>;
}

function DetailTitle({ title, count, tone }) {
    return <div className="mb-2 flex items-center justify-between border-b pb-2 text-xs"><b className={tone}>{title}</b><span className="text-slate-400">{count}</span></div>;
}

function EmptyDetails({ text }) {
    return <p className="py-3 text-center text-xs text-slate-400">{text}</p>;
}

function PaymentProfitDetails({ rows = [], total = 0 }) {
    const visible = buildPaymentFeeRows(rows).filter((row) => row.ordersCount > 0 || row.baseAmount > 0 || row.feeAmount > 0);
    return <ProfitDetailBox testid="advanced-profit-payment-details"><DetailTitle title="💳 تفاصيل رسوم طرق الدفع والعمولات البنكية" count={`${integer(visible.length)} طريقة / حساب`} tone="text-violet-900" />{visible.length === 0 ? <EmptyDetails text="لا توجد رسوم طرق دفع في هذه الفترة" /> : <div className="overflow-x-auto"><table className="w-full min-w-[760px] text-[11px]"><thead className="bg-slate-50"><tr><th className="p-2 text-right">طريقة الدفع / الحساب</th><th>الطلبات</th><th>المبلغ الخاضع</th><th>نسبة العمولة</th><th>VAT</th><th>إجمالي الرسوم</th></tr></thead><tbody>{visible.map((row) => <tr key={row.key} className="border-t"><td className="p-2 font-bold">{row.name}{row.parentName && row.parentName !== row.name && <small className="block text-slate-400">{row.parentName}</small>}</td><td className="text-center num">{row.kind === "ad_bank_commission" ? "—" : integer(row.ordersCount)}</td><td className="text-center num">{money(row.baseAmount)}</td><td className="text-center num text-violet-700">{row.commissionPercent == null ? "—" : `${row.commissionPercent.toFixed(2)}%`}</td><td className="text-center num">{row.vatAmount > 0 ? money(row.vatAmount) : row.vatPercent > 0 ? `${row.vatPercent.toFixed(0)}%` : "—"}</td><td className="text-center num font-black text-violet-800">{money(row.feeAmount)}</td></tr>)}<tr className="border-t-2 border-violet-200 bg-violet-50"><td colSpan="5" className="p-2 font-black">الإجمالي</td><td className="text-center num font-black text-violet-900">{money(total)}</td></tr></tbody></table></div>}</ProfitDetailBox>;
}

function OperatingProfitDetails({ totals = {}, total = 0 }) {
    const rows = [["رواتب الموظفين", totals.operating_salaries_employee], ["مصاريف منزلية", totals.operating_salaries_household], ["صدقات / زكاة", totals.operating_salaries_charity], ["إيجارات", totals.operating_rentals_total], ["كهرباء وماء", totals.operating_utilities_total], ["تجديدات وتأمين والتزامات دورية", totals.operating_renewals_total], ["مصاريف مدفوعة مقدماً", totals.operating_prepaid_total], ["مصاريف يومية أخرى", totals.operating_daily_other_total]].filter(([, value]) => Number(value || 0) > 0);
    return <ProfitDetailBox testid="advanced-profit-operating-details"><DetailTitle title="💼 تفاصيل المصروفات التشغيلية" count={`${integer(rows.length)} بند`} tone="text-orange-900" />{rows.length === 0 ? <EmptyDetails text="لا توجد مصروفات تشغيلية في هذه الفترة" /> : <div className="text-xs">{rows.map(([name, value]) => <div key={name} className="flex justify-between border-b py-2"><b>{name}</b><span className="num font-black text-orange-700">{money(value)}</span></div>)}<div className="flex justify-between border-t-2 border-orange-200 py-2"><b>الإجمالي</b><span className="num font-black text-orange-800">{money(total)}</span></div></div>}</ProfitDetailBox>;
}

export function ProfitCard({ data, loading = false }) {
    const [expanded, setExpanded] = useState(null);
    const t = data?.totals || {};
    const adsQuality = data?.ads_v2?.spend_quality || {};
    const adsTotal = finiteFinancialValue(t.total_ads_cost, { nonnegative: true });
    const adsSpendComplete = (
        adsTotal !== null
        && t.ads_spend_data_complete === true
        && adsQuality.status === "complete"
        && adsQuality.amount_complete === true
    );
    const fees = t.total_payment_fees ?? (Number(t.other_payment_fees || 0) + Number(t.tamara_fees || 0) + Number(t.tabby_fees || 0) + Number(t.emkan_fees || 0) + Number(t.bank_fees || 0) + Number(t.ad_bank_commission_fees || 0));
    const rows = [
        { key: "sales", label: "المبيعات", value: t.total_sales, Icon: CircleDollarSign, color: "text-emerald-700" },
        { key: "products", label: "تكاليف المنتجات", value: t.total_product_cost, Icon: PackageOpen, color: "text-amber-700" },
        { key: "ads", label: "إجمالي تكاليف الإعلانات", value: t.total_ads_cost, Icon: Megaphone, color: "text-rose-600", expandable: true },
        { key: "shipping", label: "إجمالي تكاليف الشحن (مقدم + آجل)", value: t.total_shipping_cost, Icon: Truck, color: "text-sky-700", expandable: true },
        { key: "payment", label: "إجمالي رسوم جميع طرق الدفع", value: fees, Icon: CreditCard, color: "text-violet-700", expandable: true },
        { key: "operating", label: "المصروفات التشغيلية (رواتب وإيجارات وغيرها)", value: t.operating_expenses_total, Icon: BriefcaseBusiness, color: "text-orange-700", expandable: true },
    ];
    const sales = Number(t.total_sales || 0);
    const orderCount = Number(t.total_orders || 0);
    const averageBasket = orderCount > 0 ? sales / orderCount : 0;
    const netProfit = finiteFinancialValue(t.net_profit);
    const netMargin = adsSpendComplete && sales > 0 && netProfit !== null ? (netProfit / sales * 100).toFixed(2) : null;
    const details = {
        ads: <ProfitDetailBox testid="advanced-profit-ads-details"><AdsExecutiveBreakdownTable data={data?.ads_v2?.executive_breakdown} /></ProfitDetailBox>,
        shipping: <ShippingProfitDetails rows={data?.shipping_breakdown} total={t.total_shipping_cost} />,
        payment: <PaymentProfitDetails rows={data?.payment_breakdown} total={fees} />,
        operating: <OperatingProfitDetails totals={t} total={t.operating_expenses_total} />,
    };
    const initialLoading = loading && !data;
    return <Panel className="border-emerald-200" testid="advanced-profit-summary"><div className="flex h-14 items-center justify-between border-b border-emerald-800 bg-emerald-700 px-4 text-white"><h2 className="flex items-center gap-2 font-extrabold"><TrendingUp className="h-5 w-5" />الملخص التنفيذي للأرباح</h2><span className="text-[9px] font-bold text-emerald-100">{initialLoading ? "جارٍ مزامنة الفترة…" : "الفترة المحددة"}</span></div><div className="grid grid-cols-2 gap-2 border-b border-emerald-100 bg-emerald-50/40 p-3 sm:grid-cols-4"><Metric label="تكلفة الطلب" value={initialLoading || !adsSpendComplete || t.avg_cost_per_order == null ? "—" : `${money(t.avg_cost_per_order)} ر.س`} Icon={ShoppingBag} tone="bg-blue-50 text-blue-700" /><Metric label="عدد الطلبات" value={initialLoading ? "—" : integer(orderCount)} Icon={ShoppingCart} tone="bg-emerald-50 text-emerald-700" /><Metric label="العائد" value={initialLoading || !adsSpendComplete || t.overall_roas == null ? "—" : `${Number(t.overall_roas).toFixed(2)}×`} Icon={TrendingUp} tone="bg-violet-50 text-violet-700" /><Metric label="متوسط قيمة سلة المشتريات" value={initialLoading ? "—" : `${money(averageBasket)} ر.س`} Icon={ShoppingBag} tone="bg-rose-50 text-rose-600" /></div><div className="px-4 py-2">{rows.map((row, index) => { const rowValue = finiteFinancialValue(row.value); const rowIncomplete = row.key === "ads" && !adsSpendComplete; const percentage = index > 0 && sales > 0 && rowValue !== null && !rowIncomplete ? (rowValue / sales * 100).toFixed(2) : null; return <div key={row.key}><button type="button" disabled={!row.expandable} onClick={() => row.expandable && setExpanded((value) => value === row.key ? null : row.key)} aria-expanded={row.expandable ? expanded === row.key : undefined} data-testid={`advanced-profit-row-${row.key}`} dir="rtl" className={`flex min-h-[56px] w-full items-center gap-3 border-b text-right last:border-0 ${row.expandable ? "cursor-pointer rounded-lg hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-emerald-200" : "cursor-default"}`}><span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-slate-50 ${row.color}`}><row.Icon className="h-4 w-4" /></span><p className="flex min-w-0 flex-1 items-center gap-1 text-right text-xs font-extrabold text-slate-700">{row.label}{row.expandable && <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${expanded === row.key ? "rotate-180" : ""}`} />}</p><div dir="ltr" className={`num flex shrink-0 items-baseline gap-1 text-left text-base font-black ${row.color}`}><span>{initialLoading ? "—" : rowIncomplete ? "غير مكتمل" : optionalMoney(row.value)}</span>{!initialLoading && !rowIncomplete && rowValue !== null && <span className="text-[11px] font-extrabold">ر.س</span>}{percentage && <span className="ml-1 rounded-md bg-current/10 px-1.5 py-0.5 text-[9px] opacity-80">{percentage}%</span>}</div></button>{row.expandable && expanded === row.key && details[row.key]}</div>; })}</div><div dir="ltr" className="m-4 flex min-h-[64px] items-center justify-between rounded-xl bg-emerald-600 px-5 text-white"><p className="num flex items-baseline gap-1 text-xl font-black">{!initialLoading && netMargin && <span data-testid="advanced-profit-net-margin" title="هامش صافي الأرباح">{netMargin}%</span>}<span>{initialLoading ? "—" : !adsSpendComplete ? "غير مكتمل" : optionalMoney(t.net_profit)}</span>{!initialLoading && adsSpendComplete && netProfit !== null && <span className="text-sm">ر.س</span>}</p><div dir="rtl"><p className="font-black">صافي الأرباح</p><p className="text-[9px] text-emerald-100">{!initialLoading && !adsSpendComplete ? "بانتظار اكتمال بيانات الإعلانات" : "بعد جميع التكاليف والمصروفات"}</p></div></div></Panel>;
}

function orderSource(order) {
    return [
        order?.source?.channel,
        order?.source?.platform,
        order?.source?.source,
        typeof order?.source === "string" ? order.source : "",
        order?.utm?.source,
        order?.utm_source,
        order?.marketing?.source,
        order?.attribution?.source,
        order?.source_channel,
    ].map((value) => String(value || "").trim().toLowerCase()).find(Boolean) || "";
}

function SourceBadge({ order }) {
    const source = orderSource(order);
    let badge = null;
    if (source.includes("snap")) badge = { label: "سناب", mark: "👻", className: "border-yellow-300 bg-yellow-300 text-slate-950" };
    else if (source.includes("tiktok") || source.includes("tik tok")) badge = { label: "تيك توك", mark: "♪", className: "border-slate-900 bg-slate-950 text-white" };
    else if (source.includes("meta") || source.includes("facebook") || source.includes("instagram") || source === "fb" || source === "ig") badge = { label: "ميتا", mark: source.includes("instagram") ? <Instagram className="h-3 w-3" /> : "∞", className: "border-blue-500 bg-blue-500 text-white" };
    else if (source.includes("google") || source.includes("adwords") || source.includes("gads")) badge = { label: "جوجل", mark: "G", className: "border-blue-200 bg-white text-blue-600" };
    if (!badge) return null;
    return <span title={`مصدر الطلب: ${badge.label}`} aria-label={`مصدر الطلب: ${badge.label}`} className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[9px] font-black leading-none shadow-sm ${badge.className}`}>{badge.mark}</span>;
}

const ORDER_STATUS_AR = {
    under_review: "بانتظار المراجعة",
    reviewed: "تمت المراجعة",
    processing: "قيد التنفيذ",
    completed: "تم التنفيذ",
    delivering: "جاري التوصيل",
    delivered: "تم التوصيل",
    shipped: "تم الشحن",
    canceled: "ملغي",
    cancelled: "ملغي",
    refunded: "مسترجع",
};

function orderStatusLabel(order) {
    const raw = order?.status_native || order?.status?.name || order?.status || "";
    const normalized = String(raw).trim().toLowerCase().replaceAll(" ", "_");
    return ORDER_STATUS_AR[normalized] || raw || "بانتظار المراجعة";
}

function orderCity(order) {
    return order?.shipping?.address?.city || order?.customer?.shipping_address?.city || "غير محدد";
}

function CustomerAvatar({ customer }) {
    const avatarUrl = String(customer?.avatar_url || "").trim();
    const gender = String(customer?.gender || "").toLowerCase();
    const fallback = gender === "female" ? "👩" : gender === "male" ? "👨" : null;
    return <div className="relative flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full bg-slate-100 text-slate-600 sm:h-12 sm:w-12">
        {fallback ? <span className="text-xl leading-none sm:text-2xl">{fallback}</span> : <User size={22} weight="fill" />}
        {avatarUrl && <img src={avatarUrl} alt="" className="absolute inset-0 h-full w-full object-cover" loading="lazy" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.style.display = "none"; }} />}
    </div>;
}

export function LatestOrders({ orders, totals = {} }) {
    const orderCount = Number(totals.total_orders || 0);
    const average = orderCount > 0 ? Number(totals.total_sales || 0) / orderCount : 0;
    return <Panel className="border-sky-200" testid="advanced-latest-orders">
        <div className="flex h-14 items-center justify-between border-b border-sky-700 bg-sky-600 px-4 text-white">
            <h2 className="flex items-center gap-2 font-extrabold"><ShoppingBag className="h-5 w-5" />أحدث الطلبات</h2>
            <div className="flex items-center gap-3 text-[10px] font-bold"><span className="inline-flex items-center gap-1 rounded-full bg-white/15 px-2 py-1"><ShoppingBag className="h-3.5 w-3.5" />{integer(orderCount)} طلب</span><span className="whitespace-nowrap">متوسط: <b className="num">{money(average)} ر.س</b></span></div>
        </div>
        <div className="divide-y divide-slate-100">{orders.slice(0, 8).map((order) => {
            const id = String(order.order_number);
            const status = orderStatusLabel(order);
            const itemCount = Number(order.items?.length || order.items_count || 0);
            const payment = order.payment?.method_native || order.payment?.method || order.payment_method || "غير محدد";
            return <Link
                key={id}
                to={`/orders-v2/${encodeURIComponent(id)}?returnTo=${encodeURIComponent("/dashboard-advanced")}`}
                dir="rtl"
                className="flex min-h-[88px] items-center gap-3 px-4 py-4 text-right hover:bg-slate-50 sm:px-5"
            >
                <CustomerAvatar customer={order.customer} />
                <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <div className="min-w-0 truncate text-[15px] font-semibold">{order.customer?.name || "عميل بدون اسم"}</div>
                        {order.is_new && <span className="shrink-0 rounded-full border border-rose-300 px-2 py-0.5 text-[11px] font-bold text-rose-600">جديد</span>}
                    </div>
                    <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-400 sm:text-xs">
                        <span className="whitespace-nowrap">#{id}</span><span>•</span>
                        <span className="whitespace-nowrap">{orderCity(order)}</span><span>•</span>
                        <span className="inline-flex items-center gap-1 whitespace-nowrap"><span className="h-2 w-2 shrink-0 rounded-full bg-slate-800" />{status}</span><span>•</span>
                        <span className="whitespace-nowrap">{itemCount} قطعة</span><span>•</span>
                        <span className="whitespace-nowrap">{payment}</span>
                    </div>
                </div>
                <div className="shrink-0 text-left">
                    <div className="flex items-center justify-end gap-1.5"><SourceBadge order={order} /><span className="num whitespace-nowrap font-semibold text-teal-800">{money(order.totals?.total || order.total_amount)} ر.س</span></div>
                    <div className="mt-1 whitespace-nowrap text-[11px] text-slate-400 sm:text-xs">{relativeTime(order.created_at || order.order_date)}</div>
                </div>
                <ChevronLeft className="h-4 w-4 shrink-0 text-slate-300" />
            </Link>;
        })}</div>
    </Panel>;
}

function GaLive({ data }) { const pages = data?.top_pages || []; const minutes = data?.active_users?.per_minute || []; const max = Math.max(1, ...pages.map((p) => Number(p.views || 0))); const minuteMax = Math.max(1, ...minutes.map((m) => Number(m.active_users || 0))); return <div className="space-y-4"><Panel className="border-blue-200"><div className="flex h-14 items-center gap-2 border-b border-blue-800 bg-blue-700 px-4 text-white"><BarChart3 className="h-5 w-5" /><h2 className="text-sm font-black">Google Analytics 4 — مباشر</h2></div><div className="p-4"><h3 className="mb-3 text-sm font-extrabold">الصفحات الأكثر مشاهدة</h3>{pages.slice(0, 6).map((p, i) => <div key={`${p.title}-${i}`} className="mb-3"><div className="flex justify-between gap-2 text-[10px]"><span className="truncate">{p.title}</span><b>{p.views}</b></div><div className="mt-1 h-1.5 rounded bg-slate-100"><div className="h-full rounded bg-blue-500" style={{ width: `${Number(p.views || 0) / max * 100}%` }} /></div></div>)}</div></Panel><Panel className="border-violet-200"><div className="flex h-14 items-center gap-2 border-b border-violet-800 bg-violet-700 px-4 text-white"><UsersRound className="h-5 w-5" /><h2 className="font-black">المستخدمون النشطون الآن</h2></div><div className="grid grid-cols-2 gap-2 p-4"><Metric label="آخر 30 دقيقة" value={integer(data?.active_users?.last_30_minutes)} Icon={UsersRound} tone="bg-blue-50 text-blue-600" /><Metric label="آخر 5 دقائق" value={integer(data?.active_users?.last_5_minutes)} Icon={UsersRound} tone="bg-violet-50 text-violet-600" /></div><div className="flex h-36 items-end gap-1 overflow-hidden px-4 pb-4" dir="ltr" data-testid="advanced-ga-active-chart">{minutes.map((m, i) => <div key={i} className="max-h-full flex-1 rounded-t bg-violet-600" style={{ height: `${Math.min(100, Math.max(4, Number(m.active_users || 0) / minuteMax * 100))}%` }} />)}</div></Panel></div>; }

export async function loadDashboardPeriodSnapshot({
    next,
    background = false,
    requestSequence,
    isLatest,
    apiClient = api,
    setData,
    setLoading,
    setLoadError,
    now = Date.now,
}) {
    if (!background) {
        setLoading(true);
        setLoadError(null);
    }
    try {
        const query = new URLSearchParams(filtersToQueryString(next));
        query.set("_refresh", String(now()));
        const response = await apiClient.get(`/dashboard-v2?${query.toString()}`, {
            headers: { "Cache-Control": "no-cache", Pragma: "no-cache" },
        });
        if (isLatest(requestSequence)) {
            setData(response.data);
            setLoadError(null);
        }
    } catch {
        if (!background && isLatest(requestSequence)) {
            setLoadError("تعذر تحميل بيانات الفترة المحددة");
        }
    } finally {
        if (isLatest(requestSequence)) setLoading(false);
    }
}

export default function AdvancedDashboard() {
    const [filters, setFilters] = useState(() => defaultFilters("today"));
    const [data, setData] = useState(null); const [carts, setCarts] = useState([]); const [cartSummary, setCartSummary] = useState({ abandoned_count: 0, recovered_count: 0 }); const [ga, setGa] = useState(null); const [unifiedShadow, setUnifiedShadow] = useState(null); const [loading, setLoading] = useState(true); const [loadError, setLoadError] = useState(null);
    const { orders } = useOrders();
    const dashboardDataRef = useRef(null);
    const requestSequenceRef = useRef(0);
    const backgroundRefreshInFlightRef = useRef(false);
    const lastOrderSignatureRef = useRef("");
    const orderSignature = useMemo(() => dashboardOrdersSignature(orders), [orders]);
    const loadPeriod = useCallback(async (next, { background = false } = {}) => {
        if (background && backgroundRefreshInFlightRef.current) return;
        const requestSequence = ++requestSequenceRef.current;
        if (background) backgroundRefreshInFlightRef.current = true;
        try {
            await loadDashboardPeriodSnapshot({
                next,
                background,
                requestSequence,
                isLatest: (sequence) => sequence === requestSequenceRef.current,
                setData,
                setLoading,
                setLoadError,
            });
        } finally {
            if (background) backgroundRefreshInFlightRef.current = false;
        }
    }, []);
    useEffect(() => { loadPeriod(filters); }, [filters, loadPeriod]);
    useEffect(() => { dashboardDataRef.current = data; }, [data]);
    useEffect(() => {
        const previousSignature = lastOrderSignatureRef.current;
        lastOrderSignatureRef.current = orderSignature;
        if (shouldRefreshDashboardForOrders(previousSignature, orderSignature, Boolean(dashboardDataRef.current))) {
            loadPeriod(filters, { background: true });
        }
    }, [filters, loadPeriod, orderSignature]);
    useEffect(() => {
        const refresh = () => {
            if (
                (typeof document === "undefined" || !document.hidden)
                && (typeof navigator === "undefined" || navigator.onLine)
            ) loadPeriod(filters, { background: true });
        };
        const handleVisibilityChange = () => { if (!document.hidden) refresh(); };
        const timer = window.setInterval(refresh, DASHBOARD_AUTO_REFRESH_MS);
        window.addEventListener("focus", refresh);
        window.addEventListener("online", refresh);
        document.addEventListener("visibilitychange", handleVisibilityChange);
        return () => {
            window.clearInterval(timer);
            window.removeEventListener("focus", refresh);
            window.removeEventListener("online", refresh);
            document.removeEventListener("visibilitychange", handleVisibilityChange);
        };
    }, [filters, loadPeriod]);
    useEffect(() => {
        let active = true;
        let cartRequestInFlight = false;
        const loadCarts = async () => {
            if (cartRequestInFlight || (typeof document !== "undefined" && document.hidden) || (typeof navigator !== "undefined" && !navigator.onLine)) return;
            cartRequestInFlight = true;
            const cartQuery = new URLSearchParams({ from_date: filters.from || "", to_date: filters.to || filters.from || "" }).toString();
            try {
                const result = await api.get(`/dashboard-v2/abandoned-carts/recent?${cartQuery}`);
                if (!active) return;
                setCarts(result.data?.items || []);
                setCartSummary({ abandoned_count: Number(result.data?.abandoned_count || 0), recovered_count: Number(result.data?.recovered_count || 0) });
            } catch { /* Keep the last good cart snapshot during transient failures. */ }
            finally { cartRequestInFlight = false; }
        };
        const handleVisibilityChange = () => { if (!document.hidden) loadCarts(); };
        loadCarts();
        const timer = window.setInterval(loadCarts, CARTS_AUTO_REFRESH_MS);
        window.addEventListener("focus", loadCarts);
        window.addEventListener("online", loadCarts);
        document.addEventListener("visibilitychange", handleVisibilityChange);
        return () => {
            active = false;
            window.clearInterval(timer);
            window.removeEventListener("focus", loadCarts);
            window.removeEventListener("online", loadCarts);
            document.removeEventListener("visibilitychange", handleVisibilityChange);
        };
    }, [filters.from, filters.to]);
    useEffect(() => {
        let active = true;
        setUnifiedShadow(null);
        const query = new URLSearchParams({
            from_date: filters.from || "",
            to_date: filters.to || filters.from || "",
        }).toString();
        api.get(`/dashboard-v2/unified-marketing-shadow?${query}`)
            .then((result) => {
                if (active) setUnifiedShadow(result.data || null);
            })
            .catch(() => {
                if (active) setUnifiedShadow({
                    shadow_passed: false,
                    reason: "تعذر تحميل المقارنة",
                });
            });
        return () => { active = false; };
    }, [filters.from, filters.to]);
    useEffect(() => {
        let active = true;
        const loadGa = async () => {
            try {
                const result = await api.get("/integrations-v2/google_analytics_4/realtime-dashboard");
                if (active) setGa(result.data);
            } catch { /* Preserve the last successful realtime snapshot. */ }
        };
        loadGa();
        const timer = window.setInterval(loadGa, 60_000);
        return () => { active = false; window.clearInterval(timer); };
    }, []);
    return <div dir="rtl" className="space-y-4" data-testid="advanced-dashboard-page">
        <header className="flex flex-wrap items-center justify-between gap-3">
            <div><p className="text-xs text-slate-400">لوحة التحكم الافتراضية</p><h1 className="text-2xl font-black sm:text-3xl">لوحة التحكم المتقدمة</h1></div>
            <Link to="/dashboard-v2" className="inline-flex items-center gap-2 rounded-xl border bg-white px-4 py-2 text-sm font-bold"><ArrowRight className="h-4 w-4" />لوحة التحكم القديمة</Link>
        </header>
        <div className="flex items-stretch gap-2"><div className="min-w-0 flex-1"><AdvancedFilters value={filters} onChange={setFilters} defaultPreset="today" /></div><button onClick={() => loadPeriod(filters)} className="rounded-xl border bg-white px-4 text-blue-700" aria-label="تحديث بيانات الفترة"><RefreshCw className={`h-5 w-5 ${loading ? "animate-spin" : ""}`} /></button></div>
        {!loading && loadError && <Panel className="border-amber-200 bg-amber-50 p-5 text-sm font-bold text-amber-900"><span role="alert">{loadError} — {data ? "تم الاحتفاظ بآخر بيانات موثوقة." : "لم تُعرض أرقام بديلة أو بيانات من فترة سابقة."}</span></Panel>}
        {(Boolean(data) || loading) && <>
        <SummaryStrip data={data} filters={filters} loading={loading} />
        <CampaignAdvisorCard />
        <div dir="ltr" className="grid items-start gap-4 min-[1280px]:grid-cols-[clamp(280px,24vw,350px)_minmax(0,1fr)]"><aside dir="rtl" className="space-y-4"><AdsCard ads={data?.ads_v2} unifiedShadow={unifiedShadow} /><TopProductsCard rows={data?.product_cost_v2?.product_rows} summary={data?.product_cost_v2} loading={loading} /><AbandonedCartsCard carts={carts} summary={cartSummary} /></aside><main dir="rtl" className="min-w-0"><div dir="ltr" className="grid min-w-0 items-start gap-4 min-[1120px]:grid-cols-[minmax(0,2fr)_minmax(280px,.92fr)]"><div dir="rtl" className="space-y-4"><ProfitCard data={data} loading={loading} /><LatestOrders orders={orders} totals={data?.totals} /></div><div dir="rtl"><GaLive data={ga} /></div></div></main></div>
        </>}
    </div>;
}
