import { useEffect, useMemo, useState } from "react";
import { ChartPieSlice, CalendarBlank } from "@phosphor-icons/react";
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
    PieChart, Pie, Cell,
} from "recharts";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";

const COLORS = ["#0A3622", "#D4AF37", "#16A34A", "#D97706", "#0EA5E9", "#7C3AED", "#DC2626", "#0891B2"];

export default function Reports() {
    const [allAnalyses, setAllAnalyses] = useState([]);
    const [loading, setLoading] = useState(true);
    const [fromDate, setFromDate] = useState("");
    const [toDate, setToDate] = useState("");

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/analyses");
                setAllAnalyses(data || []);
            } finally { setLoading(false); }
        })();
    }, []);

    const setPreset = (kind) => {
        const today = new Date();
        const iso = (d) => d.toISOString().slice(0, 10);
        let f = "", t = iso(today);
        if (kind === "today") { f = t; }
        else if (kind === "7d") { const d = new Date(today); d.setDate(d.getDate() - 6); f = iso(d); }
        else if (kind === "30d") { const d = new Date(today); d.setDate(d.getDate() - 29); f = iso(d); }
        else if (kind === "month") { const d = new Date(today.getFullYear(), today.getMonth(), 1); f = iso(d); }
        else if (kind === "year") { const d = new Date(today.getFullYear(), 0, 1); f = iso(d); }
        setFromDate(f);
        setToDate(t);
    };
    const resetRange = () => { setFromDate(""); setToDate(""); };

    // Apply date filter
    const analyses = useMemo(() => {
        if (!fromDate && !toDate) return allAnalyses;
        return allAnalyses.filter((a) => {
            const d = (a.date || "").slice(0, 10);
            if (fromDate && d < fromDate) return false;
            if (toDate && d > toDate) return false;
            return true;
        });
    }, [allAnalyses, fromDate, toDate]);

    // Aggregate across all analyses
    const agg = (() => {
        let total_sales = 0, total_orders = 0, total_fees = 0, total_ship = 0, total_ads = 0, total_prods = 0, net = 0;
        const paymentMap = {};
        const shipMap = {};
        const sourceMap = {};
        for (const a of analyses) {
            const s = a.report.summary;
            total_sales += s.total_sales;
            total_orders += s.total_orders;
            total_fees += s.total_payment_fees;
            total_ship += s.total_shipping_cost;
            total_ads += s.total_ads_cost;
            total_prods += s.total_product_cost;
            net += s.net_profit;
            for (const p of a.report.payment_breakdown || []) {
                if (!paymentMap[p.name]) paymentMap[p.name] = { name: p.name, orders_count: 0, total_sales: 0, fee_amount: 0 };
                paymentMap[p.name].orders_count += p.orders_count;
                paymentMap[p.name].total_sales += p.total_sales;
                paymentMap[p.name].fee_amount += p.fee_amount;
            }
            for (const sh of a.report.shipping_breakdown || []) {
                if (!shipMap[sh.name]) shipMap[sh.name] = { name: sh.name, orders_count: 0, total_cost: 0 };
                shipMap[sh.name].orders_count += sh.orders_count;
                shipMap[sh.name].total_cost += sh.total_cost;
            }
            for (const src of a.report.order_sources || []) {
                if (!sourceMap[src.name]) sourceMap[src.name] = { name: src.name, orders_count: 0, total_sales: 0 };
                sourceMap[src.name].orders_count += src.orders_count;
                sourceMap[src.name].total_sales += src.total_sales || 0;
            }
        }
        return {
            total_sales, total_orders, total_fees, total_ship, total_ads, total_prods, net,
            payments: Object.values(paymentMap).sort((a, b) => b.total_sales - a.total_sales),
            shippings: Object.values(shipMap).sort((a, b) => b.orders_count - a.orders_count),
            sources: Object.values(sourceMap).sort((a, b) => b.orders_count - a.orders_count),
        };
    })();

    // monthly trend
    const monthly = (() => {
        const m = {};
        for (const a of analyses) {
            const k = (a.date || "").slice(0, 7);
            if (!m[k]) m[k] = { month: k, sales: 0, profit: 0, ads: 0 };
            m[k].sales += a.report.summary.total_sales;
            m[k].profit += a.report.summary.net_profit;
            m[k].ads += a.report.summary.total_ads_cost;
        }
        return Object.values(m).sort((x, y) => x.month.localeCompare(y.month));
    })();

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

            {/* Date range filter */}
            <div className="rounded-xl border border-border bg-white p-4 flex flex-col md:flex-row md:items-center gap-3 flex-wrap" data-testid="reports-date-filter">
                <div className="flex items-center gap-2 text-sm font-semibold text-brand">
                    <CalendarBlank size={20} weight="duotone" /> الفترة الزمنية:
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    <label className="text-xs text-muted-foreground">من</label>
                    <input
                        type="date"
                        value={fromDate}
                        onChange={(e) => setFromDate(e.target.value)}
                        className="px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                        data-testid="reports-date-from"
                        dir="ltr"
                    />
                    <label className="text-xs text-muted-foreground ms-2">إلى</label>
                    <input
                        type="date"
                        value={toDate}
                        onChange={(e) => setToDate(e.target.value)}
                        className="px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                        data-testid="reports-date-to"
                        dir="ltr"
                    />
                    {(fromDate || toDate) && (
                        <button
                            onClick={resetRange}
                            className="px-3 py-2 border border-border text-sm font-semibold rounded-lg hover:bg-accent transition-colors"
                            data-testid="reports-reset-date"
                        >إعادة تعيين</button>
                    )}
                </div>
                <div className="flex items-center gap-1.5 md:ms-auto flex-wrap">
                    {[
                        { k: "today", label: "اليوم" },
                        { k: "7d", label: "آخر أسبوع" },
                        { k: "30d", label: "آخر شهر" },
                        { k: "month", label: "هذا الشهر" },
                        { k: "year", label: "هذه السنة" },
                    ].map(p => (
                        <button key={p.k} onClick={() => setPreset(p.k)}
                            className="px-3 py-1.5 border border-border rounded-lg text-xs font-semibold hover:bg-brand hover:text-white hover:border-brand transition-colors"
                            data-testid={`reports-preset-${p.k}`}
                        >{p.label}</button>
                    ))}
                </div>
            </div>

            {analyses.length === 0 ? (
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
                            { label: "الإعلانات", value: agg.total_ads },
                            { label: "المنتجات", value: agg.total_prods },
                            { label: "عدد التحاليل", value: analyses.length, isInt: true },
                            { label: "صافي الربح", value: agg.net, accent: true },
                        ].map((c, idx) => (
                            <div key={idx} className={`rounded-xl border p-5 ${c.accent ? "bg-brand text-white border-brand" : "bg-white border-border"}`} data-testid={`agg-kpi-${idx}`}>
                                <div className={`text-sm mb-1 ${c.accent ? "text-white/80" : "text-muted-foreground"}`}>{c.label}</div>
                                <div className="num text-2xl font-extrabold" style={{ fontFamily: "Tajawal" }}>
                                    {c.isInt ? formatInt(c.value) : formatMoney(c.value)}
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Monthly chart */}
                    <div className="rounded-xl border border-border bg-white p-6">
                        <h2 className="text-2xl font-bold mb-5" style={{ fontFamily: "Tajawal" }}>المبيعات والأرباح والإعلانات</h2>
                        <div className="h-80" data-testid="monthly-bar-chart">
                            <ResponsiveContainer width="100%" height="100%">
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
                                <ResponsiveContainer width="100%" height="100%">
                                    <PieChart>
                                        <Pie data={agg.payments.map((p) => ({ name: p.name, value: p.total_sales }))}
                                             dataKey="value" outerRadius={90} label={(e) => e.name}>
                                            {agg.payments.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                                        </Pie>
                                        <Tooltip formatter={(v) => formatMoney(v) + " ر.س"} contentStyle={{ direction: "rtl", fontFamily: "Cairo" }} />
                                    </PieChart>
                                </ResponsiveContainer>
                            </div>
                        </div>

                        <div className="rounded-xl border border-border bg-white p-6">
                            <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>شركات الشحن — الطلبات</h3>
                            <div className="h-72" data-testid="ship-pie">
                                <ResponsiveContainer width="100%" height="100%">
                                    <PieChart>
                                        <Pie data={agg.shippings.map((s) => ({ name: s.name, value: s.orders_count }))}
                                             dataKey="value" outerRadius={90} label={(e) => e.name}>
                                            {agg.shippings.map((_, i) => <Cell key={i} fill={COLORS[(i + 2) % COLORS.length]} />)}
                                        </Pie>
                                        <Tooltip formatter={(v) => formatInt(v) + " طلب"} contentStyle={{ direction: "rtl", fontFamily: "Cairo" }} />
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
                                    <ResponsiveContainer width="100%" height="100%">
                                        <PieChart>
                                            <Pie data={agg.sources.map((s) => ({ name: s.name, value: s.orders_count }))}
                                                 dataKey="value" outerRadius={90} label={(e) => e.name}>
                                                {agg.sources.map((_, i) => <Cell key={i} fill={COLORS[(i + 4) % COLORS.length]} />)}
                                            </Pie>
                                            <Tooltip formatter={(v) => formatInt(v) + " طلب"} contentStyle={{ direction: "rtl", fontFamily: "Cairo" }} />
                                            <Legend wrapperStyle={{ fontFamily: "Cairo" }} />
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
