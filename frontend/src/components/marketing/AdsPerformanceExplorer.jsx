import { useMemo, useState } from "react";
import {
    Bar,
    BarChart,
    CartesianGrid,
    Cell,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";

const SERIES = Object.freeze([
    {
        id: "orders",
        label: "عمليات الشراء/البيع",
        valueKey: "orders",
        tone: "bg-blue-600 text-white",
        stroke: "#2563eb",
        format: "integer",
        hint: "الطلبات المنسوبة",
    },
    {
        id: "sales",
        label: "المبيعات المنسوبة",
        valueKey: "sales_sar",
        tone: "bg-rose-600 text-white",
        stroke: "#dc2626",
        format: "money",
        hint: "قيمة المبيعات",
    },
    {
        id: "roas",
        label: "عائد الإنفاق الإعلاني",
        valueKey: "roas",
        tone: "bg-amber-400 text-slate-950",
        stroke: "#f59e0b",
        format: "ratio",
        hint: "المبيعات ÷ الصرف",
    },
    {
        id: "spend",
        label: "التكلفة",
        valueKey: "spend_sar",
        tone: "bg-emerald-700 text-white",
        stroke: "#15803d",
        format: "money",
        hint: "إجمالي الصرف",
    },
]);

const SNAPCHAT_SERIES_TEXT = Object.freeze({
    orders: { label: "طلبات سلة المطابقة", hint: "سلة · UTM Campaign ID حرفي" },
    sales: { label: "مبيعات سلة", hint: "سلة · الطلبات المطابقة" },
    roas: { label: "ROAS سلة", hint: "مبيعات سلة ÷ صرف Snapchat" },
    spend: { label: "صرف Snapchat", hint: "Snapchat Ads API" },
});

function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function formatMetric(value, type) {
    const parsed = finite(value);
    if (parsed === null) return "—";
    if (type === "integer") return parsed.toLocaleString("en-US", { maximumFractionDigits: 0 });
    if (type === "ratio") {
        return `${parsed.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}×`;
    }
    return `${parsed.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ر.س`;
}

function displayDate(value) {
    const raw = String(value || "");
    const match = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!match) return raw;
    return `${Number(match[3])}/${Number(match[2])}`;
}

export function formatAdsHourLabel(value) {
    const match = String(value || "").match(/^(\d{2}):00$/);
    if (!match) return String(value || "");
    const hour = Number(match[1]);
    const period = hour < 12 ? "ص" : "م";
    const display = hour % 12 || 12;
    return `${display} ${period}`;
}

export function buildAdsHourlyChartInput(snapshot = {}) {
    if (
        snapshot?.date_from !== snapshot?.date_to
        || snapshot?.source?.hourly_available !== true
        || !Array.isArray(snapshot?.hourly)
    ) {
        return [];
    }
    return snapshot.hourly
        .filter((row) => /^\d{2}:00$/.test(String(row?.hour || "")))
        .sort((left, right) => Number(left.hour_index || 0) - Number(right.hour_index || 0))
        .map((row) => ({
            date: formatAdsHourLabel(row.hour),
            date_iso: `${row.date || snapshot.date_from}T${row.hour}`,
            orders: finite(row.orders) || 0,
            sales_sar: finite(row.sales_sar) || 0,
            roas: finite(row.roas),
            spend_sar: finite(row.spend_sar) || 0,
            observed: row.observed === true,
            is_future: row.is_future === true,
        }));
}

function sourceSpecificValue(row, series, snapchat) {
    if (!snapchat) return row?.[series.valueKey];
    const keys = {
        orders: "salla_matched_orders",
        sales: "salla_sales_sar",
        roas: "salla_roas",
        spend: "snapchat_spend_sar",
    };
    return row?.[keys[series.id]];
}

export function buildAdsChartRows(daily = [], { snapchat = false } = {}) {
    const source = Array.isArray(daily) ? daily : [];
    const maximums = Object.fromEntries(SERIES.map((series) => [
        series.id,
        Math.max(0, ...source.map((row) => finite(sourceSpecificValue(row, series, snapchat)) || 0)),
    ]));
    return source.map((row) => {
        const output = {
            date: displayDate(row?.date),
            date_iso: row?.date_iso || row?.date,
            observed: row?.observed === true,
            is_future: row?.is_future === true,
        };
        SERIES.forEach((series) => {
            const raw = finite(sourceSpecificValue(row, series, snapchat));
            output[`${series.id}_raw`] = raw;
            output[series.id] = raw === null || maximums[series.id] <= 0
                ? null
                : Number(((raw / maximums[series.id]) * 100).toFixed(4));
        });
        return output;
    });
}

export function toggleMetricVisibility(current, metricId) {
    const next = new Set(current);
    if (next.has(metricId)) next.delete(metricId);
    else next.add(metricId);
    return next;
}

