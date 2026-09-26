import { act } from "react";
import { createRoot } from "react-dom/client";
import InventoryReceivingWorkspace from "./InventoryReceivingWorkspace";
import api from "../lib/api";
import { loadInventoryReceivingCatalog, postPurchaseInventoryReceipt } from "../services/mezanInventoryReceiving";

jest.mock("../lib/api", () => ({
    __esModule: true, default: { get: jest.fn(), post: jest.fn() },
    formatApiErrorDetail: (detail) => detail || "",
}));
jest.mock("react-router-dom", () => ({
    Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}));
jest.mock("../services/mezanInventoryReceiving", () => ({
    loadInventoryReceivingCatalog: jest.fn(), postPurchaseInventoryReceipt: jest.fn(),
}));
jest.mock("../components/inventory/StockPreparationOrders", () => () => <p>existing-stock-preparation</p>);
jest.mock("../components/inventory/SallaInventorySync", () => () => <p>existing-salla-sync</p>);

test("purchase receipt entry routes all invoices to full approval and exposes no direct receipt submission", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    loadInventoryReceivingCatalog.mockResolvedValue({ products: [], locations: [], recent_receipts: [] });
    api.get.mockResolvedValue({ data: { items: [
        { id: "new-draft", schema_version: "g47-v1", state: "draft", invoice_number: "INV-1" },
        { id: "legacy", invoice_number: "OLD" },
    ] } });
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => root.render(<InventoryReceivingWorkspace />));
        expect(container.querySelector('a[href="/purchase-invoices?invoice=new-draft"]').textContent).toContain("للاعتماد الكامل");
        expect(container.querySelector('a[href="/purchase-invoices?invoice=legacy"]').textContent).toContain("عرض");
        expect(container.querySelector("form")).toBeNull();
        expect(container.textContent).toContain("لا استلام منفرد لبند");
        expect(postPurchaseInventoryReceipt).not.toHaveBeenCalled();
        expect(api.post).not.toHaveBeenCalled();
        await act(async () => Array.from(container.querySelectorAll("nav button")).find((button) => button.textContent.includes("تجهيز")).click());
        expect(container.textContent).toContain("existing-stock-preparation");
        await act(async () => Array.from(container.querySelectorAll("nav button")).find((button) => button.textContent.includes("مزامنة")).click());
        expect(container.textContent).toContain("existing-salla-sync");
    } finally {
        await act(async () => root.unmount());
        container.remove();
    }
});
