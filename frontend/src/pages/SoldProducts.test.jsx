import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import SoldProducts, { CostDetails, CostSummary, SoldTable } from "./SoldProducts";
import api from "../lib/api";
import { getDashboardAdsSpend } from "../services/dashboardAdsSpend";

const mockPrint = jest.fn();
const mockProductsGet = jest.fn();
jest.mock("react-to-print", () => ({ useReactToPrint: ({ contentRef }) => () => mockPrint(contentRef.current.textContent) }), { virtual: true });
jest.mock("react-router-dom", () => ({
    Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
    useSearchParams: () => [new URLSearchParams("from=2026-11-01&to=2026-11-07")],
}), { virtual: true });
jest.mock("../lib/api", () => ({ get: jest.fn() }));
jest.mock("../services/dashboardAdsSpend", () => ({ getDashboardAdsSpend: jest.fn() }));
jest.mock("../lib/format", () => ({ formatMoney: value => Number(value).toFixed(2), formatInt: value => String(value) }));
jest.mock("../components/DateInput", () => ({ __esModule: true, default: props => <input type="date" {...props} />, isValidISODate: value => /^\d{4}-\d{2}-\d{2}$/.test(value) }));

beforeEach(() => { getDashboardAdsSpend.mockImplementation(async ({ dateFrom, dateTo }) => ({ date_from: dateFrom, date_to: dateTo })); api.get.mockReset(); mockProductsGet.mockReset(); mockPrint.mockClear(); api.get.mockImplementation((url, options) => url === "/dashboard-v2" ? Promise.resolve({ data: { totals: { total_orders: 2 } } }) : mockProductsGet(url, options)); });

const product = (id, overrides = {}) => ({
    product_id: id, sku: id, name: `منتج ${id}`, units_sold: 2, cost_status: "complete", total_cost: 50, known_cost_total: 50,
    unit_cost: 25, cost_columns: [{ key: "base", label: "المنتج: شال", kind: "product" }, { key: "service", label: "خدمة: اسم", kind: "service" }],
    cost_groups: [{ key: id, quantity: 2, complete: true, unit_cost: 25, total_cost: 50, cells: [{ key: "base", status: "costed", unit_cost: 25 }, { key: "service", status: "not_required", unit_cost: 0 }] }],
    ...overrides,
});

test("distinguishes not requested, unresolved, missing, included and unavailable cells", () => {
    const statuses = ["not_required", "unresolved", "missing_cost", "included_in_base", "historical_unavailable"];
    const row = product("A", {
        cost_columns: statuses.map(status => ({ key: status, label: status, kind: "service" })),
        cost_groups: [{ key: "states", quantity: 1, complete: false, cells: statuses.map(status => ({ key: status, status, unit_cost: null })) }],
        cost_status: "incomplete", total_cost: null,
    });
    const html = renderToStaticMarkup(<CostDetails row={row} />);
    for (const label of ["غير محدد", "اختيار غير متحقق", "تكلفة ناقصة", "ضمن تكلفة المنتج", "تفصيل غير متاح"]) expect(html).toContain(label);
});

test("keeps partial known costs in summary while withholding a complete total", () => {
    const html = renderToStaticMarkup(<CostSummary rows={[product("A"), product("B", { cost_status: "incomplete", total_cost: null, known_cost_total: 20 })]} final />);
    expect(html).toContain("المعروف 70.00 ر.س — الإجمالي غير مكتمل");
    expect(html).toContain("متوسط تكلفة القطعة: غير مكتمل");
});

test("collapsed products omit details on screen but details print includes all rows", () => {
    const rows = [product("A"), product("B")];
    const screen = renderToStaticMarkup(<SoldTable rows={rows} expanded={{}} onToggle={() => {}} />);
    const summary = renderToStaticMarkup(<SoldTable rows={rows} printable />);
    const details = renderToStaticMarkup(<SoldTable rows={rows} printable allDetails />);
    expect(screen).not.toContain('data-testid="sold-product-cost-details"');
    expect(screen).toContain('aria-expanded="false"');
    expect(summary).not.toContain("غير محدد");
    expect(details.match(/data-testid="sold-product-cost-details"/g)).toHaveLength(2);
});

