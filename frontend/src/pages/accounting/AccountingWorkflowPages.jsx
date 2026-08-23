import { Link } from "react-router-dom";
import { ArrowLeft, LockKey } from "@phosphor-icons/react";
import { ImplementationNotice, ReadinessPanel } from "./AccountingShared";

const PAGE_LINKS = {
    settlements: [
        { to: "/salla-settlements", label: "رفع تسوية سلة" },
        { to: "/bnpl-settlements/register", label: "تسجيل تسوية تمارا/تابي/إمكان" },
        { to: "/settlements-overview", label: "سجل التسويات الحالي" },
    ],
    "shipping-cod": [
        { to: "/shipping/orders-ledger", label: "دفتر شركات الشحن" },
        { to: "/couriers-ledger", label: "أرصدة شركات الشحن" },
        { to: "/bank-transfer-review?workspace=store-delivery", label: "مراجعة تحصيلات موصلي المتجر" },
        { to: "/shipping/settings", label: "قواعد عمولة COD" },
    ],
    "inventory-purchases": [
        { to: "/suppliers-v2", label: "الموردون والفواتير" },
        { to: "/inventory-receiving-v2", label: "استلام المخزون" },
        { to: "/purchase-invoices", label: "فواتير الشراء الحالية" },
    ],
    "financial-movements": [
        { to: "/new-transaction", label: "الحركة المالية الموحّدة" },
        { to: "/financial-movements", label: "سجل الحركات الحالي" },
    ],
    "payroll-obligations": [
        { to: "/recurring-obligations", label: "الالتزامات والمصاريف الدورية" },
        { to: "/employees-ledger", label: "دفتر الموظفين" },
    ],
    "journals-reports": [
        { to: "/transactions", label: "القيود اليومية" },
        { to: "/financial-position-ledger", label: "المركز المالي من القيود" },
        { to: "/accounting/reconciliation", label: "تقرير المطابقة" },
    ],
};

export function PartialWorkflowPage({ page }) {
    const links = PAGE_LINKS[page.id] || [];
    const first = links[0];
    return (
        <div className="space-y-5" data-testid={`accounting-page-${page.id}`}>
            <ImplementationNotice page={page} />
            <section className="rounded-2xl border border-slate-200 bg-white p-5">
                <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                    <div>
                        <h2 className="text-xl font-black text-slate-950">القدرات التشغيلية الموجودة حاليًا</h2>
                        <p className="mt-2 max-w-3xl text-sm font-semibold leading-6 text-slate-600">
                            لم يُنقل كل المنطق إلى واجهة الصفحة الجديدة بعد. لا يُنشأ قيد افتتاحي ولا تُستورد بيانات من ميزان القديم هنا.
                        </p>
                    </div>
                    {first && (
                        <Link to={first.to} className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-emerald-800 px-4 text-sm font-extrabold text-white">
                            <ArrowLeft size={18} weight="bold" /> {first.label}
                        </Link>
                    )}
                </div>
                {links.length > 1 && (
                    <div className="mt-5 grid gap-2 border-t border-slate-100 pt-4 sm:grid-cols-2 xl:grid-cols-3">
                        {links.slice(1).map((link) => (
                            <Link key={link.to} to={link.to} className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs font-extrabold text-slate-700 hover:border-emerald-300 hover:bg-emerald-50">
                                {link.label}
                            </Link>
                        ))}
                    </div>
                )}
            </section>
        </div>
    );
}

export function OpeningBalancesBlocked({ status }) {
    return (
        <div className="space-y-5" data-testid="accounting-page-opening-balances">
            <div className="rounded-2xl border border-rose-200 bg-rose-50 p-6">
                <div className="flex items-start gap-3">
                    <LockKey size={30} weight="duotone" className="shrink-0 text-rose-700" />
                    <div>
                        <h2 className="text-xl font-black text-rose-950">إدخال وترحيل الأرصدة الافتتاحية محجوب</h2>
                        <p className="mt-2 text-sm font-semibold leading-6 text-rose-800">
                            لم يُعتمد توقيت القطع، ولم تُنشأ معاينة متوازنة، ولم يُسجل اعتماد صريح. لذلك لا توجد حقول إدخال أو زر ترحيل إلى Production.
                        </p>
                    </div>
                </div>
            </div>
            <ReadinessPanel status={status} />
        </div>
    );
}
