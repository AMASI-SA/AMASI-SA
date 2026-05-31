import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
    ChartBar, ChartLine, Megaphone, ArrowsClockwise, ArrowLeft,
} from "@phosphor-icons/react";
import {
    BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
    LineChart, Line, Cell,
} from "recharts";
import { toast } from "sonner";
import api, { formatApiErrorDetail } from "../lib/api";
import { formatMoney, formatInt, todayISO } from "../lib/format";
import DateInput from "../components/DateInput";

const PLATFORM_THEME = {
    snapchat: { color: "#F7D000", label: "Snapchat",  bg: "#FFFCEA", border: "#FFE066" },
    tiktok:   { color: "#000000", label: "TikTok",    bg: "#fff7fb", border: "#FBCFE8" },
    meta:     { color: "#1877F2", label: "Meta",      bg: "#EFF6FF", border: "#BFDBFE" },
};

function monthStartISO() {
    const t = new Date();
    return `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-01`;
}

export default function AdsReport() {
    const [fromDate, setFromDate] = useState(monthStartISO());
    const [toDate, setToDate] = useState(todayISO());
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);

    const fetch = async () => {
        setLoading(true);
        try {
            const { data: d } = await api.get(
                `/reports/ads?from_date=${encodeURIComponent(fromDate)}&to_date=${encodeURIComponent(toDate)}`
            );
            setData(d);
        } catch (err) {
            toast.error(formatApiErrorDetail(err.response?.data?.detail));
        } finally { setLoading(false); }
    };

    useEffect(() => { fetch(); /* eslint-disable-next-line */ }, [fromDate, toDate]);

    const platforms = data?.platforms || [];
    const combined = data?.combined;
    const series = data?.series || [];

    return (
        <div className="space-y-6 animate-fade-in-up" data-testid="ads-report-page">
            {/* Header */}
            <div className="flex flex-col md:flex-row md:items-end md:justify-between gap-4">
                <div>
                    <div className="flex items-center gap-2 text-xs text-muted-foreground mb-2">
                        <Link to="/reports" className="hover:text-brand" data-testid="ads-report-back-link">
                            <ArrowLeft size={14} className="inline" /> العودة للتقارير
                        </Link>
                    </div>
                    <h1 className="text-3xl sm:text-4xl lg:text-5xl font-extrabold tracking-tight" style={{ fontFamily: "Tajawal" }}>
                        تقرير الإعلانات الموحَّد
                    </h1>
                    <p className="text-muted-foreground mt-2 text-sm sm:text-base">
                        مقارنة أداء Snapchat و TikTok و Meta عبر فترة زمنية واحدة — الصرف، ROAS، CPC، CPM، CTR، الطلبات والعائد.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={fetch}
                    className="inline-flex items-center justify-center gap-2 px-4 py-2.5 bg-brand text-white font-bold rounded-lg bg-brand-hover transition-colors disabled:opacity-60 w-full md:w-auto"
                    disabled={loading}
                    data-testid="ads-report-refresh-btn"
                >
                    <ArrowsClockwise size={16} weight="bold" className={loading ? "animate-spin" : ""} />
                    تحديث البيانات
                </button>
            </div>

            {/* Date range picker */}
            <div className="rounded-xl border border-border bg-white p-4 flex flex-wrap items-end gap-3" data-testid="ads-report-filters">
                <div>
                    <label className="block text-xs font-bold mb-1.5">من تاريخ</label>
                    <DateInput value={fromDate} onChange={(e) => setFromDate(e.target.value)} data-testid="ads-report-from-date" />
                </div>
                <div>
                    <label className="block text-xs font-bold mb-1.5">إلى تاريخ</label>
                    <DateInput value={toDate} onChange={(e) => setToDate(e.target.value)} data-testid="ads-report-to-date" />
                </div>
                <div className="text-xs text-muted-foreground">
                    {data?.range && (
                        <span>الفترة المحسوبة: <span className="num font-bold" dir="ltr">{data.range.from_date} → {data.range.to_date}</span></span>
                    )}
                </div>
            </div>

            {loading && <div className="text-center py-10 text-muted-foreground">جاري التحميل…</div>}

            {!loading && data && (
                <>
                    {/* Combined totals */}
                    <CombinedTotals combined={combined} />

                    {/* Per-platform cards */}
                    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4" data-testid="ads-report-platforms">
                        {platforms.map((p) => <PlatformCard key={p.platform} p={p} />)}
                    </div>

                    {/* Daily spend comparison chart */}
                    <DailySpendChart series={series} />

                    {/* Side-by-side comparison table */}
                    <ComparisonTable platforms={platforms} combined={combined} />

                    {/* ROAS comparison chart */}
                    <RoasComparison platforms={platforms} />
                </>
            )}
        </div>
    );
}

