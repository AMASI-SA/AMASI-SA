import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
    ArrowClockwise,
    CheckCircle,
    ClockCounterClockwise,
    CurrencyCircleDollar,
    IdentificationCard,
    ShieldCheck,
    UserCircleCheck,
    UserCircleGear,
    UsersThree,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import StoreOperationsAccessWorkspace from "./StoreOperationsAccessWorkspace";
import {
    applyEmployeesV2ShadowMigration,
    getEmployeesV2,
} from "../services/employeesV2";


const moneyFormatter = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "SAR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});
const numberFormatter = new Intl.NumberFormat("en-US");

const ACCOUNT_STATUS = {
    linked: { label: "حساب الدخول مرتبط", cls: "border-emerald-200 bg-emerald-50 text-emerald-800" },
    review_required: { label: "يحتاج مراجعة الربط", cls: "border-amber-200 bg-amber-50 text-amber-900" },
    not_required: { label: "بدون حساب دخول", cls: "border-slate-200 bg-slate-50 text-slate-700" },
    conflict: { label: "تعارض في الربط", cls: "border-rose-200 bg-rose-50 text-rose-800" },
};

function errorCode(error) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string") return detail;
    return detail?.code || "employees_v2_load_failed";
}

function SummaryCard({ label, value, hint, tone = "slate", Icon }) {
    const colors = {
        slate: "border-slate-200 bg-white text-slate-950",
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-950",
        amber: "border-amber-200 bg-amber-50 text-amber-950",
        violet: "border-violet-200 bg-violet-50 text-violet-950",
    };
    return (
        <article className={`rounded-2xl border p-4 shadow-sm ${colors[tone] || colors.slate}`}>
            <div className="flex items-center gap-2 text-xs font-black opacity-75"><Icon size={20} weight="duotone" />{label}</div>
            <div className="mt-2 text-3xl font-black tabular-nums" dir="ltr">{value}</div>
            {hint && <div className="mt-1 text-xs font-bold opacity-65">{hint}</div>}
        </article>
    );
}

function SafetyPanel({ safety }) {
    const checks = [
        ["لا تعديل على الرواتب القديمة", safety?.operating_salaries_writes === false],
        ["لا قيود جديدة أو إعادة احتساب", safety?.general_ledger_writes === false && safety?.historical_recompute === false],
        ["لا تعديل على السلف والعهد", safety?.liability_writes === false],
        ["لا تغيير لحسابات الدخول والصلاحيات", safety?.user_account_writes === false && safety?.role_assignment_writes === false],
    ];
    return (
        <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4" data-testid="employees-v2-safety-panel">
            <h2 className="font-black text-emerald-950"><ShieldCheck className="ml-1 inline" size={22} weight="duotone" />حدود الترحيل الآمن</h2>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {checks.map(([label, passed]) => (
                    <div key={label} className="flex items-center gap-2 rounded-xl border border-emerald-200 bg-white px-3 py-2 text-xs font-bold text-emerald-900">
                        {passed ? <CheckCircle size={18} weight="fill" /> : <WarningCircle size={18} weight="fill" className="text-rose-600" />}
                        {label}
                    </div>
                ))}
            </div>
        </section>
    );
}

