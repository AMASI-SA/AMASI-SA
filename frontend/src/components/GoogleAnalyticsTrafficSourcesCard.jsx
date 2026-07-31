import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowsClockwise,
    ChartDonut,
    WarningCircle,
} from "@phosphor-icons/react";
import api from "../lib/api";

const SOURCE_PATH = "/integrations-v2/google_analytics_4/source-attribution-dashboard";
const PERIODS = [
    { key: "today", label: "اليوم" },
    { key: "month", label: "هذا الشهر" },
    { key: "last_30d", label: "آخر 30 يوم" },
];

const PLATFORM_TONES = {
    snapchat: "border-yellow-200 bg-yellow-50 text-yellow-900",
    tiktok: "border-slate-200 bg-slate-50 text-slate-900",
    meta: "border-blue-200 bg-blue-50 text-blue-900",
    google: "border-emerald-200 bg-emerald-50 text-emerald-900",
    direct: "border-violet-200 bg-violet-50 text-violet-900",
    other: "border-slate-200 bg-white text-slate-800",
};

function formatInt(value) {
    return Number(value || 0).toLocaleString("en-US", {
        maximumFractionDigits: 0,
    });
}

function formatMoney(value) {
    return Number(value || 0).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

function formatObservedAt(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return parsed.toLocaleString("en-US", {
        dateStyle: "short",
        timeStyle: "short",
    });
}

export function GoogleAnalyticsTrafficSourcesContent({ data, periodKey = "today" }) {
    const period = data?.[periodKey] || {};
    const rows = Array.isArray(period?.sources) ? period.sources : [];
    const visibleRows = rows.filter((row) => (
        Number(row?.sessions || 0) > 0
        || Number(row?.orders || 0) > 0
        || Number(row?.purchase_revenue || 0) > 0
    ));

    return (
        <div className="overflow-x-auto" data-testid="ga4-source-table-wrap">
            <table className="w-full min-w-[760px] text-sm" data-testid="ga4-source-table">
                <thead>
                    <tr className="border-b border-slate-200 text-xs text-slate-500">
                        <th className="p-3 text-right font-extrabold">المصدر</th>
                        <th className="p-3 text-center font-extrabold">الجلسات</th>
                        <th className="p-3 text-center font-extrabold">المستخدمون</th>
                        <th className="p-3 text-center font-extrabold">طلبات GA4</th>
                        <th className="p-3 text-left font-extrabold">مبيعات GA4 (ر.س)</th>
                    </tr>
                </thead>
                <tbody>
                    {visibleRows.length ? visibleRows.map((row) => {
                        const tone = PLATFORM_TONES[row.platform] || PLATFORM_TONES.other;
                        const rawSources = Array.isArray(row.raw_sources)
                            ? row.raw_sources.join("، ")
                            : "";
                        return (
                            <tr
                                key={row.key}
                                className="border-b border-slate-100 last:border-0 hover:bg-slate-50/70"
                                data-testid={`ga4-source-row-${row.platform || "other"}`}
                            >
                                <td className="p-3">
                                    <div className="flex items-center gap-2 min-w-0">
                                        <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-extrabold ${tone}`}>
                                            {row.label || row.key}
                                        </span>
                                        {rawSources && (
                                            <span
                                                className="truncate text-[10px] text-slate-400 max-w-[240px]"
                                                title={rawSources}
                                                dir="ltr"
                                            >
                                                {rawSources}
                                            </span>
                                        )}
                                    </div>
                                </td>
                                <td className="p-3 text-center num font-extrabold text-slate-800 tabular-nums">
                                    {formatInt(row.sessions)}
                                </td>
                                <td className="p-3 text-center num font-extrabold text-slate-800 tabular-nums">
                                    {formatInt(row.active_users)}
                                </td>
                                <td className="p-3 text-center num font-extrabold text-indigo-800 tabular-nums">
                                    {formatInt(row.orders)}
                                </td>
                                <td className="p-3 text-left num font-extrabold text-emerald-700 tabular-nums">
                                    {formatMoney(row.purchase_revenue)}
                                </td>
                            </tr>
                        );
                    }) : (
                        <tr>
                            <td colSpan={5} className="p-8 text-center text-sm text-slate-500">
                                لا توجد بيانات مصادر أو طلبات لهذه الفترة في Google Analytics.
                            </td>
                        </tr>
                    )}
                </tbody>
                <tfoot>
                    <tr className="border-t-2 border-slate-200 bg-slate-50/80 font-extrabold">
                        <td className="p-3 text-right">الإجمالي</td>
                        <td className="p-3 text-center num tabular-nums">{formatInt(period.sessions)}</td>
                        <td className="p-3 text-center text-slate-400">—</td>
                        <td className="p-3 text-center num text-indigo-800 tabular-nums">{formatInt(period.orders)}</td>
                        <td className="p-3 text-left num text-emerald-700 tabular-nums">{formatMoney(period.purchase_revenue)}</td>
                    </tr>
                </tfoot>
            </table>
        </div>
    );
}

export default function GoogleAnalyticsTrafficSourcesCard() {
    const [data, setData] = useState(null);
    const [periodKey, setPeriodKey] = useState("today");
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState("");

    const load = useCallback(async ({ force = false, silent = false } = {}) => {
        if (!silent) setRefreshing(true);
        try {
            const response = await api.get(
                `${SOURCE_PATH}${force ? "?force=true" : ""}`,
            );
            setData(response.data);
            setError("");
        } catch (requestError) {
            const detail = requestError?.response?.data?.detail;
            setError(
                (typeof detail === "object" ? detail?.message : detail)
                || "تعذر قراءة مصادر الزيارات والطلبات من Google Analytics.",
            );
        } finally {
            setLoading(false);
            if (!silent) setRefreshing(false);
        }
    }, []);

    useEffect(() => {
        load();
        const interval = window.setInterval(() => {
            if (document.visibilityState === "visible") {
                load({ silent: true });
            }
        }, 60_000);
        const onVisibility = () => {
            if (document.visibilityState === "visible") load({ silent: true });
        };
        document.addEventListener("visibilitychange", onVisibility);
        return () => {
            window.clearInterval(interval);
            document.removeEventListener("visibilitychange", onVisibility);
        };
    }, [load]);

    const period = useMemo(() => data?.[periodKey] || {}, [data, periodKey]);

    return (
        <section
            className="rounded-xl border-2 border-indigo-100 bg-gradient-to-br from-indigo-50/40 via-white to-blue-50/40 p-4 sm:p-5"
            data-testid="ga4-traffic-sources-section"
        >
            <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-4 mb-4">
                <div className="flex items-start gap-3">
                    <div className="w-11 h-11 rounded-xl bg-indigo-100 text-indigo-700 flex items-center justify-center shrink-0">
                        <ChartDonut size={24} weight="duotone" />
                    </div>
                    <div>
                        <div className="flex items-center gap-2 flex-wrap">
                            <h2 className="text-lg sm:text-xl font-extrabold text-slate-900">
                                مصادر الزيارات والطلبات — Google Analytics
                            </h2>
                            <span className="text-[10px] rounded-full border border-indigo-200 bg-white px-2 py-1 text-indigo-700 font-bold" dir="ltr">
                                Property {data?.property_id || "353865193"}
                            </span>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                            جدول مستقل حسب مصدر الجلسة. لا يستبدل تحويلات أو مبيعات Snapchat وMeta داخل بطاقات المنصات.
                            {data?.observed_at && (
                                <span className="ms-2">• آخر قراءة: {formatObservedAt(data.observed_at)}</span>
                            )}
                        </p>
                    </div>
                </div>

                <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
                    <div className="inline-flex rounded-lg border border-slate-200 bg-white p-1" data-testid="ga4-source-period-tabs">
                        {PERIODS.map((item) => (
                            <button
                                key={item.key}
                                type="button"
                                onClick={() => setPeriodKey(item.key)}
                                className={`rounded-md px-3 py-1.5 text-xs font-bold transition ${
                                    periodKey === item.key
                                        ? "bg-indigo-700 text-white"
                                        : "text-slate-600 hover:bg-indigo-50"
                                }`}
                                data-testid={`ga4-source-period-${item.key}`}
                            >
                                {item.label}
                            </button>
                        ))}
                    </div>
                    <button
                        type="button"
                        onClick={() => load({ force: true })}
                        disabled={refreshing}
                        className="inline-flex items-center justify-center gap-2 rounded-lg border border-indigo-200 bg-white px-3 py-2 text-xs font-bold text-indigo-800 hover:bg-indigo-50 disabled:opacity-50"
                        data-testid="ga4-source-refresh-btn"
                    >
                        <ArrowsClockwise
                            size={16}
                            weight="bold"
                            className={refreshing ? "animate-spin" : ""}
                        />
                        {refreshing ? "جاري التحديث…" : "تحديث المصادر"}
                    </button>
                </div>
            </div>

            {loading ? (
                <div className="h-64 rounded-lg bg-white border border-slate-100 animate-pulse" />
            ) : error ? (
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 flex items-start gap-2">
                    <WarningCircle size={20} weight="fill" className="mt-0.5 shrink-0" />
                    <span>{error}</span>
                </div>
            ) : (
                <>
                    <div className="grid grid-cols-3 gap-3 mb-4">
                        <div className="rounded-lg border border-blue-100 bg-white p-3 text-center">
                            <div className="text-[11px] text-slate-500">الجلسات</div>
                            <div className="num text-xl font-extrabold text-blue-800">{formatInt(period.sessions)}</div>
                        </div>
                        <div className="rounded-lg border border-indigo-100 bg-white p-3 text-center">
                            <div className="text-[11px] text-slate-500">طلبات GA4</div>
                            <div className="num text-xl font-extrabold text-indigo-800">{formatInt(period.orders)}</div>
                        </div>
                        <div className="rounded-lg border border-emerald-100 bg-white p-3 text-center">
                            <div className="text-[11px] text-slate-500">مبيعات GA4</div>
                            <div className="num text-xl font-extrabold text-emerald-700">{formatMoney(period.purchase_revenue)} ر.س</div>
                        </div>
                    </div>
                    <GoogleAnalyticsTrafficSourcesContent data={data} periodKey={periodKey} />
                </>
            )}
        </section>
    );
}
