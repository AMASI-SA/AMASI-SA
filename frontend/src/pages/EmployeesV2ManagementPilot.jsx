import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowClockwise,
    CheckCircle,
    ClockCounterClockwise,
    CurrencyCircleDollar,
    IdentificationCard,
    LinkSimple,
    LockKey,
    PencilSimple,
    ShieldCheck,
    UserCircle,
    UserPlus,
    WarningCircle,
    X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    assignEmployeesV2PilotRole,
    createEmployeesV2Pilot,
    getEmployeesV2Management,
    getEmployeesV2PilotEvents,
    linkEmployeesV2PilotAccount,
    unlinkEmployeesV2PilotAccount,
    updateEmployeesV2Pilot,
} from "../services/employeesV2";


const moneyFormatter = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "SAR",
    maximumFractionDigits: 2,
});
const numberFormatter = new Intl.NumberFormat("en-US");
const EMPTY_FORM = {
    name: "",
    phone: "",
    contact_email: "",
    job_title: "",
    department: "",
    hire_date: "",
    monthly_salary: "0",
    status: "draft",
    notes: "",
};
const EVENT_LABELS = {
    employee_pilot_created: "إنشاء الموظف التجريبي",
    employee_pilot_updated: "تعديل بيانات الموظف",
    employee_pilot_account_linked: "ربط حساب الدخول",
    employee_pilot_account_unlinked: "فصل حساب الدخول",
    employee_pilot_role_assigned: "تعيين الدور والصلاحيات",
};
const ROLE_FALLBACK_LABELS = {
    product_manager: "مدير المنتجات",
    product_operator: "موظف المنتجات",
    cost_manager: "مسؤول التكاليف والمشتريات",
    warehouse_operator: "موظف المخزن",
    shipping_operator: "موظف الشحن والعنونة",
    marketing_manager: "مسؤول التسويق",
};


function errorCode(error) {
    const detail = error?.response?.data?.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    const messages = {
        employee_pilot_limit_reached: "يوجد موظف تجريبي بالفعل. أكمل اختباره قبل فتح التفعيل العام.",
        employee_management_pilot_only: "الموظفون المرحّلون محميون من التعديل في المرحلة التجريبية.",
        employee_version_conflict: "تغيرت بيانات الموظف في جلسة أخرى. حدّث الصفحة ثم أعد المحاولة.",
        employee_account_reserved_for_migrated_review: "هذا الحساب محجوز لمراجعة ربط موظف مهاجر، لذلك لن يُستخدم في التجربة.",
        employee_account_has_existing_role: "هذا الحساب لديه دور سابق. استخدم حسابًا تجريبيًا جديدًا بلا صلاحيات.",
        employee_account_linked_elsewhere: "حساب الدخول مرتبط بموظف آخر.",
        employee_account_link_required_before_role: "اربط حساب الدخول أولًا ثم عيّن الدور.",
        employee_login_account_not_available: "حساب الدخول غير متاح أو لا يتبع هذا المتجر.",
        employee_pilot_status_invalid: "الموظف التجريبي يبقى مسودة أو غير نشط حتى اعتماد التفعيل.",
    };
    return messages[code] || code || "تعذر تنفيذ العملية";
}


function ModalShell({ title, children, onClose, busy = false, testId }) {
    return (
        <div className="fixed inset-0 z-[150] flex items-end justify-center bg-slate-950/70 p-0 sm:items-center sm:p-4">
            <section
                role="dialog"
                aria-modal="true"
                aria-label={title}
                data-testid={testId}
                onKeyDown={(event) => {
                    if (event.key === "Escape" && !busy) onClose();
                }}
                className="max-h-[95vh] w-full max-w-2xl overflow-hidden rounded-t-3xl bg-white shadow-2xl sm:rounded-3xl"
                dir="rtl"
            >
                <header className="flex items-center justify-between border-b px-5 py-4">
                    <h2 className="text-lg font-black text-slate-950">{title}</h2>
                    <button type="button" onClick={onClose} disabled={busy} className="rounded-lg p-2 text-slate-500 hover:bg-slate-100 disabled:opacity-40" aria-label="إغلاق"><X size={20} /></button>
                </header>
                {children}
            </section>
        </div>
    );
}


