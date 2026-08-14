import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    CalendarBlank,
    CaretLeft,
    CheckCircle,
    DotsThreeVertical,
    FileText,
    MagnifyingGlass,
    NotePencil,
    Plus,
    Receipt,
    SpinnerGap,
    StopCircle,
    WarningCircle,
    X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    createRecurringInvoice,
    createRecurringObligation,
    loadRecurringInvoices,
    loadRecurringObligationsWorkspace,
    updateRecurringObligation,
} from "../services/recurringObligations";


const TYPE_OPTIONS = [
    { value: "rent", label: "إيجار", group: "rent", entity: "location" },
    { value: "electricity", label: "كهرباء", group: "utilities", entity: "location" },
    { value: "water", label: "ماء", group: "utilities", entity: "location" },
    { value: "iqama_visa", label: "إقامة وتأشيرة", group: "renewals", entity: "employee" },
    { value: "employee_insurance", label: "تأمين موظف", group: "renewals", entity: "employee" },
    { value: "vehicle_insurance", label: "تأمين مركبة", group: "renewals", entity: "vehicle" },
    { value: "commercial_registration", label: "سجل تجاري", group: "renewals", entity: "business" },
    { value: "government_license", label: "رخصة أو تصريح", group: "renewals", entity: "business" },
    { value: "subscription", label: "اشتراك دوري", group: "renewals", entity: "business" },
    { value: "other", label: "التزام آخر", group: "renewals", entity: "other" },
];

const CYCLE_OPTIONS = [
    { value: "monthly", label: "شهري" },
    { value: "semiannual", label: "كل 6 أشهر" },
    { value: "annual", label: "سنوي" },
    { value: "biennial", label: "كل سنتين" },
    { value: "custom", label: "فترة مخصصة" },
];

const STATUS = {
    active: { label: "نشط", cls: "bg-emerald-100 text-emerald-800" },
    stopped: { label: "متوقف", cls: "bg-slate-200 text-slate-700" },
    due_soon: { label: "قريب", cls: "bg-amber-100 text-amber-900" },
    overdue: { label: "متأخر", cls: "bg-rose-100 text-rose-800" },
};

const FILTERS = [
    { value: "all", label: "الكل" },
    { value: "rent", label: "الإيجارات" },
    { value: "utilities", label: "الكهرباء والماء" },
    { value: "renewals", label: "التجديدات" },
];

const money = new Intl.NumberFormat("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});

function riyadhToday() {
    return new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).format(new Date());
}

function emptyHistory() {
    return [0, 1, 2].map(() => ({ amount: "", period_start: "", period_end: "" }));
}

function emptyForm() {
    return {
        title: "",
        expense_type: "rent",
        entity_type: "warehouse",
        entity_id: "",
        entity_name: "",
        cycle: "annual",
        period_amount: "",
        start_date: riyadhToday(),
        custom_end_date: "",
        auto_renew: true,
        estimation_basis: "last_3_invoices",
        notes: "",
        historical_invoices: emptyHistory(),
    };
}

function typeConfig(value) {
    return TYPE_OPTIONS.find((item) => item.value === value) || TYPE_OPTIONS[TYPE_OPTIONS.length - 1];
}

function typeLabel(value) {
    return typeConfig(value).label;
}

function cycleLabel(value) {
    return CYCLE_OPTIONS.find((item) => item.value === value)?.label || value;
}

function errorMessage(error) {
    const detail = error?.response?.data?.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    const labels = {
        linked_entity_not_found: "الفرع أو الموظف المرتبط غير موجود أو غير نشط.",
        invoice_period_overlap: "هذه الفترة تتداخل مع فاتورة مسجلة سابقًا.",
        obligation_invalid: "راجع مبلغ الالتزام والفترة المحددة.",
        owner_required: "هذه الصفحة متاحة لمالك المتجر فقط.",
    };
    return labels[code] || "تعذّر حفظ البيانات. راجع الحقول وحاول مرة أخرى.";
}

function relatedEntityMode(expenseType) {
    return typeConfig(expenseType).entity;
}

