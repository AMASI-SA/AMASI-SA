import React, { act } from "react";
import { createRoot } from "react-dom/client";
import BundleSupplierAdoptionPanel from "./BundleSupplierAdoptionPanel";
import { previewBundleAdoption, applyBundleAdoption, newBundleAdoptionRequestId } from "../../services/bundleSupplierAdoption";

jest.mock("../../services/bundleSupplierAdoption", () => ({
    previewBundleAdoption: jest.fn(), applyBundleAdoption: jest.fn(),
    newBundleAdoptionRequestId: jest.fn(() => "bundle-adoption:test-request"),
    bundleAdoptionError: (error) => error.message || "تعذّر التحويل",
}));

const items = [{ order_item_id: "line-1", name: "طقم هوديات", quantity: 1 }];
const preview = {
    schema: 1, state: "preview", order_number: "synthetic-order", order_line_id: "line-1",
    preview_hash: "a".repeat(64), commercial_quantity: 1, physical_quantity: 2,
    supplier: { id: "supplier-1", name: "مورد الاختبار" }, expected_total_cost: "100.00",
    requirements: [
        { piece_id: "leaf-piece-1", leaf_id: "leaf-1", leaf_unit_index: 1, unit_index: 1, product_name: "هودي باتمان", specifications: { size: "M", color: "أسود" }, unit_cost: "50.00" },
        { piece_id: "leaf-piece-2", leaf_id: "leaf-2", leaf_unit_index: 1, unit_index: 1, product_name: "هودي كات ومن", specifications: { size: "L", color: "أبيض" }, unit_cost: "50.00" },
    ],
};

let host, root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
    newBundleAdoptionRequestId.mockReturnValue("bundle-adoption:test-request");
    previewBundleAdoption.mockResolvedValue(preview);
    applyBundleAdoption.mockResolvedValue({ state: "applied", adoption_id: "adoption-1", preview, piece_ids: ["leaf-piece-1", "leaf-piece-2"] });
    host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
async function render(props = {}) {
    await act(async () => root.render(<BundleSupplierAdoptionPanel orderNumber="synthetic-order" items={items} isOwner actorKey="owner-1" sourceRevision="revision-1" {...props} />));
}
async function click(testId) {
    const button = host.querySelector(`[data-testid="${testId}"]`);
    expect(button).not.toBeNull();
    await act(async () => button.click());
}
async function fillReason(reason = "تصحيح عدد قطع الطقم حسب اختيار العميل") {
    const input = host.querySelector("textarea"); expect(input).not.toBeNull();
    await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(input, reason);
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
}
async function showPreview() {
    await click("bundle-adoption-open");
    await click("bundle-adoption-preview");
}

test("owner opens conversion on demand; opening the order performs no request", async () => {
    await render();
    expect(previewBundleAdoption).not.toHaveBeenCalled();
    expect(applyBundleAdoption).not.toHaveBeenCalled();
    await click("bundle-adoption-open");
    expect(previewBundleAdoption).not.toHaveBeenCalled();
    await click("bundle-adoption-preview");
    expect(previewBundleAdoption).toHaveBeenCalledWith("synthetic-order", "line-1");
    expect(host.textContent).toContain("مورد الاختبار");
    expect(host.textContent).toContain("هودي باتمان");
    expect(host.textContent).toContain("هودي كات ومن");
    expect(host.textContent).toContain("أسود"); expect(host.textContent).toContain("أبيض");
    expect(host.querySelector('[data-testid="bundle-adoption-physical-quantity"]').textContent).toBe("2");
    expect(host.querySelector('[data-testid="bundle-adoption-commercial-quantity"]').textContent).toBe("1");
    expect(host.querySelector('[data-testid="bundle-adoption-total"]').textContent).toContain("100");
    expect(applyBundleAdoption).not.toHaveBeenCalled();
});

test.each([false, undefined, null])("non-owner is hidden and cannot preview (%s)", async (isOwner) => {
    await render({ isOwner });
    expect(host.textContent).toBe(""); expect(previewBundleAdoption).not.toHaveBeenCalled();
});

test("apply uses the reviewed server hash, one request ID and an explicit reason", async () => {
    const onApplied = jest.fn(); await render({ onApplied }); await showPreview();
    expect(host.querySelector('[data-testid="bundle-adoption-apply"]').disabled).toBe(true);
    await fillReason(); await click("bundle-adoption-apply");
    expect(applyBundleAdoption).toHaveBeenCalledWith("synthetic-order", {
        order_line_id: "line-1", preview_hash: preview.preview_hash,
        client_request_id: "bundle-adoption:test-request", reason: "تصحيح عدد قطع الطقم حسب اختيار العميل",
    });
    expect(onApplied).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[role="status"]').textContent).toContain("تم تحويل التجهيز");
    expect(host.textContent).not.toContain("تم استلام");
});

test("unknown apply outcome retries the exact same operation without generating another request", async () => {
    applyBundleAdoption.mockRejectedValueOnce(new Error("الاتصال انقطع"));
    await render(); await showPreview(); await fillReason();
    await click("bundle-adoption-apply");
    expect(host.querySelector('[role="alert"]').textContent).toContain("الاتصال انقطع");
    expect(host.querySelector('[role="status"]')).toBeNull();
    await click("bundle-adoption-apply");
    expect(applyBundleAdoption.mock.calls[0]).toEqual(applyBundleAdoption.mock.calls[1]);
    expect(newBundleAdoptionRequestId).toHaveBeenCalledTimes(1);
});

test("a source revision change invalidates the displayed preview before apply", async () => {
    await render(); await showPreview(); await fillReason();
    await render({ sourceRevision: "revision-2" });
    expect(host.querySelector('[data-testid="bundle-adoption-apply"]')).toBeNull();
    expect(applyBundleAdoption).not.toHaveBeenCalled();
});

test("a late preview from another order is discarded", async () => {
    let resolvePreview;
    previewBundleAdoption.mockImplementation(() => new Promise((resolve) => { resolvePreview = resolve; }));
    await render(); await click("bundle-adoption-open");
    const button = host.querySelector('[data-testid="bundle-adoption-preview"]');
    await act(async () => button.click());
    await render({ orderNumber: "another-order" });
    await act(async () => resolvePreview(preview));
    expect(host.textContent).not.toContain("هودي باتمان");
    expect(host.querySelector('[data-testid="bundle-adoption-apply"]')).toBeNull();
});

test("invalid preview cannot become an apply action", async () => {
    previewBundleAdoption.mockResolvedValue({ ...preview, order_line_id: "unrelated-line" });
    await render(); await showPreview();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="bundle-adoption-apply"]')).toBeNull();
});

test("an invalid apply response never reports completion to the parent", async () => {
    const onApplied = jest.fn();
    applyBundleAdoption.mockResolvedValue({ state: "applied", adoption_id: "adoption-1", preview, piece_ids: ["same", "same"] });
    await render({ onApplied }); await showPreview(); await fillReason(); await click("bundle-adoption-apply");
    expect(host.querySelector('[role="status"]')).toBeNull();
    expect(onApplied).not.toHaveBeenCalled();
});
