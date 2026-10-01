import api from "../lib/api";
import { getOnboardingInventoryCatalog } from "./onboardingInventoryCatalog";
jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn() }));
beforeEach(() => jest.clearAllMocks());
const catalog = () => ({ products: [{ id: "v2-product", main_image: "https://catalog.test/p.jpg", variants: [{ id: "black54" }] }], components: [{ id: "v2-component" }], categories: [], locations: [], warnings: [], read_only: true, sources: { products: "mezan_products_v2", components: "mezan_cost_resources_v2" } });
test("uses dedicated accounting read-only catalogue, never syncs or reads legacy", async () => {
    api.get.mockResolvedValue({ data: catalog() });
    expect(await getOnboardingInventoryCatalog()).toEqual(catalog());
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith("/accounting-module/onboarding/inventory-catalog");
    expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});
test.each([{}, { ...catalog(), read_only: false }, { ...catalog(), sources: { products: "products", components: "components" } }])("rejects malformed or noncanonical responses without fallback", async data => {
    api.get.mockResolvedValue({ data });
    await expect(getOnboardingInventoryCatalog()).rejects.toThrow("inventory_catalog_response_invalid");
    expect(api.get).toHaveBeenCalledTimes(1);
});
test("permission errors surface unchanged", async () => {
    api.get.mockRejectedValue({ response: { status: 403 } });
    await expect(getOnboardingInventoryCatalog()).rejects.toEqual({ response: { status: 403 } });
});
