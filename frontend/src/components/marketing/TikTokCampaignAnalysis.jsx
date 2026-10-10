import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const FOCUS = { monitor: "متابعة الأداء", tracking: "مراجعة التتبع", creative: "مراجعة المحتوى" };

export default function TikTokCampaignAnalysis({ campaign, dateFrom, dateTo, onClose }) {
    const [result, setResult] = useState(null);
    const [error, setError] = useState("");
    const [loading, setLoading] = useState(false);
    const [retry, setRetry] = useState(0);
    const sequence = useRef(0);
    const accountId = campaign?.accountId, campaignId = campaign?.campaignId;
    useEffect(() => {
        const controller = new AbortController();
        const request = ++sequence.current;
        let active = true;
        setResult(null); setError("");
        if (!accountId || !campaignId) { setLoading(false); return () => { active = false; }; }
        setLoading(true);
        api.post("/integrations-v2/tiktok_ads/analyze-campaign",
            { account_id: accountId, campaign_id: campaignId, from_date: dateFrom, to_date: dateTo },
            { signal: controller.signal }).then(({ data }) => {
                if (active && request === sequence.current) setResult(data);
            }).catch((failure) => {
                if (active && request === sequence.current) setError(failure?.response?.data?.detail?.message || "تعذر تحليل الحملة؛ حاول لاحقًا.");
            }).finally(() => {
                if (active && request === sequence.current) setLoading(false);
            });
        return () => { active = false; controller.abort(); };
    }, [accountId, campaignId, dateFrom, dateTo, retry]);

    if (!campaign) return null;
    return <aside id="tiktok-native-ai-panel" data-testid="tiktok-native-ai-analysis" className="rounded-2xl border border-violet-200 bg-violet-50 p-4 space-y-3" dir="rtl">
        <div className="flex flex-wrap items-start justify-between gap-3">
            <div><h3 className="font-black">تحليل حملة TikTok</h3><p className="font-semibold">{campaign.name}</p>
                <p className="text-xs text-slate-500">{dateFrom} ← {dateTo}</p></div>
            <button type="button" onClick={onClose} className="rounded-lg border bg-white px-3 py-2">إغلاق التحليل</button>
        </div>
        {loading && <p role="status">جاري تحليل الحملة…</p>}
        {error && <p role="alert" className="rounded-lg bg-rose-50 p-3 text-rose-800">{error}</p>}
        {result && <div className="space-y-2" aria-live="polite">
            <p className="font-bold text-violet-800">{FOCUS[result.recommendation?.focus] || "متابعة الأداء"}{result.cached ? " · تحليل حديث محفوظ" : ""}</p>
            <p className="leading-7">{result.recommendation?.summary}</p>
            <p className="rounded-lg bg-white p-3 leading-7">{result.recommendation?.next_step}</p>
            <p className="text-xs text-slate-600">سجلات طلبات مؤكدة الإسناد في البيانات المتاحة: {result.context?.salla_evidence?.exact_campaign_id_records ?? "غير معروف"}</p>
        </div>}
        <p className="text-xs leading-6 text-slate-600">التحليل يقرأ هذه الحملة فقط. نتائج سلة المالية والأرباح غير مكتملة؛ تُراجع التوصية قبل أي تعديل للميزانية.</p>
        {!loading && <button type="button" onClick={() => setRetry((value) => value + 1)} className="rounded-lg bg-violet-700 px-4 py-2 font-bold text-white">إعادة التحليل</button>}
    </aside>;
}
