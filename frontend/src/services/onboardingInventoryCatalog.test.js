import api from "../lib/api";
import { getOnboardingInventoryCatalog } from "./onboardingInventoryCatalog";

jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn() }));
beforeEach(() => jest.clearAllMocks());
function catalog({ pages = 1, components, locations } = {}) {
    api.get.mockImplementation(async (url, config) => {
        if (url === "/components-v2/workspace") return { data: { components: components || [{ id: "c1", unit: "meter", track_inventory: true, category_ids: ["cat"] }], categories: [{ id: "cat", name: "Fabric" }] } };
        if (url === "/warehouse-locations/locations") return { data: locations || { items: [{ id: "loc", warehouse_id: "w" }], counts_by_state: { empty: 1 } } };
        if (url === "/products-v2") return { data: { items: [{ id: `p${config.params.page}`, variants: [{ id: "v1" }], options: [{ id: "size" }], unit_cost: 999 }], pagination: { total_pages: pages } } };
        throw new Error("unexpected path");
    });
}
test("loads real product pages, variants/options and registered component unit using GET only", async () => {
    catalog({ pages: 2 });
    const result = await getOnboardingInventoryCatalog();
    expect(result.products.map(row => row.id)).toEqual(["p1", "p2"]);
    expect(result.products[0]).toMatchObject({ variants: [{ id: "v1" }], options: [{ id: "size" }] });
    expect(result.components[0]).toMatchObject({ unit: "meter", category_ids: ["cat"] });
    expect(result.products[0]).not.toHaveProperty("unit_cost");
    expect(result).not.toHaveProperty("inventory_accounts");
    expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
    expect(api.get.mock.calls.every(([path]) => !path.includes("sync"))).toBe(true);
});
test("does not default missing units, include service resources, or infer balances", async () => {
    catalog({ components: [{ id: "missing", track_inventory: true }, { id: "service", track_inventory: false }, { id: "inactive", track_inventory: true, status: "inactive" }] });
    const result = await getOnboardingInventoryCatalog();
    expect(result.components).toHaveLength(1); expect(result.components[0].unit).toBeUndefined();
});
test("reports bounded catalogs and refuses malformed responses", async () => {
    catalog({ pages: 3, locations: { items: [{ id: "loc" }], counts_by_state: { empty: 2000 } } });
    expect((await getOnboardingInventoryCatalog({ maxProductPages: 1 })).warnings).toEqual(["product_catalog_truncated", "location_catalog_may_be_truncated"]);
    api.get.mockResolvedValue({ data: {} });
    await expect(getOnboardingInventoryCatalog()).rejects.toThrow("inventory_catalog_response_invalid");
});
test("permission errors remain failures rather than a fabricated empty owner catalog", async () => {
    api.get.mockRejectedValue({ response: { status: 403 } });
    await expect(getOnboardingInventoryCatalog()).rejects.toEqual({ response: { status: 403 } });
});