function EmployeeFormModal({ employee, busy, onClose, onSubmit }) {
    const editing = Boolean(employee);
    const [form, setForm] = useState(() => employee ? {
        name: employee.name || "",
        phone: employee.phone || "",
        contact_email: employee.contact_email || "",
        job_title: employee.job_title || "",
        department: employee.department || "",
        hire_date: employee.hire_date || "",
        monthly_salary: String(employee.salary_contract?.monthly_amount ?? 0),
        status: employee.status || "draft",
        notes: employee.notes || "",
    } : EMPTY_FORM);
    const set = (key, value) => setForm((current) => ({ ...current, [key]: value }));
    const inputClass = "mt-1 h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm outline-none focus:border-emerald-500";

    return (
        <ModalShell title={editing ? "تعديل الموظف التجريبي" : "إضافة موظف تجريبي"} onClose={onClose} busy={busy} testId="employees-v2-pilot-form-dialog">
            <form
                onSubmit={(event) => {
                    event.preventDefault();
                    if (!form.name.trim()) return toast.error("اسم الموظف مطلوب");
                    onSubmit({
                        ...form,
                        name: form.name.trim(),
                        monthly_salary: Number(form.monthly_salary || 0),
                        ...(editing ? { expected_version: employee.version } : {}),
                    });
                }}
                className="max-h-[calc(95vh-65px)] overflow-y-auto p-5"
            >
                <div className="mb-4 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-xs font-bold leading-6 text-amber-950"><LockKey className="ml-1 inline" /> هذه بيانات اختبار داخل نواة الموظفين فقط. الراتب لا يُحتسب ولا يُنشئ التزامًا أو قيدًا ماليًا.</div>
                <div className="grid gap-4 sm:grid-cols-2">
                    <label className="text-xs font-bold text-slate-600">اسم الموظف *<input autoFocus value={form.name} onChange={(event) => set("name", event.target.value)} maxLength={80} className={inputClass} data-testid="employees-v2-pilot-name" /></label>
                    <label className="text-xs font-bold text-slate-600">رقم الجوال<input value={form.phone} onChange={(event) => set("phone", event.target.value)} maxLength={40} className={inputClass} dir="ltr" /></label>
                    <label className="text-xs font-bold text-slate-600">البريد للتواصل<input type="email" value={form.contact_email} onChange={(event) => set("contact_email", event.target.value)} maxLength={254} className={inputClass} dir="ltr" /></label>
                    <label className="text-xs font-bold text-slate-600">المسمى الوظيفي<input value={form.job_title} onChange={(event) => set("job_title", event.target.value)} maxLength={120} className={inputClass} /></label>
                    <label className="text-xs font-bold text-slate-600">القسم<input value={form.department} onChange={(event) => set("department", event.target.value)} maxLength={120} className={inputClass} /></label>
                    <label className="text-xs font-bold text-slate-600">تاريخ الانضمام<input type="date" value={form.hire_date} onChange={(event) => set("hire_date", event.target.value)} className={inputClass} dir="ltr" /></label>
                    <label className="text-xs font-bold text-slate-600">الراتب الشهري التجريبي<input type="number" min="0" step="0.01" value={form.monthly_salary} onChange={(event) => set("monthly_salary", event.target.value)} className={inputClass} dir="ltr" /></label>
                    <label className="text-xs font-bold text-slate-600">الحالة<select value={form.status} onChange={(event) => set("status", event.target.value)} className={inputClass}><option value="draft">مسودة تجريبية</option><option value="inactive">غير نشط</option></select></label>
                </div>
                <label className="mt-4 block text-xs font-bold text-slate-600">ملاحظات<textarea value={form.notes} onChange={(event) => set("notes", event.target.value)} maxLength={1000} rows={4} className="mt-1 w-full rounded-xl border border-slate-300 p-3 text-sm outline-none focus:border-emerald-500" /></label>
                <footer className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                    <button type="button" onClick={onClose} disabled={busy} className="rounded-xl border px-5 py-3 text-sm font-bold text-slate-700 disabled:opacity-40">إلغاء</button>
                    <button type="submit" disabled={busy} data-testid="employees-v2-pilot-form-submit" className="rounded-xl bg-emerald-700 px-5 py-3 text-sm font-black text-white disabled:opacity-50">{busy ? "جارٍ الحفظ…" : editing ? "حفظ التعديلات" : "إنشاء الموظف التجريبي"}</button>
                </footer>
            </form>
        </ModalShell>
    );
}


