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
    Percent,
    CalendarBlank,
    Wallet,
    Bank,
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
    const [fromDate, setFromDate] = useState("");
    const [toDate, setToDate] = useState("");

    const fetchDashboard = async (params = {}) => {
        setLoading(true);
        try {
            const { data } = await api.get("/dashboard", { params });
            setData(data);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchDashboard();
    }, []);

    const applyRange = () => {
        const p = {};
        if (fromDate) p.from_date = fromDate;
        if (toDate) p.to_date = toDate;
        fetchDashboard(p);
    };
    const resetRange = () => {
        setFromDate("");
        setToDate("");
        fetchDashboard();
    };
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
        fetchDashboard({ from_date: f, to_date: t });
    };

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
                    <p className="text-muted-foreground mt-2 text-base">
                        {(fromDate || toDate)
                            ? `عرض البيانات من ${fromDate || "البداية"} إلى ${toDate || "الآن"}`
                            : "نظرة شاملة على أدائك المالي عبر جميع التحاليل المحفوظة."}
                    </p>
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

            {/* Date range filter */}
            <div className="rounded-xl border border-border bg-white p-4 flex flex-col md:flex-row md:items-center gap-3 flex-wrap" data-testid="date-filter-bar">
                <div className="flex items-center gap-2 text-sm font-semibold text-brand">
                    <CalendarBlank size={20} weight="duotone" /> الفترة الزمنية:
                </div>
                <div className="flex items-center gap-2">
                    <label className="text-xs text-muted-foreground">من</label>
                    <input
                        type="date"
                        value={fromDate}
                        onChange={(e) => setFromDate(e.target.value)}
                        className="px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                        data-testid="date-from-input"
                        dir="ltr"
                    />
                    <label className="text-xs text-muted-foreground ms-2">إلى</label>
                    <input
                        type="date"
                        value={toDate}
                        onChange={(e) => setToDate(e.target.value)}
                        className="px-3 py-2 text-sm border border-border rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-brand"
                        data-testid="date-to-input"
                        dir="ltr"
                    />
                    <button
                        onClick={applyRange}
                        className="px-4 py-2 bg-brand text-white text-sm font-bold rounded-lg bg-brand-hover transition-colors"
                        data-testid="apply-date-filter-btn"
                    >تطبيق</button>
                    {(fromDate || toDate) && (
                        <button
                            onClick={resetRange}
                            className="px-3 py-2 border border-border text-sm font-semibold rounded-lg hover:bg-accent transition-colors"
                            data-testid="reset-date-filter-btn"
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
                            data-testid={`preset-${p.k}`}
                        >{p.label}</button>
                    ))}
                </div>
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
                        <Kpi icon={Receipt} label="رسوم بوابات الدفع (ر.س)" value={formatMoney(totals.other_payment_fees)} hint="عدا تمارا وتابي وإمكان" testid="kpi-payment-fees" />
                        <Kpi icon={Wallet} label="صافي المدفوعات الإلكترونية (ر.س)" value={formatMoney(totals.electronic_net)} hint="المبيعات − العمولات" testid="kpi-electronic-net" />
                        <Kpi icon={Receipt} label="رسوم تمارا (ر.س)" value={formatMoney(totals.tamara_fees)} hint="BNPL" testid="kpi-tamara-fees" />
                        <Kpi icon={Receipt} label="رسوم تابي (ر.س)" value={formatMoney(totals.tabby_fees)} hint="BNPL" testid="kpi-tabby-fees" />
                        <Kpi icon={Receipt} label="رسوم إمكان (ر.س)" value={formatMoney(totals.emkan_fees)} hint="BNPL" testid="kpi-emkan-fees" />
                        <Kpi icon={Wallet} label="صافي تمارا وتابي وإمكان (ر.س)" value={formatMoney(totals.bnpl_net)} hint="بعد خصم العمولات" testid="kpi-bnpl-net" />
                        <Kpi icon={Truck} label="تكاليف الشحن (ر.س)" value={formatMoney(totals.total_shipping_cost)} testid="kpi-shipping-cost" />
                        <Kpi icon={Truck} label="مستحقات الشحن الآجل (ر.س)" value={formatMoney(totals.deferred_shipping_cost)} hint="ذمم للشركات الآجلة" testid="kpi-deferred-shipping" />
                        <Kpi icon={Bank} label="المتوقع من سلة (ر.س)" value={formatMoney(totals.expected_salla_transfer)} hint="حوالة سلة المتوقعة" accent testid="kpi-expected-salla" />
                        <Kpi icon={Percent} label="إجمالي الضريبة المخصومة (ر.س)" value={formatMoney(totals.total_vat)} hint="ضريبة الدفع + الشحن" testid="kpi-total-vat" />
                        <Kpi icon={Megaphone} label="تكاليف الإعلانات (ر.س)" value={formatMoney(totals.total_ads_cost)} testid="kpi-ads" />
                        <Kpi icon={Package} label="تكاليف المنتجات (ر.س)" value={formatMoney(totals.total_product_cost)} hint="من ملفات Excel" testid="kpi-products" />
                        <Kpi icon={Receipt} label="مصاريف يومية (ر.س)" value={formatMoney(totals.daily_expenses_total)} hint="من سجل التكاليف" testid="kpi-daily-expenses" />
                        <Kpi icon={TrendUp} label="صافي الربح النهائي (ر.س)" value={formatMoney(totals.net_profit)} hint="بعد التكاليف اليومية" accent testid="kpi-net-profit" />
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
                                <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
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
                        <div className="flex items-center justify-between mb-2">
                            <h2 className="text-2xl font-bold text-foreground" style={{ fontFamily: "Tajawal" }}>آخر التحاليل</h2>
                            <Link to="/history" className="text-brand text-sm font-semibold hover:underline inline-flex items-center gap-1" data-testid="see-all-history">
                                عرض الكل <CaretLeft size={16} />
                            </Link>
                        </div>
                        <p className="text-xs text-muted-foreground mb-5">
                            * صافي ربح كل تحليل محسوب قبل خصم التكاليف اليومية (تكاليف الإعلانات والمنتجات اليومية تُخصم على مستوى الفترة في بطاقة "صافي الربح النهائي").
                        </p>
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
                                            <th className="pb-3 font-semibold">صافي ربح التحليل *</th>
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
