import api from "../lib/api";

// Dedicated accounting GET reads canonical V2 collections directly, without
// catalog sync, lifecycle/index writes, cost inference, or legacy fallback.
export async function getOnboardingInventoryCatalog() {
    const { data } = await api.get("/accounting-module/onboarding/inventory-catalog");
    if (!["products", "components", "categories", "locations", "warnings"].every(key => Array.isArray(data?.[key])) || data?.read_only !== true || data?.sources?.products !== "mezan_products_v2" || data?.sources?.components !== "mezan_cost_resources_v2") throw new Error("inventory_catalog_response_invalid");
    return data;
}
