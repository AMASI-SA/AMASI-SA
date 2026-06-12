/**
 * EmployeeBalanceCard (Iter-138)
 *
 * Single source of truth for displaying ONE employee's cumulative
 * salary balance.  Wraps the canonical
 *   GET /api/liabilities/salary-accrual-summary
 * endpoint that every page should read from so the numbers stay
 * identical across the app (FinancialInputHub, FinancialPosition,
 * OperationsDashboard, OperationalReports, Advances).
 *
 * Props:
 *   employeeId   (required) — operating_salaries.id
 *   variant      (optional) — "full" (default) | "compact"
 *   className    (optional)
 *
 * Reload helper: the parent may call the card's onReload callback
 * via a ref, but for now it auto-reloads on mount + when employeeId
 * changes.  This card is read-only.
 */
import { useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";


const fmt = (v) =>
    Number(v || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });


export function EmployeeBalanceCard({
    employeeId, variant = "full", className = "",
}) {
    const [emp, setEmp] = useState(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    useEffect(() => {
        if (!employeeId) {
            setEmp(null);
            return;
        }
        let mounted = true;
        setLoading(true);
        setError(null);
        api.get("/liabilities/salary-accrual-summary")
            .then(({ data }) => {
                if (!mounted) return;
                const row = (data.employees || []).find((e) => e.id === employeeId);
                setEmp(row || null);
                if (!row) setError("لم نعثر على بيانات تراكم لهذا الموظف.");
            })
            .catch((e) => {
                if (!mounted) return;
                setError("تعذّر تحميل رصيد الموظف.");
                console.error("salary-accrual-summary failed", e);
            })
            .finally(() => mounted && setLoading(false));
        return () => { mounted = false; };
    }, [employeeId]);

    if (!employeeId) return null;
    if (loading) {
        return (
            <div
                className={`rounded-xl border-2 border-slate-200 bg-slate-50 p-3 text-xs text-slate-500 text-center ${className}`}
                data-testid="employee-balance-card-loading"
            >
                جاري حساب رصيد الموظف…
            </div>
        );
    }
    if (error || !emp) {
        return (
            <div
                className={`rounded-xl border-2 border-rose-200 bg-rose-50 p-3 text-xs text-rose-800 ${className}`}
                data-testid="employee-balance-card-error"
            >
                {error || "لا توجد بيانات لهذا الموظف."}
            </div>
        );
    }

    const isActive = emp.status === "active";

    if (variant === "compact") {
        return (
            <div
                className={`rounded-xl border-2 border-emerald-200 bg-emerald-50 p-3 ${className}`}
                data-testid="employee-balance-card-compact"
                data-employee-id={emp.id}
            >
                <div className="flex items-center justify-between gap-2 mb-2">
                    <div className="text-sm font-extrabold text-slate-900">
                        {emp.name}
                        <span className={`ms-2 px-2 py-0.5 text-[10px] font-bold rounded-full ${isActive ? "bg-emerald-600 text-white" : "bg-slate-400 text-white"}`}>
                            {isActive ? "نشط" : "موقوف"}
                        </span>
                    </div>
                    <div className="text-[11px] text-slate-500">
                        راتب شهري: <span className="num font-bold">{fmt(emp.monthly_amount)}</span> ر.س
                    </div>
                </div>
                <div className="grid grid-cols-4 gap-2 text-[11px]">
                    <Mini label="متراكم" value={emp.accrued} tone="slate" testid="emp-bal-accrued" />
                    <Mini label="سلف" value={emp.outstanding_advance} tone="amber" testid="emp-bal-advance" />
                    <Mini label="مدفوع" value={emp.paid} tone="emerald" testid="emp-bal-paid" />
                    <Mini label="صافي مستحق" value={emp.net_due} tone="rose" bold testid="emp-bal-net-due" />
                </div>
            </div>
        );
    }

    return (
        <div
            className={`rounded-xl border-2 border-emerald-200 bg-gradient-to-br from-emerald-50 to-white p-4 ${className}`}
            data-testid="employee-balance-card"
            data-employee-id={emp.id}
        >
            <div className="flex items-center justify-between gap-2 flex-wrap mb-3">
                <div>
                    <div className="text-[11px] text-slate-500">رصيد الموظف الموحَّد</div>
                    <div className="text-lg font-extrabold text-slate-900 flex items-center gap-2">
                        👤 {emp.name}
                        <span className={`px-2 py-0.5 text-[10px] font-bold rounded-full ${isActive ? "bg-emerald-600 text-white" : "bg-slate-400 text-white"}`}>
                            {isActive ? "نشط" : "موقوف"}
                        </span>
                    </div>
                </div>
                <div className="text-left">
                    <div className="text-[11px] text-slate-500">راتب شهري</div>
                    <div className="num font-extrabold text-slate-900">{fmt(emp.monthly_amount)} ر.س</div>
                </div>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
                <Box label="متراكم" sub={`${emp.days_worked || 0} يوم عمل`} value={emp.accrued} tone="slate" testid="emp-bal-accrued" />
                <Box label="السلف المفتوحة" value={emp.outstanding_advance} tone="amber" testid="emp-bal-advance" />
                <Box label="المدفوع" value={emp.paid} tone="emerald" testid="emp-bal-paid" />
                <Box label="صافي المستحق" value={emp.net_due} tone="rose" bold testid="emp-bal-net-due" />
            </div>

            <div className="text-[10px] text-slate-500 bg-white/60 border border-slate-100 rounded px-2 py-1">
                <span className="font-bold">المعادلة:</span> صافي المستحق = المتراكم (
                <span className="num">{fmt(emp.accrued)}</span>
                ) − المدفوع (
                <span className="num">{fmt(emp.paid)}</span>
                ){" "}
                ⟹ <span className="num font-bold text-rose-700">{fmt(emp.net_due)}</span> ر.س
                {Number(emp.outstanding_advance) > 0 && (
                    <span className="ms-1 text-amber-700">
                        — سلف ستُخصم من الراتب القادم: <span className="num font-bold">{fmt(emp.outstanding_advance)}</span> ر.س
                    </span>
                )}
            </div>
        </div>
    );
}


function Box({ label, sub, value, tone = "slate", bold = false, testid }) {
    const tones = {
        slate:   "bg-white border-slate-200 text-slate-900",
        emerald: "bg-emerald-50 border-emerald-300 text-emerald-900",
        amber:   "bg-amber-50 border-amber-300 text-amber-900",
        rose:    "bg-rose-50 border-rose-300 text-rose-900",
    };
    return (
        <div className={`rounded-lg border-2 p-2 text-center ${tones[tone]}`} data-testid={testid}>
            <div className="text-[10px] text-slate-500 mb-1">{label}</div>
            <div className={`num ${bold ? "text-base font-extrabold" : "text-sm font-bold"}`}>
                {fmt(value)}
            </div>
            {sub && <div className="text-[9px] text-slate-400 mt-0.5">{sub}</div>}
        </div>
    );
}


function Mini({ label, value, tone = "slate", bold = false, testid }) {
    const tones = {
        slate:   "bg-white border-slate-200",
        emerald: "bg-emerald-100 border-emerald-200",
        amber:   "bg-amber-100 border-amber-200",
        rose:    "bg-rose-100 border-rose-300",
    };
    return (
        <div className={`rounded border ${tones[tone]} px-2 py-1 text-center`} data-testid={testid}>
            <div className="text-[9px] text-slate-500">{label}</div>
            <div className={`num text-xs ${bold ? "font-extrabold" : "font-bold"}`}>
                {fmt(value)}
            </div>
        </div>
    );
}


/**
 * SalaryAccrualSummaryCard
 *
 * Aggregate view (totals across ALL employees) — for Dashboard /
 * OperationalReports.  Same endpoint, same source of truth.
 *
 * Props:
 *   showEmployeeTable (bool)  — render per-employee table below totals
 *   onLoaded (fn)             — called once data is fetched
 *   className
 */
export function SalaryAccrualSummaryCard({
    showEmployeeTable = false, onLoaded = null, className = "",
}) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    useEffect(() => {
        let mounted = true;
        setLoading(true);
        api.get("/liabilities/salary-accrual-summary")
            .then(({ data: d }) => {
                if (!mounted) return;
                setData(d);
                onLoaded?.(d);
            })
            .catch((e) => console.error("salary-accrual-summary failed", e))
            .finally(() => mounted && setLoading(false));
        return () => { mounted = false; };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    if (loading) {
        return (
            <div className={`rounded-xl bg-slate-50 border border-slate-200 p-4 text-center text-xs text-slate-500 ${className}`} data-testid="salary-accrual-loading">
                جاري حساب الرواتب التراكمية…
            </div>
        );
    }
    if (!data) return null;

    // Iter-151d — Data hygiene action. Surface a small "تنظيف بيانات
    // قديمة" link when the merchant might benefit from clearing stale
    // partial rows that block the pay-liability dropdown. Defensive
    // dry-run first so we never delete/mutate unknowingly.
    const runCleanup = async () => {
        try {
            // 1) Dry run to count
            const { data: dry } = await api.post(
                "/liabilities/admin/cleanup-stale-partial?dry_run=true"
            );
            const n = dry.candidates_found || 0;
            if (n === 0) {
                toast.info("لا توجد سطور تالفة تحتاج تنظيف");
                return;
            }
            const ok = window.confirm(
                `تم العثور على ${n} سطر التزام بحالة "جزئي" لكنه مدفوع بالكامل ` +
                `وقد يمنع تسجيل أي راتب جديد.\n\nهل تريد إصلاحها (status=paid) الآن؟`
            );
            if (!ok) return;
            const { data: res } = await api.post(
                "/liabilities/admin/cleanup-stale-partial"
            );
            toast.success(
                `تم إصلاح ${res.updated || 0} من ${res.candidates_found || 0} سطر`
            );
            // Trigger refresh via onLoaded callback if parent listens
            const { data: reloaded } = await api.get(
                "/liabilities/salary-accrual-summary"
            );
            setData(reloaded);
            onLoaded?.(reloaded);
        } catch (e) {
            toast.error("تعذّر تشغيل أداة التنظيف");
        }
    };

    return (
        <div className={className} data-testid="salary-accrual-summary">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
                <Box label="إجمالي المتراكم" sub={`${data.active_count || 0} نشط · ${data.suspended_count || 0} موقوف`} value={data.accrued_total} tone="slate" testid="sa-total-accrued" />
                <Box label="إجمالي السلف المفتوحة" value={data.advances_total} tone="amber" testid="sa-total-advances" />
                <Box label="إجمالي المدفوع" value={data.paid_total} tone="emerald" testid="sa-total-paid" />
                <Box label="صافي المستحق للموظفين" value={data.net_due} tone="rose" bold testid="sa-total-net-due" />
            </div>

            {/* Iter-151d — Data hygiene action */}
            <div className="mb-3 flex items-center justify-end">
                <button
                    type="button"
                    onClick={runCleanup}
                    className="text-[11px] text-slate-500 hover:text-rose-700 underline decoration-dotted underline-offset-2"
                    data-testid="cleanup-stale-partial-btn"
                    title="إصلاح سطور الالتزام المدفوعة بالكامل لكنها تحتفظ بحالة جزئية — يمنع تكرار خطأ &quot;المتبقي 0.00&quot; عند سداد الرواتب"
                >
                    🔧 تنظيف بيانات الالتزامات القديمة
                </button>
            </div>

            {showEmployeeTable && (
                <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
                    <table className="w-full text-xs" data-testid="sa-employees-table">
                        <thead className="bg-slate-50 text-slate-700">
                            <tr>
                                <th className="p-2 text-right">الاسم</th>
                                <th className="p-2 text-right">الحالة</th>
                                <th className="p-2 num text-right">الراتب الشهري</th>
                                <th className="p-2 num text-right">أيام عمل</th>
                                <th className="p-2 num text-right">متراكم</th>
                                <th className="p-2 num text-right">سلف</th>
                                <th className="p-2 num text-right">مدفوع</th>
                                <th className="p-2 num text-right">صافي مستحق</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(data.employees || []).map((e) => (
                                <tr key={e.id} className="border-t border-slate-100 hover:bg-slate-50">
                                    <td className="p-2 font-bold text-slate-900">{e.name}</td>
                                    <td className="p-2">
                                        <span className={`px-2 py-0.5 text-[10px] font-bold rounded-full ${e.status === "active" ? "bg-emerald-100 text-emerald-700" : "bg-slate-200 text-slate-600"}`}>
                                            {e.status === "active" ? "نشط" : "موقوف"}
                                        </span>
                                    </td>
                                    <td className="p-2 num text-slate-700">{fmt(e.monthly_amount)}</td>
                                    <td className="p-2 num text-slate-700">{e.days_worked || 0}</td>
                                    <td className="p-2 num font-bold text-amber-800">{fmt(e.accrued)}</td>
                                    <td className="p-2 num text-amber-700">{fmt(e.outstanding_advance)}</td>
                                    <td className="p-2 num text-emerald-700">{fmt(e.paid)}</td>
                                    <td className="p-2 num font-extrabold text-rose-700">{fmt(e.net_due)}</td>
                                </tr>
                            ))}
                            {(data.employees || []).length === 0 && (
                                <tr><td colSpan={8} className="p-3 text-center text-slate-500">لا يوجد موظفون مسجَّلون.</td></tr>
                            )}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}


export default EmployeeBalanceCard;
