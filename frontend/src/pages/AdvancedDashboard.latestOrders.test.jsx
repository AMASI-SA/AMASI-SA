import React from "react";
import fs from "fs";
import path from "path";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { AbandonedCartsCard, LatestOrders, SummaryStrip, TopProductsCard } from "./AdvancedDashboard";

const source = fs.readFileSync(path.join(__dirname, "AdvancedDashboard.jsx"), "utf8");

test("latest orders mirrors the Orders V2 information row", () => {
    const markup = renderToStaticMarkup(
        <MemoryRouter>
            <LatestOrders totals={{ total_orders: 2, total_sales: 337.33 }} orders={[
                {
                    order_number: "278106046",
                    created_at: new Date().toISOString(),
                    is_new: true,
                    status: "under_review",
                    customer: { name: "Rabea Ragheb", avatar_url: "https://cdn.example.com/customer.png" },
                    shipping: { address: { city: "Riyadh" } },
                    items: [{}, {}, {}],
                    payment: { method_native: "credit_card" },
                    source: { channel: "snapchat" },
                    totals: { total: 187.33 },
                },
                {
                    order_number: "278105072",
                    created_at: new Date().toISOString(),
                    is_new: false,
                    status_native: "بانتظار المراجعة",
                    customer: { name: "هاجر البارقي" },
                    shipping: { address: { city: "الرياض" } },
                    items: [{}, {}],
                    payment: { method_native: "mada" },
                    totals: { total: 150 },
                },
            ]} />
        </MemoryRouter>
    );

    expect(markup).toContain("Rabea Ragheb");
    expect(markup).toContain("https://cdn.example.com/customer.png");
    expect(markup).toContain("بانتظار المراجعة");
    expect(markup).toContain("3 قطعة");
    expect(markup).toContain("credit_card");
    expect(markup).toContain("مصدر الطلب: سناب");
    expect(markup.match(/جديد/g)).toHaveLength(1);
    expect(markup).toContain("returnTo=%2Fdashboard-advanced");
    expect(markup).toContain("2 طلب");
    expect(markup).toContain("متوسط:");
    expect(markup).toContain("168.67 ر.س");
});

test("summary strip includes current month order and sales cards", () => {
    const markup = renderToStaticMarkup(
        <MemoryRouter>
            <SummaryStrip
                data={{
                    totals: { total_orders: 5, total_sales: 500, avg_cost_per_order: 20, overall_roas: 3 },
                    product_cost_v2: { missing_products_count: 0 },
                    month_kpis: { total_orders: 903, total_sales: 169155 },
                    ads_v2: {
                        executive_breakdown: {
                            providers: {
                                snapchat: { platform_reported_orders: 4, platform_cost_per_order_sar: 25 },
                                tiktok: { platform_reported_orders: 2, platform_cost_per_order_sar: 20 },
                                meta: { platform_reported_orders: 1, platform_cost_per_order_sar: 30 },
                                google: { platform_reported_orders: null, platform_cost_per_order_sar: null },
                            },
                        },
                    },
                }}
                filters={{ from: "2026-08-15", to: "2026-08-15" }}
            />
        </MemoryRouter>
    );

    expect(markup).toContain("طلبات الشهر");
    expect(markup).toContain("903");
    expect(markup).toContain("مبيعات الشهر");
    expect(markup).toContain("169,155.00 ر.س");
    expect(markup).toContain("سناب:");
    expect(markup).toContain("متوسط:");
    expect(markup).toContain("25.00 ر.س");
    expect(markup).not.toContain("متوسط قيمة سلة المشتريات");
});

test("abandoned carts show customer, product count, image, time and more control", () => {
    const carts = Array.from({ length: 6 }, (_, index) => ({
        cart_id: `cart-${index + 1}`,
        customer_name: index === 0 ? "نورة أحمد" : `عميل ${index + 1}`,
        total: 99 + index,
        currency: "SAR",
        cart_updated_at: new Date().toISOString(),
        items: index === 0
            ? [{ quantity: 2, image_url: "https://cdn.example.com/product.png" }, { quantity: 1 }]
            : [{ quantity: 1 }],
    }));
    const markup = renderToStaticMarkup(<AbandonedCartsCard carts={carts} summary={{ abandoned_count: 24, recovered_count: 6 }} />);

    expect(markup).toContain("نورة أحمد");
    expect(markup).toContain("3 منتجات");
    expect(markup).toContain("https://cdn.example.com/product.png");
    expect(markup).toContain("منذ 1 ثانية");
    expect(markup).toContain("المزيد");
    expect(markup).toContain("متروكة 24");
    expect(markup).toContain("مكتملة 6");
    expect(markup).not.toContain("عميل 6");
});

test("top products header shows period counts and only five rows initially", () => {
    const rows = Array.from({ length: 7 }, (_, index) => ({
        identity: `product-${index + 1}`,
        name: `منتج ${index + 1}`,
        units_sold: 10 - index,
        total_sales: 1000 - index,
    }));
    const markup = renderToStaticMarkup(
        <TopProductsCard
            rows={rows}
            summary={{
                product_count: 7,
                salla_fallback_products_count: 3,
                missing_all_cost_products_count: 2,
            }}
        />
    );

    expect(markup).toContain("7 منتجًا خلال الفترة");
    expect(markup).toContain("بتكلفة سلة 3");
    expect(markup).toContain("بدون تكلفة 2");
    expect(markup).toContain("المزيد");
    expect(markup).not.toContain("منتج 6");
});

test("GA active-user bars stay inside their chart area", () => {
    expect(source).toContain('data-testid="advanced-ga-active-chart"');
    expect(source).toContain("overflow-hidden");
    expect(source).toContain("Number(m.active_users || 0) / minuteMax * 100");
    expect(source).toContain("Math.min(100");
});

test("profit summary keeps the four audited breakdowns in scrollable accordions", () => {
    expect(source).toContain('data-testid="advanced-profit-ads-details"');
    expect(source).toContain('data-testid="advanced-profit-shipping-details"');
    expect(source).toContain('data-testid="advanced-profit-payment-details"');
    expect(source).toContain('data-testid="advanced-profit-operating-details"');
    expect(source).toContain("max-h-72 overflow-auto");
    expect(source).toContain("aria-expanded={row.expandable ? expanded === row.key : undefined}");
});
