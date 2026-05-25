import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
    Coins, ShoppingBag, Receipt, Truck, Megaphone, Package, TrendUp,
    FilePdf, FileXls, ArrowLeft, Warning,
} from "@phosphor-icons/react";
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid,
    PieChart, Pie, Cell, Legend,
} from "recharts";
import api, { API_BASE } from "../lib/api";
import { formatMoney, formatInt, formatPercent } from "../lib/format";

const CHART_COLORS = ["#0A3622", "#D4AF37", "#16A34A", "#D97706", "#0EA5E9", "#7C3AED", "#DC2626", "#0891B2"];

function StatCard({ icon: Icon, label, value, accent = false, testid }) {
    return (
        <div
            className={`rounded-xl border p-5 ${accent ? "bg-brand text-white border-brand" : "bg-white border-border"}`}
            data-testid={testid}
        >
            <div className="flex items-center justify-between mb-3">
                <div className={`w-10 h-10 rounded-lg flex items-center justify-center ${accent ? "bg-white/10 text-gold" : "bg-accent text-brand"}`}>
                    <Icon size={22} weight="duotone" />
                </div>
            </div>
            <div className={`text-sm mb-1 ${accent ? "text-white/80" : "text-muted-foreground"}`}>{label}</div>
            <div className="num text-2xl font-extrabold" style={{ fontFamily: "Tajawal" }}>{value}</div>
        </div>
    );
}