test("refreshes every filtered product including unopened later pages and prints their details", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const rows = Array.from({ length: 21 }, (_, i) => product(`SKU${i}`));
    rows[20] = product("SKU20", { cost_status: "incomplete", total_cost: null, known_cost_total: 10, missing_cost_reasons: ["option_selection_unresolved:x"] });
    mockProductsGet.mockResolvedValueOnce({ data: { items: rows, count: 21 } });
    mockProductsGet.mockResolvedValueOnce({ data: { items: rows.map(row => product(row.sku)), count: 21 } });
    const host = document.createElement("div"); document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(<SoldProducts />); });
    const screenSection = host.querySelector('main > section');
    expect(screenSection.textContent).not.toContain("SKU20");
    const refresh = [...host.querySelectorAll("button")].find(button => button.textContent.includes("تحديث جميع التكاليف"));
    await act(async () => { refresh.click(); });
    expect(mockProductsGet).toHaveBeenLastCalledWith("/dashboard-v2/sold-products", { params: { from_date: "2026-11-01", to_date: "2026-11-07", statuses: "default", refresh_costs: true } });
    expect(host.textContent).toContain("اكتملت تكلفة 1 منتج؛ المتبقي 0");
    const print = [...host.querySelectorAll("button")].find(button => button.textContent === "طباعة التقرير مع التفاصيل");
    await act(async () => { print.click(); });
    expect(mockPrint.mock.calls.at(-1)[0]).toContain("SKU20");
    expect(mockPrint.mock.calls.at(-1)[0]).toContain("غير محدد");
    await act(async () => root.unmount()); host.remove();
});

test("ignores an older response after filters are applied again", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    let resolveOld;
    mockProductsGet.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    mockProductsGet.mockResolvedValueOnce({ data: { items: [product("NEW")], count: 1 } });
    const host = document.createElement("div"); document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(<SoldProducts />); });
    await act(async () => { host.querySelector("form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
    await act(async () => { resolveOld({ data: { items: [product("OLD")], count: 1 } }); });
    expect(host.querySelector("main > section").textContent).toContain("NEW");
    expect(host.querySelector("main > section").textContent).not.toContain("OLD");
    const toggle = host.querySelector('button[aria-controls]');
    await act(async () => { toggle.click(); });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(host.querySelector('main > section [data-testid="sold-product-cost-details"]')).not.toBeNull();
    await act(async () => { toggle.click(); });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(host.querySelector('main > section [data-testid="sold-product-cost-details"]')).toBeNull();
    await act(async () => root.unmount()); host.remove();
});


test("labels dynamic columns by kind and never invents missing frozen breakdown choices", () => {
    const row = product("A", { cost_columns: [{key:"base", label:"شال", kind:"product"}, {key:"new", label:"تطريز", kind:"service"}, {key:"bag", label:"كيس", kind:"component"}] });
    const html = renderToStaticMarkup(<CostDetails row={row} />);
    expect(html).toContain("المنتج: شال"); expect(html).toContain("خدمة: تطريز"); expect(html).toContain("مكوّن: كيس");
    expect(html).toContain("تفصيل غير متاح"); expect(html).not.toContain("اختيار غير متحقق");
});

test("variants sharing product and SKU keep separate accordion and refresh identities", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const first = product("SAME", { report_key: "product:variant-1", name: "المتغير الأول" });
    const second = product("SAME", { report_key: "product:variant-2", name: "المتغير الثاني", cost_status: "incomplete", total_cost: null, missing_cost_reasons: ["base_cost_or_fx_missing"] });
    mockProductsGet.mockResolvedValueOnce({ data: { items: [first, second], count: 2 } });
    mockProductsGet.mockResolvedValueOnce({ data: { items: [first, { ...second, cost_status: "complete", total_cost: 50 }], count: 2 } });
    const host = document.createElement("div"); document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(<SoldProducts />); });
    const toggles = host.querySelectorAll('main > section button[aria-controls]');
    await act(async () => { toggles[0].click(); });
    expect(toggles[0].getAttribute("aria-expanded")).toBe("true");
    expect(toggles[1].getAttribute("aria-expanded")).toBe("false");
    expect(host.querySelectorAll('main > section [data-testid="sold-product-cost-details"]')).toHaveLength(1);
    const refresh = [...host.querySelectorAll("button")].find(button => button.textContent.includes("تحديث جميع التكاليف"));
    await act(async () => { refresh.click(); });
    expect(host.textContent).toContain("اكتملت تكلفة 1 منتج؛ المتبقي 0");
    expect(host.textContent).not.toContain("اكتملت تكلفة 2 منتج");
    await act(async () => root.unmount()); host.remove();
});

