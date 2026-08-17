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

function productIdValues(value) {
    return (Array.isArray(value) ? value : String(value || "").split(","))
        .map((item) => String(item || "").trim())
        .filter(Boolean);
}

function productMatchesIds(product, expectedIds) {
    const identities = [
        product?.salla_product_id,
        product?.mezan_product_id,
        product?.id,
    ].map((value) => String(value || "").trim()).filter(Boolean);
    return identities.some((identity) => expectedIds.has(identity));
}

export function isValidSoldMissingCostResult(result, expectedProductIds = "") {
    if (
        result?.meta?.contract_version !== "sold-missing-cost-v3"
        || result?.meta?.missing_mezan_cost !== true
        || result?.meta?.sold_only !== true
        || result?.meta?.cost_semantics?.missing_mezan_cost !== "explicit_mezan_cost_only"
        || result?.meta?.cost_semantics?.calculation_cost !== "mezan_then_salla_fallback"
    ) {
        return false;
    }
    const items = Array.isArray(result?.items) ? result.items : [];
    if (items.some((item) => item?.mezan_cost_missing !== true)) return false;
    const expectedIds = new Set(productIdValues(expectedProductIds));
    if (!expectedIds.size) return true;
    if (Number(result?.pagination?.total || 0) > expectedIds.size) return false;
    return items.every((item) => productMatchesIds(item, expectedIds));
}

export function resolveInitialSelectedProduct(search = "", storedProduct = "") {
    const params = new URLSearchParams(search);
    const fromUrl = params.get("product");
    if (fromUrl) return fromUrl;
    const resolvingCampaignProduct = params.has("lookup_sku") || params.has("lookup_name");
    if (resolvingCampaignProduct) return "";
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
    // `workspace=intake` is reserved by Layout for ProductIntakeWorkspace.
    // Cost links must stay on the main MezanProductsWorkspace route.
    const params = new URLSearchParams();
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
            { ...missing[0], cost_status: "salla_fallback" },
            filters,
        );
        return directHref;
    }
    // The backend is the source of truth for the sold/missing-Mezan cohort.
    // Never copy dashboard product IDs into the URL: that can freeze a stale
    // or narrower "missing everywhere" subset and silently change the filter.
    return `/products-v2?${params.toString()}`;
}
