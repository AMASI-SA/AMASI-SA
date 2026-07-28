import { useEffect, useMemo, useState } from "react";
import {
    ArrowClockwise,
    CaretLeft,
    CaretRight,
    ChartLineUp,
    CheckCircle,
    Clock,
    Coins,
    CurrencyDollar,
    Database,
    MagnifyingGlass,
    ShieldCheck,
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
import DateInput, { isValidISODate } from "../components/DateInput";
import { monthStartSA, todaySA } from "../lib/dates";
import {
    ADS_PROVIDER_LABELS,
    getAdsManagerOverview,
    normalizeAdsManagerOverview,
} from "../services/adsManager";

const PLATFORM_OPTIONS = [
    { value: "all", label: "كل المنصات" },
    { value: "snapchat", label: "سناب شات" },
    { value: "tiktok", label: "تيك توك" },
    { value: "meta", label: "ميتا" },
];

const FRESHNESS_LABELS = {
    fresh: "حديثة",
    delayed: "متأخرة",
    stale: "قديمة",
    unavailable: "غير متاحة",
    unknown: "غير معروفة",
};

const FRESHNESS_TONES = {
    fresh: "border-emerald-200 bg-emerald-50 text-emerald-700",
    delayed: "border-amber-200 bg-amber-50 text-amber-700",
    stale: "border-rose-200 bg-rose-50 text-rose-700",
    unavailable: "border-slate-200 bg-slate-50 text-slate-600",
    unknown: "border-slate-200 bg-slate-50 text-slate-600",
};

const CAMPAIGN_COVERAGE_LABELS = {
    available: "تفاصيل الحملات متاحة",
    aggregate_only: "إجماليات فقط",
    unavailable: "تفاصيل الحملات غير متاحة",
};

const RECONCILIATION_STATUS_LABELS = {
    matched: "ضمن السماحية",
    drift: "فرق يحتاج مراجعة",
    not_comparable: "غير قابل للمقارنة",
    no_data: "لا توجد بيانات مقارنة",
};

const CONNECTION_PROVENANCE_LABELS = {
    api_connection: "ربط API مباشر",
    legacy_integration: "تكامل قائم سابقًا",
    data_feed: "تغذية بيانات فقط",
    disconnected: "غير مرتبط",
    planned: "مستقبلي",
    unknown: "مصدر غير محسوم",
};

function displayMoney(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "غير متاح";
    }
    return `${Number(value).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })} ر.س`;
}

function displayNumber(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "غير متاح";
    }
    return Number(value).toLocaleString("en-US");
}

function displayRatio(value, suffix = "") {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "غير متاح";
    }
    return `${Number(value).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })}${suffix}`;
}

function displayPercent(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "—";
    }
    return `${Number(value).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    })}%`;
}

function displaySignedMoney(value) {
    if (value === null || value === undefined || !Number.isFinite(Number(value))) {
        return "—";
    }
    const numeric = Number(value);
    const sign = numeric > 0 ? "+" : numeric < 0 ? "−" : "";
    return `${sign}${displayMoney(Math.abs(numeric))}`;
}

function displayDate(value) {
    if (!value) return "غير متاح";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleDateString("ar-SA", {
        year: "numeric",
        month: "short",
        day: "numeric",
    });
}

