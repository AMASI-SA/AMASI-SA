import React, { act } from "react";
import { createRoot } from "react-dom/client";
import CampaignHoverBadge from "./CampaignHoverBadge";
import api from "../lib/api";

jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));

const badge = { label: "سناب", mark: "S", className: "" };
const campaign = { campaignId: "winter-campaign", campaignName: "حملة الشتاء" };
const completeReport = {
    status: "available", timezone: "America/Los_Angeles", date: "2026-11-13",
    orders: 2, pieces: 3, sales_sar: 500, spend_sar: 100, cpa_sar: 50, roas: 5,
    total_cost_sar: 155, product_cost_complete: true, costs_complete: true,
    operating_expenses_sar: 18, operating_expenses_ratio: 0.036, operating_expenses_complete: true,
    operating_basis: { date: "2026-11-14", timezone: "Asia/Riyadh" },
    net_profit_sar: 227, net_profit_available: true, profit_provisional: false,
};

let host, root, originalViewport, previousActEnvironment;
beforeEach(() => {
    previousActEnvironment = global.IS_REACT_ACT_ENVIRONMENT;
    global.IS_REACT_ACT_ENVIRONMENT = true;
    originalViewport = { innerWidth: window.innerWidth, innerHeight: window.innerHeight };
    jest.clearAllMocks();
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount());
    host.remove(); jest.restoreAllMocks(); Object.assign(window, originalViewport);
    global.IS_REACT_ACT_ENVIRONMENT = previousActEnvironment;
});

