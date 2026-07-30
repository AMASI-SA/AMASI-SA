import api from "../lib/api";

export async function loadInventoryReceivingCatalog() {
    const response = await api.get("/inventory-v2/purchase-receiving/catalog");
    return response.data;
}

export async function postPurchaseInventoryReceipt(payload) {
    const response = await api.post("/inventory-v2/purchase-receipts", payload);
    return response.data;
}

export async function loadStockPreparationCatalog() {
    const response = await api.get("/inventory-v2/stock-preparation-orders/catalog");
    return response.data;
}

export async function createStockPreparationOrder(payload) {
    const response = await api.post("/inventory-v2/stock-preparation-orders", payload);
    return response.data;
}

export async function transitionStockPreparationOrder(orderId, payload) {
    const response = await api.post(
        `/inventory-v2/stock-preparation-orders/${encodeURIComponent(orderId)}/actions`,
        payload,
    );
    return response.data;
}

export async function receiveStockPreparationOrder(orderId, payload) {
    const response = await api.post(
        `/inventory-v2/stock-preparation-orders/${encodeURIComponent(orderId)}/receipts`,
        payload,
    );
    return response.data;
}

export async function loadSallaInventorySyncCatalog() {
    const response = await api.get("/inventory-v2/salla-sync/catalog");
    return response.data;
}

export async function saveSallaInventoryBranchMappings(payload) {
    const response = await api.put("/inventory-v2/salla-sync/mappings", payload);
    return response.data;
}

export async function createSallaInventorySyncPreview(payload) {
    const response = await api.post("/inventory-v2/salla-sync/previews", payload);
    return response.data;
}

export async function publishSallaInventorySync(payload) {
    const response = await api.post("/inventory-v2/salla-sync/publish", payload);
    return response.data;
}

export async function verifySallaInventorySyncRun(runId) {
    const response = await api.post(
        `/inventory-v2/salla-sync/runs/${encodeURIComponent(runId)}/verify`,
    );
    return response.data;
}

export function newInventoryReceiptIdempotencyKey(prefix = "inventory-receipt") {
    if (globalThis.crypto?.randomUUID) {
        return `${prefix}:${globalThis.crypto.randomUUID()}`;
    }
    return `${prefix}:${Date.now()}:${Math.random().toString(16).slice(2)}`;
}
