import { Link } from "react-router-dom";
import { StatusBadge } from "./AccountingUI";
import { userCanAccessAccounting } from "./accountingPages";

const ROOT = "/integrations-v2?workspace=financial&page=";
const AREAS = [
    ["لوحة المحاسبة", "home", "accounting.home.view", "ملخص التشغيل والمهام الحالية", "readonly"],
    ["الحركات اليومية", "financial-movements", "accounting.movements.view", "دليل البنك والحالة والمرجع؛ التصنيف إجراء مستقل", "readonly"],
    ["التسويات", "settlements", "accounting.settlements.view", "مزود الدفع والبنك والملف وحالة المراجعة", "readonly"],
    ["الشحن والتحصيل", "shipping-cod", "accounting.shipping.view", "شركات الشحن والموصلون؛ لا أرصدة من عينات المرشحين", "readonly"],
    ["الموردون والمشتريات", "journals-reports&report_view=supplier", "accounting.journals_reports.view", "كشف القيود فقط؛ الفاتورة والسداد وتخصيص المقدّم بانتظار العقد الموحد", "unavailable"],
    ["المخزون المحاسبي", "inventory-purchases", "accounting.inventory.view", "قيمة المخزون منفصلة عن تشغيل المستودع؛ التقييم التفصيلي غير متاح", "unavailable"],
    ["الموظفون والرواتب", "journals-reports&report_view=employee", "accounting.journals_reports.view", "كشف القيود فقط؛ الراتب الحالي وتاريخه بانتظار عقد الموظف الموحد", "unavailable"],
    ["الإعلانات", "journals-reports&report_view=ad_account", "accounting.journals_reports.view", "محفظة مقدمة ومستحق منفصلان؛ الصرف اليومي وربط المنصة غير متاحين", "readonly"],
    ["المصروفات والالتزامات", "journals-reports&report_view=liability", "accounting.journals_reports.view", "كشف حسابات الالتزامات؛ لا توقعات أو استحقاقات محسوبة محليًا", "readonly"],
    ["الضرائب", "journals-reports&report_view=tax", "accounting.journals_reports.view", "ضريبة المبيعات والمدخلات من القيود؛ الإقرارات والأدلة تحتاج عقدًا", "readonly"],
    ["القيود المحاسبية", "journals-reports", "accounting.journals_reports.view", "مدين ودائن وتاريخ محاسبي وتفاصيل المجموعة", "readonly"],
    ["التقارير", "journals-reports", "accounting.journals_reports.view", "المركز المالي وميزان المراجعة وكشوف الحسابات", "readonly"],
];
export default function AccountingDirectory({ user, permissions = [] }) {
    return <section aria-label="مجالات المحاسبة" className="space-y-3"><div><h2 className="text-lg font-bold text-slate-900">مجالات المحاسبة</h2><p className="mt-1 text-xs leading-6 text-slate-500">اعرض السجل المتاح لكل مجال. إتاحة العرض لا تمنح صلاحية الترحيل.</p></div><div className="ac-domain-grid">{AREAS.map(([label, page, permission, detail, status]) => {
        const content = <><h3>{label}</h3><StatusBadge value={status === "unavailable" ? "BLOCKED_BY_BACKEND" : status} /><p>{detail}</p></>;
        return userCanAccessAccounting(user, permission, permissions) ? <Link key={label} to={ROOT + page}>{content}<span className="mt-3 block text-xs font-semibold">فتح العرض ←</span></Link> : <article key={label}>{content}<p>يتطلب صلاحية مشاهدة مستقلة</p></article>;
    })}</div></section>;
}
