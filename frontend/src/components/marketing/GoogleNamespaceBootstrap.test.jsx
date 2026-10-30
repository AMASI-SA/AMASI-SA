import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import { AuthProvider, useAuth } from "../../context/AuthContext";
import GoogleNamespaceBootstrap from "./GoogleNamespaceBootstrap";
import fixture from "../../services/__fixtures__/googleNamespaceBootstrap.json";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() }, formatApiErrorDetail: jest.fn() }));
const copy = (x) => JSON.parse(JSON.stringify(x));
let container, root, actor, receipt, post, authFailure;
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
function Refresh() { const { refreshUser } = useAuth(); return <button onClick={() => refreshUser().catch(() => {})}>refresh-auth</button>; }
const button = (text) => [...container.querySelectorAll("button")].find((x) => x.textContent === text);
const read = () => click(button("قراءة حالة التجهيز"));
const prepare = () => click(button("مراجعة المتطلبات وحجز طلب التجهيز"));
const execute = () => click(button("تنفيذ تجهيز الحفظ"));
const checkbox = () => container.querySelector('input[type="checkbox"]');
const calls = () => api.get.mock.calls.filter(([url]) => url.includes("namespace-bootstrap"));
async function click(node) { expect(node).toBeTruthy(); await act(async () => node.click()); }
async function mount() { await act(async () => { root.render(<AuthProvider><Refresh /><GoogleNamespaceBootstrap /></AuthProvider>); await Promise.resolve(); await Promise.resolve(); }); }
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true; actor = { id: "owner", role: "owner" }; receipt = () => Promise.resolve({ data: copy(fixture.no_preparation) });
    post = (url) => Promise.resolve({ data: copy(url.endsWith("/prepare") ? fixture.prepared : fixture.verified) }); authFailure = null;
    api.get.mockReset(); api.post.mockReset();
    api.get.mockImplementation((url) => url === "/auth/me" ? authFailure ? Promise.reject(authFailure) : Promise.resolve({ data: copy(actor) }) : receipt(url));
    api.post.mockImplementation((...args) => post(...args));
    jest.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-30T12:00:00Z"));
    jest.spyOn(globalThis.crypto, "getRandomValues").mockImplementation((bytes) => { bytes.fill(15); return bytes; });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); jest.restoreAllMocks(); });

