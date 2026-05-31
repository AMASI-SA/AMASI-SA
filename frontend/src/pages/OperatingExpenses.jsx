import { useEffect, useMemo, useState } from "react";
import {
    Users, House, HandHeart, Buildings, Receipt, Plus, Trash, PencilSimple, X,
    ChartBar, Wallet, Bank,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { formatMoney, todayISO } from "../lib/format";
import DateInput from "../components/DateInput";

const SALARY_CATEGORIES = [
    { value: "employee",   label: "رواتب الموظفين",      icon: Users,     hint: "موظفين، إداريين، محاسبين، مسوقين، عاملين" },
    { value: "household",  label: "مصروف البيت الشهري",  icon: House,     hint: "مصروف الأسرة/المنزل/الشخصي الثابت" },
    { value: "charity",    label: "الصدقات والمساهمات",  icon: HandHeart, hint: "صدقات دورية، تبرعات، كفالات، مساهمات خيرية" },
];

const RENTAL_TYPES = [
    { value: "office",            label: "مكتب" },
    { value: "warehouse",         label: "مستودع" },
    { value: "shop",              label: "محل" },
    { value: "employee_housing",  label: "سكن موظفين" },
    { value: "other",             label: "أخرى" },
];

const SALARY_CATEGORY_LABEL = Object.fromEntries(SALARY_CATEGORIES.map((c) => [c.value, c.label]));
const RENTAL_TYPE_LABEL     = Object.fromEntries(RENTAL_TYPES.map((c) => [c.value, c.label]));

const TAB_BUTTONS = [
    { id: "salaries",  label: "الرواتب الشهرية",       icon: Users },
    { id: "rentals",   label: "الإيجارات السنوية",     icon: Buildings },
    { id: "daily",     label: "المصروفات اليومية الأخرى", icon: Receipt },
    { id: "report",    label: "التقارير والاحتساب",   icon: ChartBar },
];

export default function OperatingExpenses() {
    const [tab, setTab] = useState("salaries");
    const [summary, setSummary] = useState(null);
    const [salaries, setSalaries] = useState([]);
    const [rentals, setRentals] = useState([]);
    const [dailyItems, setDailyItems] = useState([]);
    const [report, setReport] = useState(null);
    const [loading, setLoading] = useState(true);
    const [modal, setModal] = useState(null);
    // modal shape:
    //   { kind: "salary"|"rental"|"daily", mode: "create"|"edit", row: {...} }

    const refresh = async () => {
        setLoading(true);
        try {
            const [sumRes, salRes, rentRes, dailyRes, reportRes] = await Promise.all([
                api.get("/operating-expenses/summary"),
                api.get("/operating-expenses/salaries"),
                api.get("/operating-expenses/rentals"),
                api.get("/operating-expenses/daily"),
                api.get("/operating-expenses/report"),
            ]);
            setSummary(sumRes.data);
            setSalaries(salRes.data.items || []);
            setRentals(rentRes.data.items || []);
            setDailyItems(dailyRes.data.items || []);
            setReport(reportRes.data);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setLoading(false); }
    };

    useEffect(() => { refresh(); }, []);

    return (
        <div className="space-y-8 animate-fade-in-up" data-testid="operating-expenses-page">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
                <div>
                    <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>
                        المصروفات التشغيلية اليومية
                    </h1>
                    <p className="text-muted-foreground mt-2 text-base">
                        المصدر الرسمي للمصروفات الثابتة والمتغيرة — تدخل تلقائياً في حسابات صافي الربح اليومي/الشهري/السنوي.
                    </p>
                </div>
            </div>

            <SummaryCards summary={summary} />

            <div className="flex flex-wrap gap-2 border-b border-border" data-testid="oe-tabs">
                {TAB_BUTTONS.map(({ id, label, icon: Icon }) => (
                    <button
                        key={id}
                        type="button"
                        onClick={() => setTab(id)}
                        data-testid={`oe-tab-${id}`}
                        className={
                            "inline-flex items-center gap-2 px-4 py-2.5 -mb-px border-b-2 text-sm font-semibold transition-colors "
                            + (tab === id
                                ? "border-brand text-brand"
                                : "border-transparent text-muted-foreground hover:text-foreground")
                        }
                    >
                        <Icon size={18} weight={tab === id ? "fill" : "regular"} />
                        {label}
                    </button>
                ))}
            </div>

            {loading && (
                <div className="text-center py-8 text-muted-foreground">جاري التحميل…</div>
            )}

            {!loading && tab === "salaries" && (
                <SalariesPanel
                    items={salaries}
                    onAdd={() => setModal({ kind: "salary", mode: "create", row: {} })}
                    onEdit={(r) => setModal({ kind: "salary", mode: "edit", row: r })}
                    onDelete={async (r) => {
                        if (!window.confirm(`حذف راتب "${r.name}"؟`)) return;
                        try {
                            await api.delete(`/operating-expenses/salaries/${r.id}`);
                            toast.success("تم الحذف");
                            await refresh();
                        } catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
                    }}
                />
            )}

            {!loading && tab === "rentals" && (
                <RentalsPanel
                    items={rentals}
                    onAdd={() => setModal({ kind: "rental", mode: "create", row: {} })}
                    onEdit={(r) => setModal({ kind: "rental", mode: "edit", row: r })}
                    onDelete={async (r) => {
                        if (!window.confirm(`حذف إيجار "${r.property_name}"؟`)) return;
                        try {
                            await api.delete(`/operating-expenses/rentals/${r.id}`);
                            toast.success("تم الحذف");
                            await refresh();
                        } catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
                    }}
                />
            )}

            {!loading && tab === "daily" && (
                <DailyPanel
                    items={dailyItems}
                    onAdd={() => setModal({ kind: "daily", mode: "create", row: { date: todayISO() } })}
                    onEdit={(r) => setModal({ kind: "daily", mode: "edit", row: r })}
                    onDelete={async (r) => {
                        if (!window.confirm("حذف هذا المصروف؟")) return;
                        try {
                            await api.delete(`/operating-expenses/daily/${r.id}`);
                            toast.success("تم الحذف");
                            await refresh();
                        } catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
                    }}
                />
            )}

            {!loading && tab === "report" && (
                <ReportPanel report={report} />
            )}

            {modal && (
                <Modal
                    state={modal}
                    onClose={() => setModal(null)}
                    onSaved={async () => { setModal(null); await refresh(); }}
                />
            )}
        </div>
    );
}

// ── Summary cards ───────────────────────────────────────────────────────────
function SummaryCards({ summary }) {
    if (!summary) {
        return (
            <div className="rounded-xl border border-border bg-white p-6" data-testid="oe-summary-skel">
                <div className="text-muted-foreground text-sm">جاري حساب الملخص…</div>
            </div>
        );
    }
    const today = summary.today || {};
    const salaries = summary.salaries || {};
    const rentals = summary.rentals || {};

    const cards = [
        // Salaries
        { id: "sal-emp",   group: "الرواتب",     label: "رواتب الموظفين (شهري)",         icon: Users,     value: salaries.employee_monthly,  testid: "oe-card-sal-employee" },
        { id: "sal-home",  group: "الرواتب",     label: "مصروف البيت (شهري)",            icon: House,     value: salaries.household_monthly, testid: "oe-card-sal-household" },
        { id: "sal-char",  group: "الرواتب",     label: "الصدقات (شهري)",                icon: HandHeart, value: salaries.charity_monthly,   testid: "oe-card-sal-charity" },
        // Rentals
        { id: "rent-ann",  group: "الإيجارات",   label: "إجمالي الإيجارات (سنوي)",       icon: Buildings, value: rentals.annual_total,       testid: "oe-card-rent-annual" },
        { id: "rent-day",  group: "الإيجارات",   label: "إيجارات اليوم",                 icon: Bank,      value: rentals.daily_total,        testid: "oe-card-rent-daily" },
        // Daily
        { id: "day-other", group: "المصروفات",   label: "مصروفات أخرى (اليوم)",          icon: Receipt,   value: today.daily_other_total,    testid: "oe-card-day-other" },
        { id: "day-tot",   group: "المصروفات",   label: "إجمالي المصروفات التشغيلية اليوم", icon: Wallet, value: today.operating_total, accent: true, testid: "oe-card-operating-total" },
    ];

    return (
        <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-3" data-testid="oe-summary">
            {cards.map((c) => (
                <div
                    key={c.id}
                    data-testid={c.testid}
                    className={
                        "rounded-xl border p-4 bg-white transition-all "
                        + (c.accent ? "border-brand/40 shadow-sm" : "border-border")
                    }
                >
                    <div className="flex items-center justify-between gap-3 mb-2">
                        <div className="text-xs font-semibold text-muted-foreground">{c.group}</div>
                        <c.icon size={20} weight="duotone" className={c.accent ? "text-brand" : "text-muted-foreground"} />
                    </div>
                    <div className="text-sm font-semibold mb-1">{c.label}</div>
                    <div className={"text-xl font-extrabold num " + (c.accent ? "text-brand" : "")}>
                        {formatMoney(c.value || 0)} <span className="text-xs font-normal text-muted-foreground">ر.س</span>
                    </div>
                </div>
            ))}
        </div>
    );
}

// ── Salaries panel ──────────────────────────────────────────────────────────
function SalariesPanel({ items, onAdd, onEdit, onDelete }) {
    return (
        <Section
            title="الرواتب الشهرية"
            description="الموظفين، مصروف البيت، الصدقات — يتم توزيع كل راتب يومياً تلقائياً."
            onAdd={onAdd}
            addTestId="oe-add-salary-btn"
        >
            {items.length === 0 ? (
                <EmptyState text="لا توجد رواتب مسجلة بعد." />
            ) : (
                <TableWrap testid="oe-salaries-table">
                    <thead>
                        <tr>
                            <Th>الاسم</Th>
                            <Th>نوع الراتب</Th>
                            <Th>المبلغ الشهري</Th>
                            <Th>التكلفة اليومية (تقريبية)</Th>
                            <Th>تاريخ البداية</Th>
                            <Th>الحالة</Th>
                            <Th>ملاحظات</Th>
                            <Th />
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((r) => {
                            const daily = r.status === "active" ? (Number(r.monthly_amount) / 30) : 0;
                            return (
                                <tr key={r.id} className="hover:bg-accent/30 transition-colors" data-testid={`oe-salary-row-${r.id}`}>
                                    <Td className="font-semibold">{r.name}</Td>
                                    <Td>{SALARY_CATEGORY_LABEL[r.category] || r.category}</Td>
                                    <Td className="num">{formatMoney(r.monthly_amount)}</Td>
                                    <Td className="num text-muted-foreground">{formatMoney(daily)}</Td>
                                    <Td>{r.start_date}</Td>
                                    <Td><StatusBadge status={r.status} /></Td>
                                    <Td className="text-muted-foreground">{r.notes || "—"}</Td>
                                    <Td><RowActions onEdit={() => onEdit(r)} onDelete={() => onDelete(r)} /></Td>
                                </tr>
                            );
                        })}
                    </tbody>
                </TableWrap>
            )}
        </Section>
    );
}

