import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    AlertTriangle, ArrowRight, BarChart3, BriefcaseBusiness, ChevronLeft,
    CircleDollarSign, CreditCard, Instagram, Megaphone, PackageOpen,
    RefreshCw, ShoppingBag, ShoppingCart, TrendingUp, Truck, Trophy, UsersRound,
} from "lucide-react";
import { User } from "@phosphor-icons/react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import api from "../lib/api";
import AdvancedFilters, { defaultFilters, filtersToQueryString } from "../components/AdvancedFilters";
import { useOrders } from "../hooks/useOrders";
import { buildMissingMezanCostHref } from "../lib/mezanV2CostLinks";

const PLATFORM_META = [
    { key: "snapchat", label: "سناب شات", color: "#f59e0b" },
    { key: "tiktok", label: "تيك توك", color: "#111827" },
    { key: "meta", label: "Meta", color: "#2563eb" },
    { key: "google", label: "Google Ads", color: "#059669" },
];

const money = (value) => Number(value || 0).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const integer = (value) => Number(value || 0).toLocaleString("en-US", { maximumFractionDigits: 0 });

function Panel({ children, className = "", testid }) {
    return <section data-testid={testid} className={`overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm ${className}`}>{children}</section>;
}

function relativeTime(value) {
    const date = new Date(value || 0);
    const seconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
    if (!Number.isFinite(seconds)) return "—";
    if (seconds < 60) return `منذ ${Math.max(1, seconds)} ثانية`;
    if (seconds < 3600) return `منذ ${Math.floor(seconds / 60)} دقيقة`;
    if (seconds < 86400) return `منذ ${Math.floor(seconds / 3600)} ساعة`;
    if (seconds < 2592000) return `منذ ${Math.floor(seconds / 86400)} يوم`;
    if (seconds < 31536000) return `منذ ${Math.floor(seconds / 2592000)} شهر`;
    return `منذ ${Math.floor(seconds / 31536000)} سنة`;
}