export default function AnalysisResult() {
    const { id } = useParams();
    const [item, setItem] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get(`/analyses/${id}`);
                setItem(data);
            } finally { setLoading(false); }
        })();
    }, [id]);

    const downloadExport = async (kind) => {
        const url = `${API_BASE}/analyses/${id}/export/${kind}`;
        const token = localStorage.getItem("access_token");
        const res = await fetch(url, { credentials: "include", headers: token ? { Authorization: `Bearer ${token}` } : {} });
        const blob = await res.blob();
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = `hesab-report-${id}.${kind === "pdf" ? "pdf" : "xlsx"}`;
        link.click();
        URL.revokeObjectURL(link.href);
    };

    if (loading) return <div className="p-10 text-center" data-testid="analysis-loading">جاري تحميل التقرير…</div>;
    if (!item) return <div className="p-10 text-center text-red-600">التحليل غير موجود</div>;

    const r = item.report;
    const s = r.summary;

    const paymentPie = r.payment_breakdown.map((p) => ({ name: p.name, value: p.total_sales }));
    const shippingBars = r.shipping_breakdown.map((sc) => ({ name: sc.name, "عدد الطلبات": sc.orders_count, "الإجمالي": sc.total_cost }));

    return (
        <div className="space-y-8 animate-fade-in-up" data-testid="analysis-page">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
                <div>
                    <Link to="/history" className="text-sm text-muted-foreground hover:text-brand inline-flex items-center gap-1 mb-3">
                        <ArrowLeft size={16} /> العودة إلى السجل
                    </Link>
                    <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>{item.name}</h1>
                    <p className="text-muted-foreground mt-2 text-base">
                        التاريخ: <span className="num">{item.date}</span> • المصدر: {item.filename}
                    </p>
                </div>
                <div className="flex gap-2">
                    <button
                        onClick={() => downloadExport("excel")}
                        className="inline-flex items-center gap-2 px-4 py-2.5 bg-white border border-border rounded-lg hover:bg-accent font-semibold transition-colors"
                        data-testid="export-excel-btn"
                    >
                        <FileXls size={20} weight="duotone" /> تصدير Excel
                    </button>
                    <button
                        onClick={() => downloadExport("pdf")}
                        className="inline-flex items-center gap-2 px-4 py-2.5 bg-brand text-white rounded-lg bg-brand-hover font-semibold transition-colors"
                        data-testid="export-pdf-btn"
                    >
                        <FilePdf size={20} weight="duotone" /> تصدير PDF
                    </button>
                </div>
            </div>

            {/* KPI */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard icon={Coins} label="إجمالي المبيعات (ر.س)" value={formatMoney(s.total_sales)} testid="stat-total-sales" />
                <StatCard icon={ShoppingBag} label="عدد الطلبات" value={formatInt(s.total_orders)} testid="stat-total-orders" />
                <StatCard icon={Receipt} label="رسوم بوابات الدفع" value={formatMoney(s.total_payment_fees)} testid="stat-payment-fees" />
                <StatCard icon={Truck} label="تكاليف الشحن" value={formatMoney(s.total_shipping_cost)} testid="stat-shipping" />
                <StatCard icon={Megaphone} label="إجمالي الإعلانات" value={formatMoney(s.total_ads_cost)} testid="stat-ads" />
                <StatCard icon={Package} label="تكاليف المنتجات" value={formatMoney(s.total_product_cost)} testid="stat-products" />
                <StatCard icon={TrendUp} label="صافي بعد العمولات" value={formatMoney(s.net_revenue_after_fees)} testid="stat-net-after-fees" />
                <StatCard icon={TrendUp} label="صافي الربح النهائي" value={formatMoney(s.net_profit)} accent testid="stat-net-profit" />
            </div>

            {/* Charts */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <div className="rounded-xl border border-border bg-white p-6">
                    <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>توزيع المبيعات حسب طريقة الدفع</h3>
                    <div className="h-72" data-testid="payment-pie-chart">
                        <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                                <Pie data={paymentPie} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={90} label={(e) => e.name}>
                                    {paymentPie.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
                                </Pie>
                                <Tooltip formatter={(v) => formatMoney(v) + " ر.س"} contentStyle={{ direction: "rtl", fontFamily: "Cairo" }} />
                                <Legend wrapperStyle={{ fontFamily: "Cairo" }} />
                            </PieChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                <div className="rounded-xl border border-border bg-white p-6">
                    <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>شركات الشحن</h3>
                    <div className="h-72" data-testid="shipping-bar-chart">
                        <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={shippingBars} layout="vertical">
                                <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                                <XAxis type="number" tick={{ fontSize: 12, fontFamily: "Cairo" }} />
                                <YAxis dataKey="name" type="category" tick={{ fontSize: 12, fontFamily: "Cairo" }} orientation="right" />
                                <Tooltip contentStyle={{ direction: "rtl", fontFamily: "Cairo" }} />
                                <Legend wrapperStyle={{ fontFamily: "Cairo" }} />
                                <Bar dataKey="عدد الطلبات" fill="#0A3622" />
                                <Bar dataKey="الإجمالي" fill="#D4AF37" />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>
            </div>

            {/* Tables */}
            <div className="rounded-xl border border-border bg-white p-6">
                <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>تفاصيل طرق الدفع</h3>
                <div className="overflow-x-auto">
                    <table className="w-full text-right" data-testid="payment-breakdown-table">
                        <thead className="text-sm text-muted-foreground border-b border-border">
                            <tr>
                                <th className="py-3 font-semibold">طريقة الدفع</th>
                                <th className="py-3 font-semibold">عدد الطلبات</th>
                                <th className="py-3 font-semibold">إجمالي المبيعات</th>
                                <th className="py-3 font-semibold">نسبة العمولة</th>
                                <th className="py-3 font-semibold">قيمة العمولة</th>
                                <th className="py-3 font-semibold">الصافي</th>
                            </tr>
                        </thead>
                        <tbody>
                            {r.payment_breakdown.map((p, i) => (
                                <tr key={i} className="border-b border-border last:border-0">
                                    <td className="py-3 font-semibold">
                                        {p.name}
                                        {!p.matched && (
                                            <span title="لم يتم العثور على عمولة لهذه الطريقة في الإعدادات" className="ms-2 inline-flex items-center gap-1 text-amber-600 text-xs">
                                                <Warning size={14} /> غير مُعد
                                            </span>
                                        )}
                                    </td>
                                    <td className="py-3 num">{formatInt(p.orders_count)}</td>
                                    <td className="py-3 num">{formatMoney(p.total_sales)}</td>
                                    <td className="py-3 num">{formatPercent(p.commission_percent)}</td>
                                    <td className="py-3 num text-red-700">{formatMoney(p.fee_amount)}</td>
                                    <td className="py-3 num font-semibold">{formatMoney(p.net_amount)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            <div className="rounded-xl border border-border bg-white p-6">
                <h3 className="text-xl font-bold mb-4" style={{ fontFamily: "Tajawal" }}>تفاصيل شركات الشحن</h3>
                <div className="overflow-x-auto">
                    <table className="w-full text-right" data-testid="shipping-breakdown-table">
                        <thead className="text-sm text-muted-foreground border-b border-border">
                            <tr>
                                <th className="py-3 font-semibold">شركة الشحن</th>
                                <th className="py-3 font-semibold">عدد الطلبات</th>
                                <th className="py-3 font-semibold">تكلفة الشحنة</th>
                                <th className="py-3 font-semibold">الإجمالي</th>
                            </tr>
                        </thead>
                        <tbody>
                            {r.shipping_breakdown.map((sh, i) => (
                                <tr key={i} className="border-b border-border last:border-0">
                                    <td className="py-3 font-semibold">
                                        {sh.name}
                                        {!sh.matched && (
                                            <span className="ms-2 inline-flex items-center gap-1 text-amber-600 text-xs">
                                                <Warning size={14} /> غير مُعد
                                            </span>
                                        )}
                                    </td>
                                    <td className="py-3 num">{formatInt(sh.orders_count)}</td>
                                    <td className="py-3 num">{formatMoney(sh.cost_per_order)}</td>
                                    <td className="py-3 num font-semibold">{formatMoney(sh.total_cost)}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
    );
}