// ── Rentals panel ───────────────────────────────────────────────────────────
function RentalsPanel({ items, onAdd, onEdit, onDelete }) {
    return (
        <Section
            title="الإيجارات السنوية"
            description="العقارات والمواقع المستأجرة — التكلفة اليومية = الإيجار السنوي ÷ 365."
            onAdd={onAdd}
            addTestId="oe-add-rental-btn"
        >
            {items.length === 0 ? (
                <EmptyState text="لا توجد إيجارات مسجلة بعد." />
            ) : (
                <TableWrap testid="oe-rentals-table">
                    <thead>
                        <tr>
                            <Th>اسم العقار</Th>
                            <Th>النوع</Th>
                            <Th>الإيجار السنوي</Th>
                            <Th>الإيجار اليومي</Th>
                            <Th>بداية العقد</Th>
                            <Th>نهاية العقد</Th>
                            <Th>الحالة</Th>
                            <Th>ملاحظات</Th>
                            <Th />
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((r) => {
                            const daily = r.status === "active" ? (Number(r.annual_amount) / 365) : 0;
                            return (
                                <tr key={r.id} className="hover:bg-accent/30 transition-colors" data-testid={`oe-rental-row-${r.id}`}>
                                    <Td className="font-semibold">{r.property_name}</Td>
                                    <Td>{RENTAL_TYPE_LABEL[r.property_type] || r.property_type}</Td>
                                    <Td className="num">{formatMoney(r.annual_amount)}</Td>
                                    <Td className="num text-muted-foreground">{formatMoney(daily)}</Td>
                                    <Td>{r.start_date}</Td>
                                    <Td>{r.end_date}</Td>
                                    <Td><StatusBadge status={r.status} /></Td>
                                    <Td className="text-muted-foreground">{r.notes || "—"}</Td>
                                    <Td><RowActions onEdit={() => onEdit(r)} onDelete={() => onDelete(r)} /></Td>
                                </tr>
                            );
                        })}
                    </tbody>
                </TableWrap>
            )}
        </Section>
    );
}

