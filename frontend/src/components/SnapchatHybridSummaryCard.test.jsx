import { renderToStaticMarkup } from "react-dom/server";
import SnapchatHybridSummaryCard from "./SnapchatHybridSummaryCard";


function visibleText(markup) {
    return markup
        .replace(/<[^>]+>/g, " ")
        .replace(/\s+/g, " ")
        .trim();
}


test("renders Salla actual KPIs separately from Snapchat attributed conversions", () => {
    const markup = renderToStaticMarkup(
        <SnapchatHybridSummaryCard
            data={{
                today: {
                    date: "2026-08-02",
                    spend: 4332.35,
                    orders: 42,
                    actual_orders: 42,
                    revenue: 9116.97,
                    actual_revenue: 9116.97,
                    roas: 2.1,
                    cost_per_order: 103.15,
                    attributed_orders: 15,
                    attributed_revenue: 3108.88,
                    attributed_roas: 0.72,
                    attributed_cpa: 288.82,
                    attribution_gap_orders: 27,
                    attribution_coverage_pct: 35.71,
                    active_orders: 42,
                    cancelled_orders: 0,
                    refunded_orders: 0,
                    conversion_data_provisional: true,
                },
                month: {
                    start: "2026-08-01",
                    spend: 6778.66,
                    orders: 72,
                    actual_orders: 72,
                    revenue: 17111.76,
                    actual_revenue: 17111.76,
                    roas: 2.52,
                    cost_per_order: 94.15,
                    attributed_orders: 19,
                    attributed_revenue: 4099.81,
                    attributed_roas: 0.6,
                    attributed_cpa: 356.77,
                    attribution_gap_orders: 53,
                    attribution_coverage_pct: 26.39,
                    active_orders: 71,
                    cancelled_orders: 1,
                    refunded_orders: 0,
                },
                conversion_freshness: {
                    provisional: true,
                    conversion_data_processed_end_time: "2026-08-02T00:00:00+00:00",
                },
            }}
        />,
    );
    const text = visibleText(markup);

    expect(markup).toContain('data-testid="snapchat-hybrid-summary-card"');
    expect(markup).toContain('data-testid="snap-hybrid-today-orders"');
    expect(markup).toContain('data-testid="snap-hybrid-today-attributed-orders"');
    expect(text).toContain("Snapchat — الأداء الفعلي");
    expect(text).toContain("الطلبات الفعلية 42");
    expect(text).toContain("المبيعات الفعلية 9,116.97 ر.س");
    expect(text).toContain("ROAS الفعلي 2.10×");
    expect(text).toContain("CPA الفعلي 103.15 ر.س");
    expect(text).toContain("تحويلات سناب المنسوبة 15");
    expect(text).toContain("مبيعات سناب المنسوبة 3,108.88 ر.س");
    expect(text).toMatch(/فجوة الإسناد \+\s*27 طلب/);
    expect(text).toMatch(/35\.71\s*%/);
    expect(text).toContain("فعالة: 42");
    expect(text).toContain("ملغية: 1");
    expect(text).toContain("تحويلات Snapchat المنسوبة ما زالت مؤقتة");
});


test("does not render without a today summary", () => {
    expect(renderToStaticMarkup(<SnapchatHybridSummaryCard data={null} />)).toBe("");
});
