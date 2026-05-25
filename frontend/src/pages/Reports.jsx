import { useEffect, useState } from "react";
import { ChartPieSlice } from "@phosphor-icons/react";
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
    PieChart, Pie, Cell,
} from "recharts";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";

const COLORS = ["#0A3622", "#D4AF37", "#16A34A", "#D97706", "#0EA5E9", "#7C3AED", "#DC2626", "#0891B2"];

export default function Reports() {
    const [analyses, setAnalyses] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/analyses");
                setAnalyses(data || []);
            } finally { setLoading(false); }
        })();
    }, []);

    // Aggregate across all analyses
    const agg = (() => {
        let total_sales = 0, total_orders = 0, total_fees = 0, total_ship = 0, total_ads = 0, total_prods = 0, net = 0;
        const paymentMap = {};
        const shipMap = {};
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
        }
        return {
            total_sales, total_orders, total_fees, total_ship, total_ads, total_prods, net,
            payments: Object.values(paymentMap).sort((a, b) => b.total_sales - a.total_sales),
            shippings: Object.values(shipMap).sort((a, b) => b.orders_count - a.orders_count),
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
                    تقارير مجمَّعة عبر جميع التحاليل المحفوظة لديك.
                </p>
            </div>

            {analyses.length === 0 ? (
                <div className="rounded-xl border border-border bg-white p-12 text-center">
                    <ChartPieSlice size={48} weight="duotone" className="text-brand mx-auto mb-3" />
                    <p className="text-muted-foreground">لا توجد بيانات بعد. ابدأ بتحليل ملف Excel من سلة.</p>
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

                    {/* Aggregate tables */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <div className="rounded-xl border border-border bg-white p-6">
                            <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>إجمالي طرق الدفع</h3>
                            <table className="w-full text-right text-sm" data-testid="agg-payments-table">
                                <thead className="text-muted-foreground border-b border-border">
                                    <tr>
                                        <th className="py-2.5 font-semibold">الاسم</th>
                                        <th className="py-2.5 font-semibold">الطلبات</th>
                                        <th className="py-2.5 font-semibold">المبيعات</th>
                                        <th className="py-2.5 font-semibold">العمولة</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {agg.payments.map((p, i) => (
                                        <tr key={i} className="border-b border-border last:border-0">
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
                            <table className="w-full text-right text-sm" data-testid="agg-shipping-table">
                                <thead className="text-muted-foreground border-b border-border">
                                    <tr>
                                        <th className="py-2.5 font-semibold">الاسم</th>
                                        <th className="py-2.5 font-semibold">الطلبات</th>
                                        <th className="py-2.5 font-semibold">الإجمالي</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {agg.shippings.map((s, i) => (
                                        <tr key={i} className="border-b border-border last:border-0">
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
