import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
    ArrowClockwise,
    ArrowRight,
    ChartLineUp,
    CheckCircle,
    Plug,
    ShieldCheck,
    WarningCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

import CapabilityMatrix from "../components/integrationsV2/CapabilityMatrix";
import IntegrationActivityPanel from "../components/integrationsV2/IntegrationActivityPanel";
import IntegrationCard from "../components/integrationsV2/IntegrationCard";
import {
    getIntegrationsActivity,
    getIntegrationsOverview,
    normalizeIntegrationOverview,
    syncIntegrationData,
    testIntegrationConnection,
} from "../services/integrationsV2";

export const MARKETING_PLATFORM_PROVIDERS = Object.freeze([
    "snapchat_ads",
    "tiktok_ads",
    "meta_ads",
    "google_ads",
]);

export function isMarketingPlatformProvider(value) {
    return MARKETING_PLATFORM_PROVIDERS.includes(String(value || "").trim());
}

const TABS = [
    { id: "overview", label: "نظرة عامة", Icon: Plug },
    { id: "capabilities", label: "القدرات والصلاحيات", Icon: ShieldCheck },
    { id: "activity", label: "المزامنة والأخطاء", Icon: WarningCircle },
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

function statusLabel(value) {
    return CONNECTION_LABELS[value] || value || "غير محسوم";
}

function formatDate(value) {
    if (!value) return "غير متاح";
    try {
        return new Intl.DateTimeFormat("ar-SA", {
            dateStyle: "medium",
            timeStyle: "short",
        }).format(new Date(value));
    } catch {
        return String(value);
    }
}

function MetricCard({ label, value, hint, tone = "slate", Icon }) {
    const tones = {
        emerald: "border-emerald-200 bg-emerald-50 text-emerald-900",
        blue: "border-blue-200 bg-blue-50 text-blue-900",
        amber: "border-amber-200 bg-amber-50 text-amber-900",
        rose: "border-rose-200 bg-rose-50 text-rose-900",
        slate: "border-slate-200 bg-slate-50 text-slate-900",
    };
    return (
        <div className={`rounded-2xl border p-4 ${tones[tone]}`}>
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="text-xs font-extrabold opacity-70">{label}</div>
                    <div className="mt-2 truncate text-xl font-black">{value}</div>
                    <div className="mt-1 text-xs font-semibold opacity-60">{hint}</div>
                </div>
                <span className="rounded-xl bg-white/70 p-2">
                    <Icon size={22} weight="duotone" />
                </span>
            </div>
        </div>
    );
}

function LoadingState() {
    return (
        <div className="space-y-4" data-testid="marketing-platform-loading">
            <div className="h-44 animate-pulse rounded-3xl bg-slate-200" />
            <div className="grid gap-3 md:grid-cols-3">
                {[0, 1, 2].map((key) => (
                    <div key={key} className="h-28 animate-pulse rounded-2xl bg-slate-100" />
                ))}
            </div>
            <div className="h-96 animate-pulse rounded-3xl bg-slate-100" />
        </div>
    );
}

export default function MarketingPlatformWorkspace({ provider }) {
    const navigate = useNavigate();
    const [overview, setOverview] = useState(() => normalizeIntegrationOverview({}));
    const [activity, setActivity] = useState({ runs: [], errors: [] });
    const [loading, setLoading] = useState(true);
    const [activityLoading, setActivityLoading] = useState(true);
    const [activeTab, setActiveTab] = useState("overview");
    const [testing, setTesting] = useState(false);
    const [syncing, setSyncing] = useState(false);
    const [error, setError] = useState("");

    const load = useCallback(async ({ silent = false } = {}) => {
        if (!silent) setLoading(true);
        setActivityLoading(true);
        setError("");
        const [overviewResult, activityResult] = await Promise.allSettled([
            getIntegrationsOverview(),
            getIntegrationsActivity({ limit: 50 }),
        ]);
        if (overviewResult.status === "fulfilled") {
            setOverview(overviewResult.value);
        } else {
            setError("تعذر تحميل حالة منصة الإعلانات.");
        }
        if (activityResult.status === "fulfilled") {
            setActivity(activityResult.value);
        }
        setActivityLoading(false);
        setLoading(false);
    }, []);

    useEffect(() => {
        load();
    }, [load, provider]);

    const integration = useMemo(
        () => overview.providers.find((row) => row.provider === provider) || null,
        [overview.providers, provider],
    );

    const providerActivity = useMemo(() => ({
        runs: (activity.runs || []).filter((row) => row.provider === provider),
        errors: (activity.errors || []).filter((row) => row.provider === provider),
    }), [activity, provider]);

    async function handleTest() {
        setTesting(true);
        try {
            const result = await testIntegrationConnection(provider);
            if (["passed", "healthy", "success", "succeeded"].includes(result?.status)) {
                toast.success("نجح فحص الربط المحلي");
            } else {
                toast.warning(result?.message || "اكتمل الفحص ويحتاج الربط إلى مراجعة");
            }
            await load({ silent: true });
        } catch (testError) {
            const detail = testError?.response?.data?.detail;
            toast.error(typeof detail === "string" ? detail : detail?.message || "تعذر اختبار الاتصال");
        } finally {
            setTesting(false);
        }
    }

    async function handleSync() {
        setSyncing(true);
        try {
            const result = await syncIntegrationData(provider, { days: 30 });
            if (result.status === "complete") {
                toast.success("اكتملت مزامنة البيانات التحليلية");
            } else if (result.status === "partial") {
                toast.warning("اكتملت المزامنة جزئيًا؛ راجع سجل الأخطاء");
            } else {
                toast.error("لم تكتمل مزامنة البيانات");
            }
            await load({ silent: true });
        } catch (syncError) {
            const detail = syncError?.response?.data?.detail;
            toast.error(typeof detail === "string" ? detail : detail?.message || "تعذر مزامنة البيانات");
        } finally {
            setSyncing(false);
        }
    }

    function openSettings() {
        const target = integration?.actions?.settings?.href
            || integration?.actions?.reconnect?.href;
        if (!target) {
            toast.info("إعدادات الربط لهذه المنصة غير متاحة من ميزان حتى الآن.");
            return;
        }
        navigate(target);
    }

    if (loading) return <LoadingState />;

    if (!integration) {
        return (
            <div className="rounded-3xl border border-amber-200 bg-amber-50 p-8 text-center" dir="rtl">
                <WarningCircle size={34} weight="duotone" className="mx-auto text-amber-700" />
                <h1 className="mt-3 text-xl font-black text-amber-950">منصة إعلانية غير معروفة</h1>
                <button
                    type="button"
                    onClick={() => navigate("/ads-manager")}
                    className="mt-5 rounded-xl bg-amber-900 px-5 py-3 font-extrabold text-white"
                >
                    العودة إلى جميع المنصات
                </button>
            </div>
        );
    }

    const healthy = integration.health?.status === "healthy";
    const connected = integration.connection_status === "connected";

    return (
        <div className="space-y-5" dir="rtl" data-testid="marketing-platform-workspace">
            <header className="overflow-hidden rounded-3xl border border-slate-800 bg-slate-950 text-white shadow-xl">
                <div className="grid gap-5 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                    <div>
                        <button
                            type="button"
                            onClick={() => navigate("/ads-manager")}
                            className="mb-4 inline-flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-3 py-2 text-xs font-extrabold text-slate-200 hover:border-emerald-300 hover:text-emerald-200"
                            data-testid="marketing-platform-back"
                        >
                            <ArrowRight size={16} weight="bold" />
                            جميع منصات الإعلانات
                        </button>
                        <div className="text-xs font-bold tracking-wide text-emerald-300">Mezan 2 · التسويق</div>
                        <h1 className="mt-1 text-2xl font-black sm:text-3xl" data-testid="marketing-platform-title">
                            {integration.name_ar}
                        </h1>
                        <p className="mt-2 max-w-3xl text-sm leading-6 text-slate-300">
                            صفحة المنصة الموحّدة لمراجعة حالة الربط، الحسابات، الصلاحيات، المزامنة والأخطاء.
                            هذه المرحلة للقراءة والمراقبة فقط ولا تنشئ أو تعدّل الحملات.
                        </p>
                    </div>
                    <button
                        type="button"
                        onClick={() => load({ silent: true })}
                        className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-emerald-200 px-5 text-sm font-black text-slate-950 transition hover:bg-emerald-100"
                        data-testid="marketing-platform-refresh"
                    >
                        <ArrowClockwise size={19} weight="bold" />
                        تحديث الحالة
                    </button>
                </div>
            </header>

            {error && (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800">
                    <WarningCircle size={20} weight="fill" className="ml-2 inline" />
                    {error}
                </div>
            )}

            <section className="grid gap-3 md:grid-cols-3" aria-label="ملخص المنصة">
                <MetricCard
                    label="حالة الربط"
                    value={statusLabel(integration.connection_status)}
                    hint={integration.connection_provenance || "مصدر الربط غير محسوم"}
                    tone={connected ? "emerald" : "amber"}
                    Icon={connected ? CheckCircle : WarningCircle}
                />
                <MetricCard
                    label="آخر مزامنة"
                    value={formatDate(integration.last_sync_at)}
                    hint={integration.data_delay_minutes === null ? "التأخير غير محسوم" : `تأخير ${integration.data_delay_minutes} دقيقة`}
                    tone="blue"
                    Icon={ArrowClockwise}
                />
                <MetricCard
                    label="صحة البيانات"
                    value={integration.health?.score === null ? "غير متاح" : `${integration.health.score}%`}
                    hint={integration.health?.data_quality || integration.health?.status || "غير محسوم"}
                    tone={healthy ? "emerald" : "rose"}
                    Icon={ChartLineUp}
                />
            </section>

            <nav className="flex gap-2 overflow-x-auto rounded-2xl border border-slate-200 bg-white p-2" aria-label="صفحات المنصة">
                {TABS.map(({ id, label, Icon }) => (
                    <button
                        key={id}
                        type="button"
                        onClick={() => setActiveTab(id)}
                        className={`inline-flex min-h-11 shrink-0 items-center gap-2 rounded-xl px-4 text-sm font-extrabold transition ${activeTab === id ? "bg-slate-950 text-white" : "text-slate-600 hover:bg-slate-50"}`}
                        aria-pressed={activeTab === id}
                        data-testid={`marketing-platform-tab-${id}`}
                    >
                        <Icon size={18} weight="duotone" />
                        {label}
                    </button>
                ))}
            </nav>

            {activeTab === "overview" && (
                <section data-testid="marketing-platform-overview">
                    <IntegrationCard
                        integration={integration}
                        testing={testing}
                        syncing={syncing}
                        settingsAvailable={Boolean(
                            integration.actions?.settings?.href
                            || integration.actions?.reconnect?.href
                        )}
                        onTest={handleTest}
                        onSync={handleSync}
                        onSettings={openSettings}
                    />
                </section>
            )}

            {activeTab === "capabilities" && (
                <CapabilityMatrix providers={[integration]} />
            )}

            {activeTab === "activity" && (
                <IntegrationActivityPanel
                    runs={providerActivity.runs}
                    errors={providerActivity.errors}
                    loading={activityLoading}
                />
            )}
        </div>
    );
}
