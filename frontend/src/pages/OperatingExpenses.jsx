import { useEffect, useMemo, useState } from "react";
import {
    Users, House, HandHeart, Buildings, Receipt, Plus, Trash, PencilSimple, X,
    ChartBar, Wallet, Bank, ShieldCheck,
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

const SALARY_COUNTRIES = [
    { value: "saudi", label: "السعودية", flag: "🇸🇦" },
    { value: "yemen", label: "اليمن",     flag: "🇾🇪" },
    { value: "other", label: "أخرى",     flag: "🌍" },
];

const RENTAL_TYPES = [
    { value: "office",            label: "مكتب" },
    { value: "warehouse",         label: "مستودع" },
    { value: "shop",              label: "محل" },
    { value: "employee_housing",  label: "سكن موظفين" },
    { value: "other",             label: "أخرى" },
];

const PREPAID_TYPES = [
    { value: "vehicle_insurance",   label: "تأمين السيارات",        icon: "🚗" },
    { value: "worker_insurance",    label: "تأمين الموظفين",        icon: "👷" },
    { value: "iqama_visa",          label: "الإقامات والتأشيرات",   icon: "🪪" },
    { value: "government_license",  label: "الرخص والتصاريح",       icon: "📜" },
    { value: "annual_subscription", label: "الاشتراكات السنوية",    icon: "🔁" },
    { value: "other",               label: "أخرى",                  icon: "📦" },
];

const SALARY_CATEGORY_LABEL = Object.fromEntries(SALARY_CATEGORIES.map((c) => [c.value, c.label]));
const SALARY_COUNTRY        = Object.fromEntries(SALARY_COUNTRIES.map((c) => [c.value, c]));
const RENTAL_TYPE_LABEL     = Object.fromEntries(RENTAL_TYPES.map((c) => [c.value, c.label]));
const PREPAID_TYPE          = Object.fromEntries(PREPAID_TYPES.map((c) => [c.value, c]));

const TAB_BUTTONS = [
    { id: "salaries",  label: "الرواتب الشهرية",          icon: Users },
    { id: "rentals",   label: "الإيجارات السنوية",        icon: Buildings },
    { id: "prepaid",   label: "المصروفات المدفوعة مقدماً", icon: ShieldCheck },
    { id: "daily",     label: "المصروفات اليومية الأخرى", icon: Receipt },
    { id: "report",    label: "التقارير والاحتساب",       icon: ChartBar },
];

export default function OperatingExpenses() {
    const [tab, setTab] = useState("salaries");
    const [summary, setSummary] = useState(null);
    const [salaries, setSalaries] = useState([]);
    const [rentals, setRentals] = useState([]);
    const [prepaids, setPrepaids] = useState([]);
    const [dailyItems, setDailyItems] = useState([]);
    const [accounts, setAccounts] = useState([]);   // Iter-94: bank/cash accounts
    const [report, setReport] = useState(null);
    const [loading, setLoading] = useState(true);
    const [modal, setModal] = useState(null);
    // modal shape:
    //   { kind: "salary"|"rental"|"prepaid"|"daily", mode: "create"|"edit", row: {...} }

    const refresh = async () => {
        setLoading(true);
        try {
            const [sumRes, salRes, rentRes, prepRes, dailyRes, reportRes, accRes] = await Promise.all([
                api.get("/operating-expenses/summary"),
                api.get("/operating-expenses/salaries"),
                api.get("/operating-expenses/rentals"),
                api.get("/operating-expenses/prepaid"),
                api.get("/operating-expenses/daily"),
                api.get("/operating-expenses/report"),
                api.get("/accounts"),
            ]);
            setSummary(sumRes.data);
            setSalaries(salRes.data.items || []);
            setRentals(rentRes.data.items || []);
            setPrepaids(prepRes.data.items || []);
            setDailyItems(dailyRes.data.items || []);
            setReport(reportRes.data);
            // Iter-94: keep only bank accounts (the user pays daily expenses
            // from a real cash/bank account, not from a payment platform).
            const raw = accRes.data?.accounts || accRes.data?.items || (Array.isArray(accRes.data) ? accRes.data : []);
            setAccounts(raw.filter((a) => a.account_type === "bank" && a.status !== "hidden" && a.status !== "inactive"));
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setLoading(false); }
    };

    useEffect(() => { refresh(); }, []);

    return (
        <div className="space-y-8 animate-fade-in-up" data-testid="operating-expenses-page">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
                <div>
                    <h1 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>
                        المصروفات التشغيلية اليومية
                    </h1>
                    <p className="text-muted-foreground mt-2 text-sm sm:text-base">
                        المصدر الرسمي للمصروفات الثابتة والمتغيرة — تدخل تلقائياً في حسابات صافي الربح اليومي/الشهري/السنوي.
                    </p>
                </div>
            </div>

            <SummaryCards summary={summary} />

            <div className="flex gap-2 border-b border-border overflow-x-auto scrollbar-thin -mx-4 sm:mx-0 px-4 sm:px-0" data-testid="oe-tabs">
                {TAB_BUTTONS.map(({ id, label, icon: Icon }) => (
                    <button
                        key={id}
                        type="button"
                        onClick={() => setTab(id)}
                        data-testid={`oe-tab-${id}`}
                        className={
                            "inline-flex items-center gap-2 px-4 py-2.5 -mb-px border-b-2 text-sm font-semibold transition-colors whitespace-nowrap flex-shrink-0 "
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

            {!loading && tab === "prepaid" && (
                <PrepaidPanel
                    items={prepaids}
                    onAdd={() => setModal({ kind: "prepaid", mode: "create", row: { start_date: todayISO() } })}
                    onEdit={(r) => setModal({ kind: "prepaid", mode: "edit", row: r })}
                    onDelete={async (r) => {
                        if (!window.confirm(`حذف هذا السجل (${PREPAID_TYPE[r.expense_type]?.label || r.expense_type})؟`)) return;
                        try {
                            await api.delete(`/operating-expenses/prepaid/${r.id}`);
                            toast.success("تم الحذف");
                            await refresh();
                        } catch (e) { toast.error(formatApiErrorDetail(e.response?.data?.detail)); }
                    }}
                />
            )}

            {!loading && tab === "daily" && (
                <DailyPanel
                    items={dailyItems}
                    accounts={accounts}
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
                    accounts={accounts}
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
    const prepaid = summary.prepaid || {};
    const byCountry = salaries.by_country || {};
    const saMonthly = byCountry.saudi?.monthly_total || 0;
    const yeMonthly = byCountry.yemen?.monthly_total || 0;

    const cards = [
        // Salaries by category
        { id: "sal-emp",   group: "الرواتب",     label: "رواتب الموظفين (شهري)",         icon: Users,     value: salaries.employee_monthly,  testid: "oe-card-sal-employee" },
        { id: "sal-home",  group: "الرواتب",     label: "مصروف البيت (شهري)",            icon: House,     value: salaries.household_monthly, testid: "oe-card-sal-household" },
        { id: "sal-char",  group: "الرواتب",     label: "الصدقات (شهري)",                icon: HandHeart, value: salaries.charity_monthly,   testid: "oe-card-sal-charity" },
        // Salaries by country
        { id: "sal-sa",    group: "حسب الدولة",  label: "رواتب السعودية 🇸🇦 (شهري)",   icon: Users,     value: saMonthly, testid: "oe-card-sal-saudi" },
        { id: "sal-ye",    group: "حسب الدولة",  label: "رواتب اليمن 🇾🇪 (شهري)",       icon: Users,     value: yeMonthly, testid: "oe-card-sal-yemen" },
        // Rentals
        { id: "rent-ann",  group: "الإيجارات",   label: "إجمالي الإيجارات (سنوي)",       icon: Buildings, value: rentals.annual_total,       testid: "oe-card-rent-annual" },
        { id: "rent-day",  group: "الإيجارات",   label: "إيجارات اليوم",                 icon: Bank,      value: rentals.daily_total,        testid: "oe-card-rent-daily" },
        // Prepaid
        { id: "prep-tot",  group: "المدفوعة مقدماً", label: "إجمالي المبالغ المدفوعة",   icon: ShieldCheck, value: prepaid.total_paid,        testid: "oe-card-prepaid-paid" },
        { id: "prep-day",  group: "المدفوعة مقدماً", label: "المدفوعة مقدماً اليومية",  icon: ShieldCheck, value: prepaid.daily_total,       testid: "oe-card-prepaid-daily" },
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
                            <Th>الدولة</Th>
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
                            const country = SALARY_COUNTRY[r.country || "saudi"] || SALARY_COUNTRY.saudi;
                            return (
                                <tr key={r.id} className="hover:bg-accent/30 transition-colors" data-testid={`oe-salary-row-${r.id}`}>
                                    <Td className="font-semibold">{r.name}</Td>
                                    <Td>{SALARY_CATEGORY_LABEL[r.category] || r.category}</Td>
                                    <Td>
                                        <span className="inline-flex items-center gap-1.5">
                                            <span aria-hidden="true">{country.flag}</span>
                                            <span>{country.label}</span>
                                        </span>
                                    </Td>
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

// ── Prepaid expenses panel ──────────────────────────────────────────────────
function PrepaidPanel({ items, onAdd, onEdit, onDelete }) {
    return (
        <Section
            title="المصروفات المدفوعة مقدماً"
            description="تأمين السيارات / تأمين الموظفين / الإقامات والتأشيرات / الرخص والتصاريح / الاشتراكات السنوية. التكلفة اليومية = المبلغ ÷ عدد أيام الفترة."
            onAdd={onAdd}
            addTestId="oe-add-prepaid-btn"
        >
            {items.length === 0 ? (
                <EmptyState text="لا توجد مصروفات مدفوعة مقدماً بعد." />
            ) : (
                <TableWrap testid="oe-prepaid-table">
                    <thead>
                        <tr>
                            <Th>النوع</Th>
                            <Th>المستفيد / الأصل</Th>
                            <Th>المبلغ المدفوع</Th>
                            <Th>عدد الأيام</Th>
                            <Th>التكلفة اليومية</Th>
                            <Th>بداية الفترة</Th>
                            <Th>نهاية الفترة</Th>
                            <Th>الحالة</Th>
                            <Th>ملاحظات</Th>
                            <Th />
                        </tr>
                    </thead>
                    <tbody>
                        {items.map((r) => {
                            const t = PREPAID_TYPE[r.expense_type] || { label: r.expense_type, icon: "📦" };
                            return (
                                <tr key={r.id} className="hover:bg-accent/30 transition-colors" data-testid={`oe-prepaid-row-${r.id}`}>
                                    <Td>
                                        <span className="inline-flex items-center gap-1.5">
                                            <span aria-hidden="true">{t.icon}</span>
                                            <span>{t.label}</span>
                                        </span>
                                    </Td>
                                    <Td className="font-semibold">{r.beneficiary}</Td>
                                    <Td className="num">{formatMoney(r.amount)}</Td>
                                    <Td className="num">{r.period_days}</Td>
                                    <Td className="num text-brand font-bold">{formatMoney(r.daily_cost)}</Td>
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
function DailyPanel({ items, accounts = [], onAdd, onEdit, onDelete }) {
    const accountNameById = Object.fromEntries(accounts.map((a) => [a.id, a.name]));
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
                            <Th>الحساب المدفوع منه</Th>
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
                                <Td>
                                    {r.paid_from_account_id ? (
                                        <span className="inline-flex items-center gap-1 text-xs font-bold text-emerald-700">
                                            🏦 {accountNameById[r.paid_from_account_id] || "حساب"}
                                        </span>
                                    ) : <span className="text-muted-foreground">—</span>}
                                </Td>
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
            const prepaidByType = d.prepaid_by_type || {};
            const ptRow = (type) => ({
                label: PREPAID_TYPE[type]?.label || type,
                value: prepaidByType[type] || 0,
                muted: !(prepaidByType[type] > 0),
                indent: true,
            });
            return {
                title,
                rows: [
                    { label: "رواتب الموظفين",     value: d.salaries_employee },
                    { label: "مصروف البيت",        value: d.salaries_household },
                    { label: "الصدقات والمساهمات", value: d.salaries_charity },
                    { label: "إجمالي الرواتب",     value: d.salaries_total ?? d.salaries_total_daily, bold: true },
                    { label: "الإيجارات",          value: d.rentals_total ?? d.rentals_daily },
                    { label: "المصروفات المدفوعة مقدماً", value: d.prepaid_total ?? d.prepaid_daily, bold: true },
                    ptRow("vehicle_insurance"),
                    ptRow("worker_insurance"),
                    ptRow("iqama_visa"),
                    ptRow("government_license"),
                    ptRow("annual_subscription"),
                    ptRow("other"),
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
                    <table className="mezan-table w-full text-sm" data-testid={`oe-report-${b.title}`}>
                        <tbody>
                            {b.rows.map((r) => (
                                <tr key={r.label} className="border-t border-border first:border-t-0">
                                    <td className={
                                        "py-2 "
                                        + (r.bold ? "font-bold " : "")
                                        + (r.indent ? "pr-4 text-muted-foreground text-xs " : "")
                                        + (r.muted && r.indent ? "opacity-60" : "")
                                    }>
                                        {r.indent && <span className="opacity-50 ml-1">└</span>}
                                        {r.label}
                                    </td>
                                    <td className={"py-2 text-left num " + (r.accent ? "text-brand font-extrabold" : (r.bold ? "font-bold" : (r.indent ? "text-xs text-muted-foreground" : "")))}>
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
function Modal({ state, accounts, onClose, onSaved }) {
    const isEdit = state.mode === "edit";
    const row = state.row || {};
    const [saving, setSaving] = useState(false);

    const [form, setForm] = useState(() => {
        if (state.kind === "salary") {
            return {
                name: row.name || "",
                category: row.category || "employee",
                country: row.country || "saudi",
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
        if (state.kind === "prepaid") {
            return {
                expense_type: row.expense_type || "vehicle_insurance",
                beneficiary: row.beneficiary || "",
                amount: row.amount ?? "",
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
            paid_from_account_id: row.paid_from_account_id || "",
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
            if (state.kind === "prepaid")   body.amount         = Number(body.amount);
            if (state.kind === "daily") {
                body.amount = Number(body.amount);
                // Iter-94: empty string → null so the backend treats it as
                // an unlinked cash expense.
                body.paid_from_account_id = body.paid_from_account_id || null;
            }

            const base = state.kind === "salary"
                ? "/operating-expenses/salaries"
                : state.kind === "rental"
                    ? "/operating-expenses/rentals"
                    : state.kind === "prepaid"
                        ? "/operating-expenses/prepaid"
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
            : state.kind === "prepaid"
                ? (isEdit ? "تعديل سجل مصروف مدفوع مقدماً" : "إضافة مصروف مدفوع مقدماً")
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
                {state.kind === "prepaid" && (
                    <PrepaidFormFields form={form} setForm={setForm} />
                )}
                {state.kind === "daily" && (
                    <DailyFormFields form={form} setForm={setForm} accounts={accounts || []} />
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
            <Field label="الدولة">
                <select required value={form.country} onChange={(e) => setForm({ ...form, country: e.target.value })} className="oe-input" data-testid="oe-salary-country">
                    {SALARY_COUNTRIES.map((c) => (
                        <option key={c.value} value={c.value}>{c.flag} {c.label}</option>
                    ))}
                </select>
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

function PrepaidFormFields({ form, setForm }) {
    // Compute live preview of period and daily cost
    const periodDays = (() => {
        const s = form.start_date && new Date(form.start_date);
        const e = form.end_date && new Date(form.end_date);
        if (!s || !e || isNaN(s) || isNaN(e)) return 0;
        return Math.max(Math.floor((e - s) / 86400000) + 1, 1);
    })();
    const dailyCost = (() => {
        const amt = Number(form.amount || 0);
        if (!amt || !periodDays) return 0;
        return amt / periodDays;
    })();

    return (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Field label="نوع المصروف">
                <select required value={form.expense_type} onChange={(e) => setForm({ ...form, expense_type: e.target.value })} className="oe-input" data-testid="oe-prepaid-type">
                    {PREPAID_TYPES.map((c) => (
                        <option key={c.value} value={c.value}>{c.icon} {c.label}</option>
                    ))}
                </select>
            </Field>
            <Field label="المستفيد / الأصل">
                <input
                    type="text" required value={form.beneficiary}
                    onChange={(e) => setForm({ ...form, beneficiary: e.target.value })}
                    placeholder="اسم العامل، لوحة السيارة، رقم الاشتراك…"
                    className="oe-input" data-testid="oe-prepaid-beneficiary"
                />
            </Field>
            <Field label="المبلغ المدفوع (ر.س)">
                <input type="number" required min="0" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="oe-input num" data-testid="oe-prepaid-amount" />
            </Field>
            <Field label="الحالة">
                <select value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })} className="oe-input" data-testid="oe-prepaid-status">
                    <option value="active">نشط</option>
                    <option value="expired">منتهي</option>
                </select>
            </Field>
            <Field label="تاريخ بداية الفترة">
                <DateInput value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} data-testid="oe-prepaid-start" />
            </Field>
            <Field label="تاريخ نهاية الفترة">
                <DateInput value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} data-testid="oe-prepaid-end" />
            </Field>
            <Field label="ملاحظات" full>
                <input type="text" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="oe-input" data-testid="oe-prepaid-notes" />
            </Field>
            <div className="sm:col-span-2 mt-1 p-3 rounded-lg bg-accent/40 text-xs" data-testid="oe-prepaid-preview">
                <div className="font-bold text-foreground mb-1">معاينة الاحتساب التلقائي:</div>
                <div className="font-mono leading-relaxed" dir="ltr">
                    <span>{formatMoney(Number(form.amount || 0))}</span>
                    <span className="text-muted-foreground"> ÷ </span>
                    <span>{periodDays} يوم</span>
                    <span className="text-muted-foreground"> = </span>
                    <span className="text-brand font-bold">{formatMoney(dailyCost)} ر.س / يوم</span>
                </div>
            </div>
        </div>
    );
}

function DailyFormFields({ form, setForm, accounts = [] }) {
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
            <Field label="الحساب المدفوع منه">
                <select
                    value={form.paid_from_account_id || ""}
                    onChange={(e) => setForm({ ...form, paid_from_account_id: e.target.value })}
                    className="oe-input"
                    data-testid="oe-daily-paid-from-account"
                >
                    <option value="">— نقدي / غير مرتبط بحساب —</option>
                    {accounts.map((a) => (
                        <option key={a.id} value={a.id}>
                            {a.name}
                            {typeof a.current_balance === "number"
                                ? `  (الرصيد: ${a.current_balance.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س)`
                                : ""}
                        </option>
                    ))}
                </select>
                <div className="text-xs text-muted-foreground mt-1">
                    عند اختيار حساب يُخصم المبلغ تلقائياً من رصيده وينعكس على المركز المالي.
                </div>
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
        <div className="rounded-xl border border-border bg-white p-4 sm:p-6">
            <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4 mb-5">
                <div className="min-w-0">
                    <h2 className="text-xl font-bold" style={{ fontFamily: "Tajawal" }}>{title}</h2>
                    {description && <p className="text-sm text-muted-foreground mt-1">{description}</p>}
                </div>
                <button
                    type="button"
                    onClick={onAdd}
                    data-testid={addTestId}
                    className="inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-brand text-white font-bold rounded-lg bg-brand-hover transition-colors shrink-0 w-full sm:w-auto"
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
        <div className="overflow-x-auto -mx-4 sm:mx-0 px-4 sm:px-0">
            <table
                data-testid={testid}
                className="mezan-table w-full text-right text-sm border-collapse min-w-[640px]
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