function normalizedFormForRow(row) {
    return {
        ...emptyForm(),
        title: row.title || "",
        expense_type: row.expense_type,
        entity_type: row.entity_type,
        entity_id: row.entity_id || "",
        entity_name: row.entity_name || "",
        cycle: row.cycle,
        period_amount: row.period_amount || "",
        start_date: row.start_date,
        custom_end_date: row.custom_end_date || "",
        auto_renew: row.auto_renew !== false,
        estimation_basis: row.estimation_basis || "last_3_invoices",
        notes: row.notes || "",
    };
}

function buildTitle(expenseType, entityName) {
    const label = typeLabel(expenseType);
    return entityName ? `${label} ${entityName}` : label;
}

function CompactSummary({ summary }) {
    const items = [
        ["المصروف اليومي", `${money.format(summary.daily_total || 0)} ر.س`, "text-slate-950"],
        ["المستحق خلال 30 يوم", `${money.format(summary.due_next_30_days || 0)} ر.س`, "text-slate-950"],
        ["التزامات نشطة", money.format(summary.active_count || 0).replace(".00", ""), "text-emerald-700"],
        ["متأخرة", money.format(summary.overdue_count || 0).replace(".00", ""), "text-rose-700"],
    ];
    return (
        <section className="grid overflow-hidden rounded-xl border border-slate-200 bg-white sm:grid-cols-2 xl:grid-cols-4" data-testid="recurring-summary-strip">
            {items.map(([label, value, cls], index) => (
                <div key={label} className={`px-4 py-3 text-center ${index ? "border-t border-slate-100 sm:border-r sm:border-t-0" : ""}`}>
                    <div className="text-[11px] font-bold text-slate-500">{label}</div>
                    <div className={`mt-1 text-lg font-black tabular-nums ${cls}`} dir="ltr">{value}</div>
                </div>
            ))}
        </section>
    );
}

function EntityField({ form, options, onChange }) {
    const mode = relatedEntityMode(form.expense_type);
    if (mode === "business") {
        return (
            <label className="block">
                <span className="field-label">مرتبط بـ</span>
                <input className="field-input bg-slate-50" value="المنشأة" readOnly />
            </label>
        );
    }
    if (mode === "location") {
        return (
            <label className="block">
                <span className="field-label">الفرع أو المستودع</span>
                <select
                    className="field-input"
                    value={form.entity_id}
                    onChange={(event) => {
                        const selected = options.locations.find((row) => row.id === event.target.value);
                        onChange({
                            entity_id: event.target.value,
                            entity_name: selected?.name || "",
                            entity_type: "warehouse",
                            title: buildTitle(form.expense_type, selected?.name || ""),
                        });
                    }}
                    required
                >
                    <option value="">اختر الفرع أو المستودع</option>
                    {options.locations.map((row) => (
                        <option key={row.id} value={row.id}>{row.name}{row.code ? ` — ${row.code}` : ""}</option>
                    ))}
                </select>
            </label>
        );
    }
    if (mode === "employee") {
        return (
            <label className="block">
                <span className="field-label">الموظف</span>
                <select
                    className="field-input"
                    value={form.entity_id}
                    onChange={(event) => {
                        const selected = options.employees.find((row) => row.id === event.target.value);
                        onChange({
                            entity_id: event.target.value,
                            entity_name: selected?.display_name || "",
                            entity_type: "employee",
                            title: buildTitle(form.expense_type, selected?.display_name || ""),
                        });
                    }}
                    required
                >
                    <option value="">اختر الموظف</option>
                    {options.employees.map((row) => (
                        <option key={row.id} value={row.id}>{row.display_name}</option>
                    ))}
                </select>
            </label>
        );
    }
    return (
        <label className="block">
            <span className="field-label">مرتبط بـ</span>
            <input
                className="field-input"
                value={form.entity_name}
                placeholder={mode === "vehicle" ? "اسم المركبة أو رقم اللوحة" : "اسم الجهة أو الأصل"}
                onChange={(event) => onChange({
                    entity_name: event.target.value,
                    entity_type: mode === "vehicle" ? "vehicle" : "other",
                    title: buildTitle(form.expense_type, event.target.value),
                })}
                required
            />
        </label>
    );
}

