import api from "../lib/api";

import {
    loadInventoryReceivingCatalog,
    loadPurchaseReceivingLocationSuggestions,
    loadStockPreparationCatalog,
    newInventoryReceiptIdempotencyKey,
    postPurchaseInventoryReceipt,
    createStockPreparationOrder,
    receiveStockPreparationOrder,
    transitionStockPreparationOrder,
    createSallaInventorySyncPreview,
    loadSallaInventorySyncCatalog,
    publishSallaInventorySync,
    saveSallaInventoryBranchMappings,
    verifySallaInventorySyncRun,
} from "./mezanInventoryReceiving";

jest.mock("../lib/api", () => ({
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
}));

describe("Mezan inventory receiving API", () => {
    beforeEach(() => {
        api.get.mockReset();
        api.post.mockReset();
        api.put.mockReset();
    });

    test("loads the governed purchase receiving catalog", async () => {
        api.get.mockResolvedValueOnce({ data: { purchase_invoices: [] } });

        const result = await loadInventoryReceivingCatalog();

        expect(api.get).toHaveBeenCalledWith("/inventory-v2/purchase-receiving/catalog");
        expect(result).toEqual({ purchase_invoices: [] });
    });

    test("posts one idempotent receipt payload", async () => {
        const payload = {
            idempotency_key: "inventory-receipt:test-1",
            purchase_invoice_id: "invoice-1",
        };
        api.post.mockResolvedValueOnce({ data: { ok: true } });

        const result = await postPurchaseInventoryReceipt(payload);

        expect(api.post).toHaveBeenCalledWith("/inventory-v2/purchase-receipts", payload);
        expect(result).toEqual({ ok: true });
    });

    test("loads governed permanent-location suggestions", async () => {
        const payload = {
            purchase_invoice_id: "invoice-1",
            purchase_invoice_line_id: "line-1",
            product_id: "product-1",
            quantity: 5,
            preparation_state: "requires_preparation",
            specifications: [],
        };
        api.post.mockResolvedValueOnce({
            data: {
                recommended_location_id: "location-1",
                suggestions: [{ id: "location-1" }],
            },
        });

        const result = await loadPurchaseReceivingLocationSuggestions(payload);

        expect(api.post).toHaveBeenCalledWith(
            "/inventory-v2/purchase-receiving/location-suggestions",
            payload,
        );
        expect(result.recommended_location_id).toBe("location-1");
    });

    test("creates receipt-specific idempotency keys", () => {
        expect(newInventoryReceiptIdempotencyKey()).toMatch(/^inventory-receipt:/);
        expect(newInventoryReceiptIdempotencyKey()).not.toEqual(newInventoryReceiptIdempotencyKey());
    });

    test("loads and writes stock preparation orders", async () => {
        api.get.mockResolvedValueOnce({ data: { orders: [] } });
        api.post
            .mockResolvedValueOnce({ data: { order: { id: "stock-1" } } })
            .mockResolvedValueOnce({ data: { order: { status: "in_progress" } } })
            .mockResolvedValueOnce({ data: { order: { status: "received" } } });

        await expect(loadStockPreparationCatalog()).resolves.toEqual({ orders: [] });
        await expect(createStockPreparationOrder({ supplier_id: "sup-1" })).resolves.toEqual({ order: { id: "stock-1" } });
        await expect(transitionStockPreparationOrder("stock-1", { action: "start_preparation" })).resolves.toEqual({ order: { status: "in_progress" } });
        await expect(receiveStockPreparationOrder("stock-1", { item_id: "item-1" })).resolves.toEqual({ order: { status: "received" } });

        expect(api.get).toHaveBeenCalledWith("/inventory-v2/stock-preparation-orders/catalog");
        expect(api.post).toHaveBeenNthCalledWith(1, "/inventory-v2/stock-preparation-orders", { supplier_id: "sup-1" });
        expect(api.post).toHaveBeenNthCalledWith(2, "/inventory-v2/stock-preparation-orders/stock-1/actions", { action: "start_preparation" });
        expect(api.post).toHaveBeenNthCalledWith(3, "/inventory-v2/stock-preparation-orders/stock-1/receipts", { item_id: "item-1" });
    });

    test("supports stock-order idempotency key prefixes", () => {
        expect(newInventoryReceiptIdempotencyKey("stock-preparation-order")).toMatch(/^stock-preparation-order:/);
    });

    test("supports governed Salla branch quantity synchronization", async () => {
        api.get.mockResolvedValueOnce({ data: { salla_branches: [] } });
        api.put.mockResolvedValueOnce({ data: { mapping: { revision: 1 } } });
        api.post
            .mockResolvedValueOnce({ data: { run: { id: "preview-1" } } })
            .mockResolvedValueOnce({ data: { run: { status: "verified" } } })
            .mockResolvedValueOnce({ data: { run: { verified: true } } });

        await expect(loadSallaInventorySyncCatalog()).resolves.toEqual({ salla_branches: [] });
        await expect(saveSallaInventoryBranchMappings({ expected_revision: 0, mappings: [] })).resolves.toEqual({ mapping: { revision: 1 } });
        await expect(createSallaInventorySyncPreview({ reason_id: "123" })).resolves.toEqual({ run: { id: "preview-1" } });
        await expect(publishSallaInventorySync({ preview_id: "preview-1" })).resolves.toEqual({ run: { status: "verified" } });
        await expect(verifySallaInventorySyncRun("preview-1")).resolves.toEqual({ run: { verified: true } });

        expect(api.get).toHaveBeenCalledWith("/inventory-v2/salla-sync/catalog");
        expect(api.put).toHaveBeenCalledWith("/inventory-v2/salla-sync/mappings", { expected_revision: 0, mappings: [] });
        expect(api.post).toHaveBeenNthCalledWith(1, "/inventory-v2/salla-sync/previews", { reason_id: "123" });
        expect(api.post).toHaveBeenNthCalledWith(2, "/inventory-v2/salla-sync/publish", { preview_id: "preview-1" });
        expect(api.post).toHaveBeenNthCalledWith(3, "/inventory-v2/salla-sync/runs/preview-1/verify");
    });
});
