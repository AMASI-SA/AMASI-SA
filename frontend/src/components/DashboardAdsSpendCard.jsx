import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    ArrowsClockwise,
    ChartLineUp,
    WarningCircle,
} from "@phosphor-icons/react";
import {
    CartesianGrid,
    Legend,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import { addDaysISO, todaySA } from "../lib/dates";
import {
    DASHBOARD_ADS_PROVIDERS,
    getDashboardAdsSpend,
} from "../services/dashboardAdsSpend";

const AUTO_REFRESH_MS = 5 * 60 * 1000;
const PROVIDER_META = Object.freeze({
    snapchat: { label: "سناب شات", stroke: "#eab308" },
    meta: { label: "ميتا", stroke: "#2563eb" },
    tiktok: { label: "تيك توك", stroke: "#0f172a" },
    google: { label: "Google Ads", stroke: "#4285f4" },
});

function money(value) {
    const numeric = Number(value || 0);
    return `${numeric.toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ر.س`;
}

function selectedPeriodLabel(fromDate, toDate) {
    if (!fromDate && !toDate) return "الفترة المحددة في الملخص التنفيذي";
    const from = fromDate || toDate;
    const to = toDate || fromDate;
    const today = todaySA();
    if (from === today && to === today) return `صرفيات اليوم (${today})`;
    const yesterday = addDaysISO(today, -1);
    if (from === yesterday && to === yesterday) return `صرفيات أمس (${yesterday})`;
    if (from === to) return `صرفيات يوم ${from}`;
    return `من ${from} إلى ${to}`;
}

function isSingleDay(fromDate, toDate) {
    return Boolean(fromDate && toDate && fromDate === toDate);
}

function chartPoints(data, singleDay) {
    if (singleDay) {
        return Array.isArray(data?.hourly_spend) ? data.hourly_spend : [];
    }
    return Array.isArray(data?.daily_spend) ? data.daily_spend : [];
}

function hasSeries(points, key) {
    return points.some((point) => (
        point?.[key] !== null
        && point?.[key] !== undefined
        && Number.isFinite(Number(point[key]))
    ));
}

function providerValue(data, provider) {
    const state = data?.providers?.[provider] || {};
    const total = data?.provider_totals_sar?.[provider];
    if (total !== null && total !== undefined && Number.isFinite(Number(total))) {
        return money(total);
    }
    if (!state.connected) return "غير مربوط";
    return "لا بيانات";
}

export function DashboardAdsSpendCardContent({
    data,
    fromDate,
    toDate,
    loading = false,
    refreshing = false,
    error = "",
    onRefresh,
}) {
    const singleDay = isSingleDay(fromDate, toDate);
    const points = useMemo(
        () => chartPoints(data, singleDay),
        [data, singleDay],
    );
    const total = Number(data?.total_sar || 0);
    const displayError = error || data?.refresh_error || "";
    const xKey = singleDay ? "hour" : "date";
    const availableSeries = useMemo(
        () => Object.fromEntries(
            DASHBOARD_ADS_PROVIDERS.map((provider) => [
                provider,
                hasSeries(points, provider),
            ]),
        ),
        [points],
    );
    const hasAnySeries = Object.values(availableSeries).some(Boolean);
    const hasTikTokMakeMarker = singleDay
        && data?.providers?.tiktok?.hourly_source === "make_daily_total_marker";

    return (
        <section
            className="h-full overflow-hidden rounded-2xl border-2 border-yellow-300 bg-white shadow-sm"
            data-testid="dashboard-ads-spend-card"
            data-from-date={fromDate || ""}
            data-to-date={toDate || ""}
            data-chart-granularity={singleDay ? "hour" : "day"}
        >
            <div className="flex min-h-14 items-center justify-between gap-3 bg-yellow-400 px-4 py-3 text-black">
                <div className="flex min-w-0 items-center gap-2">
                    <ChartLineUp size={20} weight="bold" className="shrink-0" />
                    <div className="min-w-0">
                        <h2 className="truncate text-base font-extrabold sm:text-lg">
                            صرفيات منصات الإعلانات
                        </h2>
                        <p className="truncate text-[10px] font-bold text-black/70 sm:text-xs">
                            مرتبطة بتاريخ الملخص التنفيذي للأرباح
                        </p>
                    </div>
                </div>
                <button
                    type="button"
                    onClick={onRefresh}
                    disabled={refreshing}
                    className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-black/10 bg-white/80 transition hover:bg-white disabled:opacity-50"
                    aria-label="تحديث صرفيات الإعلانات"
                    data-testid="dashboard-ads-spend-refresh"
                >
                    <ArrowsClockwise
                        size={17}
                        weight="bold"
                        className={refreshing ? "animate-spin" : ""}
                    />
                </button>
            </div>

            <div className="p-3 sm:p-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <div>
                        <div className="text-xs font-extrabold text-slate-800" data-testid="dashboard-ads-spend-period">
                            {selectedPeriodLabel(fromDate, toDate)}
                        </div>
                        <div className="mt-1 text-[10px] font-semibold text-slate-500">
                            سناب شات + ميتا + تيك توك + Google Ads · العرض بتوقيت الرياض
                        </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        <div className="rounded-full border border-yellow-200 bg-yellow-50 px-3 py-1 text-[11px] font-extrabold text-yellow-900">
                            {singleDay ? "عرض ساعي" : "عرض يومي"}
                        </div>
                        <div className="rounded-full border border-yellow-200 bg-yellow-50 px-3 py-1 text-[11px] font-extrabold text-yellow-900">
                            إجمالي المنصات: {money(total)}
                        </div>
                    </div>
                </div>

                <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="dashboard-ads-provider-totals">
                    {DASHBOARD_ADS_PROVIDERS.map((provider) => {
                        const meta = PROVIDER_META[provider];
                        const state = data?.providers?.[provider] || {};
                        return (
                            <div
                                key={provider}
                                className="rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-2"
                                data-testid={`dashboard-ads-provider-${provider}`}
                            >
                                <div className="flex items-center gap-1.5 text-[10px] font-bold text-slate-600">
                                    <span
                                        className="h-2 w-2 rounded-full"
                                        style={{ backgroundColor: meta.stroke }}
                                    />
                                    <span>{meta.label}</span>
                                </div>
                                <div className="mt-1 text-[11px] font-extrabold text-slate-900">
                                    {providerValue(data, provider)}
                                </div>
                                <div className="mt-0.5 text-[9px] font-semibold text-slate-400">
                                    {singleDay
                                        ? provider === "tiktok"
                                            && state.hourly_source === "make_daily_total_marker"
                                            ? "إجمالي Make عند آخر تحديث"
                                            : state.hourly_available
                                            ? "بيانات ساعية أصلية"
                                            : state.daily_available
                                                ? "إجمالي اليوم متاح"
                                                : state.connected
                                                    ? "بانتظار البيانات"
                                                    : "غير متصل"
                                        : state.daily_available
                                            ? "بيانات يومية أصلية"
                                            : state.connected
                                                ? "بانتظار البيانات"
                                                : "غير متصل"}
                                </div>
                            </div>
                        );
                    })}
                </div>

                {singleDay && (
                    <div className="mb-3 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-[10px] font-semibold text-slate-600">
                        {hasTikTokMakeMarker
                            ? "علامة تيك توك تمثل إجمالي اليوم عند ساعة آخر تحديث من Make؛ بقية الخطوط مبنية على ساعات المنصات الأصلية، ولا يتم توزيع الإجمالي بالتخمين."
                            : "خطوط الساعات مبنية على ساعات المنصات الأصلية فقط، ولا يتم توزيع إجمالي اليوم على الساعات بالتخمين."}
                    </div>
                )}

                {displayError && (
                    <div
                        className="mb-3 flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-bold text-amber-900"
                        data-testid="dashboard-ads-spend-error"
                    >
                        <WarningCircle size={17} weight="fill" className="mt-0.5 shrink-0" />
                        <span>{displayError}</span>
                    </div>
                )}

                {loading && !points.length ? (
                    <div className="h-[360px] animate-pulse rounded-xl bg-slate-100" data-testid="dashboard-ads-spend-loading" />
                ) : points.length && hasAnySeries ? (
                    <div
                        className="h-[360px] min-w-0"
                        dir="ltr"
                        data-testid={singleDay
                            ? "dashboard-ads-spend-hourly-chart"
                            : "dashboard-ads-spend-daily-chart"}
                    >
                        <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
                            <LineChart data={points} margin={{ top: 12, right: 12, left: 2, bottom: 8 }}>
                                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                                <XAxis
                                    dataKey={xKey}
                                    tick={{ fontSize: 10, fill: "#64748b" }}
                                    minTickGap={singleDay ? 8 : 16}
                                    interval={singleDay ? 2 : "preserveStartEnd"}
                                />
                                <YAxis
                                    tick={{ fontSize: 10, fill: "#64748b" }}
                                    width={54}
                                    tickFormatter={(value) => Number(value).toLocaleString("en-US")}
                                />
                                <Tooltip
                                    formatter={(value, name) => [money(value), name]}
                                    labelFormatter={(value) => singleDay
                                        ? `الساعة: ${value}`
                                        : `التاريخ: ${value}`}
                                    contentStyle={{
                                        direction: "rtl",
                                        borderRadius: 12,
                                        borderColor: "#e2e8f0",
                                        fontFamily: "Cairo",
                                    }}
                                />
                                <Legend wrapperStyle={{ direction: "rtl", fontSize: 11 }} />
                                {DASHBOARD_ADS_PROVIDERS.map((provider) => (
                                    availableSeries[provider] ? (
                                        <Line
                                            key={provider}
                                            type="monotone"
                                            dataKey={provider}
                                            name={PROVIDER_META[provider].label}
                                            stroke={PROVIDER_META[provider].stroke}
                                            strokeWidth={provider === "snapchat" ? 2.5 : 2.25}
                                            connectNulls={false}
                                            dot={provider === "tiktok" && hasTikTokMakeMarker
                                                ? { r: 4, strokeWidth: 2 }
                                                : false}
                                        />
                                    ) : null
                                ))}
                            </LineChart>
                        </ResponsiveContainer>
                    </div>
                ) : (
                    <div
                        className="flex h-[360px] items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 text-center text-sm font-bold text-slate-500"
                        data-testid={singleDay
                            ? "dashboard-ads-spend-hourly-empty"
                            : "dashboard-ads-spend-daily-empty"}
                    >
                        لا توجد صرفيات إعلانية أصلية ضمن التاريخ المحدد حتى الآن.
                    </div>
                )}
            </div>
        </section>
    );
}

export default function DashboardAdsSpendCard({ fromDate, toDate }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState("");
    const requestId = useRef(0);

    const load = useCallback(async ({ silent = false, refresh = !silent } = {}) => {
        const currentRequest = requestId.current + 1;
        requestId.current = currentRequest;
        if (!silent) setRefreshing(true);
        if (!data) setLoading(true);
        try {
            const next = await getDashboardAdsSpend({
                dateFrom: fromDate,
                dateTo: toDate,
                refresh,
            });
            if (requestId.current !== currentRequest) return;
            setData(next);
            setError("");
        } catch (requestError) {
            if (requestId.current !== currentRequest) return;
            const detail = requestError?.response?.data?.detail;
            setError(
                (typeof detail === "object" ? detail?.message : detail)
                || requestError?.message
                || "تعذر قراءة صرفيات منصات الإعلانات.",
            );
        } finally {
            if (requestId.current === currentRequest) {
                setLoading(false);
                if (!silent) setRefreshing(false);
            }
        }
    }, [data, fromDate, toDate]);

    useEffect(() => {
        load({ refresh: true });
    }, [fromDate, toDate]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        const timer = window.setInterval(() => {
            if (document.visibilityState === "visible") {
                load({ silent: true, refresh: false });
            }
        }, AUTO_REFRESH_MS);
        const onVisible = () => {
            if (document.visibilityState === "visible") {
                load({ silent: true, refresh: false });
            }
        };
        document.addEventListener("visibilitychange", onVisible);
        return () => {
            window.clearInterval(timer);
            document.removeEventListener("visibilitychange", onVisible);
        };
    }, [load]);

    return (
        <DashboardAdsSpendCardContent
            data={data}
            fromDate={fromDate}
            toDate={toDate}
            loading={loading}
            refreshing={refreshing}
            error={error}
            onRefresh={() => load({ refresh: true })}
        />
    );
}

export { selectedPeriodLabel };
