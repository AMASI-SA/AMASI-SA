function setListFilter(params, key, value) {
    const values = (Array.isArray(value) ? value : String(value || "").split(","))
        .map((item) => String(item || "").trim())
        .filter(Boolean);
    if (values.length) params.set(key, values.join(","));
}

function applyOrderFilters(params, filters) {
    if (filters.from) params.set("from", filters.from);
    if (filters.to) params.set("to", filters.to);
    setListFilter(params, "payment_methods", filters.payment_methods);
    setListFilter(params, "shipping_companies", filters.shipping_companies);
}

export function resolveInitialSelectedProduct(search = "", storedProduct = "") {
    const params = new URLSearchParams(search);
    const fromUrl = params.get("product");
    if (fromUrl) return fromUrl;
    const soldMissingList = params.get("missing_mezan_cost") === "1"
        && params.get("sold_only") === "1";
    if (params.get("view") === "list" || soldMissingList) return "";
    return storedProduct || "";
}

export function resolveInitialProductsView(search = "", storedProduct = "") {
    const params = new URLSearchParams(search);
    if (params.get("view") === "list") return "list";
    return resolveInitialSelectedProduct(search, storedProduct) ? "detail" : "list";
}

export function buildMezanProductCostHref(product, filters = {}) {
    const missingMezanCost = product?.cost_status !== "complete";
    const params = new URLSearchParams({ workspace: "intake" });
    if (missingMezanCost) {
        params.set("missing_mezan_cost", "1");
        params.set("sold_only", "1");
    }
    applyOrderFilters(params, filters);

    const productId = product?.mezan_product_id || product?.salla_product_id;
    if (productId && product?.catalog_product_found !== false) {
        params.set("product", String(productId));
        params.set("focus", "cost");
    }
    return `/products-v2?${params.toString()}`;
}


export function buildMissingMezanCostHref(productCost, filters = {}) {
    const params = new URLSearchParams({
        workspace: "intake",
        missing_mezan_cost: "1",
        sold_only: "1",
        view: "list",
    });
    applyOrderFilters(params, filters);

    const missing = Array.isArray(productCost?.missing_products)
        ? productCost.missing_products
        : [];
    if (missing.length === 1 && missing[0]?.catalog_product_found !== false) {
        const directHref = buildMezanProductCostHref(
            { ...missing[0], cost_status: "missing" },
            filters,
        );
        return directHref;
    }
    return `/products-v2?${params.toString()}`;
}
