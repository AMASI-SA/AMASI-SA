import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { AI_DATA_CONTRACT, AI_ORDER_FIELDS } from "./aiControl/fields";
import { getMarketingPerformance } from "../services/marketingPerformance";
import {
    addDaysIso,
    BLOCK,
    buildQuery,
    deepHasKeyFragment,
    fmtInt,
    fmtMoney,
    fmtPct,
    getAny,
    hasValue,
    OK,
    statusLabel,
    todayIso,
    toneClass,
    WARN,
} from "./aiControl/helpers";

function fieldCoverage(items, field) {
    const total = items.length;
    if (!total) return { ...field, present: 0, total: 0, pct: 0, status: field.required ? BLOCK : WARN };
    const present = items.reduce((count, item) => {
        const ok = field.fragments
            ? deepHasKeyFragment(item, field.fragments)
            : hasValue(getAny(item, field.paths || []));
        return count + (ok ? 1 : 0);
    }, 0);
    const pct = (present / total) * 100;
    const status = field.required && pct < 95 ? BLOCK : pct < 80 ? WARN : OK;
    return { ...field, present, total, pct, status };
}

function Kpi({ title, value, hint }) {
    return (
        <div className="rounded-xl border bg-white p-4">
            <p className="text-xs text-slate-600 font-bold">{title}</p>
            <div className="text-2xl font-extrabold mt-1 num">{value}</div>
            {hint && <p className="text-xs text-slate-500 mt-2 leading-5">{hint}</p>}
        </div>
    );
}

function Tab({ active, onClick, children }) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={`px-4 py-2 rounded-lg text-sm font-bold border ${
                active ? "bg-brand text-white border-brand" : "bg-white text-slate-700 border-slate-200 hover:bg-slate-50"
            }`}
        >
            {children}
        </button>
    );
}

function Gate({ gate }) {
    return (
        <div className={`rounded-xl border p-4 ${toneClass(gate.status)}`}>
            <div className="flex justify-between gap-2">
                <h3 className="font-extrabold">{gate.title}</h3>
                <span className="text-xs font-bold whitespace-nowrap">{statusLabel(gate.status)}</span>
            </div>
            <p className="text-sm mt-2 leading-6">{gate.text}</p>
        </div>
    );
}

