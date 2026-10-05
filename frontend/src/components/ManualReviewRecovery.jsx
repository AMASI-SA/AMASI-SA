import { useEffect, useRef, useState } from "react";
import {
    listManualReviewRecoveryCandidates,
    prepareManualReviewRecovery,
    confirmManualReviewRecovery,
} from "../services/manualReviewRecovery";

const labels = {
    name: "الاسم", full_name: "الاسم", first_name: "الاسم الأول", last_name: "اسم العائلة",
    phone: "الهاتف", mobile: "الجوال", email: "البريد", city: "المدينة", country: "الدولة",
    address: "العنوان", street: "الشارع", district: "الحي", postal_code: "الرمز البريدي",
    company: "شركة الشحن", company_code: "رمز شركة الشحن", method: "الطريقة", status: "الحالة",
    recipient: "المستلم", value: "القيمة", values: "القيم", label: "الوصف", title: "العنوان",
    quantity: "الكمية", sku: "SKU", product_id: "معرّف المنتج", variant_id: "معرّف الصنف",
    order_item_id: "معرّف عنصر الطلب", source_item_id: "معرّف عنصر سلة", items: "العناصر",
    options: "الخيارات", options_raw: "خيارات العميل", options_normalized: "الاختيارات",
    custom_fields: "تفاصيل العميل", color: "اللون", size: "المقاس", material: "الخامة",
    services: "الخدمات", components: "المكونات", eligible: "مؤهل", reason: "السبب",
    paid: "مدفوع", is_paid: "مدفوع", amount: "المبلغ", total: "الإجمالي", currency: "العملة",
    payment_method: "طريقة الدفع", customer_notes: "ملاحظات العميل", supplier_export: "التجهيز من المورد",
    product_name: "المنتج", component_name: "المكوّن", service_name: "الخدمة", accepted: "القبول",
    preparation_route: "مسار التجهيز", specifications_snapshot: "مواصفات المنتج", source: "بيانات المصدر", component: "المكونات",
};

const routeNames = { supplier_file: "ملف المورد", internal_preparation: "تجهيز داخلي", direct_assembly: "تجميع مباشر" };
function businessItems(items) {
    return (Array.isArray(items) ? items : []).map((item) => {
        const result = {};
        for (const key of ["name", "product_name", "component_name", "service_name", "sku", "quantity", "options", "specifications_snapshot", "supplier_export"]) {
            if (item[key] != null) result[key] = item[key];
        }
        if (item.preparation_route) result.preparation_route = routeNames[item.preparation_route] || item.preparation_route;
        for (const key of ["components", "services"]) if (Array.isArray(item[key])) result[key] = businessItems(item[key]);
        return result;
    });
}

// Show approval facts as readable rows, not a raw JSON payload. Unknown facts
// remain visible rather than silently disappearing from the approval preview.
function Facts({ value }) {
    if (value == null || value === "") return <span>غير متوفر</span>;
    if (typeof value === "boolean") return <span>{value ? "نعم" : "لا"}</span>;
    if (Array.isArray(value)) return value.length ? <ul className="space-y-1">{value.map((entry, i) => <li key={i}><Facts value={entry} /></li>)}</ul> : <span>لا توجد</span>;
    if (typeof value !== "object") return <span className="break-words">{String(value)}</span>;
    return <dl className="space-y-1">{Object.entries(value).filter(([, entry]) => entry != null).map(([key, entry]) => (
        <div key={key} className="flex flex-wrap gap-2"><dt className="font-semibold">{labels[key] || key}:</dt><dd><Facts value={entry} /></dd></div>
    ))}</dl>;
}

const guardMessages = {
    component_acceptance_changed: "تغيّرت شروط قبول المكونات. حدّث الطلب وراجعه مجددًا.",
    review_completion_source_changed: "تغيّرت بيانات الطلب. حدّث الطلب وراجعه مجددًا.",
    review_revision_conflict: "تغيّرت المراجعة. حدّث الطلب قبل التأكيد.",
    component_source_event_stale: "بيانات المكونات تحتاج إلى تحديث قبل الموافقة.",
    manual_review_recovery_preview_expired: "انتهت صلاحية المعاينة. حدّث الطلب من جديد.",
};

function errorMessage(error) {
    const detail = error?.response?.data?.detail;
    const code = detail?.code || error?.code;
    const message = guardMessages[code] || detail?.message || (typeof detail === "string" ? detail : null) || error?.message || "تعذرت الاستعادة للمراجعة.";
    return code ? `${message} (${code})` : message;
}

