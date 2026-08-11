import { useEffect, useMemo, useState } from "react";
import {
    ArrowSquareOut, CheckCircle, Robot, SpinnerGap, WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    applyHighConfidenceGoogleTaxonomy,
    getGoogleTaxonomyPilot,
    getLatestGoogleTaxonomyPilot,
    startGoogleTaxonomyPilot,
} from "../../services/aiStoreOperations";

const TERMINAL = new Set(["completed", "completed_with_errors", "failed", "credit_exhausted"]);
const COMPLETE = new Set(["completed", "completed_with_errors"]);
const CANDIDATE_RETRIEVER_VERSION = 2;

const STATUS_LABELS = {
    high_confidence: "جاهز للاعتماد ≥90%",
    review_required: "مراجعة 70–89%",
    review_required_existing_category: "مراجعة — يوجد تصنيف حالي",
    low_confidence: "ثقة منخفضة",
    no_change: "لا تغيير",
    missing_data: "بيانات ناقصة",
    ai_failed: "فشل AI",
};

function statusClass(value) {
    if (value === "high_confidence" || value === "no_change") return "bg-emerald-50 text-emerald-800 border-emerald-200";
    if (String(value).startsWith("review_required")) return "bg-amber-50 text-amber-800 border-amber-200";
    if (value === "ai_failed" || value === "missing_data") return "bg-rose-50 text-rose-800 border-rose-200";
    return "bg-slate-50 text-slate-700 border-slate-200";
}

function openProduct(productId) {
    if (!productId || typeof window === "undefined") return;
    const url = new URL(window.location.href);
    url.searchParams.set("product", productId);
    window.location.href = `${url.pathname}${url.search}${url.hash}`;
}

