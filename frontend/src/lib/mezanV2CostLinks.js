export function buildMissingMezanCostHref(productCost, filters = {}) {
    const params = new URLSearchParams({
        workspace: "intake",
        missing_mezan_cost: "1",
        sold_only: "1",
    });
    if (filters.from) params.set("from", filters.from);
    if (filters.to) params.set("to", filters.to);

    const missing = Array.isArray(productCost?.missing_products)
        ? productCost.missing_products
        : [];
    if (missing.length === 1 && missing[0]?.catalog_product_found !== false) {
        const productId = missing[0]?.mezan_product_id
            || missing[0]?.salla_product_id;
        if (productId) {
            params.set("product", String(productId));
            params.set("focus", "cost");
        }
    }
    return `/products-v2?${params.toString()}`;
}