function ChartTooltip({ active, label, payload, granularity = "day", seriesList = SERIES }) {
    if (!active || !Array.isArray(payload) || !payload.length) return null;
    return (
        <div className="min-w-56 rounded-2xl border border-slate-200 bg-white p-4 text-right shadow-xl" dir="rtl">
            <div className="mb-3 font-black text-slate-900">
                {granularity === "hour" ? "الوقت" : "التاريخ"}: {label}
            </div>
            <div className="space-y-2">
                {payload.map((entry) => {
                    const series = seriesList.find((item) => item.id === entry.dataKey);
                    if (!series) return null;
                    return (
                        <div key={series.id} className="flex items-center justify-between gap-4 text-sm">
                            <span className="font-bold text-slate-600">{series.label}</span>
                            <span className="font-mono font-black text-slate-900">
                                {formatMetric(entry.payload?.[`${series.id}_raw`], series.format)}
                            </span>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

function MetricToggle({ series, value, active, onToggle }) {
    return (
        <button
            type="button"
            onClick={onToggle}
            aria-pressed={active}
            data-testid={`ads-performance-metric-${series.id}`}
            className={`relative min-h-36 overflow-hidden p-5 text-right transition ${series.tone} ${active ? "opacity-100" : "opacity-35 grayscale"}`}
        >
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="text-base font-black opacity-95">{series.label}</div>
                    <div className="mt-4 break-words font-mono text-4xl font-black sm:text-[2.7rem]">
                        {formatMetric(value, series.format)}
                    </div>
                    <div className="mt-2 text-sm font-bold opacity-80">{series.hint}</div>
                </div>
                <span className="rounded-full bg-white/20 px-2.5 py-1 text-xs font-black">
                    {active ? "ظاهر" : "مخفي"}
                </span>
            </div>
            <div className="absolute inset-x-0 bottom-0 h-1.5 bg-black/15">
                <div className="h-full bg-white/70 transition-all" style={{ width: active ? "100%" : "18%" }} />
            </div>
        </button>
    );
}

function SingleDayBarTooltip({ active, payload, series }) {
    if (!active || !payload?.length) return null;
    return (
        <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-right shadow-lg" dir="rtl">
            <div className="text-xs font-black text-slate-500">{series.label}</div>
            <div className="mt-1 font-mono text-base font-black text-slate-950">
                {formatMetric(payload[0]?.payload?.raw, series.format)}
            </div>
        </div>
    );
}

function SingleDaySnapshot({ row, visibleSeries }) {
    return (
        <div className="min-h-80 rounded-2xl border border-slate-200 bg-slate-50 p-4 sm:p-6" data-testid="ads-performance-single-day-chart">
            <div className="mb-5 flex flex-wrap items-center justify-between gap-2">
                <div>
                    <h3 className="text-lg font-black text-slate-900">رسم أداء يوم واحد</h3>
                    <p className="mt-1 text-sm font-semibold text-slate-500">
                        بيانات الساعات قيد أول مزامنة؛ يظهر هذا الرسم المؤقت حتى وصول صفوف HOUR من Snapchat.
                    </p>
                </div>
                <span className="rounded-full bg-white px-3 py-1 text-sm font-black text-slate-700 shadow-sm">
                    {row?.date || "اليوم"}
                </span>
            </div>
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                {visibleSeries.map((series) => {
                    const raw = finite(row?.[`${series.id}_raw`]);
                    const safeValue = raw === null ? 0 : Math.max(0, raw);
                    const domainMax = safeValue > 0 ? safeValue * 1.18 : 1;
                    const chartData = [{ id: series.id, value: safeValue, raw }];
                    return (
                        <article
                            key={series.id}
                            className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
                            data-testid={`ads-performance-single-day-bar-${series.id}`}
                        >
                            <div className="text-center text-sm font-black text-slate-700">{series.label}</div>
                            <div className="mt-2 text-center font-mono text-xl font-black text-slate-950">
                                {formatMetric(raw, series.format)}
                            </div>
                            <div className="mt-3 h-52">
                                <ResponsiveContainer width="100%" height="100%">
                                    <BarChart data={chartData} margin={{ top: 12, right: 18, bottom: 4, left: 18 }}>
                                        <CartesianGrid strokeDasharray="3 3" vertical={false} />
                                        <YAxis domain={[0, domainMax]} hide />
                                        <XAxis dataKey="id" hide />
                                        <Tooltip content={<SingleDayBarTooltip series={series} />} cursor={{ fill: "rgba(148, 163, 184, 0.08)" }} />
                                        <Bar dataKey="value" radius={[12, 12, 3, 3]} maxBarSize={92} animationDuration={350}>
                                            <Cell fill={series.stroke} />
                                        </Bar>
                                    </BarChart>
                                </ResponsiveContainer>
                            </div>
                            <div className="mt-1 text-center text-xs font-bold text-slate-400">{series.hint}</div>
                        </article>
                    );
                })}
            </div>
        </div>
    );
}

export default function AdsPerformanceExplorer({ totals = {}, daily = [], platformLabel = "المنصة" }) {
    const [visibleMetrics, setVisibleMetrics] = useState(
        () => new Set(SERIES.map((series) => series.id)),
    );
    const snapchat = platformLabel === "سناب شات";
    const seriesList = useMemo(
        () => SERIES.map((series) => (
            snapchat ? { ...series, ...SNAPCHAT_SERIES_TEXT[series.id] } : series
        )),
        [snapchat],
    );
    const hourlyInput = [];
    const chartInput = hourlyInput.length ? hourlyInput : daily;
    const chartRows = useMemo(
        () => buildAdsChartRows(chartInput, { snapchat }),
        [chartInput, snapchat],
    );
    const hourlyMode = hourlyInput.length > 0;
    const visibleSeries = seriesList.filter((series) => visibleMetrics.has(series.id));

    function toggleMetric(metricId) {
        setVisibleMetrics((current) => toggleMetricVisibility(current, metricId));
    }

    return (
        <section
            className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm"
            data-testid="ads-performance-explorer"
            data-chart-granularity={hourlyMode ? "hour" : "day"}
            aria-label={`الرسم البياني لأداء ${platformLabel}`}
        >
            <div className="grid sm:grid-cols-2 xl:grid-cols-4" role="group" aria-label="اختيار خطوط الرسم البياني">
                {seriesList.map((series) => (
                    <MetricToggle
                        key={series.id}
                        series={series}
                        value={sourceSpecificValue(totals, series, snapchat)}
                        active={visibleMetrics.has(series.id)}
                        onToggle={() => toggleMetric(series.id)}
                    />
                ))}
            </div>

            <div className="border-t border-slate-200 bg-white p-4 sm:p-6">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <h2 className="text-xl font-black text-slate-900">
                            {hourlyMode ? "اتجاه الأداء بالساعة" : "اتجاه الأداء اليومي"}
                        </h2>
                        <p className="mt-1 text-sm font-semibold text-slate-500">
                            {hourlyMode
                                ? "عند اختيار يوم واحد يعرض ميزان ساعات اليوم من 12 صباحًا إلى 11 مساءً مثل Snapchat، وتتحدث الساعة الحالية تلقائيًا."
                                : "اضغط على أي بطاقة لإخفاء مؤشرها أو إظهاره، وتظهر الفترات المتعددة كرسم زمني يومي."}
                        </p>
                    </div>
                    <div className="rounded-full bg-slate-100 px-3 py-1 text-sm font-black text-slate-700">
                        {visibleSeries.length} من {seriesList.length} مؤشرات ظاهرة
                    </div>
                </div>

                {chartRows.length === 1 && visibleSeries.length ? (
                    <SingleDaySnapshot row={chartRows[0]} visibleSeries={visibleSeries} />
                ) : chartRows.length > 1 && visibleSeries.length ? (
                    <div className="h-96" data-testid={hourlyMode ? "ads-performance-hourly-chart" : "ads-performance-chart"}>
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={chartRows} margin={{ top: 12, right: 12, bottom: 8, left: 12 }}>
                                <CartesianGrid strokeDasharray="3 3" vertical={hourlyMode} />
                                <XAxis
                                    dataKey="date"
                                    interval={hourlyMode ? 1 : 0}
                                    tickMargin={12}
                                    tick={{ fontSize: 14, fontWeight: 800 }}
                                />
                                <YAxis domain={[0, 100]} hide />
                                <Tooltip content={<ChartTooltip granularity={hourlyMode ? "hour" : "day"} seriesList={seriesList} />} />
                                {visibleSeries.map((series) => (
                                    <Line
                                        key={series.id}
                                        type={hourlyMode ? "linear" : "monotone"}
                                        dataKey={series.id}
                                        name={series.label}
                                        stroke={series.stroke}
                                        strokeWidth={hourlyMode ? 3 : 4}
                                        dot={hourlyMode ? false : { r: 4, strokeWidth: 2 }}
                                        activeDot={{ r: 7 }}
                                        connectNulls={false}
                                        animationDuration={300}
                                    />
                                ))}
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                ) : (
                    <div className="flex h-80 items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-slate-50 text-center text-sm font-bold text-slate-500">
                        {chartRows.length
                            ? "كل المؤشرات مخفية. اضغط على بطاقة لإظهارها."
                            : "لا توجد نقاط موثقة ضمن الفترة المحددة."}
                    </div>
                )}
            </div>
        </section>
    );
}

export { SERIES as ADS_PERFORMANCE_SERIES };
