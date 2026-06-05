import { useEffect, useMemo, useState } from "react";
import { ChartPieSlice, CalendarBlank, ArrowsClockwise, Megaphone } from "@phosphor-icons/react";
import { Link } from "react-router-dom";
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
    PieChart, Pie, Cell,
} from "recharts";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";
import AdvancedFilters, { defaultFilters, filtersToQueryString } from "../components/AdvancedFilters";
import ProductSalesReport from "../components/ProductSalesReport";
import ProviderCommissionCard from "../components/ProviderCommissionCard";

const COLORS = ["#0A3622", "#D4AF37", "#16A34A", "#D97706", "#0EA5E9", "#7C3AED", "#DC2626", "#0891B2"];

function formatRelative(ms) {
    if (ms < 5_000) return "الآن";
    if (ms < 60_000) return `قبل ${Math.floor(ms / 1000)} ثانية`;
    if (ms < 3_600_000) return `قبل ${Math.floor(ms / 60_000)} دقيقة`;
    return `قبل ${Math.floor(ms / 3_600_000)} ساعة`;
}

export default function Reports() {
    const [dashboard, setDashboard] = useState(null);
    const [reconciliation, setReconciliation] = useState(null);
    const [allDaily, setAllDaily] = useState([]);
    const [loading, setLoading] = useState(true);
    const [filters, setFilters] = useState(defaultFilters());
    const [lastUpdated, setLastUpdated] = useState(null);
    const [nowTick, setNowTick] = useState(Date.now());
    const fromDate = filters.from;
    const toDate = filters.to;

    const fetchAll = async (loud = true) => {
        if (loud) setLoading(true);
        try {
            const qs = filtersToQueryString(filters);
            const [dashRes, dailyRes, reconRes] = await Promise.all([
                api.get(`/dashboard${qs ? "?" + qs : ""}`),
                api.get("/daily-costs"),
                // Pass the same date filter so the transparency block + KPI
                // honour the user's selected period (iter-71b).
                api.get(`/reconciliation/summary${qs ? "?" + qs : ""}`).catch(() => ({ data: null })),
            ]);
            setDashboard(dashRes.data || null);
            setAllDaily(dailyRes.data || []);
            setReconciliation(reconRes.data || null);
            setLastUpdated(Date.now());
        } catch {
            /* silent on background refresh */
        } finally {
            if (loud) setLoading(false);
        }
    };

    useEffect(() => { fetchAll(true); /* eslint-disable-next-line */ }, [filters]);

    // Auto-poll every 60s while the tab is active.
    useEffect(() => {
        const id = setInterval(() => {
            if (document.visibilityState === "visible") fetchAll(false);
        }, 60_000);
        return () => clearInterval(id);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [filters]);

    useEffect(() => {
        const id = setInterval(() => setNowTick(Date.now()), 1000);
        return () => clearInterval(id);
    }, []);

    useEffect(() => {
        const onVis = () => {
            if (document.visibilityState === "visible") fetchAll(false);
        };
        document.addEventListener("visibilitychange", onVis);
        return () => document.removeEventListener("visibilitychange", onVis);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [filters]);

    // Apply date filter to daily costs (dashboard already filters server-side)
    const daily = useMemo(() => {
        if (!fromDate && !toDate) return allDaily;
        return allDaily.filter((d) => {
            const x = (d.date || "").slice(0, 10);
            if (fromDate && x < fromDate) return false;
            if (toDate && x > toDate) return false;
            return true;
        });
    }, [allDaily, fromDate, toDate]);

    // Aggregate daily ads
    const adsAgg = useMemo(() => {
        const totals = { snap: 0, snap2: 0, tiktok: 0, insta: 0, google: 0, products: 0 };
        for (const d of daily) {
            totals.snap += Number(d.snapchat_ads || 0);
            totals.snap2 += Number(d.snapchat_ads_2 || 0);
            totals.tiktok += Number(d.tiktok_ads || 0);
            totals.insta += Number(d.instagram_ads || 0);
            totals.google += Number(d.google_ads || 0);
            totals.products += Number(d.product_costs || 0);
        }
        const totalAds = totals.snap + totals.snap2 + totals.tiktok + totals.insta + totals.google;
        return { ...totals, totalAds, totalProducts: totals.products };
    }, [daily]);

    // Apply payment/shipping filters via case-insensitive substring match
    // (kept as a util in case we need client-side adjustments; primary
    //  filtering happens server-side now in /api/dashboard).

    // Aggregate from unified dashboard payload (single source of truth)
    const agg = useMemo(() => {
        if (!dashboard) {
            return {
                total_sales: 0, total_orders: 0, total_fees: 0, total_ship: 0,
                total_ads: 0, total_prods: 0, net: 0,
                payments: [], shippings: [], sources: [],
            };
        }
        const t = dashboard.totals || {};
        const payments = (dashboard.payment_breakdown || []).map((p) => ({
            name: p.name,
            orders_count: p.orders_count || 0,
            total_sales: p.total_sales || 0,
            fee_amount: p.fee_amount || 0,
        })).sort((a, b) => b.total_sales - a.total_sales);
        const shippings = (dashboard.shipping_breakdown || []).map((s) => ({
            name: s.name,
            orders_count: s.orders_count || 0,
            total_cost: s.total_cost || 0,
        })).sort((a, b) => b.orders_count - a.orders_count);
        const sources = (dashboard.source_breakdown || []).map((s) => ({
            name: s.name,
            orders_count: s.count || 0,
            total_sales: 0,
        }));
        return {
            total_sales: t.total_sales || 0,
            total_orders: t.total_orders || 0,
            total_fees: t.total_payment_fees || 0,
            total_ship: t.total_shipping_cost || 0,
            total_ads: t.daily_ads_total || 0,
            total_prods: t.daily_products_total || 0,
            net: t.net_profit || 0,
            payments, shippings, sources,
        };
    }, [dashboard]);

    // monthly trend
    const monthly = useMemo(() => {
        const items = (dashboard?.monthly || []).map((m) => ({
            month: m.month,
            sales: m.sales || 0,
            profit: m.profit || 0,
            ads: 0,
        }));
        // Merge daily ads into ads by month
        const adsMap = {};
        for (const d of daily) {
            const k = (d.date || "").slice(0, 7);
            adsMap[k] = (adsMap[k] || 0) + Number(d.snapchat_ads || 0)
                + Number(d.snapchat_ads_2 || 0) + Number(d.tiktok_ads || 0)
                + Number(d.instagram_ads || 0) + Number(d.google_ads || 0);
        }
        for (const it of items) it.ads = adsMap[it.month] || 0;
        return items;
    }, [dashboard, daily]);

    const hasData = (dashboard?.totals?.total_orders || 0) > 0
        || (dashboard?.recent_analyses?.length || 0) > 0;

    if (loading) return <div className="p-10 text-center" data-testid="reports-loading">جاري التحميل…</div>;

    return (
        <div className="space-y-8 animate-fade-in-up" data-testid="reports-page">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
                <div>
                    <h1 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>التقارير</h1>
                    <p className="text-muted-foreground mt-2 text-sm sm:text-base">
                        {(fromDate || toDate)
                            ? `عرض البيانات من ${fromDate || "البداية"} إلى ${toDate || "الآن"}`
                            : "تقارير مجمَّعة عبر جميع التحاليل المحفوظة لديك."}
                    </p>
                </div>
                <Link
                    to="/reports/ads"
                    className="inline-flex items-center gap-2 px-4 py-2.5 bg-brand text-white font-bold rounded-lg bg-brand-hover transition-colors shrink-0"
                    data-testid="reports-ads-link"
                >
                    <Megaphone size={18} weight="duotone" />
                    تقرير الإعلانات الموحَّد
                </Link>
            </div>

            {/* Advanced filters: date preset + payment + shipping */}
            <div className="flex items-stretch gap-2">
                <div className="flex-1">
                    <AdvancedFilters value={filters} onChange={setFilters} />
                </div>
                <button
                    type="button"
                    onClick={() => fetchAll(true)}
                    disabled={loading}
                    title="تحديث البيانات الآن (يحدّث تلقائياً كل دقيقة)"
                    className="px-3 py-2 rounded-lg border border-border bg-white font-bold text-sm hover:bg-accent transition-colors disabled:opacity-50 inline-flex items-center gap-1.5"
                    data-testid="reports-refresh-btn"
                >
                    <ArrowsClockwise size={16} weight="bold" className={loading ? "animate-spin" : ""} />
                    تحديث
                </button>
            </div>
            {lastUpdated && (
                <div className="text-xs text-muted-foreground -mt-2" data-testid="reports-last-updated">
                    آخر تحديث: {formatRelative(nowTick - lastUpdated)} • يحدِّث تلقائياً كل دقيقة
                </div>
            )}

            {!hasData ? (
                <div className="rounded-xl border border-border bg-white p-12 text-center">
                    <ChartPieSlice size={48} weight="duotone" className="text-brand mx-auto mb-3" />
                    <p className="text-muted-foreground">
                        {(fromDate || toDate) ? "لا توجد تحاليل ضمن الفترة المحددة." : "لا توجد بيانات بعد. ابدأ بتحليل ملف Excel من سلة."}
                    </p>
                </div>
            ) : (
                <>
                    {/* Aggregate KPIs */}
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                        {[
                            // iter-71: use reconciliation.transparency.total_sales as the
                            // source of truth so the KPI matches the Reports↔Accounts card.
                            { label: "إجمالي المبيعات", value: reconciliation?.transparency?.total_sales ?? agg.total_sales, accent: true },
                            { label: "إجمالي الطلبات", value: agg.total_orders, isInt: true },
                            { label: "رسوم الدفع", value: agg.total_fees },
                            { label: "الشحن", value: agg.total_ship },
                            { label: "الإعلانات (يومي)", value: adsAgg.totalAds },
                            { label: "مصاريف يومية", value: adsAgg.totalProducts },
                            { label: "عدد التحاليل", value: dashboard?.recent_analyses?.length || 0, isInt: true },
                            { label: "صافي الربح النهائي", value: agg.net - adsAgg.totalAds - adsAgg.totalProducts, accent: true },
                        ].map((c, idx) => (
                            <div key={idx} className={`rounded-xl border p-5 ${c.accent ? "bg-brand text-white border-brand" : "bg-white border-border"}`} data-testid={`agg-kpi-${idx}`}>
                                <div className={`text-sm mb-1 ${c.accent ? "text-white/80" : "text-muted-foreground"}`}>{c.label}</div>
                                <div className="num text-2xl font-extrabold" style={{ fontFamily: "Tajawal" }}>
                                    {c.isInt ? formatInt(c.value) : formatMoney(c.value)}
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Reports vs Accounts transparency card (iter-70) */}
                    {reconciliation?.transparency && (
                        <div className="rounded-xl border border-border bg-white p-5" data-testid="reports-vs-accounts-card">
                            <div className="flex items-center justify-between mb-4">
                                <div>
                                    <h3 className="text-base font-bold" style={{ fontFamily: "Tajawal" }}>
                                        المبيعات ↔ الأصول — توضيح الفرق
                                    </h3>
                                    <p className="text-xs text-muted-foreground mt-0.5">
                                        التقارير تجيب على "كم بعت؟" والأصول على "أين الأموال؟" — هنا تفصيل الفارق.
                                    </p>
                                </div>
                                <Link to="/reconciliation" className="text-xs text-brand font-bold hover:underline" data-testid="reports-goto-reconciliation">
                                    شاشة المطابقة →
                                </Link>
                            </div>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
                                <div className="rounded-lg bg-emerald-50 border border-emerald-200 p-4" data-testid="trx-total-sales">
                                    <div className="text-[11px] font-bold text-emerald-700">إجمالي المبيعات (التقارير)</div>
                                    <div className="num text-xl font-extrabold text-emerald-900 mt-1">
                                        {formatMoney(reconciliation.transparency.total_sales)}
                                    </div>
                                    <div className="text-[10px] text-emerald-700/70 mt-1">
                                        {formatInt(reconciliation.transparency.in_accounts_orders +
                                            reconciliation.transparency.unclassified_orders +
                                            reconciliation.transparency.empty_payment_method_orders)} طلب
                                    </div>
                                </div>
                                <div className="rounded-lg bg-sky-50 border border-sky-200 p-4" data-testid="trx-in-accounts">
                                    <div className="text-[11px] font-bold text-sky-700">داخل الأصول (المنصات الـ 6)</div>
                                    <div className="num text-xl font-extrabold text-sky-900 mt-1">
                                        {formatMoney(reconciliation.transparency.in_accounts)}
                                    </div>
                                    <div className="text-[10px] text-sky-700/70 mt-1">
                                        {formatInt(reconciliation.transparency.in_accounts_orders)} طلب
                                    </div>
                                </div>
                                <div className={`rounded-lg p-4 border ${reconciliation.transparency.gap === 0 ? "bg-slate-50 border-slate-200" : "bg-amber-50 border-amber-200"}`} data-testid="trx-gap">
                                    <div className={`text-[11px] font-bold ${reconciliation.transparency.gap === 0 ? "text-slate-700" : "text-amber-700"}`}>الفرق المُفسَّر</div>
                                    <div className={`num text-xl font-extrabold mt-1 ${reconciliation.transparency.gap === 0 ? "text-slate-900" : "text-amber-900"}`}>
                                        {formatMoney(reconciliation.transparency.gap)}
                                    </div>
                                    <div className={`text-[10px] mt-1 ${reconciliation.transparency.gap === 0 ? "text-slate-700/70" : "text-amber-700/70"}`}>
                                        {reconciliation.transparency.gap === 0 ? "الأرقام متطابقة" : "موضّح أدناه"}
                                    </div>
                                </div>
                            </div>

                            {(reconciliation.transparency.unclassified_amount > 0 ||
                                reconciliation.transparency.empty_payment_method_amount > 0) && (
                                <div className="rounded-lg border border-border overflow-hidden">
                                    <table className="w-full text-sm">
                                        <thead className="bg-slate-50/70 text-xs text-muted-foreground">
                                            <tr>
                                                <th className="text-right px-4 py-2 font-bold">السبب</th>
                                                <th className="text-right px-4 py-2 font-bold">القيمة الخام</th>
                                                <th className="text-right px-4 py-2 font-bold">عدد الطلبات</th>
                                                <th className="text-right px-4 py-2 font-bold">المبلغ</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {reconciliation.transparency.unclassified_buckets.map((b) => (
                                                <tr key={b.raw} className="border-t border-border" data-testid={`trx-row-${b.raw}`}>
                                                    <td className="px-4 py-2.5 text-muted-foreground">طريقة دفع غير مُصنَّفة</td>
                                                    <td className="px-4 py-2.5 font-mono text-xs">{b.raw}</td>
                                                    <td className="px-4 py-2.5 num">{formatInt(b.count)}</td>
                                                    <td className="px-4 py-2.5 num font-bold text-amber-700">{formatMoney(b.amount)}</td>
                                                </tr>
                                            ))}
                                            {reconciliation.transparency.empty_payment_method_amount > 0 && (
                                                <tr className="border-t border-border" data-testid="trx-row-empty">
                                                    <td className="px-4 py-2.5 text-muted-foreground">طلبات بدون طريقة دفع</td>
                                                    <td className="px-4 py-2.5 font-mono text-xs">—</td>
                                                    <td className="px-4 py-2.5 num">{formatInt(reconciliation.transparency.empty_payment_method_orders)}</td>
                                                    <td className="px-4 py-2.5 num font-bold text-amber-700">{formatMoney(reconciliation.transparency.empty_payment_method_amount)}</td>
                                                </tr>
                                            )}
                                        </tbody>
                                    </table>
                                </div>
                            )}

                            <div className="text-[10px] text-muted-foreground mt-3 leading-relaxed">
                                الفلاتر المطبَّقة: حالات الطلبات المُعتمدة ({reconciliation.transparency.filters_applied.report_included_statuses?.length || 0})
                                {reconciliation.transparency.filters_applied.hide_inferred_date_orders && " · إخفاء الطلبات مستنتجة التاريخ"}
                                · نفس الفلاتر تماماً مُطبَّقة على المزامنة لضمان توافق الأرقام.
                            </div>
                        </div>
                    )}

                    {/* iter-73 — Per-provider commission cards (collapsible) */}
                    {(() => {
                        const breakdown = dashboard?.payment_breakdown || [];
                        const findBy = (rx) => breakdown.find(
                            (p) => rx.test(p?.name || "") || rx.test(p?.normalized_payment_method || "")
                        );
                        const salla  = findBy(/سلة|salla/i);
                        const tamara = findBy(/تمارا|tamara/i);
                        const tabby  = findBy(/تابي|تابى|tabby/i);
                        const emkan  = findBy(/إمكان|امكان|emkan/i);
                        const cards = [
                            { p: salla,  accent: "emerald", testid: "provider-card-salla" },
                            { p: tamara, accent: "violet",  testid: "provider-card-tamara" },
                            { p: tabby,  accent: "sky",     testid: "provider-card-tabby" },
                            { p: emkan,  accent: "amber",   testid: "provider-card-emkan" },
                        ].filter((c) => c.p);
                        if (cards.length === 0) return null;
                        return (
                            <div className="rounded-xl border border-border bg-white p-5" data-testid="provider-commission-section">
                                <div className="flex items-center justify-between mb-4">
                                    <div>
                                        <h3 className="text-base font-bold" style={{ fontFamily: "Tajawal" }}>
                                            صافي مزوّدات الدفع بعد العمولة
                                        </h3>
                                        <p className="text-xs text-muted-foreground mt-0.5">
                                            لكل مزوّد دفع: الإجمالي قبل العمولة، الصافي بعد خصمها، عدد الطلبات، ونسبة العمولة المعمول بها.
                                        </p>
                                    </div>
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                    {cards.map(({ p, accent, testid }) => (
                                        <ProviderCommissionCard
                                            key={testid}
                                            provider={p}
                                            accent={accent}
                                            testid={testid}
                                        />
                                    ))}
                                </div>
                            </div>
                        );
                    })()}

                    {/* Ads breakdown by platform */}
                    <div className="rounded-xl border border-border bg-white p-6">
                        <div className="flex items-center justify-between mb-5">
                            <div>
                                <h3 className="text-xl font-bold" style={{ fontFamily: "Tajawal" }}>تكاليف الإعلانات حسب المنصة</h3>
                                <p className="text-xs text-muted-foreground mt-1">إجماليات الإعلانات والمنتجات من سجل التكاليف اليومية للفترة المحددة.</p>
                            </div>
                            <div className="text-end">
                                <div className="text-xs text-muted-foreground">إجمالي الإعلانات</div>
                                <div className="text-2xl font-extrabold text-brand num" style={{ fontFamily: "Tajawal" }}>{formatMoney(adsAgg.totalAds)}</div>
                            </div>
                        </div>
                        {adsAgg.totalAds === 0 && adsAgg.totalProducts === 0 ? (
                            <div className="text-center py-8 text-muted-foreground text-sm">
                                لم تسجل أي تكاليف يومية في هذه الفترة.
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                                <table
                                    className="w-full text-right text-sm border-collapse
                                        [&_th]:px-3 [&_th]:border-s [&_th]:border-border
                                        [&_td]:px-3 [&_td]:border-s [&_td]:border-border
                                        [&_th:first-child]:border-s-0 [&_td:first-child]:border-s-0"
                                    data-testid="agg-ads-table"
                                >
                                    <thead className="text-muted-foreground bg-accent/40 border-b-2 border-border">
                                        <tr>
                                            <th className="py-3 font-semibold">المنصة</th>
                                            <th className="py-3 font-semibold">الإجمالي (ر.س)</th>
                                            <th className="py-3 font-semibold">% من الإعلانات</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {[
                                            { name: "سناب شات", value: adsAgg.snap },
                                            { name: "سناب شات 2", value: adsAgg.snap2 },
                                            { name: "تيك توك", value: adsAgg.tiktok },
                                            { name: "إنستقرام", value: adsAgg.insta },
                                            { name: "جوجل", value: adsAgg.google },
                                        ].map((row, i) => (
                                            <tr key={i} className="border-b border-border last:border-0 hover:bg-accent/30 transition-colors">
                                                <td className="py-2.5 font-semibold">{row.name}</td>
                                                <td className="py-2.5 num font-bold">{formatMoney(row.value)}</td>
                                                <td className="py-2.5 num text-muted-foreground">
                                                    {adsAgg.totalAds > 0 ? ((row.value / adsAgg.totalAds) * 100).toFixed(1) + "%" : "—"}
                                                </td>
                                            </tr>
                                        ))}
                                        <tr className="bg-accent/30 font-bold">
                                            <td className="py-2.5">مصاريف يومية</td>
                                            <td className="py-2.5 num text-red-700">{formatMoney(adsAgg.totalProducts)}</td>
                                            <td className="py-2.5 text-muted-foreground">—</td>
                                        </tr>
                                    </tbody>
                                </table>
                                <div className="h-64" data-testid="ads-pie">
                                    <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
                                        <PieChart>
                                            <Pie data={[
                                                { name: "سناب شات", value: adsAgg.snap },
                                                { name: "سناب شات 2", value: adsAgg.snap2 },
                                                { name: "تيك توك", value: adsAgg.tiktok },
                                                { name: "إنستقرام", value: adsAgg.insta },
                                                { name: "جوجل", value: adsAgg.google },
                                            ].filter(x => x.value > 0)}
                                                 dataKey="value" outerRadius={90}>
                                                {[0,1,2,3,4].map((i) => <Cell key={i} fill={COLORS[(i + 1) % COLORS.length]} stroke="#fff" strokeWidth={2} className="cursor-pointer" />)}
                                            </Pie>
                                            <Tooltip
                                                formatter={(v, name) => [formatMoney(v) + " ر.س", name]}
                                                contentStyle={{ direction: "rtl", fontFamily: "Cairo", borderRadius: 8, border: "1px solid #E5E7EB" }}
                                            />
                                            <Legend wrapperStyle={{ fontFamily: "Cairo", fontSize: 12 }} />
                                        </PieChart>
                                    </ResponsiveContainer>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Monthly chart */}
                    <div className="rounded-xl border border-border bg-white p-6">
                        <h2 className="text-2xl font-bold mb-5" style={{ fontFamily: "Tajawal" }}>المبيعات والأرباح والإعلانات</h2>
                        <div className="h-80" data-testid="monthly-bar-chart">
                            <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
                                <BarChart data={monthly}>
                                    <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                                    <XAxis dataKey="month" tick={{ fontSize: 12, fontFamily: "Cairo" }} reversed />
                                    <YAxis tick={{ fontSize: 12, fontFamily: "Cairo" }} orientation="right" />
                                    <Tooltip contentStyle={{ direction: "rtl", fontFamily: "Cairo" }} />
                                    <Legend wrapperStyle={{ fontFamily: "Cairo" }} />
                                    <Bar dataKey="sales" name="المبيعات" fill="#0A3622" />
                                    <Bar dataKey="profit" name="صافي الربح" fill="#D4AF37" />
                                    <Bar dataKey="ads" name="الإعلانات" fill="#DC2626" />
                                </BarChart>
                            </ResponsiveContainer>
                        </div>
                    </div>

                    {/* Pies — payments + shipping */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <div className="rounded-xl border border-border bg-white p-6">
                            <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>طرق الدفع — المبيعات</h3>
                            <div className="h-72" data-testid="pay-pie">
                                <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
                                    <PieChart>
                                        <Pie data={agg.payments.map((p) => ({ name: p.name, value: p.total_sales }))}
                                             dataKey="value" outerRadius={100}>
                                            {agg.payments.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} stroke="#fff" strokeWidth={2} className="cursor-pointer" />)}
                                        </Pie>
                                        <Tooltip
                                            formatter={(v, name) => [formatMoney(v) + " ر.س", name]}
                                            contentStyle={{ direction: "rtl", fontFamily: "Cairo", borderRadius: 8, border: "1px solid #E5E7EB" }}
                                        />
                                        <Legend wrapperStyle={{ fontFamily: "Cairo", fontSize: 12 }} />
                                    </PieChart>
                                </ResponsiveContainer>
                            </div>
                        </div>

                        <div className="rounded-xl border border-border bg-white p-6">
                            <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>شركات الشحن — الطلبات</h3>
                            <div className="h-72" data-testid="ship-pie">
                                <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
                                    <PieChart>
                                        <Pie data={agg.shippings.map((s) => ({ name: s.name, value: s.orders_count }))}
                                             dataKey="value" outerRadius={100}>
                                            {agg.shippings.map((_, i) => <Cell key={i} fill={COLORS[(i + 2) % COLORS.length]} stroke="#fff" strokeWidth={2} className="cursor-pointer" />)}
                                        </Pie>
                                        <Tooltip
                                            formatter={(v, name) => [formatInt(v) + " طلب", name]}
                                            contentStyle={{ direction: "rtl", fontFamily: "Cairo", borderRadius: 8, border: "1px solid #E5E7EB" }}
                                        />
                                        <Legend wrapperStyle={{ fontFamily: "Cairo", fontSize: 12 }} />
                                    </PieChart>
                                </ResponsiveContainer>
                            </div>
                        </div>
                    </div>

                    {/* Order sources */}
                    <div className="rounded-xl border border-border bg-white p-6">
                        <div className="flex items-center justify-between mb-5">
                            <div>
                                <h3 className="text-xl font-bold" style={{ fontFamily: "Tajawal" }}>مصادر الطلبات</h3>
                                <p className="text-xs text-muted-foreground mt-1">عدد ومبيعات الطلبات حسب مصدر الطلب من ملفات Excel.</p>
                            </div>
                        </div>
                        {agg.sources.length === 0 ? (
                            <div className="text-center py-8 text-muted-foreground text-sm">
                                لم يتم العثور على عمود "مصدر الطلب" في الملفات. تأكد من أن الملف يحتوي على عمود بهذا الاسم.
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                                <table
                                    className="w-full text-right text-sm border-collapse
                                        [&_th]:px-3 [&_th]:border-s [&_th]:border-border
                                        [&_td]:px-3 [&_td]:border-s [&_td]:border-border
                                        [&_th:first-child]:border-s-0 [&_td:first-child]:border-s-0"
                                    data-testid="agg-sources-table"
                                >
                                    <thead className="text-muted-foreground bg-accent/40 border-b-2 border-border">
                                        <tr>
                                            <th className="py-3 font-semibold">مصدر الطلب</th>
                                            <th className="py-3 font-semibold">عدد الطلبات</th>
                                            <th className="py-3 font-semibold">إجمالي المبيعات</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {agg.sources.map((s, i) => (
                                            <tr key={i} className="border-b border-border last:border-0 hover:bg-accent/30 transition-colors">
                                                <td className="py-2.5 font-semibold">{s.name}</td>
                                                <td className="py-2.5 num font-bold text-brand">{formatInt(s.orders_count)}</td>
                                                <td className="py-2.5 num">{formatMoney(s.total_sales || 0)}</td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                                <div className="h-64" data-testid="sources-pie">
                                    <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
                                        <PieChart>
                                            <Pie data={agg.sources.map((s) => ({ name: s.name, value: s.orders_count }))}
                                                 dataKey="value" outerRadius={90}>
                                                {agg.sources.map((_, i) => <Cell key={i} fill={COLORS[(i + 4) % COLORS.length]} stroke="#fff" strokeWidth={2} className="cursor-pointer" />)}
                                            </Pie>
                                            <Tooltip
                                                formatter={(v, name) => [formatInt(v) + " طلب", name]}
                                                contentStyle={{ direction: "rtl", fontFamily: "Cairo", borderRadius: 8, border: "1px solid #E5E7EB" }}
                                            />
                                            <Legend wrapperStyle={{ fontFamily: "Cairo", fontSize: 12 }} />
                                        </PieChart>
                                    </ResponsiveContainer>
                                </div>
                            </div>
                        )}
                    </div>

                    {/* Aggregate tables */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <div className="rounded-xl border border-border bg-white p-4 sm:p-6">
                            <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>إجمالي طرق الدفع</h3>
                            <div className="overflow-x-auto -mx-4 sm:mx-0 px-4 sm:px-0">
                            <table
                                className="w-full text-right text-sm border-collapse min-w-[480px]
                                    [&_th]:px-3 [&_th]:border-s [&_th]:border-border
                                    [&_td]:px-3 [&_td]:border-s [&_td]:border-border
                                    [&_th:first-child]:border-s-0 [&_td:first-child]:border-s-0"
                                data-testid="agg-payments-table"
                            >
                                <thead className="text-muted-foreground bg-accent/40 border-b-2 border-border">
                                    <tr>
                                        <th className="py-3 font-semibold">الاسم</th>
                                        <th className="py-3 font-semibold">الطلبات</th>
                                        <th className="py-3 font-semibold">المبيعات</th>
                                        <th className="py-3 font-semibold">العمولة</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {agg.payments.map((p, i) => (
                                        <tr key={i} className="border-b border-border last:border-0 hover:bg-accent/30 transition-colors">
                                            <td className="py-2.5 font-semibold">{p.name}</td>
                                            <td className="py-2.5 num">{formatInt(p.orders_count)}</td>
                                            <td className="py-2.5 num">{formatMoney(p.total_sales)}</td>
                                            <td className="py-2.5 num text-red-700">{formatMoney(p.fee_amount)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            </div>
                        </div>

                        <div className="rounded-xl border border-border bg-white p-4 sm:p-6">
                            <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>إجمالي شركات الشحن</h3>
                            <div className="overflow-x-auto -mx-4 sm:mx-0 px-4 sm:px-0">
                            <table
                                className="w-full text-right text-sm border-collapse min-w-[400px]
                                    [&_th]:px-3 [&_th]:border-s [&_th]:border-border
                                    [&_td]:px-3 [&_td]:border-s [&_td]:border-border
                                    [&_th:first-child]:border-s-0 [&_td:first-child]:border-s-0"
                                data-testid="agg-shipping-table"
                            >
                                <thead className="text-muted-foreground bg-accent/40 border-b-2 border-border">
                                    <tr>
                                        <th className="py-3 font-semibold">الاسم</th>
                                        <th className="py-3 font-semibold">الطلبات</th>
                                        <th className="py-3 font-semibold">الإجمالي</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {agg.shippings.map((s, i) => (
                                        <tr key={i} className="border-b border-border last:border-0 hover:bg-accent/30 transition-colors">
                                            <td className="py-2.5 font-semibold">{s.name}</td>
                                            <td className="py-2.5 num">{formatInt(s.orders_count)}</td>
                                            <td className="py-2.5 num font-semibold">{formatMoney(s.total_cost)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                            </div>
                        </div>
                    </div>
                </>
            )}

            {/* Iteration 26: Product Sales Report — per-product KPIs with
                cost-completeness flags and totals over only complete rows. */}
            <ProductSalesReport fromDate={fromDate} toDate={toDate} />
        </div>
    );
}
