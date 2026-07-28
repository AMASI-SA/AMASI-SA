import api from "../lib/api";

export async function getAiStoreFoundation() {
    return (await api.get("/ai-store-operations/foundation")).data;
}

export async function listProductIntake({ status = "needs_attention", limit = 100 } = {}) {
    return (await api.get("/ai-store-operations/product-intake", {
        params: { status, limit },
    })).data;
}

export async function getStoreOperationsAccess() {
    return (await api.get("/ai-store-operations/access")).data;
}

export async function saveStoreOperationsAccess(userId, payload) {
    return (await api.put(`/ai-store-operations/access/${encodeURIComponent(userId)}`, payload)).data;
}

export async function getStoreOperationsAudit({ limit = 100 } = {}) {
    return (await api.get("/ai-store-operations/access/audit/log", { params: { limit } })).data;
}