test("shows per-account source units on screen and excludes them from both print modes", () => {
    const rows = [product("sources", { units_sold: 6, traffic_sources: [
        { platform: "snapchat", platform_label: "سناب شات", account_id: "account-123", account_status: "confirmed", attribution_status: "confirmed", units_sold: 2 },
        { platform: "tiktok", platform_label: "تيك توك", account_id: null, account_status: "unknown", attribution_status: "platform_only", units_sold: 1 },
        { platform: "direct", platform_label: "مباشر", account_id: null, account_status: "not_applicable", attribution_status: "confirmed", units_sold: 3 },
    ] })];
    const screen = renderToStaticMarkup(<SoldTable rows={rows} onToggle={() => {}} />);
    expect(screen).toContain("account-123");
    expect(screen).toContain("حساب غير محدد");
    expect(screen).toContain("مباشر");
    for (const count of [1, 2, 3]) expect(screen).toContain(`${count} قطعة`);
    for (const allDetails of [false, true]) {
        const printed = renderToStaticMarkup(<SoldTable rows={rows} printable allDetails={allDetails} />);
        for (const value of ["account-123", "سناب شات", "تيك توك", "مباشر", "product-traffic-sources", "القطع حسب المنصة والحساب"]) expect(printed).not.toContain(value);
    }
});

test("missing source data remains explicitly unknown instead of direct", () => {
    const screen = renderToStaticMarkup(<SoldTable rows={[product("unknown")]} onToggle={() => {}} />);
    expect(screen).toContain("مصدر القطع غير متحقق");
    expect(screen).not.toContain("مباشر");
    expect(screen).toContain("@media print { .product-traffic-sources");
});


test("source quantities preserve fractional return-policy units and unavailable attribution stays explicit", () => {
    const rows = [product("fractional-sources", { units_sold: 2, traffic_sources: [
        { platform: "snapchat", platform_label: "سناب شات", account_id: "fractional-account", account_status: "confirmed", attribution_status: "confirmed", units_sold: 0.5 },
        { platform: "direct", platform_label: "مباشر", account_id: null, account_status: "not_applicable", attribution_status: "unavailable", units_sold: 1.5 },
    ] })];
    const screen = renderToStaticMarkup(<SoldTable rows={rows} onToggle={() => {}} />);
    expect(screen).toContain("0.5 قطعة");
    expect(screen).toContain("1.5 قطعة");
    expect(screen).toContain("تعذر تحديث المصدر");
    expect(screen).not.toContain("مباشر");
    for (const allDetails of [false, true]) {
        const printed = renderToStaticMarkup(<SoldTable rows={rows} printable allDetails={allDetails} />);
        expect(printed).not.toContain("تعذر تحديث المصدر");
        expect(printed).not.toContain("fractional-account");
    }
});

