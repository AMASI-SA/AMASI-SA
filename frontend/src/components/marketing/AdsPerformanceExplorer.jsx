import { useMemo, useState } from "react";
import {
    CartesianGrid,
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

function finite(value) {
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

export function buildAdsChartRows(daily = []) {
    const source = Array.isArray(daily) ? daily : [];
    const maximums = Object.fromEntries(SERIES.map((series) => [
        series.id,
        Math.max(0, ...source.map((row) => finite(row?.[series.valueKey]) || 0)),
    ]));
    return source.map((row) => {
        const output = {
            date: displayDate(row?.date),
            date_iso: row?.date,
        };
        SERIES.forEach((series) => {
            const raw = finite(row?.[series.valueKey]);
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

function ChartTooltip({ active, label, payload }) {
    if (!active || !Array.isArray(payload) || !payload.length) return null;
    return (
        <div className="min-w-56 rounded-2xl border border-slate-200 bg-white p-4 text-right shadow-xl" dir="rtl">
            <div className="mb-3 font-black text-slate-900">التاريخ: {label}</div>
            <div className="space-y-2">
                {payload.map((entry) => {
                    const series = SERIES.find((item) => item.id === entry.dataKey);
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
                    <div className="text-sm font-black opacity-90">{series.label}</div>
                    <div className="mt-4 break-words font-mono text-3xl font-black sm:text-4xl">
                        {formatMetric(value, series.format)}
                    </div>
                    <div className="mt-2 text-xs font-bold opacity-75">{series.hint}</div>
                </div>
                <span className="rounded-full bg-white/20 px-2 py-1 text-[10px] font-black">
                    {active ? "ظاهر" : "مخفي"}
                </span>
            </div>
            <div className="absolute inset-x-0 bottom-0 h-1.5 bg-black/15">
                <div className="h-full bg-white/70 transition-all" style={{ width: active ? "100%" : "18%" }} />
            </div>
        </button>
    );
}

export default function AdsPerformanceExplorer({ totals = {}, daily = [], platformLabel = "المنصة" }) {
    const [visibleMetrics, setVisibleMetrics] = useState(
        () => new Set(SERIES.map((series) => series.id)),
    );
    const chartRows = useMemo(() => buildAdsChartRows(daily), [daily]);
    const visibleSeries = SERIES.filter((series) => visibleMetrics.has(series.id));

    function toggleMetric(metricId) {
        setVisibleMetrics((current) => toggleMetricVisibility(current, metricId));
    }

    return (
        <section
            className="overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-sm"
            data-testid="ads-performance-explorer"
            aria-label={`الرسم البياني لأداء ${platformLabel}`}
        >
            <div className="grid sm:grid-cols-2 xl:grid-cols-4" role="group" aria-label="اختيار خطوط الرسم البياني">
                {SERIES.map((series) => (
                    <MetricToggle
                        key={series.id}
                        series={series}
                        value={totals?.[series.valueKey]}
                        active={visibleMetrics.has(series.id)}
                        onToggle={() => toggleMetric(series.id)}
                    />
                ))}
            </div>

            <div className="border-t border-slate-200 bg-white p-4 sm:p-6">
                <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <h2 className="font-black text-slate-900">اتجاه الأداء اليومي</h2>
                        <p className="mt-1 text-xs font-semibold text-slate-500">
                            اضغط على أي بطاقة لإخفاء خطها أو إظهاره. الخطوط مطبّعة لعرض الاتجاه، والقيم الأصلية تظهر عند المرور.
                        </p>
                    </div>
                    <div className="rounded-full bg-slate-100 px-3 py-1 text-xs font-black text-slate-600">
                        {visibleSeries.length} من {SERIES.length} مؤشرات ظاهرة
                    </div>
                </div>

                {chartRows.length && visibleSeries.length ? (
                    <div className="h-80" data-testid="ads-performance-chart">
                        <ResponsiveContainer width="100%" height="100%">
                            <LineChart data={chartRows} margin={{ top: 10, right: 8, bottom: 5, left: 8 }}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                                <XAxis dataKey="date" tickMargin={10} />
                                <YAxis domain={[0, 100]} hide />
                                <Tooltip content={<ChartTooltip />} />
                                {visibleSeries.map((series) => (
                                    <Line
                                        key={series.id}
                                        type="monotone"
                                        dataKey={series.id}
                                        name={series.label}
                                        stroke={series.stroke}
                                        strokeWidth={3}
                                        dot={{ r: 3 }}
                                        activeDot={{ r: 6 }}
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
                            ? "كل المؤشرات مخفية. اضغط على بطاقة لإظهار خطها."
                            : "لا توجد نقاط يومية موثقة ضمن الفترة المحددة."}
                    </div>
                )}
            </div>
        </section>
    );
}

export { SERIES as ADS_PERFORMANCE_SERIES };
