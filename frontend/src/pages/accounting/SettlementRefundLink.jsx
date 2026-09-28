import { useState } from "react";
import api from "../../lib/api";

export default function SettlementRefundLink({ draft, entries, canEdit, onLinked }) {
    const [values, setValues] = useState({});
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const refunds = entries.filter(row => Number(row.actual_refund_amount || 0) + Number(row.actual_partial_refund_amount || 0) > 0);
    if (!refunds.length) return null;
    async function link(row) {
        setBusy(true); setError("");
        try {
            await api.put(`/financial-provider-apps/accounting-module/settlements/drafts/${encodeURIComponent(draft.id)}/refund-match`,
                { entry_id: row.id, refund_ids: values[row.id].split(/[,،]/).map(value => value.trim()).filter(Boolean) });
            await onLinked();
        } catch (e) {
            setError(e?.response?.data?.detail?.message || e?.response?.data?.detail || "تعذر ربط الاسترداد");
        } finally { setBusy(false); }
    }
    return <section aria-label="مطابقة الاستردادات" className="space-y-3 rounded border p-3">
        <h4 className="font-bold">مطابقة الاستردادات مع العمليات الأصلية</h4>
        <p>المزود هو مزود الدفع الأصلي. سطر الكشف دليل للاسترداد ولا ينشئ قيدًا ثانيًا. كل استرداد جزئي له هويته المستقلة.</p>
        {refunds.map(row => <div key={row.id} className="rounded border p-2">
            <p>طلب {row.order_number} — استرداد {(Number(row.actual_refund_amount || 0) + Number(row.actual_partial_refund_amount || 0)).toFixed(2)} SAR</p>
            {row.refund_links?.length ? <p>مرتبط بالاستردادات {row.refund_links.map(link => link.refund_id + " — " + link.txn_group_id).join("، ")}</p>
                : <><p>بانتظار تسجيل الاسترداد واعتماد حركته من الحركات المالية اليومية. حفظ الكشف ومطابقته لا ينشئان قيدًا.</p>
                    {canEdit && <><label>معرفات الحركات اليومية المعتمدة أو الاستردادات الأصلية المثبتة سابقًا (افصل بفاصلة عند التجميع)<input aria-label={`معرف استرداد ${row.id}`} value={values[row.id] || ""}
                        onChange={e => setValues(old => ({ ...old, [row.id]: e.target.value }))} className="block w-full rounded border p-2" /></label>
                        <button disabled={busy || !values[row.id]} onClick={() => link(row)} className="rounded border p-2">ربط الاسترداد المثبت دون قيد جديد</button></>}
                </>}
        </div>)}
        {error && <p role="alert">{String(error)}</p>}
    </section>;
}
