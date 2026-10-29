import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";
import { liveCategoryScope, validateLiveCategoryPreview } from "./googleLiveCategoryPreview";

const numericId = (value) => typeof value === "string" && /^[1-9][0-9]{0,29}$/.test(value);
const list = (value, maximum) => Array.isArray(value) && value.length <= maximum;
const unique = (values) => new Set(values).size === values.length;
const same = (a, b) => a.length === b.length && a.every((value, index) => value === b[index]);
const flag = (value) => value === null || typeof value === "boolean";
const digest = (value) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const instant = (value) => typeof value === "string" && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && !value.endsWith("-00:00") && Number.isFinite(Date.parse(value));
const BLOCKERS = ["global_stock_unproved", "preparation_time_unproved", "size_coverage_unproved", "paid_authority_absent"];
const OPTIONAL_BLOCKERS = ["native_visibility_or_availability_blocked_or_unknown", "native_option_value_out_of_stock", "category_hidden_or_visibility_unproved"];
function unproved() { const error = new Error("تعذر إثبات مطابقة مشاهدات المخزون للطلب المحدود."); error.code = "GOOGLE_NATIVE_INVENTORY_PREVIEW_UNPROVED"; return error; }
function requireProof(condition) { if (!condition) throw unproved(); }
function noAuthority(row) { return row?.globally_ready === false && row.size_coverage_verified === false && row.preparation_time === null && row.merchant_mapping_verified === false && row.execution_authorized === false && row.provider_revision === null && row.remote_snapshot_atomic === false; }
function stock(row) {
    requireProof(row && (row.quantity === null || typeof row.quantity === "string" && /^[0-9]{1,18}(?:\.[0-9]{1,6})?$/.test(row.quantity)) && flag(row.unlimited));
    const positive = row.quantity !== null && /[1-9]/.test(row.quantity), zero = row.quantity !== null && !positive;
    requireProof(row.state === (row.unlimited === true || positive ? "positive" : zero && row.unlimited === null ? "zero_unlimited_unknown" : zero && row.unlimited === false ? "zero" : "unknown"));
}
function customerUrl(value) {
    requireProof(typeof value === "string" && value.length <= 2000 && value === value.trim() && !/[\\\u0000-\u001f]/.test(value));
    const url = new URL(value); requireProof(url.protocol === "https:" && url.hostname && !url.username && !url.password && !url.hash && !url.port);
}
export function nativeInventoryOptions(plan, category) {
    const categoryId = plan?.fields?.category_ids?.length ? category?.category_id : undefined;
    const scope = liveCategoryScope(plan, categoryId);
    validateLiveCategoryPreview(category, scope);
    requireProof(scope.excludedIds.every(numericId) && category.unresolved_exclusion_ids.length === 0);
    return category.products;
}
export function nativeInventoryScope(plan, category, selectedIds) {
    const products = nativeInventoryOptions(plan, category);
    requireProof(list(selectedIds, 5) && selectedIds.length > 0 && selectedIds.every(numericId) && unique(selectedIds)
        && selectedIds.every((id) => products.some((row) => row.salla_product_id === id && row.excluded === false)));
    return { planId: plan.plan_id, revision: plan.revision, storeId: category.store_id, categoryId: category.category_id, selectedIds };
}
export function validateNativeInventoryPreview(data, scope) {
    try {
        requireProof(data?.plan_id === scope.planId && data.plan_revision === scope.revision && data.status === "native_stock_observations_only"
            && data.store_id === scope.storeId && data.category_id === scope.categoryId && digest(data.category_response_digest)
            && typeof data.category_traversal_complete === "boolean" && list(data.selected_product_ids, 5) && same(data.selected_product_ids, scope.selectedIds)
            && list(data.products, 5) && same(data.products.map((row) => row?.salla_product_id), scope.selectedIds)
            && instant(data.observed_started_at) && instant(data.observed_finished_at) && Date.parse(data.observed_started_at) <= Date.parse(data.observed_finished_at) && noAuthority(data));
        for (const product of data.products) {
            requireProof(noAuthority(product) && product.eligible_for_ad_selection === false && ["sale", "out", "hidden", "unknown"].includes(product.status)
                && flag(product.is_available) && digest(product.source_digest) && instant(product.observed_at)
                && Date.parse(product.observed_at) >= Date.parse(data.observed_started_at) && Date.parse(product.observed_at) <= Date.parse(data.observed_finished_at)
                && typeof product.variant_traversal_complete === "boolean" && typeof product.simple_product_observed === "boolean"
                && list(product.native_variant_page_digests, 2) && product.native_variant_page_digests.length > 0 && product.native_variant_page_digests.every(digest)
                && list(product.variants, 120) && product.variants.length <= product.native_variant_page_digests.length * 60
                && (!product.simple_product_observed || product.variant_traversal_complete && product.variants.length === 0)
                && list(product.blocking_reasons, 7) && unique(product.blocking_reasons) && BLOCKERS.every((reason) => product.blocking_reasons.includes(reason))
                && product.blocking_reasons.every((reason) => [...BLOCKERS, ...OPTIONAL_BLOCKERS].includes(reason)));
            customerUrl(product.customer_url); stock(product.parent_stock);
            const seen = new Set(); let optionOut = false;
            for (const variant of product.variants) {
                requireProof(numericId(variant?.variant_id) && !seen.has(variant.variant_id) && variant.size_coverage_verified === false && variant.purchasability_verified === false);
                seen.add(variant.variant_id); stock(variant.stock);
                requireProof(variant.source_option_labels === null || list(variant.source_option_labels, 5000));
                if (variant.source_option_labels !== null) {
                    const labels = new Set();
                    for (const label of variant.source_option_labels) {
                        requireProof(numericId(label?.option_id) && numericId(label.value_id) && !labels.has(label.value_id)
                            && [label.option_label, label.value_label].every((value) => value === null || typeof value === "string") && flag(label.is_out_of_stock));
                        labels.add(label.value_id); optionOut ||= label.is_out_of_stock === true;
                    }
                }
            }
            requireProof(product.blocking_reasons.includes("native_visibility_or_availability_blocked_or_unknown") === (product.status !== "sale" || product.is_available !== true)
                && product.blocking_reasons.includes("native_option_value_out_of_stock") === optionOut);
        }
        return data;
    } catch { throw unproved(); }
}
export async function getGoogleNativeInventoryPreview(plan, category, selectedIds, { signal } = {}) {
    const scope = nativeInventoryScope(plan, category, selectedIds);
    const response = await api.post(`${GOOGLE_ADS_ROOT}/commercial-plans/${scope.planId}/native-inventory-preview`, {
        expected_revision: scope.revision, category_id: scope.categoryId, selected_product_ids: scope.selectedIds,
    }, { signal });
    return validateNativeInventoryPreview(response.data, scope);
}