function CoverageTable({ rows }) {
    return (
        <div className="overflow-x-auto rounded-xl border bg-white">
            <table className="min-w-full text-sm">
                <thead className="bg-slate-50 text-slate-600">
                    <tr>
                        <th className="p-3 text-right">البيان</th>
                        <th className="p-3 text-right">التغطية</th>
                        <th className="p-3 text-right">الحالة</th>
                        <th className="p-3 text-right">لماذا يحتاجه AI؟</th>
                    </tr>
                </thead>
                <tbody>
                    {rows.map((row) => (
                        <tr key={row.id} className="border-t">
                            <td className="p-3 font-bold">{row.label}</td>
                            <td className="p-3 num">{fmtInt(row.present)} / {fmtInt(row.total)} — {fmtPct(row.pct)}</td>
                            <td className="p-3"><span className={`inline-flex rounded-full border px-2 py-1 text-xs font-bold ${toneClass(row.status)}`}>{statusLabel(row.status)}</span></td>
                            <td className="p-3 text-slate-600 leading-6">{row.why}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

function SimpleList({ title, rows }) {
    if (!rows.length) {
        return <div className="rounded-xl border bg-emerald-50 border-emerald-200 p-4 text-emerald-800 font-bold">✅ {title}: لا توجد عينة ظاهرة</div>;
    }
    const keys = Object.keys(rows[0]);
    return (
        <div className="rounded-xl border bg-white p-5 overflow-x-auto">
            <h2 className="text-xl font-extrabold mb-3">{title}</h2>
            <table className="min-w-full text-sm">
                <thead className="bg-slate-50"><tr>{keys.map((key) => <th key={key} className="p-3 text-right">{key}</th>)}</tr></thead>
                <tbody>
                    {rows.map((row, idx) => (
                        <tr key={idx} className="border-t">{keys.map((key) => <td key={key} className="p-3 num">{String(row[key])}</td>)}</tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function AIControlCenter() {
    const [fromDate, setFromDate] = useState(addDaysIso(-30));
    const [toDate, setToDate] = useState(todayIso());
    const [tab, setTab] = useState("readiness");
    const [loading, setLoading] = useState(false);
    const [data, setData] = useState({});
    const [aiQuestion, setAiQuestion] = useState("حلّل حالة ميزان وحدد أهم مشكلة والخطوة التالية.");
    const [aiLoading, setAiLoading] = useState(false);
    const [aiResult, setAiResult] = useState(null);
    const [marketingContext, setMarketingContext] = useState(null);
    const [marketingError, setMarketingError] = useState(null);

    const load = async () => {
        setLoading(true);
        setMarketingError(null);
        const range = { from_date: fromDate, to_date: toDate };
        const endpoints = [
            ["dashboard", `/dashboard${buildQuery(range)}`],
            ["orders", `/orders${buildQuery({ ...range, page: 1, limit: 500 })}`],
            ["statusSummary", `/orders/status-summary${buildQuery(range)}`],
            ["adsBreakdown", `/dashboard/ads-cost-breakdown${buildQuery(range)}`],
            ["qoyodStats", "/integrations/qoyod/first-sync-monitor/stats/summary"],
        ];
        const next = {};
        await Promise.all(endpoints.map(async ([key, url]) => {
            try {
                const res = await api.get(url);
                next[key] = { ok: true, data: res.data };
            } catch (e) {
                next[key] = { ok: false, error: e?.response?.data?.detail || e?.message || "فشل التحميل" };
            }
        }));
        try {
            const snapchat = await getMarketingPerformance({
                platform: "snapchat",
                dateFrom: fromDate,
                dateTo: toDate,
                page: 1,
                limit: 50,
            });
            setMarketingContext(snapchat);
        } catch (error) {
            setMarketingContext(null);
            setMarketingError(
                error?.response?.data?.detail
                || error?.message
                || "تعذر تحميل بيانات سناب شات",
            );
        }
        setData(next);
        setLoading(false);
        const failed = Object.values(next).filter((x) => !x.ok).length;
        if (failed) toast.warning(`تم الفحص مع ${failed} مصدر لم يستجب`);
    };

    useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, []);

    const model = useMemo(() => {
        const orders = data.orders?.data?.items || [];
        const dashboardSummary = data.dashboard?.data?.summary || {};
        const statusSummary = data.statusSummary?.data || {};
        const adsBreakdown = data.adsBreakdown?.data || {};
        const qoyodStats = data.qoyodStats?.data || {};
        const coverage = AI_ORDER_FIELDS.map((field) => fieldCoverage(orders, field));
        const requiredBlockers = coverage.filter((row) => row.required && row.status === BLOCK);
        const productReady = coverage.find((row) => row.id === "products")?.status === OK;
        const adCoverage = coverage.find((row) => row.id === "ad_link");
        const adSpend = Number(dashboardSummary.daily_ads_total || dashboardSummary.total_ads_cost || adsBreakdown.total_amount || 0);
        const qoyodFailed = Number(qoyodStats?.stats?.failed || qoyodStats?.failed || 0);

        const seen = new Map();
        orders.forEach((order) => {
            const number = getAny(order, ["order_number", "reference_id", "order_id"]);
            if (!hasValue(number)) return;
            const key = String(number).trim();
            seen.set(key, (seen.get(key) || 0) + 1);
        });
        const duplicates = [...seen.entries()].filter(([, count]) => count > 1).slice(0, 20);
        const missingOrders = orders.filter((order) => AI_ORDER_FIELDS.some((field) => field.required && !hasValue(getAny(order, field.paths || [])))).slice(0, 25);

        const gates = [
            { title: "المحاسبة والتقارير", status: requiredBlockers.length ? BLOCK : OK, text: "لا نعتمد أي تحليل محاسبي إذا كانت حقول الطلب الأساسية ناقصة." },
            { title: "الربح الحقيقي", status: productReady ? OK : BLOCK, text: "تحليل الربح يحتاج سطور منتجات وتكلفة وSKU على مستوى الطلب." },
            { title: "الإعلانات", status: adSpend <= 0 ? WARN : adCoverage?.status === OK ? OK : BLOCK, text: "لا قرار توسع أو إيقاف بدون ربط الطلب بالحملة أو الإعلان." },
            { title: "قيود / ZATCA", status: qoyodFailed > 0 ? BLOCK : data.qoyodStats?.ok ? OK : WARN, text: "أي فشل ترحيل أو سداد في قيود يعتبر مانعاً قبل الاعتماد." },
        ];

        const recommendations = [];
        coverage.forEach((row) => {
            if (row.required && row.status === BLOCK) recommendations.push(`P0: أضف أو أصلح ${row.label} في بيانات الطلبات — التغطية الحالية ${fmtPct(row.pct)}.`);
        });
        if (adCoverage?.status !== OK) recommendations.push("P0: أضف campaign_id / ad_id / ad_squad_id / UTM داخل الطلب حتى يصبح تحليل الحملات موثوقاً.");
        if (!data.adsBreakdown?.ok) recommendations.push("P1: أصلح تفصيل مصروفات الإعلانات لأن إجمالي الصرف وحده لا يكفي لحساب CPA وROAS.");
        if (duplicates.length) recommendations.push("P0: امنع تكرار الطلبات عبر upsert صارم على user_id + order_number.");

        return { orders, dashboardSummary, statusSummary, adsBreakdown, qoyodStats, coverage, gates, recommendations, duplicates, missingOrders };
    }, [data]);

    const overall = model.gates.some((gate) => gate.status === BLOCK) ? BLOCK : model.gates.some((gate) => gate.status === WARN) ? WARN : OK;

    const askAssistant = (question) => {
        setAiQuestion(question);
    };

    const runAiAnalysis = async () => {
        setAiLoading(true);
        try {
            const campaignRows = (marketingContext?.campaigns || [])
                .slice(0, 30)
                .map((campaign) => ({
                    campaign_id: campaign.campaign_id,
                    campaign_name: campaign.campaign_name,
                    status: campaign.status,
                    delivery_status: campaign.delivery_status,
                    spend_sar: campaign.spend_sar,
                    sales_sar: campaign.sales_sar,
                    orders: campaign.orders,
                    roas: campaign.roas,
                    cpa_sar: campaign.cpa_sar,
                    ctr_pct: campaign.ctr_pct,
                    daily_budget: campaign.budget?.daily_native,
                    currency: campaign.budget?.currency,
                    data_complete: campaign.data_complete,
                }));
            const context = {
                period: { from_date: fromDate, to_date: toDate },
                readiness: statusLabel(overall),
                metrics: {
                    api_orders: Number(data.orders?.data?.total || model.orders.length || 0),
                    dashboard_orders: Number(model.dashboardSummary.total_orders || 0),
                    total_sales: Number(model.dashboardSummary.total_sales || 0),
                    ad_spend: Number(
                        marketingContext?.totals?.spend_sar
                        || model.dashboardSummary.daily_ads_total
                        || model.adsBreakdown.total_amount
                        || 0,
                    ),
                    qoyod_failed: Number(model.qoyodStats?.stats?.failed || model.qoyodStats?.failed || 0),
                    duplicate_orders: model.duplicates.length,
                    missing_critical_fields: model.missingOrders.length,
                    snapchat: {
                        spend_sar: marketingContext?.totals?.spend_sar,
                        sales_sar: marketingContext?.totals?.sales_sar,
                        orders: marketingContext?.totals?.orders,
                        roas: marketingContext?.totals?.roas,
                        cpa_sar: marketingContext?.totals?.cpa_sar,
                        data_complete: marketingContext?.totals?.data_complete,
                        ai_analysis_ready:
                            marketingContext?.ai_readiness?.ai_analysis_ready === true,
                        campaign_count: campaignRows.length,
                        campaigns: campaignRows,
                    },
                },
                gates: model.gates.map((gate) => ({
                    title: gate.title,
                    status: statusLabel(gate.status),
                    evidence: gate.text,
                })),
                coverage: model.coverage.map((row) => ({
                    field: row.label,
                    present: row.present,
                    total: row.total,
                    percent: Number(row.pct.toFixed(1)),
                    status: statusLabel(row.status),
                })),
                errors: [
                    ...Object.entries(data)
                        .filter(([, result]) => !result?.ok)
                        .map(([source, result]) => ({
                            source,
                            message: result?.error || "فشل المصدر",
                        })),
                    ...(marketingError
                        ? [{ source: "snapchat_campaigns", message: marketingError }]
                        : []),
                ],
                anomalies: model.duplicates.map(([order, count]) => ({ type: "duplicate_order", order, count })),
                recommendations: model.recommendations,
            };
            const response = await api.post("/ai/analyze", { question: aiQuestion, context });
            setAiResult(response.data?.analysis || null);
            toast.success("اكتمل التحليل بالقراءة فقط");
        } catch (error) {
            toast.error(error?.response?.data?.detail || "تعذر تشغيل محلل ميزان");
        } finally {
            setAiLoading(false);
        }
    };

    return (
        <div className="space-y-6" dir="rtl" data-testid="ai-control-center">
            <div className="flex items-start justify-between flex-wrap gap-4">
                <div>
                    <div className="inline-flex bg-slate-900 text-white rounded-full px-3 py-1 text-xs font-bold mb-3">🧠 مساعد ميزان · قرارات مبنية على الدليل</div>
                    <h1 className="text-3xl font-extrabold">مساعد ميزان</h1>
                    <p className="text-sm text-slate-600 mt-2 leading-7 max-w-3xl">تحدث معه بطريقتك الطبيعية: اطلب توسيع المبيعات أو مراجعة حملات سناب، وسيشرح القرار والدليل. أي تعديل فعلي ينتقل إلى المعاينة والاعتماد والتحقق قبل التنفيذ.</p>
                </div>
                <div className="rounded-xl border bg-white p-3 flex flex-wrap items-end gap-3">
                    <label className="text-xs font-bold text-slate-600">من<input type="date" value={fromDate} onChange={(e) => setFromDate(e.target.value)} className="block mt-1 border rounded-lg px-3 py-2 text-sm" /></label>
                    <label className="text-xs font-bold text-slate-600">إلى<input type="date" value={toDate} onChange={(e) => setToDate(e.target.value)} className="block mt-1 border rounded-lg px-3 py-2 text-sm" /></label>
                    <button onClick={load} disabled={loading} className="bg-brand text-white rounded-lg px-4 py-2 font-bold text-sm disabled:opacity-50">{loading ? "جارٍ الفحص…" : "🔄 فحص الآن"}</button>
                </div>
            </div>

            <section className="rounded-2xl border border-violet-200 bg-gradient-to-l from-violet-50 to-white p-5" data-testid="mezan-assistant-quick-actions">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                    <div>
                        <h2 className="text-lg font-extrabold">ماذا تريد من ميزان؟</h2>
                        <p className="mt-1 text-sm text-slate-600">اختر سؤالًا جاهزًا أو اكتب بطريقتك في المحادثة أدناه.</p>
                    </div>
                    <Link
                        to="/ads-manager?provider=snapchat"
                        className="rounded-xl border border-violet-200 bg-white px-4 py-2 text-sm font-bold text-violet-800 hover:bg-violet-100"
                    >
                        فتح إدارة سناب
                    </Link>
                </div>
                <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-4">
                    {[
                        "وسّع المبيعات من الحملات الرابحة، ولا تتبع كلامي إذا خالف الأرقام.",
                        "راجع حملات سناب آخر 3 أيام وحدد ما يستمر وما يحتاج إيقافًا.",
                        "لماذا ارتفعت تكلفة الطلب؟ افحص الإعلان والحملة وصفحة المنتج.",
                        "اقترح اختبارًا جديدًا للكوب والمشط بدون زيادة عشوائية للميزانية.",
                    ].map((question) => (
                        <button
                            key={question}
                            type="button"
                            onClick={() => askAssistant(question)}
                            className="rounded-xl border border-slate-200 bg-white p-3 text-right text-sm font-bold leading-6 hover:border-violet-300 hover:bg-violet-50"
                        >
                            {question}
                        </button>
                    ))}
                </div>
                <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
                    التنفيذ الآلي غير مفتوح بلا حدود: المساعد يحلل ويقترح، ثم تستخدم إدارة سناب لمسار المعاينة ← الاعتماد ← التنفيذ ← التحقق ← التراجع.
                </div>
            </section>

            <div className={`rounded-xl border-2 p-5 ${toneClass(overall)}`}>
                <h2 className="text-xl font-extrabold">حكم الجاهزية العام: {statusLabel(overall)}</h2>
                <p className="text-sm mt-1 leading-6">إذا ظهرت بوابة ⛔ فالقرار المرتبط بها ممنوع حتى يكتمل مصدر البيانات أو يتم إصلاح الفرق.</p>
            </div>

            <AIAnalyst
                question={aiQuestion}
                setQuestion={setAiQuestion}
                loading={aiLoading}
                result={aiResult}
                onAnalyze={runAiAnalysis}
            />

            <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <Kpi title="طلبات API" value={fmtInt(data.orders?.data?.total || model.orders.length)} />
                <Kpi title="طلبات Dashboard" value={fmtInt(model.dashboardSummary.total_orders)} />
                <Kpi title="مبيعات" value={fmtMoney(model.dashboardSummary.total_sales)} />
                <Kpi title="صرف إعلاني" value={fmtMoney(model.dashboardSummary.daily_ads_total || model.adsBreakdown.total_amount)} />
                <Kpi title="فشل قيود" value={fmtInt(model.qoyodStats?.stats?.failed || model.qoyodStats?.failed || 0)} />
            </div>

            <div className="flex flex-wrap gap-2">
                <Tab active={tab === "readiness"} onClick={() => setTab("readiness")}>جاهزية البيانات</Tab>
                <Tab active={tab === "reconciliation"} onClick={() => setTab("reconciliation")}>كشف الفروقات</Tab>
                <Tab active={tab === "errors"} onClick={() => setTab("errors")}>أخطاء الربط</Tab>
                <Tab active={tab === "contract"} onClick={() => setTab("contract")}>عقد البيانات</Tab>
            </div>

            {tab === "readiness" && <div className="space-y-5"><div className="grid grid-cols-1 md:grid-cols-2 gap-4">{model.gates.map((gate) => <Gate key={gate.title} gate={gate} />)}</div><CoverageTable rows={model.coverage} /><Recommendations items={model.recommendations} /></div>}
            {tab === "reconciliation" && <Reconciliation model={model} />}
            {tab === "errors" && <Errors data={data} model={model} />}
            {tab === "contract" && <Contract />}
        </div>
    );
}

function AIAnalyst({ question, setQuestion, loading, result, onAnalyze }) {
    const severityTone = {
        ok: "bg-emerald-50 border-emerald-200 text-emerald-900",
        info: "bg-blue-50 border-blue-200 text-blue-900",
        warning: "bg-amber-50 border-amber-200 text-amber-900",
        critical: "bg-red-50 border-red-200 text-red-900",
    };
    return (
        <section className="rounded-xl border-2 border-violet-200 bg-white p-5" data-testid="mezan-ai-analyst">
            <div className="flex items-start justify-between gap-3 flex-wrap">
                <div>
                    <h2 className="text-xl font-extrabold">✨ محلل ميزان</h2>
                    <p className="text-sm text-slate-600 mt-1">OpenAI · قراءة وتحليل فقط · لا يملك أدوات تعديل أو إرسال إلى قيود</p>
                </div>
                <span className="rounded-full bg-emerald-100 text-emerald-800 px-3 py-1 text-xs font-bold">🔒 قراءة فقط</span>
            </div>
            <div className="mt-4 flex flex-col md:flex-row gap-3">
                <textarea
                    value={question}
                    onChange={(event) => setQuestion(event.target.value)}
                    maxLength={500}
                    rows={2}
                    className="flex-1 rounded-lg border px-3 py-2 text-sm leading-6"
                    placeholder="مثال: وسّع المبيعات وقرر ما الذي يستمر أو يتوقف بناءً على الربحية."
                />
                <button
                    type="button"
                    onClick={onAnalyze}
                    disabled={loading || question.trim().length < 3}
                    className="rounded-lg bg-violet-700 text-white px-5 py-2 font-bold disabled:opacity-50"
                >
                    {loading ? "مساعد ميزان يحلل…" : "إرسال إلى مساعد ميزان"}
                </button>
            </div>
            {result && (
                <div className={`mt-5 rounded-xl border p-4 ${severityTone[result.severity] || severityTone.info}`}>
                    <div className="flex justify-between gap-2 flex-wrap">
                        <h3 className="font-extrabold">{result.summary}</h3>
                        <span className="text-xs font-bold">{result.safe_to_act ? "✅ الدليل كافٍ" : "⛔ يحتاج تحقق قبل الإجراء"}</span>
                    </div>
                    {!!result.findings?.length && (
                        <div className="mt-4 space-y-3">
                            {result.findings.map((finding, index) => (
                                <div key={`${finding.title}-${index}`} className="rounded-lg bg-white/70 border p-3">
                                    <div className="font-bold">{finding.title}</div>
                                    <p className="text-sm mt-1"><strong>الدليل:</strong> {finding.evidence}</p>
                                    <p className="text-sm mt-1"><strong>الأثر:</strong> {finding.impact}</p>
                                </div>
                            ))}
                        </div>
                    )}
                    {!!result.next_actions?.length && (
                        <div className="mt-4">
                            <h4 className="font-extrabold mb-2">الخطوات المقترحة</h4>
                            <ol className="space-y-2">
                                {result.next_actions.map((item, index) => (
                                    <li key={`${item.priority}-${index}`} className="text-sm rounded-lg bg-white/70 border p-3">
                                        <strong>{item.priority} — {item.action}</strong>
                                        <div className="mt-1">التحقق: {item.verification}</div>
                                    </li>
                                ))}
                            </ol>
                        </div>
                    )}
                    {!!result.limitations?.length && <p className="text-xs mt-4">حدود التحليل: {result.limitations.join(" · ")}</p>}
                </div>
            )}
        </section>
    );
}

function Recommendations({ items }) {
    return (
        <div className="rounded-xl border bg-white p-5">
            <h2 className="text-xl font-extrabold mb-3">توصيات للمطور</h2>
            {items.length ? <ul className="space-y-2 text-sm leading-7 list-disc list-inside">{items.map((item) => <li key={item}>{item}</li>)}</ul> : <p className="text-sm text-slate-500">لا توجد توصيات حرجة في العينة الحالية.</p>}
        </div>
    );
}

function Reconciliation({ model }) {
    const gap = (Number(model.statusSummary?.totals?.orders_count) || 0) - (Number(model.dashboardSummary.total_orders) || 0);
    return (
        <div className="space-y-5">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3"><Kpi title="تكرار رقم الطلب" value={fmtInt(model.duplicates.length)} /><Kpi title="طلبات ناقصة حقول حرجة" value={fmtInt(model.missingOrders.length)} /><Kpi title="فرق Dashboard vs Status" value={fmtInt(gap)} hint="قد يكون بسبب فلاتر الحالات؛ لا يُتجاهل قبل التفسير." /></div>
            <SimpleList title="طلبات مكررة" rows={model.duplicates.map(([order, count]) => ({ order, count }))} />
            <SimpleList title="عينة طلبات ناقصة" rows={model.missingOrders.map((order) => ({ order: getAny(order, ["order_number", "reference_id", "order_id"]) || "—", date: getAny(order, ["order_date", "created_at", "date"]) || "—", amount: getAny(order, ["total_amount", "amount", "total"]) || 0 }))} />
        </div>
    );
}

function Errors({ data, model }) {
    const endpoints = [["Dashboard", data.dashboard], ["Orders", data.orders], ["Status", data.statusSummary], ["Ads", data.adsBreakdown], ["Qoyod", data.qoyodStats]];
    return (
        <div className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-5 gap-3">{endpoints.map(([name, result]) => <div key={name} className={`rounded-lg border p-3 ${result?.ok ? "bg-white" : "bg-red-50 border-red-200"}`}><div className="font-bold text-sm">{name}</div><div className="text-xs mt-1 break-words">{result?.ok ? "✅ متصل" : `⛔ ${result?.error || "فشل"}`}</div></div>)}</div>
            <Gate gate={{ title: "قيود", status: (model.qoyodStats?.stats?.failed || 0) > 0 ? BLOCK : OK, text: `failed = ${fmtInt(model.qoyodStats?.stats?.failed || 0)}` }} />
            <Gate gate={{ title: "الإعلانات", status: data.adsBreakdown?.ok ? OK : WARN, text: `entries = ${fmtInt((model.adsBreakdown.items || []).length)} / total = ${fmtMoney(model.adsBreakdown.total_amount)}` }} />
        </div>
    );
}

function Contract() {
    return (
        <div className="space-y-4">
            {AI_DATA_CONTRACT.map((section) => (
                <div key={section.title} className="rounded-xl border bg-white p-5">
                    <h3 className="text-lg font-extrabold mb-3">{section.title}</h3>
                    <ul dir="ltr" className="list-disc list-inside text-sm leading-7 text-slate-700">{section.items.map((item) => <li key={item}>{item}</li>)}</ul>
                </div>
            ))}
        </div>
    );
}