// ── Daily expenses panel ────────────────────────────────────────────────────
function DailyPanel({ items, onAdd, onEdit, onDelete }) {
    return (
        <Section
            title="المصروفات اليومية الأخرى"
            description="الصيانة، الوقود، المواصلات، الأدوات المكتبية، الضيافة، اشتراكات البرامج… وغيرها."
            onAdd={onAdd}
            addTestId="oe-add-daily-btn"
        >
            {items.length === 0 ? (
                <EmptyState text="لا توجد مصروفات يومية بعد." />
            ) : (
                <TableWrap testid="oe-daily-table">
                    <thead>
                        <tr>
                            <Th>التاريخ</Th>
                            <Th>النوع</Th>
                            <Th>الوصف</Th>
                            <Th>المبلغ</Th>
                            <Th>طريقة الدفع</Th>
                            <Th>ملاحظات</Th>
                            <Th />
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((r) => (
                            <tr key={r.id} className="hover:bg-accent/30 transition-colors" data-testid={`oe-daily-row-${r.id}`}>
                                <Td className="num font-semibold">{r.date}</Td>
                                <Td>{r.expense_type}</Td>
                                <Td className="text-muted-foreground">{r.description || "—"}</Td>
                                <Td className="num">{formatMoney(r.amount)}</Td>
                                <Td>{r.payment_method || "—"}</Td>
                                <Td className="text-muted-foreground">{r.notes || "—"}</Td>
                                <Td><RowActions onEdit={() => onEdit(r)} onDelete={() => onDelete(r)} /></Td>
                            </tr>
                        ))}
                    </tbody>
                </TableWrap>
            )}
        </Section>
    );
}

