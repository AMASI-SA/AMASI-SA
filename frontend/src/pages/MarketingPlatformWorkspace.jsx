import { useCallback, useEffect, useState } from "react";
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
import AdsPerformanceExplorer from "../components/marketing/AdsPerformanceExplorer";
import CampaignManagerTable from "../components/marketing/CampaignManagerTable";
import { isValidISODate } from "../components/DateInput";
import { todaySA } from "../lib/dates";
import {
    getMarketingPerformance,
    isMarketingPerformanceProvider,
    MARKETING_PLATFORM_CONFIG,
    MARKETING_PLATFORMS,
} from "../services/marketingPerformance";

export const MARKETING_PLATFORM_PROVIDERS = MARKETING_PLATFORMS;
export { isMarketingPerformanceProvider as isMarketingPlatformProvider };

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

function AccountSummaries({ accounts = [] }) {
    return (
        <section className="grid gap-4 lg:grid-cols-2" data-testid="marketing-account-summaries">
            {accounts.map((account) => (
                <article key={account.account_id} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
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
                            <div className="text-[10px] font-black text-slate-500">الصرف</div>
                            <div className="mt-1 font-mono font-black">{money(account.spend_sar)}</div>
                        </div>
                        <div className="rounded-xl bg-slate-50 p-3">
                            <div className="text-[10px] font-black text-slate-500">الطلبات</div>
                            <div className="mt-1 font-mono font-black">{numeric(account.orders)}</div>
                        </div>
                        <div className="rounded-xl bg-slate-50 p-3">
                            <div className="text-[10px] font-black text-slate-500">المبيعات</div>
                            <div className="mt-1 font-mono font-black">{money(account.sales_sar)}</div>
                        </div>
                        <div className="rounded-xl bg-slate-50 p-3">
                            <div className="text-[10px] font-black text-slate-500">ROAS</div>
                            <div className="mt-1 font-mono font-black">{ratio(account.roas, "×")}</div>
                        </div>
                    </div>
                </article>
            ))}
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
    const [activeTab, setActiveTab] = useState("overview");

    const load = useCallback(async ({ silent = false } = {}) => {
        if (silent) setRefreshing(true);
        else setLoading(true);
        setError("");
        try {
            const result = await getMarketingPerformance({
                platform,
                dateFrom: appliedRange.dateFrom,
                dateTo: appliedRange.dateTo,
                campaignQuery: appliedQuery,
                page,
                limit: 25,
            });
            setData(result);
        } catch (loadError) {
            const detail = loadError?.response?.data?.detail;
            setError(
                typeof detail === "string"
                    ? detail
                    : detail?.message || "تعذر تحميل تقرير المنصة الإعلانية.",
            );
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [appliedQuery, appliedRange, page, platform]);

    useEffect(() => {
        const currentToday = todaySA();
        setDateFrom(currentToday);
        setDateTo(currentToday);
        setAppliedRange({ dateFrom: currentToday, dateTo: currentToday });
        setPage(1);
        setAppliedQuery("");
        setQuery("");
        setActiveTab("overview");
    }, [platform]);

    useEffect(() => {
        load();
    }, [load]);

    const totals = data?.totals || {};
    const connection = data?.connection || {};
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
        setAppliedRange({ dateFrom, dateTo });
        setAppliedQuery(query.trim());
    }

    function applyDateRange(range) {
        if (!isValidISODate(range.dateFrom) || !isValidISODate(range.dateTo)) return;
        setDateFrom(range.dateFrom);
        setDateTo(range.dateTo);
        setPage(1);
        setAppliedRange(range);
    }

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
                            onClick={() => load({ silent: true })}
                            disabled={refreshing}
                            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-xl bg-emerald-200 px-5 text-sm font-black text-slate-950 transition hover:bg-emerald-100 disabled:opacity-60"
                            data-testid="marketing-platform-refresh"
                        >
                            <ArrowClockwise size={19} weight="bold" className={refreshing ? "animate-spin" : ""} />
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
                    <span className="mb-1 block text-xs font-black text-slate-600">بحث في الحملات</span>
                    <span className="relative block">
                        <MagnifyingGlass size={18} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400" />
                        <input
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder="اسم الحملة أو رقمها أو الحساب"
                            className="h-11 w-full rounded-xl border border-slate-200 bg-slate-50 pr-10 pl-3 text-sm outline-none focus:border-emerald-400 focus:bg-white"
                        />
                    </span>
                </label>
                <button type="submit" className="h-11 rounded-xl bg-slate-950 px-6 text-sm font-black text-white hover:bg-slate-800">
                    تطبيق التقرير
                </button>
            </form>

            {error && (
                <div className="rounded-2xl border border-rose-200 bg-rose-50 p-4 font-bold text-rose-800">
                    <WarningCircle size={20} weight="fill" className="ml-2 inline" />
                    {error}
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

            {activeTab === "overview" && <InsightPanel insights={data?.insights || []} />}

            {activeTab === "campaigns" && (
                <CampaignManagerTable
                    platform={platform}
                    platformLabel={config.label}
                    campaigns={data?.campaigns || []}
                    totals={totals}
                    pagination={pagination}
                    page={page}
                    onPageChange={setPage}
                    readOnly={data?.policy?.mutations_allowed !== true}
                />
            )}

            {activeTab === "accounts" && <AccountSummaries accounts={data?.accounts || []} />}

            {activeTab === "ai" && (
                <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
                    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                        <div className="flex items-center gap-3">
                            <span className="rounded-2xl bg-violet-100 p-3 text-violet-700">
                                <Robot size={26} weight="duotone" />
                            </span>
                            <div>
                                <h2 className="text-xl font-black text-slate-900">جاهزية ذكاء ميزان</h2>
                                <p className="mt-1 text-xs font-semibold text-slate-500">تتحقق الجاهزية من الدليل الفعلي، لا من مجرد وجود الربط.</p>
                            </div>
                        </div>
                        <div className="mt-5 grid gap-3 sm:grid-cols-2">
                            <ReadinessItem label="بيانات التقرير" ready={data?.ai_readiness?.report_ready} detail="وجود صفوف أداء كاملة دون تجاوز حد القراءة." />
                            <ReadinessItem label="هوية الحملات" ready={data?.ai_readiness?.campaign_identity_ready} detail="مطابقة معرف الحملة مع الاسم والحالة والميزانية." />
                            <ReadinessItem label="الصرف" ready={data?.ai_readiness?.spend_ready} detail="توفر صرف موثق بالريال ضمن الفترة." />
                            <ReadinessItem label="الطلبات" ready={data?.ai_readiness?.orders_ready} detail="توفر عدد مشتريات منسوب بواسطة المنصة." />
                            <ReadinessItem label="المبيعات" ready={data?.ai_readiness?.sales_ready} detail="توفر قيمة مشتريات منسوبة بواسطة المنصة." />
                            <ReadinessItem label="التحليل والمقارنة" ready={data?.ai_readiness?.ratios_ready} detail="إمكانية حساب ROAS وCPA دون خلط فترات ناقصة." />
                        </div>
                    </section>
                    <aside className="rounded-2xl border border-slate-800 bg-slate-950 p-5 text-white shadow-xl">
                        <div className="flex items-center gap-2 text-emerald-200">
                            <ShieldCheck size={22} weight="fill" />
                            <span className="font-black">حوكمة التنفيذ</span>
                        </div>
                        <h2 className="mt-4 text-xl font-black">الذكاء يحلل الآن، ولا ينفذ بعد</h2>
                        <p className="mt-2 text-sm font-semibold leading-6 text-slate-300">
                            إنشاء الحملات وتعديل الميزانية والإيقاف والاستئناف تبقى مقفلة حتى اكتمال دورة آمنة يمكن مراجعتها والتراجع عنها.
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
