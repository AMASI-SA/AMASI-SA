import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    ArrowClockwise,
    CheckCircle,
    ClockCounterClockwise,
    CurrencyCircleDollar,
    DeviceMobile,
    IdentificationCard,
    Key,
    LinkSimple,
    MagnifyingGlass,
    PencilSimple,
    ShieldCheck,
    UserCircle,
    UserPlus,
    UsersThree,
    X,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    assignEmployeesV2Role,
    assignEmployeesV2MobileAppPermissions,
    createAndLinkEmployeesV2Account,
    createEmployeesV2,
    getEmployeesV2Events,
    getEmployeesV2Management,
    linkEmployeesV2Account,
    resetEmployeesV2AccountPassword,
    unlinkEmployeesV2Account,
    updateEmployeesV2,
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
    status: "active",
    notes: "",
};
const EVENT_LABELS = {
    employee_created: "إضافة الموظف",
    employee_updated: "تعديل بيانات الموظف أو حالته",
    employee_payroll_status_changed: "تغيير حالة الموظف واحتساب الراتب",
    employee_account_linked: "ربط حساب الدخول",
    employee_account_unlinked: "فصل حساب الدخول وإيقافه",
    employee_role_assigned: "تعيين الدور والصلاحيات",
    employee_mobile_app_permissions_assigned: "تعيين صلاحيات تطبيق AMASI",
    employee_account_password_reset: "تغيير كلمة مرور حساب الدخول",
    employee_shadow_migrated: "نقل الموظف إلى نواة ميزان 2",
    employee_pilot_created: "إنشاء سجل التجربة السابق",
    employee_pilot_updated: "تعديل سجل التجربة السابق",
    employee_pilot_account_linked: "ربط حساب التجربة السابق",
    employee_pilot_account_unlinked: "فصل حساب التجربة السابق",
    employee_pilot_role_assigned: "تعيين دور التجربة السابق",
};
const ROLE_FALLBACK_LABELS = {
    product_manager: "مدير المنتجات",
    product_operator: "موظف المنتجات",
    preparation_operator: "موظف التجهيز",
    customer_service: "خدمة العملاء",
    cost_manager: "مسؤول التكاليف والمشتريات",
    warehouse_operator: "موظف المخزن",
    shipping_operator: "موظف الشحن والعنونة",
    marketing_manager: "مسؤول التسويق",
};
const ROLE_DESCRIPTIONS = {
    preparation_operator: "يرى ملفات ومنتجات التجهيز المسندة إليه فقط، ويستطيع إيقاف قطعة مع كتابة السبب. الاستلام النهائي وتعديل الأسعار وإضافة خدمة تُمنح كصلاحيات إضافية أدناه.",
    customer_service: "يستطيع تسجيل إيقاف إلغاء أو تعديل أو ملاحظة على الطلب أو المنتج أو القطعة، ويصل الإشعار إلى موظف التجهيز المسؤول.",
};
const OPTIONAL_PERMISSION_LABELS = {
    "inventory.preparation.receive": "استلام منتجات التجهيز واعتماد فاتورة المورد",
    "supplier_receiving.product_price.edit": "تعديل سعر المنتج عند الاستلام",
    "supplier_receiving.service_price.edit": "تعديل سعر الخدمة عند الاستلام",
    "supplier_receiving.service.add": "إضافة خدمة للمنتج عند الاستلام",
    "fulfillment.stop.manage": "إدارة إيقافات خدمة العملاء",
};


