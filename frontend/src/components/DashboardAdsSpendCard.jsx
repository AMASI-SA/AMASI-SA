import DashboardPanel from "./DashboardPanel";
import DashboardAdsAccountRow from "./DashboardAdsAccountRow";
import DashboardAdsPeriodsOverview from "./DashboardAdsPeriodsOverview";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    ArrowsClockwise,
    ChartLineUp,
    WarningCircle,
} from "@phosphor-icons/react";
import {
    CartesianGrid,
    Line,
    LineChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from "recharts";
import { addDaysISO, todaySA } from "../lib/dates";
import { buildDashboardAdsTableMetrics } from "../lib/dashboardAdsAccountResults";
import {
    DASHBOARD_ADS_PROVIDERS,
    dashboardKnownIncompleteAmount,
    getDashboardAdsSpend,
    googleKnownIncompleteAmount,
} from "../services/dashboardAdsSpend";
import { refreshSnapchatSpend, snapchatSpendReadbackMatches } from "../services/snapchatManualSpendRefresh";

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

function providerValue(data, provider, googlePeriodMatches = false) {
    const state = data?.providers?.[provider] || {};
    const total = data?.provider_totals_sar?.[provider];
    if (provider === "snapchat" && snapchatAmountIncomplete(state)) {
        return incompleteSnapchatValue(state);
    }
    const knownGoogle = provider === "google" && total === null && googlePeriodMatches
        ? googleKnownIncompleteAmount(state) : null;
    if (knownGoogle !== null) return `المتاح ${money(knownGoogle)} · غير مكتمل`;
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
function SnapchatAccountSpend({ account, ready }) {
    const available = ready && accountAvailable(account);
    const currency = typeof account.currency === "string" && /^[A-Z]{3}$/.test(account.currency) ? account.currency : null;
    const native = available && currency && hasKnownAmount(account.total_native) ? Number(account.total_native) : null;
    const provisional = available && (account.quality?.provisional === true || account.quality?.status === "provisional");
    const observed = typeof account.quality?.observed_at === "string" && Number.isFinite(Date.parse(account.quality.observed_at))
        ? account.quality.observed_at : null;
    const noActivity = provisional && Array.isArray(account.quality?.reason_codes)
        && account.quality.reason_codes.includes("snapchat_operational_no_activity_observed");
    const foreignNative = native !== null && currency !== "SAR";
    const hint = !available ? "غير مكتمل" : provisional ? "أولي" : "مكتمل";
    return <div title={hint}>
        <div className="font-bold text-slate-900">
            {!ready ? "بانتظار بيانات الفترة" : !available ? "بيانات غير مكتملة"
                : <bdi dir="ltr" className="whitespace-nowrap tabular-nums">{foreignNative
                    ? currency + " " + native.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })
                    : money(account.total_sar)}</bdi>}
        </div>
        {available && foreignNative && <bdi dir="ltr" className="block whitespace-nowrap text-[9px] font-medium tabular-nums text-slate-500">{money(account.total_sar)}</bdi>}
        <span className="sr-only">{hint}</span>
        {ready && observed && <time className="sr-only" dateTime={observed}>
            آخر رصد: {new Date(observed).toLocaleString("ar-SA", { timeZone: "Asia/Riyadh", hour: "2-digit", minute: "2-digit" })}
        </time>}
        {available && account.quality?.reason_codes?.includes("snapchat_manual_spend_observation_stale") && <p className="sr-only">
            هذه آخر قراءة محفوظة. اضغط تحديث لجلب صرف سناب الآن.
        </p>}
        {noActivity && <p className="sr-only" data-testid="dashboard-snapchat-account-no-activity-hint">
            لم يُرصد نشاط في هذا الحساب حتى آخر فحص. الأرقام أولية وقد تتأخر تقارير سناب.
        </p>}
    </div>;
}

function providerStatus(data, provider, singleDay, availableSeries) {
    const state = data?.providers?.[provider] || {};
    if (provider === "snapchat" && state.connected) {
        if (snapchatAmountIncomplete(state) || !hasAmount(data?.provider_totals_sar?.snapchat)) {
            return hasKnownAmount(state.known_total_sar)
                ? "بيانات الحسابات غير مكتملة" : "بانتظار اكتمال بيانات سناب";
        }
        if (singleDay && !availableSeries.snapchat) return "تفصيل سناب الساعي غير مكتمل";
    }
    if (singleDay && provider === "tiktok" && state.hourly_source === "make_daily_total_marker") {
        return "إجمالي Make عند آخر تحديث";
    }
    if (singleDay && state.hourly_available) return "بيانات ساعية أصلية";
    if (state.daily_available) return singleDay ? "إجمالي اليوم متاح" : "بيانات يومية أصلية";
    return state.connected ? "بانتظار البيانات" : "غير متصل";
}

