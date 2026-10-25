import { useEffect, useRef, useState } from "react";
import {
    autonomyError, getSnapchatAutonomyExecutions, listSnapchatAutonomy,
    microToNativeAmount, nativeAmountToMicro, revokeSnapchatAutonomy, saveSnapchatAutonomy,
} from "../../services/snapchatAutonomy";

const ACTIONS = { pause: "إيقاف", reduce: "خفض الميزانية", scale: "توسيع الميزانية" };
const MODES = { disabled: "موقوف", shadow: "محاكاة", enabled: "تفويض تلقائي" };
const STATUSES = {
    completed: "نُفذ وتحقق", failed: "توقف التنفيذ", executing: "جارٍ التنفيذ",
    provider_state_uncertain: "يحتاج مصالحة مع سناب", verification_required: "يحتاج تحققًا",
};
const BLOCKERS = {
    final_write_fence_not_installed: "التنفيذ التلقائي غير متاح حاليًا",
    selected_account_unavailable: "الحساب غير متصل أو غير محدد في ميزان",
    account_binding_changed: "تغيرت عملة الحساب أو توقيته؛ احفظ تفويضًا جديدًا",
    policy_expired: "انتهت مدة التفويض",
    current_owner_grant_required: "يلزم اعتماد جديد من المالك",
};