// ── Combined totals header ───────────────────────────────────────────────────
function CombinedTotals({ combined }) {
    if (!combined) return null;
    return (
        <div className="rounded-xl border-2 border-brand/30 bg-gradient-to-br from-emerald-50 via-white to-amber-50/40 p-6" data-testid="ads-report-combined">
            <div className="flex items-center gap-3 mb-4">
                <div className="w-11 h-11 rounded-xl bg-brand text-white flex items-center justify-center">
                    <Megaphone size={22} weight="duotone" />
                </div>
                <div>
                    <h2 className="text-2xl font-bold" style={{ fontFamily: "Tajawal" }}>إجمالي جميع المنصات</h2>
                    <p className="text-xs text-muted-foreground">Snapchat + TikTok + Meta</p>
                </div>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
                <Metric label="إجمالي الصرف"  value={combined.spend}     money testid="combined-spend" big />
                <Metric label="إجمالي العائد"  value={combined.revenue}   money testid="combined-revenue" big colorClass="text-emerald-700" />
                <Metric label="إجمالي الطلبات" value={combined.purchases} integer testid="combined-purchases" big />
                <Metric label="ROAS"           value={combined.roas}      suffix="x" testid="combined-roas" big colorClass={combined.roas >= 2 ? "text-emerald-700" : "text-amber-700"} />
                <Metric label="مرات الظهور"    value={combined.impressions} integer testid="combined-impressions" />
                <Metric label="النقرات"        value={combined.clicks}    integer testid="combined-clicks" />
            </div>
        </div>
    );
}

