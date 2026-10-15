import api from "../lib/api";

const BASE = "/integrations-v2/snapchat_ads/decision-ledger";
const WINDOW_DAYS = [14, 7, 3, 2, 1];
const DIAGNOSTIC_METRICS = {
    sales: "sales_sar",
    sales_sar: "sales_sar",
    orders: "orders",
    contribution_profit: "contribution_profit_sar",
    contribution_profit_sar: "contribution_profit_sar",
    roas: "roas",
    cpa: "cpa_sar",
    cpa_sar: "cpa_sar",
};

function object(value) {
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function array(value) {
    return Array.isArray(value) ? value : [];
}

function text(value, fallback = "") {
    return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function number(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}

function firstObject(...values) {
    return values.map(object).find((value) => Object.keys(value).length) || {};
}

function normalizeWindows(value) {
    const source = Array.isArray(value)
        ? Object.fromEntries(value.map((row) => [String(row?.days || ""), row]))
        : object(value);
    const normalized = {};
    WINDOW_DAYS.forEach((days) => {
        const row = source[days]
            ?? source[String(days)]
            ?? source[`${days}d`]
            ?? source[`last_${days}_days`]
            ?? source[`days_${days}`];
        if (row !== undefined && row !== null) {
            const full = object(row);
            normalized[String(days)] = Object.keys(object(full.campaign)).length
                ? full.campaign
                : full;
        }
    });
    return normalized;
}

function normalizeProductComparisons(value) {
    const source = Array.isArray(value)
        ? Object.fromEntries(value.map((row) => [String(row?.days || ""), row]))
        : object(value);
    const normalized = {};
    WINDOW_DAYS.forEach((days) => {
        const row = object(source[days] ?? source[String(days)] ?? source[`${days}d`]);
        const comparisons = array(
            row.product_sales_comparison || row.product_comparison || row.products,
        );
        if (comparisons.length) normalized[String(days)] = comparisons;
    });
    return normalized;
}

function normalizeActual(evaluation, value) {
    const direct = firstObject(value.actual, evaluation.actual, evaluation.metrics);
    if (Object.keys(direct).length) return direct;
    const evidence = object(evaluation.evidence);
    const campaignDelta = firstObject(
        evaluation.campaign_delta,
        object(evaluation.deltas).campaign,
        object(evidence.deltas).campaign,
    );
    const actual = {};
    ["orders", "sales_sar", "contribution_profit_sar", "spend_sar", "roas", "cpa_sar"]
        .forEach((key) => {
            const metric = object(campaignDelta[key]);
            if (number(metric.actual) !== null) actual[key] = number(metric.actual);
        });
    return actual;
}

export function isDirectSnapchatDecision(value = {}) {
    const source = text(value.source || value.origin || value.change_source).toLowerCase();
    return value.external_change === true
        || value.provider_observed === true
        || value.actor_kind === "unknown_external"
        || object(value.evidence).provider_observation === true
        || source.includes("provider_observed")
        || [
            "snapchat_direct",
            "direct_snapchat",
            "provider_observed",
            "provider_external",
            "external",
        ].includes(source);
}

export function normalizeAdDecision(payload = {}) {
    const value = object(payload?.data || payload);
    const evidence = firstObject(value.evidence, value.decision_evidence, value.metrics_snapshot);
    const evaluation = firstObject(value.latest_evaluation, value.evaluation, value.business_outcome, value.outcome);
    const evaluationEvidence = object(evaluation.evidence);
    const evaluationDeltas = firstObject(evaluation.deltas, evaluationEvidence.deltas);
    const execution = firstObject(value.execution, value.provider_execution);
    const baseline = firstObject(value.baseline, value.baseline_windows, value.metrics_snapshot);
    const directSnapchat = isDirectSnapchatDecision(value);
    const recordedReason = text(value.reason || value.decision_reason || value.note);
    const reason = directSnapchat && (
        !recordedReason
        || recordedReason === "السبب غير مسجل؛ رُصد التغيير من Snapchat"
    ) ? "" : recordedReason;
    const source = text(value.source || value.origin || value.change_source,
        directSnapchat ? "snapchat_direct" : "mezan");

    return {
        decision_id: text(value.decision_id || value.entry_id || value.id || value.proposal_id),
        proposal_id: text(value.proposal_id) || null,
        account_id: text(value.account_id || value.ad_account_id),
        account_name: text(value.account_name || value.display_name),
        entity_type: text(value.entity_type || value.target_type || value.level, "campaign"),
        entity_id: text(value.entity_id || value.entity?.id || value.target_id || value.provider_entity_id),
        entity_name: text(
            value.entity_name
            || value.entity?.name
            || value.target_name
            || value.after?.name
            || value.after?.display_name
            || value.before?.name
            || value.before?.display_name
            || value.name,
        ),
        action: text(value.action || value.operation || value.change_type, "unknown"),
        source,
        direct_snapchat: directSnapchat,
        occurred_at: text(value.occurred_at || value.effective_at || value.changed_at || value.executed_at || value.created_at) || null,
        reason: reason || null,
        reason_unrecorded: !reason,
        before: firstObject(value.before, value.original_snapshot, value.previous, value.provider_before),
        after: firstObject(value.after, value.updated_snapshot, value.current, value.provider_after),
        changes: (() => {
            const planned = firstObject(value.changes, value.change_set, value.planned_changes, value.payload);
            if (Object.keys(planned).length) return planned;
            return Object.fromEntries(array(value.field_diffs).map((diff) => [
                text(diff?.field, "change"),
                { before: diff?.before, after: diff?.after },
            ]));
        })(),
        expected: firstObject(value.expected, value.expected_outcome, value.expectation),
        actual: (() => {
            const normalized = normalizeActual(evaluation, value);
            return Object.keys(normalized).length
                ? normalized
                : firstObject(value.actual_outcome);
        })(),
        execution: {
            ...execution,
            status: text(execution.status || value.execution_status || value.status, "unknown"),
        },
        outcome: {
            ...evaluation,
            status: text(evaluation.status || evaluation.outcome_status || value.outcome_status || value.evaluation_status, "pending"),
            verdict: text(evaluation.verdict || value.verdict) || null,
            evaluated_at: text(evaluation.evaluated_at || value.evaluated_at) || null,
            expected_vs_actual: firstObject(
                evaluation.expected_vs_actual,
                evaluation.expectation_assessment,
                evaluationEvidence.expected_vs_actual,
            ),
            deltas: {
                campaign: firstObject(
                    evaluation.campaign_delta,
                    object(evaluationDeltas).campaign,
                    object(evaluationEvidence.deltas).campaign,
                ),
                account: firstObject(
                    evaluation.account_delta,
                    object(evaluationDeltas).account,
                    object(evaluationEvidence.deltas).account,
                ),
                store: firstObject(
                    evaluation.store_delta,
                    object(evaluationDeltas).store,
                    object(evaluationEvidence.deltas).store,
                ),
            },
            post_attribution: firstObject(
                evaluation.post_attribution,
                evaluation.attribution_product_comparison,
                evaluationEvidence.post_attribution,
                evaluationEvidence.attribution_product_comparison,
            ),
        },
        evidence: {
            ...evidence,
            products: array(evidence.products || value.products || baseline.confirmed_product_links),
            inventory: array(baseline.inventory || evidence.inventory),
            product_link_state: text(evidence.product_link_state || value.product_link_state),
            windows: normalizeWindows(
                evidence.windows
                || baseline.windows
                || value.evidence_windows
                || value.metric_windows,
            ),
            product_comparison_windows: normalizeProductComparisons(
                baseline.windows
                || value.baseline_windows
                || evidence.windows
                || value.evidence_windows,
            ),
        },
        trend_override_reason: text(
            value.trend_override_reason
            || evidence.trend_override_reason
            || value.recent_trend_override_reason,
        ) || null,
        supporting_context: array(
            value.supporting_context
            || evidence.supporting_context
            || evidence.decision_evidence
            || baseline.supporting_context
            || value.contextual_evidence,
        ),
        annotations: array(value.annotations).map((item) => ({
            id: text(item?.annotation_id || item?.id),
            text: text(item?.text),
            annotated_at: text(item?.annotated_at || item?.created_at) || null,
            actor_kind: text(item?.actor_kind),
        })).filter((item) => item.text),
    };
}

export function normalizeAdDecisionAccount(payload = {}) {
    const value = object(payload);
    const recent = array(
        value.recent_decisions || value.latest_decisions || value.decisions || value.items,
    ).map(normalizeAdDecision).filter((row) => row.decision_id);
    return {
        account_id: text(value.account_id || value.ad_account_id),
        account_name: text(value.account_name || value.display_name || value.name),
        total: number(value.total ?? value.decision_count ?? value.total_decisions) || recent.length,
        last_changed_at: text(value.last_changed_at || value.latest_change_at || recent[0]?.occurred_at) || null,
        recent_decisions: recent,
    };
}

export function normalizeAdDecisionAccounts(payload = {}) {
    const value = object(payload?.data || payload);
    const rows = array(value.accounts || value.items || value.results || payload);
    return rows.map(normalizeAdDecisionAccount).filter((row) => row.account_id);
}

export function normalizeAdDecisionPage(payload = {}, requestedPage = 1, requestedLimit = 5) {
    const value = object(payload?.data || payload);
    const pagination = firstObject(value.pagination, value.meta);
    const items = array(value.decisions || value.entries || value.items || value.results || payload)
        .map(normalizeAdDecision)
        .filter((row) => row.decision_id);
    const page = Math.max(1, Math.trunc(number(pagination.page ?? value.page) || requestedPage));
    const limit = Math.max(1, Math.trunc(number(pagination.limit ?? value.limit) || requestedLimit));
    const total = Math.max(0, Math.trunc(number(pagination.total ?? value.total) || items.length));
    const pages = Math.max(
        items.length ? 1 : 0,
        Math.trunc(number(pagination.pages ?? value.pages) || Math.ceil(total / limit)),
    );
    return { items, pagination: { page, limit, total, pages } };
}

function normalizeStringList(value) {
    return array(value).map((item) => text(item)).filter(Boolean);
}

function normalizeDiagnosticDecision(value = {}) {
    const row = object(value);
    return {
        decision_id: text(row.decision_id || row.id) || null,
        account_id: text(row.account_id) || null,
        campaign_id: text(row.campaign_id) || null,
        entity_type: text(row.entity_type) || null,
        entity_id: text(row.entity_id) || null,
        action: text(row.action, "unknown"),
        effective_at: text(row.effective_at) || null,
        classification: text(row.classification, "insufficient"),
        confidence: number(row.confidence),
        measurement_scope: text(row.measurement_scope) || null,
        measured_change: object(row.measured_change),
        association_not_causation: row.association_not_causation !== false,
        caveats: normalizeStringList(row.caveats),
    };
}

export function normalizeAdBusinessDiagnosis(payload = {}) {
    const value = object(payload?.data || payload);
    return {
        source_mode: text(value.source_mode) || null,
        read_only: value.read_only === true,
        provider: text(value.provider, "snapchat_ads"),
        metric: text(value.metric, "sales_sar"),
        periods: object(value.periods),
        headline: object(value.headline),
        likely_contributors: array(value.likely_contributors)
            .map(normalizeDiagnosticDecision),
        decisions: array(value.decisions).map(normalizeDiagnosticDecision),
        caveats: normalizeStringList(value.caveats),
        coverage: object(value.coverage),
    };
}

function normalizeAdaptiveJudgment(value = {}) {
    const wrapper = object(value);
    const nested = object(wrapper.judgment);
    const row = Object.keys(nested).length ? nested : wrapper;
    return {
        source_mode: text(wrapper.source_mode || row.source_mode) || null,
        recommended_action: text(row.recommended_action, "observe"),
        entity_type: text(row.entity_type, "campaign"),
        entity_id: text(row.entity_id) || null,
        confidence: number(row.confidence),
        reason_ar: text(row.reason_ar) || null,
        primary_objective: text(row.primary_objective) || null,
        expected_outcome: array(row.expected_outcome).map(object),
        evidence_used: normalizeStringList(row.evidence_used),
        evidence_not_used: normalizeStringList(row.evidence_not_used),
        uncertainties: normalizeStringList(row.uncertainties),
        recent_improvement_treatment: text(row.recent_improvement_treatment) || null,
        safe_to_prepare_proposal: row.safe_to_prepare_proposal === true,
        provider_write_reached: wrapper.provider_write_reached === true,
        proposal_created: wrapper.proposal_created === true,
    };
}

export function normalizeAdaptiveSnapchatReview(payload = {}) {
    const value = object(payload?.data || payload);
    return {
        provider: text(value.provider, "snapchat_ads"),
        mode: text(value.mode, "supervised_shadow_learning"),
        objective: text(value.objective) || null,
        judgments: array(value.judgments).map(normalizeAdaptiveJudgment),
        proposals_created: Math.max(0, Math.trunc(number(value.proposals_created) || 0)),
        provider_write_reached: value.provider_write_reached === true,
    };
}

export async function getAdDecisionAccountSummaries({ limitPerAccount = 5, signal } = {}) {
    const limit = Math.min(5, Math.max(1, Math.trunc(Number(limitPerAccount) || 5)));
    const response = await api.get(`${BASE}/accounts`, {
        params: { limit_per_account: limit },
        signal,
    });
    return normalizeAdDecisionAccounts(response.data);
}

export async function getAdDecisionHistory({ accountId, page = 1, limit = 5, signal } = {}) {
    const normalizedPage = Math.max(1, Math.trunc(Number(page) || 1));
    const normalizedLimit = Math.min(5, Math.max(1, Math.trunc(Number(limit) || 5)));
    const response = await api.get(BASE, {
        params: {
            account_id: text(accountId),
            page: normalizedPage,
            limit: normalizedLimit,
        },
        signal,
    });
    return normalizeAdDecisionPage(response.data, normalizedPage, normalizedLimit);
}

export async function diagnoseAdBusinessChange({
    dateFrom,
    dateTo,
    metric = "sales",
    accountId,
    signal,
} = {}) {
    const normalizedMetric = DIAGNOSTIC_METRICS[text(metric).toLowerCase()] || "sales_sar";
    const params = {
        date_from: text(dateFrom),
        date_to: text(dateTo),
        metric: normalizedMetric,
    };
    if (text(accountId)) params.account_id = text(accountId);
    const response = await api.get(`${BASE}/diagnose`, { params, signal });
    return normalizeAdBusinessDiagnosis(response.data);
}

export async function reviewAdaptiveSnapchat({
    accountId,
    maxEntities = 5,
    userSuggestions = [],
    signal,
} = {}) {
    const suggestions = (Array.isArray(userSuggestions) ? userSuggestions : [userSuggestions])
        .map((value) => text(value))
        .filter(Boolean)
        .slice(0, 20);
    const payload = {
        max_entities: Math.min(5, Math.max(1, Math.trunc(Number(maxEntities) || 5))),
        user_suggestions: suggestions,
    };
    if (text(accountId)) payload.account_id = text(accountId);
    const response = await api.post(`${BASE}/adaptive-review`, payload, { signal });
    return normalizeAdaptiveSnapchatReview(response.data);
}

export function adDecisionError(error) {
    const detail = error?.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    if (typeof detail?.message === "string" && detail.message.trim()) return detail.message;
    return error?.message || "تعذر تحميل سجل تعديلات الإعلانات.";
}
