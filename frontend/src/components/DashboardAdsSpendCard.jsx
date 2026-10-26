import DashboardPanel from "./DashboardPanel";
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

function hasAmount(value) {
    return value !== null && value !== undefined && value !== ""
        && Number.isFinite(Number(value)) && Number(value) >= 0;
}

function hasKnownAmount(value) {
    return (typeof value === "number" || typeof value === "string" && value.trim() !== "")
        && hasAmount(value);
}

function incompleteSnapchatValue(state) {
    return hasKnownAmount(state.known_total_sar)
        ? `المتاح ${money(state.known_total_sar)} · غير مكتمل`
        : "بيانات غير مكتملة";
}

function snapchatAmountIncomplete(state) {
    return state.connected === true && (
        state.amount_available === false
        || state.data_state === "unknown_incomplete"
        || state.data_quality === "incomplete"
    );
}

function providerValue(data, provider) {
    const state = data?.providers?.[provider] || {};
    const total = data?.provider_totals_sar?.[provider];
    if (provider === "snapchat" && snapchatAmountIncomplete(state)) {
        return incompleteSnapchatValue(state);
    }
    if (hasAmount(total)) {
        return `${money(total)}${provider === "snapchat" && state.provisional === true ? " · أولي" : ""}`;
    }
    if (!state.connected) return "غير مربوط";
    if (provider === "snapchat") return incompleteSnapchatValue(state);
    return "لا بيانات";
}


const ACCOUNT_COLORS = ["#eab308", "#0f766e", "#c2410c", "#7c3aed"];
function displayAccounts(rows) {
    if (!Array.isArray(rows)) return [];
    const ids = new Set();
    return rows.filter((row) => {
        if (typeof row?.account_id !== "string" || !row.account_id.trim() || ids.has(row.account_id)) return false;
        ids.add(row.account_id);
        return true;
    });
}
function accountAvailable(row) {
    return row?.quality?.amount_available === true && row.quality.connected !== false
        && row.quality.status !== "incomplete" && row.quality.data_state !== "unknown_incomplete"
        && row.projection_timezone === "Asia/Riyadh" && hasKnownAmount(row.total_sar);
}
function accountName(row) {
    const label = row.currency === "USD" ? "سناب الدولار" : row.currency === "SAR" ? "سناب السعودي" : "حساب سناب";
    const name = typeof row.display_name === "string" ? row.display_name.trim() : "";
    return name ? label + " · " + name : label;
}
function accountChartPoints(data, singleDay, accounts, from, to, ready) {
    const points = new Map();
    chartPoints(data, singleDay).forEach((point) => {
        const key = singleDay ? point.hour_index : point.date;
        if (singleDay ? !Number.isInteger(key) || key < 0 || key > 23 : typeof key !== "string") return;
        points.set(key, { ...point, snapchat_accounts: new Map() });
    });
    accounts.forEach((account) => {
        const source = singleDay ? account.hourly_sar?.[from] : Object.entries(account.daily_sar || {}).map(([date, spend_sar]) =>
            ({ date, spend_sar, status: account.daily_state?.[date] }));
        if (!Array.isArray(source)) return;
        source.forEach((point) => {
            const key = singleDay ? point.hour_index : point.date;
            if (singleDay ? !Number.isInteger(key) || key < 0 || key > 23 || point.date !== from
                : typeof key !== "string" || key < from || key > to) return;
            const current = points.get(key) || { date: singleDay ? from : key,
                ...(singleDay ? { hour_index: key, hour: String(key).padStart(2, "0") + ":00" } : {}),
                snapchat_accounts: new Map() };
            const knownState = ["provisional", "confirmed", "confirmed_data", "confirmed_zero", "reconciled",
                "provisional_data", "provisional_zero"].includes(point.status);
            current.snapchat_accounts.set(account.account_id, ready && accountAvailable(account)
                && knownState && hasKnownAmount(point.spend_sar) ? Number(point.spend_sar) : null);
            points.set(key, current);
        });
    });
    return [...points.values()].sort((a, b) => singleDay ? a.hour_index - b.hour_index : a.date.localeCompare(b.date));
}
function SnapchatAccountCard({ account, ready, color }) {
    const available = ready && accountAvailable(account);
    const currency = typeof account.currency === "string" && /^[A-Z]{3}$/.test(account.currency) ? account.currency : null;
    const native = available && currency && hasKnownAmount(account.total_native) ? Number(account.total_native) : null;
    const provisional = available && (account.quality?.provisional === true || account.quality?.status === "provisional");
    const observed = typeof account.quality?.observed_at === "string" && Number.isFinite(Date.parse(account.quality.observed_at))
        ? account.quality.observed_at : null;
    const noActivity = provisional && Array.isArray(account.quality?.reason_codes)
        && account.quality.reason_codes.includes("snapchat_operational_no_activity_observed");
    return <div className="rounded-xl border border-slate-200 bg-slate-50 px-2.5 py-2"
        data-testid={"dashboard-snapchat-account-" + account.account_id}>
        <div className="flex items-center gap-1.5 text-[10px] font-bold text-slate-600">
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: color }} />
            <span>{accountName(account)}</span>
        </div>
        <div className="mt-1 text-[11px] font-extrabold text-slate-900">
            {!ready ? "بانتظار بيانات الفترة" : !available ? "بيانات غير مكتملة"
                : native !== null ? native.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " " + currency
                : money(account.total_sar)}
        </div>
        {available && native !== null && <div className="mt-0.5 text-[10px] font-bold text-slate-600">{money(account.total_sar)} بالريال السعودي</div>}
        <div className="mt-0.5 text-[9px] font-semibold text-slate-500">{!available ? "غير مكتمل" : provisional ? "أولي" : "مكتمل"}</div>
        {ready && observed && <time className="mt-1 block text-[9px] text-slate-500" dateTime={observed}>
            آخر رصد: {new Date(observed).toLocaleString("ar-SA", { timeZone: "Asia/Riyadh", hour: "2-digit", minute: "2-digit" })}
        </time>}
        {noActivity && <p className="mt-1 text-[9px] leading-4 text-amber-700" data-testid="dashboard-snapchat-account-no-activity-hint">
            لم يُرصد نشاط في هذا الحساب حتى آخر فحص. الأرقام أولية وقد تتأخر تقارير سناب.
        </p>}
    </div>;
}

