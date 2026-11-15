import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import api from "../lib/api";

const ENDPOINT = "/salla/webhook-incident-recovery";
const REQUEST_OPTIONS = { timeout: 30_000, _mezanAuthRetried: true };
const QUEUE_STATES = [
    ["pending", "بانتظار المعالجة"], ["processing", "قيد المعالجة"],
    ["retry", "بانتظار إعادة المحاولة"], ["quarantined", "موقوفة"], ["done", "مكتملة"],
];
const isOwner = (user) => user?.is_owner === true || user?.role === "owner";
const count = (value) => Number.isSafeInteger(value) && value >= 0;
const sameTime = (a, b) => Number.isFinite(Date.parse(a)) && Date.parse(a) === Date.parse(b);
const sameRuntime = (a, b) => Boolean(a?.release_id && a?.source_git_sha
    && a.release_id === b?.release_id && a.source_git_sha === b?.source_git_sha);

function riyadhInput(value) {
    const milliseconds = Date.parse(value);
    return Number.isFinite(milliseconds)
        ? new Date(milliseconds + 3 * 60 * 60 * 1000).toISOString().slice(0, 19)
        : "";
}

function formatTime(value) {
    if (!Number.isFinite(Date.parse(value))) return "—";
    return new Intl.DateTimeFormat("ar-SA-u-ca-gregory", {
        timeZone: "Asia/Riyadh", dateStyle: "medium", timeStyle: "medium",
    }).format(new Date(value));
}

function errorMessage(error, applying = false) {
    const code = error?.response?.data?.detail?.code;
    const messages = {
        owner_only: "هذه الصيانة متاحة لمالك المتجر فقط.",
        merchant_owner_mismatch: "الحساب الحالي ليس مالك المتجر المحدد لهذه المعالجة.",
        merchant_binding_ambiguous: "تعذر تحديد ربط وحيد للمتجر. يلزم مراجعة ربط الحساب قبل الاستعادة.",
        recovery_disabled: "أداة استعادة أحداث الحادث غير مفعّلة حاليًا.",
        runtime_unverified: "تعذر التحقق من نسخة التشغيل الحالية. حدّث حالة الأداة قبل المتابعة.",
        runtime_changed: "تغيّرت نسخة التشغيل. حدّث حالة الأداة وأنشئ معاينة جديدة.",
        invalid_request: "تحقق من نهاية الفترة وحجم الدفعة ثم أعد المعاينة.",
        batch_payload_limit: "حجم بيانات الدفعة أكبر من الحد المسموح. اختر عددًا أقل من الأحداث ثم أعد المعاينة.",
        invalid_plan: "الخطة غير صالحة للتطبيق. أنشئ معاينة جديدة.",
        plan_hash_mismatch: "تعذر مطابقة الخطة المعروضة. أنشئ معاينة جديدة.",
        plan_stale: "تغيّرت حالة الأحداث منذ المعاينة. أنشئ معاينة جديدة.",
        already_granted: "سبق منح هذه الأحداث محاولة استعادة. أعد المعاينة لعرض المتبقي.",
        worker_lease_busy: "عامل المعالجة مشغول حاليًا. انتظر قليلًا ثم أنشئ معاينة جديدة.",
        worker_lease_lost: "تغيّر قفل المعالجة. أنشئ معاينة جديدة قبل التطبيق.",
        recovery_conflict: "حدث تعارض مع معالجة أخرى. أعد المعاينة قبل التطبيق.",
        recovery_unavailable: "تعذر تأكيد حالة الاستعادة حاليًا. حدّث حالة الأداة وأعد المعاينة.",
    };
    return messages[code] || (applying
        ? "تعذر تأكيد نتيجة التطبيق. أعد المعاينة للتحقق من المتبقي؛ لن تُعاد محاولة التطبيق تلقائيًا."
        : "تعذر تحميل بيانات الاستعادة. حاول مجددًا من الأزرار أدناه.");
}

function readyConfig(config) {
    return config?.enabled === true && config?.runtime_validated === true
        && /^rg5-[a-f0-9]{64}$/.test(config?.runtime?.release_id || "")
        && /^[a-f0-9]{40}$/.test(config?.runtime?.source_git_sha || "")
        && Number.isFinite(Date.parse(config?.incident_start))
        && Number.isInteger(config?.max_batch) && config.max_batch > 0 && config.max_batch <= 50
        && Number.isInteger(config?.max_duration_seconds)
        && config.max_duration_seconds > 0 && config.max_duration_seconds <= 86_400;
}