export function AbandonedCartsCard({ carts, summary = {} }) {
    const [visibleCount, setVisibleCount] = useState(5);
    const cartRows = carts || [];
    const visibleCarts = cartRows.slice(0, visibleCount);
    const hasMore = visibleCount < cartRows.length;
    useEffect(() => { setVisibleCount(5); }, [carts]);
    return (
        <Panel className="border-rose-200" testid="advanced-abandoned-carts">
            <div className="flex h-14 items-center justify-between border-b border-rose-800 bg-rose-700 px-4 text-white">
                <h2 className="flex items-center gap-2 font-extrabold"><ShoppingCart className="h-5 w-5" />السلات المتروكة</h2>
                <div className="flex items-center gap-1.5 text-[10px] font-black">
                    <span className="rounded-full bg-white/15 px-2 py-1">متروكة {integer(summary.abandoned_count)}</span>
                    <span className="rounded-full bg-white/15 px-2 py-1">مكتملة {integer(summary.recovered_count)}</span>
                </div>
            </div>
            <div className="h-[410px] overflow-y-auto overscroll-contain" data-testid="advanced-abandoned-carts-scroll">
            {visibleCarts.length ? visibleCarts.map((cart) => {
                const item = Array.isArray(cart.items) ? cart.items[0] : null;
                const productCount = (cart.items || []).reduce((sum, product) => sum + Math.max(1, Number(product?.quantity || 1)), 0);
                return <div key={cart.cart_id} className="flex min-h-[82px] items-center gap-3 border-b border-rose-100 px-4 py-3 last:border-0 odd:bg-rose-50/30">
                    {item?.image_url
                        ? <img src={item.image_url} alt="" className="h-11 w-11 shrink-0 rounded-xl object-cover" loading="lazy" />
                        : <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-rose-100 text-xl">🛒</div>}
                    <div className="min-w-0 flex-1"><p className="truncate text-xs font-extrabold text-slate-800">{cart.customer_name || "عميل سلة"}</p><p className="mt-1 text-[10px] font-bold text-rose-600">{integer(productCount)} {productCount === 1 ? "منتج" : "منتجات"}</p><p className="mt-0.5 truncate text-[9px] text-slate-400">سلة #{cart.cart_id}</p></div>
                    <div className="text-left"><p className="num text-xs font-black text-rose-500">{money(cart.total)} {cart.currency || "SAR"}</p><p className="mt-1 text-[10px] text-slate-400">{relativeTime(cart.cart_updated_at || cart.updated_at)}</p></div>
                </div>;
            }) : <div className="p-8 text-center text-xs text-slate-400">لا توجد سلات متروكة نشطة.</div>}
            </div>
            {cartRows.length > 5 && <button type="button" onClick={() => hasMore ? setVisibleCount((value) => Math.min(value + 5, cartRows.length)) : setVisibleCount(5)} className="w-full border-t border-rose-200 bg-rose-50/60 px-4 py-3 text-xs font-extrabold text-rose-700 hover:bg-rose-100">{hasMore ? "المزيد" : "عرض أقل"}</button>}
        </Panel>
    );
}

export function TopProductsCard({ rows, summary = {} }) {
    const [visibleCount, setVisibleCount] = useState(5);
    const products = [...(rows || [])].sort((a, b) => Number(b.units_sold || 0) - Number(a.units_sold || 0));
    const visibleProducts = products.slice(0, visibleCount);
    const hasMore = visibleCount < products.length;
    useEffect(() => { setVisibleCount(5); }, [rows]);
    return (
        <Panel className="border-indigo-200" testid="advanced-top-products">
            <div className="flex h-14 items-center justify-between border-b border-indigo-800 bg-indigo-700 px-4 text-white"><h2 className="flex items-center gap-2 font-extrabold"><Trophy className="h-5 w-5" />المنتجات الأكثر مبيعًا</h2><div className="text-left text-[9px] font-bold leading-4"><p>{integer(summary.product_count)} منتجًا خلال الفترة</p><p className="text-indigo-100">بتكلفة سلة {integer(summary.salla_fallback_products_count)} · بدون تكلفة {integer(summary.missing_all_cost_products_count)}</p></div></div>
            <div className="grid grid-cols-[minmax(0,1fr)_58px_94px] gap-2 border-b px-3 py-2 text-[9px] font-bold text-slate-400"><span>المنتج</span><span>الوحدات</span><span>المبيعات</span></div>
            <div className="h-[330px] overflow-y-auto overscroll-contain" data-testid="advanced-top-products-scroll">
            {visibleProducts.length ? visibleProducts.map((item) => <div key={item.identity} className="grid min-h-[66px] grid-cols-[minmax(0,1fr)_58px_94px] items-center gap-2 border-b px-3 py-2 last:border-0">
                <div className="flex min-w-0 items-center gap-2">{item.image_url ? <img src={item.image_url} alt="" className="h-10 w-10 rounded-lg object-cover" /> : <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-amber-50">📦</div>}<p className="line-clamp-2 text-[10px] font-bold">{item.name}</p></div>
                <span className="num text-xs font-bold">{integer(item.units_sold)}</span><span className="num whitespace-nowrap text-[10px] font-black text-blue-600">{money(item.total_sales)} ر.س</span>
            </div>) : <div className="p-8 text-center text-xs text-slate-400">لا توجد منتجات مباعة في الفترة.</div>}
            </div>
            {products.length > 5 && <button type="button" onClick={() => hasMore ? setVisibleCount((value) => Math.min(value + 5, products.length)) : setVisibleCount(5)} className="w-full border-t border-indigo-200 bg-indigo-50/60 px-4 py-3 text-xs font-extrabold text-indigo-700 hover:bg-indigo-100">{hasMore ? "المزيد" : "عرض أقل"}</button>}
        </Panel>
    );
}

function Metric({ label, value, Icon, tone }) {
    return <div className="flex min-w-0 items-center justify-between gap-2 rounded-xl border bg-white px-3 py-3 shadow-sm"><div className="min-w-0"><p className="line-clamp-2 text-[10px] font-bold text-slate-500">{label}</p><p className="num mt-1 whitespace-nowrap text-base font-black">{value}</p></div><span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${tone}`}><Icon className="h-4 w-4" /></span></div>;
}

function PlatformPeriodSummary({ ads }) {
    const providers = ads?.executive_breakdown?.providers || {};
    const meta = {
        snapchat: { label: "سناب", mark: "👻", tone: "bg-yellow-300 text-slate-950" },
        tiktok: { label: "تيك توك", mark: "♪", tone: "bg-slate-950 text-white" },
        meta: { label: "Meta", mark: "∞", tone: "bg-blue-500 text-white" },
        google: { label: "Google", mark: "G", tone: "bg-white text-blue-600 ring-1 ring-blue-200" },
    };
    return <div className="col-span-2 grid min-h-[78px] grid-cols-2 overflow-hidden rounded-xl border bg-white shadow-sm min-[1180px]:col-span-1 min-[1180px]:grid-cols-4" data-testid="advanced-platform-period-summary">
        {PLATFORM_META.map(({ key }) => {
            const row = providers[key] || {};
            const orderCount = row.platform_reported_orders == null ? null : Number(row.platform_reported_orders || 0);
            const average = row.platform_cost_per_order_sar == null ? null : Number(row.platform_cost_per_order_sar || 0);
            const provider = meta[key];
            return <div key={key} className="flex min-w-0 items-center justify-center gap-1.5 border-l px-1.5 py-2 last:border-l-0">
                <span className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-[10px] font-black ${provider.tone}`}>{provider.mark}</span>
                <div className="min-w-0 text-[8px] font-bold leading-4 text-slate-500"><p className="truncate text-slate-700">{provider.label}: <b className="num text-[10px] text-slate-950">{orderCount == null ? "—" : integer(orderCount)}</b></p><p className="whitespace-nowrap">متوسط: <b className="num text-[9px] text-slate-950">{average == null ? "—" : `${money(average)} ر.س`}</b></p></div>
            </div>;
        })}
    </div>;
}