test("actual AuthProvider mounts with no bootstrap read or write; separate acknowledgements and original prepared binding are required", async () => {
    await mount(); expect(calls()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
    await read(); expect(button("مراجعة المتطلبات وحجز طلب التجهيز").disabled).toBe(true);
    await click(checkbox()); await prepare();
    expect(api.post.mock.calls[0][1].request_nonce).toBe("0f".repeat(16));
    expect(button("تنفيذ تجهيز الحفظ").disabled).toBe(true); expect(checkbox().checked).toBe(false);
    for (const key of ["execution_token", "request_nonce", "observation_digest", "manifest_digest"]) expect(container.innerHTML).not.toContain(fixture.prepared[key]);
    await click(checkbox()); await execute();
    expect(api.post.mock.calls[1][1].request_nonce).toBe(fixture.prepared.request_nonce);
    expect(container.textContent).toContain("اكتمل تجهيز"); expect(container.textContent).toContain("لم يُمنح تفويض إنفاق");
    receipt = () => Promise.resolve({ data: copy(fixture.historical) }); await read();
    expect(container.textContent).toContain("لا يثبت جاهزية التخزين الحالية"); expect(checkbox()).toBeNull(); expect(api.post).toHaveBeenCalledTimes(2);
});
test.each([{ id: "owner", role: "viewer" }, { id: "owner", role: "owner", disabled: true }, { id: "owner", role: "owner", is_active: false }, { id: "owner", role: "owner", disabled: 1 }, { id: " owner", role: "owner" }])("unproved owner %j cannot see or call bootstrap", async (value) => {
    actor = value; await mount(); expect(button("قراءة حالة التجهيز")).toBeUndefined(); expect(calls()).toHaveLength(0); expect(api.post).not.toHaveBeenCalled();
});
test.each(["foreign", "expired", "consumed", "applied", "historical"])("server %s receipt offers no preparation or execution", async (key) => {
    receipt = () => Promise.resolve({ data: copy(fixture[key]) }); await mount(); await read(); expect(checkbox()).toBeNull(); expect(api.post).not.toHaveBeenCalled();
});
test("compatible empty no-op is current storage evidence without execute authority", async () => {
    post = () => Promise.resolve({ data: copy(fixture.noop) }); await mount(); await read(); await click(checkbox()); await prepare();
    expect(container.textContent).toContain("متوافقة وفارغة"); expect(checkbox()).toBeNull(); expect(api.post).toHaveBeenCalledTimes(1);
});
test.each(["prepared", "no_preparation"])("unknown execute stays read-only even after GET returns %s", async (key) => {
    receipt = () => Promise.resolve({ data: copy(fixture.prepared) }); post = () => Promise.reject(new Error("unknown commit"));
    await mount(); await read(); await click(checkbox()); await execute();
    expect(container.textContent).toContain("لم تتأكد نتيجة الطلب"); expect(api.post).toHaveBeenCalledTimes(1);
    receipt = () => Promise.resolve({ data: copy(fixture[key]) }); await read(); await read();
    expect(container.textContent).toContain("القراءة فقط متاحة"); expect(checkbox()).toBeNull(); expect(api.post).toHaveBeenCalledTimes(1);
});
test("unknown prepare may recover original preparation but requires separate new execute acknowledgement", async () => {
    post = () => Promise.reject(new Error("lost prepare response")); await mount(); await read(); await click(checkbox()); await prepare();
    receipt = () => Promise.resolve({ data: copy(fixture.prepared) }); await read();
    expect(checkbox().checked).toBe(false); expect(button("تنفيذ تجهيز الحفظ").disabled).toBe(true);
    post = () => Promise.resolve({ data: copy(fixture.verified) }); await click(checkbox()); await execute();
    expect(api.post.mock.calls[1][1].request_nonce).toBe(fixture.prepared.request_nonce);
});
test("unknown prepare absence permits a new explicit preparation only", async () => {
    post = () => Promise.reject(new Error("lost preparation")); await mount(); await read(); await click(checkbox()); await prepare(); await read();
    expect(container.textContent).toContain("لا يثبت أن طلبًا سابقًا لم يصل"); expect(checkbox().checked).toBe(false);
    expect(button("تنفيذ تجهيز الحفظ")).toBeUndefined(); expect(api.post).toHaveBeenCalledTimes(1);
});
test.each([401, 403])("%s invalidates bindings with no automatic recovery; fresh real AuthProvider observation still starts idle", async (status) => {
    receipt = () => Promise.resolve({ data: copy(fixture.prepared) }); post = () => Promise.reject({ response: { status } });
    await mount(); await read(); await click(checkbox()); await execute();
    expect(container.textContent).toContain("تعذر تأكيد صلاحية المالك"); expect(button("قراءة حالة التجهيز")).toBeUndefined();
    await click(button("refresh-auth")); expect(checkbox()).toBeNull(); expect(calls()).toHaveLength(1); expect(api.post).toHaveBeenCalledTimes(1);
});
test.each(["different-owner", "same-owner", "unavailable", "viewer"])("late ignored-abort response cannot enter new %s auth generation", async (change) => {
    const pending = deferred(); receipt = () => pending.promise; await mount(); await read(); const signal = calls()[0][1].signal;
    if (change === "different-owner") actor = { id: "second", role: "owner" };
    if (change === "viewer") actor = { id: "owner", role: "viewer" };
    if (change === "unavailable") authFailure = { response: { status: 503 } };
    await click(button("refresh-auth")); expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve({ data: copy(fixture.prepared) }));
    expect(checkbox()).toBeNull(); expect(container.textContent).not.toContain("يوجد طلب تحضير محفوظ"); expect(api.post).not.toHaveBeenCalled();
});
test("owner A to B to A cannot revive old preparation or late result", async () => {
    const pending = deferred(); receipt = () => pending.promise; await mount(); await read();
    actor = { id: "second", role: "owner" }; await click(button("refresh-auth")); actor = { id: "owner", role: "owner" }; await click(button("refresh-auth"));
    await act(async () => pending.resolve({ data: copy(fixture.prepared) })); expect(checkbox()).toBeNull(); expect(calls()).toHaveLength(1);
});
test("synchronous double click admits one prepare and unmount abort is not rollback or retry", async () => {
    const pending = deferred(); await mount(); await read(); await click(checkbox()); post = () => pending.promise;
    const node = button("مراجعة المتطلبات وحجز طلب التجهيز"); await act(async () => { node.click(); node.click(); }); expect(api.post).toHaveBeenCalledTimes(1);
    const signal = api.post.mock.calls[0][2].signal; await act(async () => root.render(<div />)); expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve({ data: copy(fixture.prepared) })); expect(container.textContent).toBe(""); expect(api.post).toHaveBeenCalledTimes(1);
});
test("client expiry can only remove execution and invalid authority response is refused", async () => {
    receipt = () => Promise.resolve({ data: copy(fixture.prepared) }); await mount(); await read();
    jest.spyOn(Date, "now").mockReturnValue(Date.parse(fixture.prepared.expires_at)); await click(checkbox());
    expect(button("تنفيذ تجهيز الحفظ").disabled).toBe(true); await execute(); expect(api.post).not.toHaveBeenCalled();
    receipt = () => Promise.resolve({ data: { ...fixture.prepared, funding_ready: true } }); await read();
    expect(checkbox()).toBeNull(); expect(container.querySelector('[role="alert"]')).not.toBeNull();
});
