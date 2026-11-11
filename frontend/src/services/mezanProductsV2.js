import api from "../lib/api";

export const RECENT_PRODUCTS_SYNC_INTERVAL_MS = 60 * 60 * 1000;
const RECENT_SYNC_RETRY_MS = 60 * 1000;
const RECENT_SYNC_REQUEST_TIMEOUT_MS = 105 * 1000;
let recentSyncScope = null;

function ownerSyncState(ownerId) {
    const id = ownerId == null ? "" : String(ownerId).trim();
    // Callers without an authenticated owner identity use server admission.
    // Never share a successful timestamp or pending response across users.
    if (!id) return null;
    if (recentSyncScope?.ownerId !== id) {
        recentSyncScope = { ownerId: id, nextAllowedAt: 0, promise: null };
    }
    return recentSyncScope;
}

export function getRecentProductsSyncNextAt(ownerId) {
    const id = ownerId == null ? "" : String(ownerId).trim();
    return id && recentSyncScope?.ownerId === id ? recentSyncScope.nextAllowedAt : null;
}

function nextRecentSyncAt(result, now) {
    if (result?.ok === true && result?.skipped !== true) {
        return now + RECENT_PRODUCTS_SYNC_INTERVAL_MS;
    }
    const serverTime = Date.parse(result?.next_retry_at || "");
    const maximum = result?.skipped === true && result?.reason === "up_to_date"
        ? RECENT_PRODUCTS_SYNC_INTERVAL_MS : 2 * RECENT_SYNC_RETRY_MS;
    return Number.isFinite(serverTime) && serverTime > now
        ? Math.min(serverTime, now + maximum)
        : now + RECENT_SYNC_RETRY_MS;
}

export async function syncRecentProductsV2({ force = false, ownerId = null } = {}) {
    const now = Date.now();
    const state = ownerSyncState(ownerId);
    if (state?.promise) return state.promise;
    if (!force && state && now < state.nextAllowedAt) return null;
    const request = api.post("/products-v2/sync-recent", undefined, {
        params: { force },
        timeout: RECENT_SYNC_REQUEST_TIMEOUT_MS,
        _mezanAuthDeadlineAt: now + RECENT_SYNC_REQUEST_TIMEOUT_MS,
    }).then((response) => {
        if (state) state.nextAllowedAt = nextRecentSyncAt(response.data, Date.now());
        return response.data;
    }).catch((error) => {
        if (state) state.nextAllowedAt = Date.now() + RECENT_SYNC_RETRY_MS;
        throw error;
    }).finally(() => {
        if (state) state.promise = null;
    });
    if (state) state.promise = request;
    return request;
}

export async function listProductsV2({ page = 1, perPage = 30, query = "", status = "" } = {}) {
    const params = { page, per_page: perPage };
    if (query.trim()) params.q = query.trim();
    if (status) params.status = status;
    return (await api.get("/products-v2", { params })).data;
}

export async function listWorkspaceProducts({
    page = 1,
    perPage = 30,
    query = "",
    status = "",
    sort = "newest",
    missingSku = false,
    missingMezanCost = false,
    soldOnly = false,
    fromDate = "",
    toDate = "",
    paymentMethods = "",
    shippingCompanies = "",
    productIds = "",
} = {}) {
    if (page === 1 && sort === "newest" && !query.trim()) {
        try { await syncRecentProductsV2(); } catch { /* keep local listing available */ }
    }
    const params = {
        page,
        per_page: perPage,
        sort,
        missing_sku: missingSku,
        missing_mezan_cost: missingMezanCost,
        sold_only: soldOnly,
    };
    if (query.trim()) params.q = query.trim();
    if (status) params.status = status;
    if (fromDate) params.from = fromDate;
    if (toDate) params.to = toDate;
    if (paymentMethods) params.payment_methods = paymentMethods;
    if (shippingCompanies) params.shipping_companies = shippingCompanies;
    if (productIds) params.product_ids = productIds;
    const endpoint = missingMezanCost && soldOnly
        ? "/products-v2/workspace/sold-missing-cost-products"
        : "/products-v2/workspace/products";
    return (await api.get(endpoint, { params })).data;
}

