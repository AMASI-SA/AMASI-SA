import { useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    Bank,
    CalendarCheck,
    CheckCircle,
    Coins,
    HandCoins,
    User,
    Wallet,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    accrueAccountingPayroll,
    classifyAccountingEmployeeMovement,
    getAccountingPayrollContext,
} from "../../services/accountingModule";
import { formatMoney, LoadingBlock } from "./AccountingShared";

const OUT_ACTIONS = [
    ["salary_payment", "صرف راتب"],
    ["advance_grant", "منح سلفة"],
    ["custody_grant", "تسليم عهدة"],
];
const IN_ACTIONS = [
    ["advance_repayment", "استرداد سلفة"],
    ["custody_return", "إرجاع عهدة"],
];

function nowRiyadh() {
    const now = new Date();
    const date = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Riyadh",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).format(now);
    const month = date.slice(0, 7);
    return { date, month };
}

function errorText(error, fallback) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (detail?.message) return detail.message;
    if (detail?.code) {
        const values = Object.entries(detail)
            .filter(([key]) => !["code", "message"].includes(key))
            .map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join(", ") : value}`)
            .join(" · ");
        return values ? `${detail.code} — ${values}` : detail.code;
    }
    return fallback;
}

function EmployeeCard({ employee }) {
    return (
        <article className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="flex items-center gap-2">
                        <User size={19} weight="duotone" className="text-emerald-800" />
                        <h3 className="truncate text-sm font-black text-slate-950">{employee.name || employee.id}</h3>
                    </div>
                    <p className="mt-1 text-[10px] font-semibold text-slate-400" dir="ltr">{employee.id}</p>
                </div>
                <span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-extrabold text-slate-600">
                    راتب {formatMoney(employee.monthly_amount)}
                </span>
            </div>
            <div className="mt-4 grid grid-cols-3 gap-2">
                <div className="rounded-xl bg-rose-50 p-3">
                    <div className="text-[10px] font-bold text-rose-700">راتب مستحق</div>
                    <div className="mt-1 text-sm font-black text-rose-900">{formatMoney(employee.salary_payable)}</div>
                </div>
                <div className="rounded-xl bg-amber-50 p-3">
                    <div className="text-[10px] font-bold text-amber-700">سلفة عليه</div>
                    <div className="mt-1 text-sm font-black text-amber-900">{formatMoney(employee.advance)}</div>
                </div>
                <div className="rounded-xl bg-sky-50 p-3">
                    <div className="text-[10px] font-bold text-sky-700">عهدة</div>
                    <div className="mt-1 text-sm font-black text-sky-900">{formatMoney(employee.custody)}</div>
                </div>
            </div>
        </article>
    );
}

function MovementClassifier({ movement, employees, onDone, canPost }) {
    const actions = movement.direction === "out" ? OUT_ACTIONS : IN_ACTIONS;
    const [employeeId, setEmployeeId] = useState("");
    const [action, setAction] = useState(actions[0]?.[0] || "");
    const [reason, setReason] = useState("");
    const [applyAdvances, setApplyAdvances] = useState(true);
    const [busy, setBusy] = useState(false);

    async function submit() {
        if (!employeeId) return toast.error("اختر الموظف");
        if (!action) return toast.error("اختر نوع الحركة");
        if (reason.trim().length < 3) return toast.error("اكتب سبب التصنيف");
        setBusy(true);
        try {
            const result = await classifyAccountingEmployeeMovement(movement.id, {
                employee_id: employeeId,
                action,
                apply_open_advances: applyAdvances,
                reason: reason.trim(),
            });
            if (result.state === "already_posted") {
                toast.info("هذه الحركة مصنفة ومرحلة مسبقًا؛ لم يتكرر القيد.");
            } else {
                toast.success("تم ربط الحركة بالموظف وترحيل القيد من دليل البنك نفسه.");
            }
            await onDone();
        } catch (error) {
            toast.error(errorText(error, "تعذر تصنيف حركة الموظف"), { duration: 9000 });
        } finally {
            setBusy(false);
        }
    }

    return (
        <div className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 lg:grid-cols-[1fr_1fr_1fr_1.2fr_auto] lg:items-end" data-testid={"payroll-movement-" + movement.id}>
            <div>
                <div className="text-[10px] font-bold text-slate-500">الحركة البنكية</div>
                <div className={"mt-1 text-sm font-black " + (movement.direction === "out" ? "text-rose-700" : "text-emerald-700")}>
                    {movement.direction === "out" ? "خارج" : "داخل"} · {formatMoney(movement.amount)}
                </div>
                <div className="mt-1 text-[10px] font-semibold text-slate-500">{movement.movement_date} · {movement.reference || movement.description || "بدون مرجع"}</div>
            </div>
            <label className="text-[11px] font-extrabold text-slate-600">
                الموظف
                <select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs">
                    <option value="">اختر الموظف</option>
                    {employees.map((employee) => <option key={employee.id} value={employee.id}>{employee.name}</option>)}
                </select>
            </label>
            <label className="text-[11px] font-extrabold text-slate-600">
                نوع الحركة
                <select value={action} onChange={(event) => setAction(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 bg-white px-2 text-xs">
                    {actions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
            </label>
            <label className="text-[11px] font-extrabold text-slate-600">
                سبب / وصف الاعتماد
                <input value={reason} onChange={(event) => setReason(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 px-2 text-xs" placeholder="مثال: راتب سبتمبر حسب كشف البنك" />
                {action === "salary_payment" && (
                    <span className="mt-2 flex items-start gap-2 text-[10px] font-bold text-slate-500">
                        <input type="checkbox" checked={applyAdvances} onChange={(event) => setApplyAdvances(event.target.checked)} className="mt-0.5" />
                        خصم السلفة المفتوحة من المتبقي المستحق تلقائيًا
                    </span>
                )}
            </label>
            <button type="button" onClick={submit} disabled={!canPost || busy} className="min-h-10 rounded-xl bg-emerald-800 px-4 text-xs font-black text-white disabled:opacity-40">
                {busy ? "جاري الترحيل…" : "اعتماد الحركة"}
            </button>
        </div>
    );
}

export default function AccountingPayroll({ accountingPermissions = [] }) {
    const initial = nowRiyadh();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [period, setPeriod] = useState(initial.month);
    const [date, setDate] = useState(initial.date);
    const [reason, setReason] = useState("");
    const [confirmed, setConfirmed] = useState(false);
    const [busy, setBusy] = useState(false);
    const canPost = accountingPermissions.includes("accounting.payroll.post");

    async function refresh() {
        setLoading(true);
        try {
            setData(await getAccountingPayrollContext());
        } catch (error) {
            toast.error(errorText(error, "تعذر تحميل الرواتب والسلف"));
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => { refresh(); }, []);

    const totals = useMemo(() => {
        const employees = data?.employees || [];
        return employees.reduce((acc, employee) => ({
            salary: acc.salary + Number(employee.salary_payable || 0),
            advance: acc.advance + Number(employee.advance || 0),
            custody: acc.custody + Number(employee.custody || 0),
        }), { salary: 0, advance: 0, custody: 0 });
    }, [data?.employees]);

    async function accrue() {
        if (!confirmed) return;
        setBusy(true);
        try {
            const result = await accrueAccountingPayroll({
                period,
                accrued_at: date + "T00:00:00+03:00",
                reason: reason.trim(),
            });
            if (result.posted === 0 && result.already_posted > 0) {
                toast.info("رواتب هذه الفترة مرحلة مسبقًا؛ لم يتكرر أي قيد.");
            } else {
                toast.success(`تم إثبات استحقاق ${result.posted} موظف تلقائيًا لهذه الفترة.`);
            }
            setConfirmed(false);
            setReason("");
            await refresh();
        } catch (error) {
            toast.error(errorText(error, "تعذر إثبات استحقاق الرواتب"), { duration: 9000 });
        } finally {
            setBusy(false);
        }
    }

    if (loading && !data) return <LoadingBlock label="جاري تحميل الرواتب والسلف…" />;

    return (
        <div className="space-y-5" dir="rtl" data-testid="accounting-payroll">
            <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-5">
                <div className="flex items-start gap-3">
                    <Coins size={28} weight="duotone" className="shrink-0 text-emerald-800" />
                    <div>
                        <h2 className="text-xl font-black text-emerald-950">الرواتب والسلف والعهد — ميزان 2</h2>
                        <p className="mt-2 text-sm font-semibold leading-6 text-emerald-900">
                            استحقاق الراتب ينشئ الالتزام تلقائيًا. أما الصرف الفعلي فلا يُكتب يدويًا من جديد؛ تربطه بحركة البنك التي رُفعت في كشف الحركات اليومية.
                        </p>
                    </div>
                </div>
            </section>

            <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4">
                    <div className="text-xs font-bold text-rose-700">رواتب مستحقة</div>
                    <div className="mt-1 text-2xl font-black text-rose-900">{formatMoney(totals.salary)}</div>
                </div>
                <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4">
                    <div className="text-xs font-bold text-amber-700">سلف الموظفين</div>
                    <div className="mt-1 text-2xl font-black text-amber-900">{formatMoney(totals.advance)}</div>
                </div>
                <div className="rounded-2xl border border-sky-200 bg-sky-50 p-4">
                    <div className="text-xs font-bold text-sky-700">العهد المفتوحة</div>
                    <div className="mt-1 text-2xl font-black text-sky-900">{formatMoney(totals.custody)}</div>
                </div>
            </div>

            <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="payroll-accrual">
                <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                        <div className="flex items-center gap-2"><CalendarCheck size={22} className="text-violet-800" /><h3 className="font-black text-slate-950">إثبات استحقاق الرواتب</h3></div>
                        <p className="mt-1 text-xs font-semibold text-slate-500">يقرأ الراتب الشهري المسجل لكل موظف نشط. إعادة نفس الشهر لا تكرر القيد.</p>
                    </div>
                    <button type="button" onClick={() => refresh()} disabled={loading} className="inline-flex min-h-9 items-center gap-2 rounded-lg border border-slate-200 px-3 text-xs font-extrabold">
                        <ArrowClockwise size={16} /> تحديث
                    </button>
                </div>
                <div className="mt-4 grid gap-3 lg:grid-cols-[1fr_1fr_1.5fr]">
                    <label className="text-[11px] font-extrabold text-slate-600">
                        فترة الراتب
                        <input type="month" value={period} onChange={(event) => setPeriod(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 px-3" />
                    </label>
                    <label className="text-[11px] font-extrabold text-slate-600">
                        التاريخ المحاسبي
                        <input type="date" value={date} onChange={(event) => setDate(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 px-3" />
                    </label>
                    <label className="text-[11px] font-extrabold text-slate-600">
                        ملاحظة
                        <input value={reason} onChange={(event) => setReason(event.target.value)} className="mt-1 min-h-10 w-full rounded-lg border border-slate-200 px-3" placeholder="مثال: استحقاق رواتب سبتمبر" />
                    </label>
                </div>
                <label className="mt-4 flex items-start gap-2 rounded-xl border border-violet-100 bg-violet-50 p-3 text-xs font-extrabold leading-6 text-violet-950">
                    <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} className="mt-1" />
                    راجعت فترة الرواتب، وأوافق على إثبات راتب جميع الموظفين النشطين حسب الراتب الشهري المسجل. لا يتم صرف أي بنك في هذه الخطوة.
                </label>
                <div className="mt-3 flex justify-end">
                    <button type="button" onClick={accrue} disabled={!canPost || !confirmed || busy} className="min-h-11 rounded-xl bg-violet-800 px-5 text-sm font-black text-white disabled:opacity-40">
                        {busy ? "جاري الإثبات…" : "إثبات استحقاق رواتب الشهر"}
                    </button>
                </div>
            </section>

            <section className="space-y-3">
                <div>
                    <h3 className="font-black text-slate-950">أرصدة الموظفين</h3>
                    <p className="mt-1 text-xs font-semibold text-slate-500">هذه الأرقام من قيود MZ2 بعد الرصيد الافتتاحي فقط.</p>
                </div>
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    {(data?.employees || []).map((employee) => <EmployeeCard key={employee.id} employee={employee} />)}
                </div>
            </section>

            <section className="space-y-3 rounded-2xl border border-slate-200 bg-slate-50 p-5" data-testid="payroll-bank-movements">
                <div className="flex items-start gap-3">
                    <Bank size={22} weight="duotone" className="shrink-0 text-slate-700" />
                    <div>
                        <h3 className="font-black text-slate-950">حركات البنك التي تنتظر تصنيف موظف</h3>
                        <p className="mt-1 text-xs font-semibold leading-5 text-slate-500">
                            اختر الموظف ونوع الحركة فقط. مبلغ البنك وتاريخه لا يمكن تغييره من هنا.
                        </p>
                    </div>
                </div>
                {(data?.pending_movements || []).length === 0 ? (
                    <div className="flex items-center gap-2 rounded-xl border border-emerald-100 bg-white p-4 text-xs font-extrabold text-emerald-800">
                        <CheckCircle size={18} weight="fill" /> لا توجد حركات بنكية غير مصنفة للموظفين.
                    </div>
                ) : (data?.pending_movements || []).map((movement) => (
                    <MovementClassifier
                        key={movement.id}
                        movement={movement}
                        employees={data?.employees || []}
                        canPost={canPost}
                        onDone={refresh}
                    />
                ))}
            </section>

            {!canPost && (
                <div className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold leading-6 text-amber-900">
                    <WarningCircle size={18} className="mt-0.5 shrink-0" />
                    لديك صلاحية العرض فقط. إثبات الاستحقاق أو ربط حركة البنك بموظف يحتاج صلاحية ترحيل الرواتب.
                </div>
            )}
        </div>
    );
}
