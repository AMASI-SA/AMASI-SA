import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import { AuthProvider, useAuth } from "../../context/AuthContext";
import GoogleNativeInventoryPreview from "./GoogleNativeInventoryPreview";
import fixture from "../../services/__fixtures__/googleProductTestReview.json";
jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), put: jest.fn(), post: jest.fn() }, formatApiErrorDetail: jest.fn() }));
const copy = (v) => JSON.parse(JSON.stringify(v));
let container, root, readFit, readNative, readCandidate;
const base = "/integrations-v2/google_ads/commercial-plans/winter";
function Refresh() { const { refreshUser } = useAuth(); return <button type="button" aria-label="refresh-auth" onClick={() => refreshUser().catch(() => {})}>refresh</button>; }
beforeEach(() => { globalThis.IS_REACT_ACT_ENVIRONMENT = true; readFit = readNative = readCandidate = null; api.get.mockReset(); api.put.mockReset(); api.post.mockReset();
    api.get.mockImplementation((url) => url === "/auth/me" ? Promise.resolve({ data: copy(fixture.auth_user) }) : url === base + "/product-season-reviews" ? readFit ? readFit() : Promise.resolve({ data: copy(fixture.fit_state) }) : Promise.reject(new Error("unexpected GET")));
    api.post.mockImplementation((url) => url === base + "/native-inventory-preview" ? readNative ? readNative() : Promise.resolve({ data: copy(fixture.native_response) }) : url === base + "/product-test-review" ? readCandidate ? readCandidate() : Promise.resolve({ data: copy(fixture.candidate) }) : Promise.reject(new Error("unexpected POST")));
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render() { await act(async () => { root.render(<AuthProvider><Refresh /><GoogleNativeInventoryPreview plan={fixture.plan} category={fixture.category} isOwner /></AuthProvider>); await Promise.resolve(); await Promise.resolve(); }); }
const byLabel = (label) => container.querySelector(`[aria-label="${label}"]`);
const button = (label) => [...container.querySelectorAll("button")].find((node) => node.textContent === label);
const nativeButton = () => button("فحص مخزون المنتجات المحددة فقط");
const fitButton = () => button("قراءة مراجعات الملاءمة المحفوظة");
const candidateButton = () => button("مراجعة مرشحي الاختبار المحددين");
const fitCalls = () => api.get.mock.calls.filter(([url]) => url.endsWith("/product-season-reviews"));
async function click(node) { await act(async () => node.click()); }
async function select() { await click(byLabel("فحص مخزون المنتج 1")); await click(byLabel("فحص مخزون المنتج 2")); }

test("controlled seam reuses existing 1..5 selection and performs no automatic read", async () => {
    await render(); expect(fitButton()).toBeUndefined(); expect(api.post).not.toHaveBeenCalled(); await select();
    expect(byLabel("فحص مخزون المنتج 3").disabled).toBe(true); expect(fitCalls()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
    await click(fitButton()); expect(fitCalls()).toHaveLength(1); expect(fitCalls()[0][1].params.getAll("product_ids")).toEqual(["1", "2"]);
    await click(candidateButton()); expect(api.post).toHaveBeenCalledTimes(1); expect(api.post.mock.calls[0][0]).toBe(base + "/product-test-review");
    expect(api.post.mock.calls[0][1].selected_product_ids).toEqual(["1", "2"]); expect(container.querySelectorAll('[data-campaign-context]')).toHaveLength(1);
});
test("native read busy disables child actions without dispatching duplicate provider reads", async () => {
    await render(); await select(); let finish; readNative = () => new Promise((resolve) => { finish = resolve; }); await click(nativeButton());
    expect(fitButton().disabled).toBe(true); expect(fitCalls()).toHaveLength(0); expect(api.post).toHaveBeenCalledTimes(1);
    await act(async () => finish({ data: copy(fixture.native_response) })); expect(fitButton().disabled).toBe(false); expect(container.querySelector('[data-native-inventory-result]')).not.toBe(null);
});
test("child GET busy prevents native read and selection change aborts old child request", async () => {
    await render(); await select(); let finish; readFit = () => new Promise((resolve) => { finish = resolve; }); await click(fitButton()); const signal = fitCalls()[0][1].signal;
    expect(nativeButton().disabled).toBe(true); expect(api.post).not.toHaveBeenCalled(); await click(byLabel("فحص مخزون المنتج 2")); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: copy(fixture.fit_state) })); expect(container.querySelector('[data-fit-product]')).toBe(null); expect(fitCalls()).toHaveLength(1); expect(api.post).not.toHaveBeenCalled();
});
test("child review POST busy prevents old native request and resets on identical public auth refresh", async () => {
    await render(); await select(); await click(fitButton()); let finish; readCandidate = () => new Promise((resolve) => { finish = resolve; }); await click(candidateButton());
    const signal = api.post.mock.calls[0][2].signal; expect(nativeButton().disabled).toBe(true);
    await click(byLabel("refresh-auth")); expect(signal.aborted).toBe(true); await act(async () => finish({ data: copy(fixture.candidate) }));
    expect(container.querySelector('[data-product-test-result]')).toBe(null); expect(container.querySelector('[data-fit-product]')).toBe(null); expect(fitCalls()).toHaveLength(1); expect(api.post).toHaveBeenCalledTimes(1);
});

test("same public auth refresh aborts pending legacy stock POST and clears shared selection too", async () => {
    await render(); await select(); let finish; readNative = () => new Promise((resolve) => { finish = resolve; }); await click(nativeButton()); const signal = api.post.mock.calls[0][2].signal;
    await click(byLabel("refresh-auth")); expect(signal.aborted).toBe(true); await act(async () => finish({ data: copy(fixture.native_response) }));
    expect(container.querySelector('[data-native-inventory-result]')).toBe(null); expect(byLabel("فحص مخزون المنتج 1").checked).toBe(false); expect(byLabel("فحص مخزون المنتج 2").checked).toBe(false);
    expect(fitCalls()).toHaveLength(0); expect(api.post).toHaveBeenCalledTimes(1);
});
