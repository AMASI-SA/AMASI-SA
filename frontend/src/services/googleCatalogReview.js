import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";

function unproven() {
    const error = new Error("تعذر إثبات مطابقة نتيجة الكتالوج لنسخة الخطة الحالية.");
    error.code = "GOOGLE_CATALOG_REVIEW_UNPROVEN";
    return error;
}

export function pinnedCatalogReviewScope(plan) {
    const id = plan?.plan_id, revision = plan?.revision;
    const pinned = plan?.fields?.pinned_product_ids, excluded = plan?.fields?.excluded_product_ids;
    if (typeof id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(id) || !Number.isSafeInteger(revision) || revision < 1
        || !Array.isArray(pinned) || !Array.isArray(excluded) || pinned.length > 100 || excluded.length > 100
        || [...pinned, ...excluded].some((value) => typeof value !== "string" || !/^[0-9]{1,30}$/.test(value))
        || new Set(pinned).size !== pinned.length || new Set(excluded).size !== excluded.length) throw unproven();
    return { planId: id, revision, productIds: pinned.filter((value) => !excluded.includes(value)) };
}

export function validateGoogleCatalogReview(result, scope) {
    const catalog = result?.catalog;
    if (result?.plan_id !== scope.planId || result?.plan_revision !== scope.revision || result?.provider_writes !== 0
        || result?.execution_authorized !== false || result?.source_verification !== "catalog_row_only"
        || !Array.isArray(result?.pinned_product_ids_reviewed) || result.pinned_product_ids_reviewed.join("|") !== scope.productIds.join("|")
        || !catalog || !Array.isArray(catalog.products) || !Array.isArray(catalog.missing_or_unverified_product_ids)
        || catalog.category_membership_verified !== false || catalog.variant_availability_verified !== false
        || catalog.source_writes !== 0 || catalog.execution_authorized !== false) throw unproven();
    const seen = new Set();
    for (const row of catalog.products) {
        if (!row || typeof row.product_id !== "string" || !scope.productIds.includes(row.product_id) || seen.has(row.product_id)
            || !["unknown", "hidden", "out_of_stock", "available"].includes(row.status)
            || !["verified", "unverified"].includes(row.verification)) throw unproven();
        seen.add(row.product_id);
    }
    for (const id of catalog.missing_or_unverified_product_ids) {
        if (typeof id !== "string" || !scope.productIds.includes(id) || seen.has(id)) throw unproven();
        seen.add(id);
    }
    if (seen.size !== scope.productIds.length) throw unproven();
    return result;
}

export async function getGoogleCatalogReview(plan, { signal } = {}) {
    const scope = pinnedCatalogReviewScope(plan);
    const result = (await api.get(`${GOOGLE_ADS_ROOT}/commercial-plans/${scope.planId}/catalog-review`, {
        params: { expected_revision: scope.revision }, signal,
    })).data;
    return validateGoogleCatalogReview(result, scope);
}
