import { useEffect, useState } from "react";
import api from "../../lib/api";

const base = "/financial-provider-apps/accounting-module/periods";
export default function AccountingPeriods() {
    const [state, setState] = useState(null);
    const [month, setMonth] = useState("");
    const [reason, setReason] = useState("");
    const [evidence, setEvidence] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const load = async () => setState((await api.get(base)).data);
    useEffect(() => { load().catch(() => setError("تعذر تحميل الفترات المحاسبية")); }, []);
    const selected = state?.items.find(row => row.month === month);
    async function change() {
        setBusy(true); setError("");
        try {
            await api.put(base, { month, closed: !selected?.closed, revision: selected?.revision || 0,
                reason: reason.trim(), evidence_ref: evidence.trim() });
            setReason(""); setEvidence(""); await load();
        } catch (_) { setError("لم يُؤكد تغيير الفترة. حدّث الحالة وراجع الصلاحية أو تعارض النسخة."); }
        finally { setBusy(false); }
    }
    return <section aria-label="الفترات المحاسبية" className="rounded-xl border bg-white p-4 space-y-3">
        <h2 className="font-bold">الفترات المحاسبية — Asia/Riyadh</h2>
        <p>الإقفال يمنع ترحيل قيود ميزان 2 داخل الشهر بحسب التاريخ المحاسبي. تبقى القراءة والمسودات متاحة؛ لا يتغير تاريخ القيد تلقائيًا.</p>
        {state?.items.map(row => <p key={row.month}>{row.month}: {row.closed ? "مقفلة" : "مفتوحة"} — {row.reason}</p>)}
        {state?.can_manage && <>
            <label>الشهر المحاسبي<input type="month" aria-label="الشهر المحاسبي" value={month} onChange={e => setMonth(e.target.value)} className="block border p-2" /></label>
            <label>سبب قرار الفترة<input aria-label="سبب قرار الفترة" value={reason} maxLength={500} onChange={e => setReason(e.target.value)} className="block border p-2" /></label>
            <label>مرجع اعتماد الفترة<input aria-label="مرجع اعتماد الفترة" value={evidence} maxLength={200} onChange={e => setEvidence(e.target.value)} className="block border p-2" /></label>
            <button disabled={busy || !month || !reason.trim() || !evidence.trim()} onClick={change} className="rounded border px-3 py-2">{selected?.closed ? "إعادة فتح الفترة باعتماد المالك" : "إقفال الفترة وانتظار المعاملات الجارية"}</button>
            <p>إعادة الفتح قرار مستقل للمالك بمرجع وسبب محفوظين، وليست جزءًا من اعتماد قيد مرفوض.</p>
        </>}
        <button disabled={busy} onClick={() => load().catch(() => setError("تعذر تحديث الفترات"))} className="rounded border px-3 py-2">تحديث الفترات</button>
        {error && <p role="alert">{error}</p>}
    </section>;
}
