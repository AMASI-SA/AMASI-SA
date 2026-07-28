import api from "../lib/api";

export const ADS_PROVIDER_ORDER = Object.freeze(["snapchat", "tiktok", "meta"]);

export const ADS_PROVIDER_LABELS = Object.freeze({
    snapchat: "سناب شات",
    tiktok: "تيك توك",
    meta: "ميتا",
});

const SAFE_PROVIDERS = new Set(ADS_PROVIDER_ORDER);
const SAFE_CAMPAIGN_PROVIDERS = new Set(["tiktok", "meta"]);
const SAFE_FRESHNESS = new Set(["fresh", "delayed", "stale", "unavailable", "unknown"]);
const SAFE_CAMPAIGN_COVERAGE = new Set(["available", "aggregate_only", "unavailable"]);
const SAFE_PERFORMANCE_COVERAGE = new Set(["complete", "partial", "stale", "unavailable"]);
const SAFE_PERFORMANCE_REASONS = new Set([
    "source_unavailable",
    "source_truncated",
    "invalid_source_dates",
    "incomplete_spend",
    "missing_performance_dates",
    "stale_performance",
    "incomplete_revenue",
    "incomplete_conversions",
    "unverified_zero_performance",
]);
const SAFE_RECONCILIATION = new Set(["matched", "drift", "not_comparable", "no_data"]);
const SAFE_RECONCILIATION_BASIS = new Set([
    "account_day_aligned",
    "aggregate_period_only",
    "unavailable",
]);
const SAFE_RECONCILIATION_SEVERITY = new Set(["none", "info", "warning"]);
const SECRET_TEXT_RE = /(bearer\s+[a-z0-9._~+/=-]{8,}|access[\s_-]*token|refresh[\s_-]*token|client[\s_-]*secret|app[\s_-]*secret|api[\s_-]*key|(?:token|secret|authorization|password|cookie|credential)\s*[:=])/i;
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

const METRIC_KEYS = Object.freeze([
    "provider_reported_spend_sar",
    "booked_ad_expense_sar",
    "platform_attributed_revenue_sar",
    "platform_reported_purchases",
    "platform_reported_impressions",
    "platform_reported_clicks",
    "platform_roas",
    "platform_cpa_sar",
    "platform_cpc_sar",
    "platform_cpm_sar",
    "platform_ctr_pct",
]);

function safeText(value, fallback = "") {
    if (typeof value !== "string") return fallback;
    if (SECRET_TEXT_RE.test(value)) return "تم حجب تفاصيل حساسة";
    return value;
}

function nullableText(value) {
    const result = safeText(value).trim();
    return result || null;
}

function safeNumber(value, { min = null, max = null, integer = false } = {}) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return null;
    let bounded = parsed;
    if (min !== null) bounded = Math.max(min, bounded);
    if (max !== null) bounded = Math.min(max, bounded);
    return integer ? Math.trunc(bounded) : bounded;
}

function safePositiveInteger(value, fallback, { min = 0, max = 1000000 } = {}) {
    const parsed = safeNumber(value, { min, max, integer: true });
    return parsed === null ? fallback : parsed;
}

function normalizeMetrics(value) {
    const source = value && typeof value === "object" ? value : {};
    return METRIC_KEYS.reduce((metrics, key) => {
        metrics[key] = safeNumber(source[key], { min: 0 });
        return metrics;
    }, {});
}

function normalizeFreshness(value) {
    const source = value && typeof value === "object" ? value : {};
    return {
        last_observed_at: nullableText(source.last_observed_at),
        data_delay_minutes: safeNumber(source.data_delay_minutes, { min: 0 }),
        observed_days: safePositiveInteger(source.observed_days, 0, { min: 0, max: 366 }),
        requested_days: safePositiveInteger(source.requested_days, 0, { min: 0, max: 366 }),
        status: SAFE_FRESHNESS.has(source.status) ? source.status : "unknown",
    };
}

