import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ProductTrafficSources from "./ProductTrafficSources";
import api from "../lib/api";
jest.mock("../lib/api", () => ({ get: jest.fn() }));

let host, root;
const source = { platform: "snapchat", platform_label: "سناب شات", account_id: "account-1", account_status: "confirmed", attribution_status: "confirmed", units_sold: 7,
    campaigns: [
        { campaign_id: "a", campaign_name: "شالات الشتاء", attribution_status: "confirmed", units_sold: 4 },
        { campaign_id: "b", campaign_name: "هدايا", attribution_status: "confirmed", units_sold: 2 },
        { campaign_id: null, attribution_status: "unknown", units_sold: 1 },
    ] };
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    api.get.mockReset();
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
    global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); delete global.ResizeObserver; });
const render = async (sources = [source], promotionContext) => act(async () => root.render(<ProductTrafficSources sources={sources} promotionContext={promotionContext} />));
test("platform icons and counts remain without campaign interactions or requests", async () => {
    await render([source], { from_date: "2026-11-01", to_date: "2026-11-08", product_id: "p" });
    const badge = host.querySelector("li");
    expect(badge.textContent).toContain("7 قطعة");
    expect(badge.textContent).toContain("👻");
    expect(badge.getAttribute("title")).toContain("سناب شات");
    await act(async () => {
        badge.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
        badge.click();
        badge.dispatchEvent(new FocusEvent("focusin", { bubbles: true }));
        await new Promise(resolve => setTimeout(resolve, 250));
    });
    expect(document.querySelector('[role="dialog"]')).toBeNull();
    expect(host.querySelector("button")).toBeNull();
    expect(api.get).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain("شالات الشتاء");
});

test("direct and unknown stay distinct and sources remain excluded from print", async () => {
    await render([
        { platform: "direct", platform_label: "مباشر", account_status: "not_applicable", units_sold: 2 },
        { platform: "unknown", platform_label: "غير معروف", units_sold: 3 },
    ]);
    expect(host.textContent).toContain("مباشر");
    expect(host.textContent).toContain("غير معروف");
    expect(host.querySelector("style").textContent).toContain("display: none !important");
    expect(api.get).not.toHaveBeenCalled();
});