function AccountLinkModal({ employee, candidates, busy, onClose, onLink, onUnlink }) {
    const [selected, setSelected] = useState(candidates?.[0]?.id || "");
    const linked = Boolean(employee.account?.user_id);
    return (
        <ModalShell title="حساب الدخول" onClose={onClose} busy={busy} testId="employees-v2-account-dialog">
            <div className="p-5">
                {linked ? (
                    <>
                        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4"><div className="font-black text-emerald-950">{employee.account.name || "حساب مرتبط"}</div><div className="mt-1 text-xs text-emerald-800" dir="ltr">{employee.account.email}</div></div>
                        <p className="mt-3 text-xs leading-6 text-slate-500">الفصل يعطّل الدور الذي أنشأته هذه التجربة، ولا يحذف حساب الدخول.</p>
                        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onClose} disabled={busy} className="rounded-xl border px-4 py-2.5 text-sm font-bold">إلغاء</button><button type="button" onClick={onUnlink} disabled={busy} data-testid="employees-v2-unlink-account" className="rounded-xl bg-rose-600 px-4 py-2.5 text-sm font-black text-white disabled:opacity-50">{busy ? "جارٍ الفصل…" : "فصل الحساب"}</button></div>
                    </>
                ) : candidates?.length ? (
                    <>
                        <p className="mb-3 text-sm leading-7 text-slate-600">اختر حسابًا تجريبيًا غير مرتبط ولا يحمل صلاحيات سابقة. الحسابات المحجوزة لمراجعة الموظفين المرحّلين لا تظهر هنا.</p>
                        <label className="text-xs font-bold text-slate-600">الحساب<select value={selected} onChange={(event) => setSelected(event.target.value)} className="mt-1 h-12 w-full rounded-xl border px-3 text-sm" data-testid="employees-v2-account-select">{candidates.map((account) => <option key={account.id} value={account.id}>{account.name || "حساب"} — {account.email}</option>)}</select></label>
                        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onClose} disabled={busy} className="rounded-xl border px-4 py-2.5 text-sm font-bold">إلغاء</button><button type="button" onClick={() => onLink(selected)} disabled={busy || !selected} data-testid="employees-v2-link-account" className="rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-black text-white disabled:opacity-50">{busy ? "جارٍ الربط…" : "ربط الحساب"}</button></div>
                    </>
                ) : (
                    <div className="rounded-2xl border border-amber-200 bg-amber-50 p-5 text-sm leading-7 text-amber-950">
                        <p><WarningCircle className="ml-1 inline" /> لا يوجد حساب آمن متاح للتجربة. أنشئ حساب Viewer جديدًا بلا صلاحيات ثم عد إلى هنا واضغط تحديث.</p>
                        <Link to="/team" className="mt-4 inline-flex items-center gap-2 rounded-xl bg-amber-900 px-4 py-2.5 text-xs font-black text-white"><UserPlus size={18} /> فتح إدارة الفريق وإنشاء حساب</Link>
                    </div>
                )}
            </div>
        </ModalShell>
    );
}


