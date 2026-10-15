import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
    ArrowClockwise,
    ChartLineUp,
    CheckCircle,
    MagnifyingGlass,
    Plug,
    ShieldCheck,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";
import { useOptionalAuth } from "../context/AuthContext";
import CapabilityMatrix from "../components/integrationsV2/CapabilityMatrix";
import IntegrationActivityPanel from "../components/integrationsV2/IntegrationActivityPanel";
import IntegrationCard from "../components/integrationsV2/IntegrationCard";
import FinancialProviderAppsWorkspace from "./FinancialProviderAppsWorkspace";
import {
    focusedIntegrationProvider,
    integrationWorkspaceFromSearchParams,
    providersForIntegrationWorkspace,
    summarizeAdvertisingWorkspace,
} from "../lib/integrationWorkspaces";
import {
    filterIntegrationProviders,
    getIntegrationsActivity,
    getIntegrationsOverview,
    normalizeIntegrationOverview,
    syncIntegrationData,
    testIntegrationConnection,
} from "../services/integrationsV2";

const TABS = [
    { id: "apps", label: "التطبيقات", Icon: Plug },
    { id: "capabilities", label: "مصفوفة القدرات", Icon: ChartLineUp },
    { id: "activity", label: "المزامنة والأخطاء", Icon: WarningCircle },
];

const FILTERS = [
    { id: "all", label: "الكل" },
    { id: "api_connection", label: "ربط API مباشر" },
    { id: "legacy_integration", label: "تكامل قائم سابقًا" },
    { id: "data_feed", label: "تغذية بيانات فقط" },
    { id: "attention", label: "يحتاج انتباه" },
    { id: "disconnected", label: "غير مرتبط" },
    { id: "planned", label: "مستقبلي" },
    { id: "unknown", label: "غير محسوم" },
];

function SummaryCard({ label, value, hint, tone, Icon, testid }) {
    const tones = {
        emerald: "border-emerald-100 bg-emerald-50 text-emerald-700",
        blue: "border-blue-100 bg-blue-50 text-blue-700",
        sky: "border-sky-100 bg-sky-50 text-sky-700",
        amber: "border-amber-100 bg-amber-50 text-amber-700",
        rose: "border-rose-100 bg-rose-50 text-rose-700",
        slate: "border-slate-200 bg-slate-50 text-slate-700",
        violet: "border-violet-100 bg-violet-50 text-violet-700",
    };
    return (
        <div className={`rounded-xl border p-4 ${tones[tone]}`} data-testid={testid}>
            <div className="flex items-start justify-between gap-3">
                <div>
                    <div className="text-xs font-extrabold opacity-80">{label}</div>
                    <div className="mt-2 font-mono text-3xl font-black">{value}</div>
                </div>
                <div className="rounded-lg bg-white/70 p-2">
                    <Icon size={22} weight="duotone" />
                </div>
            </div>
            <div className="mt-2 text-[11px] font-semibold opacity-70">{hint}</div>
        </div>
    );
}

function PageSkeleton() {
    return (
        <div className="space-y-5" data-testid="integrations-v2-loading">
            <div className="h-40 animate-pulse rounded-xl bg-slate-200" />
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
                {[0, 1, 2, 3, 4, 5].map((key) => (
                    <div key={key} className="h-28 animate-pulse rounded-xl bg-slate-100" />
                ))}
            </div>
            <div className="grid gap-4 xl:grid-cols-2">
                {[0, 1, 2, 3].map((key) => (
                    <div key={key} className="h-96 animate-pulse rounded-xl bg-slate-100" />
                ))}
            </div>
        </div>
    );
}