test("cost columns show quantity totals and footer sums while unit cost stays per piece", () => {
    const row = product("SHAWL", {
        units_sold: 5, total_cost: 105, cost_status: "complete",
        cost_columns: [{key:"base",label:"شال",kind:"product"},{key:"name",label:"تطريز الاسم",kind:"service"},{key:"bag",label:"كيس",kind:"component"}],
        cost_groups: [
            {key:"yes",quantity:3,complete:true,unit_cost:25,total_cost:75,cells:[{key:"base",status:"costed",unit_cost:15},{key:"name",status:"costed",unit_cost:8},{key:"bag",status:"costed",unit_cost:2}]},
            {key:"no",quantity:2,complete:true,unit_cost:15,total_cost:30,cells:[{key:"base",status:"costed",unit_cost:13},{key:"name",status:"not_required",unit_cost:null},{key:"bag",status:"costed",unit_cost:2}]},
        ],
    });
    const host = document.createElement("div");
    host.innerHTML = renderToStaticMarkup(<CostDetails row={row} />);
    const rows = [...host.querySelectorAll('tbody tr')].map(tr => [...tr.children].map(td => td.textContent));
    expect(rows).toEqual([["45.00","24.00","6.00","25.00","3","75.00"],["26.00","غير محدد","4.00","15.00","2","30.00"]]);
    expect([...host.querySelectorAll('tfoot td')].map(td => td.textContent)).toEqual(["71.00","24.00","10.00","الإجمالي","5","105.00"]);
});

test("platforms use compact icons and counts while direct and unknown remain text", () => {
    const row = product("icons", { traffic_sources: [
        {platform:"snapchat",platform_label:"سناب شات",account_id:"snap-1",account_status:"confirmed",units_sold:3},
        {platform:"tiktok",platform_label:"تيك توك",account_status:"unknown",units_sold:2},
        {platform:"direct",platform_label:"مباشر",account_status:"not_applicable",units_sold:1},
        {platform:"unknown",platform_label:"غير معروف",account_status:"unknown",units_sold:4},
    ]});
    const host = document.createElement("div"); host.innerHTML = renderToStaticMarkup(<SoldTable rows={[row]} expanded={{}} onToggle={() => {}} />);
    const list = host.querySelector('.product-traffic-sources ul');
    expect(list.textContent).not.toContain("سناب شات"); expect(list.textContent).not.toContain("تيك توك"); expect(list.textContent).not.toContain("حساب غير محدد");
    expect(list.textContent).toContain("3 قطعة"); expect(list.textContent).toContain("مباشر"); expect(list.textContent).toContain("غير معروف");
    expect(list.querySelector('li[aria-label*="snap-1"]').getAttribute('title')).toContain("سناب شات");
    expect(list.querySelectorAll('[aria-hidden="true"]')).toHaveLength(2);
});

test("period expenses can be printed even when no products were sold", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    mockProductsGet.mockResolvedValue({ data: { items: [], count: 0 } });
    const host = document.createElement("div"); document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(<SoldProducts />); });
    const print = [...host.querySelectorAll("button")].find(button => button.textContent === "طباعة التقرير مع التفاصيل");
    expect(print.disabled).toBe(false);
    await act(async () => { print.click(); });
    expect(mockPrint.mock.calls.at(-1)[0]).toContain("الملخص المالي للفترة");
    expect(mockPrint.mock.calls.at(-1)[0]).toContain("تفاصيل المصروفات التشغيلية");
    await act(async () => root.unmount()); host.remove();
});

test("product sources retain counts without loading campaign spend", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const row = product("source-product", { traffic_sources: [{ platform: "snapchat", platform_label: "سناب شات", account_id: "account-1", account_status: "confirmed", attribution_status: "confirmed", units_sold: 2 }] });
    mockProductsGet.mockResolvedValue({ data: { currency: "SAR", allocation_method: "product_sales_share", rows: [] } });
    const host = document.createElement("div"); document.body.appendChild(host);
    const root = createRoot(host);
    try {
        await act(async () => root.render(<SoldTable rows={[row]} range={{ from: "2026-11-02", to: "2026-11-05", statuses: "reviewed,in_progress" }} />));
        expect(api.get).not.toHaveBeenCalled();
        await act(async () => host.querySelector('.product-traffic-sources li').click());
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 230)); });
        expect(api.get).not.toHaveBeenCalled();
        expect(host.querySelector('.product-traffic-sources button')).toBeNull();
    } finally { await act(async () => root.unmount()); host.remove(); }
});
