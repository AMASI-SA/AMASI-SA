/**
 * المركز المالي — Financial Position
 *
 * Iter-93 — Read-only aggregator screen. It does NOT perform any
 * calculations. Every number is sourced from existing endpoints:
 *   • GET /api/liabilities/summary     → assets, liabilities, net_position
 *   • GET /api/reconciliation/summary  → collection rate, total pending
 *   • GET /api/liabilities?status=…    → count of open obligations
 *
 * Designed for a non-financial data-entry user: 6 KPIs + a small
 * breakdown table. No new collections, no new backend logic.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    Scales, Wallet, Receipt, CurrencyDollar, ChartLineUp,
    ClipboardText, Clock, ArrowRight, Truck, Users, UserMinus,
    HandCoins, CaretDown, CaretUp,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";


const fmtMoney = (v) => {
    const num = Number(v || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
    return `\u2068${num}\u00A0ر.س\u2069`;
};
const fmtPct = (v) => `${Number(v || 0).toFixed(1)}%`;


function Card({ title, value, sub, tone = "slate", Icon, testid }) {
    const tones = {
        slate:   "bg-slate-50 border-slate-200 text-slate-900",
        emerald: "bg-emerald-50 border-emerald-200 text-emerald-900",
        sky:     "bg-sky-50 border-sky-200 text-sky-900",
        rose:    "bg-rose-50 border-rose-200 text-rose-900",
        amber:   "bg-amber-50 border-amber-200 text-amber-900",
        violet:  "bg-violet-50 border-violet-200 text-violet-900",
    };
    return (
        <div className={`rounded-xl border p-4 ${tones[tone]}`} data-testid={testid}>
            <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold opacity-80">{title}</span>
                {Icon && <Icon size={20} weight="duotone" className="opacity-70" />}
            </div>
            <div className="num text-2xl sm:text-3xl font-extrabold">{value}</div>
            {sub && <div className="text-[11px] opacity-70 mt-1">{sub}</div>}
        </div>
    );
}


function SectionTitle({ Icon, children }) {
    return (
        <div className="flex items-center gap-2 mb-3 mt-6">
            {Icon && <Icon size={18} weight="duotone" className="text-slate-700" />}
            <h2 className="text-base font-bold text-slate-800">{children}</h2>
        </div>
    );
}


function MiniRow({ label, value, hint, link }) {
    const content = (
        <div className="flex items-center justify-between gap-3 py-2 px-3 rounded-lg hover:bg-slate-50 transition">
            <div className="flex flex-col">
                <span className="text-sm font-bold text-slate-800">{label}</span>
                {hint && <span className="text-[11px] text-slate-500">{hint}</span>}
            </div>
            <div className="flex items-center gap-2">
                <span className="num text-sm font-bold text-slate-900">{value}</span>
                {link && <ArrowRight size={14} className="text-slate-400" />}
            </div>
        </div>
    );
    return link ? <Link to={link}>{content}</Link> : content;
}


export default function FinancialPosition() {
    const [summary, setSummary] = useState(null);
    const [recon, setRecon] = useState(null);
    const [openCount, setOpenCount] = useState(null);
    const [loading, setLoading] = useState(true);
    const [showSalaryRows, setShowSalaryRows] = useState(false);

    const load = async () => {
        setLoading(true);
        try {
            // Iter-217 — Primary source is the SSOT endpoint. We map
            // its enriched shape into the legacy `summary` shape the
            // rest of this page expects. Reconciliation totals + open
            // liability counters are still read from their legacy
            // endpoints (they don't suffer from the SSOT drift bug).
            const [pos, r, listUnpaid, listPartial] = await Promise.all([
                api.get("/accounting/financial-position"),
                api.get("/reconciliation/summary"),
                api.get("/liabilities?status=unpaid&limit=1"),
                api.get("/liabilities?status=partial&limit=1"),
            ]);
            const p = pos.data || {};
            const a = p.assets || {};
            const li = p.liabilities || {};
            const totals = p.totals || {};
            const adapted = {
                net_position: totals.net_position ?? p.net_position ?? 0,
                assets: {
                    banks: a.banks ?? 0,
                    payment_platforms_remaining:
                        a.payment_platforms_remaining ?? 0,
                    payment_platforms_expected:
                        a.payment_platforms_remaining ?? 0,
                    employee_advance: a.employee_advance ?? 0,
                    employee_custody: a.employee_custody ?? 0,
                    external_receivable: a.external_receivable ?? 0,
                    courier_cod_receivable: a.courier_cod_receivable ?? 0,
                    store_driver_cod_receivable:
                        a.store_driver_cod_receivable ?? 0,
                    ad_account_prepaid: a.ad_account_prepaid ?? 0,
                    total: totals.total_assets ?? 0,
                },
                liabilities: {
                    salaries_unpaid: li.salaries_unpaid ?? 0,
                    ad_accounts_unpaid: li.ad_accounts_unpaid ?? 0,
                    shipping_unpaid: li.courier_payable ?? 0,
                    store_driver_payable: li.store_driver_payable ?? 0,
                    supplier_unpaid: li.supplier_payable ?? 0,
                    external_payable: li.external_payable ?? 0,
                    by_ad_provider: p.by_ad_provider || {},
                    by_shipping_company: {},  // not derived from ledger
                    total: totals.total_liabilities ?? 0,
                },
                salary_breakdown: p.salary_breakdown || {
                    accrued_total: 0, advances_total: 0,
                    paid_total: 0, net_due: 0,
                    active_count: 0, suspended_count: 0,
                    employees: [],
                },
                source: p.source || "general_ledger",
                iter: p.iter || null,
            };
            setSummary(adapted);
            setRecon(r.data);
            setOpenCount(
                (Number(listUnpaid?.data?.total) || 0)
                + (Number(listPartial?.data?.total) || 0)
            );
        } catch (e) {
            toast.error(formatApiErrorDetail(e) || "تعذّر تحميل المركز المالي");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        load();
        // Realtime is unnecessary — refresh button is enough for this user
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    if (loading && !summary) {
        return (
            <div className="p-6 text-center text-slate-500" data-testid="fin-loading">
                جاري التحميل…
            </div>
        );
    }
    if (!summary || !recon) return null;

    const a = summary.assets || {};
    const l = summary.liabilities || {};
    const sb = summary.salary_breakdown || {
        accrued_total: 0, advances_total: 0, paid_total: 0, net_due: 0,
        active_count: 0, suspended_count: 0, employees: [],
    };
    const totals = recon.totals || {};
    const byProvider = l.by_ad_provider || {};

    return (
        <div className="space-y-2" dir="rtl" data-testid="financial-position-page">
            {/* Header */}
            <div className="flex items-center justify-between flex-wrap gap-3 mb-2">
                <div>
                    <h1 className="text-2xl sm:text-3xl font-extrabold text-slate-900">
                        المركز المالي
                    </h1>
                    <p className="text-sm text-slate-500 mt-1">
                        نظرة شاملة على الأصول والالتزامات وصافي المركز المالي للنشاط.
                    </p>
                </div>
                <button
                    onClick={load}
                    disabled={loading}
                    className="px-4 py-2 rounded-lg bg-slate-900 text-white text-sm font-bold hover:bg-slate-800 disabled:opacity-50"
                    data-testid="fin-refresh-btn"
                >
                    {loading ? "جاري التحديث…" : "تحديث"}
                </button>
            </div>

            {/* Net position banner */}
            <div
                className="rounded-2xl border-2 border-violet-300 bg-gradient-to-l from-violet-50 to-sky-50 p-5 mb-4"
                data-testid="net-position-banner"
            >
                <div className="flex items-center justify-between flex-wrap gap-3">
                    <div>
                        <div className="flex items-center gap-2 text-violet-800">
                            <Scales size={18} weight="duotone" />
                            <span className="text-xs font-bold">صافي المركز المالي</span>
                        </div>
                        <div className="num text-3xl sm:text-4xl font-extrabold text-violet-900 mt-1">
                            {fmtMoney(summary.net_position)}
                        </div>
                        <div className="text-[11px] text-violet-700/70 mt-1">
                            = إجمالي الأصول − إجمالي الالتزامات
                        </div>
                    </div>
                    <div className="flex items-center gap-3 text-sm">
                        <div className="text-center">
                            <div className="text-[11px] text-slate-500">الأصول</div>
                            <div className="num font-bold text-emerald-700">
                                {fmtMoney(a.total)}
                            </div>
                        </div>
                        <span className="text-2xl text-slate-400">−</span>
                        <div className="text-center">
                            <div className="text-[11px] text-slate-500">الالتزامات</div>
                            <div className="num font-bold text-rose-700">
                                {fmtMoney(l.total)}
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Assets */}
            <SectionTitle Icon={Wallet}>الأصول</SectionTitle>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <Card
                    title="إجمالي البنوك"
                    value={fmtMoney(a.banks)}
                    sub="من رصيد الحسابات البنكية الحالية"
                    tone="sky"
                    Icon={Wallet}
                    testid="kpi-assets-banks"
                />
                <Card
                    title="رصيد المنصات (لم يُحوَّل بعد)"
                    value={fmtMoney(a.payment_platforms_remaining ?? a.payment_platforms_expected)}
                    sub="الرصيد المتبقي على بوابات الدفع — بعد خصم ما تم تحويله للبنوك"
                    tone="violet"
                    Icon={Receipt}
                    testid="kpi-assets-platforms"
                />
                <Card
                    title="عهدة COD لدى موصلي المتجر"
                    value={fmtMoney(a.store_driver_cod_receivable)}
                    sub="مبالغ نقدية على كل موصل باسمه حتى يوردها"
                    tone="amber"
                    Icon={Users}
                    testid="kpi-assets-store-drivers-cod"
                />
                <Card
                    title="إجمالي الأصول"
                    value={fmtMoney(a.total)}
                    sub="البنوك + المنصات + المديونيات (بدون تكرار)"
                    tone="emerald"
                    Icon={CurrencyDollar}
                    testid="kpi-assets-total"
                />
            </div>

            {/* Liabilities */}
            <SectionTitle Icon={Receipt}>الالتزامات</SectionTitle>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <Card
                    title="الرواتب المستحقة (صافي)"
                    value={fmtMoney(sb.net_due ?? l.salaries_unpaid)}
                    sub={`متراكم ${fmtMoney(sb.accrued_total)} − مدفوع ${fmtMoney(sb.paid_total)}`}
                    tone="amber"
                    Icon={ClipboardText}
                    testid="kpi-liab-salaries"
                />
                <Card
                    title="الالتزامات الإعلانية"
                    value={fmtMoney(l.ad_accounts_unpaid)}
                    sub={
                        (byProvider.snapchat || 0) + (byProvider.tiktok || 0) + (byProvider.meta || 0) === 0
                            ? "لا توجد فواتير حالياً"
                            : `سناب: ${fmtMoney(byProvider.snapchat)} · تيك توك: ${fmtMoney(byProvider.tiktok)} · ميتا: ${fmtMoney(byProvider.meta)}`
                    }
                    tone="rose"
                    Icon={ChartLineUp}
                    testid="kpi-liab-ads"
                />
                <Card
                    title="مستحقات شركات الشحن"
                    value={fmtMoney(l.shipping_unpaid)}
                    sub={(() => {
                        const by = l.by_shipping_company || {};
                        const rows = Object.entries(by)
                            .filter(([, v]) => Number(v?.remaining || 0) > 0)
                            .map(([name, v]) => `${name}: ${fmtMoney(v.remaining)}`);
                        if (rows.length === 0) return "لا مستحقات قيد السداد — راجع «أرصدة شركات الشحن» للتفاصيل";
                        return rows.slice(0, 3).join(" · ") + (rows.length > 3 ? " …" : "");
                    })()}
                    tone="amber"
                    Icon={Truck}
                    testid="kpi-liab-shipping"
                />
                <Card
                    title="أجور موصلي المتجر المستحقة"
                    value={fmtMoney(l.store_driver_payable)}
                    sub="تكلفة التوصيل المسجلة لكل موصل على حدة"
                    tone="amber"
                    Icon={Users}
                    testid="kpi-liab-store-drivers"
                />
                {/* Iter-144 — Unified courier ledger summary */}
                <ShippingLedgerSummary />
                <Card
                    title="إجمالي الالتزامات"
                    value={fmtMoney(l.total)}
                    sub="الرواتب + الإعلانات + الشحن + الموردين"
                    tone="rose"
                    Icon={CurrencyDollar}
                    testid="kpi-liab-total"
                />
            </div>

            {/* Salary breakdown (Iter-115) ─────────────────────────── */}
            <SectionTitle Icon={Users}>تفاصيل الرواتب — تراكم حسب أيام العمل</SectionTitle>
            <div className="rounded-xl border border-amber-200 bg-amber-50/40 p-4" data-testid="salary-breakdown-card">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                    <div className="rounded-lg bg-white border border-amber-200 p-3" data-testid="salary-accrued">
                        <div className="flex items-center justify-between mb-1">
                            <span className="text-[11px] font-bold text-amber-900/80">إجمالي الرواتب المتراكمة</span>
                            <ClipboardText size={16} weight="duotone" className="text-amber-700" />
                        </div>
                        <div className="num text-xl sm:text-2xl font-extrabold text-amber-900">
                            {fmtMoney(sb.accrued_total)}
                        </div>
                        <div className="text-[10px] text-amber-700/70 mt-1">
                            مجموع راتب اليوم × أيام العمل الفعلية
                        </div>
                    </div>
                    <div className="rounded-lg bg-white border border-sky-200 p-3" data-testid="salary-advances">
                        <div className="flex items-center justify-between mb-1">
                            <span className="text-[11px] font-bold text-sky-900/80">إجمالي السلف على الموظفين</span>
                            <HandCoins size={16} weight="duotone" className="text-sky-700" />
                        </div>
                        <div className="num text-xl sm:text-2xl font-extrabold text-sky-900">
                            {fmtMoney(sb.advances_total)}
                        </div>
                        <div className="text-[10px] text-sky-700/70 mt-1">
                            مدينة على الموظفين — تُعرض منفصلة
                        </div>
                    </div>
                    <div className="rounded-lg bg-white border border-emerald-200 p-3" data-testid="salary-paid">
                        <div className="flex items-center justify-between mb-1">
                            <span className="text-[11px] font-bold text-emerald-900/80">إجمالي المدفوع</span>
                            <CurrencyDollar size={16} weight="duotone" className="text-emerald-700" />
                        </div>
                        <div className="num text-xl sm:text-2xl font-extrabold text-emerald-900">
                            {fmtMoney(sb.paid_total)}
                        </div>
                        <div className="text-[10px] text-emerald-700/70 mt-1">
                            مبالغ مدفوعة فعلياً من البنك (بدون السلف)
                        </div>
                    </div>
                    <div className="rounded-lg bg-white border border-rose-200 p-3" data-testid="salary-net-due">
                        <div className="flex items-center justify-between mb-1">
                            <span className="text-[11px] font-bold text-rose-900/80">صافي المستحق للدفع</span>
                            <Scales size={16} weight="duotone" className="text-rose-700" />
                        </div>
                        <div className="num text-xl sm:text-2xl font-extrabold text-rose-900">
                            {fmtMoney(sb.net_due)}
                        </div>
                        <div className="text-[10px] text-rose-700/70 mt-1">
                            المتراكم − المدفوع
                        </div>
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-3 mt-3">
                    <div className="flex items-center gap-2 rounded-lg bg-white border border-slate-200 p-2.5" data-testid="salary-active-count">
                        <Users size={18} weight="duotone" className="text-emerald-600" />
                        <div>
                            <div className="text-[10px] text-slate-500 font-bold">الموظفون النشطون</div>
                            <div className="num text-lg font-extrabold text-slate-900">
                                {Number(sb.active_count || 0).toLocaleString("en-US")}
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center gap-2 rounded-lg bg-white border border-slate-200 p-2.5" data-testid="salary-suspended-count">
                        <UserMinus size={18} weight="duotone" className="text-rose-600" />
                        <div>
                            <div className="text-[10px] text-slate-500 font-bold">الموظفون الموقوفون</div>
                            <div className="num text-lg font-extrabold text-slate-900">
                                {Number(sb.suspended_count || 0).toLocaleString("en-US")}
                            </div>
                        </div>
                    </div>
                </div>

                <button
                    type="button"
                    onClick={() => setShowSalaryRows((v) => !v)}
                    className="mt-3 w-full flex items-center justify-center gap-1.5 text-sm font-bold text-amber-900 hover:text-amber-700 transition py-1.5 rounded-lg hover:bg-amber-100/60"
                    data-testid="toggle-salary-rows-btn"
                >
                    {showSalaryRows ? <CaretUp size={16} /> : <CaretDown size={16} />}
                    {showSalaryRows ? "إخفاء قائمة الموظفين" : `عرض تفاصيل ${(sb.employees || []).length} موظف`}
                </button>

                {showSalaryRows && (sb.employees || []).length > 0 && (
                    <div className="mt-3 rounded-lg border border-slate-200 bg-white overflow-x-auto" data-testid="salary-employees-table">
                        <table className="mezan-table compact w-full text-xs sm:text-sm" dir="rtl">
                            <thead className="bg-slate-50 text-slate-600">
                                <tr>
                                    <th className="p-2 text-right">الموظف</th>
                                    <th className="p-2 text-right">الحالة</th>
                                    <th className="p-2 text-right">راتب شهري</th>
                                    <th className="p-2 text-right">أيام العمل</th>
                                    <th className="p-2 text-right">المتراكم</th>
                                    <th className="p-2 text-right">سلف مفتوحة</th>
                                    <th className="p-2 text-right">مدفوع</th>
                                    <th className="p-2 text-right">صافي مستحق</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-100">
                                {(sb.employees || []).map((e) => (
                                    <tr key={e.id} data-testid={`salary-emp-row-${e.id}`}>
                                        <td className="p-2 font-bold text-slate-800">{e.name || "—"}</td>
                                        <td className="p-2">
                                            {e.status === "active" ? (
                                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 text-[10px] font-bold">
                                                    نشط
                                                </span>
                                            ) : (
                                                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full bg-rose-100 text-rose-700 text-[10px] font-bold">
                                                    موقوف
                                                </span>
                                            )}
                                        </td>
                                        <td className="p-2 num text-slate-700">{fmtMoney(e.monthly_amount)}</td>
                                        <td className="p-2 num text-slate-700">
                                            {Number(e.days_worked || 0).toLocaleString("en-US")} يوم
                                        </td>
                                        <td className="p-2 num font-bold text-amber-800">{fmtMoney(e.accrued)}</td>
                                        <td className="p-2 num text-sky-700">{fmtMoney(e.outstanding_advance)}</td>
                                        <td className="p-2 num text-emerald-700">{fmtMoney(e.paid)}</td>
                                        <td className="p-2 num font-extrabold text-rose-700">{fmtMoney(e.net_due)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            {/* Quick indicators */}
            <SectionTitle Icon={ChartLineUp}>مؤشرات مختصرة</SectionTitle>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <Card
                    title="نسبة التحصيل"
                    value={fmtPct(totals.collection_rate)}
                    sub={`من إجمالي ${fmtMoney(totals.expected)}`}
                    tone={
                        Number(totals.collection_rate || 0) >= 80
                            ? "emerald" : Number(totals.collection_rate || 0) >= 50 ? "amber" : "rose"
                    }
                    Icon={ChartLineUp}
                    testid="kpi-collection-rate"
                />
                <Card
                    title="المبالغ المعلَّقة"
                    value={fmtMoney(totals.pending)}
                    sub="مبيعات لم تصل بعد للبنك"
                    tone="amber"
                    Icon={Clock}
                    testid="kpi-pending"
                />
                <Card
                    title="عدد الالتزامات المفتوحة"
                    value={Number(openCount || 0).toLocaleString("en-US")}
                    sub={
                        Number(l.overdue_total || 0) > 0
                            ? `منها متأخرة بقيمة ${fmtMoney(l.overdue_total)}`
                            : "لا توجد التزامات متأخرة"
                    }
                    tone={Number(l.overdue_total || 0) > 0 ? "rose" : "slate"}
                    Icon={ClipboardText}
                    testid="kpi-open-liabilities"
                />
            </div>

            {/* Quick links to source pages */}
            <SectionTitle Icon={ArrowRight}>روابط سريعة</SectionTitle>
            <div className="rounded-xl border border-slate-200 bg-white divide-y divide-slate-100">
                <MiniRow
                    label="عرض تفاصيل الأصول والحسابات"
                    value={fmtMoney(a.total)}
                    hint="البنوك ومنصات الدفع"
                    link="/accounts"
                />
                <MiniRow
                    label="عرض المطابقة والتسويات"
                    value={fmtPct(totals.collection_rate)}
                    hint={`متوقَّع ${fmtMoney(totals.expected)} · محوَّل ${fmtMoney(totals.transferred)}`}
                    link="/reconciliation"
                />
                <MiniRow
                    label="عرض حسابات شركات الشحن"
                    value={fmtMoney(l.shipping_unpaid)}
                    hint="مستحقات مُحتسبة من الطلبات المسلّمة فقط"
                    link="/shipping-accounts"
                />
                <MiniRow
                    label="عرض المصروفات التشغيلية والرواتب"
                    value={fmtMoney(l.salaries_unpaid)}
                    hint="إدارة الموظفين والمصروفات"
                    link="/operating-expenses"
                />
            </div>

            {/* Generated_at footer */}
            <div className="text-center text-[11px] text-slate-400 mt-4">
                البيانات مأخوذة مباشرةً من Reconciliation و Liabilities — لا يوجد تخزين أو إعادة حساب.
                {summary.generated_at && (
                    <span> آخر تحديث: {new Date(summary.generated_at).toLocaleString("en-US")}</span>
                )}
            </div>
        </div>
    );
}


// Iter-144 — Unified courier ledger summary inside FinancialPosition.
function ShippingLedgerSummary() {
    const [data, setData] = useState(null);
    useEffect(() => {
        api.get("/shipping-accounts/ledger")
            .then(({ data: d }) => setData(d))
            .catch(() => setData({ totals: {} }));
    }, []);
    if (!data) return null;
    const t = data.totals || {};
    return (
        <Card
            title="🚚 صافي حسابات شركات الشحن"
            value={fmtMoney(t.net_balance || 0)}
            sub={`لنا: ${fmtMoney(t.net_owed_to_us || 0)} · علينا: ${fmtMoney(t.net_owed_by_us || 0)} · COD معلَّق: ${fmtMoney(t.cod_pending || 0)} (متابعة فقط)`}
            tone={Number(t.net_balance || 0) >= 0 ? "emerald" : "rose"}
            Icon={Truck}
            testid="kpi-shipping-ledger-net"
        />
    );
}
