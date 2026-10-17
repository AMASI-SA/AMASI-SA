import api from "../lib/api";
import { bundleError, bundleLeafCost, bundleMoney, bundleOptionVisible, changeBundleSource, linkBundleReceipt, getOrderBundles, getProductBundle, previewProductBundle, saveProductBundle } from "./productBundles";

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

test.each([
    ["bundle_policy_revision_conflict", "إعادة تحميل السياسة"],
    ["bundle_policy_evidence_required", "مرجع فاتورة"],
    ["supplier_v2_identity_required", "اختر موردًا صالحًا"],
    ["supplier_verified_identity_required", "أكمل توثيق المورد"],
    ["bundle_component_policy_unresolved", "غير محسومة"],
    ["bundle_invalid_option_mapping", "خيار المنتج الداخلي"],
    ["bundle_invalid_customization_mapping", "الملفات أو التواريخ"],
    ["bundle_invalid_mapping", "غير صالح"],
    ["bundle_policy_unknown_option", "صحح الربط"],
    ["bundle_policy_unknown_variant", "اختر نسخة موجودة"],
    ["bundle_policy_unknown_component", "أعد تحميل السياسة"],
    ["bundle_policy_invalid_rule", "احذف القاعدة الفارغة"],
    ["bundle_policy_duplicate_rule", "قاعدة توريد مكررة"],
    ["bundle_policy_invalid_customization", "حقلًا نصيًا"]
])("explains %s with an actionable correction", (code, expected) => {
    expect(bundleError({ response: { data: { detail: { code } } } })).toContain(expected);
});
