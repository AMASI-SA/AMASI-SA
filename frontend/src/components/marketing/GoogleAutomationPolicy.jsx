import { useEffect, useRef, useState } from "react";
import { googleErrorMessage, isGoogleRequestCancelled } from "../../services/googleAds";
import { verifyGoogleBudgetStorage, activateGoogleAutomationPolicy, getGoogleAutomationPolicy, revokeGoogleAutomationPolicy, saveGoogleAutomationPolicy } from "../../services/googleAutomationPolicy";

const INPUT = "mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm disabled:bg-slate-100";
const BUTTON = "rounded-lg border border-slate-300 px-3 py-2 text-sm font-bold disabled:opacity-40";
const ACTIONS = [["pause", "إيقاف الحملات"], ["enable", "إعادة تفعيل الحملات"], ["set_budget", "تعديل الميزانيات"]];

function localDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return "";
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function formFrom(policy) {
    return { campaign_ids: (policy.campaign_ids || []).join("\n"), allowed_actions: policy.allowed_actions || [],
        max_campaign_daily_budget_native: policy.max_campaign_daily_budget_native || "",
        max_account_daily_budget_native: policy.max_account_daily_budget_native || "",
        max_budget_change_percent: policy.max_budget_change_percent || "",
        cooldown_seconds: String(policy.cooldown_seconds || 18000), max_actions_per_day: String(policy.max_actions_per_day || 1),
        expires_at: localDate(policy.expires_at) };
}