// ── Report panel ────────────────────────────────────────────────────────────
function ReportPanel({ report }) {
    const blocks = useMemo(() => {
        if (!report) return [];
        const mk = (title, key, dataKey) => {
            const d = report[dataKey] || {};
            return {
                title,
                rows: [
                    { label: "رواتب الموظفين",     value: d.salaries_employee },
                    { label: "مصروف البيت",        value: d.salaries_household },
                    { label: "الصدقات والمساهمات", value: d.salaries_charity },
                    { label: "إجمالي الرواتب",     value: d.salaries_total ?? d.salaries_total_daily, bold: true },
                    { label: "الإيجارات",          value: d.rentals_total ?? d.rentals_daily },
                    { label: "المصروفات الأخرى",   value: d.daily_other_total },
                    { label: "الإجمالي التشغيلي",  value: d.operating_total, accent: true },
                ],
                from: d.from_date || d.date,
                to: d.to_date || d.date,
            };
        };
        return [
            mk("المصروفات اليومية (اليوم)", "daily", "daily"),
            mk("المصروفات الشهرية (الشهر الحالي)", "monthly", "monthly"),
            mk("المصروفات السنوية (السنة الحالية)", "yearly", "yearly"),
        ];
    }, [report]);

    return (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4" data-testid="oe-report-grid">
            {blocks.map((b) => (
                <div key={b.title} className="rounded-xl border border-border bg-white p-5">
                    <div className="flex items-center justify-between mb-3">
                        <h3 className="text-lg font-bold" style={{ fontFamily: "Tajawal" }}>{b.title}</h3>
                        <div className="text-xs text-muted-foreground num" dir="ltr">{b.from} → {b.to}</div>
                    </div>
                    <table className="w-full text-sm" data-testid={`oe-report-${b.title}`}>
                        <tbody>
                            {b.rows.map((r) => (
                                <tr key={r.label} className="border-t border-border first:border-t-0">
                                    <td className={"py-2 " + (r.bold ? "font-bold" : "")}>{r.label}</td>
                                    <td className={"py-2 text-left num " + (r.accent ? "text-brand font-extrabold" : (r.bold ? "font-bold" : ""))}>
                                        {formatMoney(r.value || 0)}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ))}
        </div>
    );
}

// ── Modal (add/edit) ────────────────────────────────────────────────────────
function Modal({ state, onClose, onSaved }) {
    const isEdit = state.mode === "edit";
    const row = state.row || {};
    const [saving, setSaving] = useState(false);

    const [form, setForm] = useState(() => {
        if (state.kind === "salary") {
            return {
                name: row.name || "",
                category: row.category || "employee",
                monthly_amount: row.monthly_amount ?? "",
                start_date: row.start_date || todayISO(),
                status: row.status || "active",
                notes: row.notes || "",
            };
        }
        if (state.kind === "rental") {
            return {
                property_name: row.property_name || "",
                property_type: row.property_type || "office",
                annual_amount: row.annual_amount ?? "",
                start_date: row.start_date || todayISO(),
                end_date: row.end_date || todayISO(),
                status: row.status || "active",
                notes: row.notes || "",
            };
        }
        return {
            date: row.date || todayISO(),
            expense_type: row.expense_type || "",
            description: row.description || "",
            amount: row.amount ?? "",
            payment_method: row.payment_method || "",
            notes: row.notes || "",
        };
    });

    const submit = async (e) => {
        e?.preventDefault?.();
        setSaving(true);
        try {
            const body = { ...form };
            // Coerce amount fields
            if (state.kind === "salary")    body.monthly_amount = Number(body.monthly_amount);
            if (state.kind === "rental")    body.annual_amount  = Number(body.annual_amount);
            if (state.kind === "daily")     body.amount         = Number(body.amount);

            const base = state.kind === "salary"
                ? "/operating-expenses/salaries"
                : state.kind === "rental"
                    ? "/operating-expenses/rentals"
                    : "/operating-expenses/daily";

            if (isEdit) {
                await api.put(`${base}/${row.id}`, body);
                toast.success("تم التحديث");
            } else {
                await api.post(base, body);
                toast.success("تم الحفظ");
            }
            await onSaved();
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally {
            setSaving(false);
        }
    };

    const title = state.kind === "salary"
        ? (isEdit ? "تعديل سجل راتب" : "إضافة راتب جديد")
        : state.kind === "rental"
            ? (isEdit ? "تعديل سجل إيجار" : "إضافة إيجار جديد")
            : (isEdit ? "تعديل مصروف يومي" : "إضافة مصروف يومي");

    return (
        <div
            className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4"
            onClick={onClose}
            data-testid="oe-modal-overlay"
        >
            <form
                onSubmit={submit}
                onClick={(e) => e.stopPropagation()}
                className="bg-white rounded-xl border border-border w-full max-w-2xl p-6"
                data-testid={`oe-modal-${state.kind}`}
            >
                <div className="flex items-center justify-between mb-5">
                    <h3 className="text-xl font-bold" style={{ fontFamily: "Tajawal" }}>{title}</h3>
                    <button type="button" onClick={onClose} className="p-1.5 rounded-lg hover:bg-accent" data-testid="oe-modal-close">
                        <X size={18} />
                    </button>
                </div>

                {state.kind === "salary" && (
                    <SalaryFormFields form={form} setForm={setForm} />
                )}
                {state.kind === "rental" && (
                    <RentalFormFields form={form} setForm={setForm} />
                )}
                {state.kind === "daily" && (
                    <DailyFormFields form={form} setForm={setForm} />
                )}

                <div className="mt-6 flex justify-end gap-2">
                    <button type="button" onClick={onClose} className="px-4 py-2.5 rounded-lg border border-border text-sm font-semibold hover:bg-accent">إلغاء</button>
                    <button type="submit" disabled={saving} className="px-5 py-2.5 rounded-lg bg-brand text-white font-bold bg-brand-hover disabled:opacity-60" data-testid="oe-modal-submit">
                        {saving ? "جاري الحفظ…" : (isEdit ? "حفظ التعديل" : "حفظ")}
                    </button>
                </div>
            </form>
        </div>
    );
}

function SalaryFormFields({ form, setForm }) {
    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="الاسم">
                <input type="text" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className="oe-input" data-testid="oe-salary-name" />
            </Field>
            <Field label="نوع الراتب">
                <select required value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })} className="oe-input" data-testid="oe-salary-category">
                    {SALARY_CATEGORIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
                <div className="text-xs text-muted-foreground mt-1">
                    {SALARY_CATEGORIES.find((c) => c.value === form.category)?.hint}
                </div>
            </Field>
            <Field label="المبلغ الشهري (ر.س)">
                <input type="number" required min="0" step="0.01" value={form.monthly_amount} onChange={(e) => setForm({ ...form, monthly_amount: e.target.value })} className="oe-input num" data-testid="oe-salary-amount" />
            </Field>
            <Field label="تاريخ البداية">
                <DateInput value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} data-testid="oe-salary-start" />
            </Field>
            <Field label="الحالة">
                <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="oe-input" data-testid="oe-salary-status">
                    <option value="active">نشط</option>
                    <option value="stopped">متوقف</option>
                </select>
            </Field>
            <Field label="ملاحظات" full>
                <input type="text" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="oe-input" data-testid="oe-salary-notes" />
            </Field>
        </div>
    );
}

