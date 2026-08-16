import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    AlertTriangle,
    ArrowRight,
    BarChart3,
    Clock3,
    Filter,
    RefreshCw,
    ShieldCheck,
    Sparkles,
    TrendingUp,
} from "lucide-react";

import api from "../lib/api";

const ACTIONS = {
    pause: { label: "إيقاف مقترح", tone: "border-red-200 bg-red-50 text-red-700" },
    reduce: { label: "خفض مقترح", tone: "border-orange-200 bg-orange-50 text-orange-700" },
    monitor: { label: "مراقبة", tone: "border-amber-200 bg-amber-50 text-amber-700" },
    maintain: { label: "استمرار", tone: "border-sky-200 bg-sky-50 text-sky-700" },
    scale: { label: "توسعة مقترحة", tone: "border-emerald-200 bg-emerald-50 text-emerald-700" },
};

const LEVELS = { campaign: "حملة", ad_group: "مجموعة إعلانية", ad: "إعلان" };
const CONFIDENCE = { high: "عالية", medium: "متوسطة", low: "منخفضة" };
const PRIORITY = { critical: 4, high: 3, medium: 2, low: 1 };
const ACTION_PRIORITY = { pause: 5, reduce: 4, scale: 3, monitor: 2, maintain: 1 };
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

function MetricCard({ icon: Icon, label, value, tone }) {
    return <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex items-center justify-between gap-3">
            <div>
                <p className="text-[10px] font-extrabold text-slate-500">{label}</p>
                <p className="mt-1 text-2xl font-black text-slate-900">{Number(value || 0).toLocaleString("en-US")}</p>
            </div>
            <span className={`rounded-xl p-2 ${tone}`}><Icon className="h-5 w-5" /></span>
        </div>
    </div>;
}

function executionLabel(status) {
    if (status === "completed") return "تم التنفيذ";
    if (status === "verification_required") return "بانتظار التحقق";
    if (status === "failed") return "تعذر التنفيذ";
    if (status === "executing") return "جارٍ التنفيذ…";
    return null;
}