export function SummaryStrip({ data, filters }) {
    const totals = data?.totals || {};
    const monthTotals = data?.month_kpis || {};
    const missing = Number(data?.product_cost_v2?.missing_products_count || totals.missing_product_cost_count || 0);
    return <div dir="ltr" className="grid gap-3 min-[1180px]:grid-cols-[minmax(0,1.75fr)_minmax(260px,.7fr)]" data-testid="advanced-date-summary">
        <div dir="rtl" className="grid grid-cols-2 gap-2 min-[1180px]:grid-cols-[minmax(130px,.55fr)_minmax(130px,.55fr)_minmax(0,2.2fr)]">
            <Metric label="طلبات الشهر" value={integer(monthTotals.total_orders)} Icon={ShoppingCart} tone="bg-teal-50 text-teal-700" />
            <Metric label="مبيعات الشهر" value={`${money(monthTotals.total_sales)} ر.س`} Icon={CircleDollarSign} tone="bg-cyan-50 text-cyan-700" />
            <PlatformPeriodSummary ads={data?.ads_v2} />
        </div>
        <Link to={buildMissingMezanCostHref(data?.product_cost_v2, filters)} dir="rtl" className="flex min-h-[78px] items-center justify-center gap-3 rounded-xl border border-amber-300 bg-amber-50 px-4 text-center text-amber-900"><AlertTriangle className="h-5 w-5 text-amber-500" /><p className="text-xs font-extrabold">{integer(missing)} منتجًا مبيعًا بدون تكلفة ميزان<span className="block text-amber-700">أضف التكلفة لاعتماد الأرباح</span></p></Link>
    </div>;
}

