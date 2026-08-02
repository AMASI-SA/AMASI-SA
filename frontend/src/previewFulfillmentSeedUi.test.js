import {
    isPreviewRuntimeHost,
    previewSeedScenario,
    previewSeedStatusLabel,
} from "./previewFulfillmentSeedUi";


test("seed controls appear only on preview or local hosts", () => {
    expect(isPreviewRuntimeHost("salla-analytics.preview.emergent.host")).toBe(true);
    expect(isPreviewRuntimeHost("preview.mezan.example")).toBe(true);
    expect(isPreviewRuntimeHost("localhost")).toBe(true);
    expect(isPreviewRuntimeHost("mezansalla.com")).toBe(false);
    expect(isPreviewRuntimeHost("salla-analytics.emergent.host")).toBe(false);
});

test("preview scenario describes the deterministic fulfillment dataset", () => {
    expect(previewSeedScenario({ expected: {
        products: 3,
        orders: 20,
        reviewed_orders: 18,
        pending_orders: 2,
        reviewed_quantity: 62,
        necklace_quantity: 50,
        watch_quantity: 10,
        bag_quantity: 2,
    } })).toEqual({
        products: 3,
        orders: 20,
        reviewedOrders: 18,
        pendingOrders: 2,
        reviewedQuantity: 62,
        necklaceQuantity: 50,
        watchQuantity: 10,
        bagQuantity: 2,
    });
});

test("status label distinguishes empty and created preview data", () => {
    expect(previewSeedStatusLabel({ available: true, created: false }))
        .toBe("لم تُنشأ بيانات الاختبار بعد");
    expect(previewSeedStatusLabel({
        available: true,
        created: true,
        counts: { orders: 20, products: 3 },
    })).toBe("20 طلب • 3 منتجات");
    expect(previewSeedStatusLabel({ available: false }))
        .toBe("غير متاح خارج Preview");
});