export default function CampaignRecommendations() {
    const [snapshot, setSnapshot] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [periodTotals, setPeriodTotals] = useState(null);
    const [approving, setApproving] = useState({});
    const [expanded, setExpanded] = useState(null);
    const [provider, setProvider] = useState("all");
    const [level, setLevel] = useState("all");
    const [action, setAction] = useState("all");

    const load = useCallback(async () => {
        setError("");
        try {
            const response = await api.get("/ads-manager/ai-monitor/latest");
            setSnapshot(response.data);
            const range = response.data?.range;
            if (range?.from && range?.to) {
                try {
                    const query = new URLSearchParams({ from_date: range.from, to_date: range.to }).toString();
                    const dashboard = await api.get(`/dashboard-v2?${query}`);
                    setPeriodTotals(dashboard.data?.totals || null);
                } catch {
                    setPeriodTotals(null);
                }
            }
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
    const urgent = recommendations.filter((item) => ["pause", "reduce"].includes(item.action)).length;
    const scale = recommendations.filter((item) => item.action === "scale").length;
    const watching = recommendations.filter((item) => ["monitor", "maintain"].includes(item.action)).length;
    const visible = recommendations
        .filter((item) => (
            (provider === "all" || item.provider === provider)
            && (level === "all" || item.entity_level === level)
            && (action === "all" || item.action === action)
        ))
        .map((item, originalIndex) => ({ item, originalIndex }))
        .sort((left, right) => {
            const leftItem = left.item;
            const rightItem = right.item;
            const priorityDifference = (PRIORITY[rightItem.priority] || 0) - (PRIORITY[leftItem.priority] || 0);
            if (priorityDifference) return priorityDifference;
            const actionDifference = (ACTION_PRIORITY[rightItem.action] || 0) - (ACTION_PRIORITY[leftItem.action] || 0);
            if (actionDifference) return actionDifference;
            const leftImpact = Math.max(
                Math.abs(Number(leftItem.financial_impact?.period_estimated_contribution_sar || 0)),
                Math.abs(Number(leftItem.financial_impact?.forecast_delta_sar || 0)),
                Math.abs(Number(leftItem.decision_score || 0)),
            );
            const rightImpact = Math.max(
                Math.abs(Number(rightItem.financial_impact?.period_estimated_contribution_sar || 0)),
                Math.abs(Number(rightItem.financial_impact?.forecast_delta_sar || 0)),
                Math.abs(Number(rightItem.decision_score || 0)),
            );
            if (rightImpact !== leftImpact) return rightImpact - leftImpact;
            return left.originalIndex - right.originalIndex;
        })
        .map(({ item }) => item);

    const approve = async (item) => {
        const change = item.action === "pause"
            ? "إيقاف"
            : item.action === "reduce"
                ? `خفض الميزانية ${item.change_percent || 15}%`
                : `رفع الميزانية ${item.change_percent || 15}%`;
        if (!window.confirm(`موافقتك ستنفّذ ${change} على ${item.entity_name} في ${item.provider === "meta" ? "Meta" : "Snapchat"}. هل تريد المتابعة؟`)) return;
        setApproving((value) => ({ ...value, [item.recommendation_id]: true }));
        try {
            const { data } = await api.post(
                `/ads-manager/ai-monitor/recommendations/${encodeURIComponent(item.recommendation_id)}/approve`,
                { snapshot_id: snapshot.snapshot_id },
            );
            setSnapshot((value) => ({
                ...value,
                recommendations: (value.recommendations || []).map((row) => (
                    row.recommendation_id === item.recommendation_id
                        ? { ...row, execution_status: data.status }
                        : row
                )),
            }));
            window.setTimeout(load, 6000);
        } catch (requestError) {
            window.alert(requestError?.response?.data?.detail?.message || "تعذّر تنفيذ التوصية بأمان. حدّث التحليل وحاول مجددًا.");
        } finally {
            setApproving((value) => ({ ...value, [item.recommendation_id]: false }));
        }
    };

    const explain = (item) => {
        const waitHours = Number(item.recommended_wait_hours || (item.action === "scale" ? 6 : ["pause", "reduce"].includes(item.action) ? 3 : 6));
        return {
            facts: item.decision_facts?.length ? item.decision_facts : (item.evidence || []),
            whyNow: item.why_now || item.rationale,
            proposedAction: item.proposed_action || (
                item.action === "scale"
                    ? `رفع الميزانية ${item.change_percent || 15}% فقط ثم منع أي توسعة ثانية قبل القياس.`
                    : item.action === "reduce"
                        ? `خفض الميزانية ${item.change_percent || 15}% فقط ثم مراقبة النتيجة.`
                        : item.action === "pause"
                            ? "إيقاف مؤقت ثم مراجعة جودة الإعلان والتحويل قبل إعادة التشغيل."
                            : "المراقبة دون تغيير الميزانية حتى تكتمل عينة أقوى."
            ),
            waitHours,
            observationPlan: item.observation_plan || `المجدول يعيد الفحص كل ساعة. اصبر ${waitHours} ساعات قبل اتخاذ قرار ثانٍ على التوصية نفسها.`,
            criteria: item.success_criteria?.length ? item.success_criteria : [
                "تحسن المشتريات مقارنة بسرعة الصرف",
                "تحسن تكلفة الشراء أو ثبات العائد",
                "عدم انتقال الهدر إلى كيان آخر داخل الحملة",
            ],
            risk: item.risk_if_ignored || "قد يستمر الصرف دون ظهور دليل جديد يبرر الميزانية الحالية.",
            financial: item.financial_impact || null,
        };
    };

    const shareOfPeriodResult = (value) => {
        const netProfit = Number(periodTotals?.net_profit);
        if (!Number.isFinite(netProfit) || netProfit === 0) return null;
        return Math.abs(Number(value || 0)) / Math.abs(netProfit) * 100;
    };

    const periodImpactLabel = (value) => {
        const netProfit = Number(periodTotals?.net_profit || 0);
        const impact = Number(value || 0);
        if (netProfit < 0) return impact >= 0 ? "خففت صافي الخسارة" : "زادت صافي الخسارة";
        return impact >= 0 ? "رفعت صافي الربح" : "خفضت صافي الربح";
    };

    const forecastImpactLabel = (value) => {
        const netProfit = Number(periodTotals?.net_profit || 0);
        const impact = Number(value || 0);
        if (netProfit < 0) return impact >= 0 ? "خفض متوقع في الخسارة" : "زيادة متوقعة في الخسارة";
        return impact >= 0 ? "زيادة متوقعة في الربح" : "نقص متوقع في الربح";
    };

    return <main dir="rtl" className="min-h-screen bg-slate-50 p-3 sm:p-5" data-testid="campaign-recommendations-page">
        <div className="mx-auto max-w-[1500px] space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                    <Link to="/dashboard-advanced" className="rounded-xl border border-slate-200 bg-white p-2 text-slate-600 shadow-sm hover:text-violet-700" aria-label="العودة إلى لوحة التحكم">
                        <ArrowRight className="h-5 w-5" />
                    </Link>
                    <div>
                        <h1 className="text-xl font-black text-slate-900">توصيات الحملات الإعلانية</h1>
                        <p className="mt-1 text-xs font-bold text-slate-500">سناب وMeta · الحملة والمجموعة والإعلان · التنفيذ بعد موافقتك</p>
                    </div>
                </div>
                <button type="button" onClick={load} disabled={loading} className="flex items-center gap-2 rounded-xl bg-violet-700 px-4 py-2 text-xs font-extrabold text-white disabled:bg-slate-300">
                    <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />تحديث التوصيات
                </button>
            </div>

            <section className="overflow-hidden rounded-2xl border border-violet-200 bg-white shadow-sm">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-violet-800 bg-violet-700 px-5 py-4 text-white">
                    <div>
                        <h2 className="flex items-center gap-2 font-black"><Sparkles className="h-5 w-5" />ملاحظات الذكاء على الحملات</h2>
                        <p className="mt-1 text-[10px] text-violet-100">تحليل مستقل للأداء والصرف والنتائج كل ساعة</p>
                    </div>
                    <div className="flex items-center gap-3 text-[10px] font-extrabold">
                        <span className="flex items-center gap-1 rounded-full bg-white/15 px-3 py-1.5"><Clock3 className="h-3.5 w-3.5" />{snapshot?.generated_at ? relativeTime(snapshot.generated_at) : "بانتظار أول تشغيل"}</span>
                        <span className="flex items-center gap-1 rounded-full bg-white/15 px-3 py-1.5"><ShieldCheck className="h-3.5 w-3.5" />تنفيذ بعد موافقتك</span>
                    </div>
                </div>
                <div className="px-5 py-4">
                    <p className="text-sm font-extrabold leading-7 text-slate-800">{loading ? "جارٍ قراءة التحليل…" : (snapshot?.summary || "سيظهر أول تحليل بعد اكتمال التشغيل الدوري.")}</p>
                    <p className="mt-2 text-[10px] font-bold text-slate-500">فحص {Number(snapshot?.entities_scanned || 0).toLocaleString("en-US")} كيانًا، ووصلت {Number(snapshot?.candidates_scanned || 0).toLocaleString("en-US")} إشارة إلى مرحلة التقييم.</p>
                    {snapshot?.range?.from && <p className="mt-2 text-[10px] font-black text-slate-500">فترة الدراسة: <span dir="ltr">{snapshot.range.from} → {snapshot.range.to}</span></p>}
                    {periodTotals && <p className="mt-1 text-[10px] font-black text-violet-700">صافي نتيجة فترة الدراسة: {money(periodTotals.net_profit)} ر.س · المبيعات {money(periodTotals.total_sales)} ر.س</p>}
                </div>
            </section>

            <section className="grid grid-cols-2 gap-3 lg:grid-cols-4" aria-label="ملخص توصيات الحملات">
                <MetricCard icon={BarChart3} label="جميع التوصيات" value={recommendations.length} tone="bg-violet-50 text-violet-700" />
                <MetricCard icon={AlertTriangle} label="إجراء لتقليل الهدر" value={urgent} tone="bg-orange-50 text-orange-700" />
                <MetricCard icon={TrendingUp} label="فرص توسعة" value={scale} tone="bg-emerald-50 text-emerald-700" />
                <MetricCard icon={Clock3} label="مراقبة واستمرار" value={watching} tone="bg-sky-50 text-sky-700" />
            </section>

            {urgent > 0 && <section className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-orange-200 bg-orange-50 px-4 py-3" data-testid="campaign-recommendations-action-required">
                <div className="flex items-center gap-3">
                    <span className="rounded-xl bg-orange-100 p-2 text-orange-700"><AlertTriangle className="h-5 w-5" /></span>
                    <div><p className="text-sm font-black text-orange-900">إجراء مطلوب على {urgent} توصية</p><p className="mt-1 text-[10px] font-bold text-orange-700">راجع الدليل ثم وافق فقط على التغيير المناسب؛ لا يُنفّذ شيء تلقائيًا.</p></div>
                </div>
                <span className="rounded-full bg-white px-3 py-1 text-[10px] font-black text-orange-700">مراجعة بشرية إلزامية</span>
            </section>}

            <section className="rounded-2xl border border-slate-200 bg-white p-3 shadow-sm">
                <div className="flex flex-wrap items-center gap-2">
                    <span className="flex items-center gap-1 px-2 text-[10px] font-black text-slate-500"><Filter className="h-4 w-4" />تصفية</span>
                    <select aria-label="تصفية حسب المنصة" value={provider} onChange={(event) => setProvider(event.target.value)} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold text-slate-700">
                        <option value="all">كل المنصات</option><option value="snapchat">سناب</option><option value="meta">Meta</option>
                    </select>
                    <select aria-label="تصفية حسب المستوى" value={level} onChange={(event) => setLevel(event.target.value)} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold text-slate-700">
                        <option value="all">كل المستويات</option><option value="campaign">الحملات</option><option value="ad_group">المجموعات</option><option value="ad">الإعلانات</option>
                    </select>
                    <select aria-label="تصفية حسب القرار" value={action} onChange={(event) => setAction(event.target.value)} className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-xs font-bold text-slate-700">
                        <option value="all">كل القرارات</option><option value="pause">إيقاف</option><option value="reduce">خفض</option><option value="monitor">مراقبة</option><option value="maintain">استمرار</option><option value="scale">توسعة</option>
                    </select>
                    <span className="mr-auto px-2 text-[10px] font-extrabold text-slate-400">ترتيب تلقائي: الأهم والأكثر تأثيرًا أولًا · المعروض {visible.length} من {recommendations.length}</span>
                </div>
            </section>

            {error && <div className="rounded-2xl border border-red-200 bg-red-50 p-4 text-sm font-bold text-red-700">{error}</div>}

            <section className="grid gap-3 lg:grid-cols-2" data-testid="campaign-recommendations-list">
                {visible.map((item, index) => {
                    const config = ACTIONS[item.action] || ACTIONS.monitor;
                    const status = executionLabel(item.execution_status);
                    const blocked = Boolean(status);
                    const details = explain(item);
                    const isExpanded = expanded === item.recommendation_id;
                    return <article key={item.recommendation_id} className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm hover:border-violet-300">
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-1.5"><p className="text-[10px] font-black text-slate-400">{item.provider === "meta" ? "Meta" : "سناب"} · {LEVELS[item.entity_level] || item.entity_level}</p>{index === 0 && <span className="rounded-full bg-red-50 px-2 py-0.5 text-[8px] font-black text-red-700">الأعلى أولوية وتأثيرًا</span>}</div>
                                <Link to={`/ads-manager?provider=${item.provider}`} className="mt-1 block truncate text-sm font-black text-slate-900 hover:text-violet-700">{item.entity_name}</Link>
                                {item.parent_name && <p className="mt-1 truncate text-[10px] font-bold text-slate-400">ضمن {item.parent_name}</p>}
                            </div>
                            <span className={`shrink-0 rounded-full border px-3 py-1 text-[10px] font-black ${config.tone}`}>{config.label}</span>
                        </div>
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-[9px] font-extrabold text-slate-400"><span>أُنشئت {relativeTime(item.generated_at || snapshot?.generated_at)}</span><span>•</span><span dir="ltr">{dateTime(item.generated_at || snapshot?.generated_at)}</span></div>
                        <p className="mt-3 text-xs font-bold leading-6 text-slate-600">{details.whyNow}</p>
                        {item.evidence?.length > 0 && <div className="mt-3 flex flex-wrap gap-1.5">{item.evidence.map((evidence) => <span key={evidence} className="rounded-lg bg-slate-100 px-2 py-1 text-[9px] font-extrabold text-slate-600">{evidence}</span>)}</div>}
                        <button type="button" onClick={() => setExpanded(isExpanded ? null : item.recommendation_id)} aria-expanded={isExpanded} className="mt-3 w-full rounded-xl border border-violet-200 bg-violet-50 px-3 py-2 text-xs font-black text-violet-800 hover:bg-violet-100">
                            {isExpanded ? "إخفاء شرح القرار" : "لماذا اتخذ ميزان هذا القرار؟"}
                        </button>
                        {isExpanded && <div className="mt-3 space-y-3 rounded-2xl border border-violet-100 bg-violet-50/40 p-4" data-testid={`recommendation-explanation-${item.recommendation_id}`}>
                            <div><h3 className="text-xs font-black text-slate-900">الأرقام التي بنى عليها القرار</h3><div className="mt-2 flex flex-wrap gap-1.5">{details.facts.map((fact) => <span key={fact} className="rounded-lg bg-white px-2 py-1 text-[10px] font-extrabold text-slate-700 shadow-sm">{fact}</span>)}</div></div>
                            <div><h3 className="text-xs font-black text-slate-900">ماذا يقترح ميزان؟</h3><p className="mt-1 text-[11px] font-bold leading-6 text-slate-700">{details.proposedAction}</p></div>
                            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3"><h3 className="text-xs font-black text-amber-900">كم ساعة أصبر؟</h3><p className="mt-1 text-[11px] font-bold leading-6 text-amber-800">{details.observationPlan}</p></div>
                            <div><h3 className="text-xs font-black text-slate-900">متى نعتبر القرار ناجحًا؟</h3><ul className="mt-1 space-y-1 text-[10px] font-bold leading-5 text-slate-600">{details.criteria.map((criterion) => <li key={criterion}>• {criterion}</li>)}</ul></div>
                            {details.financial && (() => {
                                const periodShare = shareOfPeriodResult(details.financial.period_estimated_contribution_sar);
                                const forecastShare = shareOfPeriodResult(details.financial.forecast_delta_sar);
                                const periodContribution = Number(details.financial.period_estimated_contribution_sar || 0);
                                const forecastDelta = Number(details.financial.forecast_delta_sar || 0);
                                return <div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 p-3" data-testid={`recommendation-financial-impact-${item.recommendation_id}`}>
                                    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-xs font-black text-emerald-950">أثر الحملة على الربح</h3><span className="rounded-full bg-white px-2 py-1 text-[9px] font-black text-emerald-700">تقدير مالي · ليس ضمانًا</span></div>
                                    <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
                                        <div className="rounded-xl bg-white p-2"><p className="text-[9px] font-bold text-slate-400">صرف فترة الدراسة</p><p className="mt-1 text-xs font-black text-slate-800">{money(details.financial.period_spend_sar)} ر.س</p></div>
                                        <div className="rounded-xl bg-white p-2"><p className="text-[9px] font-bold text-slate-400">إيراد المنصة</p><p className="mt-1 text-xs font-black text-slate-800">{money(details.financial.period_provider_revenue_sar)} ر.س</p></div>
                                        <div className="rounded-xl bg-white p-2"><p className="text-[9px] font-bold text-slate-400">مساهمة الربح التقديرية</p><p className={`mt-1 text-xs font-black ${periodContribution >= 0 ? "text-emerald-700" : "text-red-700"}`}>{periodContribution >= 0 ? "+" : ""}{money(periodContribution)} ر.س</p></div>
                                            <div className="rounded-xl bg-white p-2"><p className="text-[9px] font-bold text-slate-400">{periodImpactLabel(periodContribution)} · نسبتها من صافي النتيجة</p><p className={`mt-1 text-xs font-black ${periodContribution >= 0 ? "text-emerald-700" : "text-red-700"}`}>{periodShare == null ? "—" : `${periodShare.toFixed(2)}%`}</p></div>
                                    </div>
                                    <div className="mt-3 rounded-xl bg-white p-3">
                                        <h4 className="text-[11px] font-black text-slate-900">توقع فترة الانتظار القادمة: {details.financial.forecast_hours} ساعات</h4>
                                        <div className="mt-2 grid gap-2 sm:grid-cols-3">
                                            <p className="text-[10px] font-bold text-slate-600">بدون تنفيذ القرار: <b dir="ltr">{money(details.financial.forecast_without_action_sar)} ر.س</b></p>
                                            <p className="text-[10px] font-bold text-slate-600">بعد تنفيذ القرار: <b dir="ltr">{money(details.financial.forecast_with_action_sar)} ر.س</b></p>
                                            <p className={`text-[10px] font-black ${forecastDelta >= 0 ? "text-emerald-700" : "text-red-700"}`}>{forecastImpactLabel(forecastDelta)}: {forecastDelta >= 0 ? "+" : ""}{money(forecastDelta)} ر.س {forecastShare == null ? "" : `(${forecastShare.toFixed(2)}% من صافي النتيجة)`}</p>
                                        </div>
                                    </div>
                                    <p className="mt-2 text-[9px] font-bold leading-5 text-emerald-800">{details.financial.limitation}</p>
                                </div>;
                            })()}
                            <div><h3 className="text-xs font-black text-red-800">ماذا لو تجاهلنا التوصية؟</h3><p className="mt-1 text-[10px] font-bold leading-5 text-red-700">{details.risk}</p></div>
                            {item.guardrail && <p className="rounded-xl bg-white px-3 py-2 text-[10px] font-bold leading-5 text-violet-800">ضابط التنفيذ: {item.guardrail}</p>}
                        </div>}
                        <div className="mt-4 flex items-center justify-between gap-3 border-t border-slate-100 pt-3">
                            <p className="text-[10px] font-black text-violet-700">الثقة: {CONFIDENCE[item.confidence] || item.confidence}</p>
                            {item.approval_available
                                ? <button type="button" disabled={approving[item.recommendation_id] || blocked} onClick={() => approve(item)} className="rounded-xl bg-violet-700 px-3 py-2 text-[10px] font-black text-white disabled:bg-slate-300">{status || (approving[item.recommendation_id] ? "جارٍ التنفيذ…" : "موافقة وتنفيذ")}</button>
                                : <span className="text-[9px] font-bold text-slate-400">توصية للمتابعة فقط</span>}
                        </div>
                    </article>;
                })}
            </section>

            {!loading && visible.length === 0 && !error && <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-10 text-center text-sm font-bold text-slate-400">لا توجد توصيات مطابقة للفلاتر المحددة.</div>}
        </div>
    </main>;
}
