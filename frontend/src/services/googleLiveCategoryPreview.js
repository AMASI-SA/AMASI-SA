import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";

const numericId = (value) => typeof value === "string" && /^[1-9][0-9]{0,29}$/.test(value);
const text = (value, limit = 240) => typeof value === "string" && value.length > 0 && value.length <= limit && value.trim() === value;
const list = (value, maximum) => Array.isArray(value) && value.length <= maximum;
const unique = (values) => new Set(values).size === values.length;
const instant = (value) => typeof value === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && !value.endsWith("-00:00") && Number.isFinite(Date.parse(value));
function httpsUrl(value) {
    if (!text(value, 2000) || /[\\\u0000-\u001f]/.test(value)) return false;
    try { const url = new URL(value); return url.protocol === "https:" && !!url.hostname && !url.username && !url.password && !url.hash && !url.port; } catch { return false; }
}
function unproved() { const error = new Error("تعذر إثبات مطابقة معاينة القسم للخطة المحفوظة."); error.code = "GOOGLE_LIVE_CATEGORY_PREVIEW_UNPROVED"; return error; }
function requireProof(condition) { if (!condition) throw unproved(); }

export function liveCategoryOptions(plan) {
    const fields = plan?.fields;
    requireProof(typeof plan?.plan_id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(plan.plan_id)
        && Number.isSafeInteger(plan.revision) && plan.revision > 0 && fields
        && list(fields.category_ids, 40) && fields.category_ids.every(numericId) && unique(fields.category_ids)
        && list(fields.excluded_product_ids, 100) && fields.excluded_product_ids.every((id) => text(id)) && unique(fields.excluded_product_ids)
        && (fields.category_url == null || httpsUrl(fields.category_url))
        && (fields.category_ids.length > 0 || fields.category_url));
    return { categoryIds: fields.category_ids, categoryUrl: fields.category_url ?? null };
}
export function liveCategoryScope(plan, categoryId) {
    const { categoryIds, categoryUrl } = liveCategoryOptions(plan);
    const selected = categoryId || (categoryIds.length === 1 ? categoryIds[0] : null);
    requireProof(selected ? numericId(selected) && categoryIds.includes(selected) : !!categoryUrl);
    return { planId: plan.plan_id, revision: plan.revision, categoryId: selected, categoryUrl, excludedIds: plan.fields.excluded_product_ids };
}
export function validateLiveCategoryPreview(data, scope) {
    try {
        requireProof(data?.plan_id === scope.planId && data.plan_revision === scope.revision && data.status === "live_membership_observation_only"
            && numericId(data.store_id) && numericId(data.category_id) && (!scope.categoryId || data.category_id === scope.categoryId)
            && (data.category_url === null || httpsUrl(data.category_url)) && (!scope.categoryUrl || data.category_url === scope.categoryUrl)
            && ["active", "hidden", "unknown"].includes(data.category_status) && instant(data.observed_started_at) && instant(data.observed_finished_at)
            && Date.parse(data.observed_started_at) <= Date.parse(data.observed_finished_at)
            && data.source === "salla_owned_category_products_fixed_get" && data.provider_revision === null && data.remote_snapshot_atomic === false
            && typeof data.pagination_traversal_complete === "boolean" && Number.isSafeInteger(data.pages_read) && data.pages_read >= 1 && data.pages_read <= 4
            && data.stored_scope_authority === "declared_or_unknown" && typeof data.response_digest === "string" && /^[a-f0-9]{64}$/.test(data.response_digest)
            && data.mezan_product_mapping_verified === false && data.inventory_verified === false && data.execution_authorized === false
            && list(data.products, 200) && data.products_observed === data.products.length && data.products.length <= data.pages_read * 100
            && list(data.unresolved_exclusion_ids, 100) && unique(data.unresolved_exclusion_ids)
            && list(data.limitations, 10) && data.limitations.length > 0 && data.limitations.every((value) => text(value, 1000)));
        const unresolved = scope.excludedIds.filter((id) => !numericId(id));
        requireProof(unresolved.length === data.unresolved_exclusion_ids.length && unresolved.every((id) => data.unresolved_exclusion_ids.includes(id)));
        const seen = new Set(), hidden = data.category_status === "unknown" ? null : data.category_status === "hidden";
        for (const row of data.products) {
            requireProof(numericId(row?.salla_product_id) && !seen.has(row.salla_product_id)
                && row.membership === "observed_category_endpoint" && row.excluded === scope.excludedIds.includes(row.salla_product_id)
                && row.category_hidden === hidden && ["sale", "out", "hidden", "unknown"].includes(row.product_status)
                && row.inventory === "unproved" && row.eligible_for_ad_selection === false);
            seen.add(row.salla_product_id);
        }
        return data;
    } catch { throw unproved(); }
}
export async function getGoogleLiveCategoryPreview(plan, { categoryId, signal } = {}) {
    const scope = liveCategoryScope(plan, categoryId), params = { expected_revision: scope.revision };
    if (scope.categoryId) params.category_id = scope.categoryId;
    const response = await api.get(`${GOOGLE_ADS_ROOT}/commercial-plans/${scope.planId}/live-category-preview`, { params, signal });
    return validateLiveCategoryPreview(response.data, scope);
}