function AdsCard({ ads }) {
    const [monthly, setMonthly] = useState(false);
    const rows = useMemo(() => {
        const daily = ads?.history || [];
        if (!monthly) return daily.map((row) => ({ ...row, label: row.date?.slice(5) }));
        const grouped = {};
        daily.forEach((row) => { const key = String(row.date || "").slice(0, 7); grouped[key] ||= { label: key, snapchat: 0, tiktok: 0, meta: 0, google: 0 }; PLATFORM_META.forEach(({ key: p }) => { grouped[key][p] += Number(row[p] || 0); }); });
        return Object.values(grouped);
    }, [ads?.history, monthly]);
    const plotRows = rows.length === 1
        ? [{ ...rows[0], label: "بداية" }, { ...rows[0], label: rows[0].label || "الآن" }]
        : rows;
    const breakdown = ads?.breakdown || {};
    return <Panel className="border-amber-200" testid="advanced-ads-chart">
        <div className="flex h-14 items-center justify-between border-b border-amber-700 bg-amber-600 px-4 text-white"><h2 className="flex items-center gap-2 font-extrabold"><CircleDollarSign className="h-5 w-5" />مصروفات منصات الإعلانات</h2><div className="rounded-lg border border-white/30 bg-white/15 p-1 text-[10px] font-bold"><button onClick={() => setMonthly(false)} className={`rounded-md px-2 py-1 ${!monthly ? "bg-white text-amber-800" : ""}`}>يومي</button><button onClick={() => setMonthly(true)} className={`rounded-md px-2 py-1 ${monthly ? "bg-white text-amber-800" : ""}`}>شهري</button></div></div>
        <div className="h-[190px] px-2 pt-3" dir="ltr"><ResponsiveContainer><LineChart data={plotRows}><CartesianGrid vertical={false} strokeDasharray="4 4" /><XAxis dataKey="label" tick={{ fontSize: 9 }} /><YAxis tick={{ fontSize: 9 }} width={36} /><Tooltip formatter={(value) => `${money(value)} ر.س`} />{PLATFORM_META.map((p) => <Line key={p.key} type="monotone" dataKey={p.key} name={p.label} stroke={p.color} strokeWidth={2.5} dot={false} activeDot={{ r: 3 }} connectNulls />)}</LineChart></ResponsiveContainer></div>
        <div className="grid grid-cols-4 gap-1 p-2">{PLATFORM_META.map((p) => <div key={p.key} className="rounded-lg border p-2 text-center"><p className="text-[9px] font-bold" style={{ color: p.color }}>{p.label}</p><p className="num mt-1 text-[10px] font-black">{money(breakdown[p.key === "google" ? "google_transitional" : p.key])}</p></div>)}</div>
        <div className="flex h-11 items-center justify-between border-t bg-amber-50 px-4 font-extrabold"><span>إجمالي المصروفات</span><span className="num">{money(ads?.total)} ر.س</span></div>
    </Panel>;
}

function ProfitCard({ data }) {
    const t = data?.totals || {};
    const fees = t.total_payment_fees ?? (Number(t.other_payment_fees || 0) + Number(t.tamara_fees || 0) + Number(t.tabby_fees || 0) + Number(t.emkan_fees || 0) + Number(t.bank_fees || 0) + Number(t.ad_bank_commission_fees || 0));
    const rows = [
        ["المبيعات", t.total_sales, CircleDollarSign, "text-emerald-700"], ["تكاليف المنتجات", t.total_product_cost, PackageOpen, "text-amber-700"], ["إجمالي تكاليف الإعلانات", t.total_ads_cost, Megaphone, "text-rose-600"], ["إجمالي تكاليف الشحن (مقدم + آجل)", t.total_shipping_cost, Truck, "text-sky-700"], ["إجمالي رسوم جميع طرق الدفع", fees, CreditCard, "text-violet-700"], ["المصروفات التشغيلية (رواتب وإيجارات وغيرها)", t.operating_expenses_total, BriefcaseBusiness, "text-orange-700"],
    ];
    const sales = Number(t.total_sales || 0);
    const orderCount = Number(t.total_orders || 0);
    const averageBasket = orderCount > 0 ? sales / orderCount : 0;
    return <Panel className="border-emerald-200" testid="advanced-profit-summary"><div className="flex h-14 items-center justify-between border-b border-emerald-800 bg-emerald-700 px-4 text-white"><h2 className="flex items-center gap-2 font-extrabold"><TrendingUp className="h-5 w-5" />الملخص التنفيذي للأرباح</h2><span className="text-[9px] font-bold text-emerald-100">الفترة المحددة</span></div><div className="grid grid-cols-2 gap-2 border-b border-emerald-100 bg-emerald-50/40 p-3 sm:grid-cols-4"><Metric label="تكلفة الطلب" value={t.avg_cost_per_order == null ? "—" : `${money(t.avg_cost_per_order)} ر.س`} Icon={ShoppingBag} tone="bg-blue-50 text-blue-700" /><Metric label="عدد الطلبات" value={integer(orderCount)} Icon={ShoppingCart} tone="bg-emerald-50 text-emerald-700" /><Metric label="العائد" value={t.overall_roas == null ? "—" : `${Number(t.overall_roas).toFixed(2)}×`} Icon={TrendingUp} tone="bg-violet-50 text-violet-700" /><Metric label="متوسط قيمة سلة المشتريات" value={`${money(averageBasket)} ر.س`} Icon={ShoppingBag} tone="bg-rose-50 text-rose-600" /></div><div className="px-4 py-2">{rows.map(([label, value, Icon, color], index) => <div dir="ltr" key={label} className="grid min-h-[56px] grid-cols-[minmax(150px,.75fr)_minmax(0,1.25fr)_38px] items-center gap-3 border-b last:border-0"><div className={`num text-left text-base font-black ${color}`}>{money(value)} ر.س{index > 0 && sales > 0 && <span className="ml-2 text-[9px] opacity-70">{(Number(value || 0) / sales * 100).toFixed(2)}%</span>}</div><p dir="rtl" className="text-right text-xs font-extrabold text-slate-700">{label}</p><span className={`flex h-9 w-9 items-center justify-center rounded-xl bg-slate-50 ${color}`}><Icon className="h-4 w-4" /></span></div>)}</div><div dir="ltr" className="m-4 flex min-h-[64px] items-center justify-between rounded-xl bg-emerald-600 px-5 text-white"><p className="num text-xl font-black">{money(t.net_profit)} ر.س</p><div dir="rtl"><p className="font-black">صافي الأرباح</p><p className="text-[9px] text-emerald-100">بعد جميع التكاليف والمصروفات</p></div></div></Panel>;
}

