import { useEffect, useMemo, useState } from "react";
import {
    CheckCircle, Clock, MagicWand, Robot, SpinnerGap, WarningCircle, XCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    cancelProductMediaAiJob,
    createProductMediaAiJob,
    getProductMediaAiState,
} from "../../services/mezanProductsV2";

const STATUS_LABELS = {
    waiting_provider: "بانتظار ربط OpenAI",
    waiting_image_engine: "بانتظار تفعيل محرك الصور",
    ready_for_execution: "جاهزة للمحرك عند ربط التنفيذ",
    proposal_created: "تم إنشاء الاقتراح",
    cancelled: "ملغاة",
    completed: "مكتملة",
    failed: "فشلت",
};

function ProviderStatus({ provider }) {
    if (!provider) return null;
    if (!provider.connected) {
        return <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-bold text-rose-800"><XCircle className="ml-1 inline" /> OpenAI غير مربوط في بيئة التشغيل.</div>;
    }
    if (!provider.ready) {
        return <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs leading-6 text-amber-900"><CheckCircle className="ml-1 inline text-emerald-600" /> OpenAI متصل ومحلل ميزان جاهز. <b>تنفيذ الصور غير مفعل بعد</b>؛ الطلبات أدناه تُحفظ بأمان بانتظار تفعيل محرك الصور.</div>;
    }
    return <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs font-bold text-emerald-900"><CheckCircle className="ml-1 inline" /> OpenAI متصل وإعدادات نموذج الصور موجودة. التنفيذ الفعلي سيبقى خلف الاعتماد البشري عند توصيل المحرك.</div>;
}

function JobRow({ job, onCancel, cancelling }) {
    const cancellable = ["waiting_provider", "waiting_image_engine", "ready_for_execution", "proposal_created"].includes(job.status);
    return (
        <article className="rounded-xl border bg-white p-3 text-xs">
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                    <div className="font-black text-slate-900">{job.operation_label || job.operation}</div>
                    <div className="mt-1 text-slate-500">{STATUS_LABELS[job.status] || job.status} · {job.aspect_ratio || "original"}</div>
                </div>
                {cancellable && <button type="button" disabled={cancelling} onClick={() => onCancel(job.id)} className="rounded-lg border border-rose-200 px-3 py-2 font-bold text-rose-700 disabled:opacity-40">إلغاء</button>}
            </div>
            {job.prompt && <p className="mt-2 rounded-lg bg-slate-50 p-2 leading-5 text-slate-600">{job.prompt}</p>}
            <div className="mt-2 text-[10px] text-slate-400">{job.created_at || ""}</div>
        </article>
    );
}

