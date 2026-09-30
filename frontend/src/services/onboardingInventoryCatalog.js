import api from "../lib/api";

const array = value => Array.isArray(value) ? value : [];
const active = row => row.archived !== true && row.status !== "inactive" && row.is_active !== false;

// Existing catalog GET contracts only. Never sync products, infer a unit/cost,
// or import/approve physical inventory while opening the onboarding editor.
// Inventory account identities come from the accounting mapping contract,
// not from resource categories or product names.
export async function getOnboardingInventoryCatalog({ maxProductPages = 50 } = {}) {
    if (!Number.isInteger(maxProductPages) || maxProductPages < 1 || maxProductPages > 100) throw new Error("inventory_catalog_page_limit_invalid");
    const [componentResponse, locationResponse, firstProductResponse] = await Promise.all([
        api.get("/components-v2/workspace"),
        api.get("/warehouse-locations/locations", { params: { limit: 1000 } }),
        api.get("/products-v2", { params: { page: 1, per_page: 100 } }),
    ]);
    const componentData = componentResponse.data;
    const locationData = locationResponse.data;
    const first = firstProductResponse.data;
    if (!Array.isArray(componentData?.components) || !Array.isArray(componentData?.categories) || !Array.isArray(locationData?.items) || !Array.isArray(first?.items) || !Number.isInteger(first?.pagination?.total_pages)) throw new Error("inventory_catalog_response_invalid");
    const products = [...first.items];
    const warnings = [];
    const pageCount = Math.min(first.pagination.total_pages, maxProductPages);
    for (let page = 2; page <= pageCount; page += 1) {
        const response = await api.get("/products-v2", { params: { page, per_page: 100 } });
        if (!Array.isArray(response.data?.items)) throw new Error("inventory_catalog_response_invalid");
        products.push(...response.data.items);
    }
    if (first.pagination.total_pages > maxProductPages) warnings.push("product_catalog_truncated");
    // The legacy read contracts have bounded arrays but no continuation token.
    // Report bounds honestly instead of implying every identity was loaded.
    if (componentData.components.length >= 5000) warnings.push("component_catalog_may_be_truncated");
    if (componentData.categories.length >= 500) warnings.push("category_catalog_may_be_truncated");
    const locationTotal = Object.values(locationData.counts_by_state || {}).reduce((sum, value) => sum + Number(value || 0), 0);
    if (locationTotal > locationData.items.length || locationData.items.length >= 1000) warnings.push("location_catalog_may_be_truncated");
    return {
        products: products.filter(active).map(row => ({
            id: row.id || row.mezan_product_id, mezan_product_id: row.mezan_product_id,
            name: row.name, sku: row.sku, barcode: row.barcode,
            variants: array(row.variants).filter(variant => variant.id !== undefined && variant.id !== null),
            variants_count: row.variants_count, variants_required: row.variants_required,
            options: array(row.options),
        })).filter(row => row.id),
        components: componentData.components.filter(row => active(row) && row.track_inventory === true).map(row => ({
            id: row.id, name: row.name, code: row.code, kind: row.kind,
            unit: row.unit, category_ids: array(row.category_ids), track_inventory: true,
        })),
        categories: componentData.categories.filter(active).map(row => ({ id: row.id, name: row.name })),
        locations: locationData.items.filter(row => row.state !== "disabled").map(row => ({
            id: row.id, warehouse_id: row.warehouse_id, warehouse_name: row.warehouse_name,
            code: row.code, name: row.name,
        })),
        warnings,
    };
}
