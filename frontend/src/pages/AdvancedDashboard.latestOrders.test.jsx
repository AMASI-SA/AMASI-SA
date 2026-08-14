import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import { AbandonedCartsCard, LatestOrders, SummaryStrip } from "./AdvancedDashboard";

test("latest orders mirrors the Orders V2 information row", () => {
    const markup = renderToStaticMarkup(
        <MemoryRouter>
            <LatestOrders orders={[
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
});

test("summary strip includes current month order and sales cards", () => {
    const markup = renderToStaticMarkup(
        <MemoryRouter>
            <SummaryStrip
                data={{
                    totals: { total_orders: 5, total_sales: 500, avg_cost_per_order: 20, overall_roas: 3 },
                    product_cost_v2: { missing_products_count: 0 },
                    month_kpis: { total_orders: 903, total_sales: 169155 },
                }}
                filters={{ from: "2026-08-15", to: "2026-08-15" }}
            />
        </MemoryRouter>
    );

    expect(markup).toContain("طلبات الشهر");
    expect(markup).toContain("903");
    expect(markup).toContain("مبيعات الشهر");
    expect(markup).toContain("169,155.00 ر.س");
    expect(markup).toContain("min-\[1180px\]:grid-cols-6");
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
    const markup = renderToStaticMarkup(<AbandonedCartsCard carts={carts} />);

    expect(markup).toContain("نورة أحمد");
    expect(markup).toContain("3 منتجات");
    expect(markup).toContain("https://cdn.example.com/product.png");
    expect(markup).toContain("منذ 1 ثانية");
    expect(markup).toContain("المزيد");
    expect(markup).not.toContain("عميل 6");
});
