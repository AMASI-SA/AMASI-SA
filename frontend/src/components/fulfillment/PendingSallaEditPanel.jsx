import { useCallback, useEffect, useRef, useState } from "react";
import { applyPendingSallaEdit, getPendingSallaEdits } from "../../services/sallaPendingEdit";

const buttonClass = "rounded-lg border px-3 py-2 text-sm font-bold disabled:opacity-50";
const pendingEdit = row => row.change_type === "edit_options" && row.application_state === "pending_application";
const actionable = row => pendingEdit(row) && row.apply_allowed === true && ["representation_only", "preparation_file", "components"].includes(row.classification);
const actionLabel = row => row.classification === "representation_only" ? "اعتماد التغيير" : "اعتماد إعادة التجهيز";
const classificationLabel = { representation_only: "تغيير شكلي — لا يحتاج إعادة تجهيز", preparation_file: "تغيير يؤثر على ملف التجهيز", components: "تغيير يؤثر على المكونات", reconciliation_required: "تسوية المكونات مطلوبة", exception_required: "مراجعة الاستثناء مطلوبة" };
const optionValue = value => typeof value === "object" ? JSON.stringify(value) : String(value ?? "—");

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
            const result = await getPendingSallaEdits(orderNumber);
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
        if (submitting.current || !selected || !actionable(selected) || !employee || reason.trim().length < 3) return;
        submitting.current = true;
        setBusy(true);
        setError("");
        const payload = retry.current || {
            event_id: selected.event_id, employee_id: employee, reason: reason.trim(),
            idempotency_key: `salla-edit-${globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`}`,
            expected_revision: selected.expected_revision, expected_generation: selected.expected_generation,
            units: selected.units,
        };
        retry.current = payload;
        try {
            const result = await applyPendingSallaEdit(orderNumber, payload);
            if (!alive.current) return;
            retry.current = null;
            setUncertain(false);
            setSelected(null);
            const message = result?.eligible_for_execution === false
                ? "تم اعتماد التغيير، لكن التجهيز متوقف بسبب حاجز آخر."
                : "تم اعتماد تغيير الخيارات.";
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
    const changes = (data?.changes || []).filter(pendingEdit);
    return <section className="space-y-3 rounded-2xl border border-violet-200 bg-white p-5" data-testid="pending-salla-edits" dir="rtl">
        <h2 className="font-extrabold">تعديلات خيارات من سلة</h2>
        {loading && <p role="status">جاري تحميل التعديلات…</p>}
        {error && <p role="alert">{error}</p>}
        {notice && <p role="status">{notice}</p>}
        {!selected && <button className={buttonClass} disabled={loading || busy} onClick={load}>تحديث التعديلات</button>}
        {data?.enabled && changes.map(row => <article key={row.event_id} className="space-y-2 rounded-xl border p-3">
            <p className="text-xs font-bold text-violet-700">تم تعديل خيارات المنتج في سلة</p>
            <h3 className="font-bold">{row.new_data?.product_name || row.new_data?.product_id}</h3>
            {(row.new_data?.image_url || row.new_data?.image) && <img src={row.new_data.image_url || row.new_data.image} alt={row.new_data.product_name || "المنتج المتأثر"} className="h-24 w-24 rounded-lg object-contain" />}
            <p>الكمية: {row.new_data?.quantity}</p>
            {row.new_data?.variant_id && <p>الخيار: {row.new_data.variant_id}</p>}
            {[ ["الخيارات السابقة", row.old_data], ["الخيارات الجديدة", row.new_data] ].map(([title, source]) => <div key={title}>
                <h4 className="font-bold">{title}</h4>
                <dl className="space-y-1 text-sm" aria-label={title}>{Object.entries(source?.options || {}).map(([label, value]) =>
                    <div key={label}><dt className="inline font-bold">{label}: </dt><dd className="inline">{optionValue(value)}</dd></div>
                )}</dl>
                {source?.custom_fields && <dl aria-label={`${title} — تفاصيل إضافية`}>{Object.entries(source.custom_fields).map(([label, value]) =>
                    <div key={label}><dt className="inline font-bold">{label}: </dt><dd className="inline">{optionValue(value)}</dd></div>
                )}</dl>}
            </div>)}
            <p>{classificationLabel[row.classification] || "تصنيف غير مؤكد — يلزم التحقق"}</p>
            <p>المرحلة الحالية: {row.stage || "غير محددة"}</p>
            <p>الموظف المتأثر: {(row.affected_employees || []).map(person => person.name || person.id).join("، ") || "غير مسند"}</p>
            <p>حالة التنبيه: {optionValue(row.notification_status)}</p>
            <p>الإجراء المطلوب: {row.classification === "representation_only" ? "اعتماد التغيير الشكلي دون إعادة تجهيز" : "أوقف تجهيز النسخة القديمة حتى معالجة التغيير"}</p>
            {row.reason && <p>{row.reason}</p>}
            {actionable(row) && <button className={buttonClass} disabled={loading || busy || Boolean(selected)} onClick={() => {
                setSelected(row); setEmployee(""); setReason(""); setError(""); setNotice("");
            }}>{actionLabel(row)}</button>}
        </article>)}
        {data?.enabled && !loading && !changes.length && <p>لا توجد تعديلات خيارات معلّقة.</p>}
        {selected && <div role="dialog" aria-modal="true" aria-label="تأكيد معالجة تعديل الخيارات" className="space-y-3 rounded-xl border bg-violet-50 p-4">
            <p>{actionLabel(selected)}: {selected.new_data?.product_name || selected.new_data?.product_id}؟ الخيارات التجارية تُقرأ من سلة فقط.</p>
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

export default function PendingSallaEditPanel(props) {
    return props.orderNumber ? <OrderPanel key={props.orderNumber} {...props} /> : null;
}
