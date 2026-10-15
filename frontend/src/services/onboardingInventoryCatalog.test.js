import api from "../lib/api";
import { getOnboardingInventoryCatalog } from "./onboardingInventoryCatalog";
jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn() }));
beforeEach(() => jest.clearAllMocks());
test("uses the read-only V2 projection and preserves catalogue evidence", async () => {
    const data = { read_only: true, products: [{ product_v2_id: "p", main_image: "/p.png", variants: [{ id: "v", barcode: "12", options: [{ name: "اللون", value: "أسود" }] }] }], components: [], categories: [], locations: [{ id: "l", provenance: "AMBIGUOUS" }] };
    api.get.mockResolvedValue({ data }); expect(await getOnboardingInventoryCatalog()).toEqual(data);
    expect(api.get).toHaveBeenCalledWith("/accounting-module/onboarding/inventory-catalog"); expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});
test("malformed and permission failures never masquerade as an empty catalogue", async () => {
    api.get.mockResolvedValue({ data: {} }); await expect(getOnboardingInventoryCatalog()).rejects.toThrow("inventory_catalog_response_invalid");
    api.get.mockRejectedValue({ response: { status: 403 } }); await expect(getOnboardingInventoryCatalog()).rejects.toEqual({ response: { status: 403 } });
});
