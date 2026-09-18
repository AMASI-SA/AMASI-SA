import { useEffect, useRef, useState } from "react";
import api from "../../lib/api";

const GATES = [
    ["contract", "سلامة بيانات التسويق الموحد"],
    ["period_closed", "اكتمال اليوم بتوقيت الحساب"],
    ["coverage", "تغطية الحملات والمجموعات والإعلانات"],
    ["reconciliation", "تطابق الإجماليات"],
    ["freshness", "حداثة المزامنة"],
    ["attribution", "اكتمال ربط الطلبات بالحملات"],
    ["financial_coverage", "اكتمال التكاليف والربحية"],
];

function validScope(data, accountId, date) {
    return data?.phase === "decision-intelligence-phase5-v1"
        && data?.mode === "recommendation_shadow"
        && data?.account?.id === accountId
        && data?.period?.date_from === date
        && data?.period?.date_to === date;
}

export default function SnapchatDecisionReadiness({ accountId, date }) {
    const [state, setState] = useState({ status: "idle" });
    const request = useRef(0);
    useEffect(() => {
        request.current += 1;
        setState({ status: "idle" });
        return () => { request.current += 1; };
    }, [accountId, date]);

    async function inspect() {
        const current = ++request.current;
        setState({ status: "loading" });
        try {
            const { data } = await api.get("/decision-intelligence/phase5/shadow", {
                params: { provider: "snapchat_ads", date_from: date, date_to: date, max_candidates: 1 },
            });
            if (request.current !== current) return;
            if (!validScope(data, accountId, date)) throw new Error("scope_mismatch");
            setState({ status: "done", data, accountId, date });
        } catch {
            if (request.current === current) setState({ status: "error" });
        }
    }

    const data = state.accountId === accountId && state.date === date ? state.data : null;
    const ready = data?.decision_ready === true && data?.period?.closed === true
        && GATES.every(([key]) => data.gates?.[key]?.passed === true);
    const financial = data?.gates?.financial_coverage;
    const attribution = data?.gates?.attribution?.campaign_attribution;
    return <section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="فحص جاهزية سناب للذكاء">
        <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
                <h2 className="font-black">جاهزية سناب للذكاء</h2>
                <p className="mt-1 text-xs text-slate-600">آخر يوم مغلق: {date || "بانتظار تحديد اليوم"} · فحص للقراءة فقط، لا يفعّل قرارات أو يعدّل الحملات.</p>
            </div>
            <button type="button" onClick={inspect} disabled={!accountId || !date || state.status === "loading"} className="rounded-lg border px-3 py-2 text-sm font-bold disabled:opacity-50">
                {state.status === "loading" ? "جارٍ فحص الجاهزية…" : "فحص جاهزية الذكاء"}
            </button>
        </div>
        {state.status === "error" && <p role="alert" className="mt-3 text-sm text-red-700">تعذر التحقق من جاهزية هذا الحساب والفترة. يلزم دخول المالك وإعادة الفحص.</p>}
        {data && <div className="mt-3" aria-live="polite">
            <p className={`font-bold ${ready ? "text-emerald-700" : "text-amber-800"}`}>{ready ? "اجتازت الفترة شروط البيانات للتوصيات التجريبية" : "الجاهزية غير مكتملة"}</p>
            <ul className="mt-2 grid gap-2 text-sm sm:grid-cols-2">
                {GATES.map(([key, label]) => <li key={key}>{label}: {data.gates?.[key]?.passed === true ? "مكتمل" : data.gates?.[key]?.passed === false ? "غير مكتمل" : "لم يُتحقق منه"}</li>)}
            </ul>
            {Number.isInteger(attribution?.unmatched_orders) && <p className="mt-2 text-sm">طلبات غير مرتبطة خلال يوم الفحص: {attribution.unmatched_orders}</p>}
            {Number.isInteger(financial?.missing_cost_orders) && <p className="mt-2 text-sm">طلبات ناقصة التكلفة خلال يوم الفحص: {financial.missing_cost_orders}</p>}
            <p className="mt-2 text-xs text-slate-600">نتيجة الفحص تخص هذا اليوم فقط. لا تعني تفعيل الربط أو التنفيذ الآلي.</p>
        </div>}
    </section>;
}
