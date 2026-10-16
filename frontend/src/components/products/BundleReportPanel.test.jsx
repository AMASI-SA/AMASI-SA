import React, { act } from "react";
import { createRoot } from "react-dom/client";
import BundleReportPanel from "./BundleReportPanel";
import { getBundleReport } from "../../services/productBundles";
import { loadMezanSuppliersWorkspace } from "../../services/mezanSuppliersV2";
jest.mock("../../services/productBundles", () => ({ ...jest.requireActual("../../services/productBundles"), getBundleReport: jest.fn() }));
jest.mock("../../services/mezanSuppliersV2", () => ({ loadMezanSuppliersWorkspace: jest.fn() }));

test("supplier groups preserve separate requirements for two orders", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    loadMezanSuppliersWorkspace.mockResolvedValue({ suppliers: [{ id: "supplier", company_name: "مورد البلوفرات" }] });
    getBundleReport.mockResolvedValue({ items: [], sales_count: 2, waiting_count: 2, component_usage: [{ kind: "product", reference_id: "p", name: "بلوفر", consumed_quantity: "4", reserved_quantity: "2" }], supplier_requirements: [{ supplier_id: "supplier", requirements: ["order100", "order200"].map((order) => ({ order_number: order, order_line_id: "line", leaf_id: "leaf", name: "بلوفر", quantity: "2", expected_unit_cost: "50" })) }] });
    const node = document.createElement("div"); const root = createRoot(node);
    await act(async () => root.render(<BundleReportPanel />));
    await act(async () => node.querySelector("button").click());
    expect(node.textContent).toContain("مورد البلوفرات");
    expect(node.textContent).toContain("order100"); expect(node.textContent).toContain("order200");
    expect(node.querySelectorAll("table")[2].querySelectorAll("tbody tr")).toHaveLength(2);
    await act(async () => root.unmount());
});