function RentalFormFields({ form, setForm }) {
    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="اسم العقار">
                <input type="text" required value={form.property_name} onChange={(e) => setForm({ ...form, property_name: e.target.value })} className="oe-input" data-testid="oe-rental-name" />
            </Field>
            <Field label="نوع العقار">
                <select required value={form.property_type} onChange={(e) => setForm({ ...form, property_type: e.target.value })} className="oe-input" data-testid="oe-rental-type">
                    {RENTAL_TYPES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
            </Field>
            <Field label="قيمة الإيجار السنوي (ر.س)">
                <input type="number" required min="0" step="0.01" value={form.annual_amount} onChange={(e) => setForm({ ...form, annual_amount: e.target.value })} className="oe-input num" data-testid="oe-rental-amount" />
            </Field>
            <Field label="تاريخ بداية العقد">
                <DateInput value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} data-testid="oe-rental-start" />
            </Field>
            <Field label="تاريخ نهاية العقد">
                <DateInput value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} data-testid="oe-rental-end" />
            </Field>
            <Field label="حالة العقد">
                <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="oe-input" data-testid="oe-rental-status">
                    <option value="active">نشط</option>
                    <option value="expired">منتهي</option>
                </select>
            </Field>
            <Field label="ملاحظات" full>
                <input type="text" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="oe-input" data-testid="oe-rental-notes" />
            </Field>
        </div>
    );
}

