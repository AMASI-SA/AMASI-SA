import { useCallback, useEffect, useRef, useState } from "react";
import { applyPendingSallaAdd, getPendingSallaAdds } from "../../services/sallaPendingAdd";

const buttonClass = "rounded-lg border px-3 py-2 text-sm font-bold disabled:opacity-50";

function OrderPanel({ orderNumber, onApplied }) {
    const alive = useRef(false);
    const serial = useRef(0);
    const submitting = useRef(false);
    const retry = useRef(null);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [notice, setNotice] = useState("");
    const [selected, setSelected] = useState(null);
    const [employee, setEmployee] = useState("");
    const [reason, setReason] = useState("");
    const [uncertain, setUncertain] = useState(false);

    const load = useCallback(async () => {
        const request = ++serial.current;
        setLoading(true);
        try {
            const result = await getPendingSallaAdds(orderNumber);
            if (alive.current && request === serial.current) { setData(result); setError(""); }
        } catch (e) {
            if (alive.current && request === serial.current) {
                setData(null);
                if (e.status !== 403 && e.status !== 404) setError(e.message);
            }
        } finally {
            if (alive.current && request === serial.current) setLoading(false);
        }
    }, [orderNumber]);

    useEffect(() => {
        alive.current = true;
        void load();
        return () => { alive.current = false; serial.current += 1; };
    }, [load]);

    async function confirm() {
        if (submitting.current || !selected || !employee || reason.trim().length < 3) return;
        submitting.current = true;
        setBusy(true);
        setError("");
        const payload = retry.current || {
            event_id: selected.event_id, employee_id: employee, reason: reason.trim(),
            idempotency_key: `salla-add-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`,
            expected_revision: selected.expected_revision, expected_generation: selected.expected_generation,
        };
        retry.current = payload;
        try {
            const result = await applyPendingSallaAdd(orderNumber, payload);
            if (!alive.current) return;
            retry.current = null;
            setUncertain(false);
            setSelected(null);
            const message = result?.eligible_for_execution === false
                ? "تمت المراجعة والإسناد، لكن التجهيز متوقف بسبب حاجز آخر."
                : "تم إرسال المنتج إلى تمت المراجعة.";
            setNotice(`${message}${result?.change_id ? ` مرجع التغيير: ${result.change_id}` : ""}`);
            await load();
            if (alive.current) {
                try { await onApplied?.(); }
                catch { if (alive.current) setError("تمت العملية؛ تعذّر تحديث تفاصيل الطلب. حدّث الصفحة."); }
            }
        } catch (e) {
            if (!alive.current) return;
            if (e.status === 409) {
                retry.current = null;
                setSelected(null);
                setUncertain(false);
                await load();
                if (alive.current) setError("تغيّرت حالة الطلب. تم تحديث القائمة؛ راجعها قبل إعادة المحاولة.");
            } else {
                const unknown = !e.status || e.status === 408 || e.status >= 500;
                setUncertain(unknown);
                if (!unknown) retry.current = null;
                setError(unknown ? "لم نتأكد من نتيجة العملية. أعد المحاولة للتحقق بنفس الطلب." : e.message);
            }
        } finally {
            submitting.current = false;
            if (alive.current) setBusy(false);
        }
    }

    if (!loading && !error && !data?.enabled && !notice) return null;
    const changes = (data?.changes || []).filter(row => !row.change_type || row.change_type === "add_product");
    return <section className="space-y-3 rounded-2xl border border-violet-200 bg-white p-5" data-testid="pending-salla-adds" dir="rtl">
        <h2 className="font-extrabold">منتجات مضافة من سلة</h2>
        {loading && <p role="status">جاري تحميل الإضافات…</p>}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        {!selected && <button className={buttonClass} disabled={loading || busy} onClick={load}>تحديث الإضافات</button>}
        {data?.enabled && changes.map(row => <article key={row.event_id} className="space-y-2 rounded-xl border p-3">
            <p className="text-xs font-bold text-violet-700">منتج مضاف إلى الطلب</p>
            <h3 className="font-bold">{row.new_data?.product_name || row.new_data?.product_id}</h3>
            <p>الكمية: {row.new_data?.quantity}</p>
            {row.new_data?.variant_id && <p>الخيار: {row.new_data.variant_id}</p>}
            <dl className="space-y-1 text-sm" aria-label="خيارات العميل">{Object.entries(row.new_data?.options || {}).map(([label, value]) =>
                <div key={label}><dt className="inline font-bold">{label}: </dt><dd className="inline">{typeof value === "object" ? JSON.stringify(value) : String(value ?? "—")}</dd></div>
            )}</dl>
            {row.reason && <p>{row.reason}</p>}
            {row.apply_allowed === true && <button className={buttonClass} disabled={loading || busy || Boolean(selected)} onClick={() => {
                setSelected(row); setEmployee(""); setReason(""); setError(""); setNotice("");
            }}>إرسال إلى تمت المراجعة</button>}
        </article>)}
        {data?.enabled && !loading && !changes.length && <p>لا توجد إضافات معلّقة.</p>}
        {selected && <div role="dialog" aria-modal="true" aria-label="تأكيد إرسال المنتج" className="space-y-3 rounded-xl border bg-violet-50 p-4">
            <p>إرسال {selected.new_data?.product_name || selected.new_data?.product_id} إلى تمت المراجعة وإسناده للموظف المحدد؟</p>
            <label className="block">الموظف <select aria-label="الموظف" value={employee} disabled={busy || uncertain} onChange={e => setEmployee(e.target.value)}>
                <option value="">اختر الموظف</option>
                {(data?.employees || []).map(person => <option key={person.id} value={person.id}>{person.name}</option>)}
            </select></label>
            <label className="block">السبب <textarea aria-label="السبب" value={reason} disabled={busy || uncertain} onChange={e => setReason(e.target.value)} /></label>
            <button className={buttonClass} disabled={busy || !employee || reason.trim().length < 3} onClick={confirm}>{busy ? "جاري الإرسال…" : uncertain ? "إعادة المحاولة بنفس الطلب" : "تأكيد الإرسال"}</button>
            <button className={buttonClass} disabled={busy || uncertain} onClick={() => setSelected(null)}>إلغاء</button>
        </div>}
    </section>;
}

export default function PendingSallaAddPanel(props) {
    return props.orderNumber ? <OrderPanel key={props.orderNumber} {...props} /> : null;
}