// ── Per-platform card ───────────────────────────────────────────────────────
function PlatformCard({ p }) {
    const theme = PLATFORM_THEME[p.platform] || { color: "#888", label: p.label, bg: "#fff", border: "#e5e7eb" };
    return (
        <div
            className="rounded-xl border-2 p-5"
            style={{ background: theme.bg, borderColor: theme.border }}
            data-testid={`ads-report-platform-${p.platform}`}
        >
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                    <div
                        className="w-9 h-9 rounded-lg flex items-center justify-center text-white font-extrabold"
                        style={{ background: theme.color, color: p.platform === "snapchat" ? "#000" : "#fff" }}
                    >
                        {theme.label[0]}
                    </div>
                    <h3 className="text-lg font-bold" style={{ fontFamily: "Tajawal" }}>{theme.label}</h3>
                </div>
                <span
                    className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                        p.roas >= 2 ? "bg-emerald-100 text-emerald-700"
                            : p.spend > 0 ? "bg-amber-100 text-amber-700" : "bg-gray-100 text-gray-600"
                    }`}
                >
                    {p.spend > 0 ? `${p.roas}x ROAS` : "بدون نشاط"}
                </span>
            </div>
            <div className="space-y-2.5 text-sm">
                <PlatformRow label="الصرف"      value={formatMoney(p.spend)} unit="ر.س" bold />
                <PlatformRow label="العائد"     value={formatMoney(p.revenue)} unit="ر.س" colorClass="text-emerald-700" />
                <PlatformRow label="الطلبات"    value={formatInt(p.purchases)} />
                <div className="h-px bg-border/70 my-2" />
                <PlatformRow label="CPC"        value={p.cpc > 0 ? formatMoney(p.cpc) : "—"} unit={p.cpc > 0 ? "ر.س" : ""} />
                <PlatformRow label="CPM"        value={p.cpm > 0 ? formatMoney(p.cpm) : "—"} unit={p.cpm > 0 ? "ر.س" : ""} />
                <PlatformRow label="CTR"        value={p.ctr > 0 ? `${p.ctr}%` : "—"} />
                <PlatformRow label="CPA"        value={p.cpa > 0 ? formatMoney(p.cpa) : "—"} unit={p.cpa > 0 ? "ر.س" : ""} />
                <PlatformRow label="مرات الظهور" value={formatInt(p.impressions)} />
                <PlatformRow label="النقرات"     value={formatInt(p.clicks)} />
            </div>
        </div>
    );
}

function PlatformRow({ label, value, unit, bold, colorClass = "" }) {
    return (
        <div className="flex items-center justify-between">
            <span className="text-muted-foreground text-xs">{label}</span>
            <span className={"num " + (bold ? "font-extrabold text-base " : "font-bold ") + colorClass} dir="ltr">
                {value}{unit && <span className="text-xs font-normal text-muted-foreground ms-1">{unit}</span>}
            </span>
        </div>
    );
}

// ── Daily spend comparison chart ────────────────────────────────────────────
function DailySpendChart({ series }) {
    if (!series.length) return null;
    return (
        <div className="rounded-xl border border-border bg-white p-5" data-testid="ads-report-daily-chart">
            <div className="flex items-center gap-2 mb-4">
                <ChartLine size={20} weight="duotone" className="text-brand" />
                <h3 className="text-lg font-bold" style={{ fontFamily: "Tajawal" }}>الصرف اليومي عبر المنصات</h3>
            </div>
            <div style={{ width: "100%", height: 320 }}>
                <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
                    <LineChart data={series} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
                        <XAxis dataKey="date" tick={{ fontSize: 11 }} reversed />
                        <YAxis tick={{ fontSize: 11 }} />
                        <Tooltip
                            formatter={(v) => `${formatMoney(v)} ر.س`}
                            labelFormatter={(l) => `التاريخ: ${l}`}
                        />
                        <Legend />
                        <Line type="monotone" dataKey="snapchat" name="Snapchat" stroke={PLATFORM_THEME.snapchat.color} strokeWidth={2.5} dot={false} />
                        <Line type="monotone" dataKey="tiktok"   name="TikTok"   stroke={PLATFORM_THEME.tiktok.color}   strokeWidth={2.5} dot={false} />
                        <Line type="monotone" dataKey="meta"     name="Meta"     stroke={PLATFORM_THEME.meta.color}     strokeWidth={2.5} dot={false} />
                    </LineChart>
                </ResponsiveContainer>
            </div>
        </div>
    );
}

// ── Side-by-side comparison table ───────────────────────────────────────────
function ComparisonTable({ platforms, combined }) {
    const rows = [
        { key: "spend",       label: "الصرف",       money: true },
        { key: "revenue",     label: "العائد",       money: true },
        { key: "purchases",   label: "الطلبات",      integer: true },
        { key: "roas",        label: "ROAS",         suffix: "x" },
        { key: "cpc",         label: "CPC",          money: true },
        { key: "cpm",         label: "CPM",          money: true },
        { key: "ctr",         label: "CTR",          suffix: "%" },
        { key: "cpa",         label: "CPA (تكلفة شراء)", money: true },
        { key: "impressions", label: "مرات الظهور",  integer: true },
        { key: "clicks",      label: "النقرات",      integer: true },
    ];

    const fmt = (row, p) => {
        const v = p[row.key];
        if (row.integer) return formatInt(v || 0);
        if (row.suffix === "x") return v > 0 ? `${v}x` : "—";
        if (row.suffix === "%") return v > 0 ? `${v}%` : "—";
        if (row.money) return v > 0 ? formatMoney(v) : "—";
        return String(v ?? "—");
    };

    return (
        <div className="rounded-xl border border-border bg-white p-5" data-testid="ads-report-comparison-table">
            <div className="flex items-center gap-2 mb-4">
                <ChartBar size={20} weight="duotone" className="text-brand" />
                <h3 className="text-lg font-bold" style={{ fontFamily: "Tajawal" }}>مقارنة المقاييس جنباً إلى جنب</h3>
            </div>
            <div className="overflow-x-auto">
                <table className="w-full text-right text-sm border-collapse
                    [&_th]:px-3 [&_th]:py-2.5 [&_th]:border [&_th]:border-border [&_th]:whitespace-nowrap [&_th]:bg-accent/60 [&_th]:font-semibold
                    [&_td]:px-3 [&_td]:py-2.5 [&_td]:border [&_td]:border-border [&_td]:whitespace-nowrap">
                    <thead>
                        <tr>
                            <th className="text-right">المقياس</th>
                            {platforms.map((p) => (
                                <th key={p.platform} className="text-center" style={{ color: PLATFORM_THEME[p.platform]?.color }}>
                                    {PLATFORM_THEME[p.platform]?.label || p.label}
                                </th>
                            ))}
                            <th className="text-center text-brand">الإجمالي</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => (
                            <tr key={row.key} className="hover:bg-accent/30">
                                <td className="font-semibold">{row.label}</td>
                                {platforms.map((p) => (
                                    <td key={p.platform} className="text-center num font-semibold">
                                        {fmt(row, p)}
                                    </td>
                                ))}
                                <td className="text-center num font-extrabold text-brand">
                                    {fmt(row, combined)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

// ── ROAS comparison bar chart ───────────────────────────────────────────────
function RoasComparison({ platforms }) {
    const chartData = useMemo(() => platforms.map((p) => ({
        platform: PLATFORM_THEME[p.platform]?.label || p.label,
        ROAS: p.roas,
        color: PLATFORM_THEME[p.platform]?.color,
    })), [platforms]);

    if (!chartData.some((p) => p.ROAS > 0)) return null;

    return (
        <div className="rounded-xl border border-border bg-white p-5" data-testid="ads-report-roas-chart">
            <div className="flex items-center gap-2 mb-4">
                <ChartBar size={20} weight="duotone" className="text-brand" />
                <h3 className="text-lg font-bold" style={{ fontFamily: "Tajawal" }}>مقارنة ROAS بين المنصات</h3>
            </div>
            <div style={{ width: "100%", height: 280 }}>
                <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
                    <BarChart data={chartData} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#eef2f7" />
                        <XAxis dataKey="platform" tick={{ fontSize: 12 }} />
                        <YAxis tick={{ fontSize: 11 }} />
                        <Tooltip formatter={(v) => `${v}x`} />
                        <Bar dataKey="ROAS" radius={[6, 6, 0, 0]}>
                            {chartData.map((row, i) => (
                                <Cell key={i} fill={row.color} />
                            ))}
                        </Bar>
                    </BarChart>
                </ResponsiveContainer>
            </div>
        </div>
    );
}

// ── Reusable Metric tile ─────────────────────────────────────────────────────
function Metric({ label, value, money, integer, suffix, testid, big, colorClass = "" }) {
    let display;
    if (integer) display = formatInt(value || 0);
    else if (suffix === "x") display = value > 0 ? `${value}x` : "—";
    else if (money) display = formatMoney(value || 0);
    else display = String(value ?? "—");

    return (
        <div
            className={"bg-white border border-border rounded-lg " + (big ? "p-4" : "p-3")}
            data-testid={testid}
        >
            <div className="text-xs text-muted-foreground mb-1">{label}</div>
            <div
                className={"num font-extrabold " + (big ? "text-xl " : "text-base ") + colorClass}
                style={{ fontFamily: "Tajawal" }}
            >
                {display}{money && value > 0 && <span className="text-xs font-normal text-muted-foreground ms-1">ر.س</span>}
            </div>
        </div>
    );
}