function datetimeInput(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function initialForm(policy) {
    return {
        mode: policy.configured_mode || "disabled", actions: policy.allowed_actions || [],
        budget: microToNativeAmount(policy.max_entity_daily_budget_micro),
        increase: microToNativeAmount(policy.max_daily_budget_increase_micro),
        percent: String(policy.max_change_percent ?? ""),
        interval: String(policy.min_interval_minutes ?? ""),
        maxActions: String(policy.max_actions_per_cycle ?? ""),
        expires: datetimeInput(policy.expires_at), accepted: false,
    };
}

function PolicyCard({ initialPolicy, executorReady, onSaved }) {
    const [policy, setPolicy] = useState(initialPolicy);
    const [form, setForm] = useState(() => initialForm(initialPolicy));
    const [busy, setBusy] = useState(false);
    const busyRef = useRef(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [history, setHistory] = useState(null);
    const field = (name, value) => setForm((current) => ({ ...current, [name]: value }));

    useEffect(() => {
        setPolicy(initialPolicy); setForm(initialForm(initialPolicy));
    }, [initialPolicy]);

    const apply = (saved) => {
        setPolicy(saved); setForm(initialForm(saved));
        onSaved(saved);
    };

    const save = async (event) => {
        event.preventDefault();
        if (busyRef.current) return;
        busyRef.current = true; setBusy(true); setError(""); setNotice("");
        try {
            const active = form.mode !== "disabled";
            const integer = (value) => value === "" ? null : Number(value);
            const saved = await saveSnapchatAutonomy(policy.account_id, {
                expected_revision: policy.revision,
                mode: form.mode, allowed_actions: form.actions,
                max_entity_daily_budget_micro: form.budget === "" ? null : nativeAmountToMicro(form.budget),
                max_daily_budget_increase_micro: form.increase === "" ? null : nativeAmountToMicro(form.increase, { allowZero: true }),
                max_change_percent: integer(form.percent), min_interval_minutes: integer(form.interval),
                max_actions_per_cycle: integer(form.maxActions),
                expires_at: form.expires ? new Date(form.expires).toISOString() : null,
                acknowledgement_version: policy.acknowledgement_version,
                accept_autonomous_execution: form.mode === "enabled" && form.accepted,
            });
            apply(saved);
            setNotice(saved.execution_enabled ? "حُفظ التفويض؛ القرارات التالية تُنفذ ضمن حدوده بعد التحقق من الأدلة." : active ? "حُفظت المحاكاة." : "التفويض موقوف.");
        } catch (failure) { setError(autonomyError(failure)); }
        finally { busyRef.current = false; setBusy(false); }
    };

    const revoke = async () => {
        if (busyRef.current) return;
        busyRef.current = true; setBusy(true); setError(""); setNotice("");
        try {
            apply(await revokeSnapchatAutonomy(policy.account_id, policy.revision));
            setNotice("أوقف التفويض؛ أي عملية لم تبدأ كتابتها تحتاج اعتمادًا صالحًا قبل التنفيذ.");
        } catch (failure) { setError(autonomyError(failure)); }
        finally { busyRef.current = false; setBusy(false); }
    };

    const loadHistory = async () => {
        try { setHistory((await getSnapchatAutonomyExecutions(policy.account_id)).items || []); }
        catch (failure) { setError(autonomyError(failure)); }
    };

    return <article className="rounded-xl border border-slate-200 bg-white p-4" data-testid={`snap-autonomy-${policy.account_id}`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
            <div><h3 className="text-sm font-black text-slate-900">{policy.display_name || policy.account_id}</h3><p className="text-xs text-slate-500">{policy.currency} · {policy.timezone}</p></div>
            <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-bold" data-testid="autonomy-effective-mode">{MODES[policy.effective_mode] || "موقوف"}</span>
        </div>
        {(policy.blockers || []).length > 0 && <p className="mt-2 text-xs text-amber-800">{policy.blockers.map((code) => BLOCKERS[code] || "يلزم تحديث التفويض والتحقق من جاهزية الحساب").join(" · ")}</p>}
        <form onSubmit={save} className="mt-3 space-y-3">
            <label className="block text-xs font-bold">الوضع<select aria-label="وضع التفويض" value={form.mode} onChange={(event) => field("mode", event.target.value)} className="mt-1 block w-full rounded-lg border p-2"><option value="disabled">موقوف</option><option value="shadow">محاكاة دون تعديل الإعلانات</option><option value="enabled" disabled={!executorReady}>تفويض تلقائي ضمن الحدود</option></select></label>
            <fieldset className="flex flex-wrap gap-3 text-xs" disabled={busy}><legend className="mb-1 font-bold">الإجراءات المسموحة</legend>{Object.entries(ACTIONS).map(([key, label]) => <label key={key} className="flex items-center gap-1"><input type="checkbox" checked={form.actions.includes(key)} onChange={(event) => field("actions", event.target.checked ? [...form.actions, key] : form.actions.filter((value) => value !== key))} />{label}</label>)}</fieldset>
            <div className="grid gap-3 sm:grid-cols-2">
                <label className="text-xs font-bold">أقصى ميزانية يومية للكيان ({policy.currency})<input aria-label="أقصى ميزانية يومية للكيان" value={form.budget} onChange={(event) => field("budget", event.target.value)} inputMode="decimal" required={form.mode !== "disabled"} className="mt-1 block w-full rounded-lg border p-2" /></label>
                <label className="text-xs font-bold">سقف زيادات الميزانيات في اليوم ({policy.currency})<input aria-label="سقف زيادات الميزانيات في اليوم" value={form.increase} onChange={(event) => field("increase", event.target.value)} inputMode="decimal" required={form.mode !== "disabled"} className="mt-1 block w-full rounded-lg border p-2" /></label>
                <label className="text-xs font-bold">أقصى نسبة تغيير (%)<input aria-label="أقصى نسبة تغيير" type="number" min="5" max="30" step="1" value={form.percent} onChange={(event) => field("percent", event.target.value)} required={form.mode !== "disabled"} className="mt-1 block w-full rounded-lg border p-2" /></label>
                <label className="text-xs font-bold">الفاصل بين دورات التنفيذ (دقيقة)<input aria-label="الفاصل بين دورات التنفيذ" type="number" min="300" max="10080" step="1" value={form.interval} onChange={(event) => field("interval", event.target.value)} required={form.mode !== "disabled"} className="mt-1 block w-full rounded-lg border p-2" /></label>
                <label className="text-xs font-bold">أقصى إجراءات في الدورة<input aria-label="أقصى إجراءات في الدورة" type="number" min="1" max="18" step="1" value={form.maxActions} onChange={(event) => field("maxActions", event.target.value)} required={form.mode !== "disabled"} className="mt-1 block w-full rounded-lg border p-2" /></label>
                <label className="text-xs font-bold">انتهاء التفويض<input aria-label="انتهاء التفويض" type="datetime-local" value={form.expires} onChange={(event) => field("expires", event.target.value)} required={form.mode !== "disabled"} className="mt-1 block w-full rounded-lg border p-2" /></label>
            </div>
            <p className="text-xs leading-5 text-slate-500">تُراجع القرارات في دورة التحليل كل خمس ساعات. سقف الزيادات يحد مجموع رفع الميزانيات بواسطة التفويض في يوم الحساب؛ الصرف الفعلي يعتمد على تسليم سناب.</p>
            {form.mode === "enabled" && <label className="flex items-start gap-2 rounded-lg bg-amber-50 p-3 text-xs font-bold leading-5"><input aria-label="أوافق على التنفيذ التلقائي ضمن الحدود" type="checkbox" checked={form.accepted} onChange={(event) => field("accepted", event.target.checked)} required />أفوّض ميزان لتنفيذ الإجراءات المحددة على هذا الحساب ضمن هذه الحدود والمدة، مع التحقق وسجل التنفيذ.</label>}
            {error && <p role="alert" className="text-xs font-bold text-red-700">{error}</p>}
            {notice && <p role="status" className="text-xs font-bold text-emerald-700">{notice}</p>}
            <div className="flex flex-wrap gap-2"><button type="submit" disabled={busy || (form.mode === "enabled" && (!executorReady || !form.accepted))} className="rounded-lg bg-violet-700 px-3 py-2 text-xs font-bold text-white disabled:opacity-40">{busy ? "جارٍ الحفظ…" : "حفظ التفويض"}</button>{policy.configured_mode !== "disabled" && <button type="button" onClick={revoke} disabled={busy} className="rounded-lg border border-red-200 px-3 py-2 text-xs font-bold text-red-700">إيقاف التفويض الآن</button>}<button type="button" onClick={loadHistory} className="rounded-lg border px-3 py-2 text-xs font-bold">سجل التنفيذ</button></div>
        </form>
        {history && <div className="mt-3 space-y-2 text-xs">{history.length ? history.map((row) => <div key={row.execution_id} className="rounded-lg bg-slate-50 p-2"><p>{ACTIONS[row.action] || row.action} · {STATUSES[row.status] || "يحتاج مراجعة"}</p><p className="mt-1 text-slate-500">بتفويض المالك · {row.entity_id}</p></div>) : <p className="text-slate-500">لا توجد عمليات تلقائية مسجلة لهذا الحساب.</p>}</div>}
    </article>;
}

export default function SnapchatAutonomyPanel({ onPoliciesChange }) {
    const [data, setData] = useState(null);
    const [error, setError] = useState("");
    const onChangeRef = useRef(onPoliciesChange);
    useEffect(() => { onChangeRef.current = onPoliciesChange; }, [onPoliciesChange]);
    useEffect(() => {
        let active = true;
        listSnapchatAutonomy().then((result) => {
            if (active) { setData(result); onChangeRef.current?.(result.items || []); }
        }).catch((failure) => { if (active) setError(autonomyError(failure)); });
        return () => { active = false; };
    }, []);
    const saved = (policy) => {
        const next = { ...data, items: (data.items || []).map((row) => row.account_id === policy.account_id ? policy : row) };
        setData(next);
        onChangeRef.current?.(next.items);
    };
    return <section className="rounded-2xl border border-violet-200 bg-violet-50/30 p-4" data-testid="snapchat-autonomy-panel">
        <h2 className="text-sm font-black text-slate-900">تفويض إدارة سناب</h2><p className="mt-1 text-xs leading-5 text-slate-600">حدّد الحساب والإجراءات والميزانيات والمدة. المحاكاة تعرض القرارات المؤهلة، والتفويض المفعّل ينفذها ضمن الحدود بعد التحقق من البيانات.</p>
        {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
        {!data && !error && <p className="mt-2 text-xs text-slate-500">جارٍ قراءة التفويضات…</p>}
        {data && !data.executor_ready && <p className="mt-2 text-xs text-amber-800">التنفيذ التلقائي غير متاح حاليًا؛ المحاكاة متاحة.</p>}
        {data && <div className="mt-3 grid gap-3 lg:grid-cols-2">{(data.items || []).map((policy) => <PolicyCard key={policy.account_id} initialPolicy={policy} executorReady={data.executor_ready} onSaved={saved} />)}</div>}
    </section>;
}
