import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react-dom/test-utils";
import CampaignHoverBadge from "./CampaignHoverBadge";
import api from "../lib/api";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
let host, root;
beforeEach(() => { host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host); jest.clearAllMocks(); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
const badge = { label: "سناب", mark: "S", className: "" };
const campaign = { campaignId: "ny-campaign", campaignDisplay: "حملة الشتاء" };

test("hover shows independent Salla values and account-local timestamp without fetching", async () => {
    await act(async () => root.render(<CampaignHoverBadge badge={badge} campaign={campaign} report={{
        status: "partial", timezone: "America/New_York", date: "2026-11-13",
        orders: 1, pieces: 4, sales_sar: 375, spend_sar: null,
        spend_reason: "snapchat_hours_incomplete", updated_at: "2026-11-13T12:05:00Z",
    }} />));
    const icon = host.querySelector('[role="button"]');
    expect(icon.getAttribute("aria-label")).toContain("مصدر الطلب: سناب");
    expect(icon.getAttribute("title")).toBe("حملة الشتاء");
    await act(async () => icon.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
    const popup = document.querySelector('[role="tooltip"]');
    expect(popup.textContent).toContain("4 قطعة");
    expect(popup.textContent).toContain("375.00");
    expect(popup.textContent).toContain("غير مكتملة");
    expect(popup.textContent).toContain("07:05");
    expect(api.get).not.toHaveBeenCalled();
});

test("tap prevents navigation of enclosing order link and Escape dismisses", async () => {
    await act(async () => root.render(<a href="/orders-v2/123"><CampaignHoverBadge badge={badge} campaign={campaign} /></a>));
    const icon = host.querySelector('[role="button"]');
    const click = new MouseEvent("click", { bubbles: true, cancelable: true });
    await act(async () => icon.dispatchEvent(click));
    expect(click.defaultPrevented).toBe(true);
    expect(document.querySelector('[role="tooltip"]')).not.toBeNull();
    await act(async () => icon.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    expect(api.get).not.toHaveBeenCalled();
});
