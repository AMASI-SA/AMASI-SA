export const productIdentity = row => String(row.product_v2_id || row.id || row.mezan_product_id || "");
export const rowProductIdentity = row => String(row.product_v2_id || row.product_id || "");
export const requiresStockVariant = product => Boolean(product?.variants_required || product?.variants_count || product?.variants?.length);
const text = value => typeof value === "string" || typeof value === "number" ? String(value) : "";
export function optionSummary(product, variant) {
    if (!variant) return (product?.options || []).map(option => [option.name || option.label || "", (option.values || []).map(value => text(value.name || value.value || value.label)).join("، ") || text(option.value)].filter(Boolean).join(": ")).join(" · ");
    const raw = variant.options || variant.selections || [];
    const choices = Array.isArray(raw) ? raw : Object.entries(raw).map(([name, value]) => ({ name, value }));
    return choices.map(choice => {
        const option = (product?.options || []).find(o => String(o.id) === String(choice.option_id));
        const value = (option?.values || []).find(v => String(v.id) === String(choice.value_id));
        return [text(choice.name || choice.label || option?.name), text(choice.value?.name || choice.value || value?.name)].filter(Boolean).join(": ");
    }).filter(Boolean).join(" · ") || variant.name || "";
}
export function searchProducts(products, query) {
    const needle = query.trim().toLocaleLowerCase();
    return products.filter(product => [product.name, product.sku, product.barcode, productIdentity(product), optionSummary(product), ...(product.variants || []).flatMap(v => [v.id, v.name, v.sku, v.barcode, optionSummary(product, v)])].some(value => String(value || "").toLocaleLowerCase().includes(needle)));
}
export const inventoryImage = (product, variant) => variant?.image_url || variant?.image || product?.main_image || product?.image_url || "";