function HistoricalInvoices({ form, onChange }) {
    const utility = ["electricity", "water"].includes(form.expense_type);
    if (!utility || form.estimation_basis === "manual") return null;
    const count = form.estimation_basis === "last_invoice" ? 1 : 3;
    const rows = form.historical_invoices.slice(0, count);
    function updateRow(index, field, value) {
        const next = form.historical_invoices.map((row, rowIndex) => (
            rowIndex === index ? { ...row, [field]: value } : row
        ));
        onChange({ historical_invoices: next });
    }
    return (
        <section className="rounded-xl border border-sky-200 bg-sky-50/60 p-3" data-testid="utility-history-fields">
            <div className="text-xs font-black text-sky-950">فواتير سابقة لمعيار ميزان</div>
            <p className="mt-1 text-[11px] font-bold leading-5 text-sky-800">يحسب ميزان مجموع المبالغ ÷ مجموع أيام الاستهلاك الفعلية.</p>
            <div className="mt-3 space-y-2">
                {rows.map((row, index) => (
                    <div key={index} className="grid grid-cols-[1fr_1fr_0.8fr] gap-2">
                        <input aria-label={`بداية الفاتورة ${index + 1}`} type="date" className="field-input min-h-10 px-2 text-xs" value={row.period_start} onChange={(event) => updateRow(index, "period_start", event.target.value)} />
                        <input aria-label={`نهاية الفاتورة ${index + 1}`} type="date" className="field-input min-h-10 px-2 text-xs" value={row.period_end} onChange={(event) => updateRow(index, "period_end", event.target.value)} />
                        <input aria-label={`مبلغ الفاتورة ${index + 1}`} inputMode="decimal" className="field-input min-h-10 px-2 text-xs" placeholder="المبلغ" value={row.amount} onChange={(event) => updateRow(index, "amount", event.target.value)} />
                    </div>
                ))}
            </div>
        </section>
    );
}