export default function ManualReviewRecovery({ onRecovered }) {
    const [candidates, setCandidates] = useState([]);
    const [session, setSession] = useState(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [success, setSuccess] = useState("");
    const lock = useRef(false);
    const submitted = useRef(false);
    const [retryPending, setRetryPending] = useState(false);
    useEffect(() => {
        let active = true;
        listManualReviewRecoveryCandidates().then((data) => {
            if (active) setCandidates(Array.isArray(data?.items) ? data.items : []);
        }).catch((e) => { if (active) setError(errorMessage(e)); });
        return () => { active = false; };
    }, []);

    const prepare = async (candidate) => {
        if (lock.current) return;
        lock.current = true; setBusy(true); setSession(null); setError(""); setSuccess("");
        submitted.current = false; setRetryPending(false);
        try {
            const next = await prepareManualReviewRecovery(candidate.order_number, candidate.old_operation_id);
            if (!next?.session_id || !next?.approval_hash || !next?.preview?.order || String(next.order_number) !== String(candidate.order_number) || next.old_operation_id !== candidate.old_operation_id) {
                throw new Error("معاينة الطلب غير مكتملة. لم يتم اعتماد الطلب.");
            }
            setSession(next);
        } catch (e) { setError(errorMessage(e)); }
        finally { lock.current = false; setBusy(false); }
    };

    const confirm = async () => {
        if (lock.current || !session) return;
        lock.current = true; setBusy(true); setError("");
        try {
            if (!submitted.current && (!session.expires_at || !Number.isFinite(Date.parse(session.expires_at)) || Date.parse(session.expires_at) <= Date.now())) {
                const expired = new Error("انتهت صلاحية المعاينة. حدّث الطلب وراجعه مجددًا.");
                expired.code = "manual_review_recovery_preview_expired";
                throw expired;
            }
            submitted.current = true;
            await confirmManualReviewRecovery(session.order_number, session);
            setCandidates((items) => items.filter((item) => String(item.order_number) !== String(session.order_number)));
            setSession(null); setRetryPending(false); setSuccess(`اكتملت مراجعة الطلب ${session.order_number}.`);
            await onRecovered?.();
        } catch (e) {
            const code = e?.response?.data?.detail?.code || e?.code;
            const status = e?.response?.status;
            const retryable = code === "review_completion_in_progress" || (!code && !e?.response) || status >= 500 || status === 429;
            if (retryable) {
                setRetryPending(true);
                setError("لم يصل تأكيد النتيجة بعد. قد تكون العملية قيد الإكمال. أعد التحقق بنفس الموافقة؛ لن تُنشأ موافقة جديدة.");
            } else { setSession(null); setRetryPending(false); setError(errorMessage(e)); }
        }
        finally { lock.current = false; setBusy(false); }
    };

    if (!candidates.length && !error && !success) return null;
    return <section dir="rtl" aria-label="استعادة الطلبات للمراجعة" className="space-y-3 rounded-2xl border border-amber-200 bg-white p-4">
        <h3 className="font-bold">طلبات تحتاج إعادة مراجعة</h3>
        <p className="text-sm">حدّث بيانات الطلب ثم راجعها قبل تأكيد موافقة جديدة.</p>
        {error && <p role="alert" className="text-rose-700">{error}</p>}
        {success && <p role="status" className="text-emerald-700">{success}</p>}
        {candidates.map((candidate) => <div key={candidate.old_operation_id} className="flex items-center justify-between gap-3">
            <span>الطلب {candidate.order_number}</span>
            <button type="button" disabled={busy || retryPending} onClick={() => prepare(candidate)} className="rounded-lg bg-amber-100 px-3 py-2 disabled:opacity-50">تحديث واستعادة للمراجعة</button>
        </div>)}
        {busy && <p role="status">جارٍ التحقق…</p>}
        {session && <div className="space-y-4 border-t pt-4" aria-label="معاينة الموافقة الجديدة">
            <h4 className="font-bold">البيانات الحالية للطلب {session.order_number}</h4>
            <p>المصدر: سلة — راجع جميع التفاصيل أدناه قبل التأكيد.</p>
            <h5 className="font-bold">المنتجات والكميات وخيارات العميل</h5>
            <Facts value={(session.preview.order.items || []).map((item) => ({ name: item.name, product_id: item.product_id, variant_id: item.variant_id, sku: item.sku, quantity: item.quantity, options_raw: item.options_raw, options_normalized: item.options_normalized, custom_fields: item.custom_fields, color: item.color, size: item.size, material: item.material }))} />
            <h5 className="font-bold">العميل وملاحظاته</h5><Facts value={session.preview.order.customer} /><Facts value={session.preview.order.customer_notes} />
            <h5 className="font-bold">الشحن والمستلم</h5><Facts value={session.preview.order.shipping} />
            <h5 className="font-bold">الدفع</h5><Facts value={session.preview.order.payment} /><Facts value={{ total: session.preview.order.totals?.total, currency: session.preview.order.totals?.currency }} />
            <h5 className="font-bold">الخدمات والمكونات</h5><Facts value={{ accepted: session.preview.components?.accepted, items: businessItems(Array.isArray(session.preview.components) ? session.preview.components : session.preview.components?.items) }} /><Facts value={businessItems(session.preview.items)} />
            <h5 className="font-bold">أهلية المراجعة</h5><Facts value={session.preview.eligibility} />
            <div className="flex gap-3">
                <button type="button" disabled={busy} onClick={confirm} className="rounded-lg bg-violet-700 px-3 py-2 text-white disabled:opacity-50">{retryPending ? "إعادة التحقق من الاستعادة" : "تأكيد الاستعادة للمراجعة"}</button>
                {!submitted.current && <button type="button" disabled={busy} onClick={() => setSession(null)}>إلغاء المعاينة</button>}
            </div>
        </div>}
    </section>;
}
