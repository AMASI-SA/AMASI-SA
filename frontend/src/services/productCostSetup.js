import api from "../lib/api";

export async function saveProductCostCategory(productId, categoryId) {
    return (await api.put(
        `/products-v2/${encodeURIComponent(productId)}/cost-category`,
        { category_id: categoryId },
    )).data;
}

export async function saveProductCostCompletion(productId, complete = true) {
    return (await api.put(
        `/products-v2/${encodeURIComponent(productId)}/cost-completion`,
        { complete },
    )).data;
}
