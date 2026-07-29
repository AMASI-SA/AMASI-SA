import api from "../lib/api";

let recentSyncPromise = null;
let recentSyncAt = 0;
const RECENT_SYNC_TTL_MS = 45_000;

export async function syncRecentProductsV2({ force = false } = {}) {
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

export async function listWorkspaceProducts({ page = 1, perPage = 30, query = "", status = "", sort = "newest", missingSku = false } = {}) {
    if (page === 1 && sort === "newest" && !query.trim()) {
        try { await syncRecentProductsV2(); } catch { /* keep local listing available */ }
    }
    const params = { page, per_page: perPage, sort, missing_sku: missingSku };
    if (query.trim()) params.q = query.trim();
    if (status) params.status = status;
    return (await api.get("/products-v2/workspace/products", { params })).data;
}

export async function getProductsV2Summary() { return (await api.get("/products-v2/summary")).data; }
export async function syncProductsV2() { const response = await api.post("/products-v2/sync"); recentSyncAt = Date.now(); return response.data; }
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