function DailyFormFields({ form, setForm }) {
    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="التاريخ">
                <DateInput value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} data-testid="oe-daily-date" />
            </Field>
            <Field label="نوع المصروف">
                <input type="text" required placeholder="صيانة، وقود، اشتراكات…" value={form.expense_type} onChange={(e) => setForm({ ...form, expense_type: e.target.value })} className="oe-input" data-testid="oe-daily-type" />
            </Field>
            <Field label="الوصف" full>
                <input type="text" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="oe-input" data-testid="oe-daily-description" />
            </Field>
            <Field label="المبلغ (ر.س)">
                <input type="number" required min="0" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="oe-input num" data-testid="oe-daily-amount" />
            </Field>
            <Field label="طريقة الدفع">
                <input type="text" placeholder="نقدي، بطاقة، تحويل…" value={form.payment_method} onChange={(e) => setForm({ ...form, payment_method: e.target.value })} className="oe-input" data-testid="oe-daily-payment-method" />
            </Field>
            <Field label="ملاحظات" full>
                <input type="text" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="oe-input" data-testid="oe-daily-notes" />
            </Field>
        </div>
    );
}

// ── Reusable bits ───────────────────────────────────────────────────────────
function Section({ title, description, onAdd, addTestId, children }) {
    return (
        <div className="rounded-xl border border-border bg-white p-6">
            <div className="flex items-start justify-between gap-4 mb-5">
                <div>
                    <h2 className="text-xl font-bold" style={{ fontFamily: "Tajawal" }}>{title}</h2>
                    {description && <p className="text-sm text-muted-foreground mt-1">{description}</p>}
                </div>
                <button
                    type="button"
                    onClick={onAdd}
                    data-testid={addTestId}
                    className="inline-flex items-center gap-2 px-4 py-2.5 bg-brand text-white font-bold rounded-lg bg-brand-hover transition-colors shrink-0"
                >
                    <Plus size={18} weight="bold" />
                    إضافة جديد
                </button>
            </div>
            {children}
        </div>
    );
}