function EmployeeCard({ employee }) {
    const accountStatus = ACCOUNT_STATUS[employee.account?.status] || ACCOUNT_STATUS.not_required;
    const salary = employee.salary_contract || {};
    const financial = employee.financial_snapshot || {};
    const role = employee.operational_role || {};
    return (
        <article className="rounded-2xl border bg-white p-4 shadow-sm" data-testid="employees-v2-employee-card">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 className="text-lg font-black text-slate-950">{employee.name || "موظف"}</h2>
                        <span className={`rounded-full px-2.5 py-1 text-[11px] font-black ${employee.status === "active" ? "bg-emerald-100 text-emerald-800" : "bg-slate-200 text-slate-700"}`}>
                            {employee.status === "active" ? "نشط" : "متوقف"}
                        </span>
                        {employee.shadow_exists && <span className="rounded-full bg-violet-100 px-2.5 py-1 text-[11px] font-black text-violet-800">نُقل إلى النواة الجديدة</span>}
                    </div>
                    <div className="mt-1 text-xs font-bold text-slate-500" dir="ltr">Legacy: {employee.legacy_employee_id}</div>
                </div>
                <div className={`rounded-xl border px-3 py-2 text-xs font-black ${accountStatus.cls}`}>
                    {accountStatus.label}
                    {employee.account?.account_email && <div className="mt-1 font-normal" dir="ltr">{employee.account.account_email}</div>}
                    {employee.account?.suggested_account?.email && <div className="mt-1 font-normal" dir="ltr">اقتراح: {employee.account.suggested_account.email}</div>}
                </div>
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <div className="rounded-xl border bg-slate-50 p-3"><div className="text-[11px] font-bold text-slate-500">الراتب الشهري</div><div className="mt-1 font-black" dir="ltr">{moneyFormatter.format(salary.monthly_amount || 0)}</div></div>
                <div className="rounded-xl border bg-slate-50 p-3"><div className="text-[11px] font-bold text-slate-500">الراتب المستحق</div><div className="mt-1 font-black" dir="ltr">{moneyFormatter.format(financial.salary_payable || 0)}</div></div>
                <div className="rounded-xl border bg-slate-50 p-3"><div className="text-[11px] font-bold text-slate-500">السلف</div><div className="mt-1 font-black" dir="ltr">{moneyFormatter.format(financial.advance || 0)}</div></div>
                <div className="rounded-xl border bg-slate-50 p-3"><div className="text-[11px] font-bold text-slate-500">العهد</div><div className="mt-1 font-black" dir="ltr">{moneyFormatter.format(financial.custody || 0)}</div></div>
                <div className="rounded-xl border bg-slate-50 p-3"><div className="text-[11px] font-bold text-slate-500">الدور التشغيلي</div><div className="mt-1 truncate font-black">{role.role_key || "غير محدد"}</div><div className="mt-1 text-[10px] text-slate-500">{numberFormatter.format(role.effective_permissions?.length || 0)} صلاحية</div></div>
            </div>

            {(employee.warnings?.length > 0 || employee.blockers?.length > 0) && (
                <div className="mt-3 flex flex-wrap gap-2">
                    {employee.warnings?.map((warning) => <span key={warning} className="rounded-lg border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-bold text-amber-900">{warning === "account_link_review_required" ? "ربط حساب الدخول يحتاج اعتمادًا يدويًا" : warning === "login_account_not_created" ? "يمكن إنشاء حساب دخول لاحقًا" : "تغير الراتب القديم بعد إنشاء النسخة التجريبية"}</span>)}
                    {employee.blockers?.map((blocker) => <span key={blocker} className="rounded-lg border border-rose-200 bg-rose-50 px-2 py-1 text-[11px] font-bold text-rose-800">{blocker === "conflicting_account_links" ? "يوجد أكثر من حساب مرتبط" : "معرّف الموظف القديم مفقود أو مكرر"}</span>)}
                </div>
            )}
        </article>
    );
}