export default function ProductGoogleTaxonomyPilotPanel() {
    const [payload, setPayload] = useState(null);
    const [limit, setLimit] = useState(200);
    const [loading, setLoading] = useState(true);
    const [starting, setStarting] = useState(false);
    const [retrying, setRetrying] = useState(false);
    const [applying, setApplying] = useState(false);
    const [expanded, setExpanded] = useState(true);

    const run = payload?.run || null;
    const items = useMemo(() => payload?.items || [], [payload?.items]);
    const counters = run?.counters || {};
    const progress = run?.progress || {};
    const pendingHighConfidence = useMemo(
        () => items.filter((row) => row.decision_status === "high_confidence" && row.apply_status === "pending").length,
        [items],
    );
    const pendingRetry = useMemo(
        () => items.filter((row) => (
            row.apply_status !== "applied"
            && ["review_required", "review_required_existing_category", "low_confidence"].includes(row.decision_status)
            && Number(row.candidate_retriever_version || 1) < CANDIDATE_RETRIEVER_VERSION
        )).length,
        [items],
    );

    async function loadLatest() {
        try {
            const result = await getLatestGoogleTaxonomyPilot();
            setPayload(result);
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error((typeof detail === "string" ? detail : detail?.message) || "تعذر تحميل حالة Pilot تصنيف Google");
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { loadLatest(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        if (!run?.run_id || TERMINAL.has(run.status)) return undefined;
        const timer = window.setInterval(async () => {
            try {
                const result = await getGoogleTaxonomyPilot(run.run_id);
                setPayload(result);
            } catch {
                // Keep the last known run visible; the next poll may recover.
            }
        }, 2500);
        return () => window.clearInterval(timer);
    }, [run?.run_id, run?.status]);

    async function startPilot() {
        setStarting(true);
        try {
            const selectionMode = limit === 200 ? "next_unseen" : "sample";
            const result = await startGoogleTaxonomyPilot(limit, selectionMode);
            setPayload(result);
            setExpanded(true);
            toast.success(limit === 200
                ? "بدأت دفعة التصنيف التالية على المنتجات غير المحللة سابقًا. لا توجد كتابة إلى Salla."
                : `بدأ Pilot تصنيف Google على ${limit} منتجًا. لا توجد كتابة إلى Salla.`);
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error((typeof detail === "string" ? detail : detail?.message) || "تعذر بدء Pilot تصنيف Google");
        } finally {
            setStarting(false);
        }
    }

    async function applyHighConfidence() {
        if (!run?.run_id || !pendingHighConfidence) return;
        setApplying(true);
        try {
            const confirmation = payload?.apply_confirmation || "اعتماد تصنيفات Google عالية الثقة في ميزان";
            const result = await applyHighConfidenceGoogleTaxonomy(run.run_id, confirmation);
            setPayload(result);
            const applied = result?.apply_result?.applied || 0;
            const stale = result?.apply_result?.stale || 0;
            toast.success(`تم اعتماد ${applied} تصنيف عالي الثقة داخل ميزان${stale ? `، وتجاوز ${stale} منتجًا تغيّر بعد التحليل` : ""}.`);
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error((typeof detail === "string" ? detail : detail?.message) || "تعذر اعتماد نتائج Pilot");
        } finally {
            setApplying(false);
        }
    }

    async function retryReviewQueue() {
        if (!pendingRetry) return;
        setRetrying(true);
        try {
            const result = await startGoogleTaxonomyPilot(
                Math.max(20, Math.min(200, pendingRetry)),
                "retry_review",
            );
            setPayload(result);
            setExpanded(true);
            toast.success(`بدأت إعادة تحليل ${pendingRetry} منتجًا من المراجعة والثقة المنخفضة فقط. لا توجد كتابة إلى Salla.`);
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error((typeof detail === "string" ? detail : detail?.message) || "تعذر إعادة تحليل قائمة المراجعة");
        } finally {
            setRetrying(false);
        }
    }

    return <section className="rounded-2xl border border-violet-200 bg-white shadow-sm sm:rounded-3xl" dir="rtl" data-testid="google-taxonomy-ai-pilot">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-violet-100 bg-violet-50 px-4 py-3 sm:px-5">
            <div className="min-w-0">
                <h2 className="flex items-center gap-2 font-black text-slate-900"><Robot className="text-violet-700" /> AI Product Manager — Google Category Pilot</h2>
                <p className="mt-1 text-xs text-slate-500">Pilot محكوم لـ20–50 منتجًا، أو دفعة 200، أو إعادة محاولة النتائج غير المحسومة بمحرك مرشحات أحدث. لا توجد أي كتابة إلى Salla.</p>
            </div>
            <div className="flex items-center gap-2">
                <select value={limit} onChange={(event) => setLimit(Number(event.target.value))} disabled={starting || (run && !TERMINAL.has(run.status))} className="rounded-xl border bg-white px-3 py-2 text-sm font-bold">
                    <option value={20}>20 منتج</option>
                    <option value={30}>30 منتج</option>
                    <option value={50}>50 منتج</option>
                    <option value={200}>200 منتج — الدفعة التالية</option>
                </select>
                <button disabled={starting || (run && !TERMINAL.has(run.status))} onClick={startPilot} className="rounded-xl bg-violet-700 px-4 py-2 text-sm font-black text-white disabled:opacity-50">
                    {starting ? <><SpinnerGap className="ml-1 inline animate-spin" /> بدء…</> : limit === 200 ? "تشغيل الدفعة التالية" : "تشغيل Pilot"}
                </button>
                <button type="button" onClick={() => setExpanded((value) => !value)} className="rounded-xl border bg-white px-3 py-2 text-xs font-bold">{expanded ? "إخفاء" : "إظهار"}</button>
            </div>
        </div>

        {expanded && <div className="space-y-4 p-4 sm:p-5">
            <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 text-xs font-bold text-sky-900">
                قواعد الثقة: ≥90% جاهز للاعتماد البشري الجماعي داخل ميزان · 70–89% مراجعة · أقل من 70% لا يُكتب · أي تصنيف حالي مختلف لا يُستبدل جماعيًا مهما كانت الثقة.
            </div>

            {loading ? <div className="p-8 text-center text-slate-400"><SpinnerGap className="inline animate-spin" /> جاري تحميل آخر Pilot…</div> : !run ? <div className="rounded-xl border border-dashed p-6 text-center text-sm text-slate-500">لم يتم تشغيل Pilot بعد.</div> : <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="text-sm"><b>الحالة:</b> {run.status === "running" || run.status === "queued" ? <span className="text-violet-700"><SpinnerGap className="ml-1 inline animate-spin" /> جاري التحليل</span> : run.status === "credit_exhausted" ? <span className="text-amber-700">متوقف — رصيد OpenAI غير كافٍ</span> : run.status === "failed" ? <span className="text-rose-700">فشل</span> : run.status === "completed_with_errors" ? <span className="text-amber-700">مكتمل مع نتائج تحتاج مراجعة</span> : <span className="text-emerald-700">مكتمل</span>}</div>
                    <div className="text-xs text-slate-500">Run: <span className="font-mono">{String(run.run_id || "").slice(0, 10)}</span> · Model: {run.model || "—"} · Taxonomy: {run.taxonomy_version || "—"}</div>
                </div>

                {!TERMINAL.has(run.status) && <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-violet-200 bg-violet-50 p-3 text-sm text-violet-950">
                    <div>تم حفظ <b className="num">{Number(progress.saved || 0).toLocaleString("en-US")}</b> من <b className="num">{Number(counters.selected || run.requested_limit || 0).toLocaleString("en-US")}</b> منتج حتى الآن</div>
                    <div>{Number(run.resume_count || 0) > 0 ? <>استؤنف تلقائيًا <b className="num">{Number(run.resume_count).toLocaleString("en-US")}</b> مرة</> : "الحفظ المرحلي والاستئناف التلقائي مفعّلان"}</div>
                </div>}

                <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-11">
                    {[
                        ["المحدد", counters.selected],
                        ["المحلل", counters.analyzed],
                        ["لا تغيير", counters.no_change],
                        ["≥90%", counters.high_confidence],
                        ["مراجعة", counters.review_required],
                        ["<70%", counters.low_confidence],
                        ["فشل AI", counters.ai_failed],
                        ["فحص بصري", counters.visual_checked],
                        ["فشل بصري", counters.visual_failed],
                        ["ناقص", counters.missing_data],
                        ["اعتمد", counters.applied],
                    ].map(([label, value]) => <div key={label} className="rounded-xl border bg-slate-50 p-2 text-center"><div className="num text-lg font-black text-slate-900">{Number(value || 0).toLocaleString("en-US")}</div><div className="text-[11px] text-slate-500">{label}</div></div>)}
                </div>

                {run.coverage && <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-violet-200 bg-violet-50 p-3 text-sm text-violet-950">
                    <div>تغطية الكتالوج: <b className="num">{Number(run.coverage.seen_after || 0).toLocaleString("en-US")}</b> من <b className="num">{Number(run.coverage.total_products || 0).toLocaleString("en-US")}</b> منتج</div>
                    <div>المتبقي بعد هذه الدفعة: <b className="num">{Number(run.coverage.remaining_after || 0).toLocaleString("en-US")}</b></div>
                </div>}

                {run.retry_queue && <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
                    <div>إعادة محاولة غير المحسومة: <b className="num">{Number(run.retry_queue.selected_now || 0).toLocaleString("en-US")}</b> منتج</div>
                    <div>إصدار محرك المرشحات: <b className="num">{Number(run.retry_queue.candidate_retriever_version || 0).toLocaleString("en-US")}</b></div>
                </div>}

                {run.error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800"><WarningCircle className="ml-1 inline" />{run.error}</div>}

                {run.status === "credit_exhausted" && <div className="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm font-bold text-amber-950">
                    لم تُسجل المنتجات غير المكتملة كفشل. بعد شحن رصيد OpenAI اختر «200 منتج — الدفعة التالية» ثم اضغط «تشغيل الدفعة التالية»؛ سيكمل ميزان المنتجات المتبقية فقط.
                </div>}

                {COMPLETE.has(run.status) && pendingHighConfidence > 0 && <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 p-3">
                    <div className="text-sm text-emerald-900"><CheckCircle className="ml-1 inline" />يوجد <b>{pendingHighConfidence}</b> اقتراحًا ≥90% لمنتجات بلا تصنيف Google حالي.</div>
                    <button disabled={applying} onClick={applyHighConfidence} className="rounded-xl bg-emerald-700 px-4 py-2 text-sm font-black text-white disabled:opacity-50">{applying ? "جارٍ الاعتماد…" : "اعتماد عالي الثقة في ميزان"}</button>
                </div>}

                {TERMINAL.has(run.status) && pendingRetry > 0 && <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3">
                    <div className="text-sm text-amber-950"><WarningCircle className="ml-1 inline" />يوجد <b>{pendingRetry}</b> منتجًا في المراجعة أو الثقة المنخفضة. ستُعاد هذه النتائج فقط، ولن تُمس المنتجات المعتمدة.</div>
                    <button disabled={retrying || starting || applying} onClick={retryReviewQueue} className="rounded-xl bg-amber-700 px-4 py-2 text-sm font-black text-white disabled:opacity-50">{retrying ? "جارٍ بدء الإعادة…" : "إعادة تحليل قائمة المراجعة"}</button>
                </div>}

                {!!items.length && <div className="overflow-hidden rounded-2xl border">
                    <div className="max-h-[520px] overflow-auto divide-y">
                        {items.map((row) => <div key={`${row.run_id}-${row.mezan_product_id}`} className="grid gap-3 p-3 md:grid-cols-[64px_minmax(180px,1.1fr)_minmax(220px,1.4fr)_110px_minmax(180px,1fr)_90px] md:items-center">
                            <div>{row.main_image ? <img src={row.main_image} alt="" className="h-14 w-14 rounded-xl border object-cover" /> : <div className="flex h-14 w-14 items-center justify-center rounded-xl border bg-slate-50 text-xs text-slate-400">بدون صورة</div>}</div>
                            <div className="min-w-0"><div className="font-bold text-slate-900">{row.product_name || "بدون اسم"}</div><div className="mt-1 text-[11px] text-slate-400">{row.salla_product_id || row.mezan_product_id}</div></div>
                            <div className="min-w-0"><div className="font-bold text-violet-900">{row.google_category_name || "لم يُحدد"} {row.google_category_id ? <span className="num text-xs text-slate-400">ID {row.google_category_id}</span> : null}</div><div className="mt-1 text-xs text-slate-500">{row.google_category_path || "—"}</div></div>
                            <div><span className="num text-xl font-black">{Number(row.ai_confidence || 0)}</span><span className="text-xs text-slate-400">%</span></div>
                            <div><span className={`inline-block rounded-lg border px-2 py-1 text-[11px] font-bold ${statusClass(row.decision_status)}`}>{STATUS_LABELS[row.decision_status] || row.decision_status}</span><p className="mt-2 text-xs text-slate-600">{row.ai_reason || "—"}</p>{row.visual_verification_status === "failed" && <div className="mt-1 text-[11px] font-bold text-rose-700">فشل بصري آمن · {row.visual_verification_error_code || "provider_error"} · {Number(row.visual_verification_attempts || 0).toLocaleString("en-US")} محاولة</div>}{row.apply_status === "applied" && <div className="mt-1 text-[11px] font-bold text-emerald-700">تم الاعتماد داخل ميزان</div>}</div>
                            <button onClick={() => openProduct(row.mezan_product_id)} className="rounded-lg border px-2 py-2 text-xs font-bold text-slate-700"><ArrowSquareOut className="ml-1 inline" />فتح</button>
                        </div>)}
                    </div>
                </div>}
            </>}
        </div>}
    </section>;
}
