import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    ArrowClockwise,
    Bank,
    ClipboardText,
    GearSix,
    UploadSimple,
    UserGear,
    Wallet,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import {
    getAccountingSettlementContext,
    getAccountingSettlementDrafts,
} from "../../services/accountingModule";
import AccountingPeriods from "./AccountingPeriods";
import AccountingWriteControl from "./AccountingWriteControl";
import AccountingDailyAutomationActions from "./AccountingDailyAutomationActions";
import AccountingDailyAutomationStatus from "./AccountingDailyAutomationStatus";
import { formatMoney, SummaryCard } from "./AccountingShared";
import { ACCOUNTING_PAGES } from "./accountingPages";

const REVIEW_STATUSES = new Set(["needs_review", "ready_for_review", "reviewed", "rejected"]);

function todayRiyadh() {
    return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh" }).format(new Date());
}

export default function AccountingDailyWorkspace({ status, user, accountingPermissions = [], canManagePermissions = false, onOpenPermissions }) {
    const [context, setContext] = useState(null);
    const [drafts, setDrafts] = useState([]);
    const [receipts, setReceipts] = useState([]);
    const [loading, setLoading] = useState(true);
    const [advancedOpen, setAdvancedOpen] = useState(false);
    const [automationRefreshToken, setAutomationRefreshToken] = useState(0);

    const load = useCallback(async () => {
        setLoading(true);
        const results = await Promise.allSettled([
            getAccountingSettlementContext(),
            getAccountingSettlementDrafts({ limit: 30 }),
            api.get(BASE + "/bank-receipts"),
        ]);
        if (results[0].status === "fulfilled") setContext(results[0].value);
        if (results[1].status === "fulfilled") setDrafts(results[1].value?.items || []);
        if (results[2].status === "fulfilled") setReceipts(results[2].value?.data?.items || []);
        setLoading(false);
    }, []);

    useEffect(() => { load(); }, [load]);

    const today = todayRiyadh();
    const receivedToday = useMemo(
        () => receipts.filter((row) => row.received_on === today).reduce((sum, row) => sum + Number(row.amount || 0), 0),
        [receipts, today],
    );
    const settlementsNeedReview = useMemo(
        () => drafts.filter((draft) => REVIEW_STATUSES.has(draft.status)).length,
        [drafts],
    );
    const reviewCount = Number(status?.review_count || 0);
    const safeActive = status?.cutover?.safe_active === true;
    const pending = Math.max(reviewCount, settlementsNeedReview);
    const stateLabel = safeActive
        ? (pending > 0 ? "محدثة · " + pending + " تحتاج منك" : "محدثة · لا توجد مراجعات")
        : "قيد التجهيز قبل التفعيل";

    return (
        <div className="space-y-5" dir="rtl" data-testid="accounting-home-page">
            <header className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm sm:p-6" data-testid="daily-accounting-header">
                <div className="flex flex-wrap items-start justify-between gap-4">
                    <div>
                        <div className="flex flex-wrap items-center gap-2">
                            <span className={"rounded-full px-3 py-1 text-xs font-extrabold " + (safeActive ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-900")}>
                                {safeActive ? "المحاسبة تعمل تلقائيًا" : "Preview / تجهيز"}
                            </span>
                            <span className="text-xs font-bold text-slate-500">{stateLabel}</span>
                        </div>
                        <h1 className="mt-3 text-2xl font-black text-slate-950 sm:text-3xl">المحاسبة اليومية</h1>
                        <p className="mt-2 max-w-3xl text-sm font-semibold leading-7 text-slate-600">
                            سجّل الشيء الذي حدث فقط. ميزان يتولى التصنيف والمطابقة والقيد والضريبة في الخلفية، ويطلب منك القرار عند وجود استثناء حقيقي.
                        </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        {canManagePermissions && (
                            <button type="button" onClick={onOpenPermissions} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-extrabold text-slate-700 hover:bg-slate-50">
                                <UserGear size={18} /> الصلاحيات
                            </button>
                        )}
                        <button type="button" onClick={load} disabled={loading} className="inline-flex min-h-10 items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 text-xs font-extrabold text-slate-700 hover:bg-slate-50 disabled:opacity-50">
                            <ArrowClockwise size={18} className={loading ? "animate-spin" : ""} /> تحديث
                        </button>
                    </div>
                </div>
            </header>

            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                <SummaryCard label="وصل البنك اليوم" value={formatMoney(receivedToday)} hint="من الحركات التي أدخلتها اليوم" Icon={Bank} tone="emerald" testid="daily-summary-received" />
                <SummaryCard label="تسويات تحتاج منك" value={settlementsNeedReview.toLocaleString("en-US")} hint="العمليات السليمة لا تظهر هنا" Icon={UploadSimple} tone={settlementsNeedReview ? "amber" : "emerald"} testid="daily-summary-settlements" />
                <SummaryCard label="أرصدة البنوك" value={formatMoney(status?.balance_visibility?.banks)} hint={safeActive ? "من قيود ميزان 2 الموثقة" : "تظهر بعد اكتمال التفعيل"} Icon={Wallet} tone="sky" testid="daily-summary-banks" />
                <SummaryCard label="مراجعات الجاهزية" value={pending.toLocaleString("en-US")} hint="بوابات القطع والإعداد، منفصلة عن قائمة قرارات اليوم" Icon={ClipboardText} tone={pending ? "rose" : "emerald"} testid="daily-summary-review" />
            </div>

            <AccountingDailyAutomationActions
                settlementContext={context}
                permissions={accountingPermissions}
                onSaved={async () => {
                    await load();
                    setAutomationRefreshToken((value) => value + 1);
                }}
            />

            <AccountingDailyAutomationStatus
                status={status}
                drafts={drafts}
                receipts={receipts}
                refreshToken={automationRefreshToken}
            />

            <section className="rounded-2xl border border-slate-200 bg-slate-50 p-4" data-testid="daily-accounting-advanced">
                <button type="button" onClick={() => setAdvancedOpen((value) => !value)} className="flex w-full items-center justify-between gap-3 text-right">
                    <div>
                        <div className="flex items-center gap-2 text-sm font-black text-slate-800"><GearSix size={20} /> التفاصيل والإعدادات المتقدمة</div>
                        <p className="mt-1 text-xs font-semibold text-slate-500">للمحاسب أو المالك فقط: التسويات التفصيلية، القيود والتقارير، الفترات، الإيقاف وإعادة المعالجة.</p>
                    </div>
                    <span className="text-xs font-extrabold text-slate-500">{advancedOpen ? "إخفاء" : "عرض"}</span>
                </button>
                {advancedOpen && (
                    <div className="mt-4 space-y-4 border-t border-slate-200 pt-4">
                        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                            {[
                                ["settlements", "التسويات التفصيلية"],
                                ["financial-movements", "الحركات والاستردادات"],
                                ["journals-reports", "القيود والتقارير"],
                                ["opening-balances", "الأرصدة الافتتاحية"],
                            ].map(([id, label]) => {
                                const page = ACCOUNTING_PAGES.find((item) => item.id === id);
                                return <Link key={id} to={page.to} className="rounded-xl border border-slate-200 bg-white p-3 text-xs font-extrabold text-slate-700 hover:border-emerald-300">{label}</Link>;
                            })}
                        </div>
                        {(user?.is_owner === true || String(user?.role || "").toLowerCase() === "owner") && (
                            <div className="grid gap-4 xl:grid-cols-2">
                                <AccountingWriteControl />
                                <AccountingPeriods />
                            </div>
                        )}
                    </div>
                )}
            </section>

        </div>
    );
}