function validateReview(data, config, request, ownerId) {
    const { plan, plan_sha256: hash, summary } = data || {};
    return plan && !Array.isArray(plan) && /^[a-f0-9]{64}$/.test(hash || "")
        && sameRuntime(plan.runtime, config.runtime)
        && typeof ownerId === "string" && ownerId.length > 0 && plan.owner_id === ownerId
        && config.merchant_id != null && plan.merchant_id === config.merchant_id
        && sameTime(plan.incident_start, config.incident_start)
        && sameTime(plan.incident_end, request.incident_end)
        && plan.batch_limit === request.batch_limit
        && sameTime(summary?.incident_start, plan.incident_start)
        && sameTime(summary?.incident_end, plan.incident_end)
        && summary?.state === "quarantined" && summary?.attempts === 8
        && count(summary?.eligible_total) && count(summary?.selected_count) && count(summary?.remaining)
        && Array.isArray(plan.rows) && plan.rows.length === summary.selected_count
        && plan.eligible_total === summary.eligible_total
        && plan.rows.every((row) => row?.state === summary.state && row?.attempts === summary.attempts)
        && summary.selected_count <= request.batch_limit
        && summary.eligible_total === summary.selected_count + summary.remaining;
}

export function SallaWebhookRecoveryLink() {
    const { user } = useAuth();
    return isOwner(user) ? (
        <Link to="/salla-webhook-recovery" className="rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50">
            استعادة أحداث سلة المتوقفة
        </Link>
    ) : null;
}

