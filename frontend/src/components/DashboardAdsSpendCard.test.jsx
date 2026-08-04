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
    Line: ({ name }) => <div>{name}</div>,
    CartesianGrid: () => null,
    Legend: () => null,
    Tooltip: () => null,
    XAxis: () => null,
    YAxis: () => null,
}));


test("renders the yellow ads report for the same dashboard date range", () => {
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
                        booked_ad_expense_sar: 8866.47,
                    },
                ],
            }}
            onRefresh={() => {}}
        />,
    );

    expect(html).toContain("صرفيات منصات الإعلانات");
    expect(html).toContain("مرتبطة بتاريخ الملخص التنفيذي للأرباح");
    expect(html).toContain("صرفيات يوم 2026-07-15");
    expect(html).toContain("8,866.47 ر.س");
    expect(html).toContain("سناب شات");
    expect(html).toContain("ميتا");
    expect(html).toContain("تيك توك");
    expect(html).toContain("المصروف المحاسبي");
});


test("uses Riyadh today and yesterday labels", () => {
    const today = todaySA();
    const yesterday = addDaysISO(today, -1);

    expect(selectedPeriodLabel(today, today)).toContain("صرفيات اليوم");
    expect(selectedPeriodLabel(yesterday, yesterday)).toContain("صرفيات أمس");
    expect(selectedPeriodLabel("2026-08-01", "2026-08-04"))
        .toBe("من 2026-08-01 إلى 2026-08-04");
});