export default function AppsIntegrationsControlCenter() {
    const navigate = useNavigate();
    const { user } = useOptionalAuth() || {};
    const isMetaReviewer = user?.role === "meta_reviewer";
    const [searchParams] = useSearchParams();
    const [overview, setOverview] = useState(() => normalizeIntegrationOverview({}));
    const [activity, setActivity] = useState({ runs: [], errors: [] });
    const [loading, setLoading] = useState(true);
    const [activityLoading, setActivityLoading] = useState(true);
    const [error, setError] = useState("");
    const [activeTab, setActiveTab] = useState("apps");
    const [statusFilter, setStatusFilter] = useState("all");
    const [query, setQuery] = useState("");
    const [testingProvider, setTestingProvider] = useState("");
    const [syncingProvider, setSyncingProvider] = useState("");

    const workspace = integrationWorkspaceFromSearchParams(searchParams);
    const accountsWorkspace = workspace === "accounts";
    const financialWorkspace = workspace === "financial";

    const load = useCallback(async ({ silent = false } = {}) => {
        if (!silent) setLoading(true);
        setActivityLoading(true);
        setError("");
        const [overviewResult, activityResult] = await Promise.allSettled([
            getIntegrationsOverview(),
            getIntegrationsActivity({ limit: 50, provider: isMetaReviewer ? "meta_ads" : undefined }),
        ]);

        if (overviewResult.status === "fulfilled") {
            const nextOverview = overviewResult.value;
            setOverview(isMetaReviewer
                ? {
                    ...nextOverview,
                    providers: nextOverview.providers.filter(
                        (provider) => ["meta_ads", "instagram"].includes(provider.provider),
                    ),
                }
                : nextOverview);
        } else {
            setError("تعذر تحميل حالة التكاملات. أعد المحاولة بعد التحقق من Backend.");
        }

        if (activityResult.status === "fulfilled") {
            setActivity(activityResult.value);
        }
        setActivityLoading(false);
        setLoading(false);
    }, [isMetaReviewer]);

    useEffect(() => {
        if (financialWorkspace) {
            setLoading(false);
            setActivityLoading(false);
            return;
        }
        load();
    }, [load, financialWorkspace]);

    useEffect(() => {
        if (accountsWorkspace && activeTab === "capabilities") {
            setActiveTab("apps");
        }
    }, [accountsWorkspace, activeTab]);

    const focusedProvider = focusedIntegrationProvider(
        searchParams,
        overview.providers,
    );
    const workspaceProviders = useMemo(() => providersForIntegrationWorkspace(
        overview.providers,
        workspace,
    ), [overview.providers, workspace]);
    const visibleProviders = useMemo(() => filterIntegrationProviders(
        workspaceProviders,
        { query, status: statusFilter },
    ).filter((provider) => !focusedProvider || provider.provider === focusedProvider), [
        workspaceProviders,
        query,
        statusFilter,
        focusedProvider,
    ]);
    const advertisingSummary = useMemo(
        () => summarizeAdvertisingWorkspace(overview.providers),
        [overview.providers],
    );
    const visibleTabs = accountsWorkspace
        ? TABS.filter((tab) => tab.id !== "capabilities")
        : TABS;

    async function handleTest(provider) {
        setTestingProvider(provider);
        try {
            const result = await testIntegrationConnection(provider);
            if (["passed", "healthy", "success", "succeeded"].includes(result?.status)) {
                toast.success("نجح الفحص المحلي للإعداد والبيانات");
            } else {
                toast.warning(result?.message || "اكتمل الفحص ويحتاج الربط إلى مراجعة");
            }
            await load({ silent: true });
        } catch (testError) {
            const detail = testError?.response?.data?.detail;
            const message = typeof detail === "string"
                ? detail
                : detail?.message || "تعذر اختبار الاتصال";
            toast.error(message);
            await load({ silent: true });
        } finally {
            setTestingProvider("");
        }
    }

    async function handleSync(provider) {
        setSyncingProvider(provider);
        try {
            const result = await syncIntegrationData(provider, { days: 30 });
            if (result.status === "complete") {
                toast.success(
                    `اكتملت مزامنة سناب: ${result.accounts_complete} حساب، ${result.rows_saved} صف يومي`,
                );
            } else if (result.status === "partial") {
                toast.warning(
                    `اكتملت المزامنة جزئيًا: ${result.accounts_complete}/${result.accounts_attempted} حساب، ${result.errors_count} ملاحظة`,
                    { duration: 8000 },
                );
            } else {
                toast.error("لم تكتمل مزامنة بيانات سناب.");
            }
            await load({ silent: true });
        } catch (syncError) {
            const status = syncError?.response?.status;
            const code = syncError?.response?.data?.detail?.code;
            const knownMessages = {
                snapchat_analytics_sync_in_progress: "توجد مزامنة سناب قيد التشغيل بالفعل.",
                snapchat_account_limit_exceeded: "عدد حسابات سناب المفعّلة يتجاوز الحد الآمن للتشغيل الواحد.",
                snapchat_provider_call_budget_exceeded: "النطاق المطلوب يتجاوز ميزانية الاتصال الآمنة.",
                snapchat_currency_unverified: "عملة أحد حسابات سناب مفقودة أو غير مدعومة.",
                snapchat_usd_rate_unverified: "سعر تحويل الدولار إلى الريال غير صالح.",
                snapchat_needs_reauth: "يجب إعادة توثيق ربط Snapchat قبل المزامنة.",
                snapchat_analytics_sync_disabled: "مزامنة سناب متوقفة مؤقتًا بحارس الأمان.",
            };
            const message = knownMessages[code] || (status === 403
                ? "مزامنة سناب متاحة لمالك الحساب فقط."
                : status === 409
                    ? "لا يمكن بدء المزامنة الآن؛ راجع حالة ربط حسابات سناب."
                    : status === 503
                        ? "مزامنة سناب متوقفة مؤقتًا بحارس الأمان."
                        : "تعذر مزامنة بيانات سناب. راجع سجل المزامنة المنقح.");
            toast.error(message);
            await load({ silent: true });
        } finally {
            setSyncingProvider("");
        }
    }

    function openSettings(provider) {
        const integration = overview.providers.find((row) => row.provider === provider);
        const target = (
            integration?.actions?.settings?.href
            || integration?.actions?.reconnect?.href
        );
        if (!target) {
            toast.info("صفحة الربط الخاصة بهذا التطبيق ستُضاف في مرحلة لاحقة.");
            return;
        }
        navigate(target);
    }

    if (financialWorkspace) return <FinancialProviderAppsWorkspace />;
    if (loading) return <PageSkeleton />;

    return (
        <div className="space-y-5" dir="rtl" data-testid="apps-integrations-control-center">
            <header className="overflow-hidden rounded-xl border border-emerald-950 bg-emerald-950 text-white">
                <div className="grid gap-6 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                    <div>
                        <div className="mb-3 flex flex-wrap items-center gap-2">
                            <span className="inline-flex items-center gap-2 rounded-full border border-emerald-700 bg-emerald-900 px-3 py-1 text-xs font-extrabold text-emerald-100">
                                <ShieldCheck size={16} weight="fill" />
                                {accountsWorkspace
                                    ? "ربط الحسابات الإعلانية"
                                    : "مرحلة المراقبة والجاهزية"}
                            </span>
                            <button
                                type="button"
                                onClick={() => navigate("/integrations-v2")}
                                className={`rounded-full border px-3 py-1 text-xs font-extrabold transition ${
                                    !accountsWorkspace
                                        ? "border-white bg-white text-emerald-950"
                                        : "border-emerald-700 bg-emerald-900 text-emerald-100 hover:bg-emerald-800"
                                }`}
                                data-testid="integrations-workspace-apps"
                            >
                                كل التطبيقات
                            </button>
                            <button
                                type="button"
                                onClick={() => navigate("/integrations-v2?workspace=accounts")}
                                className={`rounded-full border px-3 py-1 text-xs font-extrabold transition ${
                                    accountsWorkspace
                                        ? "border-white bg-white text-emerald-950"
                                        : "border-emerald-700 bg-emerald-900 text-emerald-100 hover:bg-emerald-800"
                                }`}
                                data-testid="integrations-workspace-accounts"
                            >
                                الحسابات الإعلانية
                            </button>
                        </div>
                        <h1 className="text-2xl font-black tracking-tight sm:text-3xl">
                            {accountsWorkspace
                                ? "الحسابات الإعلانية المرتبطة"
                                : "التطبيقات والتكاملات"}
                        </h1>
                        <div className="mt-1 text-xs font-bold tracking-wide text-emerald-300">
                            {accountsWorkspace
                                ? "Advertising Accounts Workspace"
                                : "Apps & Integrations Control Center"}
                        </div>
                        <p className="mt-2 max-w-3xl text-sm leading-6 text-emerald-100">
                            {accountsWorkspace
                                ? "مكان واحد لمراجعة حسابات Snapchat وTikTok وMeta وGoogle، والتحقق من معرّف كل حساب وعملته وتوقيته وحالة اتصاله ومزامنته، من دون خلطها بأرصدة ومديونيات الإعلانات."
                                : "مركز واحد لقياس صحة الربط وجودة البيانات والصلاحيات، وتجهيز ميزان لإدارة المتجر والإعلانات مستقبلًا ضمن دورة اعتماد وتحقق كاملة. مزامنة سناب التحليلية تتم هنا داخل V2 ولا تعتمد على صفحات القديم."}
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={() => load({ silent: true })}
                        className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-emerald-600 bg-white px-4 text-sm font-extrabold text-emerald-950 transition hover:bg-emerald-50"
                        data-testid="integrations-refresh-all"
                    >
                        <ArrowClockwise size={19} weight="bold" />
                        تحديث الحالة
                    </button>
                </div>
                <div className="border-t border-emerald-800 bg-emerald-900 px-5 py-3 text-xs font-semibold leading-5 text-emerald-100 sm:px-7">
                    {accountsWorkspace
                        ? "هذه الصفحة تدير نطاق الحسابات والقراءة التحليلية فقط. لا تنشئ تعبئة أو مديونية، ولا تسجل مصروفًا محاسبيًا، ولا تغيّر حملات أو ميزانيات. صفحة الأرصدة والمديونيات المالية تبقى مستقلة تحت العمليات المالية."
                        : "التصنيف مبني على أدلة محلية محفوظة، وزر الفحص لا يغيّر الربط. زر مزامنة سناب يحدّث الحقائق التحليلية فقط؛ لا ينشئ المركز حملات، ولا يغيّر ميزانيات، ولا يحذف Tokens أو ربط سلة أو قيود. أي تعديل حساس مستقبلًا يمر: اقتراح ← معاينة ← اعتماد ← تنفيذ ← تحقق ← سجل ← رجوع."}
                </div>
            </header>

            {error && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">
                    <div className="flex items-center gap-2 font-bold">
                        <WarningCircle size={20} weight="fill" />
                        {error}
                    </div>
                    <button
                        type="button"
                        onClick={() => load()}
                        className="rounded-lg border border-rose-200 bg-white px-3 py-2 text-xs font-extrabold"
                        data-testid="integrations-retry"
                    >
                        إعادة المحاولة
                    </button>
                </div>
            )}

            {accountsWorkspace ? (
                <section
                    className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6"
                    aria-label="ملخص الحسابات الإعلانية"
                    data-testid="advertising-accounts-summary"
                >
                    <SummaryCard
                        label="منصات إعلانية"
                        value={advertisingSummary.providers_total}
                        hint="Snapchat وTikTok وMeta وGoogle"
                        tone="slate"
                        Icon={ChartLineUp}
                        testid="advertising-summary-providers"
                    />
                    <SummaryCard
                        label="ربط API مباشر"
                        value={advertisingSummary.api_connections}
                        hint="اتصال موثق بواجهة المنصة"
                        tone="emerald"
                        Icon={Plug}
                        testid="advertising-summary-api"
                    />
                    <SummaryCard
                        label="منصات متصلة"
                        value={advertisingSummary.connected_providers}
                        hint="حالة الاتصال الحالية"
                        tone="blue"
                        Icon={CheckCircle}
                        testid="advertising-summary-connected"
                    />
                    <SummaryCard
                        label="حسابات ظاهرة"
                        value={advertisingSummary.accounts_visible}
                        hint="حسابات مكتشفة داخل الربط"
                        tone="sky"
                        Icon={ChartLineUp}
                        testid="advertising-summary-accounts"
                    />
                    <SummaryCard
                        label="عملات / توقيتات"
                        value={`${advertisingSummary.currencies}/${advertisingSummary.timezones}`}
                        hint="عملات وتوقيتات الحسابات الظاهرة"
                        tone="violet"
                        Icon={ShieldCheck}
                        testid="advertising-summary-locales"
                    />
                    <SummaryCard
                        label="يحتاج انتباه"
                        value={advertisingSummary.attention_required}
                        hint="إعادة ربط أو صلاحيات أو صحة البيانات"
                        tone={advertisingSummary.attention_required ? "rose" : "emerald"}
                        Icon={WarningCircle}
                        testid="advertising-summary-attention"
                    />
                </section>
            ) : (
                <section
                    className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6"
                    aria-label="تصنيف مصادر التكامل"
                >
                    <SummaryCard
                        label="ربط API مباشر"
                        value={overview.summary.api_connections}
                        hint="سلة وموصلات API الحديثة"
                        tone="emerald"
                        Icon={Plug}
                        testid="integrations-summary-api"
                    />
                    <SummaryCard
                        label="تكامل قائم سابقًا"
                        value={overview.summary.legacy_integrations}
                        hint="موصل انتقالي تحت إدارة V2"
                        tone="amber"
                        Icon={ShieldCheck}
                        testid="integrations-summary-legacy"
                    />
                    <SummaryCard
                        label="تغذية بيانات فقط"
                        value={overview.summary.data_feeds}
                        hint="لا تثبت اتصال إدارة API"
                        tone="sky"
                        Icon={ChartLineUp}
                        testid="integrations-summary-feed"
                    />
                    <SummaryCard
                        label="غير مرتبط"
                        value={overview.summary.disconnected}
                        hint="يحتاج موصلًا معتمدًا"
                        tone="slate"
                        Icon={WarningCircle}
                        testid="integrations-summary-disconnected"
                    />
                    <SummaryCard
                        label="مستقبلي"
                        value={overview.summary.planned}
                        hint="ضمن الخطة اللاحقة"
                        tone="violet"
                        Icon={CheckCircle}
                        testid="integrations-summary-planned"
                    />
                    <SummaryCard
                        label="غير محسوم"
                        value={overview.summary.unknown}
                        hint={`من أصل ${overview.summary.total} تطبيقات`}
                        tone="rose"
                        Icon={WarningCircle}
                        testid="integrations-summary-unknown"
                    />
                </section>
            )}

            {accountsWorkspace && (
                <section className="flex flex-col gap-3 rounded-xl border border-sky-200 bg-sky-50 p-4 text-sm text-sky-900 sm:flex-row sm:items-center sm:justify-between" data-testid="advertising-accounts-separation-note">
                    <div>
                        <div className="font-extrabold">فصل واضح بين الربط والمحاسبة</div>
                        <div className="mt-1 text-xs font-semibold text-sky-800">
                            الحسابات أدناه تخص الاتصال والاختيار والمزامنة. التعبئة والأرصدة والمديونيات موجودة في صفحة مالية مستقلة ولا تؤثر في هذا الربط.
                        </div>
                    </div>
                    <button
                        type="button"
                        onClick={() => navigate("/ad-accounts")}
                        className="shrink-0 rounded-lg border border-sky-300 bg-white px-3 py-2 text-xs font-extrabold text-sky-900 hover:bg-sky-100"
                        data-testid="open-ad-account-balances"
                    >
                        فتح الأرصدة والمديونيات
                    </button>
                </section>
            )}

            <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white p-2">
                <nav className="flex min-w-max gap-2" aria-label="أقسام مركز التكاملات">
                    {visibleTabs.map(({ id, label, Icon }) => (
                        <button
                            key={id}
                            type="button"
                            onClick={() => setActiveTab(id)}
                            className={`inline-flex min-h-10 items-center gap-2 rounded-lg px-4 text-sm font-extrabold transition ${
                                activeTab === id
                                    ? "bg-emerald-900 text-white"
                                    : "text-slate-600 hover:bg-slate-50"
                            }`}
                            aria-pressed={activeTab === id}
                            data-testid={`integrations-tab-${id}`}
                        >
                            <Icon size={18} weight="duotone" />
                            {accountsWorkspace && id === "apps"
                                ? "الحسابات المرتبطة"
                                : label}
                        </button>
                    ))}
                </nav>
            </div>

            {activeTab === "apps" && (
                <>
                    <section className="grid gap-3 rounded-xl border border-slate-200 bg-white p-3 lg:grid-cols-[minmax(260px,1fr)_auto] lg:items-center">
                        <div className="relative">
                            <MagnifyingGlass
                                size={18}
                                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400"
                            />
                            <input
                                value={query}
                                onChange={(event) => setQuery(event.target.value)}
                                placeholder={accountsWorkspace
                                    ? "ابحث باسم المنصة أو الحساب أو Ad Account ID…"
                                    : "ابحث باسم التطبيق أو الحساب…"}
                                className="h-11 w-full rounded-lg border border-slate-200 bg-slate-50 pe-3 ps-10 text-sm outline-none transition focus:border-emerald-400 focus:bg-white"
                                data-testid="integrations-search"
                            />
                        </div>
                        <div className="flex flex-wrap gap-2">
                            {FILTERS.map((filter) => (
                                <button
                                    key={filter.id}
                                    type="button"
                                    onClick={() => setStatusFilter(filter.id)}
                                    className={`rounded-full border px-3 py-2 text-xs font-extrabold transition ${
                                        statusFilter === filter.id
                                            ? "border-emerald-900 bg-emerald-900 text-white"
                                            : "border-slate-200 bg-white text-slate-600 hover:border-emerald-300"
                                    }`}
                                    aria-pressed={statusFilter === filter.id}
                                    data-testid={`integrations-filter-${filter.id}`}
                                >
                                    {filter.label}
                                </button>
                            ))}
                        </div>
                    </section>

                    {visibleProviders.length ? (
                        <section className={`grid gap-4 ${focusedProvider || isMetaReviewer ? "" : "xl:grid-cols-2"}`} aria-label={accountsWorkspace ? "بطاقات الحسابات الإعلانية" : "بطاقات التطبيقات"}>
                            {visibleProviders.map((integration) => (
                                <IntegrationCard
                                    key={integration.provider}
                                    integration={integration}
                                    testing={testingProvider === integration.provider}
                                    syncing={syncingProvider === integration.provider}
                                    settingsAvailable={Boolean(
                                        integration.actions?.settings?.href
                                        || integration.actions?.reconnect?.href
                                    )}
                                    onTest={handleTest}
                                    onSync={handleSync}
                                    onSettings={openSettings}
                                />
                            ))}
                        </section>
                    ) : (
                        <div className="rounded-xl border border-dashed border-slate-200 bg-white p-12 text-center text-sm text-slate-500">
                            {accountsWorkspace
                                ? "لا توجد حسابات إعلانية تطابق البحث أو الفلتر."
                                : "لا توجد تطبيقات تطابق البحث أو الفلتر."}
                        </div>
                    )}
                </>
            )}

            {activeTab === "capabilities" && !accountsWorkspace && (
                <CapabilityMatrix providers={overview.providers} />
            )}

            {activeTab === "activity" && (
                <IntegrationActivityPanel
                    runs={activity.runs}
                    errors={activity.errors}
                    loading={activityLoading}
                />
            )}
        </div>
    );
}
