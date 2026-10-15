import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import MezanV2NavigationShell from "./MezanV2NavigationShell";
import { getAccountingAccess } from "../services/accountingModule";
import AccountingFirstRunGate from "../pages/accounting/AccountingFirstRunGate";
import { getOnboardingSession, listOnboardingSessions } from "../services/accountingOnboarding";

jest.mock("react-router/dom", () => {
    const { TextEncoder, TextDecoder } = require("util");
    if (!global.TextEncoder) global.TextEncoder = TextEncoder;
    if (!global.TextDecoder) global.TextDecoder = TextDecoder;
    const path = require("path");
    const packagePath = require.resolve("react-router/package.json");
    let target = require(packagePath).exports["./dom"];
    while (target && typeof target === "object") {
        target = target.require || target.node || target.default;
    }
    if (typeof target !== "string") throw new Error("react-router/dom CommonJS export missing");
    return require(path.resolve(path.dirname(packagePath), target));
}, { virtual: true });

jest.mock("../context/AuthContext", () => ({
    useOptionalAuth: () => ({ user: { id: "owner-fixture", role: "owner" } }),
}));
jest.mock("../services/accountingModule", () => ({
    getAccountingAccess: jest.fn(async () => ({ user_id: "owner-fixture", is_owner: true, permissions: [] })),
}));
jest.mock("../services/accountingOnboarding", () => ({
    getOnboardingSession: jest.fn(), listOnboardingSessions: jest.fn(),
}));

function Harness() {
    const location = useLocation();
    const entry = location.pathname === "/integrations-v2" && !new URLSearchParams(location.search).has("page");
    return <><MezanV2NavigationShell location={location} onOpenAll={() => {}} />
        {entry && <AccountingFirstRunGate />}
        <output>{location.pathname + location.search}</output></>;
}

let root, node;
beforeEach(() => {
    jest.clearAllMocks();
    getAccountingAccess.mockResolvedValue({ user_id: "owner-fixture", is_owner: true, permissions: [] });
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    listOnboardingSessions.mockResolvedValue({ items: [] });
    getOnboardingSession.mockResolvedValue({ status: "reviewed", reviewed_by: "owner-fixture",
        reviewed_at: "2026-09-30T00:00:00Z", reviewed_hash: "fixture-hash", preview: { hash: "fixture-hash" } });
});
afterEach(() => { act(() => root.unmount()); node.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; });

test.each([390, 1440])("owner first-run click and persistent nine links at viewport %s", async width => {
    Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
    await act(async () => root.render(<MemoryRouter initialEntries={["/dashboard-advanced"]}><Harness /></MemoryRouter>));
    const clickAccounting = () => act(async () => node.querySelector('[data-testid="mezan-v2-primary-accounting"]').click());
    await clickAccounting();
    expect(node.querySelector("output").textContent).toBe("/integrations-v2?workspace=financial&page=opening-balances");
    const links = () => [...node.querySelectorAll('[data-testid="mezan-v2-secondary-link-accounting"]')];
    expect(links()).toHaveLength(9);
    expect(links().map(link => link.textContent)).toContain("الصناديق والحسابات المالية");
    listOnboardingSessions.mockResolvedValue({ items: [{ id: "reviewed-1", status: "reviewed" }] });
    await clickAccounting();
    expect(node.querySelector("output").textContent).toBe("/integrations-v2?workspace=financial&page=home");
    expect(links()).toHaveLength(9);
    expect(links().map(link => link.textContent)).toContain("الأرصدة الافتتاحية");
    expect(getOnboardingSession).toHaveBeenCalledWith("reviewed-1");
    await act(async () => links().find(link => link.textContent === "الأرصدة الافتتاحية").click());
    expect(node.querySelector("output").textContent).toContain("page=opening-balances");
});

test.each(["api-error", "detail-error", "draft", "previewed", "unverified-review", "hash-mismatch"])("first-run fails closed for %s", async state => {
    if (state === "api-error") listOnboardingSessions.mockRejectedValue(new Error("unavailable"));
    else listOnboardingSessions.mockResolvedValue({ items: [{ id: "session-1", status: ["draft", "previewed"].includes(state) ? state : "reviewed" }] });
    if (state === "detail-error") getOnboardingSession.mockRejectedValue(new Error("unavailable"));
    if (state === "unverified-review") getOnboardingSession.mockResolvedValue({ status: "reviewed" });
    if (state === "hash-mismatch") getOnboardingSession.mockResolvedValue({ status: "reviewed", reviewed_by: "owner", reviewed_at: "date", reviewed_hash: "a", preview: { hash: "b" } });
    await act(async () => root.render(<MemoryRouter initialEntries={["/integrations-v2?workspace=financial"]}><Harness /></MemoryRouter>));
    expect(node.querySelector("output").textContent).toContain("page=opening-balances");
});

test("a handed-off reviewed session remains complete without querying financial activation", async () => {
    listOnboardingSessions.mockResolvedValue({ items: [{ id: "session-1", status: "handed_off" }] });
    getOnboardingSession.mockResolvedValue({ status: "handed_off", reviewed_by: "owner", reviewed_at: "date", reviewed_hash: "a", preview: { hash: "a" } });
    await act(async () => root.render(<MemoryRouter initialEntries={["/integrations-v2?workspace=financial"]}><Harness /></MemoryRouter>));
    expect(node.querySelector("output").textContent).toContain("page=home");
});
