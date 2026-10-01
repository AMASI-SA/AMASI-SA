import api from "../lib/api";
import {
    filterMezanComponents, getMezanComponentWorkspace, MEZAN_COMPONENT_PREVIEW_META,
    linkMezanComponentToProductPreview, summarizeMezanComponents, unlinkMezanComponentFromProductPreview,
} from "./mezanComponentCatalog";
import { linkProductResource, unlinkProductResource } from "./mezanProductsV2";

jest.mock("../lib/api", () => ({ __esModule: true, default: {
    get: jest.fn(), put: jest.fn(), delete: jest.fn(),
} }));

// Explicit transport fixtures, not a replacement business-derivation algorithm.
// Actual Mongo workspace derivation is tested in test_component_workspace_derivation.py.
function workspaceFixture() {
    const codes = ["PKG-BAG", "CHAIN-SILVER", "CHAIN-GOLD", "PACK", "LABOR-PLATING", "PRINT", "CUT"];
    return {
        components: codes.map((code, index) => ({
            id: code, code, name: ["كيس", "سلسال فضي", "سلسال ذهبي", "غلاف", "طلاء", "طباعة", "قص"][index],
            status: "active", track_inventory: index < 4,
            reference_cost: { amount: null, currency: "SAR" }, product_usages: [],
        })),
        products: [{ id: "product/one", sku: "AMS10026" }],
        meta: { ...MEZAN_COMPONENT_PREVIEW_META },
    };
}
function usage(productId = "product/one", quantity = 1, condition = null) {
    return { id: `${productId}:${condition?.option_key || "product"}:${condition?.value_key || "base"}`,
        product_id: productId, product_sku: productId === "product/one" ? "AMS10026" : "AMS-SECOND",
        quantity, source: condition ? "option" : "product", condition };
}
function respond(workspace) { api.get.mockResolvedValue({ data: workspace }); }

