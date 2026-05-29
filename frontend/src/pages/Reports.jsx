import { useEffect, useMemo, useState } from "react";
import { ChartPieSlice, CalendarBlank } from "@phosphor-icons/react";
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
    PieChart, Pie, Cell,
} from "recharts";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";
import AdvancedFilters, { defaultFilters, filtersToQueryString } from "../components/AdvancedFilters";

const COLORS = ["#0A3622", "#D4AF37", "#16A34A", "#D97706", "#0EA5E9", "#7C3AED", "#DC2626", "#0891B2"];

export default function Reports() {
    const [dashboard, setDashboard] = useState(null);
    const [allDaily, setAllDaily] = useState([]);
    const [loading, setLoading] = useState(true);
    const [filters, setFilters] = useState(defaultFilters());
    const fromDate = filters.from;
    const toDate = filters.to;

    useEffect(() => {
        (async () => {
            setLoading(true);
            try {
                const qs = filtersToQueryString(filters);
                const [dashRes, dailyRes] = await Promise.all([
                    api.get(`/dashboard${qs ? "?" + qs : ""}`),
                    api.get("/daily-costs"),
                ]);
                setDashboard(dashRes.data || null);
                setAllDaily(dailyRes.data || []);
            } finally { setLoading(false); }
        })();
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
            <div>
                <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>التقارير</h1>
                <p className="text-muted-foreground mt-2 text-base">
                    {(fromDate || toDate)
                        ? `عرض البيانات من ${fromDate || "البداية"} إلى ${toDate || "الآن"}`
                        : "تقارير مجمَّعة عبر جميع التحاليل المحفوظة لديك."}
                </p>
            </div>

            {/* Advanced filters: date preset + payment + shipping */}
            <AdvancedFilters value={filters} onChange={setFilters} />

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
                            { label: "إجمالي المبيعات", value: agg.total_sales, accent: true },
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
                        <div className="rounded-xl border border-border bg-white p-6">
                            <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>إجمالي طرق الدفع</h3>
                            <table
                                className="w-full text-right text-sm border-collapse
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

                        <div className="rounded-xl border border-border bg-white p-6">
                            <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>إجمالي شركات الشحن</h3>
                            <table
                                className="w-full text-right text-sm border-collapse
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
                </>
            )}
        </div>
    );
}
