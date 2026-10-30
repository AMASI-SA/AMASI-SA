import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../../context/AuthContext";
import { BOOTSTRAP_LABELS, bootstrapExpiry, executeGoogleNamespaceBootstrap, newBootstrapNonce, prepareGoogleNamespaceBootstrap, readGoogleBootstrapReceipt } from "../../services/googleNamespaceBootstrap";

const BUTTON = "rounded-lg border px-3 py-2 font-bold disabled:opacity-40";
const PREPARE_ACK = "أفهم أن هذه الخطوة تتحقق من المتطلبات وتحجز طلب تجهيز تقني، ولا تنشئ حملات أو تفويض إنفاق.";
const EXECUTE_ACK = "أوافق على تجهيز بُنى الحفظ الناقصة الموضحة فقط؛ لا يشمل ذلك تشغيل الحملات أو تغيير حدود الإنفاق.";
const UNKNOWN = "لم تتأكد نتيجة الطلب. قد تكون العملية مستمرة أو بقي حجز محفوظ؛ اقرأ الإيصال ولا تُعد الإرسال بالتخمين.";
const MESSAGES = {
    no_preparation: "لم تُرصد تهيئة محفوظة في هذه القراءة. هذا لا يثبت أن طلبًا سابقًا لم يصل إلى الخادم.",
    prepared: "يوجد طلب تحضير محفوظ. راجع البُنى المطلوب تجهيزها ثم أكّد التنفيذ بشكل منفصل.",
    already_compatible_empty_noop: "ثبت في هذا الطلب أن بُنى الحفظ متوافقة وفارغة. لم تُنشأ بُنى أو حجز جديد، ولا توجد خطوة تنفيذ مطلوبة.",
    verified_at_observation: "اكتمل تجهيز بُنى الحفظ المحددة وثبتت قراءتها في هذا الطلب. لم تُفعّل سياسات أو حملات، ولم يُمنح تفويض إنفاق.",
    historical_completed_receipt: "يوجد إيصال تجهيز سابق مكتمل؛ لا يثبت جاهزية التخزين الحالية أو صلاحية تشغيل الحملات.",
    reconciliation_required: "يوجد طلب غير محسوم يحتاج تحققًا تقنيًا. الحجز محفوظ؛ لا يُعاد التنفيذ أو يُحرر بالتخمين.",
    preparation_expired_retained: "انتهت مهلة التحضير وبقي الحجز محفوظًا. انتهاء المهلة لا يسمح بإنشاء طلب بديل.",
    reservation_owned_by_another_auth_context: "يوجد حجز محفوظ لا يطابق سياق الجلسة الحالي. يمكن قراءة حالته فقط.",
};
function validOwner(user, status) {
    return status === "authenticated" && typeof user?.id === "string" && user.id.trim() === user.id && user.id.length > 0 && user.id.length <= 200
        && user.role === "owner" && !user.deleted_at && user.disabled !== true && user.is_active !== false
        && ["disabled", "is_active"].every((key) => user[key] == null || typeof user[key] === "boolean");
}
function BootstrapPanel({ context, generation }) {
    const [view, setView] = useState({ phase: "idle" }), [prepareAck, setPrepareAck] = useState(false), [executeAck, setExecuteAck] = useState(false);
    const mounted = useRef(true), pending = useRef(null), uncertainty = useRef(null), denied = useRef(false);
    const fresh = useCallback(() => mounted.current && context.current.generation === generation && context.current.allowed && !denied.current, [context, generation]);
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; pending.current?.controller.abort(); }; }, []);
    const candidate = view.data?.status === "prepared" ? view.data : null;
    useEffect(() => {
        if (!candidate) return undefined;
        const timer = setTimeout(() => { if (fresh()) { setExecuteAck(false); setView({ phase: "expired" }); } }, Math.max(0, Math.min(bootstrapExpiry(candidate.expires_at) - Date.now(), 2147483647)));
        return () => clearTimeout(timer);
    }, [candidate, fresh]); // A client deadline can only remove an action; server authority stays independent.
    async function run(operation) {
        if (!fresh() || pending.current) return;
        const prepared = candidate;
        if (operation === "prepare" && (!prepareAck || view.data?.status !== "no_preparation" || uncertainty.current === "execute")) return;
        if (operation === "execute" && (!executeAck || !prepared || uncertainty.current === "execute" || bootstrapExpiry(prepared.expires_at) <= Date.now())) {
            if (prepared && bootstrapExpiry(prepared.expires_at) <= Date.now()) { setExecuteAck(false); setView({ phase: "expired" }); }
            return;
        }
        let nonce;
        if (operation === "prepare") {
            try { nonce = newBootstrapNonce(); } catch { setView({ phase: "error", message: "تعذر إنشاء هوية طلب آمنة؛ لم يُرسل طلب التحضير." }); return; }
        }
        const task = { controller: new AbortController() }; pending.current = task;
        if (operation !== "receipt") uncertainty.current = operation;
        setPrepareAck(false); setExecuteAck(false); setView({ phase: "pending" });
        try {
            const requestOptions = { signal: task.controller.signal };
            const data = await (operation === "receipt" ? readGoogleBootstrapReceipt(requestOptions)
                : operation === "prepare" ? prepareGoogleNamespaceBootstrap(nonce, requestOptions) : executeGoogleNamespaceBootstrap(prepared, requestOptions));
            if (!fresh() || pending.current !== task) return;
            if (operation === "receipt" && uncertainty.current === "execute") {
                // Do not retain a recovered token or turn an unknown execute into another POST.
                setView({ phase: "inspection", message: MESSAGES[data.status], status: data.status });
            } else {
                uncertainty.current = null;
                setView({ phase: "result", data, receivedAt: new Date().toLocaleString("ar-SA") });
            }
        } catch (error) {
            if (!fresh() || pending.current !== task) return;
            if ([401, 403].includes(error?.response?.status)) {
                denied.current = true;
                setView({ phase: "denied" });
            } else setView({ phase: "error", message: uncertainty.current ? UNKNOWN : "تعذرت قراءة حالة التجهيز بشكل موثوق. لا توجد نتيجة جديدة مثبتة." });
        } finally {
            if (pending.current === task) pending.current = null;
        }
    }
    const busy = view.phase === "pending";
    const canPrepare = view.data?.status === "no_preparation" && uncertainty.current !== "execute";
    return <section aria-label="تجهيز حفظ حدود جوجل" className="rounded-2xl border border-slate-200 bg-white p-4 text-sm leading-7">
        <h3 className="font-bold">تجهيز حفظ حدود جوجل</h3>
        <p>تجهيز بُنى حفظ ثابتة داخل ميزان، وليس إجراءً على حساب إعلاني مختار. لا يفعّل سياسات أو حملات ولا يمنح تفويض إنفاق.</p>
        {view.phase === "denied" ? <p role="alert">تعذر تأكيد صلاحية المالك لهذه الجلسة. أعد تأكيد الجلسة ثم اقرأ الإيصال؛ انقطاع الجلسة لا يثبت إلغاء عملية بدأت.</p> : <>
            <button type="button" className={BUTTON} disabled={busy} onClick={() => run("receipt")}>قراءة حالة التجهيز</button>
            {busy && <p role="status">جارٍ التحقق من طلب التخزين…</p>}
            {view.phase === "error" && <p role="alert">{view.message}</p>}
            {view.phase === "inspection" && <p role="status">{view.message} نتيجة التنفيذ السابقة غير مؤكدة في هذه الصفحة؛ القراءة فقط متاحة.</p>}
            {view.phase === "expired" && <p role="status">انتهت المهلة المعروضة؛ اقرأ الإيصال. لم يُحذف الحجز أو يُفتح طلب جديد.</p>}
            {view.data && <p role="status">{MESSAGES[view.data.status]}</p>}
            {view.receivedAt && <p className="text-xs">وقت تلقي النتيجة في المتصفح: {view.receivedAt}</p>}
            {canPrepare && <div><label className="block"><input type="checkbox" checked={prepareAck} onChange={(event) => setPrepareAck(event.target.checked)} /> {PREPARE_ACK}</label>
                <button type="button" className={BUTTON} disabled={!prepareAck || busy} onClick={() => run("prepare")}>مراجعة المتطلبات وحجز طلب التجهيز</button></div>}
            {candidate && <div><ul>{candidate.would_create.map((key) => <li key={key}>{BOOTSTRAP_LABELS[key]}</li>)}</ul>
                <p>نهاية مهلة التحضير بحسب الخادم: {candidate.expires_at}</p>
                <label className="block"><input type="checkbox" checked={executeAck} onChange={(event) => setExecuteAck(event.target.checked)} /> {EXECUTE_ACK}</label>
                <button type="button" className={BUTTON} disabled={!executeAck || busy || bootstrapExpiry(candidate.expires_at) <= Date.now()} onClick={() => run("execute")}>تنفيذ تجهيز الحفظ</button></div>}
        </>}
        <p className="text-xs">إذا انقطعت الصفحة فقد تستمر العملية. اقرأ الإيصال قبل أي خطوة؛ الإيصال السابق ليس إثباتًا لجاهزية حالية.</p>
    </section>;
}
export default function GoogleNamespaceBootstrap() {
    const { user, authStatus } = useAuth();
    const allowed = validOwner(user, authStatus);
    const fingerprint = allowed ? JSON.stringify([user.id, user.role, user.disabled, user.is_active, user.deleted_at]) : "denied";
    const context = useRef({ generation: 0 });
    if (context.current.user !== user || context.current.status !== authStatus || context.current.fingerprint !== fingerprint) {
        context.current = { generation: context.current.generation + 1, user, status: authStatus, fingerprint, allowed };
    }
    if (!allowed) return null;
    return <BootstrapPanel key={context.current.generation} context={context} generation={context.current.generation} />;
}
