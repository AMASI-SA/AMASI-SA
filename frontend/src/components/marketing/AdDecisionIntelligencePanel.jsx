import { useEffect, useRef, useState } from "react";
import { Brain, MagnifyingGlass, ShieldCheck, WarningCircle } from "@phosphor-icons/react";

import {
    adDecisionError,
    diagnoseAdBusinessChange,
    reviewAdaptiveSnapchat,
} from "../../services/adDecisionLearning";

const METRICS = [
    { value: "sales", label: "المبيعات" },
    { value: "orders", label: "الطلبات" },
    { value: "contribution_profit", label: "مكسب المساهمة" },
    { value: "roas", label: "العائد الإعلاني ROAS" },
    { value: "cpa", label: "تكلفة الطلب CPA" },
];

const CLASSIFICATIONS = {
    likely_contributor: "مساهم محتمل",
    association: "ارتباط زمني",
    contradictory: "النتيجة لا توافق التوقع",
    insufficient: "الأدلة غير كافية",
};

const ACTIONS = {
    observe: "المراقبة",
    investigate: "التحقق أولًا",
    increase_budget: "دراسة رفع الميزانية",
    decrease_budget: "دراسة خفض الميزانية",
    pause: "دراسة الإيقاف المؤقت",
    activate: "دراسة التشغيل",
};

const CAVEATS = {
    decision_timing_and_direction_support_association_not_causation: "توافق التوقيت والاتجاه يدل على ارتباط، ولا يثبت أن التعديل هو السبب.",
    temporal_association_is_not_causation: "التزامن لا يثبت السببية.",
    salary_season_and_trend_context_is_not_a_basis_without_verified_ledger_evidence: "الراتب والموسم والترند تبقى سياقًا فقط ما لم يوجد لها دليل موثّق.",
    contribution_profit_excludes_payment_shipping_bnpl_and_operating_allocations: "مكسب المساهمة هنا لا يشمل بعض تكاليف الدفع والشحن والتشغيل.",
    historical_periods_use_current_mezan_catalog_cost_resolution: "تكلفة المنتجات التاريخية محسوبة وفق بيانات التكلفة المتاحة حاليًا.",
    whole_store_contribution_subtracts_selected_snapchat_spend_only: "مكسب المتجر يخصم إنفاق حسابات سناب المحددة فقط.",
    multiple_campaign_decisions_overlap_the_same_window: "توجد تعديلات متداخلة في الفترة نفسها، لذلك لا يمكن عزل أثر تعديل واحد.",
    scope_not_isolated_to_one_campaign: "القياس غير معزول على حملة واحدة.",
    selected_period_contains_pre_decision_results: "الفترة المختارة تضم نتائج سبقت التعديل.",
    post_decision_only_measurement_required: "يلزم قياس مستقل للمدة التي تلت التعديل.",
    baseline_contains_post_decision_results: "فترة المقارنة تضم نتائج بعد التعديل.",
    pre_and_post_decision_slices_required: "يلزم فصل القياس قبل التعديل وبعده.",
    selected_period_has_unresolved_snapchat_attribution: "بعض طلبات الفترة لم يُحسم إسنادها إلى سناب.",
    snapchat_campaign_performance_sync_incomplete_for_previous_and_selected_windows: "بيانات صرف سناب ليست مكتملة في فترتي المقارنة، لذلك لا يمكن الحكم على الصرف أو ROAS أو CPA أو المكسب.",
    snapchat_campaign_performance_sync_incomplete_requested_metric_not_measurable: "لم تكتمل بيانات سناب اللازمة لهذا المؤشر في فترتي المقارنة، لذلك لم يعرض ميزان تغيرًا رقميًا.",
};

function localDate(value) {
    const year = value.getFullYear();
    const month = String(value.getMonth() + 1).padStart(2, "0");
    const day = String(value.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
}

function initialPeriod() {
    const today = new Date();
    const from = new Date(today);
    from.setDate(from.getDate() - 6);
    return { dateFrom: localDate(from), dateTo: localDate(today) };
}

function canceled(error) {
    return error?.name === "CanceledError" || error?.name === "AbortError";
}

function percent(value) {
    if (value === null || value === undefined || value === "") return "غير متاح";
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "غير متاح";
    return `${parsed > 0 ? "+" : ""}${parsed.toLocaleString("ar-SA", { maximumFractionDigits: 1 })}%`;
}

function confidence(value) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "غير محددة";
    return `${Math.round(parsed * 100).toLocaleString("ar-SA")}%`;
}

