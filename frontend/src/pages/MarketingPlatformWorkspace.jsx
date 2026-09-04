import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
    ArrowClockwise,
    ArrowRight,
    ChartLineUp,
    CheckCircle,
    Database,
    Gear,
    MagnifyingGlass,
    Megaphone,
    Robot,
    ShieldCheck,
    WarningCircle,
} from "@phosphor-icons/react";

import ArabicDateRangePicker from "../components/marketing/ArabicDateRangePicker";
import AdAccountDecisionHistory from "../components/marketing/AdAccountDecisionHistory";
import AdsEntityLevelWorkspace from "../components/marketing/AdsEntityLevelWorkspace";
import AdsPerformanceExplorer from "../components/marketing/AdsPerformanceExplorer";
import { mergePaginatedRows } from "../components/marketing/infiniteScrollPagination";
import { isValidISODate } from "../components/DateInput";
import { todaySA } from "../lib/dates";
import {
    getMarketingPerformance,
    isMarketingPerformanceProvider,
    MARKETING_PLATFORM_CONFIG,
    MARKETING_PLATFORMS,
} from "../services/marketingPerformance";
import {
    getSnapchatAdSquadPerformance,
    SNAPCHAT_ENTITY_PAGE_SIZE,
} from "../services/snapchatAdSquadPerformance";
import {
    CAMPAIGN_RESULTS_SOURCE_EVENT,
    campaignResultsSource,
} from "../marketingCampaignResultSource";

export const MARKETING_PLATFORM_PROVIDERS = MARKETING_PLATFORMS;
export { isMarketingPerformanceProvider as isMarketingPlatformProvider };

export function isSnapchatPlatformSnapshotPending(platform, data) {
    return platform === "snapchat"
        && data?.result_source === "platform"
        && data?.source?.platform_total_snapshot_ready === false;
}

const TABS = [
    { id: "overview", label: "نظرة عامة", Icon: ChartLineUp },
    { id: "campaigns", label: "الحملات", Icon: Megaphone },
    { id: "accounts", label: "الحسابات", Icon: Database },
    { id: "ai", label: "جاهزية الذكاء الاصطناعي", Icon: Robot },
];

const CONNECTION_LABELS = {
    connected: "متصل",
    data_available: "بيانات متاحة",
    needs_reauth: "يحتاج إعادة ربط",
    not_connected: "غير متصل",
    not_configured: "غير مهيأ",
    planned: "مستقبلي",
    error: "خطأ",
    unknown: "غير محسوم",
};

const CAMPAIGN_DIAGNOSTIC_LABELS = {
    inactive: "الحملة غير نشطة",
    deleted: "الحملة محذوفة أو مؤرشفة لدى المزود",
    outside_account: "الحملة تتبع حسابًا إعلانيًا آخر",
    outside_date_range: "لا توجد لها بيانات Snapchat داخل الفترة",
    filtered: "الحملة خارج الفلتر الحالي",
    pagination_truncated: "الحملة موجودة في صفحة أخرى",
    provider_missing: "الحملة غير موجودة في كتالوج Snapchat المتاح",
    source_failed: "تعذر التحقق من مصدر Snapchat",
};

const TAB_IDS = new Set(TABS.map((tab) => tab.id));

function workspaceUrlState() {
    if (typeof window === "undefined") {
        return { tab: "overview", accountId: null, historyPage: 1 };
    }
    const params = new URLSearchParams(window.location.search);
    const tab = TAB_IDS.has(params.get("tab")) ? params.get("tab") : "overview";
    const historyPage = Math.max(1, Math.trunc(Number(params.get("history_page")) || 1));
    return {
        tab,
        accountId: params.get("account")?.trim() || null,
        historyPage,
    };
}

function money(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "غير متاح";
    }
    return `${Number(value).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ر.س`;
}

function numeric(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "غير متاح";
    }
    return Number(value).toLocaleString("en-US");
}

function ratio(value, suffix = "") {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "غير متاح";
    }
    return `${Number(value).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })}${suffix}`;
}

function dateTime(value) {
    if (!value) return "غير متاح";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString("ar-SA", {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
    });
}