export default function ProductMediaAiProposalPanel({ productId, images = [] }) {
    const [state, setState] = useState(null);
    const [operation, setOperation] = useState("");
    const [sourceImageUrl, setSourceImageUrl] = useState("");
    const [aspectRatio, setAspectRatio] = useState("original");
    const [prompt, setPrompt] = useState("");
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [cancelling, setCancelling] = useState("");

    async function load() {
        if (!productId) return;
        setLoading(true);
        try {
            const result = await getProductMediaAiState(productId);
            setState(result);
            const firstAllowed = (result.operations || []).find((row) => row.allowed);
            setOperation((current) => current || firstAllowed?.key || "");
            setSourceImageUrl((current) => (
                (result.source_images || []).some((row) => row.url === current) ? current : ""
            ));
        } catch (error) {
            toast.error(error?.response?.data?.detail?.message || "تعذر تحميل طبقة ذكاء الصور");
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        setState(null);
        setOperation("");
        setSourceImageUrl("");
        setPrompt("");
        load();
    }, [productId]); // eslint-disable-line react-hooks/exhaustive-deps

    const operations = state?.operations || [];
    const selectedOperation = operations.find((row) => row.key === operation);
    const availableImages = useMemo(() => {
        const authoritative = Array.isArray(state?.source_images) ? state.source_images : images;
        return (authoritative || []).filter((row) => row?.url);
    }, [state?.source_images, images]);

    async function createJob() {
        if (!operation) return toast.error("اختر نوع التعديل");
        if (selectedOperation?.requires_source && !sourceImageUrl) return toast.error("اختر الصورة الأصلية");
        setSaving(true);
        try {
            const result = await createProductMediaAiJob(productId, {
                operation,
                source_image_url: sourceImageUrl || null,
                aspect_ratio: aspectRatio,
                prompt: prompt.trim(),
            });
            setState((current) => ({ ...current, provider: result.provider, jobs: [result.job, ...(current?.jobs || [])] }));
            setPrompt("");
            toast.success(result.job.status === "waiting_image_engine" ? "تم حفظ طلب AI؛ OpenAI متصل ومحرك الصور ينتظر التفعيل" : "تم حفظ طلب تعديل الصورة");
        } catch (error) {
            const detail = error?.response?.data?.detail;
            toast.error(detail?.code || detail?.message || "تعذر حفظ طلب ذكاء الصور");
        } finally {
            setSaving(false);
        }
    }

    async function cancelJob(jobId) {
        setCancelling(jobId);
        try {
            const result = await cancelProductMediaAiJob(productId, jobId);
            setState((current) => ({ ...current, jobs: (current?.jobs || []).map((row) => row.id === jobId ? result.job : row) }));
            toast.success("تم إلغاء الطلب");
        } finally {
            setCancelling("");
        }
    }

    return (
        <section className="rounded-2xl border border-indigo-200 bg-indigo-50/40 p-3 sm:p-4" data-testid="product-media-ai-proposals" dir="rtl">
            <div className="flex items-start gap-3">
                <div className="rounded-xl bg-indigo-700 p-2 text-white"><Robot size={22} weight="duotone" /></div>
                <div><h2 className="font-black">ذكاء صور المنتجات</h2><p className="mt-1 text-xs leading-5 text-slate-500">يحفظ طلب التعديل كاقتراح فقط. لا يعدل الأصل ولا ينشر إلى سلة تلقائيًا.</p></div>
            </div>
            <div className="mt-3"><ProviderStatus provider={state?.provider} /></div>
            {loading ? <div className="p-6 text-center text-slate-500"><SpinnerGap className="inline animate-spin" /> جارٍ التحقق…</div> : (
                <>
                    <div className="mt-4 grid gap-3 md:grid-cols-2">
                        <label className="text-xs font-bold text-slate-600">نوع العملية
                            <select value={operation} onChange={(event) => setOperation(event.target.value)} className="mt-1 w-full rounded-xl border bg-white p-3 text-sm">
                                <option value="">اختر العملية…</option>
                                {operations.map((row) => <option key={row.key} value={row.key} disabled={!row.allowed}>{row.label}{!row.allowed ? " — غير مسموح" : ""}</option>)}
                            </select>
                        </label>
                        <label className="text-xs font-bold text-slate-600">المقاس المقترح
                            <select value={aspectRatio} onChange={(event) => setAspectRatio(event.target.value)} className="mt-1 w-full rounded-xl border bg-white p-3 text-sm"><option value="original">نفس المقاس</option><option value="1:1">1:1 مربع</option><option value="4:5">4:5 متجر</option><option value="9:16">9:16 سناب/ستوري</option><option value="16:9">16:9 أفقي</option></select>
                        </label>
                    </div>
                    {selectedOperation?.requires_source && <label className="mt-3 block text-xs font-bold text-slate-600">الصورة الأصلية
                        <select value={sourceImageUrl} onChange={(event) => setSourceImageUrl(event.target.value)} className="mt-1 w-full rounded-xl border bg-white p-3 text-sm"><option value="">اختر صورة محفوظة…</option>{availableImages.map((row, index) => <option key={row.id || row.url} value={row.url}>صورة {index + 1}{row.is_main ? " — الرئيسية" : ""}{row.alt ? ` — ${row.alt}` : ""}</option>)}</select>
                    </label>}
                    {selectedOperation?.requires_source && !availableImages.length && <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">احفظ صورة المنتج أو مسودة الصور أولًا، ثم أنشئ طلب التعديل.</div>}
                    <label className="mt-3 block text-xs font-bold text-slate-600">تعليمات التعديل
                        <textarea rows={3} maxLength={1200} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="مثال: حافظ على لون المريول وتفاصيل القماش، أزل الخلفية وضع خلفية استوديو بيضاء…" className="mt-1 w-full rounded-xl border bg-white p-3 text-sm" />
                    </label>
                    <div className="mt-3 flex justify-end"><button type="button" onClick={createJob} disabled={saving || !selectedOperation?.allowed || (selectedOperation?.requires_source && !availableImages.length)} className="rounded-xl bg-indigo-700 px-5 py-3 font-black text-white disabled:opacity-40">{saving ? <SpinnerGap className="inline animate-spin" /> : <MagicWand className="inline" />} حفظ طلب AI</button></div>
                </>
            )}
            <div className="mt-5 border-t border-indigo-100 pt-4">
                <h3 className="text-sm font-black"><Clock className="ml-1 inline" /> الطلبات الأخيرة</h3>
                <div className="mt-3 space-y-2">{!(state?.jobs || []).length ? <div className="rounded-xl border border-dashed bg-white p-4 text-center text-xs text-slate-400">لا توجد طلبات بعد.</div> : (state.jobs || []).map((job) => <JobRow key={job.id} job={job} cancelling={cancelling === job.id} onCancel={cancelJob} />)}</div>
            </div>
            <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-[11px] leading-5 text-amber-900"><WarningCircle className="ml-1 inline" /> حتى بعد تفعيل محرك الصور، النتيجة ستضاف إلى مسودة الصور للمقارنة والاعتماد، ولن تُنشر مباشرة.</div>
        </section>
    );
}
