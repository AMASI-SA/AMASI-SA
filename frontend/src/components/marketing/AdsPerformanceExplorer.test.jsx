import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AdsPerformanceExplorer, {
    buildAdsChartRows,
    toggleMetricVisibility,
} from "./AdsPerformanceExplorer";

describe("AdsPerformanceExplorer", () => {
    test("normalizes each metric independently while retaining raw values", () => {
        const rows = buildAdsChartRows([
            { date: "2026-08-01", spend_sar: 50, sales_sar: 100, orders: 2, roas: 2 },
            { date: "2026-08-02", spend_sar: 100, sales_sar: 50, orders: 4, roas: 0.5 },
        ]);

        expect(rows[0].date).toBe("1/8");
        expect(rows[0].spend).toBe(50);
        expect(rows[1].spend).toBe(100);
        expect(rows[0].sales).toBe(100);
        expect(rows[1].orders).toBe(100);
        expect(rows[0].spend_raw).toBe(50);
        expect(rows[1].roas_raw).toBe(0.5);
    });

    test("toggles exactly the selected metric", () => {
        const current = new Set(["orders", "sales", "roas", "spend"]);
        const hidden = toggleMetricVisibility(current, "roas");
        expect(hidden.has("roas")).toBe(false);
        expect(hidden.has("spend")).toBe(true);
        expect(current.has("roas")).toBe(true);
        expect(toggleMetricVisibility(hidden, "roas").has("roas")).toBe(true);
    });

    test("renders real one-day bar charts and keeps metric toggles interactive", async () => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        const container = document.createElement("div");
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(
                <AdsPerformanceExplorer
                    totals={{ orders: 4, sales_sar: 100, roas: 2, spend_sar: 50 }}
                    daily={[{ date: "2026-08-04", orders: 4, sales_sar: 100, roas: 2, spend_sar: 50 }]}
                    platformLabel="سناب شات"
                />,
            );
        });

        expect(container.querySelector('[data-testid="ads-performance-single-day-chart"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="ads-performance-single-day-bar-orders"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="ads-performance-single-day-bar-sales"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="ads-performance-single-day-bar-roas"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="ads-performance-single-day-bar-spend"]')).not.toBeNull();
        expect(container.textContent).toContain("رسم أداء يوم واحد");
        expect(container.textContent).toContain("50.00 ر.س");

        const roas = container.querySelector('[data-testid="ads-performance-metric-roas"]');
        expect(roas.getAttribute("aria-pressed")).toBe("true");

        await act(async () => {
            roas.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        });
        expect(roas.getAttribute("aria-pressed")).toBe("false");
        expect(container.querySelector('[data-testid="ads-performance-single-day-bar-roas"]')).toBeNull();
        expect(container.textContent).toContain("3 من 4 مؤشرات ظاهرة");

        await act(async () => root.unmount());
        container.remove();
    });
});
