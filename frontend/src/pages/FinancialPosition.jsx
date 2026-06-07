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
    ClipboardText, Clock, ArrowRight, Truck,
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

    const load = async () => {
        setLoading(true);
        try {
            // Parallel fetch — purely read-only. No calculations here.
            const [s, r, listUnpaid, listPartial] = await Promise.all([
                api.get("/liabilities/summary"),
                api.get("/reconciliation/summary"),
                api.get("/liabilities?status=unpaid&limit=1"),
                api.get("/liabilities?status=partial&limit=1"),
            ]);
            setSummary(s.data);
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
                    title="الرواتب المستحقة"
                    value={fmtMoney(l.salaries_unpaid)}
                    sub="مولَّدة من الموظفين النشطين"
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
                        if (rows.length === 0) return "لا مستحقات قيد السداد — تُحسب من الطلبات المسلّمة فقط";
                        return rows.slice(0, 3).join(" · ") + (rows.length > 3 ? " …" : "");
                    })()}
                    tone="amber"
                    Icon={Truck}
                    testid="kpi-liab-shipping"
                />
                <Card
                    title="إجمالي الالتزامات"
                    value={fmtMoney(l.total)}
                    sub="الرواتب + الإعلانات + الشحن + الموردين"
                    tone="rose"
                    Icon={CurrencyDollar}
                    testid="kpi-liab-total"
                />
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
                    <span> آخر تحديث: {new Date(summary.generated_at).toLocaleString("ar-SA")}</span>
                )}
            </div>
        </div>
    );
}
