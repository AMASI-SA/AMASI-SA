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


const context = { product_id: "product-1", from_date: "2026-11-01", to_date: "2026-11-07", statuses: "reviewed", report_scope: "sold" };
const estimates = { currency: "SAR", allocation_method: "product_sales_share", rows: [{ campaign_id: "a", campaign_name: "شالات الشتاء", ad_group_id: "g", ad_group_name: "مجموعة الشتاء", ad_id: "ad", ad_name: "إعلان الشال", units_sold: 7, ad_spend_sar: 140, estimated_product_spend_sar: 70, estimated_unit_promotion_cost_sar: 10, status: "estimated" }] };
const settleHover = async () => act(async () => { await new Promise(resolve => setTimeout(resolve, 230)); });

test("promotion loads only after opening and reuses the mounted badge result", async () => {
    api.get.mockResolvedValue({ data: estimates });
    await render([source], context);
    expect(api.get).not.toHaveBeenCalled();
    await click(trigger());
    expect(api.get).not.toHaveBeenCalled();
    await settleHover();
    expect(api.get).toHaveBeenCalledTimes(1);
    const [url, options] = api.get.mock.calls[0];
    expect(url).toBe("/dashboard-v2/product-promotion-estimates");
    expect(options.params).toMatchObject({ ...context, platform: "snapchat", account_id: "account-1" });
    expect(options.timeout).toBe(20000);
    const table = popup().querySelector('table[aria-label="صرف الإعلانات ونصيب المنتج التقديري"]');
    expect([...table.querySelectorAll("tbody td")].map(cell => cell.textContent)).toEqual(["شالات الشتاء", "مجموعة الشتاء", "إعلان الشالتقدير على مستوى الإعلان", "7", "140.00 ر.س", "70.00 ر.س", "10.00 ر.س"]);
    expect(popup().textContent).toContain("ليس تكلفة شراء فردية مؤكدة");
    await click(trigger());
    await click(trigger());
    await settleHover();
    expect(api.get).toHaveBeenCalledTimes(1);
});

test("unknown account and unsupported long range do not trigger an estimate request", async () => {
    await render([{ ...source, account_id: null, account_status: "unknown" }], context);
    await click(trigger()); await settleHover();
    expect(api.get).not.toHaveBeenCalled();
    expect(popup().textContent).toContain("الحساب الإعلاني غير متحقق");
    await render([source], { ...context, to_date: "2026-12-07" });
    await click(trigger()); await settleHover();
    expect(api.get).not.toHaveBeenCalled();
    expect(popup().textContent).toContain("٣١ يومًا");
});

test("changed period aborts old request and cannot display its late response", async () => {
    let resolveOld;
    api.get.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }))
        .mockResolvedValueOnce({ data: { ...estimates, rows: [{ ...estimates.rows[0], ad_name: "الإعلان الجديد" }] } });
    await render([source], context); await click(trigger()); await settleHover();
    const signal = api.get.mock.calls[0][1].signal;
    await render([source], { ...context, from_date: "2026-11-02" });
    expect(signal.aborted).toBe(true);
    expect(popup().textContent).not.toContain("140.00");
    await settleHover();
    await act(async () => resolveOld({ data: estimates }));
    expect(popup().textContent).toContain("الإعلان الجديد");
    expect(popup().textContent).not.toContain("إعلان الشال");
    expect(api.get).toHaveBeenCalledTimes(2);
});

test("failed estimates retry explicitly and null spend never becomes zero", async () => {
    api.get.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ data: { ...estimates, rows: [{ ...estimates.rows[0], ad_spend_sar: null, estimated_product_spend_sar: null, estimated_unit_promotion_cost_sar: null, reason: "spend_coverage_missing" }] } });
    await render([source], context); await click(trigger()); await settleHover();
    expect(popup().textContent).toContain("تعذر تحميل تفاصيل الترويج");
    await click([...popup().querySelectorAll("button")].find(button => button.textContent === "إعادة المحاولة"));
    await settleHover();
    expect(popup().textContent).toContain("بيانات صرف الإعلان غير مكتملة");
    const table = popup().querySelector('table[aria-label="صرف الإعلانات ونصيب المنتج التقديري"]');
    expect([...table.querySelectorAll("tbody td")].slice(-3).map(cell => cell.textContent)).toEqual(["غير متاح", "غير متاح", "غير متاح"]);
});

test("different product counts withhold spending estimates until report refresh", async () => {
    api.get.mockResolvedValue({ data: { ...estimates, rows: [{ ...estimates.rows[0], units_sold: 6 }] } });
    await render([source], context); await click(trigger()); await settleHover();
    expect(popup().textContent).toContain("تغيرت بيانات الفترة؛ حدّث التقرير");
    expect(popup().textContent).not.toContain("140.00");
    expect(popup().querySelector("tfoot").textContent).toBe("الإجمالي7");
});


test("missing ad is displayed by its confirmed campaign name without invented ad spend", async () => {
    api.get.mockResolvedValue({ data: { ...estimates, rows: [{ ...estimates.rows[0], ad_id: null, ad_name: null, allocation_level: "campaign", ad_spend_sar: null, estimated_product_spend_sar: null, estimated_unit_promotion_cost_sar: null, reason: "ad_identity_unverified" }] } });
    await render([source], context); await click(trigger()); await settleHover();
    const table = popup().querySelector('table[aria-label="صرف الإعلانات ونصيب المنتج التقديري"]');
    const label = table.querySelectorAll("tbody td")[2].textContent;
    expect(label).toContain("شالات الشتاء");
    expect(label).toContain("على مستوى الحملة");
    expect(label).not.toContain("إعلان غير معروف");
    expect([...table.querySelectorAll("tbody td")].slice(-3).map(cell => cell.textContent)).toEqual(["غير متاح", "غير متاح", "غير متاح"]);
});