function orderSource(order) {
    return [
        order?.source?.channel,
        order?.source?.platform,
        order?.source?.source,
        typeof order?.source === "string" ? order.source : "",
        order?.utm?.source,
        order?.utm_source,
        order?.marketing?.source,
        order?.attribution?.source,
        order?.source_channel,
    ].map((value) => String(value || "").trim().toLowerCase()).find(Boolean) || "";
}

function SourceBadge({ order }) {
    const source = orderSource(order);
    let badge = null;
    if (source.includes("snap")) badge = { label: "سناب", mark: "👻", className: "border-yellow-300 bg-yellow-300 text-slate-950" };
    else if (source.includes("tiktok") || source.includes("tik tok")) badge = { label: "تيك توك", mark: "♪", className: "border-slate-900 bg-slate-950 text-white" };
    else if (source.includes("meta") || source.includes("facebook") || source.includes("instagram") || source === "fb" || source === "ig") badge = { label: "ميتا", mark: source.includes("instagram") ? <Instagram className="h-3 w-3" /> : "∞", className: "border-blue-500 bg-blue-500 text-white" };
    else if (source.includes("google") || source.includes("adwords") || source.includes("gads")) badge = { label: "جوجل", mark: "G", className: "border-blue-200 bg-white text-blue-600" };
    if (!badge) return null;
    return <span title={`مصدر الطلب: ${badge.label}`} aria-label={`مصدر الطلب: ${badge.label}`} className={`inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[9px] font-black leading-none shadow-sm ${badge.className}`}>{badge.mark}</span>;
}

const ORDER_STATUS_AR = {
    under_review: "بانتظار المراجعة",
    reviewed: "تمت المراجعة",
    processing: "قيد التنفيذ",
    completed: "تم التنفيذ",
    delivering: "جاري التوصيل",
    delivered: "تم التوصيل",
    shipped: "تم الشحن",
    canceled: "ملغي",
    cancelled: "ملغي",
    refunded: "مسترجع",
};

function orderStatusLabel(order) {
    const raw = order?.status_native || order?.status?.name || order?.status || "";
    const normalized = String(raw).trim().toLowerCase().replaceAll(" ", "_");
    return ORDER_STATUS_AR[normalized] || raw || "بانتظار المراجعة";
}

function orderCity(order) {
    return order?.shipping?.address?.city || order?.customer?.shipping_address?.city || "غير محدد";
}

function CustomerAvatar({ customer }) {
    const avatarUrl = String(customer?.avatar_url || "").trim();
    const gender = String(customer?.gender || "").toLowerCase();
    const fallback = gender === "female" ? "👩" : gender === "male" ? "👨" : null;
    return <div className="relative flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full bg-slate-100 text-slate-600 sm:h-12 sm:w-12">
        {fallback ? <span className="text-xl leading-none sm:text-2xl">{fallback}</span> : <User size={22} weight="fill" />}
        {avatarUrl && <img src={avatarUrl} alt="" className="absolute inset-0 h-full w-full object-cover" loading="lazy" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.style.display = "none"; }} />}
    </div>;
}