function normalizeCampaignCoverage(value) {
    const source = value && typeof value === "object" ? value : {};
    return {
        status: SAFE_CAMPAIGN_COVERAGE.has(source.status)
            ? source.status
            : "unavailable",
        campaign_count: safePositiveInteger(source.campaign_count, 0),
        source_rows: safePositiveInteger(source.source_rows, 0),
        detail: safeText(source.detail, "لا تتوفر تفاصيل كافية عن تغطية الحملات."),
    };
}

function normalizePerformanceCoverage(value, freshness) {
    const source = value && typeof value === "object" ? value : {};
    const observedDays = safePositiveInteger(
        source.observed_days,
        freshness.observed_days,
        { min: 0, max: 366 },
    );
    const requestedDays = safePositiveInteger(
        source.requested_days,
        freshness.requested_days,
        { min: 0, max: 366 },
    );
    const derivedStatus = freshness.status === "stale"
        ? "stale"
        : requestedDays > 0 && observedDays > 0 && observedDays < requestedDays
            ? "partial"
            : observedDays > 0
                ? "complete"
                : "unavailable";
    const status = SAFE_PERFORMANCE_COVERAGE.has(source.status)
        ? source.status
        : derivedStatus;
    const parsedCoveragePct = safeNumber(source.coverage_pct);
    const explicitCoveragePct = parsedCoveragePct !== null
        && parsedCoveragePct >= 0
        && parsedCoveragePct <= 100
        ? parsedCoveragePct
        : null;
    const coveragePct = explicitCoveragePct ?? (
        requestedDays > 0
            ? Math.round((Math.min(observedDays, requestedDays) / requestedDays) * 10000) / 100
            : null
    );
    const sourceHasEligibility = Object.prototype.hasOwnProperty.call(
        source,
        "eligible_for_ratios",
    );
    const coverageWindowAcceptable = requestedDays === 0
        || observedDays >= requestedDays
        || (
            requestedDays - observedDays === 1
            && freshness.status === "fresh"
        );
    const eligibleByEvidence = status === "complete"
        && !["stale", "unavailable"].includes(freshness.status)
        && observedDays > 0
        && coverageWindowAcceptable;
    const reasons = Array.isArray(source.reasons)
        ? source.reasons
            .filter((item) => SAFE_PERFORMANCE_REASONS.has(item))
            .slice(0, 10)
        : [];
    const missingSpendDates = Array.isArray(source.missing_spend_dates)
        ? source.missing_spend_dates
            .filter((item) => typeof item === "string" && ISO_DATE_RE.test(item))
            .slice(0, 90)
        : [];

    return {
        status,
        eligible_for_ratios: sourceHasEligibility
            ? source.eligible_for_ratios === true && eligibleByEvidence
            : eligibleByEvidence,
        observed_days: observedDays,
        requested_days: requestedDays,
        coverage_pct: coveragePct,
        missing_spend_dates: missingSpendDates,
        reasons,
        detail: safeText(
            source.detail,
            status === "complete"
                ? "تغطية الأداء مكتملة ضمن الفترة المطلوبة."
                : "بيانات الأداء لا تغطي الفترة المطلوبة بالكامل.",
        ),
    };
}

function normalizeReconciliation(value) {
    const source = value && typeof value === "object" ? value : {};
    const status = SAFE_RECONCILIATION.has(source.status)
        ? source.status
        : "not_comparable";
    const hasActionRequired = Object.prototype.hasOwnProperty.call(
        source,
        "action_required",
    );
    return {
        status,
        comparison_basis: SAFE_RECONCILIATION_BASIS.has(source.comparison_basis)
            ? source.comparison_basis
            : "unavailable",
        severity: SAFE_RECONCILIATION_SEVERITY.has(source.severity)
            ? source.severity
            : status === "drift"
                ? "warning"
                : status === "matched"
                    ? "none"
                    : "info",
        action_required: hasActionRequired
            ? source.action_required === true
            : status === "drift",
        provider_reported_spend_sar: safeNumber(
            source.provider_reported_spend_sar,
            { min: 0 },
        ),
        booked_ad_expense_sar: safeNumber(source.booked_ad_expense_sar, { min: 0 }),
        gap_sar: safeNumber(source.gap_sar),
        gap_pct: safeNumber(source.gap_pct),
        detail: safeText(source.detail, "لا تتوفر بيانات كافية للمطابقة."),
    };
}

