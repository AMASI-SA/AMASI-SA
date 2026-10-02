import api from "../lib/api";

// Read-only projection proves Product/Component V2 provenance server-side.
export async function getOnboardingInventoryCatalog() {
    const { data } = await api.get("/accounting-module/onboarding/inventory-catalog");
    if (data?.read_only !== true || !["products", "components", "categories", "locations"].every(key => Array.isArray(data[key]))) throw new Error("inventory_catalog_response_invalid");
    return data;
}
