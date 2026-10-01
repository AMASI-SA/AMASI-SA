import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../../lib/api";
import AdvertisingPanel from "./AdvertisingPanel";
import { ADVERTISING_BASE } from "./advertisingAdapter";
jest.mock("../../../lib/api", () => ({ get: jest.fn() }));
const account = { platform: "meta", integration_account_id: "SYN-E-1", display_name: "حساب تجريبي", currency: "USD", readiness: "SETUP_READY", daily_spend_readiness: "AUTOMATIC_POLICY_CONFIGURED", wallet_binding: "SYN-WALLET", payable_binding: "SYN-PAYABLE", original_wallet: { currency: "USD", opening_confirmed: true, confirmed_opening_amount: "9876", posted_original_balance: null }, run_at: "01:00", schedule_timezone: "Asia/Riyadh" };
const context = { stage: 12, identity_source: "mezan_integration_accounts_v2", items: [account] };
let root, node;
beforeEach(() => {
    jest.resetAllMocks(); global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div"); document.body.appendChild(node); root = createRoot(node);
    api.get.mockImplementation(url => Promise.resolve({ data: url.endsWith("/stage-12") ? context : { items: [] } }));
});
afterEach(() => { act(() => root.unmount()); node.remove(); });
const mount = () => act(async () => root.render(<AdvertisingPanel />));
test("native accounts, policies and inline details do not assert posted balances or fabricate FX", async () => {
    await mount();
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(node.textContent).toContain("السياسة مضبوطة؛ ليست إثبات ترحيل");
    await act(async () => [...node.querySelectorAll("button")].find(button => button.textContent === "مراجعة حساب تجريبي").click());
    expect(node.textContent).toContain("SYN-WALLET"); expect(node.textContent).toContain("SYN-PAYABLE");
    expect(node.textContent).not.toContain("9,876"); expect(node.textContent).not.toContain("0.00");
    expect(node.textContent).toContain("دليل مؤكد؛ لا يعني أنه رُحّل");
    expect(node.textContent).toContain("track_a_require_financial_ledger_identity_not_integrated");
    expect(node.textContent).toContain("ad_fx_readiness_read_contract_missing");
    expect(node.querySelector("a")).toBeNull();
});
test("posted original wallet balance displayed verbatim in its currency independently of payable", async () => {
    api.get.mockImplementation(url => Promise.resolve({ data: url.endsWith("/stage-12") ? { ...context, items: [{ ...account, original_wallet: { currency: "USD", posted_original_balance: "125.75" } }] } : { items: [] } }));
    await mount();
    await act(async () => [...node.querySelectorAll("button")].find(button => button.textContent === "مراجعة حساب تجريبي").click());
    expect(node.textContent).toContain("125.75 USD");
    expect(node.textContent).toContain("ad_sar_wallet_and_payable_balance_read_contract_missing");
});
test("empty due queue never means CLOSED_ZERO or completed posting", async () => {
    await mount();
    expect(node.textContent).toContain("غياب اليوم لا يثبت ترحيله أو CLOSED_ZERO");
    expect(node.textContent).toContain("ad_posted_daily_status_read_contract_missing");
    expect(node.textContent).toContain("لا توجد أيام في قائمة المعالجة");
});
test("read route unavailable is explicitly blocked and makes no legacy request", async () => {
    api.get.mockRejectedValue({ response: { status: 404 } });
    await mount();
    expect(node.textContent).toContain("BLOCKED_BY_BACKEND");
    expect(node.textContent).toContain("ad_native_route_not_integrated");
    expect(api.get.mock.calls.map(([url]) => url)).toEqual([`${ADVERTISING_BASE}/stage-12`, `${ADVERTISING_BASE}/due-items`]);
});
test("network error has retry and no account or balance data", async () => {
    api.get.mockRejectedValue(new Error("offline"));
    await mount();
    expect(node.querySelector('[role="alert"]')).not.toBeNull();
    expect(node.textContent).not.toContain("حساب تجريبي");
});
test("loading and empty native account states are distinct", async () => {
    api.get.mockImplementation(() => new Promise(() => {}));
    await mount();
    expect(node.querySelectorAll(".ac-skeleton")).toHaveLength(2);
    api.get.mockImplementation(url => Promise.resolve({ data: url.endsWith("/stage-12") ? { ...context, items: [] } : { items: [] } }));
    await act(async () => [...node.querySelectorAll("button")].find(button => button.textContent === "تحديث الإعلانات").click());
    expect(node.textContent).toContain("لا توجد حسابات إعلانية أصلية");
});
test("search filters only loaded native accounts and clear restores them", async () => {
    await mount();
    await act(async () => {
        const input = node.querySelector('input[type="search"]');
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(input, "No match");
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(node.textContent).toContain("لا توجد حسابات تطابق البحث");
    expect(node.querySelectorAll("tbody tr")).toHaveLength(0);
    expect(api.get).toHaveBeenCalledTimes(2);
    await act(async () => [...node.querySelectorAll("button")].find(button => button.textContent === "إعادة تعيين").click());
    expect(node.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(api.get).toHaveBeenCalledTimes(2);
});