function normalizeProvider(value) {
    const source = value && typeof value === "object" ? value : {};
    if (!SAFE_PROVIDERS.has(source.provider)) return null;
    const metricAvailability = source.metric_availability
        && typeof source.metric_availability === "object"
        ? source.metric_availability
        : {};
    const freshness = normalizeFreshness(source.freshness);
    return {
        provider: source.provider,
        provider_label: safeText(
            source.provider_label,
            ADS_PROVIDER_LABELS[source.provider],
        ),
        integration_provider: safeText(source.integration_provider),
        connection_status: safeText(source.connection_status, "unknown"),
        connection_provenance: safeText(source.connection_provenance, "unknown"),
        health_status: safeText(source.health_status, "unknown"),
        health_score: safeNumber(source.health_score, { min: 0, max: 100 }),
        last_sync_at: nullableText(source.last_sync_at),
        metrics: normalizeMetrics(source.metrics),
        freshness,
        performance_coverage: normalizePerformanceCoverage(
            source.performance_coverage,
            freshness,
        ),
        campaign_coverage: normalizeCampaignCoverage(source.campaign_coverage),
        reconciliation: normalizeReconciliation(source.reconciliation),
        metric_availability: Object.fromEntries(
            Object.entries(metricAvailability)
                .filter(([key]) => /^[a-z0-9_.-]+$/i.test(key))
                .map(([key, available]) => [key, available === true]),
        ),
    };
}

function normalizeDailySpend(value) {
    if (!Array.isArray(value)) return [];
    return value
        .filter((row) => row && typeof row === "object" && ISO_DATE_RE.test(row.date))
        .map((row) => ({
            date: row.date,
            snapchat: safeNumber(row.snapchat, { min: 0 }),
            tiktok: safeNumber(row.tiktok, { min: 0 }),
            meta: safeNumber(row.meta, { min: 0 }),
            booked_ad_expense_sar: safeNumber(row.booked_ad_expense_sar, { min: 0 }),
        }));
}

function normalizeCampaign(value) {
    const source = value && typeof value === "object" ? value : {};
    if (!SAFE_CAMPAIGN_PROVIDERS.has(source.provider)) return null;
    const campaignId = nullableText(source.campaign_id);
    if (!campaignId) return null;
    return {
        provider: source.provider,
        provider_label: safeText(
            source.provider_label,
            ADS_PROVIDER_LABELS[source.provider],
        ),
        account_id: nullableText(source.account_id),
        campaign_id: campaignId,
        campaign_name: safeText(source.campaign_name, campaignId),
        spend_reported: safeNumber(source.spend_reported, { min: 0 }),
        spend_currency: nullableText(source.spend_currency),
        spend_sar_equivalent: safeNumber(source.spend_sar_equivalent, { min: 0 }),
        revenue_reported: safeNumber(source.revenue_reported, { min: 0 }),
        revenue_sar_equivalent: safeNumber(source.revenue_sar_equivalent, { min: 0 }),
        purchases: safeNumber(source.purchases, { min: 0, integer: true }),
        impressions: safeNumber(source.impressions, { min: 0, integer: true }),
        clicks: safeNumber(source.clicks, { min: 0, integer: true }),
        roas: safeNumber(source.roas, { min: 0 }),
        cpa_reported: safeNumber(source.cpa_reported, { min: 0 }),
        cpc_reported: safeNumber(source.cpc_reported, { min: 0 }),
        cpm_reported: safeNumber(source.cpm_reported, { min: 0 }),
        ctr_pct: safeNumber(source.ctr_pct, { min: 0 }),
        spend_share_pct: safeNumber(source.spend_share_pct, { min: 0, max: 100 }),
        last_observed_date: nullableText(source.last_observed_date),
        data_source: safeText(source.data_source, "unknown"),
        currency_evidence: safeText(source.currency_evidence, "unknown"),
    };
}

