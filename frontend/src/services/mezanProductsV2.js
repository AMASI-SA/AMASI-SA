import api from "../lib/api";

export async function listProductsV2({ page = 1, perPage = 30, query = "", status = "" } = {}) {
    const params = { page, per_page: perPage };
    if (query.trim()) params.q = query.trim();
    if (status) params.status = status;
    const response = await api.get("/products-v2", { params });
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