export function LatestOrders({ orders, totals = {} }) {
    const orderCount = Number(totals.total_orders || 0);
    const average = orderCount > 0 ? Number(totals.total_sales || 0) / orderCount : 0;
    return <Panel className="border-sky-200" testid="advanced-latest-orders">
        <div className="flex h-14 items-center justify-between border-b border-sky-700 bg-sky-600 px-4 text-white">
            <h2 className="flex items-center gap-2 font-extrabold"><ShoppingBag className="h-5 w-5" />أحدث الطلبات</h2>
            <div className="flex items-center gap-3 text-[10px] font-bold"><span className="inline-flex items-center gap-1 rounded-full bg-white/15 px-2 py-1"><ShoppingBag className="h-3.5 w-3.5" />{integer(orderCount)} طلب</span><span className="whitespace-nowrap">متوسط: <b className="num">{money(average)} ر.س</b></span></div>
        </div>
        <div className="divide-y divide-slate-100">{orders.slice(0, 8).map((order) => {
            const id = String(order.order_number);
            const status = orderStatusLabel(order);
            const itemCount = Number(order.items?.length || order.items_count || 0);
            const payment = order.payment?.method_native || order.payment?.method || order.payment_method || "غير محدد";
            return <Link
                key={id}
                to={`/orders-v2/${encodeURIComponent(id)}?returnTo=${encodeURIComponent("/dashboard-advanced")}`}
                dir="rtl"
                className="flex min-h-[88px] items-center gap-3 px-4 py-4 text-right hover:bg-slate-50 sm:px-5"
            >
                <CustomerAvatar customer={order.customer} />
                <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <div className="min-w-0 truncate text-[15px] font-semibold">{order.customer?.name || "عميل بدون اسم"}</div>
                        {order.is_new && <span className="shrink-0 rounded-full border border-rose-300 px-2 py-0.5 text-[11px] font-bold text-rose-600">جديد</span>}
                    </div>
                    <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-slate-400 sm:text-xs">
                        <span className="whitespace-nowrap">#{id}</span><span>•</span>
                        <span className="whitespace-nowrap">{orderCity(order)}</span><span>•</span>
                        <span className="inline-flex items-center gap-1 whitespace-nowrap"><span className="h-2 w-2 shrink-0 rounded-full bg-slate-800" />{status}</span><span>•</span>
                        <span className="whitespace-nowrap">{itemCount} قطعة</span><span>•</span>
                        <span className="whitespace-nowrap">{payment}</span>
                    </div>
                </div>
                <div className="shrink-0 text-left">
                    <div className="flex items-center justify-end gap-1.5"><SourceBadge order={order} /><span className="num whitespace-nowrap font-semibold text-teal-800">{money(order.totals?.total || order.total_amount)} ر.س</span></div>
                    <div className="mt-1 whitespace-nowrap text-[11px] text-slate-400 sm:text-xs">{relativeTime(order.created_at || order.order_date)}</div>
                </div>
                <ChevronLeft className="h-4 w-4 shrink-0 text-slate-300" />
            </Link>;
        })}</div>
    </Panel>;
}

function GaLive({ data }) { const pages = data?.top_pages || []; const minutes = data?.active_users?.per_minute || []; const max = Math.max(1, ...pages.map((p) => Number(p.views || 0))); const minuteMax = Math.max(1, ...minutes.map((m) => Number(m.active_users || 0))); return <div className="space-y-4"><Panel className="border-blue-200"><div className="flex h-14 items-center gap-2 border-b border-blue-800 bg-blue-700 px-4 text-white"><BarChart3 className="h-5 w-5" /><h2 className="text-sm font-black">Google Analytics 4 — مباشر</h2></div><div className="p-4"><h3 className="mb-3 text-sm font-extrabold">الصفحات الأكثر مشاهدة</h3>{pages.slice(0, 6).map((p, i) => <div key={`${p.title}-${i}`} className="mb-3"><div className="flex justify-between gap-2 text-[10px]"><span className="truncate">{p.title}</span><b>{p.views}</b></div><div className="mt-1 h-1.5 rounded bg-slate-100"><div className="h-full rounded bg-blue-500" style={{ width: `${Number(p.views || 0) / max * 100}%` }} /></div></div>)}</div></Panel><Panel className="border-violet-200"><div className="flex h-14 items-center gap-2 border-b border-violet-800 bg-violet-700 px-4 text-white"><UsersRound className="h-5 w-5" /><h2 className="font-black">المستخدمون النشطون الآن</h2></div><div className="grid grid-cols-2 gap-2 p-4"><Metric label="آخر 30 دقيقة" value={integer(data?.active_users?.last_30_minutes)} Icon={UsersRound} tone="bg-blue-50 text-blue-600" /><Metric label="آخر 5 دقائق" value={integer(data?.active_users?.last_5_minutes)} Icon={UsersRound} tone="bg-violet-50 text-violet-600" /></div><div className="flex h-36 items-end gap-1 overflow-hidden px-4 pb-4" dir="ltr" data-testid="advanced-ga-active-chart">{minutes.map((m, i) => <div key={i} className="max-h-full flex-1 rounded-t bg-violet-600" style={{ height: `${Math.min(100, Math.max(4, Number(m.active_users || 0) / minuteMax * 100))}%` }} />)}</div></Panel></div>; }

