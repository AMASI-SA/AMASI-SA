import { useCallback, useEffect, useMemo, useState } from "react";
import {
    ArrowsClockwise,
    ChartBar,
    CursorClick,
    UsersThree,
    WarningCircle,
} from "@phosphor-icons/react";
import {
    Bar,
    BarChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import api from "../lib/api";

const REALTIME_PATH = "/integrations-v2/google_analytics_4/realtime-dashboard";

function formatObservedAt(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return parsed.toLocaleString("en-US", {
        dateStyle: "short",
        timeStyle: "short",
    });
}

function LoadingCard() {
    return (
        <div className="h-[360px] rounded-xl border border-border bg-white p-5 animate-pulse">
            <div className="h-6 w-48 bg-slate-100 rounded mb-5" />
            <div className="h-12 w-24 bg-slate-100 rounded mb-8" />
            <div className="space-y-3">
                {[1, 2, 3, 4, 5].map((item) => (
                    <div key={item} className="h-9 bg-slate-100 rounded" />
                ))}
            </div>
        </div>
    );
}

function EmptyRows({ text }) {
    return (
        <div className="h-48 flex items-center justify-center text-sm text-muted-foreground text-center px-5">
            {text}
        </div>
    );
}

export function GoogleAnalyticsRealtimeContent({ data }) {
    const topPages = Array.isArray(data?.top_pages) ? data.top_pages : [];
    const keyEvents = Array.isArray(data?.key_events) ? data.key_events : [];
    const minuteRows = Array.isArray(data?.active_users?.per_minute)
        ? data.active_users.per_minute
        : [];
    const maxViews = Math.max(1, ...topPages.map((item) => Number(item.views || 0)));

    const chartRows = useMemo(
        () => minuteRows.map((item) => ({
            ...item,
            label: Number(item.minutes_ago) === 0
                ? "الآن"
                : `-${Number(item.minutes_ago)}`,
        })),
        [minuteRows],
    );

    return (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4" data-testid="ga4-realtime-cards-grid">
            <article
                className="rounded-xl border border-blue-200 bg-white p-4 sm:p-5 min-h-[360px]"
                data-testid="ga4-top-pages-card"
            >
                <div className="flex items-center gap-3 mb-5">
                    <div className="w-10 h-10 rounded-lg bg-blue-50 text-blue-700 flex items-center justify-center">
                        <ChartBar size={22} weight="duotone" />
                    </div>
                    <div>
                        <h3 className="font-extrabold text-slate-900">الصفحات الأكثر مشاهدة</h3>
                        <p className="text-xs text-muted-foreground">حسب عنوان الصفحة — آخر 30 دقيقة</p>
                    </div>
                </div>

                {topPages.length ? (
                    <div className="space-y-3">
                        {topPages.slice(0, 8).map((item, index) => {
                            const views = Number(item.views || 0);
                            const width = Math.max(4, (views / maxViews) * 100);
                            return (
                                <div key={`${item.title}-${index}`} className="group">
                                    <div className="flex items-center justify-between gap-3 text-sm mb-1">
                                        <span
                                            className="truncate text-slate-700 group-hover:text-blue-700"
                                            title={item.title}
                                        >
                                            {item.title}
                                        </span>
                                        <strong className="num text-slate-900 tabular-nums">{views}</strong>
                                    </div>
                                    <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden">
                                        <div
                                            className="h-full rounded-full bg-blue-500"
                                            style={{ width: `${width}%` }}
                                        />
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                ) : (
                    <EmptyRows text="لا توجد مشاهدات مسجلة خلال آخر 30 دقيقة." />
                )}
            </article>

            <article
                className="rounded-xl border border-indigo-200 bg-white p-4 sm:p-5 min-h-[360px]"
                data-testid="ga4-active-users-card"
            >
                <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-lg bg-indigo-50 text-indigo-700 flex items-center justify-center">
                        <UsersThree size={22} weight="duotone" />
                    </div>
                    <div>
                        <h3 className="font-extrabold text-slate-900">المستخدمون النشطون الآن</h3>
                        <p className="text-xs text-muted-foreground">قياس لحظي من Google Analytics</p>
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-3 mb-4">
                    <div className="rounded-lg bg-indigo-50 border border-indigo-100 p-3">
                        <div className="text-xs text-indigo-700 mb-1">آخر 30 دقيقة</div>
                        <div className="num text-3xl font-extrabold text-indigo-950 tabular-nums">
                            {Number(data?.active_users?.last_30_minutes || 0)}
                        </div>
                    </div>
                    <div className="rounded-lg bg-violet-50 border border-violet-100 p-3">
                        <div className="text-xs text-violet-700 mb-1">آخر 5 دقائق</div>
                        <div className="num text-3xl font-extrabold text-violet-950 tabular-nums">
                            {Number(data?.active_users?.last_5_minutes || 0)}
                        </div>
                    </div>
                </div>

                <div className="h-48" dir="ltr">
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={chartRows} margin={{ top: 8, right: 0, left: -24, bottom: 0 }}>
                            <XAxis
                                dataKey="minutes_ago"
                                tick={{ fontSize: 10 }}
                                interval={4}
                                tickFormatter={(value) => Number(value) === 0 ? "الآن" : `-${value}`}
                                axisLine={false}
                                tickLine={false}
                            />
                            <YAxis
                                allowDecimals={false}
                                tick={{ fontSize: 10 }}
                                axisLine={false}
                                tickLine={false}
                            />
                            <Tooltip
                                formatter={(value) => [value, "مستخدم نشط"]}
                                labelFormatter={(value) => Number(value) === 0
                                    ? "الدقيقة الحالية"
                                    : `قبل ${value} دقيقة`}
                            />
                            <Bar dataKey="active_users" fill="#4f46e5" radius={[4, 4, 0, 0]} />
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            </article>

            <article
                className="rounded-xl border border-emerald-200 bg-white p-4 sm:p-5 min-h-[360px]"
                data-testid="ga4-key-events-card"
            >
                <div className="flex items-center gap-3 mb-5">
                    <div className="w-10 h-10 rounded-lg bg-emerald-50 text-emerald-700 flex items-center justify-center">
                        <CursorClick size={22} weight="duotone" />
                    </div>
                    <div>
                        <h3 className="font-extrabold text-slate-900">الأحداث المهمة</h3>
                        <p className="text-xs text-muted-foreground">حسب اسم الحدث — آخر 30 دقيقة</p>
                    </div>
                </div>

                {keyEvents.length ? (
                    <div className="space-y-3">
                        {keyEvents.slice(0, 10).map((item, index) => (
                            <div
                                key={`${item.event_name}-${index}`}
                                className="flex items-center justify-between gap-3 rounded-lg border border-emerald-100 bg-emerald-50/40 px-3 py-3"
                            >
                                <code className="truncate text-sm font-bold text-emerald-900" dir="ltr">
                                    {item.event_name}
                                </code>
                                <span className="num min-w-9 h-9 px-2 rounded-full bg-white border border-emerald-200 text-emerald-800 font-extrabold flex items-center justify-center tabular-nums">
                                    {Number(item.count || 0)}
                                </span>
                            </div>
                        ))}
                    </div>
                ) : (
                    <EmptyRows text="لم تُسجّل أحداث مهمة خلال آخر 30 دقيقة." />
                )}
            </article>
        </div>
    );
}

export default function GoogleAnalyticsRealtimeCards() {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState("");

    const load = useCallback(async ({ force = false, silent = false } = {}) => {
        if (!silent) setRefreshing(true);
        try {
            const response = await api.get(
                `${REALTIME_PATH}${force ? "?force=true" : ""}`,
            );
            setData(response.data);
            setError("");
        } catch (requestError) {
            const detail = requestError?.response?.data?.detail;
            setError(
                (typeof detail === "object" ? detail?.message : detail)
                || "تعذر قراءة بيانات Google Analytics اللحظية.",
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

    if (loading) {
        return (
            <section data-testid="ga4-realtime-section-loading">
                <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                    <LoadingCard />
                    <LoadingCard />
                    <LoadingCard />
                </div>
            </section>
        );
    }

    return (
        <section
            className="rounded-xl border-2 border-blue-100 bg-gradient-to-br from-blue-50/50 via-white to-emerald-50/40 p-4 sm:p-5"
            data-testid="ga4-realtime-section"
        >
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-4">
                <div>
                    <div className="flex items-center gap-2 flex-wrap">
                        <h2 className="text-lg sm:text-xl font-extrabold text-slate-900">
                            Google Analytics 4 — مباشر
                        </h2>
                        <span className="text-[10px] rounded-full border border-blue-200 bg-white px-2 py-1 text-blue-700 font-bold" dir="ltr">
                            Property {data?.property_id || "353865193"}
                        </span>
                    </div>
                    <p className="text-xs text-muted-foreground mt-1">
                        {data?.property_name || "اماسي - إحصاءات Google 4"}
                        <span className="mx-1.5">•</span>
                        آخر قراءة: {formatObservedAt(data?.observed_at)}
                        <span className="mx-1.5">•</span>
                        تحديث تلقائي كل دقيقة
                    </p>
                </div>
                <button
                    type="button"
                    onClick={() => load({ force: true })}
                    disabled={refreshing}
                    className="inline-flex items-center justify-center gap-2 rounded-lg border border-blue-200 bg-white px-3 py-2 text-xs font-bold text-blue-800 hover:bg-blue-50 disabled:opacity-50"
                    data-testid="ga4-realtime-refresh-btn"
                >
                    <ArrowsClockwise
                        size={16}
                        weight="bold"
                        className={refreshing ? "animate-spin" : ""}
                    />
                    {refreshing ? "جاري التحديث…" : "تحديث الآن"}
                </button>
            </div>

            {error ? (
                <div
                    className="rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 flex items-start gap-2"
                    data-testid="ga4-realtime-error"
                >
                    <WarningCircle size={20} weight="fill" className="mt-0.5 shrink-0" />
                    <span>{error}</span>
                </div>
            ) : (
                <GoogleAnalyticsRealtimeContent data={data} />
            )}
        </section>
    );
}
