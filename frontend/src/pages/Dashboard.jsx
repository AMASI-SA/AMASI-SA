import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
    Coins,
    ShoppingBag,
    Receipt,
    Truck,
    Megaphone,
    Package,
    TrendUp,
    CaretLeft,
    UploadSimple,
} from "@phosphor-icons/react";
import {
    LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";
import api from "../lib/api";
import { formatMoney, formatInt } from "../lib/format";
import { useAuth } from "../context/AuthContext";

function Kpi({ icon: Icon, label, value, hint, accent = false, testid }) {
    return (
        <div
            className={[
                "rounded-xl border border-border p-5 bg-white transition-shadow hover:shadow-sm",
                accent ? "border-brand/40 bg-accent" : "",
            ].join(" ")}
            data-testid={testid}
        >
            <div className="flex items-center justify-between mb-3">
                <div className={`w-10 h-10 rounded-lg ${accent ? "bg-brand text-white" : "bg-accent text-brand"} flex items-center justify-center`}>
                    <Icon size={22} weight="duotone" />
                </div>
                {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
            </div>
            <div className="text-sm text-muted-foreground mb-1">{label}</div>
            <div className="num text-2xl font-extrabold text-foreground" style={{ fontFamily: "Tajawal" }}>{value}</div>
        </div>
    );
}

export default function Dashboard() {
    const { user } = useAuth();
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        (async () => {
            try {
                const { data } = await api.get("/dashboard");
                setData(data);
            } finally {
                setLoading(false);
            }
        })();
    }, []);

    const totals = data?.totals || {};
    const monthly = data?.monthly || [];
    const recent = data?.recent_analyses || [];

    return (
        <div className="space-y-8 animate-fade-in-up" data-testid="dashboard-page">
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
                <div>
                    <div className="text-sm text-muted-foreground mb-1">مرحباً، {user?.name || "ضيف"}</div>
                    <h1 className="text-4xl sm:text-5xl font-extrabold tracking-tight text-foreground" style={{ fontFamily: "Tajawal" }}>
                        لوحة التحكم
                    </h1>
                    <p className="text-muted-foreground mt-2 text-base">نظرة شاملة على أدائك المالي عبر جميع التحاليل المحفوظة.</p>
                </div>
                <Link
                    to="/upload"
                    className="inline-flex items-center gap-2 px-5 py-3 bg-brand text-white font-semibold rounded-lg bg-brand-hover transition-colors"
                    data-testid="dashboard-upload-btn"
                >
                    <UploadSimple size={20} weight="bold" />
                    تحليل ملف جديد
                </Link>
            </div>

            {loading ? (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    {[1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
                        <div key={i} className="h-32 rounded-xl bg-white border border-border animate-pulse" />
                    ))}
                </div>
            ) : (
                <>
                    {/* KPI grid */}
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                        <Kpi icon={Coins} label="إجمالي المبيعات (ر.س)" value={formatMoney(totals.total_sales)} accent testid="kpi-total-sales" />
                        <Kpi icon={ShoppingBag} label="إجمالي الطلبات" value={formatInt(totals.total_orders)} testid="kpi-total-orders" />
                        <Kpi icon={Receipt} label="رسوم بوابات الدفع (ر.س)" value={formatMoney(totals.total_payment_fees)} testid="kpi-payment-fees" />
                        <Kpi icon={Truck} label="تكاليف الشحن (ر.س)" value={formatMoney(totals.total_shipping_cost)} testid="kpi-shipping-cost" />
                        <Kpi icon={Megaphone} label="تكاليف الإعلانات (ر.س)" value={formatMoney(totals.total_ads_cost)} testid="kpi-ads" />
                        <Kpi icon={Package} label="تكاليف المنتجات (ر.س)" value={formatMoney(totals.total_product_cost)} testid="kpi-products" />
                        <Kpi icon={TrendUp} label="عدد التحاليل" value={formatInt(totals.analyses_count)} testid="kpi-count" />
                        <Kpi icon={TrendUp} label="صافي الربح (ر.س)" value={formatMoney(totals.net_profit)} accent testid="kpi-net-profit" />
                    </div>

                    {/* Monthly chart */}
                    <div className="rounded-xl border border-border bg-white p-6">
                        <div className="flex items-center justify-between mb-6">
                            <div>
                                <h2 className="text-2xl font-bold text-foreground" style={{ fontFamily: "Tajawal" }}>الأداء الشهري</h2>
                                <p className="text-sm text-muted-foreground mt-1">المبيعات والأرباح الصافية عبر الأشهر</p>
                            </div>
                        </div>
                        {monthly.length === 0 ? (
                            <div className="text-center py-16 text-muted-foreground">
                                لا توجد بيانات بعد. قم برفع ملف Excel لبدء التحليل.
                            </div>
                        ) : (
                            <div className="h-72" data-testid="monthly-chart">
                                <ResponsiveContainer width="100%" height="100%">
                                    <LineChart data={monthly} margin={{ top: 8, right: 20, left: 20, bottom: 8 }}>
                                        <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
                                        <XAxis dataKey="month" tick={{ fontSize: 12, fontFamily: "Cairo" }} reversed />
                                        <YAxis tick={{ fontSize: 12, fontFamily: "Cairo" }} orientation="right" />
                                        <Tooltip contentStyle={{ direction: "rtl", fontFamily: "Cairo" }} />
                                        <Legend wrapperStyle={{ fontFamily: "Cairo" }} />
                                        <Line type="monotone" dataKey="sales" name="المبيعات" stroke="#0A3622" strokeWidth={2.5} dot={{ r: 4 }} />
                                        <Line type="monotone" dataKey="profit" name="صافي الربح" stroke="#D4AF37" strokeWidth={2.5} dot={{ r: 4 }} />
                                    </LineChart>
                                </ResponsiveContainer>
                            </div>
                        )}
                    </div>

                    {/* Recent analyses */}
                    <div className="rounded-xl border border-border bg-white p-6">
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-2xl font-bold text-foreground" style={{ fontFamily: "Tajawal" }}>آخر التحاليل</h2>
                            <Link to="/history" className="text-brand text-sm font-semibold hover:underline inline-flex items-center gap-1" data-testid="see-all-history">
                                عرض الكل <CaretLeft size={16} />
                            </Link>
                        </div>
                        {recent.length === 0 ? (
                            <div className="text-center py-10 text-muted-foreground">لم تقم بإجراء أي تحليل بعد.</div>
                        ) : (
                            <div className="overflow-x-auto">
                                <table className="w-full text-right" data-testid="recent-analyses-table">
                                    <thead>
                                        <tr className="text-sm text-muted-foreground border-b border-border">
                                            <th className="pb-3 font-semibold">الاسم</th>
                                            <th className="pb-3 font-semibold">التاريخ</th>
                                            <th className="pb-3 font-semibold">الطلبات</th>
                                            <th className="pb-3 font-semibold">المبيعات</th>
                                            <th className="pb-3 font-semibold">صافي الربح</th>
                                            <th className="pb-3 font-semibold"></th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {recent.map((a) => (
                                            <tr key={a.id} className="border-b border-border last:border-0">
                                                <td className="py-3 font-medium">{a.name}</td>
                                                <td className="py-3 num text-muted-foreground">{a.date}</td>
                                                <td className="py-3 num">{formatInt(a.total_orders)}</td>
                                                <td className="py-3 num font-semibold">{formatMoney(a.total_sales)}</td>
                                                <td className={`py-3 num font-semibold ${a.net_profit >= 0 ? "text-green-700" : "text-red-700"}`}>
                                                    {formatMoney(a.net_profit)}
                                                </td>
                                                <td className="py-3">
                                                    <Link to={`/analyses/${a.id}`} className="text-brand font-semibold hover:underline" data-testid={`view-analysis-${a.id}`}>
                                                        تفاصيل
                                                    </Link>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}