function RoleModal({ employee, management, busy, onClose, onSubmit }) {
    const selectableRoles = useMemo(() => Object.keys(management.role_catalog || {}).filter((key) => !["owner", "ai_product_optimizer"].includes(key)), [management.role_catalog]);
    const [roleKey, setRoleKey] = useState(employee.operational_role?.role_key || selectableRoles[0] || "product_operator");
    const [enabled, setEnabled] = useState(employee.operational_role?.enabled !== false);
    const permissions = management.role_catalog?.[roleKey] || [];
    return (
        <ModalShell title="الدور والصلاحيات" onClose={onClose} busy={busy} testId="employees-v2-role-dialog">
            <div className="max-h-[calc(95vh-65px)] overflow-y-auto p-5">
                <div className="rounded-2xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-950"><ShieldCheck className="ml-1 inline" /> يعرض ميزان الصلاحيات قبل الحفظ. هذه المرحلة تطبق الدور على حساب الموظف التجريبي فقط.</div>
                <label className="mt-4 block text-xs font-bold text-slate-600">الدور التشغيلي<select value={roleKey} onChange={(event) => setRoleKey(event.target.value)} className="mt-1 h-12 w-full rounded-xl border px-3 text-sm" data-testid="employees-v2-role-select">{selectableRoles.map((key) => <option key={key} value={key}>{management.role_labels?.[key] || ROLE_FALLBACK_LABELS[key] || key}</option>)}</select></label>
                <label className="mt-3 flex items-center gap-2 rounded-xl border p-3 text-sm font-bold"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> الدور مفعّل</label>
                <section className="mt-4 rounded-2xl border bg-slate-50 p-4"><h3 className="text-sm font-black">الصلاحيات المشمولة ({numberFormatter.format(permissions.length)})</h3><div className="mt-3 flex flex-wrap gap-2">{permissions.map((permission) => <span key={permission} className="rounded-lg border border-violet-200 bg-white px-2 py-1 text-[11px] font-bold text-violet-900" dir="ltr">{permission}</span>)}</div></section>
                <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onClose} disabled={busy} className="rounded-xl border px-4 py-2.5 text-sm font-bold">إلغاء</button><button type="button" onClick={() => onSubmit({ role_key: roleKey, enabled, extra_permissions: [], denied_permissions: [], warehouse_ids: [], workplace_warehouse_id: null, fulfillment_responsibilities: [] })} disabled={busy || !roleKey} data-testid="employees-v2-role-submit" className="rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-black text-white disabled:opacity-50">{busy ? "جارٍ الحفظ…" : "حفظ الدور"}</button></div>
            </div>
        </ModalShell>
    );
}


function EventsModal({ items, loading, onClose }) {
    return (
        <ModalShell title="سجل نشاط الموظف" onClose={onClose} testId="employees-v2-events-dialog">
            <div className="max-h-[70vh] overflow-y-auto p-5">
                {loading && <div className="py-10 text-center text-sm font-bold text-slate-500">جارٍ تحميل سجل النشاط…</div>}
                {!loading && !items.length && <div className="py-10 text-center text-sm text-slate-400">لا يوجد نشاط مسجل.</div>}
                <div className="space-y-2">{items.map((event) => <article key={event.id} className="rounded-xl border bg-slate-50 p-3 text-xs"><div className="font-black text-slate-900"><CheckCircle className="ml-1 inline text-emerald-600" />{EVENT_LABELS[event.event_type] || event.event_type}</div><div className="mt-1 text-slate-500">{event.actor_name || event.actor_id} · {event.occurred_at}</div></article>)}</div>
            </div>
        </ModalShell>
    );
}


function PilotEmployeeCard({ employee, management, onEdit, onAccount, onRole, onEvents }) {
    const salary = employee.salary_contract?.monthly_amount || 0;
    const linked = Boolean(employee.account?.user_id);
    const role = employee.operational_role || {};
    return (
        <article className="rounded-3xl border border-emerald-200 bg-white p-5 shadow-sm" data-testid="employees-v2-pilot-card">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2"><h2 className="text-xl font-black text-slate-950">{employee.name}</h2><span className="rounded-full bg-amber-100 px-2.5 py-1 text-[11px] font-black text-amber-900">تجريبي فقط</span><span className="rounded-full bg-slate-100 px-2.5 py-1 text-[11px] font-bold text-slate-700">{employee.status === "draft" ? "مسودة" : "غير نشط"}</span></div>
                    <p className="mt-2 text-sm text-slate-500">{[employee.job_title, employee.department].filter(Boolean).join(" · ") || "لم يُحدد المسمى أو القسم"}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <button type="button" onClick={onEdit} className="inline-flex items-center gap-1 rounded-xl border px-3 py-2 text-xs font-black"><PencilSimple /> تعديل</button>
                    <button type="button" onClick={onEvents} className="inline-flex items-center gap-1 rounded-xl border px-3 py-2 text-xs font-black"><ClockCounterClockwise /> النشاط</button>
                </div>
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-2xl border bg-slate-50 p-4"><div className="text-[11px] font-bold text-slate-500">الراتب التجريبي</div><div className="mt-1 font-black" dir="ltr">{moneyFormatter.format(salary)}</div><div className="mt-1 text-[10px] font-bold text-rose-600">غير مفعّل في الرواتب</div></div>
                <div className="rounded-2xl border bg-slate-50 p-4"><div className="text-[11px] font-bold text-slate-500">حساب الدخول</div><div className="mt-1 truncate font-black">{linked ? employee.account.name || employee.account.email : "غير مرتبط"}</div><button type="button" onClick={onAccount} className="mt-2 text-xs font-black text-violet-700">{linked ? "مراجعة أو فصل" : "ربط حساب"}</button></div>
                <div className="rounded-2xl border bg-slate-50 p-4"><div className="text-[11px] font-bold text-slate-500">الدور التشغيلي</div><div className="mt-1 truncate font-black">{role.role_key ? management.role_labels?.[role.role_key] || ROLE_FALLBACK_LABELS[role.role_key] || role.role_key : "غير محدد"}</div><div className="mt-1 text-[10px] text-slate-500">{numberFormatter.format(role.effective_permissions?.length || 0)} صلاحية</div></div>
                <div className="rounded-2xl border bg-slate-50 p-4"><div className="text-[11px] font-bold text-slate-500">الربط المالي</div><div className="mt-1 font-black text-emerald-800">معطّل وآمن</div><div className="mt-1 text-[10px] text-slate-500">لا Ledger · لا سلف · لا عهد</div></div>
            </div>
            <button type="button" onClick={linked ? onRole : onAccount} data-testid="employees-v2-primary-action" className="mt-4 inline-flex items-center gap-2 rounded-xl bg-violet-700 px-4 py-3 text-sm font-black text-white"><ShieldCheck size={18} />{linked ? "تعيين الدور والصلاحيات" : "ربط حساب الدخول"}</button>
        </article>
    );
}


