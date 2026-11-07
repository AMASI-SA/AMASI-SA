import React, { act } from "react";
import { createRoot } from "react-dom/client";
import ProductTrafficSources from "./ProductTrafficSources";

let host, root;
const source = { platform: "snapchat", platform_label: "سناب شات", account_id: "account-1", account_status: "confirmed", attribution_status: "confirmed", units_sold: 7,
    campaigns: [
        { campaign_id: "a", campaign_name: "شالات الشتاء", attribution_status: "confirmed", units_sold: 4 },
        { campaign_id: "b", campaign_name: "هدايا", attribution_status: "confirmed", units_sold: 2 },
        { campaign_id: null, attribution_status: "unknown", units_sold: 1 },
    ] };
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
    global.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); delete global.ResizeObserver; });
const render = async (sources = [source]) => act(async () => root.render(<ProductTrafficSources sources={sources} />));
const trigger = () => host.querySelector("button");
const popup = () => document.querySelector('[role="dialog"]');
const click = async element => act(async () => element.click());

test("hover reveals campaign quantities in a portal outside the clipped report", async () => {
    host.style.overflow = "hidden";
    await render();
    expect(popup()).toBeNull();
    await act(async () => trigger().dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
    expect(popup()).not.toBeNull();
    expect(host.contains(popup())).toBe(false);
    expect(Array.from(popup().querySelectorAll("tbody tr"), row => row.textContent)).toEqual(["شالات الشتاء4", "هدايا2", "حملة غير معروفة1"]);
    expect(popup().querySelector("tfoot").textContent).toBe("الإجمالي7");
    expect(trigger().getAttribute("aria-expanded")).toBe("true");
});

test("keyboard focus opens and Escape dismisses without navigating", async () => {
    await render();
    await act(async () => trigger().focus());
    expect(popup()).not.toBeNull();
    await act(async () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
    expect(popup()).toBeNull();
    expect(document.activeElement).toBe(trigger());
});

test("tap pins the table, a second tap closes, and explicit close works", async () => {
    await render();
    // Touch can focus a button before its click; the first tap must still open.
    await act(async () => trigger().focus());
    await click(trigger());
    expect(popup()).not.toBeNull();
    await click(trigger());
    expect(popup()).toBeNull();
    await click(trigger());
    await click(popup().querySelector('button[aria-label="إغلاق تفاصيل الحملات"]'));
    expect(popup()).toBeNull();
});

test("old responses retain all pieces in an explicit unknown campaign bucket", async () => {
    await render([{ ...source, campaigns: undefined }]);
    await click(trigger());
    expect(popup().querySelector("tbody").textContent).toBe("حملة غير معروفة7");
    expect(popup().textContent).not.toContain("شالات الشتاء");
});

test("direct traffic is not claimed as an advertising campaign and portal is print-hidden", async () => {
    await render([{ platform: "direct", platform_label: "مباشر", account_status: "not_applicable", attribution_status: "confirmed", units_sold: 3 }]);
    await click(trigger());
    expect(popup().querySelector("tbody").textContent).toBe("بدون حملة إعلانية3");
    expect(popup().classList.contains("product-traffic-campaigns")).toBe(true);
    expect(popup().classList.contains("print:hidden")).toBe(true);
    expect(host.querySelector("style").textContent).toContain(".product-traffic-campaigns { display: none !important;");
});

test("unknown and conflicting campaign names never appear as confirmed names", async () => {
    await render([{ ...source, campaigns: [{ campaign_id: "a", campaign_name: "unverified", attribution_status: "conflict", units_sold: 7 }] }]);
    await click(trigger());
    expect(popup().textContent).toContain("حملة غير معروفة — إسناد متعارض");
    expect(popup().textContent).not.toContain("unverified");
});

test("moving from badge into the floating table keeps it open, leaving closes it", async () => {
    await render();
    await act(async () => trigger().dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
    await act(async () => {
        trigger().dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: popup() }));
        popup().dispatchEvent(new MouseEvent("mouseover", { bubbles: true, relatedTarget: trigger() }));
        await new Promise(resolve => setTimeout(resolve, 200));
    });
    expect(popup()).not.toBeNull();
    await act(async () => {
        popup().dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: document.body }));
        await new Promise(resolve => setTimeout(resolve, 200));
    });
    expect(popup()).toBeNull();
});
