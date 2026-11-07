import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import SoldProducts, { CostDetails, CostSummary, SoldTable } from "./SoldProducts";
import api from "../lib/api";

const mockPrint = jest.fn();
jest.mock("react-to-print", () => ({ useReactToPrint: ({ contentRef }) => () => mockPrint(contentRef.current.textContent) }), { virtual: true });
jest.mock("react-router-dom", () => ({
    Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
    useSearchParams: () => [new URLSearchParams("from=2026-11-01&to=2026-11-07")],
}), { virtual: true });
jest.mock("../lib/api", () => ({ get: jest.fn() }));
jest.mock("../lib/format", () => ({ formatMoney: value => Number(value).toFixed(2), formatInt: value => String(value) }));
jest.mock("../components/DateInput", () => ({ __esModule: true, default: props => <input type="date" {...props} />, isValidISODate: value => /^\d{4}-\d{2}-\d{2}$/.test(value) }));

beforeEach(() => { api.get.mockReset(); mockPrint.mockClear(); });

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
    for (const label of ["غير مطلوبة", "اختيار غير متحقق", "تكلفة ناقصة", "ضمن تكلفة المنتج", "تفصيل غير متاح"]) expect(html).toContain(label);
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
    expect(summary).not.toContain("غير مطلوبة");
    expect(details.match(/data-testid="sold-product-cost-details"/g)).toHaveLength(2);
});

test("refreshes every filtered product including unopened later pages and prints their details", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const rows = Array.from({ length: 21 }, (_, i) => product(`SKU${i}`));
    rows[20] = product("SKU20", { cost_status: "incomplete", total_cost: null, known_cost_total: 10, missing_cost_reasons: ["option_selection_unresolved:x"] });
    api.get.mockResolvedValueOnce({ data: { items: rows, count: 21 } });
    api.get.mockResolvedValueOnce({ data: { items: rows.map(row => product(row.sku)), count: 21 } });
    const host = document.createElement("div"); document.body.appendChild(host);
    const root = createRoot(host);
    await act(async () => { root.render(<SoldProducts />); });
    const screenSection = host.querySelector('main > section');
    expect(screenSection.textContent).not.toContain("SKU20");
    const refresh = [...host.querySelectorAll("button")].find(button => button.textContent.includes("تحديث جميع التكاليف"));
    await act(async () => { refresh.click(); });
    expect(api.get).toHaveBeenLastCalledWith("/dashboard-v2/sold-products", { params: { from_date: "2026-11-01", to_date: "2026-11-07", statuses: "default", refresh_costs: true } });
    expect(host.textContent).toContain("اكتملت تكلفة 1 منتج؛ المتبقي 0");
    const print = [...host.querySelectorAll("button")].find(button => button.textContent === "طباعة مع تفاصيل التكاليف");
    await act(async () => { print.click(); });
    expect(mockPrint.mock.calls.at(-1)[0]).toContain("SKU20");
    expect(mockPrint.mock.calls.at(-1)[0]).toContain("غير مطلوبة");
    await act(async () => root.unmount()); host.remove();
});

test("ignores an older response after filters are applied again", async () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    let resolveOld;
    api.get.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
    api.get.mockResolvedValueOnce({ data: { items: [product("NEW")], count: 1 } });
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
