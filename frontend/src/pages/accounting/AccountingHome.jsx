import { Link } from "react-router-dom";
import {
    ArrowLeft,
    Bank,
    Buildings,
    CheckCircle,
    ClipboardText,
    LockKey,
    Package,
    Receipt,
    Truck,
    UsersThree,
    Wallet,
} from "@phosphor-icons/react";
import { ACCOUNTING_PAGES, userCanAccessAccounting } from "./accountingPages";
import { formatMoney, ReadinessPanel, SummaryCard } from "./AccountingShared";

function DailyShortcuts({ user, accountingPermissions }) {
    const shortcuts = [
        { pageId: "settlements", label: "تسوية دفعات", Icon: Receipt },
        { pageId: "financial-movements", label: "حركة مالية", Icon: Wallet },
        { pageId: "inventory-purchases", label: "فاتورة شراء", Icon: Package },
        { pageId: "payroll-obligations", label: "راتب أو التزام", Icon: UsersThree },
    ];
    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="accounting-daily-shortcuts">
            <h2 className="text-lg font-black text-slate-950">اختصارات العمل اليومي</h2>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                {shortcuts.map(({ pageId, label, Icon }) => {
                    const page = ACCOUNTING_PAGES.find((row) => row.id === pageId);
                    const allowed = userCanAccessAccounting(user, page.permission, accountingPermissions);
                    return allowed ? (
                        <Link key={pageId} to={page.to} className="group flex min-h-24 items-center justify-between gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4 transition hover:border-emerald-300 hover:bg-emerald-50">
                            <div><div className="text-sm font-black text-slate-950">{label}</div><div className="mt-1 text-[11px] font-semibold text-slate-500">فتح الصفحة المخصصة</div></div>
                            <span className="rounded-xl bg-white p-3 text-emerald-800 shadow-sm"><Icon size={24} weight="duotone" /></span>
                        </Link>
                    ) : (
                        <div key={pageId} className="flex min-h-24 items-center justify-between gap-3 rounded-xl border border-dashed border-slate-200 bg-slate-50 p-4 opacity-60">
                            <div><div className="text-sm font-black text-slate-700">{label}</div><div className="mt-1 text-[11px] font-semibold text-slate-500">تحتاج صلاحية مستقلة</div></div>
                            <LockKey size={22} className="text-slate-400" />
                        </div>
                    );
                })}
            </div>
        </section>
    );
}

function TodayTasks({ status }) {
    const tasks = status?.tasks || [];
    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-5" data-testid="accounting-today-tasks">
            <div className="flex items-center justify-between gap-3">
                <div><h2 className="text-lg font-black text-slate-950">مهام اليوم</h2><p className="mt-1 text-xs font-semibold text-slate-500">استثناءات قابلة للعمل فقط، بلا أرقام مخمنة.</p></div>
                <span className="rounded-full bg-slate-100 px-3 py-1 font-mono text-xs font-black text-slate-700" dir="ltr">{tasks.length}</span>
            </div>
            <div className="mt-4 space-y-2">
                {tasks.length === 0 ? (
                    <div className="flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50 p-4 text-sm font-extrabold text-emerald-800">
                        <CheckCircle size={21} weight="fill" /> لا توجد استثناءات محاسبية معلقة.
                    </div>
                ) : tasks.slice(0, 8).map((task) => {
                    const page = ACCOUNTING_PAGES.find((row) => row.id === task.page) || ACCOUNTING_PAGES[0];
                    return (
                        <Link key={task.id} to={page.to} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 p-3 transition hover:border-amber-300 hover:bg-amber-50">
                            <div><div className="text-xs font-extrabold text-slate-900">{task.title}</div><div className="mt-1 text-[11px] font-semibold leading-5 text-slate-500">{task.detail}</div></div>
                            <ArrowLeft size={18} className="shrink-0 text-slate-400" />
                        </Link>
                    );
                })}
            </div>
        </section>
    );
}

export default function AccountingHome({ status, user, accountingPermissions }) {
    const balancesAvailable = status?.balance_visibility?.status === "available";
    const hiddenHint = balancesAvailable
        ? "مصدر العرض المالي من القيود الموثقة بعد القطع"
        : "محجوب حتى اكتمال القطع والأدلة والاعتماد";
    return (
        <div className="space-y-5" data-testid="accounting-home-page">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <SummaryCard label="أرصدة البنوك" value={formatMoney(status?.balance_visibility?.banks)} hint={hiddenHint} Icon={Bank} tone="emerald" testid="accounting-summary-banks" />
                <SummaryCard label="مبالغ لدى المزودين" value={formatMoney(status?.balance_visibility?.providers)} hint={hiddenHint} Icon={Buildings} tone="sky" testid="accounting-summary-providers" />
                <SummaryCard label="الشحن والتحصيل" value={formatMoney(status?.balance_visibility?.couriers_cod)} hint={balancesAvailable ? "صافي لنا موجب / علينا سالب من القيود الموثقة" : hiddenHint} Icon={Truck} tone="amber" testid="accounting-summary-couriers" />
                <SummaryCard label="تحتاج مراجعة" value={Number(status?.review_count || 0).toLocaleString("en-US")} hint="أدلة أو إعدادات أو مستندات غير مكتملة" Icon={ClipboardText} tone="rose" testid="accounting-summary-review" />
            </div>
            <ReadinessPanel status={status} />
            <DailyShortcuts user={user} accountingPermissions={accountingPermissions} />
            <TodayTasks status={status} />
        </div>
    );
}
