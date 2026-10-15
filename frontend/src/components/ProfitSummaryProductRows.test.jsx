import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { MezanV2ProductCostModal } from "./ProfitSummaryCard";


jest.mock("react-router-dom", () => ({
    Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
}));


test("renders Mezan V2 product profitability rows and direct cost links", () => {
    const html = renderToStaticMarkup(
        <MezanV2ProductCostModal
            open
            onClose={() => {}}
            fromDate="2026-08-01"
            toDate="2026-08-02"
            data={{
                    total: 120,
                    product_profit_summary: {
                        product_count: 2,
                        total_units: 5,
                        total_sales: 500,
                        has_unpriced_products: true,
                    },
                    product_rows: [
                        {
                            identity: "missing-product",
                            mezan_product_id: "m-1",
                            salla_product_id: "s-1",
                            catalog_product_found: true,
                            name: "منتج بدون تكلفة ميزان",
                            sku: "AMS-1",
                            image_url: "https://example.com/product.jpg",
                            units_sold: 3,
                            average_unit_cost: 30,
                            total_sales: 300,
                            total_cost: 90,
                            net_profit: 210,
                            cost_status: "salla_fallback",
                        },
                        {
                            identity: "complete-product",
                            salla_product_id: "s-2",
                            catalog_product_found: true,
                            name: "منتج مكتمل",
                            sku: "AMS-2",
                            units_sold: 2,
                            average_unit_cost: 15,
                            total_sales: 200,
                            total_cost: 30,
                            net_profit: 170,
                            cost_status: "complete",
                        },
                    ],
            }}
        />,
    );

    expect(html).toContain("ربحية المنتجات — ميزان 2");
    expect(html).toContain("تكلفة القطعة");
    expect(html).toContain("إجمالي المبيعات");
    expect(html).toContain("إجمالي التكلفة");
    expect(html).toContain("صافي الأرباح");
    expect(html).toContain("منتج بدون تكلفة ميزان");
    expect(html).toContain("تكلفة سلة فقط — أضف تكلفة ميزان");
    expect(html).toContain("product=m-1");
    expect(html).toContain("focus=cost");
    expect(html).toContain("missing_mezan_cost=1");
    expect(html).toContain("from=2026-08-01");
    expect(html).toContain("https://example.com/product.jpg");
    expect(html).toContain("210.00 ر.س");
});


test("shows ten best-selling products per page with pagination arrows", () => {
    const productRows = Array.from({ length: 11 }, (_, index) => ({
        identity: `product-${index + 1}`,
        salla_product_id: `s-${index + 1}`,
        catalog_product_found: true,
        name: `منتج ${index + 1}`,
        units_sold: index + 1,
        average_unit_cost: 10,
        total_sales: (index + 1) * 20,
        total_cost: (index + 1) * 10,
        net_profit: (index + 1) * 10,
        cost_status: "complete",
    }));
    const html = renderToStaticMarkup(
        <MezanV2ProductCostModal
            open
            onClose={() => {}}
            data={{
                total: 660,
                product_profit_summary: {
                    product_count: 11,
                    total_units: 66,
                    total_sales: 1320,
                },
                product_rows: productRows,
            }}
        />,
    );

    expect(html).toContain("10 منتجات في الصفحة");
    expect(html).toContain("الأعلى كمية مباعة أولًا");
    expect(html).toContain('data-current-page="1"');
    expect(html).toContain('data-total-pages="2"');
    expect(html).toContain("aria-label=\"الصفحة السابقة\"");
    expect(html).toContain("aria-label=\"الصفحة التالية\"");
    expect(html).toContain('data-testid="mezan-v2-product-profit-row-product-11"');
    expect(html).not.toContain('data-testid="mezan-v2-product-profit-row-product-1"');
});