export async function getProductsV2Summary() { return (await api.get("/products-v2/summary")).data; }
export async function syncProductsV2() { return (await api.post("/products-v2/sync")).data; }
export async function getProductV2(productId) { return (await api.get(`/products-v2/${encodeURIComponent(productId)}`)).data; }
export async function refreshProductV2Details(productId) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/refresh-details`)).data; }
export async function getProductV2Costs(productId) { return (await api.get(`/products-v2/${encodeURIComponent(productId)}/costs`)).data; }
export async function saveProductV2Costs(productId, payload) { return (await api.put(`/products-v2/${encodeURIComponent(productId)}/costs`, payload)).data; }
export async function getProductImageProfile(productId) { return (await api.get(`/products-v2/${encodeURIComponent(productId)}/image-profile`)).data; }
export async function saveProductImageProfile(productId, payload) { return (await api.put(`/products-v2/${encodeURIComponent(productId)}/image-profile`, payload)).data; }
export async function getProductOptionCosts(productId) { return (await api.get(`/products-v2/${encodeURIComponent(productId)}/option-costs`)).data; }
export async function saveProductOptionCost(productId, optionId, valueId, payload) { return (await api.put(`/products-v2/${encodeURIComponent(productId)}/option-costs/${encodeURIComponent(optionId)}/${encodeURIComponent(valueId)}`, payload)).data; }
export async function deleteProductOptionCost(productId, optionId, valueId) { return (await api.delete(`/products-v2/${encodeURIComponent(productId)}/option-costs/${encodeURIComponent(optionId)}/${encodeURIComponent(valueId)}`)).data; }
export async function calculateProductCost(productId, selectedOptions) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/calculate-cost`, { selected_options: selectedOptions })).data; }
export async function getProductOperations(productId) { return (await api.get(`/products-v2/${encodeURIComponent(productId)}/operations`)).data; }
export async function saveProductOperationProfile(productId, payload) { return (await api.put(`/products-v2/${encodeURIComponent(productId)}/operations/profile`, payload)).data; }
export async function linkProductResource(productId, resourceId, quantity = 1) { return (await api.put(`/products-v2/${encodeURIComponent(productId)}/resource-links/${encodeURIComponent(resourceId)}`, { quantity })).data; }
export async function unlinkProductResource(productId, resourceId) { return (await api.delete(`/products-v2/${encodeURIComponent(productId)}/resource-links/${encodeURIComponent(resourceId)}`)).data; }
export async function linkProductGroups(productId, groupIds) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/group-links`, { group_ids: groupIds })).data; }
export async function unlinkProductGroup(productId, groupId) { return (await api.delete(`/products-v2/${encodeURIComponent(productId)}/group-links/${encodeURIComponent(groupId)}`)).data; }
export async function listProductCreationDrafts(params = {}) { return (await api.get("/products-v2/creation-drafts", { params })).data; }
export async function createProductCreationDraft(payload) { return (await api.post("/products-v2/creation-drafts", payload)).data; }
export async function updateProductCreationDraft(draftId, payload) { return (await api.put(`/products-v2/creation-drafts/${encodeURIComponent(draftId)}`, payload)).data; }
export async function previewProductCreationDraft(draftId) { return (await api.post(`/products-v2/creation-drafts/${encodeURIComponent(draftId)}/preview`)).data; }
export async function approveProductCreationDraft(draftId) { return (await api.post(`/products-v2/creation-drafts/${encodeURIComponent(draftId)}/approve`)).data; }
export async function publishProductCreationDraft(draftId) { return (await api.post(`/products-v2/creation-drafts/${encodeURIComponent(draftId)}/publish`, { confirmation: "إنشاء المنتج في سلة" })).data; }
export async function getSallaCategoryCatalog() { return (await api.get("/products-v2/category-catalog")).data; }

export async function previewMissingSkus({ prefix = "AMS", width = 5, limit = 20 } = {}) {
    return (await api.get("/products-v2/workspace/sku/preview", { params: { prefix, width, limit } })).data;
}
export async function applyMissingSkus({ prefix = "AMS", width = 5, limit = 50, confirmation }) {
    return (await api.post("/products-v2/workspace/sku/apply", { prefix, width, limit, confirmation })).data;
}

export async function getProductControlCenter(productId) { return (await api.get(`/products-v2/${encodeURIComponent(productId)}/control-center`)).data; }
export async function saveProductControlDraft(productId, payload) { return (await api.put(`/products-v2/${encodeURIComponent(productId)}/control-center/draft`, payload)).data; }
export async function approveProductControlDraft(productId, draftId) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/control-center/draft/${encodeURIComponent(draftId)}/approve`)).data; }
export async function publishProductControlDraft(productId, draftId) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/control-center/draft/${encodeURIComponent(draftId)}/publish`, { confirmation: "نشر التعديل إلى سلة" })).data; }
export async function getProductControlHistory(productId) { return (await api.get(`/products-v2/${encodeURIComponent(productId)}/control-center/history`)).data; }
export async function saveProductAiPolicy(payload) { return (await api.put("/products-v2/control-center/policy", payload)).data; }

export async function getProductMediaControl(productId) { return (await api.get(`/products-v2/${encodeURIComponent(productId)}/media-control`)).data; }
export async function saveProductMediaDraft(productId, payload) { return (await api.put(`/products-v2/${encodeURIComponent(productId)}/media-draft`, payload)).data; }
export async function approveProductMediaDraft(productId, draftId) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/media-draft/${encodeURIComponent(draftId)}/approve`)).data; }
export async function publishProductMediaDraft(productId, draftId) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/media-draft/${encodeURIComponent(draftId)}/publish`, { confirmation: "نشر صور المنتج إلى سلة" })).data; }
export async function uploadProductMediaFile(productId, file) {
    const form = new FormData();
    form.append("file", file);
    return (await api.post(`/products-v2/${encodeURIComponent(productId)}/media-upload`, form, { headers: { "Content-Type": "multipart/form-data" } })).data;
}
export async function deleteProductMediaUpload(productId, token) { return (await api.delete(`/products-v2/${encodeURIComponent(productId)}/media-upload/${encodeURIComponent(token)}`)).data; }
export async function getProductMediaAiState(productId) { return (await api.get(`/products-v2/${encodeURIComponent(productId)}/media-ai`)).data; }
export async function createProductMediaAiJob(productId, payload) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/media-ai/jobs`, payload)).data; }
export async function executeProductMediaAiJob(productId, jobId) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/media-ai/jobs/${encodeURIComponent(jobId)}/execute`)).data; }
export async function addProductMediaAiResultToDraft(productId, jobId) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/media-ai/jobs/${encodeURIComponent(jobId)}/add-to-draft`)).data; }
export async function cancelProductMediaAiJob(productId, jobId) { return (await api.post(`/products-v2/${encodeURIComponent(productId)}/media-ai/jobs/${encodeURIComponent(jobId)}/cancel`)).data; }

export async function getProductCreationDraft(draftId) { return (await api.get(`/products-v2/creation-drafts/${encodeURIComponent(draftId)}`)).data; }
