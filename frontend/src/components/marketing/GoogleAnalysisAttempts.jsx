import { useEffect, useState } from "react";
import { getGoogleAnalysisAttempts } from "../../services/googleAnalysisAttempts";

const STATUS_LABELS = { running: "التحليل جارٍ", complete: "اكتمل التحليل", failed: "لم يكتمل التحليل", cancelled: "توقفت محاولة التحليل", unknown: "حالة غير متاحة" };
const PHASE_LABELS = { native_workspace: "قراءة بيانات جوجل", policy_read: "مراجعة تفويض الحساب", structured_analysis: "مراجعة توصيات الذكاء", save_analysis: "حفظ التحليل", unknown: "مرحلة غير متاحة" };

function timestamp(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "وقت غير متاح" : date.toLocaleString("ar-SA");
}

export default function GoogleAnalysisAttempts({ accountId, dateFrom, dateTo, campaignId, refreshKey }) {
    const [state, setState] = useState({ scope: "", items: [], loading: false, error: false });
    const [revision, setRevision] = useState(0);
    const scope = JSON.stringify([accountId, dateFrom, dateTo, campaignId || null, refreshKey]);
    const scoped = state.scope === scope ? state : { items: [], loading: true, error: false };
    useEffect(() => {
        if (!accountId || !dateFrom || !dateTo) return undefined;
        let active = true;
        const controller = new AbortController();
        setState({ scope, items: [], loading: true, error: false });
        getGoogleAnalysisAttempts({ accountId, dateFrom, dateTo, campaignId, signal: controller.signal })
            .then((items) => { if (active) setState({ scope, items, loading: false, error: false }); })
            .catch(() => { if (active) setState({ scope, items: [], loading: false, error: true }); });
        return () => { active = false; controller.abort(); };
    }, [accountId, dateFrom, dateTo, campaignId, refreshKey, scope, revision]);
    if (!accountId || !dateFrom || !dateTo) return null;
    return <details className="rounded-xl border border-slate-200 bg-white p-3" aria-label="حالة محاولات تحليل جوجل">
        <summary className="cursor-pointer text-sm font-bold">حالة محاولات التحليل</summary>
        <p className="mt-2 text-xs leading-6 text-slate-500">محاولات الحساب والفترة المختارين ضمن أحدث 20 محاولة مسجلة. تحديث السجل لا يعيد التحليل.</p>
        <button type="button" className="mt-2 rounded-lg border px-3 py-2 text-xs font-bold disabled:opacity-40" disabled={scoped.loading} onClick={() => setRevision((value) => value + 1)}>تحديث سجل المحاولات</button>
        {scoped.loading && <p role="status" className="mt-2 text-xs text-slate-500">جارٍ تحميل سجل المحاولات…</p>}
        {scoped.error && <p role="status" className="mt-2 text-xs text-amber-900">تعذر تحميل سجل المحاولات. يمكنك متابعة استخدام صفحة جوجل وتحديث السجل لاحقًا.</p>}
        {!scoped.loading && !scoped.error && !scoped.items.length && <p className="mt-2 text-xs text-slate-500">لا توجد محاولات مسجلة لهذا الاختيار ضمن أحدث المحاولات.</p>}
        <ul className="mt-3 space-y-2">{scoped.items.map((item, index) => <li key={item.analysisId || index} className="rounded-lg bg-slate-50 p-3 text-xs leading-6">
            <p className="font-bold">{STATUS_LABELS[item.status]} · {PHASE_LABELS[item.phase]}</p>
            <p>{timestamp(item.createdAt)}{item.durationMs !== null && ` · ${(item.durationMs / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 })} ثانية`}</p>
            {item.status === "failed" && <p>تعذر اعتماد التحليل لهذه المحاولة. راجع حالة المحاولة عند طلب المساعدة.</p>}
            {item.errorCode && <p>رمز الحالة: <code dir="ltr">{item.errorCode}</code></p>}
            {item.providerWrites === 0 && <p>لم تُغيّر هذه المحاولة حملات جوجل.</p>}
        </li>)}</ul>
    </details>;
}
