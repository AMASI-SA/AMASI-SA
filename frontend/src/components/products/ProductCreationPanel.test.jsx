import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ProductCreationPanel from "./ProductCreationPanel";
import * as api from "../../services/mezanProductsV2";

jest.mock("../../services/mezanProductsV2", () => ({
    listProductCreationDrafts: jest.fn(), getProductCreationDraft: jest.fn(),
    publishProductCreationDraft: jest.fn(),
}));
jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
const draft = { id: "draft-1", name: "شال بالاسم", sku: "AMS-NAME", price: 249, status: "approved" };
let root, host;
async function click(label) {
    const button = [...document.querySelectorAll("button")].find((el) => el.textContent.includes(label));
    expect(button).toBeTruthy();
    await act(async () => { button.click(); });
}
beforeEach(async () => {
    jest.clearAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    window.confirm = jest.fn(() => { throw new Error("Native dialog must not block publishing"); });
    api.listProductCreationDrafts.mockResolvedValue({ items: [draft] });
    host = document.createElement("div"); document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => { root.render(<ProductCreationPanel />); });
    await click(draft.name);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
test("requires in-page confirmation and cancel makes no write", async () => {
    await click("إنشاء المنتج في سلة");
    expect(document.querySelector('[role="alertdialog"]').textContent).toContain("249");
    expect(api.publishProductCreationDraft).not.toHaveBeenCalled();
    await click("إلغاء");
    expect(api.publishProductCreationDraft).not.toHaveBeenCalled();
    expect(window.confirm).not.toHaveBeenCalled();
});
test("submits once and shows verified server result", async () => {
    let resolve;
    api.publishProductCreationDraft.mockReturnValue(new Promise((r) => { resolve = r; }));
    await click("إنشاء المنتج في سلة");
    await click("تأكيد ومتابعة");
    await click("جارٍ تنفيذ الطلب");
    expect(api.publishProductCreationDraft).toHaveBeenCalledTimes(1);
    expect(api.publishProductCreationDraft).toHaveBeenCalledWith(draft.id);
    await act(async () => resolve({ draft: { ...draft, status: "published" }, verified: true }));
    expect(host.textContent).toContain("تم الإنشاء والتحقق");
});
test("refreshes active draft after uncertain response without automatically resubmitting", async () => {
    api.publishProductCreationDraft.mockRejectedValue({ response: { data: { detail: { code: "salla_product_creation_uncertain" } } } });
    api.getProductCreationDraft.mockResolvedValue({ draft: { ...draft, status: "publish_unknown" } });
    await click("إنشاء المنتج في سلة"); await click("تأكيد ومتابعة");
    expect(api.getProductCreationDraft).toHaveBeenCalledWith(draft.id);
    expect(host.textContent).toContain("بانتظار المصالحة");
    expect(host.querySelector('[role="alert"]').textContent).toContain("لم يصل رد نهائي");
    expect(api.publishProductCreationDraft).toHaveBeenCalledTimes(1);
});
