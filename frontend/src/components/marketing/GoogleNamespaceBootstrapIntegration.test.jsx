import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import { AuthProvider, useAuth } from "../../context/AuthContext";
import GoogleAdsWorkspace from "./GoogleAdsWorkspace";
import fixture from "../../services/__fixtures__/googleNamespaceBootstrap.json";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() }, formatApiErrorDetail: jest.fn() }));
let container, root, actor, receipt;
function Refresh() { const { refreshUser } = useAuth(); return <button onClick={() => refreshUser().catch(() => {})}>refresh-auth</button>; }
const button = (text) => [...container.querySelectorAll("button")].find((x) => x.textContent === text);
const scoped = () => api.get.mock.calls.filter(([url]) => /namespace-bootstrap|namespace-prerequisites|storage-commit-readiness/.test(url));
async function click(node) { await act(async () => node.click()); }
async function mount() { await act(async () => root.render(<AuthProvider><Refresh /><GoogleAdsWorkspace dateFrom="2026-10-01" dateTo="2026-10-13" /></AuthProvider>)); }
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true; actor = { id: "owner", role: "owner" }; receipt = () => Promise.resolve({ data: fixture.no_preparation });
    jest.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-30T12:00:00Z"));
    api.get.mockReset(); api.post.mockReset(); api.put.mockReset();
    api.get.mockImplementation((url) => {
        if (url === "/auth/me") return Promise.resolve({ data: { ...actor } });
        if (url.endsWith("/namespace-bootstrap/receipt")) return receipt();
        return Promise.resolve({ data: url.endsWith("/readiness") ? { accounts: [], permissions: { owner: true }, campaign_types: [] }
            : url.endsWith("/workspace") ? { accounts: [], items: [], pagination: { page: 1, pages: 1, total: 0 } } : { items: [] } });
    });
    api.post.mockResolvedValue({ data: {} });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.restoreAllMocks(); });

test("actual Workspace/AuthProvider owner shows three independent storage surfaces without any automatic operation", async () => {
    await mount(); const wrapper = container.querySelector('[aria-label="فحوص حفظ حدود جوجل"]');
    expect(wrapper).not.toBeNull(); expect(wrapper.open).toBe(false);
    for (const label of ["فحص حفظ التخزين التقني لجوجل", "متطلبات حفظ حدود جوجل", "تجهيز حفظ حدود جوجل"]) expect(wrapper.querySelector('[aria-label="' + label + '"]')).not.toBeNull();
    expect(scoped()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
    await act(async () => { wrapper.open = true; wrapper.dispatchEvent(new Event("toggle")); });
    expect(scoped()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
    await click(button("فحص المتطلبات بالقراءة")); await click(button("التحقق من حفظ التخزين وقراءته"));
    expect(scoped()).toHaveLength(1); expect(scoped()[0][0]).toMatch(/namespace-prerequisites$/);
    expect(api.post).toHaveBeenCalledTimes(1); expect(api.post.mock.calls[0][0]).toMatch(/storage-commit-readiness$/);
    await click(button("قراءة حالة التجهيز")); expect(scoped()).toHaveLength(2);
    expect(scoped()[1][0]).toMatch(/namespace-bootstrap\/receipt$/); expect(api.post).toHaveBeenCalledTimes(1); expect(api.put).not.toHaveBeenCalled();
});
test("actual Workspace late bootstrap response is discarded across owner change without requiring account props", async () => {
    let resolve; receipt = () => new Promise((r) => { resolve = r; });
    await mount(); await click(button("قراءة حالة التجهيز")); const signal = scoped()[0][1].signal;
    actor = { id: "another-owner", role: "owner" }; await click(button("refresh-auth")); expect(signal.aborted).toBe(true);
    await act(async () => resolve({ data: fixture.prepared }));
    const section = container.querySelector('[aria-label="تجهيز حفظ حدود جوجل"]');
    expect(section).not.toBeNull(); expect(section.querySelector('input[type="checkbox"]')).toBeNull();
    expect(scoped()).toHaveLength(1); expect(api.post).not.toHaveBeenCalled();
});
test("real viewer auth cannot expose bootstrap even when stale Workspace readiness says owner", async () => {
    actor = { id: "viewer", role: "viewer" }; await mount();
    expect(container.querySelector('[aria-label="تجهيز حفظ حدود جوجل"]')).toBeNull();
    expect(scoped()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
});
