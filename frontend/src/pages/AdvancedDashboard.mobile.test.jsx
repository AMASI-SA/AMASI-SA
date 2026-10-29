import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { LatestOrders, ProfitCard } from "./AdvancedDashboard";

jest.mock("react-router-dom", () => ({
    Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
}));

let host;
let root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(() => {
    act(() => root.unmount());
    host.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
});

test("orders collapse with an icon-only control while the count and average remain in the header", () => {
    act(() => root.render(<LatestOrders totals={{ total_orders: 2, total_sales: 240 }} orders={[
        { order_number: "1001", customer: { name: "عميل تجريبي" }, totals: { total: 120, currency: "QAR" } },
    ]} />));
    const toggle = host.querySelector('.dashboard-panel-toggle');
    expect(toggle.textContent).toBe("");
    expect(toggle.querySelector("svg")).not.toBeNull();
    expect(toggle.getAttribute("aria-label")).toBe("طي بطاقة أحدث الطلبات");
    act(() => toggle.click());
    const header = host.querySelector('.dashboard-panel-header');
    expect(header.textContent).toContain("أحدث الطلبات");
    expect(header.textContent).toContain("2 طلب");
    expect(header.textContent).toContain("120.00 ر.س");
    expect(host.querySelector('a').closest('[hidden]')).not.toBeNull();
    act(() => toggle.click());
    expect(host.querySelector('a').closest('[hidden]')).toBeNull();
    expect(host.querySelector('a').textContent).toContain("120.00 QAR");
});

test.each([
    ["ads", "أداء المنصات الإعلانية", ["TikTok", "Meta", "Google Ads", "الإجمالي"]],
    ["shipping", "تفاصيل تكاليف الشحن", ["الشركة", "الشحنات", "سعر الوحدة", "ضريبة الوحدة", "الإجمالي"]],
    ["payment", "تفاصيل رسوم طرق الدفع", ["طريقة الدفع / الحساب", "الطلبات", "المبلغ الخاضع", "نسبة العمولة", "VAT", "إجمالي الرسوم"]],
])("%s details retain every column inside their own keyboard-accessible scroll region", (key, label, headings) => {
    const data = {
        totals: { total_sales: 1000, total_orders: 4, total_ads_cost: 100, total_shipping_cost: 20, total_payment_fees: 10 },
        dashboard_platform_spend: { date_from: "2026-10-26", date_to: "2026-10-26",
            providers: { snapchat: { accounts: [
                { account_id: "usd", display_name: "US account", currency: "USD", projection_timezone: "Asia/Riyadh", total_native: 21.685333, total_sar: 81.32,
                    quality: { amount_available: true, status: "provisional", provisional: true } },
                { account_id: "sar", display_name: "Saudi account", currency: "SAR", projection_timezone: "Asia/Riyadh", total_native: 0, total_sar: 0,
                    quality: { amount_available: true, status: "provisional", provisional: true } },
            ] } } },
        shipping_breakdown: [{ name: "شركة تجريبية", orders_count: 1, total_cost: 20 }],
        payment_breakdown: [{ name: "مدى", orders_count: 1, total_sales: 100, fee_amount: 10 }],
    };
    act(() => root.render(<ProfitCard data={data} />));
    act(() => host.querySelector(`[data-testid="advanced-profit-row-${key}"]`).click());
    const region = host.querySelector(`[role="region"][aria-label="${label}"]`);
    expect(region).not.toBeNull();
    expect(region.tabIndex).toBe(0);
    expect(region.classList.contains("dashboard-table-scroll")).toBe(true);
    expect(Array.from(region.querySelectorAll('thead th'), (th) => th.textContent)).toEqual(
        key === "ads" ? ["المقياس", ...headings] : headings,
    );
    if (key === "ads") {
        const accountRegion = host.querySelector('[role="region"][aria-label="صرف كل حساب سناب"]');
        expect(accountRegion).not.toBeNull();
        expect(accountRegion.tabIndex).toBe(0);
        expect(accountRegion.classList.contains("dashboard-table-scroll")).toBe(true);
        expect(accountRegion.querySelectorAll("tbody tr")).toHaveLength(2);
        expect(accountRegion.textContent).toContain("سناب الدولار");
        expect(accountRegion.textContent).toContain("سناب السعودي");
        expect(accountRegion.textContent).toContain("81.32 ر.س");
        expect(accountRegion.textContent).not.toContain("Snapchat");
    }
    const toggle = host.querySelector('.dashboard-panel-toggle');
    act(() => toggle.click());
    act(() => toggle.click());
    expect(host.querySelector(`[role="region"][aria-label="${label}"]`)).toBe(region);
    expect(region.closest('[hidden]')).toBeNull();
});

test.each(["snapchat", "tiktok", "meta", "google"])("%s badge shows only its campaign without fetching", (channel) => { const spy = jest.fn(); const original = global.fetch; global.fetch = spy; try { act(() => root.render(<LatestOrders orders={[{ order_number: "hover-test", source: { channel, campaign_name: "Winter campaign" } }]} />)); const badge = host.querySelector("span[title=\"Winter campaign\"]"); expect(badge).not.toBeNull(); badge.dispatchEvent(new MouseEvent("mouseover", { bubbles: true })); expect(spy).not.toHaveBeenCalled(); } finally { global.fetch = original; } });
