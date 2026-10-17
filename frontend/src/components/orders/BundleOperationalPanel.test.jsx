import React, { act } from "react";
import { createRoot } from "react-dom/client";
import BundleOperationalPanel from "./BundleOperationalPanel";
import { getOrderBundles, linkBundleReceipt, changeBundleSource } from "../../services/productBundles";
import { loadMezanSuppliersWorkspace } from "../../services/mezanSuppliersV2";
jest.mock("../../services/productBundles", () => ({ ...jest.requireActual("../../services/productBundles"), getOrderBundles: jest.fn(), linkBundleReceipt: jest.fn(), changeBundleSource: jest.fn() }));
jest.mock("../../services/mezanSuppliersV2", () => ({ loadMezanSuppliersWorkspace: jest.fn() }));
beforeEach(() => { jest.clearAllMocks(); linkBundleReceipt.mockResolvedValue({}); loadMezanSuppliersWorkspace.mockResolvedValue({ suppliers: [{ id: "s1", company_name: "مورد الهدايا" }] }); });

test("does not display readiness from loading or a failed response", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const node = document.createElement("div");
    const root = createRoot(node);
    getOrderBundles.mockRejectedValue(new Error("offline"));
    await act(async () => root.render(<BundleOperationalPanel orderNumber="100" />));
    expect(node.querySelector('[role="alert"]')).not.toBeNull();
    expect(node.textContent).not.toContain("جميع المكونات المطلوبة جاهزة");
    await act(async () => root.unmount());
});

test("shows frozen revision, all four leaves and server readiness", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const node = document.createElement("div");
    const root = createRoot(node);
    getOrderBundles.mockResolvedValue({ bundles: [{ order_line_id: "l", product_id: "p", product_name: "بكج هدايا", quantity: 1, revision: 7, ready: false, leaves: [1, 2, 3, 4].map((id) => ({ name: `مكوّن ${id}`, supplier_id: `s${id}`, quantity: 1, sourcing_mode: "supplier", state: "awaiting_supplier" })) }] });
    await act(async () => root.render(<BundleOperationalPanel orderNumber="100" />));
    expect(node.textContent).toContain("إصدار التركيب 7");
    expect(node.querySelectorAll("tbody tr")).toHaveLength(4);
    expect(node.textContent).toContain("المكونات لم تكتمل جاهزيتها");
    expect(node.textContent).toContain("بكج هدايا");
    expect(node.textContent).toContain("مورد الهدايا");
    expect(node.textContent).toContain("بانتظار المورد");
    await act(async () => root.unmount());
});

test("automatic mixed supply shows segments and attaches receipt to parent requirement", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const node = document.createElement("div"); const root = createRoot(node);
    getOrderBundles.mockResolvedValue({ bundles: [{ order_line_id: "line", product_name: "بكج", quantity: 1, revision: 2, leaves: [{ leaf_id: "parent-leaf", name: "قلادة", quantity: 5, sourcing_mode: "auto", supplier_id: "s1", supplier_quantity: "3", state: "awaiting_supplier", sourcing_segments: [{ leaf_id: "stock-segment", sourcing_mode: "stock", quantity: "2", state: "reserved" }, { leaf_id: "supplier-segment", sourcing_mode: "supplier", supplier_id: "s1", quantity: "3", state: "awaiting_supplier" }] }] }] });
    await act(async () => root.render(<BundleOperationalPanel orderNumber="100" canReceive canChangeSource />));
    expect(node.textContent).toContain("توريد تلقائي");
    expect(node.textContent).toContain("من المخزون: 2"); expect(node.textContent).toContain("من المورد: 3");
    expect(node.textContent).toContain("للجزء المطلوب من المورد فقط");
    expect(node.querySelector('[aria-label="مصدر قلادة"]').value).toBe("auto");
    const input = node.querySelector("input");
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "receipt-1"); input.dispatchEvent(new Event("input", { bubbles: true })); });
    const button = [...node.querySelectorAll("button")].find((item) => item.textContent === "ربط الإيصال والتحقق");
    await act(async () => button.click());
    expect(linkBundleReceipt).toHaveBeenCalledWith("100", { order_line_id: "line", leaf_id: "parent-leaf", receipt_id: "receipt-1" });
    expect(changeBundleSource).not.toHaveBeenCalled();
    await act(async () => root.unmount());
});

test("automatic unresolved source is explicit and has no supplier receipt action", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const node = document.createElement("div"); const root = createRoot(node);
    getOrderBundles.mockResolvedValue({ bundles: [{ order_line_id: "line", product_name: "بكج", quantity: 1, revision: 2, leaves: [{ leaf_id: "leaf", name: "قلادة", quantity: 5, sourcing_mode: "auto", unresolved_quantity: "5", state: "needs_source" }] }] });
    await act(async () => root.render(<BundleOperationalPanel orderNumber="100" canReceive />));
    expect(node.textContent).toContain("يحتاج مصدر توريد"); expect(node.textContent).toContain("كمية بلا مصدر: 5");
    expect(node.textContent).not.toContain("ربط الإيصال والتحقق");
    expect(node.querySelector("tbody").textContent).not.toContain("مخزون");
    await act(async () => root.unmount());
});
