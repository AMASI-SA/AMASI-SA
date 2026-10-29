import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";
import { nativeInventoryScope } from "./googleNativeInventoryPreview";

const LANES = ["supported_test_review", "exploration_test_review", "needs_owner_fit_review", "needs_stock_review", "blocked"];
const DECISIONS = ["suitable_for_test", "not_suitable", "needs_review"];
const PERIODS = { last_14_days: [13, 0], previous_14_days: [27, 14], last_30_days: [29, 0], last_90_days: [89, 0] };
const array = (v, n) => Array.isArray(v) && v.length <= n;
const text = (v, n = 240) => typeof v === "string" && v.length > 0 && v.length <= n && v.trim() === v;
const stamp = (v) => typeof v === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(v) && !v.endsWith("-00:00") && Number.isFinite(Date.parse(v));
const hash = (v) => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
const same = (a, b) => Array.isArray(a) && a.length === b.length && a.every((v, i) => v === b[i]);
function failure() { const e = new Error("تعذر مطابقة مراجعة الاختبار لنطاق المالك والخطة والمنتجات."); e.code = "GOOGLE_PRODUCT_TEST_REVIEW_UNPROVED"; return e; }
function proof(v) { if (!v) throw failure(); }
function base(data, scope) { proof(data?.plan_id === scope.planId && data.plan_revision === scope.revision && data.category_id === scope.categoryId && data.provider_writes === 0 && data.execution_authorized === false); }
export function productTestReviewScope(plan, category, ids) {
    try { const scope = nativeInventoryScope(plan, category, ids); return { ...scope, selectedIds: [...scope.selectedIds], prior: plan.fields.prior_season_context ?? null }; } catch { throw failure(); }
}
export function validateProductFitState(data, scope) {
    base(data, scope); proof(data.review_writes === 0 && array(data.products, 5) && same(data.products.map((r) => r?.product_id), scope.selectedIds));
    for (const row of data.products) {
        proof(Number.isSafeInteger(row.review_revision) && row.review_revision >= 0 && row.review_revision <= 1000000 && typeof row.current_owner_review === "boolean"
            && row.meaning === "owner_declared_fit_not_independent_verification");
        proof(row.current_owner_review ? row.review_revision > 0 && DECISIONS.includes(row.decision) && text(row.reason, 1000) : row.decision === "unknown" && row.reason === null);
    }
    return data;
}
export function productFitPayload(scope, productId, revision, decision, reason, ack) {
    proof(scope.selectedIds.includes(productId) && Number.isSafeInteger(revision) && revision >= 0 && revision < 1000000 && DECISIONS.includes(decision) && text(reason, 1000) && ack === true);
    return { expected_plan_revision: scope.revision, expected_review_revision: revision, category_id: scope.categoryId, decision, reason, acknowledge_owner_review: true };
}
function validateSaved(data, scope, productId, body) {
    base(data, scope); proof(data.product_id === productId && data.review_revision === body.expected_review_revision + 1 && data.decision === body.decision && data.reason === body.reason
        && stamp(data.reviewed_at) && hash(data.review_digest) && data.meaning === "owner_declared_fit_not_independent_verification" && data.review_writes === 1);
    return data;
}
function riyadhDay(asOf, timeZone = "Asia/Riyadh") {
    proof(stamp(asOf)); const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(asOf));
    return ["year", "month", "day"].map((key) => parts.find((p) => p.type === key).value).join("-");
}
function date(value) { proof(typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value + "T00:00:00Z")) && new Date(value + "T00:00:00Z").toISOString().slice(0, 10) === value); return Date.parse(value + "T00:00:00Z"); }
function periods(data, scope) {
    const midnight = date(riyadhDay(data.as_of)), day = (offset) => new Date(midnight - (offset + 1) * 86400000).toISOString().slice(0, 10);
    proof(data.current_windows && Object.keys(data.current_windows).length === 4);
    for (const [key, [first, last]] of Object.entries(PERIODS)) { const p = data.current_windows[key]; proof(p && Object.keys(p).length === 3 && p.from === day(first) && p.to === day(last) && p.timezone === "Asia/Riyadh"); }
    if (scope.prior === null) proof(data.prior_windows === null);
    else { const p = data.prior_windows?.owner_reviewed_prior_season; proof(data.prior_windows && Object.keys(data.prior_windows).length === 1 && p && Object.keys(p).length === 3
        && p.from === scope.prior.date_from && p.to === scope.prior.date_to && p.timezone === "Asia/Riyadh" && date(p.to) >= date(p.from)
        && date(p.to) - date(p.from) <= 365 * 86400000 && p.to < riyadhDay(data.as_of)); }
}
function orderGroups(row, productId, windows) {
    proof(row?.product_id === productId && row.sales_native === null && row.profitability === null && row.growth_14_days_percent === null && row.success_classification === "not_established" && array(row.currency_groups, 200));
    const seen = new Set();
    for (const group of row.currency_groups) {
        proof(typeof group.currency_code === "string" && /^[A-Z]{3}$/.test(group.currency_code) && !seen.has(group.currency_code) && group.account_scope === "owner_store_projection" && group.platform_account_id === null
            && group.windows && same(Object.keys(group.windows).sort(), Object.keys(windows).sort())); seen.add(group.currency_code);
        for (const value of Object.values(group.windows)) {
            proof(value && Object.keys(value).length === 3);
            const missing = value.observed_orders === null && value.observed_lines === null && value.observed_units === null;
            const present = Number.isSafeInteger(value.observed_orders) && value.observed_orders > 0 && value.observed_orders <= 1000 && Number.isSafeInteger(value.observed_lines)
                && value.observed_lines >= value.observed_orders && value.observed_lines <= 100000 && typeof value.observed_units === "string" && /^[0-9]{1,20}(?:\.[0-9]{1,6})?$/.test(value.observed_units) && Number(value.observed_units) > 0;
            proof(missing || present);
        }
    }
}
function stock(value) {
    proof(value && (value.quantity === null || typeof value.quantity === "string" && /^[0-9]{1,18}(?:\.[0-9]{1,6})?$/.test(value.quantity)) && (value.unlimited === null || typeof value.unlimited === "boolean"));
    const positive = value.quantity !== null && /[1-9]/.test(value.quantity), zero = value.quantity !== null && !positive;
    proof(value.state === (value.unlimited === true || positive ? "positive" : zero && value.unlimited === null ? "zero_unlimited_unknown" : zero && value.unlimited === false ? "zero" : "unknown"));
}
function native(row, productId, data) {
    proof(row?.salla_product_id === productId && ["sale", "out", "hidden", "unknown"].includes(row.status) && (row.is_available === null || typeof row.is_available === "boolean")
        && row.globally_ready === false && row.size_coverage_verified === false && row.preparation_time === null && row.merchant_mapping_verified === false && row.eligible_for_ad_selection === false && row.execution_authorized === false
        && row.provider_revision === null && row.remote_snapshot_atomic === false && hash(row.source_digest) && stamp(row.observed_at) && Date.parse(row.observed_at) >= Date.parse(data.native_observed_started_at) && Date.parse(row.observed_at) <= Date.parse(data.native_observed_finished_at)
        && array(row.variants, 120) && typeof row.simple_product_observed === "boolean" && typeof row.variant_traversal_complete === "boolean" && array(row.blocking_reasons, 7));
    stock(row.parent_stock); const seen = new Set();
    for (const v of row.variants) { proof(typeof v.variant_id === "string" && /^[1-9][0-9]{0,29}$/.test(v.variant_id) && !seen.has(v.variant_id) && v.size_coverage_verified === false && v.purchasability_verified === false); seen.add(v.variant_id); stock(v.stock);
        proof(v.source_option_labels === null || array(v.source_option_labels, 5000));
        for (const label of v.source_option_labels || []) proof(label && [label.option_id, label.value_id].every((id) => typeof id === "string" && /^[1-9][0-9]{0,29}$/.test(id)) && (label.is_out_of_stock === null || typeof label.is_out_of_stock === "boolean")); }
    proof(!row.simple_product_observed || row.variant_traversal_complete && row.variants.length === 0);
}
function validateReview(data, scope, fitState) {
    validateProductFitState(fitState, scope); base(data, scope); periods(data, scope);
    proof(data.source_snapshot_atomic === false && data.source_coverage_complete === false && data.platform_conversions_added === false && data.profitability === null && data.growth === null && data.paid_launch_eligible === false
        && data.review_writes === 0 && data.source_writes === 0 && data.calendar_alignment_verification === "unknown" && data.remote_snapshot_atomic === false && data.cleanup_deadline_guaranteed === false
        && data.admission_scope === "process_local_not_cross_worker_singleflight" && stamp(data.operation_started_at) && stamp(data.operation_finished_at) && stamp(data.native_observed_started_at) && stamp(data.native_observed_finished_at)
        && Date.parse(data.as_of) === Date.parse(data.operation_started_at) && Date.parse(data.operation_started_at) <= Date.parse(data.native_observed_started_at) && Date.parse(data.native_observed_started_at) <= Date.parse(data.native_observed_finished_at) && Date.parse(data.native_observed_finished_at) <= Date.parse(data.operation_finished_at)
        && data.store_id === scope.storeId && hash(data.category_response_digest) && array(data.products, 5) && same(data.products.map((r) => r?.product_id), scope.selectedIds)
        && data.ordering === "review_readiness_lanes_owner_selection_order_not_performance_ranking" && array(data.review_lanes, 5) && same(data.review_lanes.map((r) => r.decision), LANES)
        && array(data.campaign_contexts, 570) && array(data.campaign_windows, 2280));
    const contexts = new Map();
    for (const c of data.campaign_contexts) {
        proof(hash(c?.context_id) && !contexts.has(c.context_id) && c.provider === "snapchat_ads" && [c.account_id, c.campaign_id, c.currency, c.timezone, c.attribution_key, c.date].every((v) => text(v))
            && /^[A-Z]{3}$/.test(c.currency) && c.product_attribution_verified === false && c.independent_store_orders === null && c.profitability === null && c.execution_authorized === false && c.live_freshness_verified === false
            && stamp(c.observed_at) && Date.parse(c.observed_at) <= Date.parse(data.as_of) && stamp(c.window_start_utc) && stamp(c.window_end_utc) && Date.parse(c.window_start_utc) < Date.parse(c.window_end_utc)
            && array(c.association_proofs, 500) && c.association_proofs.length > 0 && c.metrics && Object.keys(c.metrics).length === 3);
        for (const metric of Object.values(c.metrics)) proof(metric === null || typeof metric === "number" && Number.isFinite(metric) && metric > 0);
        for (const p of c.association_proofs) proof(p && scope.selectedIds.includes(p.product_id) && text(p.event_id));
        const localStart = riyadhDay(c.window_start_utc, c.timezone), localEnd = riyadhDay(c.window_end_utc, c.timezone);
        const midnight = (value) => new Intl.DateTimeFormat("en-GB", { timeZone: c.timezone, hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).format(new Date(value)) === "00:00:00";
        proof(localStart === c.date && date(localEnd) - date(localStart) === 86400000 && midnight(c.window_start_utc) && midnight(c.window_end_utc)
            && c.date < riyadhDay(data.as_of, c.timezone) && Date.parse(c.observed_at) >= Date.parse(c.window_end_utc)
            && stamp(c.snapshot_finished_at) && Date.parse(c.observed_at) <= Date.parse(c.snapshot_finished_at) && Date.parse(c.snapshot_finished_at) <= Date.parse(data.as_of)
            && (c.metrics.provider_reported_purchases === null || Number.isSafeInteger(c.metrics.provider_reported_purchases)));
        contexts.set(c.context_id, c);
    }
    let groups = 0;
    for (const row of data.products) {
        proof(LANES.includes(row.decision) && row.historical_cost_quality === "unknown" && row.profitability === null && row.growth === null && row.preparation_time === null && row.size_fit === "unknown"
            && row.approved_test_budget === "not_checked_requires_separate_owner_grant" && row.paid_launch_eligible === false && row.execution_authorized === false
            && array(row.evidence_reasons, 3) && new Set(row.evidence_reasons).size === row.evidence_reasons.length && row.evidence_reasons.every((r) => ["qualifying_saved_current_gross_lines", "qualifying_saved_prior_gross_lines_alignment_unknown", "effective_shared_campaign_context_not_product_performance"].includes(r))
            && array(row.campaign_context_refs, 570) && new Set(row.campaign_context_refs).size === row.campaign_context_refs.length && row.campaign_context_refs.every((ref) => contexts.get(ref)?.association_proofs.some((p) => p.product_id === row.product_id))
            && array(row.campaign_associations, 500) && array(row.missing, 20));
        const fit = fitState.products.find((r) => r.product_id === row.product_id), actual = row.season_fit;
        proof(fit.current_owner_review ? actual?.decision === fit.decision && actual.reason === fit.reason && actual.revision === fit.review_revision && actual.meaning === "owner_declared_fit_not_independent_verification" && stamp(actual.reviewed_at) && Date.parse(actual.reviewed_at) <= Date.parse(data.as_of) && hash(actual.review_digest)
            : actual?.decision === "unknown" && actual.meaning === "no_current_owner_review");
        native(row.inventory_observation, row.product_id, data); orderGroups(row.current_store_observations, row.product_id, data.current_windows); groups += row.current_store_observations.currency_groups.length;
        if (scope.prior === null) proof(row.prior_store_observations === null); else { orderGroups(row.prior_store_observations, row.product_id, data.prior_windows); groups += row.prior_store_observations.currency_groups.length; }
        for (const a of row.campaign_associations) proof(["snapchat_ads", "meta", "tiktok"].includes(a?.provider) && [a.account_id, a.campaign_id, a.event_id].every((v) => text(v)) && a.meaning === "intended_product_association_only" && a.feed_mapping_verified === false && array(a.missing, 10));
        const item = row.inventory_observation, blocked = item.status === "hidden" || item.status === "out" || item.is_available === false || item.blocking_reasons.includes("category_hidden_or_visibility_unproved") || actual.decision === "not_suitable";
        const positive = item.status === "sale" && item.is_available === true && (item.simple_product_observed && item.parent_stock.state === "positive" || item.variants.some((v) => v.stock.state === "positive" && !(v.source_option_labels || []).some((label) => label.is_out_of_stock === true)));
        const observed = (value) => value && value.currency_groups.some((g) => Object.values(g.windows).some((v) => v.observed_lines !== null && v.observed_lines > 0));
        const expectedReasons = [...(observed(row.current_store_observations) ? ["qualifying_saved_current_gross_lines"] : []), ...(observed(row.prior_store_observations) ? ["qualifying_saved_prior_gross_lines_alignment_unknown"] : []), ...(row.campaign_context_refs.length ? ["effective_shared_campaign_context_not_product_performance"] : [])];
        proof(same(row.evidence_reasons, expectedReasons));
        const expected = blocked ? "blocked" : actual.decision !== "suitable_for_test" ? "needs_owner_fit_review" : !positive ? "needs_stock_review" : row.evidence_reasons.length ? "supported_test_review" : "exploration_test_review";
        proof(row.decision === expected && row.history_status === (expectedReasons.length ? "qualified_observations_only_not_complete_history" : "no_qualified_observations_not_absence_of_sales"));
    }
    proof(groups <= 400);
    for (const lane of data.review_lanes) proof(same(lane.product_ids, data.products.filter((r) => r.decision === lane.decision).map((r) => r.product_id)));
    const windowKeys = new Set();
    for (const w of data.campaign_windows) {
        const scopeKeys = ["provider", "account_id", "campaign_id", "currency", "timezone", "attribution_key"];
        const key = JSON.stringify([...scopeKeys.map((k) => w[k]), w.window_id]);
        proof(w.provider === "snapchat_ads" && ["last14", "previous14", "last30", "last90"].includes(w.window_id) && !windowKeys.has(key)
            && w.source_coverage_complete === false && w.metrics_aggregated === false && w.comparison_status === "partial_context_only_no_growth_inference" && array(w.context_refs, 90) && new Set(w.context_refs).size === w.context_refs.length);
        windowKeys.add(key); const days = w.window_id === "last90" ? 90 : w.window_id === "last30" ? 30 : 14, offset = w.window_id === "previous14" ? 14 : 0;
        const today = date(riyadhDay(data.as_of, w.timezone)), end = today - (offset + 1) * 86400000, first = end - (days - 1) * 86400000;
        const from = new Date(first).toISOString().slice(0, 10), to = new Date(end).toISOString().slice(0, 10);
        const selected = data.campaign_contexts.filter((c) => scopeKeys.every((k) => c[k] === w[k]) && c.date >= from && c.date <= to);
        const allDays = Array.from({ length: days }, (_, i) => new Date(first + i * 86400000).toISOString().slice(0, 10));
        proof(w.date_from === from && w.date_to === to && w.expected_days === days && w.observed_context_days === selected.length && same(w.context_refs, selected.map((c) => c.context_id))
            && same(w.missing_dates, allDays.filter((day) => !selected.some((c) => c.date === day))));
    }
    return data;
}
export function validateProductTestReview(data, scope, fitState) { try { return validateReview(data, scope, fitState); } catch { throw failure(); } }
const endpoint = (scope) => `${GOOGLE_ADS_ROOT}/commercial-plans/${scope.planId}`;
export async function getProductFitState(plan, category, ids, { signal } = {}) {
    const scope = productTestReviewScope(plan, category, ids);
    return validateProductFitState((await api.get(endpoint(scope) + "/product-season-reviews", { params: { expected_revision: scope.revision, category_id: scope.categoryId, product_ids: scope.selectedIds }, signal })).data, scope);
}
export async function saveProductFit(plan, category, ids, productId, revision, decision, reason, ack, { signal } = {}) {
    const scope = productTestReviewScope(plan, category, ids), body = productFitPayload(scope, productId, revision, decision, reason, ack);
    return validateSaved((await api.put(endpoint(scope) + "/product-season-reviews/" + productId, body, { signal })).data, scope, productId, body);
}
export async function getProductTestReview(plan, category, ids, fitState, { signal } = {}) {
    const scope = productTestReviewScope(plan, category, ids); validateProductFitState(fitState, scope);
    return validateProductTestReview((await api.post(endpoint(scope) + "/product-test-review", { expected_revision: scope.revision, category_id: scope.categoryId, selected_product_ids: scope.selectedIds }, { signal })).data, scope, fitState);
}
