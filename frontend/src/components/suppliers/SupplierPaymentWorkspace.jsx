import { useEffect, useRef, useState } from "react";
import { allocateMezanSupplierPayment, recordMezanSupplierPayment } from "../../services/mezanSuppliersV2";

export function supplierMoney(value) {
    if (value == null || !Number.isFinite(Number(value))) return "غير متاح";
    return new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Number(value) / 100);
}

const STATES = { paid: "مسددة", partial: "مسددة جزئيًا", partially_paid: "مسددة جزئيًا", unpaid: "غير مسددة" };
const FIELD = "mt-1 min-h-11 w-full rounded-xl border border-slate-300 bg-white px-3 py-2";
const BUTTON = "min-h-11 rounded-xl bg-emerald-800 px-4 py-2 font-bold text-white disabled:opacity-40";

export function SupplierPaymentForm({ supplierId, invoices = [], workspace = {}, initialInvoiceId = "", mode = "payment", onSaved, onClose }) {
    const allocation = mode === "allocation";
    const [form, setForm] = useState({ amount: "", payment_date: new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date()), financial_account_id: "", reference: "", notes: "", evidence_file_id: "", invoice_id: initialInvoiceId, payment_id: "", unallocated_kind: "payable", allow_advance: false });
    const intent = useRef(null);
    const [busy, setBusy] = useState(false);
    const [attempted, setAttempted] = useState(false);
    const [error, setError] = useState("");
    const [recorded, setRecorded] = useState(false);
    const eligible = invoices.filter((row) => row.financial_eligible === true && Number(row.outstanding_halalas) > 0);
    const payments = (workspace.payments || []).filter((row) => row.supplier_id === supplierId && (row.unallocated_payable_halalas > 0 || row.advance_available_halalas > 0));
    const available = workspace.financial_status === "available" && (allocation || (workspace.payment_available === true && (workspace.payment_accounts || []).length > 0));
    function change(key, value) { setForm((current) => ({ ...current, [key]: value })); }
    async function submit(event) {
        event.preventDefault();
        if (busy || recorded || !available) return;
        if (!intent.current) {
            if (!/^\d+(\.\d{1,2})?$/.test(form.amount) || Number(form.amount) <= 0) { setError("أدخل مبلغًا موجبًا بمنزلتين عشريتين كحد أقصى."); return; }
            if (!form.payment_date || (allocation && (!form.payment_id || !form.invoice_id)) || (!allocation && !form.financial_account_id)) { setError("أكمل التاريخ والحساب أو الدفعة والفاتورة المطلوبة."); return; }
            if (!form.reference.trim()) { setError("مرجع السداد مطلوب."); return; }
            const common = { operation_id: globalThis.crypto.randomUUID(), amount: form.amount, payment_date: form.payment_date, reference: form.reference.trim(), notes: form.notes.trim(), evidence_file_id: form.evidence_file_id || null, invoice_id: form.invoice_id || null };
            intent.current = allocation ? { ...common, payment_id: form.payment_id } : { ...common, financial_account_id: form.financial_account_id, unallocated_kind: form.unallocated_kind, allow_advance: form.allow_advance };
        }
        setAttempted(true);
        setBusy(true);
        setError("");
        try {
            await (allocation ? allocateMezanSupplierPayment : recordMezanSupplierPayment)(supplierId, intent.current);
            setRecorded(true);
            // Once accepted, never retry the financial write if the subsequent refresh fails.
            try { await onSaved?.(); onClose(); }
            catch { setError("تم تسجيل العملية، لكن تعذّر تحديث العرض. أغلق النافذة وحدّث الصفحة."); }
        } catch (failure) {
            // Only definitive request validation rejects may start a corrected intent.
            // Network/server errors retain the exact idempotency key and payload.
            if (failure.status === 400 || failure.status === 422) {
                intent.current = null;
                setAttempted(false);
            }
            setError(failure.message || "تعذّر التأكد من النتيجة؛ أعد المحاولة بنفس العملية.");
        }
        finally { setBusy(false); }
    }
    return <form onSubmit={submit} className="space-y-4 rounded-2xl border border-emerald-300 bg-white p-4" aria-label={allocation ? "تخصيص دفعة" : "تسجيل دفعة مورد"}>
        <h3 className="text-lg font-black">{allocation ? "تخصيص دفعة على فاتورة" : "تسجيل سداد للمورد"}</h3>
        {!available && <p role="status" className="rounded-xl bg-amber-50 p-3">{workspace.financial_status !== "available" ? "العقد المالي غير جاهز؛ التسجيل غير متاح." : "الحسابات المالية غير جاهزة؛ تسجيل السداد غير متاح حاليًا."}</p>}
        <fieldset disabled={busy || attempted || recorded} className="grid gap-3 sm:grid-cols-2">
            <label>المبلغ (ر.س)<input className={FIELD} inputMode="decimal" value={form.amount} required onChange={(e) => change("amount", e.target.value)} /></label>
            <label>التاريخ<input className={FIELD} type="date" value={form.payment_date} required onChange={(e) => change("payment_date", e.target.value)} /></label>
            {!allocation && <label>بنك / صندوق ميزان 2<select className={FIELD} required value={form.financial_account_id} onChange={(e) => change("financial_account_id", e.target.value)}><option value="">اختر الحساب المعتمد</option>{(workspace.payment_accounts || []).map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label>}
            {allocation && <label>الدفعة<select className={FIELD} required value={form.payment_id} onChange={(e) => change("payment_id", e.target.value)}><option value="">اختر الدفعة</option>{payments.map((row) => <option key={row.id} value={row.id}>{row.reference || row.id} · سداد غير مخصص {supplierMoney(row.unallocated_payable_halalas)} · مقدم {supplierMoney(row.advance_available_halalas)}</option>)}</select></label>}
            <label>الفاتورة<select className={FIELD} required={allocation} value={form.invoice_id} onChange={(e) => change("invoice_id", e.target.value)}><option value="">{allocation ? "اختر الفاتورة" : "غير مخصص"}</option>{eligible.map((row) => <option key={row.id} value={row.id}>{row.invoice_number} · المتبقي {supplierMoney(row.outstanding_halalas)}</option>)}</select></label>
            {!allocation && !form.invoice_id && <label>نوع المبلغ غير المخصص<select className={FIELD} value={form.unallocated_kind} onChange={(e) => change("unallocated_kind", e.target.value)}><option value="payable">سداد رصيد مستحق موثق</option><option value="advance">دفعة مقدمة للمورد</option></select></label>}
            <label>المرجع<input className={FIELD} required maxLength={200} value={form.reference} onChange={(e) => change("reference", e.target.value)} /></label>
            <label>معرّف ملف الدليل (عند الحاجة)<input className={FIELD} value={form.evidence_file_id} onChange={(e) => change("evidence_file_id", e.target.value)} /></label>
            <label className="sm:col-span-2">ملاحظة<textarea className={FIELD} maxLength={2000} value={form.notes} onChange={(e) => change("notes", e.target.value)} /></label>
            {!allocation && <label className="sm:col-span-2"><input type="checkbox" checked={form.allow_advance} onChange={(e) => change("allow_advance", e.target.checked)} /> أوافق صراحةً على تسجيل المبلغ الزائد كدفعة مقدمة منفصلة</label>}
        </fieldset>
        <p className="text-sm text-slate-600">الدفعات المقدمة لا تُخصم تلقائيًا من الرصيد المستحق. تخصيصها لفاتورة إجراء صريح.</p>
        {error && <p role="alert" className="text-rose-800">{error}</p>}
        {attempted && !recorded && !busy && <p className="text-sm">بيانات المحاولة محفوظة. إعادة المحاولة تستخدم رقم العملية نفسه لمنع تكرار السداد.</p>}
        <div className="flex gap-3"><button type="submit" className={BUTTON} disabled={busy || !available || recorded}>{busy ? "جارٍ الحفظ…" : attempted ? "إعادة المحاولة بنفس العملية" : allocation ? "تأكيد التخصيص" : "تأكيد تسجيل السداد"}</button><button type="button" disabled={busy} onClick={onClose} className="rounded-xl border px-4 py-2">إغلاق</button></div>
    </form>;
}

