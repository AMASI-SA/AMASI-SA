import React, { act } from "react";
import { createRoot } from "react-dom/client";
import MarketingPlatformWorkspace from "./MarketingPlatformWorkspace";
import { getMarketingPerformance } from "../services/marketingPerformance";

const mockNavigate = jest.fn((next) => window.history.replaceState({}, "", next.pathname + next.search));
jest.mock("react-router-dom", () => ({ useNavigate: () => mockNavigate }));
jest.mock("../services/marketingPerformance", () => ({
    ...jest.requireActual("../services/marketingPerformance"), getMarketingPerformance: jest.fn(),
}));
jest.mock("../components/marketing/GoogleAdsWorkspace", () => ({
    __esModule: true,
    default: ({ selectedAccountId, onAccountChange }) => <section>
        <p>مساحة حساب {selectedAccountId}</p>
        <button onClick={() => onAccountChange("222")}>اختيار الحساب الثاني</button>
    </section>,
}));

let root, container;
const report = (id, name) => ({ account_id: id, accounts: [{ account_id: id, account_name: name, currency: "SAR" }], campaigns: [], daily: [], insights: [], totals: {}, source: {}, native_totals: {}, ai_readiness: {}, connection: { status: "connected" } });
beforeEach(() => {
    window.history.replaceState({}, "", "/ads-manager?provider=google&tab=campaigns&account=123");
    getMarketingPerformance.mockReset(); mockNavigate.mockClear();
    mockNavigate.mockImplementation((next) => window.history.replaceState({}, "", next.pathname + next.search));
    container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test("native account selection scopes the shared report and rejects the previous account's late response", async () => {
    let finishOld;
    let deferOld = false;
    getMarketingPerformance.mockImplementation((input) => input.accountId === "123" && deferOld
        ? new Promise((resolve) => { finishOld = resolve; }) : Promise.resolve(report(input.accountId, input.accountId === "123" ? "الحساب الأول" : "الحساب المختار")));
    await act(async () => root.render(<MarketingPlatformWorkspace provider="google" />));
    expect(getMarketingPerformance.mock.calls[0][0].accountId).toBe("123");
    deferOld = true;
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent.trim() === "تحديث التقرير").click());
    await act(async () => [...container.querySelectorAll("button")].find((button) => button.textContent === "اختيار الحساب الثاني").click());
    expect(getMarketingPerformance.mock.calls.at(-1)[0].accountId).toBe("222");
    expect(container.textContent).toContain("مساحة حساب 222");
    expect(container.textContent).toContain("حساب التقرير: الحساب المختار");
    expect(new URLSearchParams(window.location.search).get("account")).toBe("222");
    await act(async () => finishOld(report("123", "تقرير سابق متأخر")));
    expect(container.textContent).not.toContain("تقرير سابق متأخر");
    expect(container.textContent).toContain("حساب التقرير: الحساب المختار");
});
