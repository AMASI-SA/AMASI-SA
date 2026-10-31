import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import { AuthProvider, useAuth } from "../../context/AuthContext";
import GoogleNamespaceBootstrap from "./GoogleNamespaceBootstrap";
import fixture from "../../services/__fixtures__/googleNamespaceBootstrap.json";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn() }, formatApiErrorDetail: jest.fn() }));
const copy = (value) => JSON.parse(JSON.stringify(value));
const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
let container, root, extraRoot, extraContainer, receipt, firstExecute, release;
function Refresh() { const { refreshUser } = useAuth(); return <button onClick={() => refreshUser().catch(() => {})}>refresh-auth</button>; }
const button = (scope, label) => [...scope.querySelectorAll("button")].find((node) => node.textContent === label);
const executeCalls = () => api.post.mock.calls.filter(([url]) => url.endsWith("/execute"));
async function click(scope, label) { const node = button(scope, label); expect(node).toBeTruthy(); await act(async () => node.click()); }
async function ack(scope) { const node = scope.querySelector('input[type="checkbox"]'); expect(node).toBeTruthy(); await act(async () => node.click()); }
async function mount(target) { await act(async () => { target.render(<AuthProvider><Refresh /><GoogleNamespaceBootstrap /></AuthProvider>); await Promise.resolve(); await Promise.resolve(); }); }
async function recoverAndAttempt(scope) {
    await click(scope, "قراءة حالة التجهيز");
    // Exercise the actual unsafe user path if offered; do not merely inspect a flag.
    const action = button(scope, "تنفيذ تجهيز الحفظ");
    if (action) { await ack(scope); await click(scope, "تنفيذ تجهيز الحفظ"); }
}
beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true; extraRoot = extraContainer = release = null;
    jest.spyOn(Date, "now").mockReturnValue(Date.parse("2026-10-30T12:00:00Z"));
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: { getRandomValues: (bytes) => { bytes.fill(3); return bytes; } } });
    receipt = fixture.no_preparation; firstExecute = () => Promise.reject(new Error("lost execute response before consumption"));
    api.get.mockReset(); api.post.mockReset();
    api.get.mockImplementation((url) => Promise.resolve({ data: url === "/auth/me" ? { id: "owner", role: "owner" } : copy(receipt) }));
    api.post.mockImplementation((url) => {
        if (url.endsWith("/prepare")) return Promise.resolve({ data: copy(fixture.prepared) });
        if (url.endsWith("/execute") && executeCalls().length === 1) return firstExecute();
        return Promise.resolve({ data: copy(fixture.verified) });
    });
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
});
afterEach(async () => {
    await act(async () => { root.unmount(); extraRoot?.unmount(); });
    if (release) await act(async () => release({ data: copy(fixture.verified) }));
    container.remove(); extraContainer?.remove(); jest.restoreAllMocks();
    if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto); else delete globalThis.crypto;
});

async function sendFirstExecute(pending) {
    if (pending) firstExecute = () => new Promise((resolve) => { release = resolve; });
    await mount(root); await click(container, "قراءة حالة التجهيز"); await ack(container);
    await click(container, "مراجعة المتطلبات وحجز طلب التجهيز"); await ack(container);
    await click(container, "تنفيذ تجهيز الحفظ");
    expect(executeCalls()).toHaveLength(1);
    // The original may still be queued before the durable consumed fence.
    receipt = fixture.prepared;
}

test.each([false, true])("same-owner actual AuthProvider refresh cannot resend unknown execute (original pending=%s)", async (pending) => {
    await sendFirstExecute(pending); await click(container, "refresh-auth");
    expect(executeCalls()[0][2].signal.aborted).toBe(pending);
    await recoverAndAttempt(container);
    expect(executeCalls()).toHaveLength(1);
});

test.each([false, true])("fresh React root and AuthProvider after page reload cannot resend unknown execute (pending=%s)", async (pending) => {
    await sendFirstExecute(pending);
    await act(async () => root.unmount()); container.remove();
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    await mount(root); await recoverAndAttempt(container);
    expect(executeCalls()).toHaveLength(1);
});

test.each([false, true])("independent second tab cannot obtain retry authority from prepared GET (original pending=%s)", async (pending) => {
    await sendFirstExecute(pending);
    extraContainer = document.createElement("div"); document.body.appendChild(extraContainer); extraRoot = createRoot(extraContainer);
    await mount(extraRoot); await recoverAndAttempt(extraContainer);
    expect(executeCalls()).toHaveLength(1);
});