export function SupplierFinancialDetail({ supplier, invoices = [], timeline = [], workspace = {}, onSaved, onClose }) {
    const [action, setAction] = useState(null);
    useEffect(() => { setAction(null); }, [supplier?.id]);
    if (!supplier) return null;
    const financial = supplier.financial || {};
    const ready = workspace.financial_status === "available";
    const eligible = invoices.filter((row) => row.financial_eligible === true);
    const historical = invoices.filter((row) => row.financial_eligible !== true);
    return <div className="fixed inset-0 z-[115] overflow-y-auto bg-slate-950/65 p-2 sm:p-4" role="dialog" aria-modal="true" aria-label={`حساب المورد ${supplier.company_name}`} data-testid="mezan-supplier-financial-overlay">
        <section className="mx-auto max-w-6xl space-y-5 rounded-3xl bg-slate-50 p-4 sm:p-6" dir="rtl" data-testid="mezan-supplier-financial-detail">
            <header className="flex items-center justify-between"><h2 className="text-2xl font-black">{supplier.company_name}</h2><button type="button" onClick={onClose} aria-label="إغلاق حساب المورد" className="rounded-xl border p-3">إغلاق</button></header>
            {!ready && <p role="status" className="rounded-xl bg-amber-50 p-4">البيانات المالية غير جاهزة؛ لا يمكن اعتبار الرصيد غير المتاح صفرًا.</p>}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[["الرصيد المستحق", financial.outstanding_halalas], ["الدفعات المقدمة", financial.advance_halalas], ["إجمالي الفواتير المؤهلة", financial.invoiced_halalas], ["المدفوع المسجل", financial.paid_halalas]].map(([label, value]) => <div key={label} className="rounded-xl border bg-white p-4"><div>{label}</div><div className="mt-2 text-xl font-black">{supplierMoney(ready ? value : null)} ر.س</div></div>)}</div>
            <p className="rounded-xl bg-sky-50 p-4">الرصيد الافتتاحي هو الوضع الفعلي الموثق عند الانتقال. الفواتير القديمة المسددة خارج النظام لا تعيد إنشاء مديونية. الرصيد المستحق والدفعات المقدمة منفصلان دون مقاصة تلقائية.</p>
            <div className="flex flex-wrap gap-3"><button type="button" className={BUTTON} onClick={() => setAction({ mode: "payment", invoiceId: "" })}>دفع مبلغ للمورد</button><button type="button" className={BUTTON} onClick={() => setAction({ mode: "allocation", invoiceId: "" })}>تخصيص دفعة على فاتورة</button></div>
            {action && <SupplierPaymentForm key={`${supplier.id}:${action.mode}:${action.invoiceId}`} supplierId={supplier.id} invoices={invoices} workspace={workspace} initialInvoiceId={action.invoiceId} mode={action.mode} onSaved={onSaved} onClose={() => setAction(null)} />}
            <section className="space-y-3"><h3 className="text-xl font-black">الفواتير المالية المؤهلة</h3>{!eligible.length && <p>{ready ? "لا توجد فواتير مالية مؤهلة." : "قائمة الفواتير المالية غير متاحة."}</p>}{eligible.map((invoice) => <article key={invoice.id} className="rounded-xl border bg-white p-4" data-testid="mezan-supplier-real-invoice"><h4 className="font-black">{invoice.invoice_number}</h4><dl className="my-3 grid grid-cols-2 gap-3 sm:grid-cols-4">{[["إجمالي الفاتورة", supplierMoney(invoice.total_halalas)], ["المدفوع", supplierMoney(invoice.paid_halalas)], ["المتبقي", supplierMoney(invoice.outstanding_halalas)], ["حالة السداد", STATES[invoice.payment_status] || "غير متاح"]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd className="font-bold">{value}</dd></div>)}</dl><button type="button" disabled={!ready || !(invoice.outstanding_halalas > 0)} className={BUTTON} onClick={() => setAction({ mode: "payment", invoiceId: invoice.id })}>تسجيل سداد</button></article>)}</section>
            {!!historical.length && <section className="space-y-3"><h3 className="text-xl font-black">سجل تاريخي / غير مؤهل ماليًا</h3>{historical.map((invoice) => <article key={invoice.id} className="rounded-xl border bg-slate-100 p-4" data-testid="mezan-supplier-history-invoice"><div className="font-black">{invoice.invoice_number}</div><p>{supplierMoney(invoice.total_halalas)} ر.س · لا ينشئ رصيدًا مستحقًا</p><p className="text-sm">{invoice.exclusion_reason === "invoice_reversed_requires_reconciliation" ? "تم عكس قيد الفاتورة؛ يلزم مراجعتها وتسويتها." : "لم يثبت ارتباط هذه الفاتورة بالسجل المالي المعتمد لميزان 2."}</p></article>)}</section>}
            <section className="space-y-3"><h3 className="text-xl font-black">كشف حساب المورد</h3>{!timeline.length && <p>{ready ? "لا توجد حركات مالية مؤهلة." : "الحركات المالية غير متاحة."}</p>}{timeline.map((row) => <article key={row.id} className="grid gap-2 rounded-xl border bg-white p-3 sm:grid-cols-3"><div>{row.sub_account === "advance" ? "دفعات مقدمة" : row.sub_account === "payable" ? "رصيد مستحق" : "نوع حساب غير متاح"} · {row.side === "debit" ? "مدين" : row.side === "credit" ? "دائن" : "—"}</div><div>{supplierMoney(row.amount_halalas)} ر.س</div><div>{row.created_at} {row.notes}</div></article>)}</section>
        </section>
    </div>;
}