export function DashboardAdsSpendCardContent({
    data,
    fromDate,
    toDate,
    loading = false,
    refreshing = false,
    error = "",
    onRefresh,
    collapsible = false,
}) {
    const singleDay = isSingleDay(fromDate, toDate);
    const snapchatState = data?.providers?.snapchat || {};
    const accountMode = Array.isArray(snapchatState.accounts);
    const accounts = useMemo(() => displayAccounts(snapchatState.accounts), [snapchatState.accounts]);
    const accountRangeReady = data?.date_from === fromDate && data?.date_to === (toDate || fromDate);
    const points = useMemo(() => accountMode
        ? accountChartPoints(data, singleDay, accounts, fromDate, toDate || fromDate, accountRangeReady)
        : chartPoints(data, singleDay), [data, singleDay, accountMode, accounts, fromDate, toDate, accountRangeReady]);
    const totalIncomplete = data?.spend_quality?.amount_available === false
        || data?.spend_quality?.status === "incomplete"
        || snapchatAmountIncomplete(snapchatState);
    const totalProvisional = data?.spend_quality?.provisional === true
        || snapchatState.provisional === true;
    const totalLabel = hasAmount(data?.total_sar) && !totalIncomplete
        ? `${money(data.total_sar)}${totalProvisional ? " · أولي" : ""}`
        : hasKnownAmount(data?.known_total_sar)
            ? `غير مكتمل، المتاح ${money(data.known_total_sar)}`
        : data ? "بيانات غير مكتملة" : "بانتظار البيانات";
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
    const accountLines = accounts.map((account, index) => ({ account, color: ACCOUNT_COLORS[index % ACCOUNT_COLORS.length] }))
        .filter(({ account }) => points.some((point) => hasKnownAmount(point.snapchat_accounts?.get(account.account_id))));
    const visibleProviders = DASHBOARD_ADS_PROVIDERS.filter((provider) => !accountMode || provider !== "snapchat");
    const hasAnySeries = visibleProviders.some((provider) => availableSeries[provider]) || accountLines.length > 0;
    const hasTikTokMakeMarker = singleDay
        && data?.providers?.tiktok?.hourly_source === "make_daily_total_marker";

    return (
        <DashboardPanel title={collapsible ? "صرفيات منصات الإعلانات" : undefined}
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
                        <div className="rounded-full border border-yellow-200 bg-yellow-50 px-3 py-1 text-[11px] font-extrabold text-yellow-900" data-testid="dashboard-ads-spend-total">
                            إجمالي المنصات: {totalLabel}
                        </div>
                    </div>
                </div>

                <div className="mb-3 grid grid-cols-2 gap-2 sm:grid-cols-4" data-testid="dashboard-ads-provider-totals">
                    {accountMode && accounts.map((account, index) => <SnapchatAccountCard key={account.account_id}
                        account={account} ready={accountRangeReady} color={ACCOUNT_COLORS[index % ACCOUNT_COLORS.length]} />)}
                    {visibleProviders.map((provider) => {
                        const meta = PROVIDER_META[provider];
                        const state = data?.providers?.[provider] || {};
                        const operationalNoActivityObserved = provider === "snapchat"
                            && state.amount_available === true && state.provisional === true
                            && hasAmount(data?.provider_totals_sar?.snapchat)
                            && Array.isArray(state.reason_codes)
                            && state.reason_codes.includes("snapchat_operational_no_activity_observed");
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
                                    {provider === "snapchat" && state.connected && (
                                        snapchatAmountIncomplete(state)
                                        || !hasAmount(data?.provider_totals_sar?.snapchat)
                                    )
                                        ? hasKnownAmount(state.known_total_sar)
                                            ? "بيانات الحسابات غير مكتملة"
                                            : "بانتظار اكتمال بيانات سناب"
                                        : provider === "snapchat" && state.connected && singleDay && !availableSeries.snapchat
                                            ? "تفصيل سناب الساعي غير مكتمل"
                                        : singleDay
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
                                {operationalNoActivityObserved && <p
                                    className="mt-1 text-[9px] font-semibold leading-4 text-amber-700"
                                    data-testid="dashboard-snapchat-no-activity-hint"
                                >لم يُرصد نشاط في أحد الحسابات حتى آخر فحص. الأرقام أولية وقد تتأخر تقارير سناب.</p>}
                            </div>
                        );
                    })}
                </div>

                {accountMode && accounts.length === 0 && <p className="mb-2 text-[10px] text-slate-500" data-testid="dashboard-snapchat-accounts-empty">لا توجد حسابات سناب مفعّلة لهذه الفترة</p>}
                {accountMode && <p className="mb-2 text-[10px] font-semibold text-slate-500">جميع قيم الرسم بالريال السعودي، ولكل حساب سناب منحنى مستقل.</p>}
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
                                {accountLines.map(({ account, color }) => <Line key={account.account_id}
                                    type="monotone" dataKey={(point) => point.snapchat_accounts?.get(account.account_id) ?? null}
                                    name={accountName(account)} stroke={color} strokeWidth={2.5} connectNulls={false} dot={{ r: 3, strokeWidth: 1 }} />)}
                                {visibleProviders.map((provider) => (
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
        </DashboardPanel>
    );
}

export default function DashboardAdsSpendCard({ fromDate, toDate, collapsible = false }) {
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
            collapsible={collapsible}
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