function MigrationWorkspace() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [applying, setApplying] = useState(false);
    const [confirmOpen, setConfirmOpen] = useState(false);
    const [query, setQuery] = useState("");
    const applyInFlight = useRef(false);
    const cancelButtonRef = useRef(null);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setData(await getEmployeesV2());
        } catch (error) {
            toast.error(errorCode(error));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(); }, [load]);
    useEffect(() => {
        if (confirmOpen) cancelButtonRef.current?.focus();
    }, [confirmOpen]);

    const employees = useMemo(() => {
        const rows = data?.employees || [];
        const needle = query.trim().toLocaleLowerCase("ar");
        if (!needle) return rows;
        return rows.filter((row) => `${row.name || ""} ${row.account?.account_email || ""} ${row.legacy_employee_id || ""}`.toLocaleLowerCase("ar").includes(needle));
    }, [data?.employees, query]);

    function requestShadowMigration() {
        const summary = data?.summary;
        if (!summary || summary.blocking_issues > 0 || summary.ready_to_create === 0) return;
        setConfirmOpen(true);
    }

    async function applyShadow() {
        const summary = data?.summary;
        if (!summary || summary.blocking_issues > 0 || summary.ready_to_create === 0) {
            setConfirmOpen(false);
            return;
        }
        if (applyInFlight.current) return;
        applyInFlight.current = true;
        setApplying(true);
        try {
            const result = await applyEmployeesV2ShadowMigration();
            setData(result.preview);
            setConfirmOpen(false);
            toast.success(result.idempotent_replay ? "النسخة التجريبية موجودة مسبقًا ومطابقة" : "تم إنشاء نواة الموظفين وعقود الرواتب التجريبية");
        } catch (error) {
            toast.error(errorCode(error));
        } finally {
            applyInFlight.current = false;
            setApplying(false);
        }
    }

    const summary = data?.summary || {};
    const canApply = !loading && summary.ready_to_create > 0 && summary.blocking_issues === 0;
    return (
        <div className="space-y-5" data-testid="employees-v2-migration-workspace">
            <section className="overflow-hidden rounded-3xl border border-slate-800 bg-white shadow-sm">
                <div className="bg-gradient-to-l from-slate-950 via-emerald-950 to-slate-950 p-6 text-white">
                    <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
                        <div>
                            <div className="flex items-center gap-2 text-sm font-black text-emerald-200"><UsersThree size={24} weight="duotone" /> Mezan Employee OS</div>
                            <h1 className="mt-2 text-2xl font-black sm:text-3xl">الموظفون والرواتب</h1>
                            <p className="mt-2 max-w-3xl text-sm leading-7 text-slate-200">هوية موحدة تربط الموظف بحساب الدخول، الصلاحيات، إدارة التجهيز، عقد الراتب والسجل المالي—مع إبقاء الرواتب القديمة والـLedger مصدر الحقيقة أثناء التحقق.</p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <button type="button" onClick={load} disabled={loading || applying} className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-4 py-3 text-sm font-black text-white disabled:opacity-50"><ArrowClockwise className={loading ? "animate-spin" : ""} /> تحديث التقرير</button>
                            <button type="button" onClick={requestShadowMigration} disabled={!canApply || applying} data-testid="employees-v2-open-shadow-confirmation" className="inline-flex items-center gap-2 rounded-xl bg-emerald-300 px-4 py-3 text-sm font-black text-emerald-950 disabled:cursor-not-allowed disabled:opacity-50"><IdentificationCard size={20} weight="duotone" />{applying ? "جارٍ إنشاء النواة…" : "إنشاء النسخة التجريبية"}</button>
                        </div>
                    </div>
                </div>
                <div className="border-t border-emerald-900 bg-emerald-950 px-5 py-3 text-xs font-bold text-emerald-100"><ClockCounterClockwise className="ml-1 inline" /> المرحلة الحالية: ترحيل ظل للقراءة والمطابقة فقط. لا يبدأ احتساب راتب جديد ولا يوقف النظام القديم.</div>
            </section>

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <SummaryCard Icon={UsersThree} label="الموظفون في النظام القديم" value={numberFormatter.format(summary.legacy_employees || 0)} hint={`${numberFormatter.format(summary.active_employees || 0)} نشط · ${numberFormatter.format(summary.stopped_employees || 0)} متوقف`} />
                <SummaryCard Icon={CurrencyCircleDollar} label="إجمالي الرواتب النشطة" value={moneyFormatter.format(summary.active_monthly_salary_total || 0)} hint="شهريًا حسب السجلات الحالية" tone="emerald" />
                <SummaryCard Icon={UserCircleCheck} label="حسابات الدخول المرتبطة" value={numberFormatter.format(summary.linked_login_accounts || 0)} hint={`${numberFormatter.format(summary.accounts_needing_review || 0)} تحتاج مراجعة`} tone="violet" />
                <SummaryCard Icon={WarningCircle} label="عوائق الترحيل" value={numberFormatter.format(summary.blocking_issues || 0)} hint={`${numberFormatter.format(summary.warnings || 0)} تنبيه غير مانع`} tone={summary.blocking_issues ? "amber" : "emerald"} />
            </section>

            <SafetyPanel safety={data?.safety} />

            {confirmOpen && (
                <div className="fixed inset-0 z-[140] flex items-center justify-center bg-slate-950/70 p-4">
                    <section
                        role="alertdialog"
                        aria-modal="true"
                        aria-labelledby="employees-v2-shadow-confirmation-title"
                        aria-describedby="employees-v2-shadow-confirmation-description"
                        dir="rtl"
                        data-testid="employees-v2-shadow-confirmation"
                        onKeyDown={(event) => {
                            if (event.key === "Escape" && !applying) setConfirmOpen(false);
                        }}
                        className="w-full max-w-xl rounded-2xl border border-emerald-200 bg-white p-6 text-right shadow-2xl"
                    >
                        <h2 id="employees-v2-shadow-confirmation-title" className="flex items-center gap-2 text-lg font-black text-emerald-950">
                            <IdentificationCard size={24} weight="duotone" />
                            تأكيد إنشاء النسخة التجريبية
                        </h2>
                        <p id="employees-v2-shadow-confirmation-description" className="mt-3 text-sm leading-7 text-slate-600">
                            سيتم إنشاء نسخة تجريبية آمنة لـ {numberFormatter.format(summary.ready_to_create || 0)} موظف وعقد راتب داخل ميزان 2. لن تتغير الرواتب القديمة أو القيود أو السلف والعهد. هل تريد المتابعة؟
                        </p>
                        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                            <button
                                ref={cancelButtonRef}
                                type="button"
                                onClick={() => setConfirmOpen(false)}
                                disabled={applying}
                                data-testid="employees-v2-cancel-shadow-migration"
                                className="inline-flex h-10 items-center justify-center rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-bold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                إلغاء
                            </button>
                            <button
                                type="button"
                                onClick={applyShadow}
                                disabled={applying}
                                data-testid="employees-v2-confirm-shadow-migration"
                                className="inline-flex h-10 items-center justify-center rounded-md bg-emerald-700 px-4 py-2 text-sm font-bold text-white hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                                {applying ? "جارٍ إنشاء النواة…" : "نعم، إنشاء النسخة التجريبية"}
                            </button>
                        </div>
                    </section>
                </div>
            )}

            <section className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-2xl border bg-white p-4"><div className="text-xs font-bold text-slate-500">رواتب مستحقة حسب Ledger</div><div className="mt-1 text-xl font-black" dir="ltr">{moneyFormatter.format(summary.salary_payable_total || 0)}</div></div>
                <div className="rounded-2xl border bg-white p-4"><div className="text-xs font-bold text-slate-500">سلف الموظفين</div><div className="mt-1 text-xl font-black" dir="ltr">{moneyFormatter.format(summary.advance_total || 0)}</div></div>
                <div className="rounded-2xl border bg-white p-4"><div className="text-xs font-bold text-slate-500">عهد الموظفين</div><div className="mt-1 text-xl font-black" dir="ltr">{moneyFormatter.format(summary.custody_total || 0)}</div></div>
            </section>

            <section className="rounded-2xl border bg-white p-4 shadow-sm">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div><h2 className="font-black"><UserCircleGear className="ml-1 inline" size={22} />تقرير مطابقة الموظفين</h2><p className="mt-1 text-xs text-slate-500">لا يتم ربط حساب بالاسم فقط؛ يظهر كاقتراح يحتاج مراجعة.</p></div>
                    <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث بالاسم أو البريد أو المعرّف…" className="h-11 w-full rounded-xl border px-4 text-sm outline-none focus:border-emerald-500 sm:max-w-sm" />
                </div>
            </section>

            <section className="space-y-3">
                {loading && <div className="rounded-2xl border bg-white p-10 text-center font-bold text-slate-500">جارٍ بناء تقرير الموظفين والرواتب…</div>}
                {!loading && employees.length === 0 && <div className="rounded-2xl border bg-white p-10 text-center text-slate-500">لا يوجد موظفون مطابقون.</div>}
                {!loading && employees.map((employee) => <EmployeeCard key={`${employee.employee_id}:${employee.legacy_employee_id}`} employee={employee} />)}
            </section>
        </div>
    );
}

export default function EmployeesV2() {
    const [searchParams] = useSearchParams();
    const workspace = searchParams.get("workspace") || "employees";
    return (
        <div className="space-y-5" dir="rtl" data-testid="employees-v2-page">
            <nav className="flex flex-wrap gap-2 rounded-2xl border bg-white p-2 shadow-sm" aria-label="أقسام الموظفين">
                <Link to="/employees-v2" className={`rounded-xl px-4 py-2.5 text-sm font-black ${workspace === "employees" ? "bg-emerald-700 text-white" : "bg-slate-100 text-slate-700"}`}>الموظفون والرواتب</Link>
                <Link to="/employees-v2?workspace=permissions" className={`rounded-xl px-4 py-2.5 text-sm font-black ${workspace === "permissions" ? "bg-violet-700 text-white" : "bg-slate-100 text-slate-700"}`}>الصلاحيات وإدارة التجهيز</Link>
            </nav>
            {workspace === "permissions" ? <StoreOperationsAccessWorkspace /> : <MigrationWorkspace />}
        </div>
    );
}