function PolicyPanel({ accountId }) {
    const [policy, setPolicy] = useState(null), [form, setForm] = useState(null);
    const [busy, setBusy] = useState(true), [error, setError] = useState(""), [notice, setNotice] = useState("");
    const [conflict, setConflict] = useState(false), [reload, setReload] = useState(0);
    const [dirty, setDirty] = useState(false), [acknowledged, setAcknowledged] = useState(false);
    const mounted = useRef(true), request = useRef(null), inflight = useRef(false);
    const [storageBusy, setStorageBusy] = useState(false), [storageNotice, setStorageNotice] = useState("");
    const storageRequest = useRef(null), storageInflight = useRef(false);
    useEffect(() => {
        mounted.current = true;
        return () => { mounted.current = false; request.current?.abort(); storageRequest.current?.abort(); };
    }, []);
    useEffect(() => {
        const controller = new AbortController();
        request.current = controller;
        let active = true;
        setBusy(true); setError(""); setNotice("");
        getGoogleAutomationPolicy(accountId, { signal: controller.signal }).then((result) => {
            if (active) { setPolicy(result); setForm(formFrom(result)); setConflict(false); setDirty(false); setAcknowledged(false); }
        }).catch((failure) => {
            if (active && !isGoogleRequestCancelled(failure)) setError(googleErrorMessage(failure, "تعذر تحميل حدود الإدارة الآلية."));
        }).finally(() => { if (active) setBusy(false); });
        return () => { active = false; controller.abort(); };
    }, [accountId, reload]);

    async function verifyStorage() {
        if (storageInflight.current) return;
        storageInflight.current = true; setStorageBusy(true); setStorageNotice("");
        const controller = new AbortController(); storageRequest.current = controller;
        try {
            const result = await verifyGoogleBudgetStorage({ signal: controller.signal });
            if (mounted.current) setStorageNotice(result?.storage_verified === true && result?.snapshot_majority_write_aborted === true
                && result?.scope === "budget_storage_transaction_only" && result?.execution_authorized === false && result?.funding_ready === false
                ? "تم التحقق من معاملة التخزين وإلغائها. هذا تحقق للتخزين فقط؛ لم تتغير السياسة ولا يمنح صلاحية تشغيل أو جاهزية إنفاق."
                : "لم تُثبت جاهزية التخزين. لم تتغير السياسة ولم يُمنح أي تفويض تشغيل.");
        } catch (failure) {
            if (mounted.current && !isGoogleRequestCancelled(failure)) setStorageNotice("تعذر إثبات جاهزية التخزين. لم تتغير السياسة ولم يُمنح أي تفويض تشغيل؛ يلزم تحقق تقني.");
        } finally {
            storageInflight.current = false;
            if (mounted.current) setStorageBusy(false);
        }
    }

    function edit(key, value) { setForm((previous) => ({ ...previous, [key]: value })); setNotice(""); setDirty(true); setAcknowledged(false); }
    async function mutate(kind) {
        if (busy || inflight.current || !policy || conflict) return;
        if (kind === "activate" && (dirty || !acknowledged || policy.executor_available !== true || policy.effective_enabled === true)) return;
        inflight.current = true; setBusy(true); setError(""); setNotice("");
        const controller = new AbortController(); request.current = controller;
        try {
            let result;
            if (kind === "activate") {
                result = await activateGoogleAutomationPolicy(accountId, policy.revision, acknowledged, { signal: controller.signal });
            } else if (kind === "revoke") {
                result = await revokeGoogleAutomationPolicy(accountId, policy.revision, { signal: controller.signal });
            } else {
                const expires = form.expires_at ? new Date(form.expires_at) : null;
                if (expires && !Number.isFinite(expires.getTime())) throw new Error("اختر تاريخ انتهاء صالحًا.");
                result = await saveGoogleAutomationPolicy(accountId, {
                    expected_revision: policy.revision, enabled: false,
                    campaign_ids: form.campaign_ids.split(/[\s,،]+/).filter(Boolean), allowed_actions: form.allowed_actions,
                    max_campaign_daily_budget_native: form.max_campaign_daily_budget_native.trim(),
                    max_account_daily_budget_native: form.max_account_daily_budget_native.trim(),
                    max_budget_change_percent: form.max_budget_change_percent.trim(),
                    cooldown_seconds: Number(form.cooldown_seconds), max_actions_per_day: Number(form.max_actions_per_day),
                    expires_at: expires ? expires.toISOString() : null,
                }, { signal: controller.signal });
            }
            if (mounted.current) {
                setPolicy(result); setForm(formFrom(result)); setDirty(false); setAcknowledged(false);
                setNotice(kind === "activate" ? "اعتُمد التفويض لهذا الإصدار. تتقيد أي إجراءات لاحقة بالحملات والحدود المعروضة." : kind === "revoke" ? "أُلغي التفويض المحفوظ. لم يبدأ هذا الطلب أي تغيير في حملات جوجل." : "حُفظت الحدود والسياسة معطّلة. لم تتغير حملات جوجل.");
            }
        } catch (failure) {
            if (mounted.current && !isGoogleRequestCancelled(failure)) {
                if (failure?.response?.status === 409) {
                    setConflict(true); setError("تغيرت السياسة المحفوظة. أعد تحميلها قبل أي حفظ أو إلغاء؛ بقيت مدخلاتك ظاهرة للمراجعة.");
                } else setError(googleErrorMessage(failure, "تعذر حفظ السياسة. بقيت مدخلاتك كما هي؛ حدّث السياسة للتحقق من نتيجتها قبل المحاولة مجددًا."));
            }
        } finally {
            inflight.current = false;
            if (mounted.current) setBusy(false);
        }
    }

    return <details className="rounded-2xl border border-slate-200 bg-white p-4" aria-label="حدود الإدارة الآلية لجوجل">
        <summary className="cursor-pointer font-black">حدود الإدارة الآلية لجوجل</summary>
        <p className="mt-3 text-sm leading-7 text-slate-600">{policy?.executor_available === true ? "احفظ الحدود أولًا، ثم راجع التفويض واعتمده صراحةً. حفظ الإعدادات وحده يُبقي الإدارة الآلية معطّلة." : "تجهيز الحدود فقط. التنفيذ الآلي غير متاح حاليًا؛ حفظ هذه الإعدادات لا يشغّل الحملات ولا يغيّر ميزانياتها."}</p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
            <label className="text-sm"><input type="checkbox" checked={policy?.effective_enabled === true} disabled readOnly /> {policy?.effective_enabled ? "الإدارة الآلية مفعّلة ضمن الحدود المعتمدة" : policy?.activation_required && policy?.executor_available ? "يلزم اعتماد جديد من المالك" : policy?.enabled ? "تفويض محفوظ؛ التنفيذ الآلي غير متاح" : "التشغيل الآلي معطّل"}</label>
            <button type="button" className={BUTTON} disabled={busy} onClick={() => setReload((value) => value + 1)}>إعادة تحميل السياسة</button>
        </div>
        <section className="mt-3 rounded-lg border border-slate-200 p-3" aria-label="تشخيص تخزين حدود جوجل">
            <p className="text-sm">تحقق من تخزين الحدود فقط بمعاملة مؤقتة تُلغى قبل الحفظ. لا يختبر اتصال جوجل ولا يفعّل الحملات أو التفويض.</p>
            <button type="button" className={`${BUTTON} mt-2`} disabled={storageBusy} onClick={verifyStorage}>التحقق من تخزين حدود جوجل</button>
            {storageBusy && <p role="status">جارٍ التحقق من التخزين…</p>}
            {storageNotice && <p role="status">{storageNotice}</p>}
        </section>
        {busy && <p role="status" className="mt-3 text-sm">جارٍ قراءة أو حفظ سياسة جوجل…</p>}
        {error && <p role="alert" className="mt-3 text-sm text-red-800">{error}</p>}
        {notice && <p role="status" className="mt-3 text-sm text-emerald-800">{notice}</p>}
        {policy?.in_flight_authorized && <p role="status" className="mt-3 text-sm text-amber-900">قد تكتمل عملية مُنحت إذن الإرسال قبل الإلغاء؛ إلغاء التفويض يمنع التفويضات الجديدة.</p>}
        {policy && <p className="mt-2 text-xs text-slate-500">النسخة المحفوظة: {policy.revision}{policy.currency_code ? ` · ${policy.currency_code} · ${policy.time_zone}` : " · تُثبت عملة الحساب وتوقيته من جوجل عند الحفظ"}{policy.expired && " · انتهى التفويض المحفوظ"}</p>}
        {form && <form className="mt-4 space-y-4" onSubmit={(event) => { event.preventDefault(); mutate("save"); }}>
            <fieldset disabled={busy || conflict} className="space-y-4">
                <label className="block text-sm font-bold">معرفات الحملات المسموح بها<textarea aria-label="معرفات الحملات المسموح بها" className={INPUT} rows={3} value={form.campaign_ids} onChange={(event) => edit("campaign_ids", event.target.value)} placeholder="معرف واحد في كل سطر" /></label>
                <div className="flex flex-wrap gap-4">{ACTIONS.map(([value, label]) => <label key={value} className="text-sm"><input type="checkbox" checked={form.allowed_actions.includes(value)} onChange={(event) => edit("allowed_actions", event.target.checked ? [...form.allowed_actions, value] : form.allowed_actions.filter((item) => item !== value))} /> {label}</label>)}</div>
                <p className="text-xs leading-6 text-slate-500">اختيار الإجراء يحدد نطاق السياسة المستقبلية فقط. الميزانية اليومية متوسط إعداد لدى جوجل، وليست سقفًا مضمونًا للصرف الفعلي.</p>
                <div className="grid gap-3 sm:grid-cols-2">
                    <label className="text-sm font-bold">حد متوسط ميزانية الحملة · عملة الحساب<input required inputMode="decimal" className={INPUT} aria-label="حد متوسط ميزانية الحملة" value={form.max_campaign_daily_budget_native} onChange={(event) => edit("max_campaign_daily_budget_native", event.target.value)} /></label>
                    <label className="text-sm font-bold">حد مجموع متوسط الميزانيات · عملة الحساب<input required inputMode="decimal" className={INPUT} aria-label="حد مجموع متوسط الميزانيات" value={form.max_account_daily_budget_native} onChange={(event) => edit("max_account_daily_budget_native", event.target.value)} /></label>
                    <label className="text-sm font-bold">أقصى تغيير في الميزانية %<input required inputMode="decimal" className={INPUT} aria-label="أقصى تغيير في الميزانية" value={form.max_budget_change_percent} onChange={(event) => edit("max_budget_change_percent", event.target.value)} /></label>
                    <label className="text-sm font-bold">مهلة بين إجراءات الحملة · ثانية<input required type="number" min="3600" max="604800" className={INPUT} aria-label="مهلة بين إجراءات الحملة" value={form.cooldown_seconds} onChange={(event) => edit("cooldown_seconds", event.target.value)} /></label>
                    <label className="text-sm font-bold">أقصى عدد إجراءات في يوم الحساب<input required type="number" min="1" max="20" className={INPUT} aria-label="أقصى عدد إجراءات في يوم الحساب" value={form.max_actions_per_day} onChange={(event) => edit("max_actions_per_day", event.target.value)} /></label>
                    <label className="text-sm font-bold">انتهاء السياسة · توقيت جهازك<input type="datetime-local" className={INPUT} aria-label="انتهاء السياسة" value={form.expires_at} onChange={(event) => edit("expires_at", event.target.value)} /></label>
                </div>
                <p className="text-xs text-slate-500">الانتهاء خلال الثلاثين يومًا القادمة. حفظ الإعدادات يُبقي السياسة معطّلة.</p>
                <button type="submit" className={BUTTON}>حفظ السياسة وإبقاؤها معطّلة</button>
            </fieldset>
        </form>}
        {policy?.revision > 0 && <section aria-label="مراجعة تفويض الإدارة الآلية" className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm leading-7">
            <h3 className="font-bold">اعتماد الحدود المحفوظة</h3>
            <p>حساب جوجل: {accountId} · النسخة: {policy.revision} · {policy.currency_code || "—"} · {policy.time_zone || "—"}</p>
            <p>الحملات: {(policy.campaign_ids || []).join("، ") || "لم تُحدد"}</p>
            <p>الإجراءات: {(policy.allowed_actions || []).map((value) => ACTIONS.find(([action]) => action === value)?.[1] || "إجراء غير مدعوم").join("، ") || "لم تُحدد"}</p>
            <p>حد الحملة: {policy.max_campaign_daily_budget_native || "—"} · حد مجموع متوسط الميزانيات: {policy.max_account_daily_budget_native || "—"} {policy.currency_code || ""}</p>
            <p>أقصى تغيير: {policy.max_budget_change_percent || "—"}% · مهلة الحملة: {policy.cooldown_seconds || "—"} ثانية · أقصى إجراءات يوم الحساب: {policy.max_actions_per_day || "—"}</p>
            <p>الانتهاء: {policy.expires_at && Number.isFinite(new Date(policy.expires_at).getTime()) ? new Date(policy.expires_at).toLocaleString("ar-SA") : "لم يُحدد"}</p>
            <p>الحدود تخص متوسط الميزانية لدى جوجل؛ ليست ضمانًا لسقف الصرف الفعلي.</p>
            {dirty && <p className="text-amber-900">لديك تعديلات غير محفوظة. احفظها ثم راجع الحدود الجديدة قبل الاعتماد.</p>}
            {policy.executor_available !== true && <p className="text-amber-900">التنفيذ الآلي غير متاح حاليًا؛ التفعيل غير متاح حتى اكتمال جاهزيته.</p>}
            <label className="mt-2 block"><input type="checkbox" aria-label="اعتماد إجراءات جوجل التلقائية ضمن الحدود" checked={acknowledged} disabled={busy || conflict || dirty || policy.executor_available !== true || policy.effective_enabled === true} onChange={(event) => setAcknowledged(event.target.checked)} /> أوافق على أن ينفذ ميزان الإجراءات المحددة تلقائيًا ضمن هذه الحملات والحدود دون موافقة منفصلة لكل إجراء.</label>
            <button type="button" className={`${BUTTON} mt-3`} disabled={busy || conflict || dirty || !acknowledged || policy.executor_available !== true || policy.effective_enabled === true} onClick={() => mutate("activate")}>تفعيل الإدارة الآلية ضمن هذه الحدود</button>
        </section>}
        {policy?.revision > 0 && <button type="button" className={`${BUTTON} mt-4 text-red-800`} disabled={busy || conflict} onClick={() => mutate("revoke")}>إلغاء التفويض المحفوظ</button>}
    </details>;
}

export default function GoogleAutomationPolicy({ accountId, isOwner }) {
    if (isOwner !== true || !/^[0-9]{1,30}$/.test(String(accountId || ""))) return null;
    return <PolicyPanel key={String(accountId)} accountId={String(accountId)} />;
}
