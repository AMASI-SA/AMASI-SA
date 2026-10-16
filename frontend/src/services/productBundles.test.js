import api from "../lib/api";
import { bundleLeafCost, bundleMoney, bundleOptionVisible, changeBundleSource, linkBundleReceipt, getOrderBundles, getProductBundle, previewProductBundle, saveProductBundle } from "./productBundles";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() } }));

test("preserves all four suppliers, nested references and optimistic revision", async () => {
    const payload = { expected_revision: 7, product_type: "composite", items: [1, 2, 3, 4].map((id) => ({ kind: "product", product_id: `p${id}`, supplier_id: `s${id}`, quantity: "1", sourcing_mode: "supplier" })) };
    api.put.mockResolvedValue({ data: { definition: payload } });
    await saveProductBundle("p/1", payload);
    expect(api.put).toHaveBeenCalledWith("/products-v2/p%2F1/bundle", payload);
    api.get.mockResolvedValue({ data: { definition: payload } });
    expect((await getProductBundle("p/1")).definition.items).toHaveLength(4);
});

test("preview sends stable option identifiers and no client cost", async () => {
    api.post.mockResolvedValue({ data: { expansion: { estimated_cost: "45" } } });
    await previewProductBundle("p1", [{ option_id: "size", value_id: "4" }]);
    expect(api.post).toHaveBeenCalledWith("/products-v2/p1/bundle/preview", { selected_options: [{ option_id: "size", value_id: "4" }] });
});

test("unknown costs stay unknown rather than appearing free", () => {
    expect(bundleLeafCost({ quantity: 4, unit_cost: null })).toBeNull();
    expect(bundleLeafCost({ quantity: "4", unit_cost: "12.50" })).toBe(50);
    expect(bundleMoney(null)).toBe("غير متاح");
});

test("order lookup safely encodes identifiers", async () => {
    api.get.mockResolvedValue({ data: { bundles: [] } });
    await getOrderBundles("A/B");
    expect(api.get).toHaveBeenCalledWith("/fulfillment-v2/orders/A%2FB/bundles");
});

test("receipt link references the existing receipt without creating inventory", async () => {
    api.post.mockResolvedValue({ data: {} });
    const payload = { order_line_id: "line", leaf_id: "leaf", receipt_id: "posted-7" };
    await linkBundleReceipt("A/B", payload);
    expect(api.post).toHaveBeenCalledWith("/fulfillment-v2/orders/A%2FB/bundles/receipts", payload);
    await changeBundleSource("A/B", { order_line_id: "line", leaf_id: "leaf", sourcing_mode: "supplier", supplier_id: "s4" });
    expect(api.post).toHaveBeenCalledWith("/fulfillment-v2/orders/A%2FB/bundles/source", { order_line_id: "line", leaf_id: "leaf", sourcing_mode: "supplier", supplier_id: "s4" });
});

test("conditional options appear only when the stable prerequisite is selected", () => {
    const option = { show_if: [{ option_id: "size", value_id: "4" }] };
    expect(bundleOptionVisible(option, [{ option_id: "size", value_id: "2" }])).toBe(false);
    expect(bundleOptionVisible(option, [{ option_id: "size", value_id: "4" }])).toBe(true);
});
