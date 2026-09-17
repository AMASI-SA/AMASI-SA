import api from "../lib/api";

let recentSyncPromise = null;
let recentSyncAt = 0;
const RECENT_SYNC_TTL_MS = 5 * 60_000;
const activeProductPublishes = new Set();
const productPublishPromises = new Map();

const ACTIVE_PUBLISH_STATUSES = new Set([
    "preparing", "publishing", "verifying", "verification_pending",
    "outcome_unknown", "rolling_back", "rollback_required",
]);

export function setProductPublishActivity(productId, active) {
    const key = String(productId || "");
    if (!key) return;
    if (active) activeProductPublishes.add(key);
    else activeProductPublishes.delete(key);
}

function trackPublishResponse(productId, payload) {
    const status = payload?.attempt?.status || payload?.status;
    if (!status) return;
    setProductPublishActivity(productId, ACTIVE_PUBLISH_STATUSES.has(status));
}

export async function syncRecentProductsV2({ force = false } = {}) {
    if (activeProductPublishes.size) {
        return { skipped: true, reason: "active_product_publish" };
    }
    const now = Date.now();
    if (!force && now - recentSyncAt < RECENT_SYNC_TTL_MS) return null;
    if (recentSyncPromise) return recentSyncPromise;
    recentSyncPromise = api.post("/products-v2/sync-recent")
        .then((response) => { recentSyncAt = Date.now(); return response.data; })
        .finally(() => { recentSyncPromise = null; });
    return recentSyncPromise;
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
export async function syncProductsV2() {
    if (activeProductPublishes.size) {
        return { skipped: true, reason: "active_product_publish" };
    }
    const response = await api.post("/products-v2/sync");
    recentSyncAt = Date.now();
    return response.data;
}
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
export async function publishProductControlDraft(productId, draftId) {
    const key = `${String(productId || "")}:${String(draftId || "")}`;
    if (productPublishPromises.has(key)) return productPublishPromises.get(key);
    setProductPublishActivity(productId, true);
    const request = api.post(`/products-v2/${encodeURIComponent(productId)}/control-center/draft/${encodeURIComponent(draftId)}/publish`, { confirmation: "نشر التعديل إلى سلة" })
        .then((response) => {
            trackPublishResponse(productId, response.data);
            return response.data;
        })
        .catch((error) => {
            trackPublishResponse(productId, error?.response?.data);
            throw error;
        })
        .finally(() => { productPublishPromises.delete(key); });
    productPublishPromises.set(key, request);
    return request;
}
export async function verifyProductControlPublishAttempt(productId, attemptId) {
    setProductPublishActivity(productId, true);
    try {
        const payload = (await api.post(`/products-v2/${encodeURIComponent(productId)}/control-center/publish-attempt/${encodeURIComponent(attemptId)}/verify`)).data;
        trackPublishResponse(productId, payload);
        return payload;
    } catch (error) {
        trackPublishResponse(productId, error?.response?.data);
        throw error;
    }
}
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
