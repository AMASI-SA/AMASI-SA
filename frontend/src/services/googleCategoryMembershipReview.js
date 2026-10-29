import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";
import { pinnedCatalogReviewScope } from "./googleCatalogReview";
const ID = /^[1-9][0-9]{0,29}$/;
function unproved() { const error = new Error("تعذر إثبات مطابقة مراجعة الأقسام لنسخة الخطة."); error.code = "GOOGLE_CATEGORY_REVIEW_UNPROVED"; return error; }
function timestamp(value) { return typeof value === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)); }
function safeUrl(value) { try { const parsed = new URL(value); return typeof value === "string" && value.length <= 2000 && !/\s/.test(value) && parsed.protocol === "https:" && !parsed.username && !parsed.password && !parsed.hash; } catch { return false; } }
export function categoryReviewScope(plan) {
    const base = pinnedCatalogReviewScope(plan), categories = plan.fields.category_ids ?? [], url = plan.fields.category_url ?? null;
    if (base.productIds.some((p) => !ID.test(p)) || !Array.isArray(categories) || categories.length > 40 || categories.some((c) => typeof c !== "string" || !ID.test(c)) || new Set(categories).size !== categories.length || url !== null && !safeUrl(url)) throw unproved();
    return { ...base, categoryIds: categories, categoryUrl: url };
}
export function validateCategoryMembershipReview(data, scope) {
    const review = data?.review;
    if (data?.plan_id !== scope.planId || data.plan_revision !== scope.revision || data.execution_authorized !== false || data.provider_writes !== 0 || !timestamp(data.observed_at)
        || !Array.isArray(data.product_ids_reviewed) || data.product_ids_reviewed.join("|") !== scope.productIds.join("|")
        || review?.scope !== "pinned_products_current_stored_category_membership" || review.execution_authorized !== false || review.provider_writes !== 0 || review.source_writes !== 0
        || review.remote_category_coverage_complete !== false || review.platform_catalog_mapping_verified !== false || review.category_url !== scope.categoryUrl
        || !Array.isArray(review.category_ids_reviewed) || review.category_ids_reviewed.join("|") !== scope.categoryIds.join("|") || !Array.isArray(review.products) || review.products.length !== scope.productIds.length) throw unproved();
    const seen = new Set();
    for (const row of review.products) {
        if (!scope.productIds.includes(row?.product_id) || seen.has(row.product_id) || !["observed_in_stored_source", "not_observed_in_stored_source", "unproved"].includes(row.membership)
            || !["unknown", "active", "hidden", "out_of_stock"].includes(row.catalog_visibility) || row.eligible_for_ad_selection !== false || row.execution_authorized !== false
            || row.remote_category_coverage_complete !== false || row.source_snapshot_immutable !== false || row.platform_catalog_mapping_verified !== false
            || !Array.isArray(row.matched_category_ids) || new Set(row.matched_category_ids).size !== row.matched_category_ids.length || row.matched_category_ids.some((c) => !scope.categoryIds.includes(c))
            || !Array.isArray(row.category_url_matched_ids) || new Set(row.category_url_matched_ids).size !== row.category_url_matched_ids.length || row.category_url_matched_ids.some((c) => !row.matched_category_ids.includes(c))) throw unproved();
        seen.add(row.product_id);
        if (row.membership === "unproved") {
            if (row.salla_product_id !== null || row.mezan_product_id !== null || row.observed_at !== null || row.source_ref !== null || row.matched_category_ids.length || row.category_url_matched_ids.length) throw unproved();
        } else {
            const age = Date.parse(data.observed_at) - Date.parse(row.observed_at);
            if (row.salla_product_id !== row.product_id || row.mezan_product_id !== `mpv2_${row.product_id}` || !timestamp(row.observed_at) || age < 0 || age > 86400000
                || typeof row.source_ref !== "string" || !/^category-observation:[a-f0-9]{64}$/.test(row.source_ref)
                || (row.membership === "observed_in_stored_source") !== (row.matched_category_ids.length > 0)) throw unproved();
        }
        const expectedUrlState = scope.categoryUrl === null ? "not_requested" : row.category_url_matched_ids.length ? "explicit_url_observed" : "not_verified";
        if (row.category_url_status !== expectedUrlState) throw unproved();
    }
    return data;
}
export async function getGoogleCategoryMembershipReview(plan, { signal } = {}) {
    const scope = categoryReviewScope(plan);
    return validateCategoryMembershipReview((await api.get(`${GOOGLE_ADS_ROOT}/commercial-plans/${scope.planId}/category-review`, { params: { expected_revision: scope.revision }, signal })).data, scope);
}