function displayDateTime(value) {
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

function metricClass(value) {
    return value === null || value === undefined
        ? "text-xl text-slate-500"
        : "text-2xl text-slate-950";
}

function MetricCard({ label, value, hint, Icon, tone, testid }) {
    const tones = {
        emerald: "border-emerald-100 bg-emerald-50/80 text-emerald-700",
        violet: "border-violet-100 bg-violet-50/80 text-violet-700",
        blue: "border-blue-100 bg-blue-50/80 text-blue-700",
        amber: "border-amber-100 bg-amber-50/80 text-amber-700",
    };
    return (
        <article
            className={`rounded-xl border p-4 ${tones[tone]}`}
            data-testid={testid}
        >
            <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                    <div className="text-xs font-black leading-5">{label}</div>
                    <div className={`mt-2 break-words font-mono font-black ${metricClass(value)}`}>
                        {value}
                    </div>
                </div>
                <span className="shrink-0 rounded-lg bg-white/80 p-2 shadow-sm">
                    <Icon size={22} weight="duotone" />
                </span>
            </div>
            <p className="mt-2 text-[11px] font-semibold leading-5 opacity-75">{hint}</p>
        </article>
    );
}

function CoverageStrip({ coverage }) {
    const partialWarnings = [
        coverage.provider_spend_is_partial && "الصرف المنصّي جزئي",
        coverage.booked_expense_is_partial && "المصروف المحاسبي جزئي",
        coverage.revenue_is_partial && "الإيراد المنسوب جزئي",
    ].filter(Boolean);
    const items = [
        {
            label: "مصادر الصرف المنصّي",
            value: `${coverage.provider_spend_providers}/${coverage.providers_total}`,
        },
        {
            label: "مصادر المصروف المحاسبي",
            value: `${coverage.booked_expense_providers}/${coverage.providers_total}`,
        },
        {
            label: "منصات بتفاصيل حملات",
            value: `${coverage.campaign_detail_providers}/${coverage.providers_total}`,
        },
        {
            label: "منصات ببيانات أداء",
            value: `${coverage.providers_with_performance_data}/${coverage.providers_total}`,
        },
        {
            label: "منصات صالحة لـ ROAS",
            value: `${coverage.ratio_eligible_providers}/${coverage.providers_total}`,
        },
    ];
    return (
        <section
            className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
            aria-labelledby="ads-coverage-heading"
            data-testid="ads-manager-coverage"
        >
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                    <h2 id="ads-coverage-heading" className="font-black text-slate-900">
                        تغطية البيانات
                    </h2>
                    <p className="mt-1 text-xs font-semibold text-slate-500">
                        كل نسبة تحسب المصادر التي قدمت دليلًا فعليًا ضمن الفترة.
                    </p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                    {partialWarnings.map((warning) => (
                        <span
                            key={warning}
                            className="rounded-full border border-amber-200 bg-amber-50 px-3 py-1 text-[11px] font-black text-amber-700"
                        >
                            {warning}
                        </span>
                    ))}
                </div>
            </div>
            <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
                {items.map((item) => (
                    <div key={item.label} className="rounded-lg bg-slate-50 px-3 py-3">
                        <div className="font-mono text-xl font-black text-slate-900">
                            {item.value}
                        </div>
                        <div className="mt-1 text-[11px] font-bold text-slate-500">
                            {item.label}
                        </div>
                    </div>
                ))}
            </div>
            {coverage.unscoped_booked_expense_sar !== null && (
                <p className="mt-3 rounded-lg border border-blue-100 bg-blue-50 px-3 py-2 text-xs font-bold text-blue-700">
                    مصروف محاسبي غير موزع على منصة:{" "}
                    <span className="font-mono">
                        {displayMoney(coverage.unscoped_booked_expense_sar)}
                    </span>
                </p>
            )}
            {coverage.source_row_limit_reached.length > 0 && (
                <p className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold leading-5 text-rose-700">
                    بلغ المصدر حد الصفوف أثناء القراءة؛ الأرقام قد تكون جزئية. المصادر:{" "}
                    <span className="font-mono">
                        {coverage.source_row_limit_reached.join("، ")}
                    </span>
                </p>
            )}
            {coverage.source_warnings.map((warning) => (
                <p
                    key={warning}
                    className="mt-3 rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-xs font-bold leading-5 text-rose-700"
                >
                    {warning}
                </p>
            ))}
        </section>
    );
}

function providerQuality(provider) {
    const coverage = provider.performance_coverage;
    if (coverage.status === "unavailable") {
        return {
            label: "بيانات الأداء غير متاحة",
            tone: "border-slate-200 bg-slate-50 text-slate-600",
        };
    }
    const isPartial = coverage.status === "partial"
        || (
            coverage.status === "stale"
            && coverage.requested_days > 0
            && coverage.observed_days < coverage.requested_days
        );
    const isStale = coverage.status === "stale"
        || provider.freshness.status === "stale";

    if (isPartial && isStale) {
        return {
            label: "بيانات جزئية وقديمة",
            tone: "border-rose-200 bg-rose-50 text-rose-700",
        };
    }
    if (isStale) {
        return {
            label: "بيانات قديمة",
            tone: "border-rose-200 bg-rose-50 text-rose-700",
        };
    }
    if (isPartial) {
        return {
            label: "بيانات جزئية",
            tone: "border-amber-200 bg-amber-50 text-amber-700",
        };
    }
    if (!coverage.eligible_for_ratios) {
        return {
            label: "غير صالحة للنسب",
            tone: "border-amber-200 bg-amber-50 text-amber-700",
        };
    }
    return {
        label: "بيانات مكتملة",
        tone: "border-emerald-200 bg-emerald-50 text-emerald-700",
    };
}

function PerformanceQualityNotice({ provider }) {
    const coverage = provider.performance_coverage;
    const quality = providerQuality(provider);
    const observed = coverage.requested_days
        ? `${coverage.observed_days}/${coverage.requested_days} يوم`
        : `${coverage.observed_days} يوم`;
    return (
        <div
            className={`mt-3 rounded-lg border px-3 py-2.5 text-xs ${quality.tone}`}
            data-testid={`ads-performance-quality-${provider.provider}`}
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-black">{quality.label}</span>
                <span className="font-mono font-black">
                    {observed}
                    {coverage.coverage_pct !== null
                        ? ` · ${displayPercent(coverage.coverage_pct)}`
                        : ""}
                </span>
            </div>
            {!coverage.eligible_for_ratios && (
                <p className="mt-1.5 font-bold leading-5">
                    مستبعدة من ROAS الإجمالي حتى تكتمل وتصبح حديثة.
                </p>
            )}
            {coverage.detail && (
                <p className="mt-1 font-semibold leading-5 opacity-80">
                    {coverage.detail}
                </p>
            )}
        </div>
    );
}

function SnapchatAccountCoverage({ accounts }) {
    if (!accounts?.length) {
        return (
            <div
                className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3"
                data-testid="ads-snapchat-account-coverage"
            >
                <span className="text-xs font-black text-slate-800">
                    تغطية حسابات سناب
                </span>
                <p className="mt-1 text-[11px] font-bold leading-5 text-slate-600">
                    لا تتوفر تغطية تفصيلية على مستوى الحسابات لهذه الفترة.
                </p>
            </div>
        );
    }
    return (
        <div
            className="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3"
            data-testid="ads-snapchat-account-coverage"
        >
            <div className="mb-2 flex items-center justify-between gap-2">
                <span className="text-xs font-black text-slate-800">
                    تغطية حسابات سناب
                </span>
                <span className="font-mono text-[11px] font-black text-slate-500">
                    عدد الحسابات: {accounts.length}
                </span>
            </div>
            <div className="space-y-2">
                {accounts.map((account) => {
                    const complete = account.status === "complete";
                    const missingConversions = Math.max(
                        account.requested_days - account.conversion_complete_days,
                        0,
                    );
                    const missingSpend = Math.max(
                        account.requested_days - account.spend_days,
                        0,
                    );
                    return (
                        <div
                            key={account.account_id}
                            className="rounded-md border border-slate-200 bg-white px-3 py-2"
                            data-testid={`ads-snap-account-${account.account_id}`}
                        >
                            <div className="flex flex-wrap items-center justify-between gap-2">
                                <span className="font-black text-slate-900">
                                    {account.account_name}
                                </span>
                                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-black ${complete
                                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                                    : "border-amber-200 bg-amber-50 text-amber-700"}`}
                                >
                                    {complete ? "مكتمل" : account.status === "partial" ? "جزئي" : "غير متاح"}
                                </span>
                            </div>
                            <div className="mt-1.5 grid grid-cols-2 gap-2 text-[11px] font-bold text-slate-600">
                                <span>
                                    الصرف:{" "}
                                    <b className="font-mono text-slate-900">
                                        {account.spend_days}/{account.requested_days}
                                    </b>
                                </span>
                                <span>
                                    التحويلات:{" "}
                                    <b className="font-mono text-slate-900">
                                        {account.conversion_complete_days}/{account.requested_days}
                                    </b>
                                </span>
                            </div>
                            {complete && account.current_day_lag_allowed && (
                                <p className="mt-1.5 text-[11px] font-bold leading-5 text-slate-600">
                                    اليوم الحالي ضمن نافذة التأخر المتوقعة.
                                </p>
                            )}
                            {!complete && (missingSpend > 0 || missingConversions > 0) && (
                                <p className="mt-1.5 text-[11px] font-bold leading-5 text-amber-700">
                                    ناقص: {missingSpend} يوم صرف، و{missingConversions} يوم تحويلات.
                                </p>
                            )}
                            {!complete && missingSpend === 0 && missingConversions === 0 && (
                                <p className="mt-1.5 text-[11px] font-bold leading-5 text-amber-700">
                                    {account.detail || "لا يمكن إثبات اكتمال هذا الحساب."}
                                </p>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}

function ReconciliationNotice({ provider }) {
    const reconciliation = provider.reconciliation;
    const approximate = reconciliation.comparison_basis === "aggregate_period_only";
    const exact = reconciliation.comparison_basis === "account_day_aligned";
    const warning = reconciliation.severity === "warning"
        || reconciliation.action_required;
    const tone = warning
        ? "border-rose-200 bg-rose-50 text-rose-700"
        : reconciliation.status === "matched"
            ? "border-emerald-200 bg-emerald-50 text-emerald-700"
            : "border-slate-200 bg-slate-50 text-slate-600";
    const basisLabel = approximate
        ? "فرق إجمالي للفترة — ليس مطابقة حساب × يوم"
        : exact
            ? "مطابقة على مستوى الحساب واليوم"
            : "أساس المقارنة غير متاح";
    return (
        <div
            className={`mt-3 rounded-lg border px-3 py-2.5 text-xs ${tone}`}
            data-testid={`ads-reconciliation-${provider.provider}`}
        >
            <div className="flex flex-wrap items-center justify-between gap-2">
                <span className="font-black">
                    {RECONCILIATION_STATUS_LABELS[reconciliation.status]}
                </span>
                {reconciliation.action_required && (
                    <span className="inline-flex items-center gap-1 font-black">
                        <WarningCircle size={15} weight="fill" />
                        يتطلب مراجعة
                    </span>
                )}
            </div>
            <p className="mt-1.5 font-bold">{basisLabel}</p>
            <p className="mt-1.5 font-semibold">
                فرق المنصة − المحاسبة:{" "}
                <span className="font-mono font-black">
                    {displaySignedMoney(reconciliation.gap_sar)}
                </span>
                {" · "}
                <span className="font-mono font-black">
                    {displayPercent(reconciliation.gap_pct)}
                </span>
            </p>
            {reconciliation.detail && (
                <p className="mt-1 font-semibold leading-5 opacity-80">
                    {reconciliation.detail}
                </p>
            )}
        </div>
    );
}

function ProviderFreshnessCard({ provider }) {
    const freshness = provider.freshness;
    const coverage = provider.performance_coverage;
    const observed = coverage.requested_days
        ? `${coverage.observed_days}/${coverage.requested_days} يوم`
        : `${coverage.observed_days} يوم`;
    return (
        <article
            className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
            data-testid={`ads-provider-${provider.provider}`}
        >
            <div className="flex items-start justify-between gap-3">
                <div>
                    <h3 className="font-black text-slate-950">{provider.provider_label}</h3>
                    <p className="mt-1 text-[11px] font-bold text-slate-500">
                        {CONNECTION_PROVENANCE_LABELS[provider.connection_provenance]
                            || "مصدر غير محسوم"}
                    </p>
                </div>
                <span className={`rounded-full border px-2.5 py-1 text-[11px] font-black ${FRESHNESS_TONES[freshness.status]}`}>
                    {FRESHNESS_LABELS[freshness.status]}
                </span>
            </div>
            <dl className="mt-4 grid grid-cols-2 gap-3 text-xs">
                <div>
                    <dt className="font-bold text-slate-500">آخر بيانات</dt>
                    <dd className="mt-1 font-black text-slate-800">
                        {displayDateTime(freshness.last_observed_at)}
                    </dd>
                </div>
                <div>
                    <dt className="font-bold text-slate-500">التغطية اليومية</dt>
                    <dd className="mt-1 font-mono font-black text-slate-800">{observed}</dd>
                </div>
                <div>
                    <dt className="font-bold text-slate-500">الصرف المنصّي</dt>
                    <dd className="mt-1 font-mono font-black text-slate-800">
                        {displayMoney(provider.metrics.provider_reported_spend_sar)}
                    </dd>
                </div>
                <div>
                    <dt className="font-bold text-slate-500">تفاصيل الحملات</dt>
                    <dd className="mt-1 font-black text-slate-800">
                        {CAMPAIGN_COVERAGE_LABELS[provider.campaign_coverage.status]}
                    </dd>
                </div>
            </dl>
            <PerformanceQualityNotice provider={provider} />
            {provider.provider === "snapchat" && (
                <SnapchatAccountCoverage
                    accounts={provider.account_performance_coverage}
                />
            )}
            <ReconciliationNotice provider={provider} />
        </article>
    );
}

function SpendChart({ points }) {
    return (
        <section
            className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5"
            aria-labelledby="daily-spend-heading"
            data-testid="ads-manager-daily-chart"
        >
            <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                    <h2 id="daily-spend-heading" className="font-black text-slate-950">
                        الصرف اليومي
                    </h2>
                    <p className="mt-1 text-xs font-semibold text-slate-500">
                        خطوط المنصات منفصلة عن المصروف المثبت محاسبيًا.
                    </p>
                </div>
                <span className="rounded-full bg-slate-100 px-3 py-1 text-[11px] font-black text-slate-600">
                    ر.س / يوم
                </span>
            </div>
            {points.length ? (
                <div className="mt-5 h-80 min-w-0" dir="ltr">
                    <ResponsiveContainer width="99%" height="100%" minWidth={0} minHeight={0}>
                        <LineChart data={points} margin={{ top: 8, right: 12, left: 8, bottom: 8 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                            <XAxis
                                dataKey="date"
                                tick={{ fontSize: 11, fill: "#64748b" }}
                                minTickGap={22}
                            />
                            <YAxis
                                tick={{ fontSize: 11, fill: "#64748b" }}
                                width={62}
                                tickFormatter={(value) => Number(value).toLocaleString("en-US")}
                            />
                            <Tooltip
                                formatter={(value, name) => [
                                    displayMoney(value),
                                    name,
                                ]}
                                labelFormatter={(value) => `التاريخ: ${value}`}
                                contentStyle={{
                                    direction: "rtl",
                                    borderRadius: 12,
                                    borderColor: "#e2e8f0",
                                    fontFamily: "Cairo",
                                }}
                            />
                            <Legend wrapperStyle={{ direction: "rtl", fontSize: 12 }} />
                            <Line
                                type="monotone"
                                dataKey="snapchat"
                                name="سناب شات"
                                stroke="#eab308"
                                strokeWidth={2}
                                connectNulls={false}
                                dot={false}
                            />
                            <Line
                                type="monotone"
                                dataKey="tiktok"
                                name="تيك توك"
                                stroke="#0f172a"
                                strokeWidth={2}
                                connectNulls={false}
                                dot={false}
                            />
                            <Line
                                type="monotone"
                                dataKey="meta"
                                name="ميتا"
                                stroke="#2563eb"
                                strokeWidth={2}
                                connectNulls={false}
                                dot={false}
                            />
                            <Line
                                type="monotone"
                                dataKey="booked_ad_expense_sar"
                                name="المصروف المحاسبي"
                                stroke="#7c3aed"
                                strokeWidth={2.5}
                                strokeDasharray="5 4"
                                connectNulls={false}
                                dot={false}
                            />
                        </LineChart>
                    </ResponsiveContainer>
                </div>
            ) : (
                <div className="mt-5 flex h-56 items-center justify-center rounded-xl border border-dashed border-slate-200 bg-slate-50 text-sm font-bold text-slate-500">
                    لا توجد نقاط يومية ضمن الفلاتر الحالية.
                </div>
            )}
        </section>
    );
}

function CampaignTable({ campaigns, pagination, loading, onPageChange }) {
    const currentPage = Math.max(1, pagination.page || 1);
    const totalPages = Math.max(0, pagination.pages || 0);
    return (
        <section
            className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-sm"
            aria-labelledby="campaign-table-heading"
            data-testid="ads-manager-campaigns"
        >
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-100 px-4 py-4 sm:px-5">
                <div>
                    <h2 id="campaign-table-heading" className="font-black text-slate-950">
                        الحملات المرصودة
                    </h2>
                    <p className="mt-1 text-xs font-semibold text-slate-500">
                        عرض تحليلي فقط · {displayNumber(pagination.total)} حملة
                    </p>
                </div>
                <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-[11px] font-black text-emerald-700">
                    <ShieldCheck size={14} weight="fill" />
                    بلا إجراءات تشغيلية
                </span>
            </div>
            <div className="overflow-x-auto">
                <table className="w-full min-w-[1050px] text-right text-xs">
                    <thead className="bg-slate-50 text-slate-600">
                        <tr>
                            <th className="px-4 py-3 font-black">المنصة</th>
                            <th className="px-4 py-3 font-black">الحملة</th>
                            <th className="px-4 py-3 font-black">الحساب</th>
                            <th className="px-4 py-3 font-black">الصرف (ر.س)</th>
                            <th className="px-4 py-3 font-black">الإيراد المنسوب</th>
                            <th className="px-4 py-3 font-black">المشتريات</th>
                            <th className="px-4 py-3 font-black">النقرات</th>
                            <th className="px-4 py-3 font-black">ROAS</th>
                            <th className="px-4 py-3 font-black">آخر رصد</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                        {campaigns.map((campaign) => (
                            <tr
                                key={`${campaign.provider}:${campaign.campaign_id}`}
                                className="text-slate-700 hover:bg-slate-50/70"
                            >
                                <td className="whitespace-nowrap px-4 py-3 font-black">
                                    {campaign.provider_label}
                                </td>
                                <td className="max-w-xs px-4 py-3">
                                    <div className="truncate font-black text-slate-900" title={campaign.campaign_name}>
                                        {campaign.campaign_name}
                                    </div>
                                    <div className="mt-1 font-mono text-[10px] text-slate-400">
                                        {campaign.campaign_id}
                                    </div>
                                </td>
                                <td className="px-4 py-3 font-mono text-[11px]">
                                    {campaign.account_id || "غير متاح"}
                                </td>
                                <td className="whitespace-nowrap px-4 py-3 font-mono font-black">
                                    {displayMoney(campaign.spend_sar_equivalent)}
                                    {campaign.spend_currency && campaign.spend_currency !== "SAR" && (
                                        <div className="mt-1 text-[10px] font-semibold text-slate-400">
                                            {displayNumber(campaign.spend_reported)} {campaign.spend_currency}
                                        </div>
                                    )}
                                </td>
                                <td className="whitespace-nowrap px-4 py-3 font-mono">
                                    {displayMoney(campaign.revenue_sar_equivalent)}
                                    {campaign.revenue_reported !== null
                                        && campaign.spend_currency
                                        && campaign.spend_currency !== "SAR" && (
                                        <div className="mt-1 text-[10px] font-semibold text-slate-400">
                                            {displayNumber(campaign.revenue_reported)}{" "}
                                            {campaign.spend_currency} أصلًا
                                        </div>
                                    )}
                                </td>
                                <td className="px-4 py-3 font-mono">
                                    {displayNumber(campaign.purchases)}
                                </td>
                                <td className="px-4 py-3 font-mono">
                                    {displayNumber(campaign.clicks)}
                                </td>
                                <td className="px-4 py-3 font-mono font-black">
                                    {displayRatio(campaign.roas, "×")}
                                </td>
                                <td className="whitespace-nowrap px-4 py-3">
                                    {displayDate(campaign.last_observed_date)}
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
                {!campaigns.length && (
                    <div className="flex min-h-40 items-center justify-center border-t border-slate-100 bg-slate-50/40 px-4 text-sm font-bold text-slate-500">
                        لا توجد حملات تطابق البحث والفترة والمنصة.
                    </div>
                )}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 sm:px-5">
                <div className="text-xs font-bold text-slate-500">
                    الصفحة <span className="font-mono text-slate-900">{currentPage}</span>
                    {" "}من{" "}
                    <span className="font-mono text-slate-900">{Math.max(totalPages, 1)}</span>
                </div>
                <div className="flex gap-2">
                    <button
                        type="button"
                        onClick={() => onPageChange(currentPage - 1)}
                        disabled={loading || currentPage <= 1}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-2 text-xs font-black text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                        data-testid="ads-campaigns-previous"
                    >
                        <CaretRight size={15} weight="bold" />
                        السابق
                    </button>
                    <button
                        type="button"
                        onClick={() => onPageChange(currentPage + 1)}
                        disabled={loading || totalPages === 0 || currentPage >= totalPages}
                        className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-3 py-2 text-xs font-black text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                        data-testid="ads-campaigns-next"
                    >
                        التالي
                        <CaretLeft size={15} weight="bold" />
                    </button>
                </div>
            </div>
        </section>
    );
}

function InsightList({ insights }) {
    if (!insights.length) return null;
    return (
        <section
            className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm sm:p-5"
            aria-labelledby="ads-insights-heading"
        >
            <h2 id="ads-insights-heading" className="font-black text-slate-950">
                ملاحظات تحليلية
            </h2>
            <div className="mt-3 grid gap-3 lg:grid-cols-2">
                {insights.map((insight) => {
                    const warning = insight.severity !== "info";
                    const Icon = warning ? WarningCircle : CheckCircle;
                    return (
                        <article
                            key={insight.code}
                            className={`rounded-lg border p-3 ${warning
                                ? "border-amber-100 bg-amber-50"
                                : "border-blue-100 bg-blue-50"}`}
                        >
                            <div className="flex items-start gap-2">
                                <Icon
                                    size={19}
                                    weight="fill"
                                    className={warning ? "text-amber-600" : "text-blue-600"}
                                />
                                <div>
                                    <h3 className="text-sm font-black text-slate-900">
                                        {insight.title}
                                    </h3>
                                    <p className="mt-1 text-xs font-semibold leading-5 text-slate-600">
                                        {insight.detail}
                                    </p>
                                </div>
                            </div>
                        </article>
                    );
                })}
            </div>
        </section>
    );
}

export function AdsManagerView({
    overview,
    filters,
    loading = false,
    error = "",
    onFilterChange = () => {},
    onApply = () => {},
    onRefresh = () => {},
    onPageChange = () => {},
}) {
    const metrics = overview.metrics;
    const roasExcludedProviders = overview.providers.filter(
        (provider) => !provider.performance_coverage.eligible_for_ratios,
    );
    const roasUnavailable = metrics.platform_roas === null
        || metrics.platform_roas === undefined;
    const roasHint = roasUnavailable && roasExcludedProviders.length
        ? `محجوب لأن بيانات ${roasExcludedProviders
            .map((provider) => provider.provider_label)
            .join(" و")} جزئية أو قديمة أو غير مكتملة.`
        : roasUnavailable
            ? "محجوب لعدم توفر صرف وإيراد موثوقين وقابلين للحساب."
            : "يُعرض فقط عند توفر صرف وإيراد حديثين ومكتملين لكل المنصات المحددة.";
    return (
        <div className="space-y-5" dir="rtl" data-testid="ads-manager-page">
            <header className="overflow-hidden rounded-xl border border-emerald-950 bg-emerald-950 text-white">
                <div className="grid gap-6 p-5 sm:p-7 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                    <div>
                        <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-emerald-700 bg-emerald-900 px-3 py-1 text-xs font-black text-emerald-100">
                            <ShieldCheck size={16} weight="fill" />
                            قراءة وتحليل فقط
                        </div>
                        <h1 className="text-2xl font-black tracking-tight sm:text-3xl">
                            مدير الإعلانات الموحّد
                        </h1>
                        <div className="mt-1 text-xs font-bold tracking-wide text-emerald-300">
                            Unified Ads Manager · Phase 1
                        </div>
                        <p className="mt-3 max-w-3xl text-sm font-semibold leading-6 text-emerald-100">
                            رؤية موحّدة للصرف والأداء مع فصل أرقام المنصات عن المصروف
                            المحاسبي. هذه المرحلة لا تنفّذ أي تغيير على الحملات.
                        </p>
                    </div>
                    <div className="flex flex-col items-start gap-2 lg:items-end">
                        <button
                            type="button"
                            onClick={onRefresh}
                            disabled={loading}
                            className="inline-flex items-center gap-2 rounded-lg border border-emerald-700 bg-emerald-900 px-4 py-2.5 text-sm font-black text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-60"
                            data-testid="ads-manager-refresh"
                        >
                            <ArrowClockwise
                                size={18}
                                weight="bold"
                                className={loading ? "animate-spin" : ""}
                            />
                            تحديث العرض
                        </button>
                        <span className="text-[11px] font-bold text-emerald-200">
                            آخر توليد: {displayDateTime(overview.generated_at)}
                        </span>
                    </div>
                </div>
            </header>

            <form
                onSubmit={onApply}
                className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm"
                data-testid="ads-manager-filters"
            >
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[1fr_1fr_1fr_1.5fr_auto] xl:items-end">
                    <label className="block">
                        <span className="mb-1.5 block text-xs font-black text-slate-600">
                            من تاريخ
                        </span>
                        <DateInput
                            value={filters.dateFrom}
                            max={todaySA()}
                            onChange={(event) => onFilterChange("dateFrom", event.target.value)}
                            data-testid="ads-filter-date-from"
                        />
                    </label>
                    <label className="block">
                        <span className="mb-1.5 block text-xs font-black text-slate-600">
                            إلى تاريخ
                        </span>
                        <DateInput
                            value={filters.dateTo}
                            max={todaySA()}
                            onChange={(event) => onFilterChange("dateTo", event.target.value)}
                            data-testid="ads-filter-date-to"
                        />
                    </label>
                    <label className="block">
                        <span className="mb-1.5 block text-xs font-black text-slate-600">
                            المنصة
                        </span>
                        <select
                            value={filters.provider}
                            onChange={(event) => onFilterChange("provider", event.target.value)}
                            className="h-[46px] w-full rounded-lg border border-slate-200 bg-white px-3 text-sm font-bold text-slate-800 outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
                            data-testid="ads-filter-provider"
                        >
                            {PLATFORM_OPTIONS.map((option) => (
                                <option key={option.value} value={option.value}>
                                    {option.label}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label className="block">
                        <span className="mb-1.5 block text-xs font-black text-slate-600">
                            بحث في الحملات
                        </span>
                        <span className="relative block">
                            <MagnifyingGlass
                                size={18}
                                className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400"
                            />
                            <input
                                type="search"
                                value={filters.campaignQuery}
                                maxLength={120}
                                onChange={(event) => onFilterChange("campaignQuery", event.target.value)}
                                placeholder="اسم الحملة أو رقمها"
                                className="h-[46px] w-full rounded-lg border border-slate-200 bg-white pr-10 pl-3 text-sm font-semibold text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100"
                                data-testid="ads-filter-campaign"
                            />
                        </span>
                    </label>
                    <button
                        type="submit"
                        disabled={loading}
                        className="inline-flex h-[46px] items-center justify-center gap-2 rounded-lg bg-emerald-700 px-5 text-sm font-black text-white transition hover:bg-emerald-800 disabled:cursor-not-allowed disabled:opacity-60"
                        data-testid="ads-filter-apply"
                    >
                        <ChartLineUp size={18} weight="bold" />
                        تطبيق وعرض
                    </button>
                </div>
            </form>

            {error && (
                <div
                    className="flex items-start gap-2 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm font-bold text-rose-700"
                    role="alert"
                    data-testid="ads-manager-error"
                >
                    <WarningCircle size={20} weight="fill" className="mt-0.5 shrink-0" />
                    <span>{error}</span>
                </div>
            )}

            <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="ملخص الإعلانات">
                <MetricCard
                    label="الصرف المبلّغ من المنصات"
                    value={displayMoney(metrics.provider_reported_spend_sar)}
                    hint="رقم تشغيلي مصدره تقارير منصات الإعلان."
                    Icon={ChartLineUp}
                    tone="emerald"
                    testid="ads-metric-provider-spend"
                />
                <MetricCard
                    label="مصروف الإعلانات المثبت محاسبيًا"
                    value={displayMoney(metrics.booked_ad_expense_sar)}
                    hint="رقم مالي مصدره قيود المصروف الإعلاني في الدفاتر."
                    Icon={Coins}
                    tone="violet"
                    testid="ads-metric-booked-expense"
                />
                <MetricCard
                    label="الإيراد المنسوب للمنصات"
                    value={displayMoney(metrics.platform_attributed_revenue_sar)}
                    hint="إسناد منصّي جزئي؛ لا يمثل صافي الربح."
                    Icon={CurrencyDollar}
                    tone="blue"
                    testid="ads-metric-attributed-revenue"
                />
                <MetricCard
                    label="ROAS المنصّي"
                    value={roasUnavailable ? "—" : displayRatio(metrics.platform_roas, "×")}
                    hint={roasHint}
                    Icon={Database}
                    tone="amber"
                    testid="ads-metric-roas"
                />
            </section>

            <CoverageStrip coverage={overview.coverage} />

            <section aria-labelledby="ads-freshness-heading">
                <div className="mb-3 flex items-center gap-2">
                    <Clock size={20} weight="duotone" className="text-slate-600" />
                    <h2 id="ads-freshness-heading" className="font-black text-slate-950">
                        حداثة المصادر
                    </h2>
                </div>
                {overview.providers.length ? (
                    <div className="grid gap-3 lg:grid-cols-3">
                        {overview.providers.map((provider) => (
                            <ProviderFreshnessCard key={provider.provider} provider={provider} />
                        ))}
                    </div>
                ) : (
                    <div className="rounded-xl border border-dashed border-slate-200 bg-white p-6 text-center text-sm font-bold text-slate-500">
                        لا توجد مصادر منصات ضمن الفلتر الحالي.
                    </div>
                )}
            </section>

            <SpendChart points={overview.daily_spend} />
            <InsightList insights={overview.insights} />
            <CampaignTable
                campaigns={overview.campaigns}
                pagination={overview.campaign_pagination}
                loading={loading}
                onPageChange={onPageChange}
            />

            <footer className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-xs font-semibold text-slate-600">
                <span>
                    الفترة: {overview.range.date_from || filters.dateFrom} —{" "}
                    {overview.range.date_to || filters.dateTo} · Asia/Riyadh
                </span>
                <a
                    href="/integrations-v2"
                    className="font-black text-emerald-700 hover:text-emerald-800 hover:underline"
                >
                    مراجعة مصادر الربط
                </a>
            </footer>
        </div>
    );
}

function errorMessage(error) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (typeof detail?.message === "string" && detail.message.trim()) {
        return detail.message;
    }
    return "تعذر تحميل مدير الإعلانات. تحقق من الاتصال ثم أعد المحاولة.";
}

function initialFilters() {
    return {
        dateFrom: monthStartSA(),
        dateTo: todaySA(),
        provider: "all",
        campaignQuery: "",
    };
}

export default function AdsManager() {
    const [filters, setFilters] = useState(initialFilters);
    const [request, setRequest] = useState(() => ({
        ...initialFilters(),
        page: 1,
        limit: 25,
        revision: 0,
    }));
    const [overview, setOverview] = useState(() => normalizeAdsManagerOverview({}));
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        let active = true;
        setLoading(true);
        setError("");
        getAdsManagerOverview(request)
            .then((result) => {
                if (active) setOverview(result);
            })
            .catch((requestError) => {
                if (active) setError(errorMessage(requestError));
            })
            .finally(() => {
                if (active) setLoading(false);
            });
        return () => {
            active = false;
        };
    }, [request]);

    const appliedProviderLabel = useMemo(
        () => ADS_PROVIDER_LABELS[request.provider] || "كل المنصات",
        [request.provider],
    );

    function handleFilterChange(field, value) {
        setFilters((current) => ({ ...current, [field]: value }));
    }

    function handleApply(event) {
        event.preventDefault();
        if (!isValidISODate(filters.dateFrom) || !isValidISODate(filters.dateTo)) {
            setError("أدخل فترة صحيحة بصيغة YYYY-MM-DD.");
            return;
        }
        if (filters.dateFrom > filters.dateTo) {
            setError("تاريخ النهاية يجب ألا يسبق تاريخ البداية.");
            return;
        }
        setRequest((current) => ({
            ...filters,
            page: 1,
            limit: current.limit,
            revision: current.revision + 1,
        }));
    }

    function handleRefresh() {
        setRequest((current) => ({
            ...current,
            revision: current.revision + 1,
        }));
    }

    function handlePageChange(page) {
        const pages = overview.campaign_pagination.pages || 1;
        if (page < 1 || page > pages) return;
        setRequest((current) => ({
            ...current,
            page,
            revision: current.revision + 1,
        }));
    }

    return (
        <>
            <span className="sr-only" aria-live="polite">
                {loading
                    ? `جارٍ تحميل بيانات ${appliedProviderLabel}`
                    : `تم تحميل بيانات ${appliedProviderLabel}`}
            </span>
            <AdsManagerView
                overview={overview}
                filters={filters}
                loading={loading}
                error={error}
                onFilterChange={handleFilterChange}
                onApply={handleApply}
                onRefresh={handleRefresh}
                onPageChange={handlePageChange}
            />
        </>
    );
}