function ObligationEditor({ row, options, onClose, onSaved }) {
    const editing = Boolean(row?.id);
    const [form, setForm] = useState(() => editing ? normalizedFormForRow(row) : emptyForm());
    const [submitting, setSubmitting] = useState(false);
    const [error, setError] = useState("");
    const config = typeConfig(form.expense_type);
    const utility = config.group === "utilities";

    function patch(values) {
        setForm((current) => ({ ...current, ...values }));
    }

    function changeType(value) {
        const next = typeConfig(value);
        const defaults = {
            expense_type: value,
            entity_id: "",
            entity_name: next.entity === "business" ? "المنشأة" : "",
            entity_type: next.entity === "business" ? "business" : next.entity === "employee" ? "employee" : next.entity === "vehicle" ? "vehicle" : "warehouse",
            title: buildTitle(value, next.entity === "business" ? "المنشأة" : ""),
            estimation_basis: next.group === "utilities" ? "last_3_invoices" : null,
        };
        patch(defaults);
    }

    async function submit(event) {
        event.preventDefault();
        const history = form.historical_invoices.filter((invoice) => (
            invoice.amount && invoice.period_start && invoice.period_end
        )).map((invoice) => ({ ...invoice, amount: Number(invoice.amount) }));
        if (!form.title.trim() || !form.entity_name.trim()) {
            setError("اسم الالتزام والجهة المرتبطة مطلوبان.");
            return;
        }
        if (!utility && Number(form.period_amount) <= 0) {
            setError("أدخل مبلغ الفترة.");
            return;
        }
        if (utility && form.estimation_basis !== "manual" && !editing && history.length === 0) {
            setError("أدخل فاتورة سابقة واحدة على الأقل، أو اختر مبلغًا تقديريًا يدويًا.");
            return;
        }
        const payload = {
            title: form.title.trim(),
            entity_type: form.entity_type,
            entity_id: form.entity_id || null,
            entity_name: form.entity_name.trim(),
            cycle: form.cycle,
            period_amount: Number(form.period_amount || 0),
            start_date: form.start_date,
            custom_end_date: form.cycle === "custom" ? form.custom_end_date : null,
            auto_renew: form.auto_renew,
            estimation_basis: utility ? form.estimation_basis : null,
            notes: form.notes.trim(),
        };
        if (!editing) {
            payload.expense_type = form.expense_type;
            payload.historical_invoices = history;
        }
        setSubmitting(true);
        setError("");
        try {
            if (editing) await updateRecurringObligation(row.id, payload);
            else await createRecurringObligation(payload);
            await onSaved();
            toast.success(editing ? "تم تحديث الالتزام" : "تمت إضافة الالتزام");
            onClose();
        } catch (saveError) {
            setError(errorMessage(saveError));
        } finally {
            setSubmitting(false);
        }
    }

    return (
        <div className="fixed inset-0 z-[120] bg-slate-950/35" role="dialog" aria-modal="true" aria-label={editing ? "تعديل الالتزام" : "إضافة التزام"}>
            <form onSubmit={submit} className="absolute inset-y-0 left-0 flex w-full max-w-[25rem] flex-col bg-white shadow-2xl" dir="rtl" data-testid="recurring-obligation-editor">
                <header className="flex items-center justify-between border-b border-slate-200 px-5 py-4">
                    <div><h2 className="text-xl font-black text-slate-950">{editing ? "تعديل الالتزام" : "إضافة التزام"}</h2><p className="mt-1 text-[11px] font-bold text-slate-500">المبلغ يُوزع تلقائيًا على أيام الفترة الفعلية.</p></div>
                    <button type="button" onClick={onClose} className="rounded-lg border border-slate-200 p-2 text-slate-600" aria-label="إغلاق"><X size={20} /></button>
                </header>
                <div className="recurring-fields flex-1 space-y-4 overflow-y-auto p-5">
                    {error && <div className="flex gap-2 rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-black text-rose-900"><WarningCircle size={19} className="shrink-0" />{error}</div>}
                    <label className="block">
                        <span className="field-label">نوع الالتزام</span>
                        <select className="field-input" value={form.expense_type} onChange={(event) => changeType(event.target.value)} disabled={editing}>
                            {TYPE_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                        </select>
                    </label>
                    <EntityField form={form} options={options} onChange={patch} />
                    <label className="block"><span className="field-label">اسم الالتزام</span><input className="field-input" value={form.title} onChange={(event) => patch({ title: event.target.value })} required /></label>
                    <div>
                        <span className="field-label">الفترة</span>
                        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                            {CYCLE_OPTIONS.slice(0, 4).map((item) => (
                                <button key={item.value} type="button" onClick={() => patch({ cycle: item.value })} className={`min-h-10 rounded-lg border px-2 text-[11px] font-black ${form.cycle === item.value ? "border-blue-700 bg-blue-50 text-blue-800" : "border-slate-200 bg-white text-slate-600"}`}>{item.label}</button>
                            ))}
                        </div>
                        <button type="button" onClick={() => patch({ cycle: "custom" })} className={`mt-1.5 min-h-9 w-full rounded-lg border text-[11px] font-black ${form.cycle === "custom" ? "border-blue-700 bg-blue-50 text-blue-800" : "border-slate-200 text-slate-600"}`}>فترة مخصصة</button>
                    </div>
                    {utility && (
                        <label className="block"><span className="field-label">معيار التقدير قبل وصول الفاتورة</span><select className="field-input" value={form.estimation_basis} onChange={(event) => patch({ estimation_basis: event.target.value })}><option value="last_3_invoices">متوسط آخر 3 فواتير</option><option value="last_invoice">آخر فاتورة</option><option value="manual">مبلغ تقديري يدوي</option></select></label>
                    )}
                    {(!utility || form.estimation_basis === "manual") && (
                        <label className="block"><span className="field-label">{utility ? "المبلغ التقديري للفترة" : "مبلغ الفترة"}</span><div className="relative"><input className="field-input pl-14 tabular-nums" dir="ltr" inputMode="decimal" value={form.period_amount} onChange={(event) => patch({ period_amount: event.target.value })} required /><span className="absolute left-3 top-3 text-xs font-black text-slate-400">ر.س</span></div></label>
                    )}
                    <div className={`grid gap-3 ${form.cycle === "custom" ? "grid-cols-2" : ""}`}>
                        <label className="block"><span className="field-label">تاريخ البداية</span><input type="date" className="field-input" value={form.start_date} onChange={(event) => patch({ start_date: event.target.value })} required /></label>
                        {form.cycle === "custom" && <label className="block"><span className="field-label">تاريخ النهاية</span><input type="date" className="field-input" value={form.custom_end_date} onChange={(event) => patch({ custom_end_date: event.target.value })} required /></label>}
                    </div>
                    <HistoricalInvoices form={form} onChange={patch} />
                    <label className="flex items-center justify-between rounded-xl border border-slate-200 px-3 py-3"><span><span className="block text-xs font-black text-slate-800">تجديد تلقائي</span><span className="mt-0.5 block text-[10px] font-bold text-slate-500">تبدأ فترة جديدة تلقائيًا بعد انتهاء الحالية</span></span><input type="checkbox" className="h-5 w-5 accent-blue-700" checked={form.auto_renew} onChange={(event) => patch({ auto_renew: event.target.checked })} /></label>
                    <label className="block"><span className="field-label">ملاحظات</span><textarea className="field-input min-h-20 py-3" value={form.notes} onChange={(event) => patch({ notes: event.target.value })} /></label>
                </div>
                <footer className="space-y-2 border-t border-slate-200 p-5"><button type="submit" disabled={submitting} className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-blue-800 px-4 text-sm font-black text-white disabled:opacity-50">{submitting ? <SpinnerGap className="animate-spin" /> : <CheckCircle size={20} />}حفظ الالتزام</button><button type="button" onClick={onClose} className="min-h-11 w-full rounded-xl border border-slate-200 text-sm font-black text-slate-700">إلغاء</button></footer>
            </form>
        </div>
    );
}

function InvoiceEditor({ obligation, onClose, onSaved }) {
    const [form, setForm] = useState({ amount: "", period_start: "", period_end: "", issue_date: riyadhToday(), due_date: "", payment_status: "unpaid", paid_date: "", notes: "" });
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    async function submit(event) {
        event.preventDefault();
        setBusy(true);
        setError("");
        try {
            await createRecurringInvoice(obligation.id, {
                ...form,
                amount: Number(form.amount),
                issue_date: form.issue_date || null,
                due_date: form.due_date || null,
                paid_date: form.payment_status === "paid" ? (form.paid_date || form.issue_date || riyadhToday()) : null,
            });
            await onSaved();
            toast.success("تمت إضافة الفاتورة وتصحيح المصروف تلقائيًا");
            onClose();
        } catch (saveError) {
            setError(errorMessage(saveError));
        } finally {
            setBusy(false);
        }
    }
    return (
        <div className="fixed inset-0 z-[130] bg-slate-950/35" role="dialog" aria-modal="true">
            <form onSubmit={submit} className="absolute inset-y-0 left-0 flex w-full max-w-[25rem] flex-col bg-white shadow-2xl" dir="rtl" data-testid="recurring-invoice-editor">
                <header className="flex items-center justify-between border-b p-5"><div><h2 className="text-xl font-black">إضافة فاتورة</h2><p className="mt-1 text-xs font-bold text-slate-500">{obligation.title}</p></div><button type="button" onClick={onClose} className="rounded-lg border p-2"><X size={20} /></button></header>
                <div className="recurring-fields flex-1 space-y-4 overflow-y-auto p-5">
                    {error && <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs font-black text-rose-900">{error}</div>}
                    <label><span className="field-label">مبلغ الفاتورة</span><input className="field-input" inputMode="decimal" dir="ltr" value={form.amount} onChange={(event) => setForm({ ...form, amount: event.target.value })} required /></label>
                    <div className="grid grid-cols-2 gap-3"><label><span className="field-label">بداية الاستهلاك</span><input type="date" className="field-input" value={form.period_start} onChange={(event) => setForm({ ...form, period_start: event.target.value })} required /></label><label><span className="field-label">نهاية الاستهلاك</span><input type="date" className="field-input" value={form.period_end} onChange={(event) => setForm({ ...form, period_end: event.target.value })} required /></label></div>
                    <div className="grid grid-cols-2 gap-3"><label><span className="field-label">تاريخ الفاتورة</span><input type="date" className="field-input" value={form.issue_date} onChange={(event) => setForm({ ...form, issue_date: event.target.value })} /></label><label><span className="field-label">تاريخ الاستحقاق</span><input type="date" className="field-input" value={form.due_date} onChange={(event) => setForm({ ...form, due_date: event.target.value })} /></label></div>
                    <label><span className="field-label">حالة السداد</span><select className="field-input" value={form.payment_status} onChange={(event) => setForm({ ...form, payment_status: event.target.value })}><option value="unpaid">غير مدفوعة</option><option value="paid">مدفوعة</option></select></label>
                    {form.payment_status === "paid" && <label><span className="field-label">تاريخ السداد</span><input type="date" className="field-input" value={form.paid_date} onChange={(event) => setForm({ ...form, paid_date: event.target.value })} /></label>}
                    <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-[11px] font-bold leading-5 text-emerald-900">إضافة الفاتورة تستبدل التقدير خلال أيامها بالمبلغ الحقيقي. حالة السداد لا تخصم المصروف مرة ثانية.</div>
                </div>
                <footer className="border-t p-5"><button disabled={busy} className="flex min-h-12 w-full items-center justify-center gap-2 rounded-xl bg-blue-800 font-black text-white">{busy ? <SpinnerGap className="animate-spin" /> : <Receipt size={20} />}حفظ الفاتورة</button></footer>
            </form>
        </div>
    );
}

function InvoiceHistory({ obligation, onClose, onAdd }) {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        loadRecurringInvoices(obligation.id).then(setItems).catch(() => toast.error("تعذّر تحميل الفواتير")).finally(() => setLoading(false));
    }, [obligation.id]);
    return (
        <div className="fixed inset-0 z-[125] bg-slate-950/35" role="dialog" aria-modal="true">
            <aside className="absolute inset-y-0 left-0 flex w-full max-w-[29rem] flex-col bg-white shadow-2xl" dir="rtl">
                <header className="flex items-center justify-between border-b p-5"><div><h2 className="text-xl font-black">تفاصيل الالتزام</h2><p className="mt-1 text-xs font-bold text-slate-500">{obligation.title}</p></div><button type="button" onClick={onClose} className="rounded-lg border p-2"><X size={20} /></button></header>
                <div className="flex-1 overflow-y-auto p-5">
                    <div className="grid grid-cols-2 gap-2 text-xs"><div className="rounded-xl bg-slate-50 p-3"><span className="text-slate-500">المصروف اليومي</span><strong className="mt-1 block text-lg tabular-nums" dir="ltr">{money.format(obligation.daily_amount)} ر.س</strong></div><div className="rounded-xl bg-slate-50 p-3"><span className="text-slate-500">المتراكم حتى اليوم</span><strong className="mt-1 block text-lg tabular-nums" dir="ltr">{money.format(obligation.accrued_to_today)} ر.س</strong></div></div>
                    <div className="mt-5 flex items-center justify-between"><h3 className="font-black">الفواتير السابقة</h3>{["electricity", "water"].includes(obligation.expense_type) && <button type="button" onClick={onAdd} className="inline-flex items-center gap-1 rounded-lg bg-blue-50 px-3 py-2 text-xs font-black text-blue-800"><Plus size={16} />إضافة فاتورة</button>}</div>
                    {loading ? <div className="grid place-items-center py-12"><SpinnerGap className="animate-spin text-blue-800" size={28} /></div> : !items.length ? <div className="mt-3 rounded-xl border border-dashed p-6 text-center text-xs font-bold text-slate-500">لا توجد فواتير مسجلة.</div> : <div className="mt-3 space-y-2">{items.map((invoice) => <article key={invoice.id} className="rounded-xl border border-slate-200 p-3"><div className="flex items-start justify-between gap-3"><div><div className="text-xs font-black">{invoice.period_start} — {invoice.period_end}</div><div className="mt-1 text-[10px] font-bold text-slate-500">{invoice.payment_status === "paid" ? "مدفوعة" : "غير مدفوعة"}{invoice.due_date ? ` · استحقاق ${invoice.due_date}` : ""}</div></div><div className="font-black tabular-nums" dir="ltr">{money.format(invoice.amount)} ر.س</div></div></article>)}</div>}
                </div>
            </aside>
        </div>
    );
}

