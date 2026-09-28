import { useEffect, useState } from "react";
import api from "../../lib/api";

const base = "/financial-provider-apps/accounting-module/write-control";

export default function AccountingWriteControl() {
    const [state, setState] = useState(null);
    const [reason, setReason] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [result, setResult] = useState(null);
    const load = async () => setState((await api.get(base)).data);
    useEffect(() => {
        let active = true;
        const refresh = () => api.get(base).then(({ data }) => {
            if (active) setState(data);
        }).catch(() => { if (active) setError("تعذر التحقق من حالة الكتابة؛ أعد التحديث قبل أي تغيير"); });
        refresh();
        const timer = setInterval(refresh, 15000);
        return () => { active = false; clearInterval(timer); };
    }, []);
    async function change() {
        setBusy(true); setError(""); setResult(null);
        try {
            await api.put(base, { paused: !state.paused, revision: state.revision, reason: reason.trim() });
            setReason(""); await load();
        } catch (_) {
            setError("لم يتم تأكيد التغيير. حدّث الحالة قبل إعادة المحاولة؛ قد تكون معاملة جارية أو تغيّر القرار من جلسة أخرى.");
        } finally { setBusy(false); }
    }
    async function replay() {
        setBusy(true); setError("");
        try { setResult((await api.post(base + "/replay")).data); await load(); }
        catch (_) { setError("تعذرت إعادة المعالجة؛ الأحداث محفوظة ويمكن مراجعتها والمحاولة بعد التحقق من الحالة"); }
        finally { setBusy(false); }
    }
    return <section aria-label="التحكم في كتابات ميزان 2" className="rounded-xl border bg-white p-4 space-y-3">
        <h2 className="font-bold">كتابات ميزان 2: {state ? (state.paused ? "متوقفة" : "متاحة") : "جارٍ التحقق"}</h2>
        {state?.paused && <p role="status">القراءة متاحة. الحفظ والاعتماد متوقفان لجميع المستخدمين والمهام. إشعارات التكامل محفوظة للمعالجة لاحقًا.</p>}
        <p>لا يغيّر الإيقاف سياسة الضريبة أو تاريخ القطع، ولا يشغّل الجسر القديم.</p>
        {state && <p>أحداث بانتظار المعالجة أو المراجعة: {state.pending_events || 0}</p>}
        {state?.can_manage && <>
            <label>سبب تغيير حالة الكتابة<input aria-label="سبب تغيير حالة الكتابة" value={reason} maxLength={500}
                onChange={e => setReason(e.target.value)} className="block rounded border p-2" /></label>
            <button disabled={busy || !reason.trim()} onClick={change} className="rounded border px-3 py-2">
                {busy ? "جارٍ تأكيد العملية…" : state.paused ? "استئناف الكتابات" : "إيقاف الكتابات وانتظار المعاملات الجارية"}
            </button>
            <button disabled={busy} onClick={() => load().catch(() => setError("تعذر تحديث الحالة"))} className="rounded border px-3 py-2">تحديث الحالة</button>
            <p>الاستئناف يسمح بالكتابات الجديدة. معالجة الأحداث المحفوظة إجراء منفصل وقد تثبت ذمم مبيعات مؤهلة؛ الاستردادات تبقى مسودات.</p>
            <button disabled={busy || state.paused || !state.pending_events} onClick={replay} className="rounded border px-3 py-2">معالجة حتى 50 حدثًا محفوظًا</button>
        </>}
        {result && <p role="status">تمت معالجة {result.processed} حدثًا؛ بقي {result.pending_events} للمعالجة أو المراجعة.</p>}
        {error && <p role="alert">{error}</p>}
    </section>;
}
