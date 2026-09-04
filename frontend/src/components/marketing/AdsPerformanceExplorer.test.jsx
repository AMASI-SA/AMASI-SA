import React, { act } from "react";
import { createRoot } from "react-dom/client";

const mockGetCampaignReportSnapshot = jest.fn();

jest.mock("../../marketingCampaignResultSource", () => ({
    getCampaignReportSnapshot: (...args) => mockGetCampaignReportSnapshot(...args),
}));

import AdsPerformanceExplorer, {
    buildAdsChartRows,
    buildAdsHourlyChartInput,
    formatAdsHourLabel,
    toggleMetricVisibility,
} from "./AdsPerformanceExplorer";

describe("AdsPerformanceExplorer", () => {
    beforeEach(() => {
        mockGetCampaignReportSnapshot.mockReset();
        mockGetCampaignReportSnapshot.mockReturnValue(null);
    });

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

    test("keeps unavailable commercial metrics as null instead of fake zero", () => {
        const rows = buildAdsChartRows([
            {
                date: "2026-08-06",
                spend_sar: 2973.19,
                sales_sar: null,
                orders: null,
                roas: null,
            },
        ]);

        expect(rows[0].spend_raw).toBe(2973.19);
        expect(rows[0].sales_raw).toBeNull();
        expect(rows[0].orders_raw).toBeNull();
        expect(rows[0].roas_raw).toBeNull();
    });

    test("builds a 24-hour Snapchat input with Arabic hour labels", () => {
        expect(formatAdsHourLabel("00:00")).toBe("12 ص");
        expect(formatAdsHourLabel("13:00")).toBe("1 م");
        const input = buildAdsHourlyChartInput({
            date_from: "2026-08-04",
            date_to: "2026-08-04",
            source: { hourly_available: true },
            hourly: [
                { date: "2026-08-04", hour: "02:00", hour_index: 2, spend_sar: 20, orders: 1, sales_sar: 50, roas: 2.5 },
                { date: "2026-08-04", hour: "00:00", hour_index: 0, spend_sar: 10, orders: 0, sales_sar: 0, roas: null },
            ],
        });
        expect(input.map((row) => row.date)).toEqual(["12 ص", "2 ص"]);
        expect(input[1]).toMatchObject({ spend_sar: 20, orders: 1, sales_sar: 50, roas: 2.5 });
    });

    test("toggles exactly the selected metric", () => {
        const current = new Set(["orders", "sales", "roas", "spend"]);
        const hidden = toggleMetricVisibility(current, "roas");
        expect(hidden.has("roas")).toBe(false);
        expect(hidden.has("spend")).toBe(true);
        expect(current.has("roas")).toBe(true);
        expect(toggleMetricVisibility(hidden, "roas").has("roas")).toBe(true);
    });

    test("renders fallback one-day bars until hourly rows arrive", async () => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        const container = document.createElement("div");
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(
                <AdsPerformanceExplorer
                    totals={{ salla_matched_orders: 4, salla_sales_sar: 100, salla_roas: 2, snapchat_spend_sar: 50 }}
                    daily={[{ date: "2026-08-04", salla_matched_orders: 4, salla_sales_sar: 100, salla_roas: 2, snapchat_spend_sar: 50 }]}
                    platformLabel="سناب شات"
                />,
            );
        });

        expect(container.querySelector('[data-testid="ads-performance-single-day-chart"]')).not.toBeNull();
        expect(container.textContent).toContain("بيانات الساعات قيد أول مزامنة");
        expect(container.textContent).toContain("طلبات سلة المطابقة");
        expect(container.textContent).toContain("صرف Snapchat");
        expect(container.textContent).not.toContain("عمليات الشراء/البيع");

        await act(async () => root.unmount());
        container.remove();
    });

    test("does not reuse an hourly snapshot from a previous request", async () => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        mockGetCampaignReportSnapshot.mockReturnValue({
            date_from: "2026-08-04",
            date_to: "2026-08-04",
            source: { hourly_available: true },
            hourly: Array.from({ length: 24 }, (_, hour) => ({
                date: "2026-08-04",
                hour: `${String(hour).padStart(2, "0")}:00`,
                hour_index: hour,
                spend_sar: hour < 9 ? 10 + hour : 0,
                sales_sar: hour === 8 ? 100 : 0,
                orders: hour === 8 ? 2 : 0,
                roas: hour === 8 ? 100 / 18 : null,
                observed: hour <= 8,
                is_future: hour > 8,
            })),
        });
        const container = document.createElement("div");
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(
                <AdsPerformanceExplorer
                    totals={{ salla_matched_orders: 2, salla_sales_sar: 100, salla_roas: 5.56, snapchat_spend_sar: 126 }}
                    daily={[{ date: "2026-08-04", salla_matched_orders: 2, salla_sales_sar: 100, salla_roas: 5.56, snapchat_spend_sar: 126 }]}
                    platformLabel="سناب شات"
                />,
            );
        });

        expect(container.querySelector('[data-testid="ads-performance-hourly-chart"]')).toBeNull();
        expect(container.querySelector('[data-testid="ads-performance-single-day-chart"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="ads-performance-explorer"]').dataset.chartGranularity).toBe("day");
        expect(container.textContent).toContain("اتجاه الأداء اليومي");

        await act(async () => root.unmount());
        container.remove();
    });
});