function TableWrap({ children, testid }) {
    return (
        <div className="overflow-x-auto">
            <table
                data-testid={testid}
                className="w-full text-right text-sm border-collapse
                    [&_th]:px-3 [&_th]:py-3 [&_th]:border [&_th]:border-border [&_th]:whitespace-nowrap [&_th]:font-semibold [&_th]:text-muted-foreground [&_th]:bg-accent/60
                    [&_td]:px-3 [&_td]:py-3 [&_td]:border [&_td]:border-border [&_td]:whitespace-nowrap"
            >
                {children}
            </table>
        </div>
    );
}

const Th = ({ children }) => <th className="text-center">{children}</th>;
const Td = ({ children, className = "" }) => <td className={"text-center " + className}>{children}</td>;

function EmptyState({ text }) {
    return <div className="text-center py-10 text-muted-foreground" data-testid="oe-empty">{text}</div>;
}

function StatusBadge({ status }) {
    const isActive = status === "active";
    const isExpired = status === "expired";
    const cls = isActive
        ? "bg-emerald-50 text-emerald-700 border-emerald-200"
        : isExpired
            ? "bg-amber-50 text-amber-700 border-amber-200"
            : "bg-rose-50 text-rose-700 border-rose-200";
    const label = isActive ? "نشط" : isExpired ? "منتهي" : "متوقف";
    return (
        <span className={`inline-block px-2.5 py-1 rounded-full text-xs font-bold border ${cls}`} data-testid="oe-status-badge">
            {label}
        </span>
    );
}

function RowActions({ onEdit, onDelete }) {
    return (
        <div className="inline-flex items-center gap-1">
            <button type="button" onClick={onEdit} title="تعديل" className="p-2 rounded-lg border border-border hover:bg-brand hover:text-white hover:border-brand transition-colors" data-testid="oe-edit-btn">
                <PencilSimple size={16} />
            </button>
            <button type="button" onClick={onDelete} title="حذف" className="p-2 rounded-lg border border-border hover:bg-red-50 hover:text-red-600 transition-colors" data-testid="oe-delete-btn">
                <Trash size={16} />
            </button>
        </div>
    );
}

function Field({ label, children, full }) {
    return (
        <div className={full ? "sm:col-span-2" : ""}>
            <label className="block text-sm font-semibold mb-1.5">{label}</label>
            {children}
        </div>
    );
}