function errorMessage(error) {
    if (error?.employeeV2CreatedAccount) {
        return "تم إنشاء حساب الدخول، لكن تعذر ربطه. حدّث الصفحة ثم اختر الحساب الجديد لإكمال الربط.";
    }
    if (error?.code === "employee_v2_viewer_permissions_unavailable") {
        return "تعذر التحقق من تصفير صلاحيات الحساب الجديد؛ لم يُنشأ الحساب حفاظًا على الأمان.";
    }
    const detail = error?.response?.data?.detail;
    const code = typeof detail === "string" ? detail : detail?.code;
    const messages = {
        employee_version_conflict: "تغيرت بيانات الموظف في جلسة أخرى. حدّث الصفحة ثم أعد المحاولة.",
        employee_account_has_existing_role: "حساب الدخول مرتبط بدور موظف آخر.",
        employee_account_linked_elsewhere: "حساب الدخول مرتبط بموظف آخر.",
        employee_account_link_required_before_role: "اربط حساب الدخول أولًا ثم عيّن الدور.",
        employee_account_link_required_before_password: "اربط حساب الدخول أولًا ثم غيّر كلمة المرور.",
        employee_login_account_not_available: "حساب الدخول غير متاح أو لا يتبع هذا المتجر.",
        employee_status_invalid: "حالة الموظف يجب أن تكون نشط أو إجازة بدون راتب أو موقوف.",
        employee_payroll_status_confirmation_required: "تعذر اعتماد تغيير حالة الراتب؛ أعد فتح الموظف وحاول مرة أخرى.",
        employee_payroll_status_effective_date_invalid: "تاريخ بدء الحالة غير صحيح.",
        employee_payroll_status_effective_date_future: "لا يمكن بدء إيقاف أو استئناف الراتب بتاريخ مستقبلي.",
        employee_payroll_return_before_leave: "تاريخ العودة يجب ألا يسبق بداية الإجازة أو الإيقاف.",
        employee_password_invalid: "كلمة المرور يجب أن تكون بين 6 و128 حرفًا.",
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
    const riyadhToday = useMemo(() => {
        const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
            timeZone: "Asia/Riyadh",
            year: "numeric",
            month: "2-digit",
            day: "2-digit",
        }).formatToParts(new Date()).map((part) => [part.type, part.value]));
        return `${parts.year}-${parts.month}-${parts.day}`;
    }, []);
    const [form, setForm] = useState(() => employee ? {
        name: employee.name || "",
        phone: employee.phone || "",
        contact_email: employee.contact_email || "",
        job_title: employee.job_title || "",
        department: employee.department || "",
        hire_date: employee.hire_date || "",
        status: employee.status || "inactive",
        status_effective_date: "",
        notes: employee.notes || "",
    } : EMPTY_FORM);
    const set = (key, value) => setForm((current) => ({ ...current, [key]: value }));
    const statusChanged = editing && form.status !== employee.status;
    const inputClass = "mt-1 h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm outline-none focus:border-emerald-500";
    return (
        <ModalShell title={editing ? "تعديل الموظف" : "إضافة موظف"} onClose={onClose} busy={busy} testId="employees-v2-employee-form-dialog">
            <form
                onSubmit={(event) => {
                    event.preventDefault();
                    if (!form.name.trim()) return toast.error("اسم الموظف مطلوب");
                    const payload = {
                        ...form,
                        name: form.name.trim(),
                        ...(editing ? { expected_version: employee.version } : {}),
                    };
                    if (statusChanged) payload.status_effective_date = form.status_effective_date || riyadhToday;
                    else delete payload.status_effective_date;
                    onSubmit(payload);
                }}
                className="max-h-[calc(95vh-65px)] overflow-y-auto p-5"
            >
                <div className="mb-4 rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-xs font-bold leading-6 text-emerald-950"><ShieldCheck className="ml-1 inline" /> عقد الراتب في ميزان 2 هو المصدر الوحيد للاحتساب. مبلغ الراتب والسلف والعهد والـLedger للقراءة فقط؛ تغيير الحالة هنا يوقف أو يستأنف احتساب الراتب.</div>
                <div className="grid gap-4 sm:grid-cols-2">
                    <label className="text-xs font-bold text-slate-600">اسم الموظف *<input autoFocus value={form.name} onChange={(event) => set("name", event.target.value)} maxLength={80} className={inputClass} data-testid="employees-v2-employee-name" /></label>
                    <label className="text-xs font-bold text-slate-600">رقم الجوال<input value={form.phone} onChange={(event) => set("phone", event.target.value)} maxLength={40} className={inputClass} dir="ltr" /></label>
                    <label className="text-xs font-bold text-slate-600">البريد للتواصل<input type="email" value={form.contact_email} onChange={(event) => set("contact_email", event.target.value)} maxLength={254} className={inputClass} dir="ltr" /></label>
                    <label className="text-xs font-bold text-slate-600">المسمى الوظيفي<input value={form.job_title} onChange={(event) => set("job_title", event.target.value)} maxLength={120} className={inputClass} /></label>
                    <label className="text-xs font-bold text-slate-600">القسم<input value={form.department} onChange={(event) => set("department", event.target.value)} maxLength={120} className={inputClass} /></label>
                    <label className="text-xs font-bold text-slate-600">تاريخ الانضمام<input type="date" value={form.hire_date} onChange={(event) => set("hire_date", event.target.value)} className={inputClass} dir="ltr" /></label>
                    <label className="text-xs font-bold text-slate-600 sm:col-span-2">الحالة<select value={form.status} onChange={(event) => set("status", event.target.value)} className={inputClass} data-testid="employees-v2-status-select"><option value="active">نشط — الراتب والدخول مفعّلان</option><option value="unpaid_leave">إجازة بدون راتب — يتوقف الراتب والدخول</option><option value="inactive">موقوف — يتوقف الراتب والدخول</option></select></label>
                </div>
                {statusChanged && <section className="mt-4 rounded-2xl border border-amber-300 bg-amber-50 p-4" data-testid="employees-v2-payroll-status-warning"><label className="block text-xs font-black text-amber-950">تاريخ سريان الحالة<input type="date" max={riyadhToday} value={form.status_effective_date || riyadhToday} onChange={(event) => set("status_effective_date", event.target.value)} className={inputClass} dir="ltr" data-testid="employees-v2-status-effective-date" /></label><p className="mt-3 text-xs font-bold leading-6 text-amber-900">{form.status === "active" ? "يعود احتساب الراتب والدخول من هذا اليوم فقط، ولا تُحتسب أيام الإجازة أو الإيقاف بأثر رجعي." : "يصبح هذا اليوم أول يوم غير مدفوع، ويتوقف احتساب الراتب والدخول حتى إعادة التفعيل."}</p></section>}
                <label className="mt-4 block text-xs font-bold text-slate-600">ملاحظات<textarea value={form.notes} onChange={(event) => set("notes", event.target.value)} maxLength={1000} rows={4} className="mt-1 w-full rounded-xl border border-slate-300 p-3 text-sm outline-none focus:border-emerald-500" /></label>
                <footer className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                    <button type="button" onClick={onClose} disabled={busy} className="rounded-xl border px-5 py-3 text-sm font-bold text-slate-700 disabled:opacity-40">إلغاء</button>
                    <button type="submit" disabled={busy} data-testid="employees-v2-employee-form-submit" className="rounded-xl bg-emerald-700 px-5 py-3 text-sm font-black text-white disabled:opacity-50">{busy ? "جارٍ الحفظ…" : editing ? "حفظ التعديلات" : "إضافة الموظف"}</button>
                </footer>
            </form>
        </ModalShell>
    );
}


