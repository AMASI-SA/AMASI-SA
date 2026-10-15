import { useEffect, useState } from "react";
import api from "../../../lib/api";
import DriverPhysicalCash from "../../../components/driver/DriverPhysicalCash";
import { AccountingSkeleton, ErrorState, MoneyDisplay } from "../AccountingUI";
import { DRIVER_BASE } from "./driverAdapter";

export default function DriverCashReconciliation({ driverId }) {
    const endpoint = `${DRIVER_BASE}/driver-cash/${encodeURIComponent(driverId)}`;
    const [data, setData] = useState(null), [error, setError] = useState("");
    const [revision, setRevision] = useState(0), [source, setSource] = useState("");
    const [allocations, setAllocations] = useState({}), [reason, setReason] = useState("");
    const [pending, setPending] = useState(null), [busy, setBusy] = useState(false), [message, setMessage] = useState("");
    useEffect(() => {
        let current = true; setData(null); setError("");
        api.get(endpoint).then(({ data: value }) => {
            if (value?.schema !== "mz2.driver.physical_cash.v1" || value.scope !== "captured_delivered_cash_only"
                    || value.driver_id !== driverId || !Array.isArray(value.items) || !Array.isArray(value.handover_candidates)) throw new Error("driver_cash_matching_contract_unavailable");
            if (current) setData(value);
        }).catch(err => { if (current) setError(err?.response?.data?.detail?.code || err.message); });
        return () => { current = false; };
    }, [endpoint, driverId, revision]);
    async function save(event) {
        event.preventDefault();
        let payload = pending;
        if (!payload) {
            const selected = data.handover_candidates.find(row => JSON.stringify([row.source_type, row.source_id]) === source);
            if (!selected || selected.journal_reversed) return;
            payload = { request_id: window.crypto.randomUUID(), source_type: selected.source_type,
                source_id: selected.source_id, reason, allocations: Object.entries(allocations)
                    .filter(([, value]) => value !== "").map(([collection_id, amount]) => ({ collection_id, amount })) };
            setPending(payload);
        }
        setBusy(true); setError(""); setMessage("");
        try {
            const { data: result } = await api.post(`${endpoint}/reconciliations`, payload);
            if (result?.financial_effect !== "none" || !["recorded", "already_recorded"].includes(result.state) || !result.id) throw new Error("driver_cash_matching_result_unverified");
            setPending(null); setAllocations({}); setSource(""); setReason("");
            setMessage("حُفظ ربط المطابقة دون إنشاء قيد مالي."); setRevision(old => old + 1);
        } catch (err) {
            setError(err?.response?.data?.detail?.code || err.message || "driver_cash_matching_unavailable");
            if (err?.response?.status >= 400 && err.response.status < 500) setPending(null);
        } finally { setBusy(false); }
    }
    return <section aria-label="مطابقة النقد الفعلي للموصل" className="min-w-0 max-w-full space-y-4">
        <DriverPhysicalCash endpoint={endpoint} refreshKey={revision} />
        <h3>ربط الإقرارات بتوريد قائم</h3>
        <p>اختر التوريد الموجود ومبلغ النقد المرتبط بكل إقرار صراحةً. يحفظ هذا الإجراء دليل المطابقة فقط؛ لا ينشئ توريدًا أو قيدًا، ولا يسوي الفروقات.</p>
        {message && <p role="status">{message}</p>}
        {error && <ErrorState message={`تعذر التحقق من المطابقة: ${error}`} onRetry={() => setRevision(old => old + 1)} />}
        {!data ? !error && <AccountingSkeleton label="جاري تحميل مصادر المطابقة" /> : <form onSubmit={save}>
            <fieldset disabled={busy || Boolean(pending)} className="space-y-3">
                <label>توريد قائم <select aria-label="توريد قائم للمطابقة" className="max-w-full" required value={source} onChange={e => setSource(e.target.value)}><option value="">اختر المرجع المثبت</option>{data.handover_candidates.map(row => <option key={JSON.stringify([row.source_type, row.source_id])} disabled={row.journal_reversed} value={JSON.stringify([row.source_type, row.source_id])}>{row.source_type === "native_cash_settlement" ? "توريد MZ2 موثق" : "توريد تشغيلي — لا يثبت قيد MZ2"} · {row.amount} · {row.source_id}{row.journal_reversed ? " · معكوس" : ""}</option>)}</select></label>
                {!data.handover_candidates.length && <p>لا توجد توريدات غير مرتبطة معروضة.</p>}
                {data.items.filter(row => row.eligible_for_reconciliation).map(row => <label key={row.id} className="block break-all">طلب {row.order_number} · المتبقي المؤكد <MoneyDisplay value={row.remaining_amount} /><input aria-label={`مبلغ مطابقة الطلب ${row.order_number}`} inputMode="decimal" value={allocations[row.id] || ""} onChange={e => setAllocations(old => ({ ...old, [row.id]: e.target.value }))} placeholder="اتركه فارغًا إن لم يكن ضمن التوريد" className="max-w-full" /></label>)}
                <label>سبب الربط <input aria-label="سبب ربط النقد" required minLength={3} maxLength={1000} value={reason} onChange={e => setReason(e.target.value)} /></label>
            </fieldset>
            <button type="submit" disabled={busy || (!pending && (!source || reason.trim().length < 3 || !Object.values(allocations).some(value => value !== "")))}>{pending ? "إعادة إرسال الربط نفسه" : "حفظ ربط المطابقة"}</button>
            {pending && <p>لم تتأكد نتيجة الطلب بعد؛ إعادة الإرسال تستخدم المرجع والبيانات نفسها.</p>}
        </form>}
    </section>;
}