function SallaMetric({ value, known, integer = false }) {
    const complete = hasKnownAmount(value);
    const amount = complete ? value : hasKnownAmount(known) ? known : null;
    if (amount === null) return <span className="text-slate-400">—</span>;
    return <>
        <bdi dir="ltr" className="block whitespace-nowrap tabular-nums">{Number(amount).toLocaleString("en-US", {
            minimumFractionDigits: integer ? 0 : 2,
            maximumFractionDigits: integer ? 0 : 2,
        })}</bdi>
        {!complete && <span className="block text-[8px] font-normal text-amber-700">المتاح</span>}
    </>;
}

function NetProfitMetric({ profit }) {
    const value = profit?.netProfit;
    if (profit?.complete !== true || typeof value !== "number" || !Number.isFinite(value)) {
        return <span className="text-slate-400">—</span>;
    }
    return <>
        <bdi dir="ltr" className={`block whitespace-nowrap font-bold tabular-nums ${value < 0 ? "text-rose-700" : "text-emerald-700"}`}>
            {value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
        </bdi>
        {profit.provisional === true && <span className="block text-[8px] font-normal text-amber-700">تقديري</span>}
    </>;
}

function SallaMetricCells({ stats = {} }) {
    return <>
        <td className="px-1 py-2 text-center"><SallaMetric value={stats.sales} known={stats.knownSales} /></td>
        <td className="px-1 py-2 text-center"><SallaMetric value={stats.orders} known={stats.knownOrders} integer /></td>
        <td className="px-1 py-2 text-center"><SallaMetric value={stats.cpa} /></td>
        <td className="px-1 py-2 text-center"><NetProfitMetric profit={stats.profit} /></td>
    </>;
}

function AdsAccountResultsTable({ data, accounts, accountMode, ready, singleDay, availableSeries,
    executiveSnapshot, salesFilters, fromDate, toDate, totalLabel }) {
    const [activeDetails, setActiveDetails] = useState(null);
    const metrics = useMemo(() => buildDashboardAdsTableMetrics({
        executiveSnapshot, spendSnapshot: data, fromDate, toDate,
        snapchatAccounts: accountMode ? accounts : null,
    }), [executiveSnapshot, data, fromDate, toDate, accountMode, accounts]);
    const tableProviders = ["snapchat", "google", "tiktok", "meta"]
        .filter((provider) => !accountMode || provider !== "snapchat");
    const filtered = ["payment_methods", "shipping_companies", "report_statuses"]
        .some((key) => Array.isArray(salesFilters?.[key])
            ? salesFilters[key].some((value) => String(value).trim())
            : Boolean(salesFilters?.[key]));
    const unrepresentedOrders = metrics.unrepresentedOrders ?? metrics.unassignedOrders;
    const overviewRecords = [
        ...(accountMode ? accounts.map((account, index) => ({
            key: `snapchat:${account.account_id}`, label: accountName(account),
            color: ACCOUNT_COLORS[index % ACCOUNT_COLORS.length], stats: metrics.accounts.get(account.account_id),
        })) : []),
        ...tableProviders.map((provider) => ({ key: provider,
            label: provider === "google" ? "جوجل" : PROVIDER_META[provider].label,
            color: PROVIDER_META[provider].stroke, stats: metrics.providers[provider] })),
        { key: "direct", label: "مباشر / بدون مصدر", color: "#94a3b8", stats: metrics.direct },
    ];
    return <div className="mt-2 min-w-0" data-testid="dashboard-ads-account-results">
        <div className="mb-2 text-[10px] font-extrabold text-slate-700">تفاصيل الحسابات</div>
        <div className="overflow-hidden rounded-xl border border-slate-200">
            <table dir="rtl" className="w-full table-fixed border-collapse text-[10px] leading-4 text-slate-800"
                data-testid="dashboard-ads-account-table">
                <caption className="sr-only">صرف الحسابات ومبيعات وطلبات سلة وصافي الأرباح للفترة المحددة</caption>
                <colgroup><col style={{ width: "22%" }} /><col style={{ width: "22%" }} />
                    <col style={{ width: "16%" }} /><col style={{ width: "10%" }} /><col style={{ width: "13%" }} />
                    <col style={{ width: "17%" }} /></colgroup>
                <thead className="bg-slate-50 text-[9px] font-bold text-slate-600"><tr>
                    <th scope="col" className="px-1.5 py-2 text-right">المنصة / الحساب</th>
                    <th scope="col" className="px-1 py-2 text-center">الصرف</th>
                    <th scope="col" className="px-1 py-2 text-center">مبيعات سلة<span className="block text-[8px] font-normal">ر.س</span></th>
                    <th scope="col" className="px-1 py-2 text-center">طلبات سلة</th>
                    <th scope="col" className="px-1 py-2 text-center">متوسط تكلفة الطلب<span className="block text-[8px] font-normal">ر.س</span></th>
                    <th scope="col" className="px-1 py-2 text-center">صافي الأرباح<span className="block text-[8px] font-normal">ر.س</span></th>
                </tr></thead>
                <tbody data-testid="dashboard-ads-provider-totals">
                    {accountMode && accounts.map((account, index) => {
                        const label = account.currency === "USD" ? "سناب الدولار" : account.currency === "SAR" ? "سناب السعودي" : "حساب سناب";
                        const name = typeof account.display_name === "string" ? account.display_name.trim() : "";
                        return <DashboardAdsAccountRow key={account.account_id} className="border-t border-slate-100"
                            label={accountName(account)} stats={metrics.accounts.get(account.account_id)} fromDate={fromDate} toDate={toDate}
                            active={activeDetails === `snapchat:${account.account_id}`} onActivate={() => setActiveDetails(`snapchat:${account.account_id}`)}
                            data-testid={"dashboard-snapchat-account-" + account.account_id}>
                            <th scope="row" className="px-1.5 py-2 text-right font-bold" title={accountName(account)}>
                                <div className="flex min-w-0 items-center gap-1"><span className="h-1.5 w-1.5 shrink-0 rounded-full"
                                    style={{ backgroundColor: ACCOUNT_COLORS[index % ACCOUNT_COLORS.length] }} />
                                    <span className="truncate">{label}</span></div>
                                {name && <span className="block truncate text-[8px] font-normal text-slate-500">{name}</span>}
                            </th>
                            <td className="px-1 py-2 text-center"><SnapchatAccountSpend account={account} ready={ready} /></td>
                            <SallaMetricCells stats={metrics.accounts.get(account.account_id)} />
                        </DashboardAdsAccountRow>;
                    })}
                    {tableProviders.map((provider) => {
                        const meta = PROVIDER_META[provider];
                        const state = data?.providers?.[provider] || {};
                        const status = providerStatus(data, provider, singleDay, availableSeries);
                        const noActivity = provider === "snapchat" && state.amount_available === true
                            && state.provisional === true && hasAmount(data?.provider_totals_sar?.snapchat)
                            && state.reason_codes?.includes("snapchat_operational_no_activity_observed");
                        return <DashboardAdsAccountRow key={provider} className="border-t border-slate-100"
                            label={meta.label} stats={metrics.providers[provider]} fromDate={fromDate} toDate={toDate}
                            active={activeDetails === provider} onActivate={() => setActiveDetails(provider)}
                            data-testid={`dashboard-ads-provider-${provider}`}>
                            <th scope="row" className="px-1.5 py-2 text-right font-bold" title={status}>
                                <div className="flex min-w-0 items-center gap-1"><span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: meta.stroke }} />
                                    <span className="truncate">{provider === "google" ? "جوجل" : meta.label}</span></div>
                                <span className="sr-only">{status}</span>
                                {noActivity && <span className="sr-only" data-testid="dashboard-snapchat-no-activity-hint">لم يُرصد نشاط في أحد الحسابات حتى آخر فحص. الأرقام أولية وقد تتأخر تقارير سناب.</span>}
                            </th>
                            <td className="px-1 py-2 text-center font-bold" title={status}>
                                <bdi dir="ltr">{providerValue(data, provider, Boolean(fromDate) && ready)}</bdi>
                            </td>
                            <SallaMetricCells stats={metrics.providers[provider]} />
                        </DashboardAdsAccountRow>;
                    })}
                    <DashboardAdsAccountRow className="border-t border-slate-100"
                        label="مباشر / بدون مصدر" stats={metrics.direct} fromDate={fromDate} toDate={toDate}
                        active={activeDetails === "direct"} onActivate={() => setActiveDetails("direct")}
                        data-testid="dashboard-ads-provider-direct">
                        <th scope="row" className="px-1.5 py-2 text-right font-bold">
                            <div className="flex min-w-0 items-center gap-1">
                                <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-400" />
                                <span>مباشر / بدون مصدر</span>
                            </div>
                        </th>
                        <td className="px-1 py-2 text-center font-medium text-slate-400">—</td>
                        <SallaMetricCells stats={metrics.direct} />
                    </DashboardAdsAccountRow>
                </tbody>
                <tfoot className="border-t border-yellow-200 bg-yellow-50 font-bold"><DashboardAdsPeriodsOverview
                    records={overviewRecords} total={metrics.total} fromDate={fromDate} toDate={toDate}
                    contextKey={JSON.stringify(salesFilters || {})}
                    active={activeDetails === "overview"} onActivate={() => setActiveDetails("overview")}
                    data-testid="dashboard-ads-account-total">
                    <th scope="row" className="px-1.5 py-2 text-right">الإجمالي</th>
                    <td className="px-1 py-2 text-center" data-testid="dashboard-ads-spend-total"><bdi dir="ltr">{totalLabel}</bdi></td>
                    <SallaMetricCells stats={metrics.total} />
                </DashboardAdsPeriodsOverview></tfoot>
            </table>
        </div>
        <p className="mt-2 text-[9px] leading-4 text-slate-500">متوسط تكلفة الطلب = الصرف بالريال ÷ طلبات سلة.</p>
        <p className="text-[9px] leading-4 text-slate-500">أشّر على أي صف لتفاصيله، أو على الإجمالي لمقارنة المنصات صباحًا ومساءً. اضغط لتثبيت التفاصيل.</p>
        {filtered && <p className="text-[9px] leading-4 text-slate-500">طلبات ومبيعات سلة حسب الفلاتر؛ الصرف إجمالي الفترة.</p>}
        {unrepresentedOrders > 0 && <p className="text-[9px] leading-4 text-amber-700" data-testid="dashboard-ads-unassigned-orders">
            {unrepresentedOrders.toLocaleString("en-US")} طلبات ضمن الإجمالي لم يُحدد لها حساب إعلاني معروض؛ تفاصيل الحسابات تعرض المتاح.
        </p>}
        {metrics.pending && <p className="text-[9px] leading-4 text-slate-500" data-testid="dashboard-ads-results-pending">بانتظار بيانات سلة المطابقة للفترة.</p>}
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
    refreshStatus = "",
    collapsible = false,
    executiveSnapshot,
    salesFilters,
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
    const knownTotal = dashboardKnownIncompleteAmount(data, { fromDate, toDate });
    const totalLabel = hasAmount(data?.total_sar) && !totalIncomplete
        ? `${money(data.total_sar)}${totalProvisional ? " · أولي" : ""}`
        : knownTotal !== null
            ? `غير مكتمل، المتاح ${money(knownTotal)}`
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
            aria-label={selectedPeriodLabel(fromDate, toDate)}
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
                    className="inline-flex shrink-0 items-center gap-1 rounded-full border border-black/10 bg-white/80 px-2.5 py-1 text-[10px] font-extrabold transition hover:bg-white disabled:cursor-wait disabled:opacity-50"
                    aria-label="تحديث صرف سناب فقط"
                    title="جلب صرف سناب فقط للفترة المحددة"
                    data-testid="dashboard-ads-spend-refresh"
                >
                    <ArrowsClockwise
                        size={14}
                        weight="bold"
                        className={refreshing ? "animate-spin" : ""}
                    />
                    تحديث
                </button>
            </div>

            <div className="p-3 sm:p-4">
                {refreshStatus && <p role="status" className="mb-3 text-xs font-bold text-amber-800" data-testid="dashboard-ads-manual-refresh-status">{refreshStatus}</p>}
                {accountMode && accounts.length === 0 && <p className="mb-2 text-[10px] text-slate-500" data-testid="dashboard-snapchat-accounts-empty">لا توجد حسابات سناب مفعّلة لهذه الفترة</p>}

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
                    <div className="h-[320px] animate-pulse rounded-xl bg-slate-100" data-testid="dashboard-ads-spend-loading" />
                ) : points.length && hasAnySeries ? (
                    <div
                        className="h-[320px] min-w-0"
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
                                {accountLines.map(({ account, color }) => <Line key={account.account_id}
                                    type="monotone" dataKey={(point) => point.snapchat_accounts?.get(account.account_id) ?? null}
                                    name={accountName(account)} stroke={color} strokeWidth={2.5} connectNulls={false} dot={{ r: 3, strokeWidth: 1 }} />)}
                                {visibleProviders.map((provider) => (
                                    availableSeries[provider] ? (
                                        <Line
                                            key={provider}
                                            type="monotone"
                                            dataKey={provider}
                                            name={provider === "tiktok" && hasTikTokMakeMarker
                                                ? "تيك توك · إجمالي اليوم عند آخر تحديث"
                                                : PROVIDER_META[provider].label}
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
                        className="flex h-[320px] items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50 px-4 text-center text-sm font-bold text-slate-500"
                        data-testid={singleDay
                            ? "dashboard-ads-spend-hourly-empty"
                            : "dashboard-ads-spend-daily-empty"}
                    >
                        لا توجد صرفيات إعلانية أصلية ضمن التاريخ المحدد حتى الآن.
                    </div>
                )}
                <AdsAccountResultsTable data={data} accounts={accounts} accountMode={accountMode}
                    ready={accountRangeReady} singleDay={singleDay} availableSeries={availableSeries}
                    executiveSnapshot={executiveSnapshot} salesFilters={salesFilters}
                    fromDate={fromDate} toDate={toDate} totalLabel={totalLabel} />
            </div>
        </DashboardPanel>
    );
}

export default function DashboardAdsSpendCard({ fromDate, toDate, collapsible = false,
    snapshot, onSnapshot, beginSnapshotRead, executiveSnapshot, salesFilters }) {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState("");
    const requestId = useRef(0);
    const [manualRefreshing, setManualRefreshing] = useState(false);
    const [refreshStatus, setRefreshStatus] = useState("");
    const manualController = useRef(null);
    const manualSequence = useRef(0);
    const snapshotCallbacks = useRef({ onSnapshot, beginSnapshotRead });
    snapshotCallbacks.current = { onSnapshot, beginSnapshotRead };

    const load = useCallback(async ({ silent = false, refresh = !silent } = {}) => {
        const currentRequest = requestId.current + 1;
        requestId.current = currentRequest;
        const snapshotVersion = snapshotCallbacks.current.beginSnapshotRead?.();
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
            snapshotCallbacks.current.onSnapshot?.(next, snapshotVersion);
            setError("");
            return next;
        } catch (requestError) {
            if (requestId.current !== currentRequest) return;
            const detail = requestError?.response?.data?.detail;
            setError(
                (typeof detail === "object" ? detail?.message : detail)
                || requestError?.message
                || "تعذر قراءة صرفيات منصات الإعلانات.",
            );
            return false;
        } finally {
            if (requestId.current === currentRequest) {
                setLoading(false);
                if (!silent) setRefreshing(false);
            }
        }
    }, [data, fromDate, toDate]);

    const refreshManually = useCallback(async () => {
        if (manualController.current) return;
        const controller = new AbortController();
        manualController.current = controller;
        const sequence = ++manualSequence.current;
        setManualRefreshing(true);
        setError("");
        setRefreshStatus("جارٍ جلب صرف سناب فقط…");
        try {
            const job = await refreshSnapchatSpend({ dateFrom: fromDate,
                dateTo: toDate || fromDate, signal: controller.signal });
            if (sequence !== manualSequence.current) return;
            const loaded = await load({ refresh: false });
            if (sequence !== manualSequence.current) return;
            if (!loaded || !snapchatSpendReadbackMatches(job, loaded)) {
                setRefreshStatus("");
                setError("تم جلب صرف سناب، لكن لم يتأكد ظهور القراءة الجديدة في البطاقة. أعد المحاولة.");
            } else {
                setRefreshStatus(job.status === "partial"
                    ? `تم تحديث صرف سناب لـ ${job.refreshed_count} حساب. تعذر تحديث ${job.failed_count} حساب؛ تظهر آخر بياناته المحفوظة إن توفرت.`
                    : "اكتمل جلب صرف سناب.");
            }
        } catch (requestError) {
            if (controller.signal.aborted || sequence !== manualSequence.current) return;
            const detail = requestError?.response?.data?.detail;
            setError((typeof detail === "object" ? detail?.message : detail)
                || requestError?.message || "تعذر جلب صرف سناب الآن.");
            setRefreshStatus("");
        } finally {
            if (manualController.current === controller) manualController.current = null;
            if (sequence === manualSequence.current) setManualRefreshing(false);
        }
    }, [fromDate, toDate, load]);

    useEffect(() => {
        manualController.current?.abort();
        manualSequence.current += 1;
        setManualRefreshing(false);
        setRefreshStatus("");
        load({ refresh: false });
        return () => {
            requestId.current += 1;
            manualSequence.current += 1;
            manualController.current?.abort();
            manualController.current = null;
        };
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
            data={snapshot === undefined ? data : snapshot}
            collapsible={collapsible}
            fromDate={fromDate}
            toDate={toDate}
            loading={loading}
            refreshing={refreshing || manualRefreshing}
            error={error}
            onRefresh={refreshManually}
            refreshStatus={refreshStatus}
            executiveSnapshot={executiveSnapshot}
            salesFilters={salesFilters}
        />
    );
}

export { selectedPeriodLabel };
