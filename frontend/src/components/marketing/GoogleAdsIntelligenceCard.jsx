import DashboardPanel from "../DashboardPanel";
import GoogleRecommendationAction from "./GoogleRecommendationAction";
import { useEffect, useMemo, useRef, useState } from "react";
import { getGoogleAnalyses, googleErrorMessage, isGoogleRequestCancelled } from "../../services/googleAds";

function text(value, fallback = "") { return typeof value === "string" ? value : fallback; }
function googleWorkspaceHref(accountId) {
    const params = new URLSearchParams({ provider: "google", tab: "ai" });
    if (accountId) params.set("account", accountId);
    return `/ads-manager?${params.toString()}`;
}
function samePeriod(record, dateFrom, dateTo) {
    const period = record.result?.period || record.period || {};
    const start = record.date_from || period.date_from, end = record.date_to || period.date_to;
    return (!dateFrom || start === dateFrom) && (!dateTo || end === dateTo);
}

export default function GoogleAdsIntelligenceCard({ accountId, dateFrom, dateTo, collapsible = false, onPreview }) {
    const [items, setItems] = useState([]), [loading, setLoading] = useState(true), [error, setError] = useState("");
    const [revision, setRevision] = useState(0);
    const sequence = useRef(0);
    useEffect(() => {
        let active = true; const controller = new AbortController(); const request = ++sequence.current;
        setLoading(true); setError(""); setItems([]);
        getGoogleAnalyses({ accountId, dateFrom, dateTo, signal: controller.signal }).then((response) => {
            if (active && request === sequence.current) setItems((response.items || []).filter((record) => samePeriod(record, dateFrom, dateTo) && (!accountId || String(record.account_id || record.result?.account_id) === String(accountId))));
        }).catch((failure) => { if (active && request === sequence.current && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure, "تعذر تحميل تحليلات جوجل المحفوظة لهذه الفترة.")); }).finally(() => { if (active && request === sequence.current) setLoading(false); });
        return () => { active = false; controller.abort(); };
    }, [accountId, dateFrom, dateTo, revision]);
    const records = useMemo(() => {
        const latestAccounts = new Map();
        items.forEach((record) => { const id = record.account_id || record.result?.account_id; if (!latestAccounts.has(id)) latestAccounts.set(id, record); });
        return [...latestAccounts.values()].slice(0, 4);
    }, [items]);
    return <DashboardPanel title={collapsible ? "ذكاء ميزان · إعلانات جوجل" : undefined} dir="rtl" data-testid="google-ads-intelligence-card" className="overflow-hidden rounded-2xl border border-emerald-200 bg-white shadow-sm">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-emerald-200 bg-emerald-50 px-4 py-3"><div><h2 className="font-black text-emerald-950">ذكاء ميزان · إعلانات جوجل</h2><p className="mt-1 text-xs text-emerald-800">توصيات التحليلات المحفوظة للفترة المختارة، بعملة وتوقيت حساب جوجل.</p></div><div className="flex gap-2"><button type="button" disabled={loading} onClick={() => setRevision((value) => value + 1)} className="rounded-xl border border-emerald-200 bg-white px-3 py-2 text-xs font-bold disabled:opacity-40">تحديث توصيات جوجل</button><a href={googleWorkspaceHref(accountId)} className="rounded-xl bg-emerald-700 px-3 py-2 text-xs font-bold text-white">فتح ذكاء جوجل</a></div></header>
        {loading && <p role="status" className="p-5 text-sm text-slate-500">جارٍ قراءة تحليلات جوجل…</p>}
        {error && <p role="alert" className="p-5 text-sm text-amber-900">{error}</p>}
        {!loading && !error && !records.length && <p className="p-5 text-sm leading-7 text-slate-500">لا يوجد تحليل محفوظ لهذه الفترة. افتح مساحة جوجل واختر حسابًا لإعداد تحليل بذكاء ميزان.</p>}
        <div className="grid gap-3 p-4 xl:grid-cols-2">{records.map((record) => {
            const result = record.result || record;
            const period = result.period || {};
            const currentAccount = String(record.account_id || result.account_id || "");
            return <article key={record.analysis_id || `${currentAccount}:${record.created_at}`} className="space-y-3 rounded-xl border border-slate-200 p-4"><h3 className="text-sm font-black">{text(result.account_name || record.account_name, "حساب جوجل")}</h3><p className="text-[11px] leading-5 text-slate-500">{period.date_from || record.date_from} ← {period.date_to || record.date_to} · {period.currency_code || period.currency || "عملة الحساب"} · {period.time_zone || period.timezone || "توقيت الحساب"}</p><p className="text-sm leading-6">{text(result.summary, "راجع توصيات التحليل وأدلته في مساحة جوجل.")}</p>{(result.recommendations || []).slice(0, 3).map((item, index) => <div key={item.recommendation_id || index} className="rounded-xl bg-slate-50 p-3"><h4 className="text-sm font-bold">{text(item.title, "توصية للحملة")}</h4>{item.campaign_name && <p className="mt-1 text-xs text-slate-500">{text(item.campaign_name)}</p>}<p className="mt-1 text-xs leading-6 text-slate-600">{text(item.rationale || item.explanation || item.detail)}</p><GoogleRecommendationAction analysisId={record.analysis_id} recommendation={item} onPreview={onPreview} /><a href={googleWorkspaceHref(currentAccount)} className="mt-2 inline-block text-xs font-bold text-emerald-700">مراجعة التوصية في مساحة جوجل</a></div>)}{(result.limitations || []).slice(0, 2).map((item, index) => <p key={index} className="text-xs leading-6 text-amber-900">{typeof item === "string" ? item : text(item.message || item.detail)}</p>)}<p className="text-[10px] text-slate-400">{record.created_at ? `وقت التحليل: ${new Date(record.created_at).toLocaleString("ar-SA")}` : ""}</p></article>;
        })}</div>
        <p className="border-t border-slate-100 px-4 py-3 text-xs leading-6 text-slate-500">تُراجع التوصيات وتُعتمد من مساحة جوجل عبر نفس بوابة المسوّق. بيانات الأداء المالية في لوحة التحكم تبقى من تقريرها المعتمد.</p>
    </DashboardPanel>;
}