function RecoveryForm({ ownerId }) {
    const [config, setConfig] = useState(null);
    const [observedAt, setObservedAt] = useState("");
    const [reload, setReload] = useState(0);
    const [loading, setLoading] = useState(true);
    const [end, setEnd] = useState("");
    const [batch, setBatch] = useState("50");
    const [busy, setBusy] = useState("");
    const [review, setReview] = useState(null);
    const [result, setResult] = useState(null);
    const [error, setError] = useState("");
    const operation = useRef(false);
    const revision = useRef(0);
    const reviewedRevision = useRef(-1);
    const mounted = useRef(false);

    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; revision.current += 1; };
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        let active = true;
        setLoading(true);
        setConfig(null);
        setError("");
        api.get(`${ENDPOINT}/config`, { ...REQUEST_OPTIONS, signal: controller.signal }).then(({ data }) => {
            if (!active) return;
            setConfig(data);
            setObservedAt(new Date().toISOString());
            if (Number.isInteger(data?.max_batch) && data.max_batch > 0 && data.max_batch <= 50) {
                setBatch(String(data.max_batch));
            }
        }).catch((failure) => {
            if (active) setError(errorMessage(failure));
        }).finally(() => { if (active) setLoading(false); });
        return () => { active = false; controller.abort(); };
    }, [reload]);

    const locked = loading || Boolean(busy);
    const queue = config?.queue;
    const showQueue = queue && count(queue.total_granted) && QUEUE_STATES.every(
        ([state]) => count(queue.counts?.[state]) && count(queue.grant_counts?.[state]),
    ) && QUEUE_STATES.reduce((total, [state]) => total + queue.grant_counts[state], 0) === queue.total_granted
        && (queue.latest_completed_at === null || (typeof queue.latest_completed_at === "string"
            && Number.isFinite(Date.parse(queue.latest_completed_at))));
    const runtimeReady = readyConfig(config);
    const ready = runtimeReady && showQueue;
    const clearPlan = () => {
        revision.current += 1;
        setReview(null);
        setResult(null);
        setError("");
    };

    const fail = (failure, applying = false) => {
        setReview(null);
        setError(errorMessage(failure, applying));
        const code = failure?.response?.data?.detail?.code;
        if (code === "recovery_disabled") setConfig((value) => ({ ...value, enabled: false }));
        if (["runtime_changed", "runtime_unverified"].includes(code)) {
            setConfig((value) => ({ ...value, runtime_validated: false }));
        }
    };

    const preview = async (event) => {
        event.preventDefault();
        if (!ready || operation.current) return;
        clearPlan();
        const at = Date.parse(`${end}+03:00`);
        const start = Date.parse(config.incident_start);
        const limit = Number(batch);
        if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(end)
            || !Number.isFinite(at) || riyadhInput(new Date(at).toISOString()).slice(0, end.length) !== end
            || at <= start || at > Date.now() || at - start > config.max_duration_seconds * 1000) {
            setError("اختر نهاية صحيحة للفترة بتوقيت الرياض، بعد بداية الحادث وحتى الوقت الحالي، ضمن ٢٤ ساعة من البداية.");
            return;
        }
        if (!Number.isInteger(limit) || limit < 1 || limit > config.max_batch) {
            setError(`حجم الدفعة يجب أن يكون من ١ إلى ${config.max_batch} حدثًا.`);
            return;
        }
        const request = { incident_end: new Date(at).toISOString(), batch_limit: limit };
        const currentRevision = revision.current;
        operation.current = true;
        setBusy("preview");
        try {
            const { data } = await api.post(`${ENDPOINT}/dry-run`, request, REQUEST_OPTIONS);
            if (!mounted.current || currentRevision !== revision.current) return;
            if (!validateReview(data, config, request, ownerId)) {
                setError("تعذر مطابقة المعاينة مع الفترة ونسخة التشغيل الحالية. حدّث حالة الأداة ثم أعد المعاينة.");
                return;
            }
            reviewedRevision.current = currentRevision;
            setReview(data);
        } catch (failure) {
            if (mounted.current && currentRevision === revision.current) fail(failure);
        } finally {
            operation.current = false;
            if (mounted.current) setBusy("");
        }
    };

    const apply = async () => {
        if (!ready || operation.current || !review || reviewedRevision.current !== revision.current
            || review.summary.selected_count === 0) return;
        const approved = review;
        operation.current = true;
        setBusy("apply");
        setReview(null);
        setResult(null);
        setError("");
        try {
            // The reviewed plan is sent unchanged. Even auth errors must not replay this POST.
            const { data } = await api.post(`${ENDPOINT}/apply`, {
                plan: approved.plan, plan_sha256: approved.plan_sha256,
            }, REQUEST_OPTIONS);
            if (!mounted.current) return;
            if (data?.applied_count !== approved.summary.selected_count
                || data?.plan_sha256 !== approved.plan_sha256
                || data?.state !== "retry" || data?.processing !== "queued_for_existing_worker") {
                fail(null, true);
                return;
            }
            setResult({ count: data.applied_count, remaining: approved.summary.remaining });
        } catch (failure) {
            if (mounted.current) fail(failure, true);
        } finally {
            operation.current = false;
            if (mounted.current) setBusy("");
        }
    };

    return (
        <div className="mx-auto max-w-4xl space-y-5 p-4 sm:p-6" dir="rtl" data-testid="salla-webhook-recovery-page">
            <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                    <h1 className="text-2xl font-extrabold text-slate-900">استعادة أحداث سلة المتوقفة</h1>
                    <p className="mt-2 text-sm leading-relaxed text-slate-600">معاينة أحداث الحادث التي استنفدت محاولاتها، ثم إعادتها مرة واحدة إلى عامل المعالجة المعتاد.</p>
                </div>
                <Link to="/settings/salla" className="text-sm font-bold text-indigo-700 underline">العودة إلى إعدادات سلة</Link>
            </div>
            <button type="button" disabled={locked} onClick={() => { clearPlan(); setReload((value) => value + 1); }} className="rounded-lg border border-slate-200 bg-white px-4 py-2 text-sm font-bold disabled:opacity-50">تحديث حالة الأداة</button>
            {loading && <p role="status" className="text-sm text-slate-600">جاري التحقق من حالة الأداة…</p>}
            {!loading && config?.enabled !== true && config && <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">أداة استعادة أحداث الحادث غير مفعّلة حاليًا.</p>}
            {!loading && config?.enabled === true && !runtimeReady && <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">تعذر التحقق من جاهزية نسخة التشغيل الحالية. حدّث حالة الأداة قبل المتابعة.</p>}
            {!loading && config && !showQueue && <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">حالة الأحداث غير معروفة. حدّث حالة الأداة لقراءة البيانات قبل التطبيق.</p>}
            {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-bold text-rose-800">{error}</p>}
            {config && <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
                <p className="text-sm text-slate-700">بداية الحادث: <time dateTime={config.incident_start} className="font-bold">{formatTime(config.incident_start)}</time> — توقيت الرياض</p>
                <form onSubmit={preview} className="space-y-4" noValidate>
                    <div className="grid gap-4 sm:grid-cols-2">
                        <label className="space-y-2 text-sm font-bold text-slate-700" htmlFor="recovery-incident-end">
                            <span className="block">نهاية فترة الحادث — توقيت الرياض</span>
                            <input id="recovery-incident-end" type="datetime-local" step="1" value={end} min={riyadhInput(config.incident_start)} max={riyadhInput(new Date().toISOString())} disabled={!ready || locked} onChange={(event) => { clearPlan(); setEnd(event.target.value); }} className="w-full rounded-lg border border-slate-300 px-3 py-2 disabled:bg-slate-100" dir="ltr" />
                        </label>
                        <label className="space-y-2 text-sm font-bold text-slate-700" htmlFor="recovery-batch-limit">
                            <span className="block">الحد الأقصى لأحداث الدفعة</span>
                            <input id="recovery-batch-limit" type="number" min="1" max={config.max_batch || 50} step="1" value={batch} disabled={!ready || locked} onChange={(event) => { clearPlan(); setBatch(event.target.value); }} className="w-full rounded-lg border border-slate-300 px-3 py-2 disabled:bg-slate-100" />
                        </label>
                    </div>
                    <p className="text-xs leading-relaxed text-slate-500">المعاينة للقراءة فقط. التطبيق يعيد إدراج الأحداث المحددة للمعالجة، ويقتصر على ٥٠ حدثًا في الدفعة.</p>
                    <button type="submit" disabled={!ready || locked} className="rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-indigo-700 disabled:opacity-50">{busy === "preview" ? "جاري إعداد المعاينة…" : "معاينة خطة الاستعادة"}</button>
                </form>
                {config.runtime_validated && config.runtime && <details className="text-xs text-slate-500"><summary className="cursor-pointer">مرجع نسخة التشغيل المتحقق منها</summary><code className="mt-2 block break-all" dir="ltr">{config.runtime.release_id}</code></details>}
            </section>}
            {showQueue && <section aria-label="حالة معالجة الأحداث" className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 sm:p-5">
                <h2 className="font-extrabold text-slate-900">حالة معالجة الأحداث</h2>
                <p className="text-xs text-slate-500">آخر قراءة: {formatTime(observedAt)} — توقيت الرياض. استخدم «تحديث حالة الأداة» لقراءة النتيجة مجددًا.</p>
                <table className="w-full text-sm">
                    <thead><tr className="border-b text-slate-500"><th className="py-2 text-right">الحالة</th><th className="px-2 py-2">جميع أحداث المتجر</th><th className="px-2 py-2">الأحداث المستعادة</th></tr></thead>
                    <tbody>{QUEUE_STATES.map(([state, label]) => <tr key={state} className="border-b last:border-0"><th scope="row" className="py-2 text-right font-medium text-slate-700">{label}</th><td className="px-2 py-2 text-center font-bold">{queue.counts[state]}</td><td className="px-2 py-2 text-center font-bold">{queue.grant_counts[state]}</td></tr>)}</tbody>
                </table>
                <p className="text-xs text-slate-600">أحداث مُنحت محاولة استعادة: {queue.total_granted} · آخر اكتمال: {formatTime(queue.latest_completed_at)}</p>
            </section>}
            {review && <section aria-label="خطة الاستعادة المعروضة" className="space-y-4 rounded-xl border border-indigo-200 bg-indigo-50 p-4 sm:p-5">
                <h2 className="font-extrabold text-indigo-950">راجع الخطة قبل التطبيق</h2>
                <p className="text-sm text-indigo-900">من {formatTime(review.summary.incident_start)} إلى {formatTime(review.summary.incident_end)} — توقيت الرياض</p>
                <dl className="grid grid-cols-3 gap-3 text-center">
                    {[["الأحداث المؤهلة", review.summary.eligible_total], ["المحدد لهذه الدفعة", review.summary.selected_count], ["المتبقي لدفعات لاحقة", review.summary.remaining]].map(([label, value]) => <div key={label} className="rounded-lg bg-white p-3"><dt className="text-xs text-slate-600">{label}</dt><dd className="mt-1 text-2xl font-extrabold text-slate-900">{value}</dd></div>)}
                </dl>
                <p className="text-sm text-indigo-900">الحالة: موقوفة بعد استنفاد المحاولات · المحاولات السابقة لكل حدث: {review.summary.attempts}</p>
                {review.summary.selected_count === 0 ? <p role="status" className="font-bold text-slate-700">لا توجد أحداث مؤهلة للاستعادة ضمن هذه الفترة.</p> : <button type="button" onClick={apply} disabled={locked || !ready} className="rounded-lg bg-emerald-700 px-4 py-2.5 text-sm font-bold text-white hover:bg-emerald-800 disabled:opacity-50">تطبيق الخطة على {review.summary.selected_count} أحداث مرة واحدة</button>}
            </section>}
            {busy === "apply" && <p role="status" className="rounded-xl bg-indigo-50 p-4 text-sm font-bold text-indigo-900">جاري إعادة إدراج الأحداث المحددة للمعالجة…</p>}
            {result && <section role="status" className="space-y-2 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950"><p className="font-bold">أُعيدت {result.count} أحداث إلى قائمة المعالجة.</p><p>يتولى عامل النظام تحديثها. المتبقي بحسب المعاينة: {result.remaining}. أنشئ معاينة جديدة قبل أي دفعة تالية.</p></section>}
        </div>
    );
}

export default function SallaWebhookRecovery() {
    const { user, loading } = useAuth();
    if (loading) return <p role="status" className="p-6 text-sm">جاري التحقق من الصلاحية…</p>;
    if (!isOwner(user)) return <p role="alert" className="m-6 rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm font-bold text-amber-900" dir="rtl">هذه الصيانة متاحة لمالك المتجر فقط.</p>;
    return <RecoveryForm key={user.id} ownerId={user.id} />;
}
