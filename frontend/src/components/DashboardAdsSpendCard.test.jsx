import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { addDaysISO, todaySA } from "../lib/dates";
import {
    DashboardAdsSpendCardContent,
    selectedPeriodLabel,
} from "./DashboardAdsSpendCard";

jest.mock("recharts", () => ({
    ResponsiveContainer: ({ children }) => <div>{children}</div>,
    LineChart: ({ children }) => <div>{children}</div>,
    Line: ({ name }) => <div>{`line:${name}`}</div>,
    CartesianGrid: () => null,
    Legend: () => null,
    Tooltip: () => null,
    XAxis: ({ dataKey }) => <div>{`axis:${dataKey}`}</div>,
    YAxis: () => null,
}));


test("renders a 24-hour provider-only chart for one dashboard day", () => {
    const html = renderToStaticMarkup(
        <DashboardAdsSpendCardContent
            fromDate="2026-07-15"
            toDate="2026-07-15"
            data={{
                daily_spend: [
                    {
                        date: "2026-07-15",
                        snapchat: 8188.42,
                        meta: 668.05,
                        tiktok: 10,
                        booked_ad_expense_sar: 999999,
                    },
                ],
                hourly_spend: Array.from({ length: 24 }, (_, hourIndex) => ({
                    date: "2026-07-15",
                    hour_index: hourIndex,
                    hour: `${String(hourIndex).padStart(2, "0")}:00`,
                    snapchat: hourIndex === 10 ? 350.25 : 0,
                    meta: null,
                    tiktok: null,
                })),
            }}
            onRefresh={() => {}}
        />,
    );

    expect(html).toContain("صرفيات منصات الإعلانات");
    expect(html).toContain("مرتبطة بتاريخ الملخص التنفيذي للأرباح");
    expect(html).toContain("صرفيات يوم 2026-07-15");
    expect(html).toContain("عرض ساعي");
    expect(html).toContain("8,866.47 ر.س");
    expect(html).toContain("axis:hour");
    expect(html).toContain("line:سناب شات");
    expect(html).not.toContain("line:المصروف المحاسبي");
    expect(html).not.toContain("999,999.00 ر.س");
    expect(html).toContain("dashboard-ads-spend-hourly-chart");
});


test("renders all platform lines by day for a multi-day range", () => {
    const html = renderToStaticMarkup(
        <DashboardAdsSpendCardContent
            fromDate="2026-07-14"
            toDate="2026-07-15"
            data={{
                daily_spend: [
                    { date: "2026-07-14", snapchat: 10, meta: 20, tiktok: 30 },
                    { date: "2026-07-15", snapchat: 40, meta: 50, tiktok: 60 },
                ],
            }}
            onRefresh={() => {}}
        />,
    );

    expect(html).toContain("عرض يومي");
    expect(html).toContain("axis:date");
    expect(html).toContain("line:سناب شات");
    expect(html).toContain("line:ميتا");
    expect(html).toContain("line:تيك توك");
    expect(html).not.toContain("المصروف المحاسبي");
    expect(html).toContain("210.00 ر.س");
});


test("uses Riyadh today and yesterday labels", () => {
    const today = todaySA();
    const yesterday = addDaysISO(today, -1);

    expect(selectedPeriodLabel(today, today)).toContain("صرفيات اليوم");
    expect(selectedPeriodLabel(yesterday, yesterday)).toContain("صرفيات أمس");
    expect(selectedPeriodLabel("2026-08-01", "2026-08-04"))
        .toBe("من 2026-08-01 إلى 2026-08-04");
});