function ReadinessItem({ label, ready, detail }) {
    return (
        <div className={`rounded-2xl border p-4 ${ready ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}>
            <div className="flex items-center gap-2 font-black text-slate-900">
                {ready
                    ? <CheckCircle size={20} weight="fill" className="text-emerald-600" />
                    : <WarningCircle size={20} weight="fill" className="text-amber-600" />}
                {label}
            </div>
            <p className="mt-2 text-xs font-semibold leading-5 text-slate-600">{detail}</p>
        </div>
    );
}

function LoadingState() {
    return (
        <div className="space-y-4" data-testid="marketing-platform-loading">
            <div className="h-44 animate-pulse rounded-3xl bg-slate-200" />
            <div className="h-[34rem] animate-pulse rounded-3xl bg-slate-100" />
            <div className="h-96 animate-pulse rounded-3xl bg-slate-100" />
        </div>
    );
}

function InsightPanel({ insights = [] }) {
    return (
        <section className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="font-black text-slate-900">ملاحظات التحليل</h2>
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
                {insights.length ? insights.map((item) => (
                    <article
                        key={`${item.code}-${item.campaign_id || "all"}`}
                        className={`rounded-xl border p-3 ${item.severity === "warning" ? "border-amber-200 bg-amber-50" : "border-blue-100 bg-blue-50"}`}
                    >
                        <div className="font-black text-slate-900">{item.title}</div>
                        <p className="mt-1 text-xs font-semibold leading-5 text-slate-600">{item.detail}</p>
                    </article>
                )) : (
                    <div className="rounded-xl bg-slate-50 p-5 text-center text-sm font-bold text-slate-500 lg:col-span-2">
                        لا توجد ملاحظات مؤكدة ضمن الفترة.
                    </div>
                )}
            </div>
        </section>
    );
}

function AccountSummaries({
    accounts = [],
    platform = "snapchat",
    decisionSummaries = [],
    selectedAccountId,
    onSelect,
    decisionHistoryEnabled = false,
}) {
    const snapchat = platform === "snapchat";
    const summaries = new Map(decisionSummaries.map((summary) => [summary.account_id, summary]));
    return (
        <section className="grid gap-4 lg:grid-cols-2" data-testid="marketing-account-summaries">
            {accounts.map((account) => {
                const decisionSummary = summaries.get(account.account_id);
                const selected = decisionHistoryEnabled
                    && selectedAccountId === account.account_id;
                const Card = decisionHistoryEnabled ? "button" : "article";
                return (
                <Card
                    key={account.account_id}
                    {...(decisionHistoryEnabled ? {
                        type: "button",
                        onClick: () => onSelect?.(account),
                        "aria-pressed": selected,
                    } : {})}
                    className={`rounded-2xl border bg-white p-5 text-right shadow-sm ${decisionHistoryEnabled ? "transition hover:-translate-y-0.5 hover:shadow-md" : ""} ${selected ? "border-violet-400 ring-2 ring-violet-100" : "border-slate-200"}`}
                    data-testid={`marketing-account-${account.account_id}`}
                >
                    <div className="flex items-start justify-between gap-3">
                        <div>
                            <h2 className="font-black text-slate-900">{account.account_name}</h2>
                            <div className="mt-1 font-mono text-xs text-slate-400">{account.account_id}</div>
                        </div>
                        <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-black text-emerald-700">
                            {account.currency || "عملة غير معروفة"}
                        </span>
                    </div>
                    <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
                        <div className="rounded-xl bg-slate-50 p-3">
                            <div className="text-[10px] font-black text-slate-500">{snapchat ? "صرف Snapchat" : "الصرف"}</div>
                            <div className="mt-1 font-mono font-black">{money(snapchat ? account.snapchat_spend_sar : account.spend_sar)}</div>
                        </div>
                        <div className="rounded-xl bg-slate-50 p-3">
                            <div className="text-[10px] font-black text-slate-500">{snapchat ? "طلبات سلة المطابقة" : "الطلبات"}</div>
                            <div className="mt-1 font-mono font-black">{numeric(snapchat ? account.salla_matched_orders : account.orders)}</div>
                        </div>
                        <div className="rounded-xl bg-slate-50 p-3">
                            <div className="text-[10px] font-black text-slate-500">{snapchat ? "مبيعات سلة" : "المبيعات"}</div>
                            <div className="mt-1 font-mono font-black">{money(snapchat ? account.salla_sales_sar : account.sales_sar)}</div>
                        </div>
                        <div className="rounded-xl bg-slate-50 p-3">
                            <div className="text-[10px] font-black text-slate-500">{snapchat ? "ROAS سلة" : "ROAS"}</div>
                            <div className="mt-1 font-mono font-black">{ratio(snapchat ? account.salla_roas : account.roas, "×")}</div>
                        </div>
                    </div>
                    {decisionHistoryEnabled && (
                        <div className="mt-3 flex items-center justify-between gap-3 border-t border-slate-100 pt-3 text-xs font-bold">
                            <span className="text-violet-700">عرض آخر 5 تعديلات ونتائجها</span>
                            <span className="font-mono text-slate-400">
                                آخر {(decisionSummary?.recent_decisions?.length || 0).toLocaleString("en-US")} موثق
                            </span>
                        </div>
                    )}
                </Card>
                );
            })}
            {!accounts.length && (
                <div className="rounded-2xl border border-dashed border-slate-200 bg-white p-12 text-center font-bold text-slate-500 lg:col-span-2">
                    لا توجد بيانات أداء موزعة على الحسابات.
                </div>
            )}
        </section>
    );
}

export default function MarketingPlatformWorkspace({ provider }) {
    const navigate = useNavigate();
    const platform = isMarketingPerformanceProvider(provider) ? provider : "snapchat";
    const config = MARKETING_PLATFORM_CONFIG[platform];
    const today = todaySA();
    const [dateFrom, setDateFrom] = useState(today);
    const [dateTo, setDateTo] = useState(today);
    const [appliedRange, setAppliedRange] = useState({ dateFrom: today, dateTo: today });
    const [query, setQuery] = useState("");
    const [appliedQuery, setAppliedQuery] = useState("");
    const [page, setPage] = useState(1);
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [error, setError] = useState("");
    const initialUrlState = useRef(workspaceUrlState());
    const [activeTab, setActiveTab] = useState(initialUrlState.current.tab);
    const [selectedHistoryAccountId, setSelectedHistoryAccountId] = useState(initialUrlState.current.accountId);
    const [historyPage, setHistoryPage] = useState(initialUrlState.current.historyPage);
    const [decisionAccountSummaries, setDecisionAccountSummaries] = useState([]);
    const [entityLevel, setEntityLevel] = useState("campaigns");
    const [activeCampaignsOnly, setActiveCampaignsOnly] = useState(true);
    const [actionReportTime, setActionReportTime] = useState("conversion");
    const [resultSource, setResultSource] = useState(
        () => campaignResultsSource(platform),
    );
    const [resultSourceRevision, setResultSourceRevision] = useState(0);
    const [adSquadSort, setAdSquadSort] = useState("orders");
    const [adSquadPage, setAdSquadPage] = useState(1);
    const [adSquadReport, setAdSquadReport] = useState(null);
    const [adSquadLoading, setAdSquadLoading] = useState(false);
    const [adSquadError, setAdSquadError] = useState("");
    const [selectedCampaign, setSelectedCampaign] = useState(null);
    const [selectedAdSquad, setSelectedAdSquad] = useState(null);
    const loadSequenceRef = useRef(0);
    const adSquadLoadSequenceRef = useRef(0);

    const writeWorkspaceUrl = useCallback((next) => {
        if (typeof window === "undefined") return;
        const params = new URLSearchParams(window.location.search);
        if (next.tab) params.set("tab", next.tab);
        else params.delete("tab");
        if (next.accountId) params.set("account", next.accountId);
        else params.delete("account");
        if (next.accountId && Number(next.historyPage) > 1) {
            params.set("history_page", String(Math.trunc(Number(next.historyPage))));
        } else {
            params.delete("history_page");
        }
        navigate({ pathname: window.location.pathname, search: `?${params.toString()}` }, { replace: true });
    }, [navigate]);

    const load = useCallback(async ({ silent = false } = {}) => {
        const requestSequence = ++loadSequenceRef.current;
        const requestId = `snap-report-${Date.now()}-${requestSequence}`;
        if (silent) setRefreshing(true);
        else setLoading(true);
        // A prior response never represents the newly requested account/range.
        // Clear it before dispatch so a failed request cannot leave old values
        // looking current.
        setData(null);
        setError("");
        try {
            const result = await getMarketingPerformance({
                platform,
                dateFrom: appliedRange.dateFrom,
                dateTo: appliedRange.dateTo,
                campaignQuery: appliedQuery,
                page,
                limit: 25,
                activeCampaignsOnly,
                actionReportTime,
                resultSource,
                requestId,
            });
            if (requestSequence !== loadSequenceRef.current) return;
            if (platform === "snapchat" && result.request_id !== requestId) {
                throw new Error("campaign_report_request_id_mismatch");
            }
            if (platform === "snapchat") {
                const resolvedRange = {
                    dateFrom: result.range?.date_from,
                    dateTo: result.range?.date_to,
                };
                if (
                    isValidISODate(resolvedRange.dateFrom)
                    && isValidISODate(resolvedRange.dateTo)
                ) {
                    setDateFrom(resolvedRange.dateFrom);
                    setDateTo(resolvedRange.dateTo);
                    setAppliedRange((current) => (
                        current.dateFrom === resolvedRange.dateFrom
                        && current.dateTo === resolvedRange.dateTo
                            ? current
                            : resolvedRange
                    ));
                }
            }
            setData(result);
            setError("");
        } catch (loadError) {
            if (requestSequence !== loadSequenceRef.current) return;
            setData(null);
            const detail = loadError?.response?.data?.detail;
            setError(
                typeof detail === "string"
                    ? detail
                    : detail?.message || "تعذر تحميل تقرير المنصة الإعلانية.",
            );
        } finally {
            if (requestSequence === loadSequenceRef.current) {
                setLoading(false);
                setRefreshing(false);
            }
        }
    }, [actionReportTime, activeCampaignsOnly, appliedQuery, appliedRange, page, platform, resultSource, resultSourceRevision]);

    const reportAccountId = data?.accounts?.[0]?.account_id || null;
    const loadAdSquads = useCallback(async () => {
        if (platform !== "snapchat" || !reportAccountId) return;
        const requestId = ++adSquadLoadSequenceRef.current;
        const requestPage = adSquadPage;
        setAdSquadLoading(true);
        setAdSquadError("");
        try {
            const result = await getSnapchatAdSquadPerformance({
                accountId: reportAccountId,
                dateFrom: appliedRange.dateFrom,
                dateTo: appliedRange.dateTo,
                query: appliedQuery,
                campaignId: selectedCampaign?.campaign_id || undefined,
                page: adSquadPage,
                limit: SNAPCHAT_ENTITY_PAGE_SIZE,
                activeCampaignsOnly,
                sortBy: adSquadSort,
                actionReportTime,
            });
            if (requestId !== adSquadLoadSequenceRef.current) return;
            setAdSquadReport((current) => {
                if (requestPage <= 1 || !current) return result;
                return {
                    ...result,
                    ad_squads: mergePaginatedRows(
                        current.ad_squads,
                        result.ad_squads,
                        (adSquad) => `${adSquad?.account_id || "unknown"}:${adSquad?.ad_squad_id || "unknown"}`,
                    ),
                    pagination: {
                        ...result.pagination,
                        page: requestPage,
                    },
                };
            });
        } catch (loadError) {
            if (requestId !== adSquadLoadSequenceRef.current) return;
            const detail = loadError?.response?.data?.detail;
            setAdSquadError(
                typeof detail === "string"
                    ? detail
                    : detail?.message || "تعذر تحميل المجموعات الإعلانية.",
            );
        } finally {
            if (requestId === adSquadLoadSequenceRef.current) {
                setAdSquadLoading(false);
            }
        }
    }, [
        actionReportTime,
        activeCampaignsOnly,
        adSquadPage,
        adSquadSort,
        appliedQuery,
        appliedRange,
        platform,
        reportAccountId,
        selectedCampaign?.campaign_id,
    ]);

    useEffect(() => {
        const currentToday = todaySA();
        setDateFrom(currentToday);
        setDateTo(currentToday);
        setAppliedRange({ dateFrom: currentToday, dateTo: currentToday });
        setPage(1);
        setAdSquadPage(1);
        setAppliedQuery("");
        setQuery("");
        const urlState = workspaceUrlState();
        setActiveTab(urlState.tab);
        setSelectedHistoryAccountId(urlState.accountId);
        setHistoryPage(urlState.historyPage);
        setDecisionAccountSummaries([]);
        setEntityLevel("campaigns");
        setActiveCampaignsOnly(true);
        setActionReportTime("conversion");
        setResultSource(campaignResultsSource(platform));
        setAdSquadSort("orders");
        setAdSquadReport(null);
        setAdSquadError("");
        setSelectedCampaign(null);
        setSelectedAdSquad(null);
    }, [platform]);

    useEffect(() => {
        if (platform !== "snapchat" || typeof window === "undefined") {
            return undefined;
        }
        const handleResultSourceUpdated = (event) => {
            if (event?.detail?.platform !== "snapchat") return;
            const nextSource = ["salla", "platform"].includes(event?.detail?.source)
                ? event.detail.source
                : campaignResultsSource("snapchat");

            // Invalidate any response that started before the source switch.
            // Keeping the old payload visible would mix Salla purchases with
            // Snapchat spend until another unrelated refresh occurs.
            loadSequenceRef.current += 1;
            setData(null);
            setLoading(true);
            setPage(1);
            setAdSquadPage(1);
            setResultSource(nextSource);
            setResultSourceRevision((value) => value + 1);
        };
        window.addEventListener(
            CAMPAIGN_RESULTS_SOURCE_EVENT,
            handleResultSourceUpdated,
        );
        return () => window.removeEventListener(
            CAMPAIGN_RESULTS_SOURCE_EVENT,
            handleResultSourceUpdated,
        );
    }, [platform]);

    useEffect(() => {
        load();
    }, [load]);

    useEffect(() => {
        if (activeTab === "campaigns" && entityLevel === "ad_squads") {
            loadAdSquads();
        }
    }, [activeTab, entityLevel, loadAdSquads]);

    const totals = data?.totals || {};
    const connection = data?.connection || {};
    const requestedCampaignDiagnostic = data?.source?.requested_campaign_diagnostic;
    const platformSnapshotPending = isSnapchatPlatformSnapshotPending(
        platform,
        data,
    );
    const pagination = data?.campaign_pagination || {
        page: 1,
        pages: 0,
        total: 0,
    };

    function applyFilters(event) {
        event.preventDefault();
        if (!isValidISODate(dateFrom) || !isValidISODate(dateTo) || dateTo < dateFrom) {
            setError("تحقق من فترة التقرير قبل المتابعة.");
            return;
        }
        setPage(1);
        setAdSquadPage(1);
        setAppliedRange({ dateFrom, dateTo });
        setAppliedQuery(query.trim());
    }

    function applyDateRange(range) {
        if (!isValidISODate(range.dateFrom) || !isValidISODate(range.dateTo)) return;
        setDateFrom(range.dateFrom);
        setDateTo(range.dateTo);
        setPage(1);
        setAdSquadPage(1);
        setAppliedRange(range);
    }

    function refreshReports() {
        load({ silent: true });
        if (entityLevel === "ad_squads") loadAdSquads();
    }

    function openCampaignAdSquads(campaign) {
        if (!campaign?.campaign_id) return;
        setSelectedCampaign({
            campaign_id: campaign.campaign_id,
            campaign_name: campaign.campaign_name || campaign.campaign_id,
        });
        setSelectedAdSquad(null);
        setAdSquadPage(1);
        setEntityLevel("ad_squads");
    }

    function openAdSquadAds(adSquad) {
        if (!adSquad?.ad_squad_id) return;
        setSelectedCampaign({
            campaign_id: adSquad.campaign_id,
            campaign_name: adSquad.campaign_name || adSquad.campaign_id,
        });
        setSelectedAdSquad({
            ad_squad_id: adSquad.ad_squad_id,
            ad_squad_name: adSquad.ad_squad_name || adSquad.ad_squad_id,
        });
        setEntityLevel("ads");
    }

    function clearEntityHierarchy() {
        setSelectedCampaign(null);
        setSelectedAdSquad(null);
        setAdSquadPage(1);
        setEntityLevel("campaigns");
    }

    const selectHistoryAccount = useCallback((account) => {
        if (!account?.account_id) return;
        setActiveTab("accounts");
        setSelectedHistoryAccountId(account.account_id);
        setHistoryPage(1);
        writeWorkspaceUrl({ tab: "accounts", accountId: account.account_id, historyPage: 1 });
    }, [writeWorkspaceUrl]);

    const changeHistoryPage = useCallback((nextPage) => {
        const normalizedPage = Math.max(1, Math.trunc(Number(nextPage) || 1));
        setHistoryPage(normalizedPage);
        writeWorkspaceUrl({
            tab: "accounts",
            accountId: selectedHistoryAccountId,
            historyPage: normalizedPage,
        });
    }, [selectedHistoryAccountId, writeWorkspaceUrl]);

    const selectedHistoryAccount = (data?.accounts || []).find(
        (account) => account.account_id === selectedHistoryAccountId,
    );

    if (loading && !data) return <LoadingState />;

    return (
        <div className="space-y-5" dir="rtl" data-testid="marketing-platform-workspace">
            <header className="overflow-hidden rounded-3xl border border-slate-800 bg-slate-950 text-white shadow-xl">
                <div className="grid gap-5 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                    <div>
                        <button
                            type="button"
                            onClick={() => navigate("/ads-manager")}
                            className="mb-4 inline-flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-extrabold text-slate-200 hover:border-emerald-300 hover:text-emerald-200"
                        >
                            <ArrowRight size={16} weight="bold" />
                            جميع منصات الإعلانات
                        </button>
                        <div className="text-xs font-bold tracking-wide text-emerald-300">Mezan 2 · التسويق</div>
                        <h1 className="mt-1 text-2xl font-black sm:text-3xl" data-testid="marketing-platform-title">
                            إدارة حملات {config.label}
                        </h1>
                        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
                            تقرير موحد لأداء المنصة. بطاقات الرسم البياني تتيح إخفاء وإظهار كل مؤشر مباشرة.
                        </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                        <button
                            type="button"
                            onClick={() => navigate(`/integrations-v2?provider=${config.integrationProvider}`)}
                            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl border border-slate-600 bg-slate-900 px-4 text-sm font-black text-white hover:border-emerald-300"
                            data-testid="marketing-platform-manage-integration"
                        >
                            <Gear size={19} weight="duotone" />
                            إدارة ربط التطبيق
                        </button>
                        <button
                            type="button"
                            onClick={refreshReports}
                            disabled={refreshing || adSquadLoading}
                            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-emerald-200 px-5 text-sm font-black text-slate-950 transition hover:bg-emerald-100 disabled:opacity-60"
                            data-testid="marketing-platform-refresh"
                        >
                            <ArrowClockwise size={19} weight="bold" className={refreshing || adSquadLoading ? "animate-spin" : ""} />
                            تحديث التقرير
                        </button>
                    </div>
                </div>
                <div className="border-t border-white/10 bg-slate-900/80 px-5 py-3 text-xs font-bold text-slate-300 sm:px-7">
                    حالة الربط: <span className="text-emerald-200">{CONNECTION_LABELS[connection.status] || connection.status || "غير محسوم"}</span>
                    <span className="mx-2">·</span>
                    آخر مزامنة: <span className="text-white">{dateTime(connection.last_sync_at)}</span>
                    <span className="mx-2">·</span>
                    التطبيق وإعادة التوثيق والصلاحيات تُدار من صفحة التطبيقات فقط.
                </div>
            </header>

            <form
                onSubmit={applyFilters}
                className="grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm lg:grid-cols-[minmax(280px,420px)_minmax(240px,1fr)_auto] lg:items-end"
            >
                <label className="block">
                    <span className="mb-1 block text-xs font-black text-slate-600">الفترة الزمنية</span>
                    <ArabicDateRangePicker
                        valueFrom={dateFrom}
                        valueTo={dateTo}
                        onApply={applyDateRange}
                    />
                </label>
                <label className="block">
                    <span className="mb-1 block text-xs font-black text-slate-600">بحث في الحملات والمجموعات</span>
                    <span className="relative block">
                        <MagnifyingGlass size={18} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder="اسم الحملة أو المجموعة أو رقمها"
                            className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 pr-10 pl-3 text-sm outline-none focus:border-emerald-400 focus:bg-white"
                        />
                    </span>
                </label>
                <button type="submit" className="h-11 rounded-xl bg-slate-950 px-6 text-sm font-black text-white hover:bg-slate-800">
                    تطبيق التقرير
                </button>
            </form>

            {platform === "snapchat" && (
                <section
                    className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
                    data-testid="snapchat-action-report-time-control"
                >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <div className="text-sm font-black text-slate-900">توقيت نتائج Snapchat</div>
                            <div className="mt-1 text-xs font-semibold text-slate-500">
                                وقت التحويل هو الافتراضي لقرارات التشغيل والذكاء الاصطناعي. وقت الظهور متاح للمقارنة التاريخية. نافذة الإسناد: 28 يومًا للنقر · 7 أيام للمشاهدة.
                            </div>
                        </div>
                        <div className="inline-flex rounded-xl border border-slate-200 bg-slate-50 p-1">
                            {[
                                ["conversion", "وقت التحويل · موصى به"],
                                ["impression", "وقت الظهور · مقارنة"],
                            ].map(([value, label]) => (
                                <button
                                    key={value}
                                    type="button"
                                    onClick={() => {
                                        setPage(1);
                                        setAdSquadPage(1);
                                        setActionReportTime(value);
                                    }}
                                    aria-pressed={actionReportTime === value}
                                    data-testid={`snapchat-action-report-time-${value}`}
                                    className={`rounded-lg px-4 py-2 text-xs font-black transition ${
                                        actionReportTime === value
                                            ? "bg-slate-950 text-white shadow-sm"
                                            : "text-slate-600 hover:bg-white"
                                    }`}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                    </div>
                </section>
            )}

            {error && (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800">
                    <WarningCircle size={20} weight="fill" className="ml-2 inline" />
                    {error}
                </div>
            )}

            {platformSnapshotPending && (
                <div
                    className="rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm font-bold leading-6 text-amber-950"
                    data-testid="snapchat-platform-total-pending"
                >
                    <WarningCircle size={20} weight="fill" className="ml-2 inline" />
                    نتائج Snapchat المطابقة لمدير الإعلانات قيد المزامنة. أخفى ميزان أرقام التحويل القديمة بدل عرض تقرير جزئي.
                </div>
            )}

            {platform === "snapchat" && data && data.reconciliation_status !== "reconciled" && (
                <div
                    className="rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm font-bold leading-6 text-amber-950"
                    data-testid="snapchat-source-reconciliation-warning"
                >
                    <WarningCircle size={20} weight="fill" className="ml-2 inline" />
                    التقرير غير متصالح بين المصدرين: سلة {data.salla_status}، سناب {data.snapchat_status}.
                    أُخفيت ROAS وCPA الحالية حتى تكتمل النافذتان. آخر سلة {dateTime(data.salla_as_of)}، وآخر سناب {dateTime(data.snapchat_as_of)}.
                </div>
            )}

            {platform === "snapchat" && data && (
                <div className="grid gap-3 sm:grid-cols-2" data-testid="snapchat-source-as-of">
                    <div className="rounded-2xl border border-slate-200 bg-white p-4 text-sm">
                        <div className="font-black text-slate-900">سلة · {data.salla_status}</div>
                        <div className="mt-1 font-semibold text-slate-500">طلبات ومبيعات وتكلفة وربح · حتى {dateTime(data.salla_as_of)}</div>
                    </div>
                    <div className="rounded-2xl border border-slate-200 bg-white p-4 text-sm">
                        <div className="font-black text-slate-900">Snapchat · {data.snapchat_status}</div>
                        <div className="mt-1 font-semibold text-slate-500">صرف ومشتريات وقيمة شراء وظهور ونقرات · حتى {dateTime(data.snapchat_as_of)}</div>
                    </div>
                </div>
            )}

            {platform === "snapchat" && requestedCampaignDiagnostic?.campaign_id && (
                <div
                    className="rounded-2xl border border-slate-300 bg-slate-50 p-4 text-sm font-bold leading-6 text-slate-800"
                    data-testid="snapchat-requested-campaign-diagnostic"
                >
                    الحملة <span className="font-mono">{requestedCampaignDiagnostic.campaign_id}</span>: {CAMPAIGN_DIAGNOSTIC_LABELS[requestedCampaignDiagnostic.reason] || requestedCampaignDiagnostic.reason}.
                </div>
            )}

            <AdsPerformanceExplorer
                totals={totals}
                daily={data?.daily || []}
                platformLabel={config.label}
            />

            {(data?.source?.row_limit_reached || data?.source?.entity_limit_reached) && (
                <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm font-bold leading-6 text-amber-900">
                    <WarningCircle size={20} weight="fill" className="ml-2 inline" />
                    بلغ مصدر البيانات حد القراءة. تظهر الصفحة ما تم التحقق منه فقط، ولا يُسمح للذكاء الاصطناعي باتخاذ قرار من تقرير جزئي.
                </div>
            )}

            <nav className="flex gap-2 overflow-x-auto rounded-2xl border border-slate-200 bg-white p-2" aria-label="أقسام تقرير المنصة">
                {TABS.map(({ id, label, Icon }) => (
                    <button
                        key={id}
                        type="button"
                        onClick={() => {
                            if (platform === "snapchat" && id === "ai") {
                                setActionReportTime("conversion");
                            }
                            setActiveTab(id);
                            writeWorkspaceUrl({
                                tab: id,
                                accountId: id === "accounts" ? selectedHistoryAccountId : null,
                                historyPage: id === "accounts" ? historyPage : 1,
                            });
                        }}
                        className={`inline-flex min-h-11 shrink-0 items-center gap-2 rounded-xl px-4 text-sm font-extrabold transition ${activeTab === id ? "bg-slate-950 text-white" : "text-slate-600 hover:bg-slate-50"}`}
                        aria-pressed={activeTab === id}
                        data-testid={`marketing-platform-tab-${id}`}
                    >
                        <Icon size={18} weight="duotone" />
                        {label}
                    </button>
                ))}
            </nav>

            {activeTab === "overview" && <InsightPanel insights={data?.insights || []} />}

            {activeTab === "campaigns" && (
                <AdsEntityLevelWorkspace
                    platform={platform}
                    platformLabel={config.label}
                    resultSource={data?.result_source || "salla"}
                    actionReportTime={actionReportTime}
                    entityLevel={entityLevel}
                    onEntityLevelChange={(level) => {
                        setEntityLevel(level);
                        setAdSquadPage(1);
                        if (level === "campaigns") {
                            setSelectedCampaign(null);
                            setSelectedAdSquad(null);
                        } else if (level === "ad_squads") {
                            setSelectedAdSquad(null);
                        }
                    }}
                    campaigns={data?.campaigns || []}
                    campaignTotals={totals}
                    campaignPagination={pagination}
                    campaignPage={page}
                    onCampaignPageChange={setPage}
                    campaignLoading={loading}
                    readOnly={data?.policy?.mutations_allowed !== true}
                    activeCampaignsOnly={activeCampaignsOnly}
                    onActiveCampaignsOnlyChange={(value) => {
                        setPage(1);
                        setAdSquadPage(1);
                        setActiveCampaignsOnly(value);
                    }}
                    adSquadSort={adSquadSort}
                    onAdSquadSortChange={(value) => {
                        setAdSquadPage(1);
                        setAdSquadSort(value);
                    }}
                    adSquadReport={adSquadReport}
                    adSquadPage={adSquadPage}
                    onAdSquadPageChange={setAdSquadPage}
                    adSquadLoading={adSquadLoading}
                    adSquadError={adSquadError}
                    selectedCampaign={selectedCampaign}
                    selectedAdSquad={selectedAdSquad}
                    onOpenAdSquads={openCampaignAdSquads}
                    onOpenAds={openAdSquadAds}
                    onClearHierarchy={clearEntityHierarchy}
                    onManagementChanged={refreshReports}
                />
            )}

            {activeTab === "accounts" && (
                <div className="space-y-4">
                    <AccountSummaries
                        accounts={data?.accounts || []}
                        platform={platform}
                        decisionSummaries={decisionAccountSummaries}
                        selectedAccountId={selectedHistoryAccountId}
                        onSelect={selectHistoryAccount}
                        decisionHistoryEnabled={platform === "snapchat"}
                    />
                    {platform === "snapchat" && (
                        <AdAccountDecisionHistory
                            accountId={selectedHistoryAccountId}
                            accountName={selectedHistoryAccount?.account_name || selectedHistoryAccount?.display_name}
                            page={historyPage}
                            onPageChange={changeHistoryPage}
                            onSummariesLoaded={setDecisionAccountSummaries}
                        />
                    )}
                </div>
            )}

            {activeTab === "ai" && (
                <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
                    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                        <div className="flex items-center gap-3">
                            <span className="rounded-2xl bg-violet-100 p-3 text-violet-700">
                                <Robot size={26} weight="duotone" />
                            </span>
                            <div>
                                <h2 className="text-xl font-black text-slate-900">جاهزية ذكاء ميزان</h2>
                                <p className="mt-1 text-xs font-semibold text-slate-500">تتحقق الجاهزية من الدليل الفعلي، لا من مجرد وجود الربط. نتائج سلة للمبيعات والمكسب هي أساس القرار، ووقت التحويل وإشارات المنصة والسياق أدلة مساندة للتحقق والمقارنة.</p>
                            </div>
                        </div>
                        <div className="mt-5 grid gap-3 sm:grid-cols-2">
                            <ReadinessItem label="بيانات التقرير" ready={data?.ai_readiness?.report_ready} detail="وجود صفوف أداء كاملة دون تجاوز حد القراءة." />
                            <ReadinessItem label="هوية الحملات" ready={data?.ai_readiness?.campaign_identity_ready} detail="مطابقة معرف الحملة مع الاسم والحالة والميزانية." />
                            <ReadinessItem label={platform === "snapchat" ? "صرف Snapchat" : "الصرف"} ready={data?.ai_readiness?.spend_ready} detail="توفر صرف موثق بالريال ضمن الفترة." />
                            <ReadinessItem label={platform === "snapchat" ? "طلبات سلة المطابقة" : "الطلبات"} ready={data?.ai_readiness?.orders_ready} detail={platform === "snapchat" ? "مطابقة حرفية عبر UTM Campaign ID." : "توفر عدد مشتريات منسوب بواسطة المنصة."} />
                            <ReadinessItem label={platform === "snapchat" ? "مبيعات سلة" : "المبيعات"} ready={data?.ai_readiness?.sales_ready} detail={platform === "snapchat" ? "مبيعات سلة من نفس مجموعة الطلبات المطابقة." : "توفر قيمة مشتريات منسوبة بواسطة المنصة."} />
                            <ReadinessItem label="التحليل والمقارنة" ready={data?.ai_readiness?.ratios_ready} detail="إمكانية حساب ROAS وCPA دون خلط فترات ناقصة." />
                        </div>
                    </section>
                    <aside className="rounded-2xl border border-slate-800 bg-slate-950 p-5 text-white shadow-xl">
                        <div className="flex items-center gap-2 text-emerald-200">
                            <ShieldCheck size={22} weight="fill" />
                            <span className="font-black">حوكمة التنفيذ</span>
                        </div>
                        <h2 className="mt-4 text-xl font-black">
                            {platform === "snapchat" ? "الذكاء يحلل، والتنفيذ اليدوي محكوم" : "الذكاء يحلل الآن، ولا ينفذ بعد"}
                        </h2>
                        <p className="mt-2 text-sm font-semibold leading-6 text-slate-300">
                            {platform === "snapchat"
                                ? "يمكن لمالك الحساب تنفيذ إنشاء أو تعديل أو إيقاف من لوحة الإدارة بعد معاينة واعتماد صريح، مع تحقق وسجل وتراجع. لا ينفذ الذكاء أي تغيير تلقائيًا."
                                : "إنشاء الحملات وتعديل الميزانية والإيقاف والاستئناف تبقى مقفلة حتى اكتمال دورة آمنة يمكن مراجعتها والتراجع عنها."}
                        </p>
                        <div className="mt-5 space-y-2">
                            {(data?.ai_readiness?.required_lifecycle || []).map((step, index) => (
                                <div key={step} className="flex items-center gap-3 rounded-xl bg-white/5 px-3 py-2 text-sm font-bold">
                                    <span className="flex h-7 w-7 items-center justify-center rounded-full bg-emerald-200 font-mono text-xs font-black text-slate-950">{index + 1}</span>
                                    {step}
                                </div>
                            ))}
                        </div>
                    </aside>
                </div>
            )}
        </div>
    );
}
