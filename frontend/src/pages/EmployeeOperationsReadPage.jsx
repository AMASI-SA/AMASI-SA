import { useEffect, useState } from "react";
import api from "../lib/api";

const reports = {
    "employees.staff": ["إدارة الموظفين", [
        ["display_name", "الموظف"], ["job_title", "المسمى الوظيفي"], ["department", "القسم"], ["status", "الحالة"],
    ]],
    "employees.drivers": ["موصلو المتجر", [
        ["name", "الموصل"], ["city", "المدينة"], ["shipping_company", "شركة الشحن"],
        ["delivery_fee", "أجرة التوصيل (ر.س)"], ["status", "الحالة"],
    ]],
    "employees.migration": ["تقرير الترحيل والرواتب", [
        ["name", "الموظف"], ["status", "حالة الترحيل"], ["salary", "الراتب الشهري (ر.س)"], ["due", "الرصيد المستحق (ر.س)"],
    ]],
    "finance.collections": ["مراجعة تحصيلات الموصلين", [
        ["order", "رقم الطلب"], ["driver", "الموصل"], ["method", "طريقة الدفع"], ["amount", "المبلغ (ر.س)"], ["date", "تاريخ التقديم"],
    ]],
    "finance.settlements": ["تسويات موصلي المتجر", [
        ["driver_name_snapshot", "الموصل"], ["settlement_type", "نوع التسوية"], ["amount", "المبلغ (ر.س)"],
        ["status", "الحالة"], ["created_at", "التاريخ"],
    ]],
    "shipping.settings": ["إعدادات شركات الشحن", [
        ["name", "شركة الشحن"], ["cost_per_order", "تكلفة الشحنة (ر.س)"], ["vat_percent", "الضريبة (%)"],
        ["payment_mode", "طريقة السداد"], ["cod_fee_fixed_per_order", "رسم التحصيل الثابت (ر.س)"],
    ]],
};
const eventColumns = [["event_type", "الحدث"], ["actor_name", "سجّله"], ["occurred_at", "التاريخ"]];
const values = {
    active: "نشط", inactive: "غير نشط", suspended: "موقوف", terminated: "منتهي الخدمة",
    blocked: "يتطلب مراجعة", ready: "جاهز", already_migrated: "تم الترحيل", stopped: "موقوف",
    cash: "نقد", bank_transfer: "تحويل بنكي", card_terminal: "شبكة", deferred: "آجل", prepaid: "مقدم",
    pending_driver_confirmation: "بانتظار تأكيد الموصل", posted: "مسجّلة", rejected: "مرفوضة",
    cod_remittance: "توريد تحصيل", earning_payment: "دفع مستحق توصيل", net_settlement: "تسوية صافية",
    employee_created: "إضافة موظف", employee_updated: "تحديث الموظف", employee_payroll_status_changed: "تغيير حالة الراتب",
    employee_salary_changed: "تغيير الراتب", employee_salary_contract_created: "إضافة عقد راتب",
    employee_account_linked: "ربط حساب", employee_account_unlinked: "فصل حساب",
    employee_role_assigned: "تعيين دور", employee_mobile_app_permissions_assigned: "تحديث صلاحيات التطبيق",
    employee_account_password_reset: "تغيير كلمة مرور الحساب", employee_shadow_migrated: "ترحيل الموظف",
    salary_contract_synchronized: "مزامنة عقد الراتب",
};
const translated = new Set(["status", "event_type", "payment_mode", "method", "settlement_type"]);
const dates = new Set(["created_at", "date", "occurred_at"]);
const dateFormatter = new Intl.DateTimeFormat("ar-SA-u-ca-gregory", { dateStyle: "medium", timeZone: "Asia/Riyadh" });
const amounts = new Set(["delivery_fee", "cost_per_order", "cod_fee_fixed_per_order", "amount", "due", "salary"]);
const moneyFormatter = new Intl.NumberFormat("ar-SA", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function display(field, value) {
    if (value == null || value === "") return "—";
    if (translated.has(field)) return values[value] || String(value);
    if (amounts.has(field) && Number.isFinite(Number(value))) return moneyFormatter.format(Number(value));
    if (dates.has(field) && Number.isFinite(Date.parse(value))) return dateFormatter.format(new Date(value));
    return String(value);
}

function ReadTable({ pageId, employee, onEmployee }) {
    const [page, setPage] = useState(1);
    const [result, setResult] = useState(null);
    const [error, setError] = useState("");
    const [retry, setRetry] = useState(0);
    const path = employee ? `employees.staff/${encodeURIComponent(employee.id)}/events` : pageId;
    const columns = employee ? eventColumns : reports[pageId][1];

    useEffect(() => {
        const controller = new AbortController();
        setResult(null);
        setError("");
        api.get(`/web-page-access-v1/employee-tools/${path}`, { params: { page }, signal: controller.signal })
            .then(({ data }) => { if (!controller.signal.aborted) setResult(data); })
            .catch((err) => {
                if (!controller.signal.aborted) setError(err.response?.status === 403 ? "هذه الصفحة غير متاحة لحسابك." : "تعذّر تحميل البيانات.");
            });
        return () => controller.abort();
    }, [path, page, retry]);

    if (error) return <p role="alert">{error} <button className="underline" onClick={() => setRetry(retry + 1)}>إعادة المحاولة</button></p>;
    if (!result) return <p role="status">جارٍ التحميل…</p>;
    return <>
        <div className="overflow-x-auto">
            <table className="w-full text-right text-sm">
                <thead className="border-b bg-slate-50"><tr>{columns.map(([id, label]) => <th key={id} scope="col" className="p-3">{label}</th>)}
                    {onEmployee && <th scope="col" className="p-3">السجل</th>}</tr></thead>
                <tbody>{result.items.map((row, index) => <tr key={row.id || row.name || index} className="border-b">
                    {columns.map(([field]) => <td key={field} className="p-3">{display(field, row[field])}</td>)}
                    {onEmployee && <td className="p-3"><button className="text-emerald-800 underline" onClick={() => onEmployee(row)}>سجل الموظف</button></td>}
                </tr>)}</tbody>
            </table>
        </div>
        {!result.items.length && <p className="p-4 text-center">لا توجد سجلات.</p>}
        {(page > 1 || result.has_next) && <nav className="mt-4 flex items-center gap-4" aria-label="صفحات السجل">
            <button disabled={page === 1} onClick={() => setPage(page - 1)}>السابق</button>
            <span>صفحة {page}</span><button disabled={!result.has_next} onClick={() => setPage(page + 1)}>التالي</button>
        </nav>}
    </>;
}

function OperationsReport({ pageId }) {
    const [employee, setEmployee] = useState(null);
    const title = employee ? `سجل الموظف · ${employee.display_name}` : reports[pageId][0];
    return <section dir="rtl" className="space-y-4 rounded-2xl border bg-white p-5">
        <h1 className="text-2xl font-bold">{title}</h1>
        {employee && <button className="underline" onClick={() => setEmployee(null)}>العودة إلى الموظفين</button>}
        <ReadTable key={employee?.id || pageId} pageId={pageId} employee={employee}
            onEmployee={pageId === "employees.staff" && !employee ? setEmployee : null} />
    </section>;
}

export default function EmployeeOperationsReadPage({ pageId }) {
    return Object.hasOwn(reports, pageId) ? <OperationsReport key={pageId} pageId={pageId} /> : null;
}
