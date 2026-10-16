import React from "react";

const STATUSES = {
    blocked: "تعذر إدخال الحساب في التحليل بسبب نقص الأدلة",
    no_candidates: "لم تدخل كيانات مؤهلة للاقتراح في هذه الدورة",
    no_recommendation: "دخلت بيانات الحساب، ولم تصدر له توصية في هذه الدورة",
    recommendations_available: "صدرت توصيات لهذا الحساب",
    analysis_failed: "تعذر إكمال تحليل الذكاء لهذه الدورة",
    unknown: "لم تُحفظ تفاصيل تحليل الحساب لهذه الدورة",
};
const GATES = {
    contract: "سلامة البيانات الموحدة",
    period_closed: "اكتمال الفترة بتوقيت الحساب",
    coverage: "تغطية الحملات والمجموعات والإعلانات",
    reconciliation: "تطابق الإجماليات",
    freshness: "حداثة المزامنة",
    attribution: "ربط الطلبات بالحملات",
    financial_coverage: "اكتمال التكاليف والربحية",
};

export default function CampaignAccountAnalysis({ snapshot }) {
    if (!snapshot?.available && !snapshot?.snapshot_id) return null;
    const accounts = Array.isArray(snapshot.account_analysis) ? snapshot.account_analysis : [];
    return <section aria-label="تغطية الحسابات في دورة التحليل" className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-xs">
        <h3 className="font-extrabold text-slate-900">حالة الحسابات في هذه الدورة</h3>
        <p className="mt-1 leading-6 text-slate-600">تعرض الصفحة نتائج آخر دورة فقط. غياب التوصية لا يعني إيقاف الحملات أو الموافقة على استمرارها.</p>
        {!accounts.length ? <p className="mt-2 text-amber-800">لم تُحفظ تفاصيل تغطية الحسابات في هذه الدورة؛ لا يمكن تحديد سبب غياب توصيات حساب منها.</p>
            : <div className="mt-2 grid gap-2 md:grid-cols-2">{accounts.map((account) => <div key={`${account.provider}:${account.account_id}`} className="rounded-lg border bg-white p-3">
                <p className="font-bold">{account.provider === "snapchat" ? "سناب" : account.provider === "meta" ? "ميتا" : account.provider} · {account.account_name || account.account_id || "الحساب غير محدد"}</p>
                <p className="mt-1 leading-6">{STATUSES[account.status] || STATUSES.unknown}</p>
                {account.period?.date_from && <p className="mt-1 text-slate-500">فترة تحليل الحساب: <span dir="ltr">{account.period.date_from} → {account.period.date_to}</span>{account.period.timezone ? ` · ${account.period.timezone}` : ""}</p>}
                {account.blocked_by?.length > 0 && <p className="mt-1 text-amber-800">يحتاج استكمال: {[...new Set(account.blocked_by.map((gate) => GATES[gate] || "التحقق من بيانات الحساب"))].join("، ")}</p>}
                {Number.isInteger(account.recommendations_count) && <p className="mt-1 text-slate-500">توصيات الدورة: {account.recommendations_count}</p>}
            </div>)}</div>}
    </section>;
}
