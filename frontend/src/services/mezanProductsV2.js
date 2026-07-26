import api from "../lib/api";

export async function listProductsV2({ page = 1, perPage = 30, query = "", status = "" } = {}) {
    const params = { page, per_page: perPage };
    if (query.trim()) params.q = query.trim();
    if (status) params.status = status;
    const response = await api.get("/products-v2", { params });
    return response.data;
}

export async function listWorkspaceProducts({
    page = 1,
    perPage = 30,
    query = "",
    status = "",
    sort = "newest",
    missingSku = false,
} = {}) {
    const params = { page, per_page: perPage, sort, missing_sku: missingSku };
    if (query.trim()) params.q = query.trim();
    if (status) params.status = status;
    const response = await api.get("/products-v2/workspace/products", { params });
    return response.data;
}

export async function getProductsV2Summary() {
    const response = await api.get("/products-v2/summary");
    return response.data;
}

export async function syncProductsV2() {
    const response = await api.post("/products-v2/sync");
    return response.data;
}

export async function getProductV2(productId) {
    const response = await api.get(`/products-v2/${encodeURIComponent(productId)}`);
    return response.data;
}

export async function refreshProductV2Details(productId) {
    const response = await api.post(`/products-v2/${encodeURIComponent(productId)}/refresh-details`);
    return response.data;
}

export async function getProductV2Costs(productId) {
    const response = await api.get(`/products-v2/${encodeURIComponent(productId)}/costs`);
    return response.data;
}

export async function saveProductV2Costs(productId, payload) {
    const response = await api.put(`/products-v2/${encodeURIComponent(productId)}/costs`, payload);
    return response.data;
}

export async function previewMissingSkus({ prefix = "AMS", width = 5, limit = 20 } = {}) {
    const response = await api.get("/products-v2/workspace/sku/preview", {
        params: { prefix, width, limit },
    });
    return response.data;
}

export async function applyMissingSkus({ prefix = "AMS", width = 5, limit = 50, confirmation }) {
    const response = await api.post("/products-v2/workspace/sku/apply", {
        prefix,
        width,
        limit,
        confirmation,
    });
    return response.data;
}