async function renderCard(overrides = {}, campaignOverride = campaign) {
    await act(async () => root.render(<CampaignHoverBadge badge={badge} campaign={campaignOverride}
        report={{ ...completeReport, ...overrides }} />));
    await act(async () => host.querySelector('[role="button"]').dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
    return document.querySelector('[role="tooltip"]');
}

function values(popup) {
    return Object.fromEntries([...popup.querySelectorAll("tr")].map(row => [
        row.querySelector("th").textContent, row.querySelector("td").textContent,
    ]));
}

test("adds costs, dynamic operating share and final profit while preserving campaign metrics", async () => {
    const popup = await renderCard();
    expect([...popup.querySelectorAll("th")].map(cell => cell.textContent)).toEqual([
        "الصرف المسجل", "عدد الطلبات", "عدد القطع", "إجمالي المبيعات", "إجمالي التكاليف",
        "المصروفات التشغيلية", "تكلفة الطلب", "العائد الإعلاني", "صافي الأرباح",
    ]);
    expect(values(popup)).toEqual({
        "الصرف المسجل": "100.00 ر.س", "عدد الطلبات": "2 طلب", "عدد القطع": "3 قطعة",
        "إجمالي المبيعات": "500.00 ر.س", "إجمالي التكاليف": "155.00 ر.س",
        "المصروفات التشغيلية": "18.00 ر.س (3.6%)", "تكلفة الطلب": "50.00 ر.س",
        "العائد الإعلاني": "5.00×", "صافي الأرباح": "227.00 ر.س",
    });
    expect(popup.textContent).toContain("2026-11-13");
    expect(popup.textContent).toContain("2026-11-14");
    expect(popup.textContent).toContain("Asia/Riyadh");
    expect(popup.textContent).toContain("الملخص التنفيذي");
    expect(api.get).not.toHaveBeenCalled();
});

test.each([
    { product_cost_complete: false, costs_complete: false },
    { product_cost_complete: false, costs_complete: true },
    { product_cost_complete: true, costs_complete: false },
])("incomplete direct costs suppress stale cost and profit numbers: %j", async flags => {
    const popup = await renderCard({ ...flags, total_cost_sar: 876543.21, net_profit_sar: 987654.32 });
    expect(values(popup)["إجمالي التكاليف"]).toBe("التكلفة غير مكتملة");
    expect(values(popup)["صافي الأرباح"]).toBe("ناقص التكلفة");
    expect(popup.textContent).not.toContain("876,543.21");
    expect(popup.textContent).not.toContain("987,654.32");
    expect(values(popup)["إجمالي المبيعات"]).toBe("500.00 ر.س");
});

test.each([
    { product_cost_complete: null },
    { costs_complete: undefined },
    { total_cost_sar: null },
    { net_profit_available: false },
    { net_profit_available: undefined },
    { spend_sar: null },
])("unknown financial evidence suppresses stale profit: %j", async incomplete => {
    const popup = await renderCard({ ...incomplete, net_profit_sar: 987654.32 });
    expect(values(popup)["صافي الأرباح"]).toBe("غير مكتمل");
    expect(popup.textContent).not.toContain("987,654.32");
});

test.each([
    { operating_expenses_complete: false },
    { operating_expenses_complete: undefined },
    { operating_expenses_sar: null },
    { operating_expenses_ratio: null },
])("unavailable operating share does not turn into zero or a final profit: %j", async incomplete => {
    const popup = await renderCard({ ...incomplete, net_profit_sar: 987654.32 });
    expect(values(popup)["إجمالي التكاليف"]).toBe("155.00 ر.س");
    expect(values(popup)["المصروفات التشغيلية"]).toBe("غير مكتمل");
    expect(values(popup)["صافي الأرباح"]).toBe("غير مكتمل");
    expect(popup.textContent).not.toContain("987,654.32");
});

test.each([
    { profit_provisional: true },
    { spend_warning: "snapchat_hours_incomplete", missing_hours: 2 },
    { spend_warning: "snapchat_hour_coverage_unverified" },
])("provisional evidence labels profit without hiding available financials: %j", async warning => {
    const popup = await renderCard(warning);
    expect(values(popup)["صافي الأرباح"]).toContain("227.00 ر.س");
    expect(values(popup)["صافي الأرباح"]).toContain("تقديري");
    expect(values(popup)["الصرف المسجل"]).toBe("100.00 ر.س");
    expect(values(popup)["تكلفة الطلب"]).toBe("50.00 ر.س");
    expect(values(popup)["العائد الإعلاني"]).toBe("5.00×");
});

test("zero orders with known zero costs and operating allocation can retain an advertising loss", async () => {
    const popup = await renderCard({ orders: 0, pieces: 0, sales_sar: 0, spend_sar: 50,
        total_cost_sar: 0, operating_expenses_sar: 0, net_profit_sar: -50, cpa_sar: null, roas: 0 });
    expect(values(popup)["إجمالي التكاليف"]).toBe("0.00 ر.س");
    expect(values(popup)["المصروفات التشغيلية"]).toBe("0.00 ر.س (3.6%)");
    expect(values(popup)["صافي الأرباح"]).toBe("-50.00 ر.س");
    expect(values(popup)["تكلفة الطلب"]).toBe("لا توجد طلبات");
});

test("negative profit is displayed without clamping and missing profit is not recomputed", async () => {
    let popup = await renderCard({ total_cost_sar: 386.75, net_profit_sar: -4.75 });
    expect(values(popup)["صافي الأرباح"]).toBe("-4.75 ر.س");
    popup = await renderCard({ net_profit_sar: null });
    expect(values(popup)["صافي الأرباح"]).toBe("غير مكتمل");
});

test("label-only Salla aggregation can show known costs and operating share while profit stays unknown", async () => {
    const popup = await renderCard({ commerce_scope: "salla_utm_label", period_basis: "salla_store_day",
        spend_sar: null, cpa_sar: null, roas: null, net_profit_sar: 987654.32 },
    { campaignId: null, campaignName: "فروه المطرزه عرفات" });
    expect(values(popup)["إجمالي التكاليف"]).toBe("155.00 ر.س");
    expect(values(popup)["المصروفات التشغيلية"]).toBe("18.00 ر.س (3.6%)");
    expect(values(popup)["صافي الأرباح"]).toBe("غير مكتمل");
    expect(popup.textContent).toContain("غير مربوط إعلانياً");
    expect(popup.textContent).not.toContain("987,654.32");
});

test("the measured nine-row card stays within the viewport and adapts on resize", async () => {
    window.innerWidth = 420; window.innerHeight = 600;
    const originalBounds = HTMLElement.prototype.getBoundingClientRect;
    jest.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function () {
        if (this.getAttribute("role") === "button") return { left: 390, top: 450, bottom: 470, width: 20, height: 20 };
        if (this.getAttribute("role") === "tooltip") return { height: Math.min(650, window.innerHeight - 20) };
        return originalBounds.call(this);
    });
    const popup = await renderCard({ spend_warning: "snapchat_hours_incomplete", missing_hours: 2 });
    const checkFits = () => {
        expect(parseFloat(popup.style.left)).toBeGreaterThanOrEqual(10);
        expect(parseFloat(popup.style.left) + parseFloat(popup.style.width)).toBeLessThanOrEqual(window.innerWidth - 10);
        expect(parseFloat(popup.style.top)).toBeGreaterThanOrEqual(10);
        expect(parseFloat(popup.style.top) + popup.getBoundingClientRect().height).toBeLessThanOrEqual(window.innerHeight - 10);
    };
    checkFits();
    await act(async () => {
        window.innerWidth = 280; window.innerHeight = 400;
        window.dispatchEvent(new Event("resize"));
    });
    checkFits();
    expect(api.get).not.toHaveBeenCalled();
});

test("a pinned card permits pointer and keyboard scrolling without opening its order or fetching", async () => {
    const navigate = jest.fn();
    await act(async () => root.render(<a href="/orders-v2/123" onClick={navigate}>
        <CampaignHoverBadge badge={badge} campaign={campaign} report={completeReport} />
    </a>));
    const icon = host.querySelector('[role="button"]');
    await act(async () => { icon.focus(); icon.click(); });
    const popup = document.querySelector('[role="tooltip"]');
    expect(popup.style.pointerEvents).toBe("auto");
    expect(popup.style.overflowY).toBe("auto");
    expect(popup.tabIndex).toBe(0);
    await act(async () => icon.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })));
    expect(document.activeElement).toBe(popup);
    expect(document.querySelector('[role="tooltip"]')).toBe(popup);
    await act(async () => popup.querySelector("td").click());
    expect(navigate).not.toHaveBeenCalled();
    await act(async () => popup.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })));
    expect(document.querySelector('[role="tooltip"]')).toBeNull();
    expect(document.activeElement).toBe(icon);
    expect(icon.getAttribute("aria-expanded")).toBe("false");
    expect(api.get).not.toHaveBeenCalled();
});
