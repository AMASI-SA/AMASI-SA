export const CREATE_PREVIEW_SEED_CONFIRMATION = "CREATE_PREVIEW_TEST_DATA";
export const RESET_PREVIEW_SEED_CONFIRMATION = "DELETE_PREVIEW_TEST_DATA";

export function isPreviewRuntimeHost(hostname = "") {
    const host = String(hostname || "").trim().toLowerCase();
    return host === "localhost"
        || host === "127.0.0.1"
        || host.includes(".preview.")
        || host.startsWith("preview.")
        || host.includes("preview-emergent")
        || host.includes("preview.emergent");
}

export function previewSeedScenario(status = {}) {
    const expected = status?.expected || {};
    return {
        products: Number(expected.products || 3),
        orders: Number(expected.orders || 20),
        reviewedOrders: Number(expected.reviewed_orders || 18),
        pendingOrders: Number(expected.pending_orders || 2),
        reviewedQuantity: Number(expected.reviewed_quantity || 62),
        necklaceQuantity: Number(expected.necklace_quantity || 50),
        watchQuantity: Number(expected.watch_quantity || 10),
        bagQuantity: Number(expected.bag_quantity || 2),
    };
}

export function previewSeedStatusLabel(status = {}) {
    if (!status?.available) return "غير متاح خارج Preview";
    if (!status?.created) return "لم تُنشأ بيانات الاختبار بعد";
    const counts = status?.counts || {};
    return `${Number(counts.orders || 0)} طلب • ${Number(counts.products || 0)} منتجات`;
}
