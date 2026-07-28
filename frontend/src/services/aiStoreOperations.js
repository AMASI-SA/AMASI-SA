import api from "../lib/api";

export async function getAiStoreFoundation() {
    return (await api.get("/ai-store-operations/foundation")).data;
}

export async function listProductIntake({ status = "needs_attention", limit = 100 } = {}) {
    return (await api.get("/ai-store-operations/product-intake", {
        params: { status, limit },
    })).data;
}