export default function RecurringObligations() {
    const [data, setData] = useState({ items: [], summary: {}, options: { locations: [], employees: [] } });
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [query, setQuery] = useState("");
    const [filter, setFilter] = useState("all");
    const [editor, setEditor] = useState(null);
    const [history, setHistory] = useState(null);
    const [invoiceEditor, setInvoiceEditor] = useState(null);

    const load = useCallback(async (soft = false) => {
        if (soft) setRefreshing(true); else setLoading(true);
        try {
            setData(await loadRecurringObligationsWorkspace());
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);

    const visible = useMemo(() => {
        const needle = query.trim().toLowerCase();
        return data.items.filter((row) => {
            const config = typeConfig(row.expense_type);
            const filterMatch = filter === "all" || config.group === filter || row.expense_type === filter;
            const searchMatch = !needle || `${row.title} ${row.entity_name} ${config.label}`.toLowerCase().includes(needle);
            return filterMatch && searchMatch;
        });
    }, [data.items, filter, query]);

    async function stop(row) {
        if (!window.confirm(`إيقاف ${row.title} من اليوم؟ لن يُحتسب ابتداءً من تاريخ الإيقاف.`)) return;
        try {
            await updateRecurringObligation(row.id, { status: "stopped" });
            await load(true);
            toast.success("تم إيقاف الالتزام");
        } catch (error) {
            toast.error(errorMessage(error));
        }
    }

    return (
        <main className="mx-auto w-full max-w-[1600px] space-y-4 px-3 py-4 sm:px-5 lg:px-6" dir="rtl" data-testid="recurring-obligations-page">
            <style>{`.recurring-fields .field-label{display:block;margin-bottom:.4rem;font-size:.72rem;font-weight:900;color:#334155}.recurring-fields .field-input{min-height:2.75rem;width:100%;border-radius:.65rem;border:1px solid #cbd5e1;padding:.55rem .75rem;font-size:.82rem;font-weight:700;outline:none}.recurring-fields .field-input:focus{border-color:#1d4ed8;box-shadow:0 0 0 2px rgba(37,99,235,.08)}`}</style>
            <header className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div><div className="text-[11px] font-bold text-slate-500">الرئيسية / المصروفات</div><h1 className="mt-1 text-2xl font-black text-slate-950 sm:text-3xl">الالتزامات والمصاريف الدورية</h1><p className="mt-1 text-xs font-bold text-slate-500">المصروف يزيد يوميًا حسب أيام الفترة الفعلية، والسداد لا يكرر الخصم من الأرباح.</p></div>
                <div className="flex gap-2"><button type="button" onClick={() => load(true)} disabled={refreshing} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-black text-slate-700"><ArrowClockwise className={refreshing ? "animate-spin" : ""} size={18} />تحديث</button><button type="button" onClick={() => setEditor({})} className="inline-flex min-h-10 items-center gap-2 rounded-xl bg-blue-800 px-4 text-xs font-black text-white shadow-sm" data-testid="add-recurring-obligation"><Plus size={18} weight="bold" />إضافة التزام</button></div>
            </header>

            <CompactSummary summary={data.summary} />

            <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm">
                <div className="flex flex-col gap-3 border-b border-slate-200 p-3 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex flex-wrap gap-1.5">{FILTERS.map((item) => <button key={item.value} type="button" onClick={() => setFilter(item.value)} className={`min-h-9 rounded-lg border px-3 text-xs font-black ${filter === item.value ? "border-blue-700 bg-blue-50 text-blue-800" : "border-slate-200 bg-white text-slate-600"}`}>{item.label}</button>)}</div>
                    <label className="relative block w-full lg:w-72"><MagnifyingGlass size={18} className="absolute right-3 top-2.5 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="بحث..." className="min-h-10 w-full rounded-lg border border-slate-200 pr-10 pl-3 text-xs font-bold outline-none focus:border-blue-600" /></label>
                </div>
                {loading ? <div className="grid min-h-72 place-items-center"><div className="text-center"><SpinnerGap className="mx-auto animate-spin text-blue-800" size={32} /><div className="mt-2 text-xs font-bold text-slate-500">جاري تحميل الالتزامات...</div></div></div> : !visible.length ? <div className="grid min-h-72 place-items-center p-6 text-center"><div><CalendarBlank size={40} className="mx-auto text-slate-300" /><h2 className="mt-3 font-black text-slate-700">لا توجد التزامات مطابقة</h2><p className="mt-1 text-xs font-bold text-slate-500">أضف أول التزام وسيبدأ الاحتساب اليومي تلقائيًا.</p></div></div> : (
                    <div className="overflow-x-auto">
                        <table className="w-full min-w-[980px] text-right text-xs">
                            <thead className="bg-slate-50 text-[11px] font-black text-slate-600"><tr><th className="px-4 py-3">الالتزام</th><th className="px-3 py-3">مرتبط بـ</th><th className="px-3 py-3">الفترة</th><th className="px-3 py-3 text-left">المبلغ</th><th className="px-3 py-3 text-left">اليومي</th><th className="px-3 py-3 text-center">الاستحقاق القادم</th><th className="px-3 py-3 text-center">الحالة</th><th className="w-14 px-2 py-3"></th></tr></thead>
                            <tbody>{visible.map((row) => {
                                const display = STATUS[row.display_status] || STATUS.active;
                                const utility = ["electricity", "water"].includes(row.expense_type);
                                return <tr key={row.id} className="border-t border-slate-100 hover:bg-slate-50/70"><td className="px-4 py-3"><button type="button" onClick={() => setHistory(row)} className="text-right"><span className="block font-black text-slate-900">{row.title}</span><span className="mt-0.5 block text-[10px] font-bold text-slate-400">{typeLabel(row.expense_type)} · {row.invoice_count || 0} فواتير</span></button></td><td className="px-3 py-3 font-bold text-slate-700">{row.entity_name}</td><td className="px-3 py-3 font-bold text-slate-700">{cycleLabel(row.cycle)}</td><td className="px-3 py-3 text-left font-black tabular-nums" dir="ltr">{utility && !row.period_amount ? "تقديري" : `${money.format(row.period_amount)} ر.س`}</td><td className="px-3 py-3 text-left font-black tabular-nums text-blue-800" dir="ltr">{money.format(row.daily_amount)} ر.س</td><td className="px-3 py-3 text-center font-bold tabular-nums" dir="ltr">{row.next_due_date || "—"}</td><td className="px-3 py-3 text-center"><span className={`inline-flex rounded-md px-2.5 py-1 text-[11px] font-black ${display.cls}`}>{display.label}</span></td><td className="px-2 py-3"><details className="relative"><summary className="cursor-pointer list-none rounded-lg p-2 text-slate-500 hover:bg-slate-100"><DotsThreeVertical size={18} /></summary><div className="absolute left-0 z-20 mt-1 w-40 overflow-hidden rounded-xl border border-slate-200 bg-white py-1 shadow-xl"><button type="button" onClick={() => setHistory(row)} className="flex w-full items-center gap-2 px-3 py-2 text-right text-[11px] font-black hover:bg-slate-50"><FileText size={16} />التفاصيل</button><button type="button" onClick={() => setEditor(row)} className="flex w-full items-center gap-2 px-3 py-2 text-right text-[11px] font-black hover:bg-slate-50"><NotePencil size={16} />تعديل</button>{utility && <button type="button" onClick={() => setInvoiceEditor(row)} className="flex w-full items-center gap-2 px-3 py-2 text-right text-[11px] font-black hover:bg-slate-50"><Receipt size={16} />إضافة فاتورة</button>}{row.status === "active" && <button type="button" onClick={() => stop(row)} className="flex w-full items-center gap-2 px-3 py-2 text-right text-[11px] font-black text-rose-700 hover:bg-rose-50"><StopCircle size={16} />إيقاف</button>}</div></details></td></tr>;
                            })}</tbody>
                        </table>
                    </div>
                )}
                <footer className="flex items-center justify-between border-t border-slate-200 px-4 py-3 text-[11px] font-bold text-slate-500"><span>{visible.length} التزام</span><span className="inline-flex items-center gap-1">المبالغ بالأرقام الإنجليزية <CaretLeft size={13} /></span></footer>
            </section>

            {editor && <ObligationEditor row={editor.id ? editor : null} options={data.options} onClose={() => setEditor(null)} onSaved={() => load(true)} />}
            {history && <InvoiceHistory obligation={history} onClose={() => setHistory(null)} onAdd={() => { setInvoiceEditor(history); setHistory(null); }} />}
            {invoiceEditor && <InvoiceEditor obligation={invoiceEditor} onClose={() => setInvoiceEditor(null)} onSaved={() => load(true)} />}
        </main>
    );
}