function caveatLabel(value) {
    return CAVEATS[value] || String(value || "").replaceAll("_", " ");
}

export default function AdDecisionIntelligencePanel({ accountId }) {
    const defaults = useRef(initialPeriod()).current;
    const [dateFrom, setDateFrom] = useState(defaults.dateFrom);
    const [dateTo, setDateTo] = useState(defaults.dateTo);
    const [metric, setMetric] = useState("sales");
    const [userSuggestion, setUserSuggestion] = useState("");
    const [diagnosis, setDiagnosis] = useState(null);
    const [adaptiveReview, setAdaptiveReview] = useState(null);
    const [diagnosisLoading, setDiagnosisLoading] = useState(false);
    const [adaptiveLoading, setAdaptiveLoading] = useState(false);
    const [diagnosisError, setDiagnosisError] = useState("");
    const [adaptiveError, setAdaptiveError] = useState("");
    const diagnosisRequestRef = useRef(0);
    const adaptiveRequestRef = useRef(0);
    const diagnosisAbortRef = useRef(null);
    const adaptiveAbortRef = useRef(null);

    function invalidateDiagnosis() {
        diagnosisRequestRef.current += 1;
        diagnosisAbortRef.current?.abort();
        diagnosisAbortRef.current = null;
        setDiagnosis(null);
        setDiagnosisError("");
        setDiagnosisLoading(false);
    }

    function invalidateAdaptiveReview() {
        adaptiveRequestRef.current += 1;
        adaptiveAbortRef.current?.abort();
        adaptiveAbortRef.current = null;
        setAdaptiveReview(null);
        setAdaptiveError("");
        setAdaptiveLoading(false);
    }

    useEffect(() => {
        diagnosisRequestRef.current += 1;
        adaptiveRequestRef.current += 1;
        diagnosisAbortRef.current?.abort();
        adaptiveAbortRef.current?.abort();
        diagnosisAbortRef.current = null;
        adaptiveAbortRef.current = null;
        setDiagnosis(null);
        setAdaptiveReview(null);
        setDiagnosisError("");
        setAdaptiveError("");
        setDiagnosisLoading(false);
        setAdaptiveLoading(false);
    }, [accountId]);

    useEffect(() => () => {
        diagnosisRequestRef.current += 1;
        adaptiveRequestRef.current += 1;
        diagnosisAbortRef.current?.abort();
        adaptiveAbortRef.current?.abort();
    }, []);

    async function runDiagnosis() {
        if (!dateFrom || !dateTo || dateFrom > dateTo) {
            setDiagnosisError("تحقق من الفترة: يجب أن يكون تاريخ البداية قبل تاريخ النهاية.");
            setDiagnosis(null);
            return;
        }
        diagnosisAbortRef.current?.abort();
        const controller = new AbortController();
        diagnosisAbortRef.current = controller;
        const requestId = ++diagnosisRequestRef.current;
        setDiagnosisLoading(true);
        setDiagnosisError("");
        try {
            const result = await diagnoseAdBusinessChange({
                dateFrom,
                dateTo,
                metric,
                accountId,
                signal: controller.signal,
            });
            if (requestId === diagnosisRequestRef.current) setDiagnosis(result);
        } catch (error) {
            if (requestId === diagnosisRequestRef.current && !canceled(error)) {
                setDiagnosisError(adDecisionError(error));
                setDiagnosis(null);
            }
        } finally {
            if (requestId === diagnosisRequestRef.current) {
                setDiagnosisLoading(false);
                diagnosisAbortRef.current = null;
            }
        }
    }

    async function runAdaptiveReview() {
        adaptiveAbortRef.current?.abort();
        const controller = new AbortController();
        adaptiveAbortRef.current = controller;
        const requestId = ++adaptiveRequestRef.current;
        setAdaptiveLoading(true);
        setAdaptiveError("");
        try {
            const result = await reviewAdaptiveSnapchat({
                accountId,
                maxEntities: 5,
                userSuggestions: userSuggestion.trim() ? [userSuggestion.trim()] : [],
                signal: controller.signal,
            });
            if (requestId === adaptiveRequestRef.current) setAdaptiveReview(result);
        } catch (error) {
            if (requestId === adaptiveRequestRef.current && !canceled(error)) {
                setAdaptiveError(adDecisionError(error));
                setAdaptiveReview(null);
            }
        } finally {
            if (requestId === adaptiveRequestRef.current) {
                setAdaptiveLoading(false);
                adaptiveAbortRef.current = null;
            }
        }
    }

    const strongest = diagnosis?.likely_contributors?.[0] || diagnosis?.decisions?.[0];
    const classification = CLASSIFICATIONS[strongest?.classification] || "الأدلة غير كافية";
    const readOnlyConfirmed = adaptiveReview
        && adaptiveReview.proposals_created === 0
        && adaptiveReview.provider_write_reached === false;

    return (
        <section className="rounded-2xl border border-indigo-200 bg-gradient-to-br from-white to-indigo-50/60 p-4 shadow-sm" data-testid="ad-decision-intelligence">
            <div className="flex items-start gap-3">
                <span className="rounded-xl bg-indigo-100 p-2 text-indigo-700"><Brain size={23} weight="duotone" /></span>
                <div>
                    <h3 className="font-black text-slate-950">اسأل ذكاء ميزان</h3>
                    <p className="mt-1 text-xs font-semibold leading-5 text-slate-600">
                        يقارن النتائج بالتعديلات المسجلة. الارتباط الزمني ليس إثباتًا للسبب، والنتائج هي الأساس.
                    </p>
                </div>
            </div>

            <div className="mt-4 grid gap-3 md:grid-cols-3">
                <label className="text-xs font-black text-slate-700">
                    من تاريخ
                    <input
                        type="date"
                        value={dateFrom}
                        onChange={(event) => { invalidateDiagnosis(); setDateFrom(event.target.value); }}
                        className="mt-1 block min-h-10 w-full rounded-xl border border-slate-200 bg-white px-3 font-mono text-sm"
                    />
                </label>
                <label className="text-xs font-black text-slate-700">
                    إلى تاريخ
                    <input
                        type="date"
                        value={dateTo}
                        onChange={(event) => { invalidateDiagnosis(); setDateTo(event.target.value); }}
                        className="mt-1 block min-h-10 w-full rounded-xl border border-slate-200 bg-white px-3 font-mono text-sm"
                    />
                </label>
                <label className="text-xs font-black text-slate-700">
                    النتيجة المراد تفسيرها
                    <select
                        value={metric}
                        onChange={(event) => { invalidateDiagnosis(); setMetric(event.target.value); }}
                        className="mt-1 block min-h-10 w-full rounded-xl border border-slate-200 bg-white px-3 text-sm font-bold"
                    >
                        {METRICS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                    </select>
                </label>
            </div>

            <button
                type="button"
                onClick={runDiagnosis}
                disabled={diagnosisLoading || !accountId}
                data-testid="run-ad-business-diagnosis"
                className="mt-3 inline-flex min-h-10 items-center gap-2 rounded-xl bg-slate-950 px-4 text-xs font-black text-white disabled:opacity-50"
            >
                <MagnifyingGlass size={17} />{diagnosisLoading ? "يجري التشخيص…" : "فسّر تغيّر النتيجة"}
            </button>

            {diagnosisError && (
                <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-bold text-rose-800">
                    <WarningCircle size={17} weight="fill" className="ml-1 inline" />{diagnosisError}
                </p>
            )}

            {diagnosis && (
                <div className="mt-3 space-y-3 rounded-xl border border-slate-200 bg-white p-3" data-testid="ad-business-diagnosis-result">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="text-sm font-black text-slate-950">التصنيف: {classification}</p>
                        <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black text-slate-700">
                            التغيّر {percent(diagnosis.headline?.delta_pct)}
                        </span>
                    </div>
                    <p className="text-xs font-bold text-amber-800">هذا تشخيص احتمالي للارتباط، وليس إثباتًا أن التعديل سبّب النتيجة.</p>
                    <div>
                        <p className="text-xs font-black text-slate-700">التعديلات المساهمة على الأرجح</p>
                        {diagnosis.likely_contributors.length ? (
                            <ul className="mt-1 space-y-1 text-xs font-semibold text-slate-600">
                                {diagnosis.likely_contributors.map((item, index) => (
                                    <li key={item.decision_id || `${item.entity_id}-${index}`} className="rounded-lg bg-slate-50 px-3 py-2">
                                        {CLASSIFICATIONS[item.classification] || item.classification} · ثقة {confidence(item.confidence)}
                                        {item.decision_id ? ` · تعديل ${item.decision_id}` : ""}
                                    </li>
                                ))}
                            </ul>
                        ) : <p className="mt-1 text-xs font-semibold text-slate-500">لا يوجد تعديل يمكن ترجيحه بالأدلة الحالية.</p>}
                    </div>
                    {!!diagnosis.caveats.length && (
                        <div>
                            <p className="text-xs font-black text-slate-700">حدود التشخيص</p>
                            <ul className="mt-1 list-disc space-y-1 pr-5 text-xs font-semibold leading-5 text-slate-600">
                                {diagnosis.caveats.map((item) => <li key={item}>{caveatLabel(item)}</li>)}
                            </ul>
                        </div>
                    )}
                </div>
            )}

            <div className="my-4 border-t border-indigo-100" />

            <label className="text-xs font-black text-slate-700">
                ملاحظتك للذكاء (اقتراح غير موثّق، وليست حقيقة)
                <textarea
                    value={userSuggestion}
                    onChange={(event) => { invalidateAdaptiveReview(); setUserSuggestion(event.target.value); }}
                    rows={2}
                    maxLength={2000}
                    placeholder="مثال: قد تكون سيولة العملاء أخف في منتصف الشهر — تحقّق ولا تفترض."
                    className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-semibold"
                />
            </label>
            <div className="mt-3 flex flex-wrap items-center gap-3">
                <button
                    type="button"
                    onClick={runAdaptiveReview}
                    disabled={adaptiveLoading || !accountId}
                    data-testid="run-adaptive-snapchat-review"
                    className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-indigo-700 px-4 text-xs font-black text-white disabled:opacity-50"
                >
                    <Brain size={17} />{adaptiveLoading ? "يراجع الأدلة…" : "راجع الحساب بالذكاء"}
                </button>
                <p className="inline-flex items-center gap-1 text-xs font-bold text-emerald-800">
                    <ShieldCheck size={17} weight="fill" />مراجعة فقط: لا إنشاء مقترح ولا كتابة إلى Snapchat
                </p>
            </div>

            {adaptiveError && (
                <p className="mt-3 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-bold text-rose-800">
                    <WarningCircle size={17} weight="fill" className="ml-1 inline" />{adaptiveError}
                </p>
            )}

            {adaptiveReview && (
                <div className="mt-3 space-y-3" data-testid="adaptive-snapchat-review-result">
                    <p
                        data-testid="adaptive-review-read-only-proof"
                        className={`rounded-xl border p-3 text-xs font-black ${readOnlyConfirmed ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-rose-200 bg-rose-50 text-rose-800"}`}
                    >
                        {readOnlyConfirmed
                            ? "تمت المراجعة دون إنشاء أي مقترح ودون الوصول إلى كتابة مزوّد الإعلانات."
                            : "لم يتأكد وضع القراءة فقط؛ لن تعرض الواجهة هذه النتيجة كقرار قابل للتنفيذ."}
                    </p>
                    {adaptiveReview.judgments.length ? adaptiveReview.judgments.map((item, index) => (
                        <article key={`${item.entity_type}-${item.entity_id || index}`} className="rounded-xl border border-indigo-100 bg-white p-3">
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <p className="text-sm font-black text-slate-950">{ACTIONS[item.recommended_action] || item.recommended_action}</p>
                                <span className="text-xs font-black text-slate-500">ثقة {confidence(item.confidence)}</span>
                            </div>
                            <p className="mt-1 font-mono text-[11px] font-bold text-slate-500">{item.entity_type} · {item.entity_id || "غير محدد"}</p>
                            <p className="mt-2 text-xs font-bold leading-5 text-slate-700">{item.reason_ar || "لم يقدم الذكاء سببًا كافيًا."}</p>
                            {!!item.uncertainties.length && (
                                <div className="mt-2 rounded-lg bg-amber-50 px-3 py-2">
                                    <p className="text-xs font-black text-amber-900">ما يزال غير مؤكد</p>
                                    <ul className="mt-1 list-disc space-y-1 pr-5 text-xs font-semibold text-amber-800">
                                        {item.uncertainties.map((uncertainty) => <li key={uncertainty}>{uncertainty}</li>)}
                                    </ul>
                                </div>
                            )}
                        </article>
                    )) : (
                        <p className="rounded-xl border border-dashed border-slate-300 bg-white p-4 text-center text-xs font-bold text-slate-500">
                            لا توجد حالة تحتاج حكمًا إضافيًا الآن.
                        </p>
                    )}
                </div>
            )}
        </section>
    );
}