function AccountModal({ employee, candidates, busy, onClose, onLink, onCreateAndLink, onUnlink, onPassword }) {
    const suggestedId = employee.account?.suggested_account?.id;
    const initialCandidate = candidates.find((row) => row.id === suggestedId) || candidates[0];
    const [selected, setSelected] = useState(initialCandidate?.id || "");
    const [creating, setCreating] = useState(!candidates.length);
    const [password, setPassword] = useState("");
    const [account, setAccount] = useState({
        name: employee.name || "",
        email: employee.contact_email || "",
        password: "",
    });
    const linked = Boolean(employee.account?.user_id);
    const setAccountField = (key, value) => setAccount((current) => ({ ...current, [key]: value }));
    const inputClass = "mt-1 h-11 w-full rounded-xl border border-slate-300 bg-white px-3 text-sm outline-none focus:border-violet-500";
    return (
        <ModalShell title="حساب دخول الموظف" onClose={onClose} busy={busy} testId="employees-v2-account-dialog">
            <div className="max-h-[calc(95vh-65px)] overflow-y-auto p-5">
                {linked ? (
                    <>
                        <div className={`rounded-2xl border p-4 ${employee.account.access_enabled ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}><div className="font-black">{employee.account.name || "حساب مرتبط"}</div><div className="mt-1 text-xs" dir="ltr">{employee.account.email}</div><div className="mt-2 text-xs font-black">{employee.account.access_enabled ? "الدخول مفعّل" : "الدخول موقوف"}</div></div>
                        <label className="mt-4 block text-xs font-bold text-slate-600">كلمة مرور جديدة<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={6} maxLength={128} className={inputClass} dir="ltr" data-testid="employees-v2-new-password" /></label>
                        <button type="button" onClick={() => onPassword(password)} disabled={busy || password.length < 6} data-testid="employees-v2-reset-password" className="mt-3 inline-flex items-center gap-2 rounded-xl bg-slate-800 px-4 py-2.5 text-sm font-black text-white disabled:opacity-40"><Key /> تغيير كلمة المرور</button>
                        <p className="mt-5 text-xs leading-6 text-slate-500">فصل الحساب يوقف دخوله ويعطّل دوره فورًا، ولا يحذف الحساب أو تاريخ الموظف.</p>
                        <div className="mt-3 flex justify-end gap-2"><button type="button" onClick={onClose} disabled={busy} className="rounded-xl border px-4 py-2.5 text-sm font-bold">إلغاء</button><button type="button" onClick={onUnlink} disabled={busy} data-testid="employees-v2-unlink-account" className="rounded-xl bg-rose-600 px-4 py-2.5 text-sm font-black text-white disabled:opacity-50">فصل الحساب وإيقافه</button></div>
                    </>
                ) : candidates.length && !creating ? (
                    <>
                        <p className="mb-3 text-sm leading-7 text-slate-600">اختر الحساب الصحيح يدويًا. حساب المالك لا يظهر ولا يمكن ربطه بموظف.</p>
                        <label className="text-xs font-bold text-slate-600">الحساب<select value={selected} onChange={(event) => setSelected(event.target.value)} className="mt-1 h-12 w-full rounded-xl border px-3 text-sm" data-testid="employees-v2-account-select">{candidates.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name || "حساب"} — {candidate.email}{candidate.has_existing_role ? " · لديه دور محفوظ" : ""}</option>)}</select></label>
                        <button type="button" onClick={() => setCreating(true)} disabled={busy} className="mt-3 text-xs font-black text-violet-700 underline">إنشاء حساب دخول جديد بدلًا من ذلك</button>
                        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onClose} disabled={busy} className="rounded-xl border px-4 py-2.5 text-sm font-bold">إلغاء</button><button type="button" onClick={() => onLink(selected)} disabled={busy || !selected} data-testid="employees-v2-link-account" className="rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-black text-white disabled:opacity-50">ربط الحساب</button></div>
                    </>
                ) : (
                    <form onSubmit={(event) => {
                        event.preventDefault();
                        if (!account.name.trim()) return toast.error("اسم حساب الدخول مطلوب");
                        if (!account.email.trim()) return toast.error("البريد الإلكتروني مطلوب");
                        if (account.password.length < 6) return toast.error("كلمة المرور يجب أن تكون 6 أحرف على الأقل");
                        onCreateAndLink({ ...account, name: account.name.trim(), email: account.email.trim() });
                    }}>
                        <div className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 text-xs font-bold leading-6 text-emerald-950"><ShieldCheck className="ml-1 inline" /> سيُنشأ حساب Viewer بصفر صلاحيات قديمة، ثم يأخذ دوره التشغيلي من صفحة الموظف فقط.</div>
                        <div className="mt-4 grid gap-4 sm:grid-cols-2">
                            <label className="text-xs font-bold text-slate-600">اسم حساب الدخول *<input value={account.name} onChange={(event) => setAccountField("name", event.target.value)} maxLength={80} className={inputClass} data-testid="employees-v2-account-name" /></label>
                            <label className="text-xs font-bold text-slate-600">البريد الإلكتروني *<input type="email" value={account.email} onChange={(event) => setAccountField("email", event.target.value)} className={inputClass} dir="ltr" data-testid="employees-v2-account-email" /></label>
                            <label className="text-xs font-bold text-slate-600 sm:col-span-2">كلمة مرور مؤقتة *<input type="password" value={account.password} onChange={(event) => setAccountField("password", event.target.value)} minLength={6} className={inputClass} dir="ltr" data-testid="employees-v2-account-password" /></label>
                        </div>
                        {candidates.length > 0 && <button type="button" onClick={() => setCreating(false)} disabled={busy} className="mt-3 text-xs font-black text-violet-700 underline">العودة لاختيار حساب متاح</button>}
                        <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onClose} disabled={busy} className="rounded-xl border px-4 py-2.5 text-sm font-bold">إلغاء</button><button type="submit" disabled={busy} data-testid="employees-v2-create-link-account" className="rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-black text-white disabled:opacity-50">إنشاء الحساب وربطه</button></div>
                    </form>
                )}
            </div>
        </ModalShell>
    );
}


function RoleModal({ employee, management, busy, onClose, onSubmit }) {
    const roles = useMemo(() => Object.keys(management.role_catalog || {}).filter((key) => !["owner", "ai_product_optimizer"].includes(key)), [management.role_catalog]);
    const initialRole = employee.operational_role?.role_key || roles[0] || "";
    const existingRole = employee.operational_role || {};
    const [roleKey, setRoleKey] = useState(initialRole);
    const [enabled, setEnabled] = useState(employee.status !== "active" ? true : employee.operational_role?.enabled !== false);
    const [extraPermissions, setExtraPermissions] = useState(() => initialRole === existingRole.role_key ? existingRole.extra_permissions || [] : []);
    const [deniedPermissions, setDeniedPermissions] = useState(() => initialRole === existingRole.role_key ? existingRole.denied_permissions || [] : []);
    const permissions = management.role_catalog?.[roleKey] || [];
    const preserveExistingScope = roleKey === existingRole.role_key;
    const optionalPermissions = Object.keys(OPTIONAL_PERMISSION_LABELS).filter((permission) => (
        (management.permissions || []).includes(permission) && !permissions.includes(permission)
    ));

    function changeRole(nextRole) {
        setRoleKey(nextRole);
        if (nextRole === existingRole.role_key) {
            setExtraPermissions(existingRole.extra_permissions || []);
            setDeniedPermissions(existingRole.denied_permissions || []);
        } else {
            setExtraPermissions([]);
            setDeniedPermissions([]);
        }
    }

    function toggleExtra(permission, selected) {
        setExtraPermissions((current) => selected
            ? [...new Set([...current, permission])]
            : current.filter((value) => value !== permission));
        if (selected) setDeniedPermissions((current) => current.filter((value) => value !== permission));
    }

    return (
        <ModalShell title="الدور والصلاحيات" onClose={onClose} busy={busy} testId="employees-v2-role-dialog">
            <div className="max-h-[calc(95vh-65px)] overflow-y-auto p-5">
                <div className="rounded-2xl border border-violet-200 bg-violet-50 p-4 text-sm text-violet-950"><ShieldCheck className="ml-1 inline" /> الدور مرتبط بحساب هذا الموظف وحده. إذا كان الموظف موقوفًا تُحفظ اختياراته لكن يبقى الوصول معطّلًا حتى إعادة تفعيله.</div>
                <label className="mt-4 block text-xs font-bold text-slate-600">الدور التشغيلي<select value={roleKey} onChange={(event) => changeRole(event.target.value)} className="mt-1 h-12 w-full rounded-xl border px-3 text-sm" data-testid="employees-v2-role-select">{roles.map((key) => <option key={key} value={key}>{management.role_labels?.[key] || ROLE_FALLBACK_LABELS[key] || key}</option>)}</select></label>
                {ROLE_DESCRIPTIONS[roleKey] && <div className="mt-3 rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-xs font-bold leading-6 text-emerald-950" data-testid="employees-v2-role-description">{ROLE_DESCRIPTIONS[roleKey]}</div>}
                <label className="mt-3 flex items-center gap-2 rounded-xl border p-3 text-sm font-bold"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> الدور مفعّل عند تفعيل الموظف</label>
                <section className="mt-4 rounded-2xl border bg-slate-50 p-4"><h3 className="text-sm font-black">الصلاحيات المشمولة ({numberFormatter.format(permissions.length)})</h3><div className="mt-3 flex flex-wrap gap-2">{permissions.map((permission) => <span key={permission} className="rounded-lg border border-violet-200 bg-white px-2 py-1 text-[11px] font-bold text-violet-900" dir="ltr">{permission}</span>)}</div></section>
                {optionalPermissions.length > 0 && <section className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 p-4" data-testid="employees-v2-optional-permissions"><h3 className="text-sm font-black text-amber-950">صلاحيات إضافية للاختبار والتشغيل</h3><p className="mt-1 text-xs font-bold leading-5 text-amber-800">لا تُمنح تلقائيًا. فعّل فقط ما يحتاجه هذا الموظف.</p><div className="mt-3 space-y-2">{optionalPermissions.map((permission) => <label key={permission} className="flex items-start gap-2 rounded-xl border border-amber-200 bg-white p-3 text-sm font-bold text-slate-800"><input type="checkbox" checked={extraPermissions.includes(permission)} onChange={(event) => toggleExtra(permission, event.target.checked)} className="mt-1 h-4 w-4 accent-violet-700" /><span>{OPTIONAL_PERMISSION_LABELS[permission]}<span className="mt-1 block text-[10px] font-semibold text-slate-400" dir="ltr">{permission}</span></span></label>)}</div></section>}
                <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onClose} disabled={busy} className="rounded-xl border px-4 py-2.5 text-sm font-bold">إلغاء</button><button type="button" onClick={() => onSubmit({ role_key: roleKey, enabled, extra_permissions: extraPermissions, denied_permissions: deniedPermissions, warehouse_ids: preserveExistingScope ? existingRole.warehouse_ids || [] : [], workplace_warehouse_id: preserveExistingScope ? existingRole.workplace_warehouse_id || null : null, fulfillment_responsibilities: preserveExistingScope ? existingRole.fulfillment_responsibilities || [] : [] })} disabled={busy || !roleKey} data-testid="employees-v2-role-submit" className="rounded-xl bg-violet-700 px-4 py-2.5 text-sm font-black text-white disabled:opacity-50">حفظ الدور</button></div>
            </div>
        </ModalShell>
    );
}


function MobileAppPermissionsModal({ employee, management, busy, onClose, onSubmit }) {
    const access = employee.mobile_app_access || {};
    const groups = management.mobile_app_permission_catalog || [];
    const [enabled, setEnabled] = useState(access.enabled !== false);
    const [selected, setSelected] = useState(() => access.stored_permissions || access.permissions || []);
    const mezanPermissionCount = employee.operational_role?.effective_permissions?.length || 0;

    function toggle(permission, checked) {
        setSelected((current) => {
            const next = new Set(current);
            if (checked) next.add(permission.key);
            else {
                next.delete(permission.key);
                groups.forEach((group) => group.permissions?.forEach((item) => {
                    if (item.requires === permission.key) next.delete(item.key);
                }));
            }
            return [...next];
        });
    }

    return (
        <ModalShell title="صلاحيات تطبيق AMASI فقط" onClose={onClose} busy={busy} testId="employees-v2-mobile-app-permissions-dialog">
            <div className="max-h-[calc(95vh-65px)] overflow-y-auto p-5">
                <div className="rounded-2xl border border-sky-200 bg-sky-50 p-4 text-sm font-bold leading-7 text-sky-950">
                    <DeviceMobile className="ml-1 inline" /> هذه الصلاحيات للتطبيق فقط ولا تمنح أي صفحة أو عملية في موقع ميزان. صلاحيات ميزان الحالية للموظف: <strong>{numberFormatter.format(mezanPermissionCount)}</strong> ولن تتغير عند الحفظ.
                </div>
                <label className="mt-4 flex items-center gap-2 rounded-xl border p-3 text-sm font-black"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} data-testid="employees-v2-mobile-app-enabled" /> تفعيل الوصول المحدد داخل التطبيق</label>
                <div className="mt-4 space-y-4">
                    {groups.map((group) => (
                        <section key={group.key} className="rounded-2xl border bg-slate-50 p-4">
                            <h3 className="text-sm font-black text-slate-950">{group.label}</h3>
                            <div className="mt-3 grid gap-2 sm:grid-cols-2">
                                {(group.permissions || []).map((permission) => {
                                    const parentMissing = permission.requires && !selected.includes(permission.requires);
                                    return <label key={permission.key} className={`flex items-start gap-2 rounded-xl border bg-white p-3 text-sm font-bold ${parentMissing ? "opacity-45" : ""}`}>
                                        <input type="checkbox" checked={selected.includes(permission.key)} disabled={busy || parentMissing} onChange={(event) => toggle(permission, event.target.checked)} className="mt-1 h-4 w-4 accent-sky-700" data-testid={`mobile-app-permission-${permission.key}`} />
                                        <span>{permission.label}<span className="mt-1 block text-[10px] font-semibold text-slate-400" dir="ltr">{permission.key}</span></span>
                                    </label>;
                                })}
                            </div>
                        </section>
                    ))}
                </div>
                <p className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs font-bold leading-6 text-amber-900">أي صفحة جديدة تضاف لاحقًا لن تظهر لهذا الموظف تلقائيًا؛ تبقى للمدير فقط حتى تمنحها له من هنا.</p>
                <div className="mt-5 flex justify-end gap-2"><button type="button" onClick={onClose} disabled={busy} className="rounded-xl border px-4 py-2.5 text-sm font-bold">إلغاء</button><button type="button" onClick={() => onSubmit({ enabled, permissions: selected })} disabled={busy} data-testid="employees-v2-mobile-app-permissions-submit" className="rounded-xl bg-sky-700 px-4 py-2.5 text-sm font-black text-white disabled:opacity-50">حفظ صلاحيات التطبيق</button></div>
            </div>
        </ModalShell>
    );
}


function EventsModal({ employee, items, loading, onClose }) {
    return (
        <ModalShell title={`سجل نشاط ${employee?.name || "الموظف"}`} onClose={onClose} testId="employees-v2-events-dialog">
            <div className="max-h-[70vh] overflow-y-auto p-5">
                {loading && <div className="py-10 text-center text-sm font-bold text-slate-500">جارٍ تحميل السجل…</div>}
                {!loading && !items.length && <div className="py-10 text-center text-sm text-slate-400">لا يوجد نشاط مسجل.</div>}
                <div className="space-y-3">{items.map((event) => <article key={event.id} className="rounded-xl border bg-slate-50 p-3 text-xs"><div className="font-black text-slate-900"><CheckCircle className="ml-1 inline text-emerald-600" />{EVENT_LABELS[event.event_type] || event.event_type}</div><div className="mt-1 text-slate-500">{event.actor_name || event.actor_id} · {event.occurred_at}</div>{event.metadata?.changed_fields?.length > 0 && <div className="mt-2 font-bold text-violet-700">الحقول: {event.metadata.changed_fields.join(" · ")}</div>}{(event.before || event.after) && <details className="mt-2"><summary className="cursor-pointer font-black text-slate-700">عرض البيانات قبل وبعد</summary><div className="mt-2 grid gap-2 sm:grid-cols-2"><pre className="overflow-auto rounded-lg bg-white p-2 text-[10px]" dir="ltr">{JSON.stringify(event.before, null, 2)}</pre><pre className="overflow-auto rounded-lg bg-white p-2 text-[10px]" dir="ltr">{JSON.stringify(event.after, null, 2)}</pre></div></details>}</article>)}</div>
            </div>
        </ModalShell>
    );
}


function EmployeeCard({ employee, management, onEdit, onAccount, onRole, onMobileAppPermissions, onEvents }) {
    const linked = Boolean(employee.account?.user_id);
    const role = employee.operational_role || {};
    const salary = employee.salary_contract?.monthly_amount;
    const roleLabel = role.role_key ? management.role_labels?.[role.role_key] || ROLE_FALLBACK_LABELS[role.role_key] || role.role_key : "غير محدد";
    const statusLabel = employee.status === "active" ? "نشط" : employee.status === "unpaid_leave" ? "إجازة بدون راتب" : "موقوف";
    const statusClass = employee.status === "active" ? "bg-emerald-100 text-emerald-800" : employee.status === "unpaid_leave" ? "bg-amber-100 text-amber-900" : "bg-rose-100 text-rose-800";
    return (
        <article className="rounded-3xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5" data-testid="employees-v2-employee-card">
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h2 className="truncate text-lg font-black text-slate-950">{employee.name}</h2><span className={`rounded-full px-2.5 py-1 text-[11px] font-black ${statusClass}`}>{statusLabel}</span><span className="rounded-full bg-slate-100 px-2 py-1 text-[10px] font-bold text-slate-600">{employee.migrated ? "مرحّل" : "جديد"}</span></div><p className="mt-2 text-xs text-slate-500">{[employee.job_title, employee.department].filter(Boolean).join(" · ") || "لم يحدد المسمى أو القسم"}</p></div>
                <button type="button" onClick={onEdit} className="shrink-0 rounded-xl border p-2.5 text-slate-700" aria-label="تعديل الموظف"><PencilSimple /></button>
            </div>
            <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                <div className="rounded-2xl border bg-slate-50 p-3"><div className="text-[10px] font-bold text-slate-500">عقد راتب ميزان 2</div><div className="mt-1 truncate text-sm font-black" dir="ltr">{salary == null ? "—" : moneyFormatter.format(salary)}</div></div>
                <div className="rounded-2xl border bg-slate-50 p-3"><div className="text-[10px] font-bold text-slate-500">حساب الدخول</div><div className={`mt-1 truncate text-sm font-black ${linked && employee.account.access_enabled ? "text-emerald-700" : "text-slate-700"}`}>{!linked ? "غير مرتبط" : employee.account.access_enabled ? "مفعّل" : "موقوف"}</div></div>
                <div className="rounded-2xl border bg-slate-50 p-3"><div className="text-[10px] font-bold text-slate-500">الدور</div><div className="mt-1 truncate text-sm font-black">{roleLabel}</div></div>
                <div className="rounded-2xl border bg-slate-50 p-3"><div className="text-[10px] font-bold text-slate-500">الصلاحيات</div><div className="mt-1 text-sm font-black">{numberFormatter.format(role.effective_permissions?.length || 0)}</div></div>
            </div>
            {employee.account?.suggested_account && !linked && <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-900">اقتراح يحتاج اعتمادًا يدويًا: {employee.account.suggested_account.name || employee.account.suggested_account.email}</div>}
            <div className="mt-4 grid grid-cols-2 gap-2"><button type="button" onClick={onAccount} className="inline-flex items-center justify-center gap-1 rounded-xl border px-3 py-2.5 text-xs font-black text-violet-700"><LinkSimple />{linked ? "الحساب وكلمة المرور" : "ربط حساب"}</button><button type="button" onClick={linked ? onRole : onAccount} className="inline-flex items-center justify-center gap-1 rounded-xl border px-3 py-2.5 text-xs font-black text-emerald-700"><ShieldCheck />{linked ? "صلاحيات ميزان" : "اربط الحساب أولًا"}</button><button type="button" onClick={linked ? onMobileAppPermissions : onAccount} className="inline-flex items-center justify-center gap-1 rounded-xl border px-3 py-2.5 text-xs font-black text-sky-700"><DeviceMobile />{linked ? `صلاحيات التطبيق (${numberFormatter.format(employee.mobile_app_access?.permissions?.length || 0)})` : "اربط الحساب أولًا"}</button><button type="button" onClick={onEvents} className="inline-flex items-center justify-center gap-1 rounded-xl border px-3 py-2.5 text-xs font-black text-slate-700"><ClockCounterClockwise />سجل النشاط</button></div>
        </article>
    );
}


export default function EmployeesV2ManagementWorkspace() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [modal, setModal] = useState(null);
    const [events, setEvents] = useState({ loading: false, items: [] });
    const [query, setQuery] = useState("");
    const [statusFilter, setStatusFilter] = useState("all");
    const [roleFilter, setRoleFilter] = useState("all");
    const [accountFilter, setAccountFilter] = useState("all");
    const mutationInFlight = useRef(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            setData(await getEmployeesV2Management());
        } catch (error) {
            toast.error(errorMessage(error));
        } finally {
            setLoading(false);
        }
    }, []);
    useEffect(() => { load(); }, [load]);

    const management = data?.management || {};
    const employees = useMemo(() => {
        const needle = query.trim().toLocaleLowerCase("ar");
        return (management.employees || []).filter((employee) => {
            const matchesQuery = !needle || `${employee.name || ""} ${employee.phone || ""} ${employee.contact_email || ""} ${employee.job_title || ""} ${employee.department || ""} ${employee.account?.email || ""}`.toLocaleLowerCase("ar").includes(needle);
            const matchesStatus = statusFilter === "all" || employee.status === statusFilter;
            const matchesRole = roleFilter === "all" || (roleFilter === "assigned" ? Boolean(employee.operational_role?.role_key) : roleFilter === "unassigned" ? !employee.operational_role?.role_key : employee.operational_role?.role_key === roleFilter);
            const matchesAccount = accountFilter === "all" || (accountFilter === "linked" ? Boolean(employee.account?.user_id) : !employee.account?.user_id);
            return matchesQuery && matchesStatus && matchesRole && matchesAccount;
        });
    }, [accountFilter, management.employees, query, roleFilter, statusFilter]);

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
            toast.error(errorMessage(error));
        } finally {
            mutationInFlight.current = false;
            setBusy(false);
        }
    }

    async function openEvents(employee) {
        setModal({ type: "events", employee });
        setEvents({ loading: true, items: [] });
        try {
            const result = await getEmployeesV2Events(employee.id);
            setEvents({ loading: false, items: result.items || [] });
        } catch (error) {
            setEvents({ loading: false, items: [] });
            toast.error(errorMessage(error));
        }
    }

    const selectedEmployee = modal?.employee;
    const selectClass = "h-11 rounded-xl border bg-white px-3 text-sm font-bold text-slate-700";
    return (
        <div className="space-y-5" data-testid="employees-v2-management">
            <section className="overflow-hidden rounded-3xl border border-emerald-200 bg-white shadow-sm">
                <div className="bg-gradient-to-l from-slate-950 via-emerald-950 to-slate-950 p-5 text-white sm:p-6"><div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between"><div><div className="flex items-center gap-2 text-sm font-black text-emerald-200"><IdentificationCard size={24} weight="duotone" /> Mezan Employee OS</div><h1 className="mt-2 text-2xl font-black sm:text-3xl">إدارة الموظفين</h1><p className="mt-2 max-w-3xl text-sm leading-7 text-slate-200">إدارة الهوية، الحالة، حساب الدخول، كلمة المرور، الدور والصلاحيات لجميع الموظفين من مكان واحد.</p></div><div className="flex gap-2"><button type="button" onClick={load} disabled={loading || busy} className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl border border-white/20 bg-white/10 px-4 py-3 text-sm font-black disabled:opacity-40"><ArrowClockwise className={loading ? "animate-spin" : ""} /> تحديث</button><button type="button" onClick={() => setModal({ type: "form" })} disabled={loading || busy || !management.can_create_employee} data-testid="employees-v2-add-employee" className="inline-flex flex-1 items-center justify-center gap-2 rounded-xl bg-emerald-300 px-4 py-3 text-sm font-black text-emerald-950 disabled:opacity-40"><UserPlus size={20} /> إضافة موظف</button></div></div></div>
                <div className="border-t border-emerald-900 bg-emerald-950 px-5 py-3 text-xs font-bold text-emerald-100"><ShieldCheck className="ml-1 inline" /> مصدر رواتب الموظفين: عقود ميزان 2، والاعتماد على رواتب الموظفين القديمة: 0. الإجازة أو الإيقاف يوقفان الاحتساب، والتفعيل يعيده من تاريخ العودة دون أثر رجعي.</div>
            </section>

            <section className="grid grid-cols-2 gap-3 xl:grid-cols-4"><div className="rounded-2xl border bg-white p-4"><UsersThree className="text-emerald-700" size={22} /><div className="mt-2 text-xs font-bold text-slate-500">إجمالي الموظفين</div><div className="mt-1 text-2xl font-black">{numberFormatter.format(management.managed_count || 0)}</div></div><div className="rounded-2xl border bg-white p-4"><UserCircle className="text-sky-700" size={22} /><div className="mt-2 text-xs font-bold text-slate-500">نشطون</div><div className="mt-1 text-2xl font-black">{numberFormatter.format(management.active_count || 0)}</div></div><div className="rounded-2xl border bg-white p-4"><LinkSimple className="text-violet-700" size={22} /><div className="mt-2 text-xs font-bold text-slate-500">حسابات مرتبطة</div><div className="mt-1 text-2xl font-black">{numberFormatter.format(management.linked_account_count || 0)}</div></div><div className="rounded-2xl border bg-white p-4"><CurrencyCircleDollar className="text-rose-700" size={22} /><div className="mt-2 text-xs font-bold text-slate-500">كتابات مالية</div><div className="mt-1 text-2xl font-black text-emerald-800">0</div></div></section>

            <section className="rounded-2xl border bg-white p-4 shadow-sm"><div className="relative"><MagnifyingGlass className="absolute right-3 top-3 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="ابحث بالاسم أو الجوال أو البريد أو المسمى…" className="h-11 w-full rounded-xl border pr-10 pl-4 text-sm outline-none focus:border-emerald-500" data-testid="employees-v2-search" /></div><div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-3"><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} className={selectClass} data-testid="employees-v2-status-filter"><option value="all">كل الحالات</option><option value="active">نشط</option><option value="unpaid_leave">إجازة بدون راتب</option><option value="inactive">موقوف</option></select><select value={accountFilter} onChange={(event) => setAccountFilter(event.target.value)} className={selectClass}><option value="all">كل الحسابات</option><option value="linked">حساب مرتبط</option><option value="unlinked">بدون حساب</option></select><select value={roleFilter} onChange={(event) => setRoleFilter(event.target.value)} className={selectClass}><option value="all">كل الأدوار</option><option value="assigned">له دور</option><option value="unassigned">بدون دور</option>{Object.keys(management.role_catalog || {}).filter((key) => !["owner", "ai_product_optimizer"].includes(key)).map((key) => <option key={key} value={key}>{management.role_labels?.[key] || ROLE_FALLBACK_LABELS[key] || key}</option>)}</select></div></section>

            {loading && <div className="rounded-3xl border bg-white p-12 text-center font-bold text-slate-500">جارٍ تحميل إدارة الموظفين…</div>}
            {!loading && !employees.length && <div className="rounded-3xl border border-dashed bg-white p-12 text-center text-slate-500">لا يوجد موظفون مطابقون للبحث والتصفية.</div>}
            {!loading && employees.length > 0 && <section className="grid gap-3 xl:grid-cols-2">{employees.map((employee) => <EmployeeCard key={employee.id} employee={employee} management={management} onEdit={() => setModal({ type: "form", employee })} onAccount={() => setModal({ type: "account", employee })} onRole={() => setModal({ type: "role", employee })} onMobileAppPermissions={() => setModal({ type: "mobile-app-permissions", employee })} onEvents={() => openEvents(employee)} />)}</section>}

            {modal?.type === "form" && <EmployeeFormModal employee={selectedEmployee} busy={busy} onClose={() => setModal(null)} onSubmit={(payload) => mutate(() => selectedEmployee ? updateEmployeesV2(selectedEmployee.id, payload) : createEmployeesV2(payload), selectedEmployee ? "تم تحديث الموظف والراتب والوصول من التاريخ المحدد" : "تمت إضافة الموظف دون إنشاء عقد راتب")} />}
            {modal?.type === "account" && selectedEmployee && <AccountModal employee={selectedEmployee} candidates={management.login_account_candidates || []} busy={busy} onClose={() => setModal(null)} onLink={(accountId) => mutate(() => linkEmployeesV2Account(selectedEmployee.id, accountId), "تم ربط حساب الدخول بالحالة الحالية للموظف")} onCreateAndLink={(payload) => mutate(() => createAndLinkEmployeesV2Account(selectedEmployee.id, payload), "تم إنشاء حساب الدخول وربطه بصفر صلاحيات قديمة")} onUnlink={() => mutate(() => unlinkEmployeesV2Account(selectedEmployee.id), "تم فصل الحساب وإيقاف وصوله فورًا")} onPassword={(password) => mutate(() => resetEmployeesV2AccountPassword(selectedEmployee.id, password), "تم تغيير كلمة مرور الموظف دون إظهارها في السجل")} />}
            {modal?.type === "role" && selectedEmployee && <RoleModal employee={selectedEmployee} management={management} busy={busy} onClose={() => setModal(null)} onSubmit={(payload) => mutate(() => assignEmployeesV2Role(selectedEmployee.id, payload), "تم حفظ الدور والصلاحيات")} />}
            {modal?.type === "mobile-app-permissions" && selectedEmployee && <MobileAppPermissionsModal employee={selectedEmployee} management={management} busy={busy} onClose={() => setModal(null)} onSubmit={(payload) => mutate(() => assignEmployeesV2MobileAppPermissions(selectedEmployee.id, payload), "تم حفظ صلاحيات التطبيق فقط دون تغيير صلاحيات ميزان")} />}
            {modal?.type === "events" && <EventsModal employee={selectedEmployee} items={events.items} loading={events.loading} onClose={() => setModal(null)} />}
        </div>
    );
}
