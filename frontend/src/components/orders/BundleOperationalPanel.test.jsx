import React, { act } from "react";
import { createRoot } from "react-dom/client";
import BundleOperationalPanel from "./BundleOperationalPanel";
import { getOrderBundles } from "../../services/productBundles";
import { loadMezanSuppliersWorkspace } from "../../services/mezanSuppliersV2";
jest.mock("../../services/productBundles", () => ({ ...jest.requireActual("../../services/productBundles"), getOrderBundles: jest.fn() }));
jest.mock("../../services/mezanSuppliersV2", () => ({ loadMezanSuppliersWorkspace: jest.fn() }));
beforeEach(() => { loadMezanSuppliersWorkspace.mockResolvedValue({ suppliers: [{ id: "s1", company_name: "مورد الهدايا" }] }); });

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
