import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";
import { pinnedCatalogReviewScope } from "./googleCatalogReview";

function unproved() { const error = new Error("تعذر مطابقة مشاهدات الموسم السابق للخطة المحفوظة."); error.code = "GOOGLE_PRIOR_SEASON_UNPROVED"; return error; }
function day(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(`${value}T00:00:00Z`))
        || new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) !== value) throw unproved();
    return Date.parse(`${value}T00:00:00Z`);
}
function timestamp(value) { return typeof value === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)); }
export function priorSeasonReviewScope(plan, ownerId) {
    let scope;
    try { scope = pinnedCatalogReviewScope(plan); } catch { throw unproved(); }
    const prior = plan?.fields?.prior_season_context;
    if (typeof ownerId !== "string" || !ownerId.trim() || ownerId.length > 128 || !prior || typeof prior !== "object" || Array.isArray(prior)
        || Object.keys(prior).sort().join() !== ["date_from", "date_to", "season_label", "alignment_note", "calendar_reference_text"].sort().join()) throw unproved();
    const first = day(prior.date_from), last = day(prior.date_to);
    if (last < first || last - first > 365 * 86400000 || day(plan.fields.starts_on) <= last
        || [[prior.season_label, 160], [prior.alignment_note, 1000]].some(([value, cap]) => typeof value !== "string" || !value.trim() || value.trim() !== value || value.length > cap)
        || (prior.calendar_reference_text !== null && (typeof prior.calendar_reference_text !== "string" || !prior.calendar_reference_text.trim() || prior.calendar_reference_text.trim() !== prior.calendar_reference_text || prior.calendar_reference_text.length > 500))) throw unproved();
    return { ...scope, ownerId, prior: { ...prior } };
}
export function validatePriorSeasonReview(response, scope) {
    const out = response?.observations, provenance = response?.owner_review_provenance;
    if (response?.plan_id !== scope.planId || response.plan_revision !== scope.revision || response.status !== "owner_declared_context_only"
        || response.calendar_alignment_verification !== "unknown" || response.growth !== null || response.profitability !== null
        || response.execution_authorized !== false || response.provider_writes !== 0 || response.source_writes !== 0
        || !response.prior_season_context || Object.keys(response.prior_season_context).length !== 5
        || Object.entries(scope.prior).some(([key, value]) => response.prior_season_context[key] !== value)
        || !Array.isArray(response.product_ids_reviewed) || response.product_ids_reviewed.join("|") !== scope.productIds.join("|")
        || provenance?.reviewed_by !== scope.ownerId || provenance.plan_revision !== scope.revision || !timestamp(provenance.reviewed_at)
        || provenance.meaning !== "owner_declared_context_not_independent_calendar_verification"
        || out?.mode !== "stored_prior_season_line_observations" || out.metric_basis !== "lower_bound_on_qualifying_saved_gross_line_observations_not_sales"
        || !timestamp(out.as_of) || Date.parse(provenance.reviewed_at) > Date.parse(out.as_of)
        || out.coverage_complete !== false || out.refunds_reconciled !== false || out.snapshot_consistent !== false || out.source_snapshot_immutable !== false
        || out.platform_conversions_added !== false || out.execution_authorized !== false || out.source_writes !== 0 || out.provider_writes !== 0
        || typeof out.truncated !== "boolean" || !Array.isArray(out.products) || out.products.length !== scope.productIds.length) throw unproved();
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(out.as_of));
    const today = ["year", "month", "day"].map((name) => parts.find((p) => p.type === name).value).join("-");
    const period = out.windows?.owner_reviewed_prior_season;
    if (scope.prior.date_to >= today || !out.windows || Object.keys(out.windows).length !== 1 || !period || Object.keys(period).length !== 3
        || period.from !== scope.prior.date_from || period.to !== scope.prior.date_to || period.timezone !== "Asia/Riyadh") throw unproved();
    const seen = new Set(); let total = 0;
    for (const product of out.products) {
        if (!scope.productIds.includes(product?.product_id) || seen.has(product.product_id) || !Array.isArray(product.currency_groups)
            || product.sales_native !== null || product.profitability !== null || product.growth_14_days_percent !== null || product.success_classification !== "not_established") throw unproved();
        seen.add(product.product_id); total += product.currency_groups.length;
        if (total > 200 || (out.truncated && product.currency_groups.length)) throw unproved();
        const currencies = new Set();
        for (const group of product.currency_groups) {
            const row = group.windows?.owner_reviewed_prior_season;
            if (typeof group.currency_code !== "string" || !/^[A-Z]{3}$/.test(group.currency_code) || currencies.has(group.currency_code)
                || group.account_scope !== "owner_store_projection" || group.platform_account_id !== null || !group.windows || Object.keys(group.windows).length !== 1
                || !row || Object.keys(row).length !== 3) throw unproved();
            currencies.add(group.currency_code);
            const missing = row.observed_orders === null && row.observed_lines === null && row.observed_units === null;
            const present = Number.isSafeInteger(row.observed_orders) && row.observed_orders > 0 && row.observed_orders <= 1000
                && Number.isSafeInteger(row.observed_lines) && row.observed_lines >= row.observed_orders && row.observed_lines <= 100000
                && typeof row.observed_units === "string" && /^[0-9]{1,20}(?:\.[0-9]{1,6})?$/.test(row.observed_units) && Number(row.observed_units) > 0;
            if (!missing && !present) throw unproved();
        }
    }
    return response;
}
export async function getGooglePriorSeasonReview(plan, { ownerId, signal } = {}) {
    const scope = priorSeasonReviewScope(plan, ownerId);
    const response = (await api.get(`${GOOGLE_ADS_ROOT}/commercial-plans/${scope.planId}/prior-season-observations`, { params: { expected_revision: scope.revision }, signal })).data;
    return validatePriorSeasonReview(response, scope);
}