function normalizeCoverage(value) {
    const source = value && typeof value === "object" ? value : {};
    const providersTotal = safePositiveInteger(
        source.providers_total,
        3,
        { min: 0, max: 3 },
    );
    return {
        revenue_is_partial: source.revenue_is_partial !== false,
        provider_spend_is_partial: source.provider_spend_is_partial !== false,
        booked_expense_is_partial: source.booked_expense_is_partial !== false,
        providers_with_performance_data: safePositiveInteger(
            source.providers_with_performance_data,
            0,
            { min: 0, max: 3 },
        ),
        providers_total: providersTotal,
        campaign_detail_providers: safePositiveInteger(
            source.campaign_detail_providers,
            0,
            { min: 0, max: 3 },
        ),
        revenue_providers: safePositiveInteger(source.revenue_providers, 0, { min: 0, max: 3 }),
        conversion_providers: safePositiveInteger(
            source.conversion_providers,
            0,
            { min: 0, max: 3 },
        ),
        click_providers: safePositiveInteger(source.click_providers, 0, { min: 0, max: 3 }),
        impression_providers: safePositiveInteger(
            source.impression_providers,
            0,
            { min: 0, max: 3 },
        ),
        ratio_eligible_providers: safePositiveInteger(
            source.ratio_eligible_providers,
            0,
            { min: 0, max: providersTotal },
        ),
        provider_spend_providers: safePositiveInteger(
            source.provider_spend_providers,
            0,
            { min: 0, max: 3 },
        ),
        booked_expense_providers: safePositiveInteger(
            source.booked_expense_providers,
            0,
            { min: 0, max: 3 },
        ),
        unscoped_booked_expense_sar: safeNumber(
            source.unscoped_booked_expense_sar,
            { min: 0 },
        ),
        source_row_limit_reached: Array.isArray(source.source_row_limit_reached)
            ? source.source_row_limit_reached
                .filter((item) => typeof item === "string")
                .map((item) => safeText(item))
                .filter(Boolean)
            : [],
        source_warnings: Array.isArray(source.source_warnings)
            ? source.source_warnings
                .filter((item) => typeof item === "string")
                .map((item) => safeText(item))
                .filter(Boolean)
                .slice(0, 10)
            : [],
    };
}

function normalizeInsight(value) {
    const source = value && typeof value === "object" ? value : {};
    return {
        code: safeText(source.code, "insight"),
        severity: ["info", "warning", "critical"].includes(source.severity)
            ? source.severity
            : "info",
        title: safeText(source.title, "ملاحظة تحليلية"),
        detail: safeText(source.detail),
        confidence: ["high", "medium", "low"].includes(source.confidence)
            ? source.confidence
            : "low",
    };
}

function normalizeSource(value) {
    const source = value && typeof value === "object" ? value : {};
    return {
        key: safeText(source.key),
        role: safeText(source.role),
        grain: safeText(source.grain),
        authoritative_for: Array.isArray(source.authoritative_for)
            ? source.authoritative_for
                .filter((item) => typeof item === "string")
                .map((item) => safeText(item))
            : [],
    };
}

export const OBSERVE_ONLY_POLICY = Object.freeze({
    mode: "observe_only",
    mutations_allowed: false,
    advertising_mutations_enabled: false,
    ai_can: Object.freeze(["قراءة البيانات", "تحليل المؤشرات"]),
    ai_cannot: Object.freeze([
        "إنشاء الحملات",
        "تعديل الحملات",
        "إيقاف الحملات",
        "تغيير الميزانيات",
    ]),
    lifecycle_required_for_future_writes: Object.freeze([]),
});

