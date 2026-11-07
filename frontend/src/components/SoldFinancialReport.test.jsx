import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import SoldFinancialReport, { financialMetrics, FinancialDetails } from "./SoldFinancialReport";
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
    expect(summary).toContain("متوسط التكلفة الكلية للطلب");
    expect(summary).not.toContain('<table');
});
