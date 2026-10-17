import React, { act } from "react";
import { createRoot } from "react-dom/client";
import BundleProductPolicy from "./BundleProductPolicy";
import api from "../../lib/api";
import { getProductV2 } from "../../services/mezanProductsV2";
import { getProductBundleComponents } from "../../services/productBundles";

jest.mock("../../lib/api", () => ({ get: jest.fn(), put: jest.fn() }));
jest.mock("../../services/mezanProductsV2", () => ({ getProductV2: jest.fn() }));
jest.mock("../../services/productBundles", () => ({ ...jest.requireActual("../../services/productBundles"), getProductBundleComponents: jest.fn() }));
let node, root;
const onSaved = jest.fn();
const policy = { revision: 7, composition_components: { "product_resource:wrap": { cost_treatment: "independent", assembly_origin: "inhouse", evidence_ref: "invoice-123", supplier_omission_supported: false, updated_by: "metadata" } }, sourcing: { stock_first: true, allow_split: false, supplier_id: "supplier", updated_at: "metadata", revision: 7, rules: [{ rule_id: "rule-1", variant_id: "M", server_metadata: "strip" }] } };
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    api.get.mockResolvedValue({ data: policy }); api.put.mockResolvedValue({ data: { ...policy, revision: 8 } });
    getProductBundleComponents.mockResolvedValue({ components: [{ binding_kind: "product_resource", binding_id: "wrap", name: "تغليف" }] });
    getProductV2.mockResolvedValue({ variants: [{ id: "M", name: "M" }], options: [{ id: "text", name: "اسم الإهداء", type: "text" }, { id: "upload", name: "ملف الصورة", type: "file" }] });
    node = document.createElement("div"); root = createRoot(node);
});
afterEach(async () => act(async () => root.unmount()));
const button = (text) => [...node.querySelectorAll("button")].find((item) => item.textContent === text);
async function render(id = "p1") { await act(async () => root.render(<BundleProductPolicy productId={id} suppliers={[{ id: "supplier", name: "مورد" }]} onSaved={onSaved} />)); }
async function open() { await act(async () => { const details = node.querySelector("details"); details.open = true; details.dispatchEvent(new Event("toggle", { bubbles: true })); }); }

test("opening is read-only and explicit save sends revision and only writable fields", async () => {
    await render(); expect(api.get).not.toHaveBeenCalled(); await open();
    expect(api.get).toHaveBeenCalledWith("/products-v2/p1/bundle-policy"); expect(api.put).not.toHaveBeenCalled();
    expect(node.textContent).toContain("اسم الإهداء — مطابقة نص كامل"); expect(node.textContent).not.toContain("ملف الصورة — مطابقة نص كامل");
    await act(async () => button("حفظ سياسة المنتج الأصلية").click());
    expect(api.put).toHaveBeenCalledWith("/products-v2/p1/bundle-policy", { expected_revision: 7, composition_components: { "product_resource:wrap": { cost_treatment: "independent", assembly_origin: "inhouse", evidence_ref: "invoice-123", supplier_omission_supported: false } }, sourcing: { stock_first: true, allow_split: false, supplier_id: "supplier", rules: [{ rule_id: "rule-1", variant_id: "M", conditions: [], customization: {}, supplier_id: null, allow_split: false }] } });
    expect(onSaved).toHaveBeenCalledTimes(1);
});

test("failed save displays error, does not report success, and retains expected revision", async () => {
    api.put.mockRejectedValue({ response: { data: { detail: { message: "تعارض الإصدار" } } } });
    await render(); await open(); await act(async () => button("حفظ سياسة المنتج الأصلية").click());
    expect(node.querySelector('[role="alert"]').textContent).toContain("تعارض الإصدار"); expect(onSaved).not.toHaveBeenCalled();
    expect(api.put.mock.calls[0][1].expected_revision).toBe(7);
});

test("in-flight old product response cannot populate a new product policy", async () => {
    let resolveOld, resolveNew;
    api.get.mockImplementation((url) => new Promise((resolve) => { if (url.includes("/old/")) resolveOld = resolve; else resolveNew = resolve; }));
    await render("old"); await open(); await render("new");
    await act(async () => resolveOld({ data: policy }));
    expect(button("حفظ سياسة المنتج الأصلية")).toBeUndefined();
    expect(api.put).not.toHaveBeenCalled();
    await open();
    expect(api.get).toHaveBeenLastCalledWith("/products-v2/new/bundle-policy");
    await act(async () => resolveNew({ data: { ...policy, revision: 19 } }));
    await act(async () => button("حفظ سياسة المنتج الأصلية").click());
    expect(api.put.mock.calls[0][0]).toBe("/products-v2/new/bundle-policy");
    expect(api.put.mock.calls[0][1].expected_revision).toBe(19);
});

test("incomplete or stale rules block save and can be removed", async () => {
    api.get.mockResolvedValue({ data: { revision: 0, composition_components: {}, sourcing: { rules: [{ rule_id: "stale", variant_id: "gone" }] } } });
    await render(); await open(); expect(button("حفظ سياسة المنتج الأصلية").disabled).toBe(true);
    expect(node.textContent).toContain("مراجع لم تعد متاحة");
    await act(async () => button("حذف سياسة الاختيار").click());
    expect(button("حفظ سياسة المنتج الأصلية").disabled).toBe(false);
    await act(async () => button("إضافة سياسة حسب الخيار أو التخصيص").click());
    expect(button("حفظ سياسة المنتج الأصلية").disabled).toBe(true);
});

test("load errors can be retried without a policy write", async () => {
    api.get.mockRejectedValueOnce(new Error("offline"));
    await render(); await open();
    // A native toggle queued behind the initial event must not silently retry a failed read.
    await act(async () => node.querySelector("details").dispatchEvent(new Event("toggle", { bubbles: true })));
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(node.querySelector('[role="alert"]')).not.toBeNull();
    await act(async () => button("إعادة تحميل السياسة").click());
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(button("حفظ سياسة المنتج الأصلية")).toBeDefined(); expect(api.put).not.toHaveBeenCalled();
});