describe("Mezan component production workspace contract", () => {
    beforeEach(() => { jest.resetAllMocks(); respond(workspaceFixture()); });

    test("loads seven fixture definitions without inventing stock quantities", async () => {
        const workspace = await getMezanComponentWorkspace();
        expect(api.get).toHaveBeenCalledWith("/components-v2/workspace");
        expect(workspace.components).toHaveLength(7);
        expect(workspace.products).toHaveLength(1);
        expect(workspace.products[0].sku).toBe("AMS10026");
        expect(workspace.meta.writes_enabled).toBe(true);
        expect(workspace.meta.mode).toBe("production");
        expect(workspace.meta.inventory_writes_enabled).toBe(false);
        for (const component of workspace.components) {
            expect(component).not.toHaveProperty("stock_quantity");
            expect(component).not.toHaveProperty("quantity");
        }
    });

    test("summarizes stock components, services, and missing costs", async () => {
        const { components } = await getMezanComponentWorkspace();
        expect(summarizeMezanComponents(components)).toEqual({ total: 7, active: 7, inactive: 0,
            stock_components: 4, labor_services: 3, missing_cost: 7 });
    });

    test("supports Arabic search and operational filters", async () => {
        const { components } = await getMezanComponentWorkspace();
        expect(filterMezanComponents(components, { query: "سلسال" })).toHaveLength(2);
        expect(filterMezanComponents(components, { filter: "stock" })).toHaveLength(4);
        expect(filterMezanComponents(components, { filter: "service" })).toHaveLength(3);
        expect(filterMezanComponents(components, { filter: "missing_cost" })).toHaveLength(7);
    });

    test("hides stopped components by default and exposes explicit status filters", async () => {
        const { components } = await getMezanComponentWorkspace();
        const rows = components.map((component, index) => index === 0 ? { ...component, status: "inactive" } : component);
        expect(filterMezanComponents(rows)).toHaveLength(6);
        expect(filterMezanComponents(rows, { status: "inactive" })).toEqual([
            expect.objectContaining({ id: rows[0].id, status: "inactive" })]);
        expect(filterMezanComponents(rows, { status: "all" })).toHaveLength(7);
        expect(summarizeMezanComponents(rows)).toMatchObject({ active: 6, inactive: 1 });
    });

    test("preserves server-derived product usages and unlinked services", async () => {
        const supplied = workspaceFixture(); supplied.components[0].product_usages = [usage()]; respond(supplied);
        const { components } = await getMezanComponentWorkspace();
        const bag = components.find((row) => row.code === "PKG-BAG");
        expect(bag.product_usages).toHaveLength(1);
        expect(bag.product_usages[0]).toMatchObject({ product_sku: "AMS10026", quantity: 1, condition: null });
        expect(components.find((row) => row.code === "LABOR-PLATING").product_usages).toHaveLength(0);
    });

    test("keeps server-supplied option-specific component links explicit", async () => {
        const supplied = workspaceFixture();
        supplied.components[1].product_usages = [usage("product/one", 1, { option_key: "color", value_key: "silver" })];
        respond(supplied);
        const silver = (await getMezanComponentWorkspace()).components[1];
        expect(silver.product_usages).toHaveLength(1);
        expect(silver.product_usages[0]).toMatchObject({ product_sku: "AMS10026", source: "option", quantity: 1,
            condition: { option_key: "color", value_key: "silver" } });
    });

    test("writes and removes a base product link using the delivered resource-link API", async () => {
        const linkedWorkspace = workspaceFixture(); linkedWorkspace.components[4].product_usages = [usage()];
        api.put.mockResolvedValue({ data: { ok: true } }); respond(linkedWorkspace);
        expect((await linkProductResource("product/one", "LABOR-PLATING", 1)).ok).toBe(true);
        expect(api.put).toHaveBeenCalledWith("/products-v2/product%2Fone/resource-links/LABOR-PLATING", { quantity: 1 });
        expect((await getMezanComponentWorkspace()).components[4].product_usages).toContainEqual(
            expect.objectContaining({ product_id: "product/one", quantity: 1, condition: null }));
        api.delete.mockResolvedValue({ data: { ok: true } }); respond(workspaceFixture());
        expect((await unlinkProductResource("product/one", "LABOR-PLATING")).ok).toBe(true);
        expect(api.delete).toHaveBeenCalledWith("/products-v2/product%2Fone/resource-links/LABOR-PLATING");
        expect((await getMezanComponentWorkspace()).components[4].product_usages).toHaveLength(0);
    });

    test("preserves server rejection for option links absent from the selected product", async () => {
        api.put.mockRejectedValue({ response: { data: { detail: { code: "option_value_not_found" } } } });
        const result = await linkMezanComponentToProductPreview({ productId: "product/one", resourceId: "LABOR-PLATING",
            quantity: 1, condition: { option_key: "size", value_key: "large" } });
        expect(result).toMatchObject({ ok: false, code: "option_value_not_found" });
        expect(api.put).toHaveBeenCalledWith("/products-v2/product%2Fone/option-costs/size/large",
            { mode: "resource", resource_id: "LABOR-PLATING", quantity: 1 });
        expect(api.get).not.toHaveBeenCalled();
    });

    test("adds and removes a valid option-specific product link with refreshed readback", async () => {
        const condition = { option_key: "color", value_key: "silver", option_name: "اللون", value_name: "فضي" };
        const linkedWorkspace = workspaceFixture(); linkedWorkspace.components[4].product_usages = [usage("product/one", 1, condition)];
        respond(linkedWorkspace); api.put.mockResolvedValue({ data: { ok: true } });
        const payload = { productId: "product/one", resourceId: "LABOR-PLATING", quantity: 1, condition };
        const linked = await linkMezanComponentToProductPreview(payload);
        expect(linked.ok).toBe(true);
        expect(api.put).toHaveBeenCalledWith("/products-v2/product%2Fone/option-costs/color/silver",
            { mode: "resource", resource_id: "LABOR-PLATING", quantity: 1 });
        expect(linked.component_workspace.components[4].product_usages[0]).toMatchObject({ product_id: "product/one", quantity: 1, condition });
        respond(workspaceFixture()); api.delete.mockResolvedValue({ data: { ok: true } });
        const removed = await unlinkMezanComponentFromProductPreview(payload);
        expect(removed.ok).toBe(true);
        expect(api.delete).toHaveBeenCalledWith("/products-v2/product%2Fone/option-costs/color/silver");
        expect(removed.component_workspace.components[4].product_usages).toHaveLength(0);
    });

    test("preserves the same component across multiple server-derived product usages", async () => {
        const supplied = workspaceFixture(); supplied.components[0].product_usages = [usage(), usage("product-second", 2)]; respond(supplied);
        const usages = (await getMezanComponentWorkspace()).components[0].product_usages;
        expect(usages.map((row) => row.product_sku)).toEqual(["AMS10026", "AMS-SECOND"]);
        expect(usages.map((row) => row.quantity)).toEqual([1, 2]);
    });

    test("preserves distinct usage identities when two option fields share a value key", async () => {
        const supplied = workspaceFixture(); supplied.components[4].product_usages = ["engraving", "gift_wrap"].map(
            (option_key, index) => ({ ...usage("product/one", 1, { option_key, value_key: "yes" }),
                id: ["opaque-binding-a", "opaque-binding-b"][index] })); respond(supplied);
        const usages = (await getMezanComponentWorkspace()).components[4].product_usages;
        expect(usages).toHaveLength(2);
        expect(new Set(usages.map((row) => row.id)).size).toBe(2);
        // Production binding IDs are opaque UUIDs, not preview recipe strings.
        expect(usages.map((row) => [row.id, row.condition.option_key, row.condition.value_key])).toEqual([
            ["opaque-binding-a", "engraving", "yes"], ["opaque-binding-b", "gift_wrap", "yes"] ]);
    });

    test("rejects a missing option condition before any API write", async () => {
        for (const method of [linkMezanComponentToProductPreview, unlinkMezanComponentFromProductPreview]) {
            expect(await method({ productId: "product/one", resourceId: "LABOR-PLATING", condition: null }))
                .toEqual({ ok: false, code: "option_value_not_found" });
        }
        expect(api.put).not.toHaveBeenCalled(); expect(api.delete).not.toHaveBeenCalled();
    });
});
