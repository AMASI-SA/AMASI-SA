import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import SoldFinancialReport, { financialMetrics, FinancialDetails, splitProductCosts } from "./SoldFinancialReport";
jest.mock("react-router-dom", () => ({ Link: ({ children }) => <span>{children}</span> }), { virtual: true });
const data = { totals: { total_orders: 2, total_sales: 400, total_ads_cost: 40, total_shipping_cost: 30, total_payment_fees: 10, operating_expenses_total: 20, product_cost_complete: true, ads_spend_data_complete: true }, ads_v2: { spend_quality: { status: "complete", amount_complete: true }, executive_breakdown: { providers: { snapchat: { spend_sar: 40 } } } } };
const products = [{ cost_status: "complete", total_cost: 100 }];
test("average includes all costs once; ads already SAR; zero orders has no average", () => {
    expect(financialMetrics(data, products)).toMatchObject({ total: 200, average: 100, ads: 40, orders: 2 });
    expect(financialMetrics({ ...data, totals: { ...data.totals, total_orders: 0 } }, products).average).toBeNull();
});
test("missing options, missing financial amounts and unverified ads cannot become complete totals", () => {
    expect(financialMetrics(data, [{ cost_status: "incomplete", total_cost: null, known_cost_total: 50 }])).toMatchObject({ product: null, productKnown: 50, total: null, average: null });
    expect(financialMetrics({ ...data, ads_v2: {} }, products).total).toBeNull();
    expect(financialMetrics({ ...data, totals: { ...data.totals, total_payment_fees: null } }, products).total).toBeNull();
});
test("print details include every financial table without opening cards", () => {
    const html = renderToStaticMarkup(<FinancialDetails data={data} />);
    for (const label of ["تفاصيل صرف المنصات", "تفاصيل شركات الشحن", "تفاصيل عمولات طرق الدفع", "تفاصيل المصروفات التشغيلية", "رواتب الموظفين", "إيجارات"]) expect(html).toContain(label);
    const summary = renderToStaticMarkup(<SoldFinancialReport data={data} products={products} printable />);
    expect(summary).toContain("متوسط قيمة الطلب");
    expect(summary).not.toContain('<table');
});

test("missing payment amounts remain unknown in the financial table", () => {
    const html = renderToStaticMarkup(<FinancialDetails data={{ totals: { total_payment_fees: null }, payment_breakdown: [{ name: "دفع غير محسوم", fee_amount: null }] }} section="payment" />);
    expect(html).toContain("غير مكتمل");
    expect(html).not.toContain("0.00 ر.س");
});

test("zero-order periods show no average rather than a missing cost", () => {
    const html = renderToStaticMarkup(<SoldFinancialReport data={{ ...data, totals: { ...data.totals, total_orders: 0 } }} products={[]} printable />);
    expect(html).toContain("متوسط قيمة الطلب");
    expect(html).toContain("—");
    expect(html).not.toContain("غير مكتمل");
});


test("actual nested payment rollups do not invent missing per-method VAT", () => {
    const html = renderToStaticMarkup(<FinancialDetails data={{ totals: { total_payment_fees: 7 }, payment_breakdown: [{ name: "بطاقات", vat_amount: 1, sub_methods: [{ key: "mada", display: "مدى", total_sales: 100, fee_amount: 7, orders_count: 2 }] }] }} section="payment" />);
    expect(html).toContain("مدى");
    expect(html).not.toContain("غير مكتمل");
    expect(html).not.toContain("الضريبة");
});

const detailed = [{ cost_status: "complete", total_cost: 100, cost_groups: [{ quantity: 2, complete: true, cells: [
    { key: "base", kind: "product", status: "costed", unit_cost: 30 },
    { key: "bag", kind: "component", status: "costed", unit_cost: 5 },
    { key: "name", kind: "service", status: "costed", unit_cost: 15 },
] }] }];
test("separates costs without double counting; acquisition and basket averages differ", () => {
    const m = financialMetrics(data, detailed);
    expect(m).toMatchObject({ total: 200, profit: 200, basketAverage: 200, adAverage: 20 });
    expect(m.split).toEqual({ product: { known: 60, complete: true }, component: { known: 10, complete: true }, service: { known: 30, complete: true } });
    const html = renderToStaticMarkup(<SoldFinancialReport data={data} products={detailed} printable />);
    expect(html).toContain("صافي الربح");
    expect(html).toContain("15.00% من المبيعات");
    expect(html).toContain("50.00% من المبيعات");
});
test("unknown service preserves known base but withholds profit; legacy split stays unknown", () => {
    const rows = JSON.parse(JSON.stringify(detailed));
    Object.assign(rows[0], { cost_status: "incomplete", total_cost: null, known_cost_total: 70 });
    rows[0].cost_groups[0].complete = false;
    Object.assign(rows[0].cost_groups[0].cells[2], { status: "unresolved", unit_cost: null });
    const m = financialMetrics(data, rows);
    expect(m.profit).toBeNull();
    expect(m.split.product).toEqual({ known: 60, complete: true });
    expect(m.split.service.complete).toBe(false);
    expect(splitProductCosts(products).product.complete).toBe(false);
    const html = renderToStaticMarkup(<SoldFinancialReport data={data} products={rows} printable />);
    expect(html).toContain("صافي الربح");
    expect(html).toContain("غير مكتمل");
});
test("zero sales never prints infinite percentages", () => {
    const html = renderToStaticMarkup(<SoldFinancialReport data={{ ...data, totals: { ...data.totals, total_sales: 0 } }} products={detailed} printable />);
    expect(html).not.toContain("Infinity");
    expect(html).toContain("النسبة غير متاحة");
});
