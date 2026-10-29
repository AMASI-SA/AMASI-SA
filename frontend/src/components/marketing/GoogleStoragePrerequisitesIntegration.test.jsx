import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleAdsWorkspace from "./GoogleAdsWorkspace";
jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
let container, root;
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset(); api.post.mockReset(); api.put.mockReset();
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function mount(owner) {
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/readiness")
        ? { accounts: [], permissions: { owner }, campaign_types: [] }
        : url.endsWith("/workspace") ? { accounts: [], items: [], pagination: { page: 1, pages: 1, total: 0 } }
        : { items: [] } }));
    await act(async () => root.render(<GoogleAdsWorkspace dateFrom="2026-10-01" dateTo="2026-10-13" />));
}
const diagnosticReads = () => api.get.mock.calls.filter(([url]) => /namespace-prerequisites|storage-commit-readiness/.test(url));

test("owner no-account diagnostics remain collapsed and mounting or opening cannot start either operation", async () => {
    await mount(true);
    const wrapper = container.querySelector('[aria-label="فحوص حفظ حدود جوجل"]');
    expect(wrapper).not.toBeNull(); expect(wrapper.open).toBe(false);
    expect(wrapper.querySelector('[aria-label="فحص حفظ التخزين التقني لجوجل"]')).not.toBeNull();
    expect(wrapper.querySelector('[aria-label="متطلبات حفظ حدود جوجل"]').open).toBe(false);
    expect(diagnosticReads()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
    await act(async () => { wrapper.open = true; wrapper.dispatchEvent(new Event("toggle")); });
    expect(diagnosticReads()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
    const buttons = [...wrapper.querySelectorAll("button")];
    await act(async () => buttons.find((node) => node.textContent === "فحص المتطلبات بالقراءة").click());
    expect(diagnosticReads()).toHaveLength(1); expect(diagnosticReads()[0][0]).toMatch(/namespace-prerequisites$/);
    expect(api.post).not.toHaveBeenCalled();
    await act(async () => buttons.find((node) => node.textContent === "التحقق من حفظ التخزين وقراءته").click());
    expect(api.post).toHaveBeenCalledTimes(1); expect(api.post.mock.calls[0][0]).toMatch(/storage-commit-readiness$/);
    expect(diagnosticReads()).toHaveLength(1); expect(api.put).not.toHaveBeenCalled();
});

test.each([false, undefined])("non-owner has no technical wrapper or diagnostic calls: %s", async (owner) => {
    await mount(owner);
    expect(container.querySelector('[aria-label="فحوص حفظ حدود جوجل"]')).toBeNull();
    expect(diagnosticReads()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
});