export default function EmployeesV2ManagementPilot() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [modal, setModal] = useState(null);
    const [events, setEvents] = useState({ loading: false, items: [] });
    const mutationInFlight = useRef(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setData(await getEmployeesV2Management());
        } catch (error) {
            toast.error(errorCode(error));
        } finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => { load(); }, [load]);

    const management = data?.management || {};
    const employee = management.employees?.[0] || null;

    async function mutate(task, successMessage) {
        if (mutationInFlight.current) return;
        mutationInFlight.current = true;
        setBusy(true);
        try {
            const result = await task();
            setData(result);
            setModal(null);
            toast.success(successMessage);
        } catch (error) {
            toast.error(errorCode(error));
        } finally {
            mutationInFlight.current = false;
            setBusy(false);
        }
    }

    async function openEvents() {
        setModal({ type: "events" });
        setEvents({ loading: true, items: [] });
        try {
            const result = await getEmployeesV2PilotEvents(employee.id);
            setEvents({ loading: false, items: result.items || [] });
        } catch (error) {
            setEvents({ loading: false, items: [] });
            toast.error(errorCode(error));
        }
    }

    return (
        <div className="space-y-5" data-testid="employees-v2-management-pilot">
            <section className="overflow-hidden rounded-3xl border border-emerald-200 bg-white shadow-sm">
                <div className="bg-gradient-to-l from-slate-950 via-emerald-950 to-slate-950 p-6 text-white">
                    <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
                        <div><div className="flex items-center gap-2 text-sm font-black text-emerald-200"><IdentificationCard size={24} weight="duotone" /> Mezan Employee OS</div><h1 className="mt-2 text-2xl font-black sm:text-3xl">إدارة الموظفين</h1><p className="mt-2 max-w-3xl text-sm leading-7 text-slate-200">أنشئ وعدّل واربط حساب الدخول والدور داخل تجربة محمية قبل فتح الإدارة للموظفين المرحّلين.</p></div>
                        <div className="flex flex-wrap gap-2"><button type="button" onClick={load} disabled={loading || busy} className="inline-flex items-center gap-2 rounded-xl border border-white/20 bg-white/10 px-4 py-3 text-sm font-black disabled:opacity-40"><ArrowClockwise className={loading ? "animate-spin" : ""} /> تحديث</button><button type="button" onClick={() => setModal({ type: "form" })} disabled={loading || busy || !management.can_create_pilot} data-testid="employees-v2-add-pilot" className="inline-flex items-center gap-2 rounded-xl bg-emerald-300 px-4 py-3 text-sm font-black text-emerald-950 disabled:cursor-not-allowed disabled:opacity-40"><UserPlus size={20} /> إضافة موظف تجريبي</button></div>
                    </div>
                </div>
                <div className="border-t border-emerald-900 bg-emerald-950 px-5 py-3 text-xs font-bold text-emerald-100"><LockKey className="ml-1 inline" /> الموظفون المرحّلون وعددهم {numberFormatter.format(data?.summary?.legacy_employees || 0)} محميون من التعديل. لا يبدأ احتساب راتب أو قيد مالي في هذه المرحلة.</div>
            </section>

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <div className="rounded-2xl border bg-white p-4"><UserCircle className="text-emerald-700" size={22} /><div className="mt-2 text-xs font-bold text-slate-500">الموظف التجريبي</div><div className="mt-1 text-2xl font-black">{numberFormatter.format(management.pilot_count || 0)} / {numberFormatter.format(management.pilot_limit || 1)}</div></div>
                <div className="rounded-2xl border bg-white p-4"><LockKey className="text-violet-700" size={22} /><div className="mt-2 text-xs font-bold text-slate-500">الموظفون المحميون</div><div className="mt-1 text-2xl font-black">{numberFormatter.format(data?.summary?.already_migrated || 0)}</div></div>
                <div className="rounded-2xl border bg-white p-4"><LinkSimple className="text-sky-700" size={22} /><div className="mt-2 text-xs font-bold text-slate-500">حسابات متاحة للتجربة</div><div className="mt-1 text-2xl font-black">{numberFormatter.format(management.login_account_candidates?.length || 0)}</div></div>
                <div className="rounded-2xl border bg-white p-4"><CurrencyCircleDollar className="text-rose-700" size={22} /><div className="mt-2 text-xs font-bold text-slate-500">كتابات مالية</div><div className="mt-1 text-2xl font-black text-emerald-800">0</div></div>
            </section>

            <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-sm leading-7 text-emerald-950" data-testid="employees-v2-pilot-safety"><ShieldCheck className="ml-1 inline" /> نطاق التجربة: موظف واحد فقط، حساب جديد بلا دور سابق، صلاحيات تشغيلية قابلة للفصل، وسجل نشاط كامل. حساب عرفات وأي حساب مقترح لموظف مهاجر محجوز للمراجعة اليدوية.</section>

            {loading && <div className="rounded-3xl border bg-white p-12 text-center font-bold text-slate-500">جارٍ تحميل إدارة الموظفين…</div>}
            {!loading && employee && <PilotEmployeeCard employee={employee} management={management} onEdit={() => setModal({ type: "form", employee })} onAccount={() => setModal({ type: "account" })} onRole={() => setModal({ type: "role" })} onEvents={openEvents} />}
            {!loading && !employee && <section className="rounded-3xl border border-dashed border-emerald-300 bg-white p-10 text-center"><UserPlus className="mx-auto text-emerald-700" size={42} weight="duotone" /><h2 className="mt-3 text-xl font-black">ابدأ بموظف تجريبي واحد</h2><p className="mx-auto mt-2 max-w-xl text-sm leading-7 text-slate-500">اختبر الإنشاء والتعديل وربط الحساب والدور والسجل من البداية للنهاية. بعد نجاح المعايير نفتح نفس الواجهة للـ15 موظفًا.</p></section>}

            {modal?.type === "form" && <EmployeeFormModal employee={modal.employee} busy={busy} onClose={() => setModal(null)} onSubmit={(payload) => mutate(() => modal.employee ? updateEmployeesV2Pilot(modal.employee.id, payload) : createEmployeesV2Pilot(payload), modal.employee ? "تم تحديث الموظف التجريبي" : "تم إنشاء الموظف التجريبي دون تفعيل مالي")} />}
            {modal?.type === "account" && employee && <AccountLinkModal employee={employee} candidates={management.login_account_candidates || []} busy={busy} onClose={() => setModal(null)} onLink={(accountId) => mutate(() => linkEmployeesV2PilotAccount(employee.id, accountId), "تم ربط حساب الدخول بنطاق التجربة")} onUnlink={() => mutate(() => unlinkEmployeesV2PilotAccount(employee.id), "تم فصل الحساب وتعطيل دوره التجريبي")} />}
            {modal?.type === "role" && employee && <RoleModal employee={employee} management={management} busy={busy} onClose={() => setModal(null)} onSubmit={(payload) => mutate(() => assignEmployeesV2PilotRole(employee.id, payload), "تم حفظ الدور والصلاحيات للموظف التجريبي")} />}
            {modal?.type === "events" && <EventsModal items={events.items} loading={events.loading} onClose={() => setModal(null)} />}
        </div>
    );
}