export function normalizeAdsManagerOverview(payload = {}) {
    const source = payload?.data && typeof payload.data === "object"
        ? payload.data
        : payload;
    const value = source && typeof source === "object" ? source : {};
    const providers = Array.isArray(value.providers)
        ? value.providers.map(normalizeProvider).filter(Boolean)
        : [];
    const byProvider = new Map(providers.map((row) => [row.provider, row]));
    const campaigns = Array.isArray(value.campaigns)
        ? value.campaigns.map(normalizeCampaign).filter(Boolean)
        : [];
    const pagination = value.campaign_pagination
        && typeof value.campaign_pagination === "object"
        ? value.campaign_pagination
        : {};
    const coverage = normalizeCoverage(value.coverage);
    coverage.ratio_eligible_providers = providers.filter(
        (provider) => provider.performance_coverage.eligible_for_ratios,
    ).length;
    const metrics = normalizeMetrics(value.metrics);
    if (
        providers.length === 0
        || coverage.ratio_eligible_providers < providers.length
    ) {
        for (const ratioKey of [
            "platform_roas",
            "platform_cpa_sar",
            "platform_cpc_sar",
            "platform_cpm_sar",
            "platform_ctr_pct",
        ]) {
            metrics[ratioKey] = null;
        }
    }

    return {
        generated_at: nullableText(value.generated_at),
        range: {
            date_from: ISO_DATE_RE.test(value?.range?.date_from || "")
                ? value.range.date_from
                : null,
            date_to: ISO_DATE_RE.test(value?.range?.date_to || "")
                ? value.range.date_to
                : null,
            timezone: safeText(value?.range?.timezone, "Asia/Riyadh"),
            provider: SAFE_PROVIDERS.has(value?.range?.provider)
                ? value.range.provider
                : "all",
        },
        metrics,
        coverage,
        providers: ADS_PROVIDER_ORDER.map((provider) => byProvider.get(provider)).filter(Boolean),
        daily_spend: normalizeDailySpend(value.daily_spend),
        campaigns,
        campaign_pagination: {
            page: safePositiveInteger(pagination.page, 1, { min: 1 }),
            limit: safePositiveInteger(pagination.limit, 25, { min: 10, max: 100 }),
            total: safePositiveInteger(pagination.total, campaigns.length),
            pages: safePositiveInteger(pagination.pages, campaigns.length ? 1 : 0),
        },
        insights: Array.isArray(value.insights)
            ? value.insights.map(normalizeInsight).slice(0, 12)
            : [],
        sources: Array.isArray(value.sources)
            ? value.sources.map(normalizeSource).filter((row) => row.key)
            : [],
        // Never trust a server-supplied write policy in the browser. Phase 1
        // remains read-only even if a compromised or future server says otherwise.
        policy: OBSERVE_ONLY_POLICY,
    };
}

function safeDate(value) {
    return typeof value === "string" && ISO_DATE_RE.test(value) ? value : undefined;
}

export async function getAdsManagerOverview({
    dateFrom,
    dateTo,
    provider = "all",
    campaignQuery = "",
    page = 1,
    limit = 25,
} = {}) {
    const query = typeof campaignQuery === "string"
        ? campaignQuery.trim().slice(0, 120)
        : "";
    const safeProvider = provider === "all" || SAFE_PROVIDERS.has(provider)
        ? provider
        : "all";
    const safePage = safePositiveInteger(page, 1, { min: 1 });
    const safeLimit = safePositiveInteger(limit, 25, { min: 10, max: 100 });
    const response = await api.get("/ads-manager/overview", {
        params: {
            date_from: safeDate(dateFrom),
            date_to: safeDate(dateTo),
            provider: safeProvider,
            campaign_query: query || undefined,
            page: safePage,
            limit: safeLimit,
        },
    });
    return normalizeAdsManagerOverview(response.data);
}