export default function AdvancedDashboard() {
    const [filters, setFilters] = useState(() => defaultFilters("today"));
    const [data, setData] = useState(null); const [carts, setCarts] = useState([]); const [cartSummary, setCartSummary] = useState({ abandoned_count: 0, recovered_count: 0 }); const [ga, setGa] = useState(null); const [loading, setLoading] = useState(true);
    const { orders } = useOrders();
    const loadPeriod = useCallback(async (next) => {
        setLoading(true);
        try {
            const response = await api.get(`/dashboard-v2?${filtersToQueryString(next)}`);
            setData(response.data);
        } finally { setLoading(false); }
    }, []);
    useEffect(() => { loadPeriod(filters); }, [filters, loadPeriod]);
    useEffect(() => { let active = true; const loadLive = async () => { const cartQuery = new URLSearchParams({ from_date: filters.from || "", to_date: filters.to || filters.from || "" }).toString(); const [cartResult, gaResult] = await Promise.allSettled([api.get(`/dashboard-v2/abandoned-carts/recent?${cartQuery}`), api.get("/integrations-v2/google_analytics_4/realtime-dashboard")]); if (!active) return; if (cartResult.status === "fulfilled") { setCarts(cartResult.value.data?.items || []); setCartSummary({ abandoned_count: Number(cartResult.value.data?.abandoned_count || 0), recovered_count: Number(cartResult.value.data?.recovered_count || 0) }); } if (gaResult.status === "fulfilled") setGa(gaResult.value.data); }; loadLive(); const timer = window.setInterval(loadLive, 60000); return () => { active = false; window.clearInterval(timer); }; }, [filters.from, filters.to]);
    return <div dir="rtl" className="space-y-4" data-testid="advanced-dashboard-page"><header className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs text-slate-400">لوحة مستقلة — لوحة التحكم الحالية محفوظة</p><h1 className="text-2xl font-black sm:text-3xl">لوحة التحكم المتقدمة</h1></div><Link to="/dashboard-v2" className="inline-flex items-center gap-2 rounded-xl border bg-white px-4 py-2 text-sm font-bold"><ArrowRight className="h-4 w-4" />العودة للوحة الحالية</Link></header><div className="flex items-stretch gap-2"><div className="min-w-0 flex-1"><AdvancedFilters value={filters} onChange={setFilters} defaultPreset="today" /></div><button onClick={() => loadPeriod(filters)} className="rounded-xl border bg-white px-4 text-blue-700" aria-label="تحديث بيانات الفترة"><RefreshCw className={`h-5 w-5 ${loading ? "animate-spin" : ""}`} /></button></div><SummaryStrip data={data} filters={filters} /><div dir="ltr" className="grid items-start gap-4 min-[1280px]:grid-cols-[clamp(280px,24vw,350px)_minmax(0,1fr)]"><aside dir="rtl" className="space-y-4"><AdsCard ads={data?.ads_v2} /><TopProductsCard rows={data?.product_cost_v2?.product_rows} summary={data?.product_cost_v2} /><AbandonedCartsCard carts={carts} summary={cartSummary} /></aside><main dir="rtl" className="min-w-0"><div dir="ltr" className="grid min-w-0 items-start gap-4 min-[1120px]:grid-cols-[minmax(0,2fr)_minmax(280px,.92fr)]"><div dir="rtl" className="space-y-4"><ProfitCard data={data} /><LatestOrders orders={orders} totals={data?.totals} /></div><div dir="rtl"><GaLive data={ga} /></div></div></main></div></div>;
}
