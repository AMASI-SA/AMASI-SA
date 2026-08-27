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


function providers({ hourly = true } = {}) {
    return {
        snapchat: {
            connected: true,
            daily_available: true,
            hourly_available: hourly,
        },
        meta: {
            connected: true,
            daily_available: true,
            hourly_available: hourly,
        },
        tiktok: {
            connected: true,
            daily_available: true,
            hourly_available: hourly,
        },
        google: {
            connected: true,
            daily_available: true,
            hourly_available: hourly,
        },
    };
}


test("renders four original hourly provider lines for one selected day", () => {
    const html = renderToStaticMarkup(
        <DashboardAdsSpendCardContent
            fromDate="2026-07-15"
            toDate="2026-07-15"
            data={{
                total_sar: 8873.97,
                provider_totals_sar: {
                    snapchat: 8188.42,
                    meta: 668.05,
                    tiktok: 10,
                    google: 7.5,
                },
                providers: providers(),
                daily_spend: [
                    {
                        date: "2026-07-15",
                        snapchat: 8188.42,
                        meta: 668.05,
                        tiktok: 10,
                        google: 7.5,
                        booked_ad_expense_sar: 999999,
                    },
                ],
                hourly_spend: Array.from({ length: 24 }, (_, hourIndex) => ({
                    date: "2026-07-15",
                    hour_index: hourIndex,
                    hour: `${String(hourIndex).padStart(2, "0")}:00`,
                    snapchat: hourIndex === 10 ? 350.25 : 0,
                    meta: hourIndex === 10 ? 25 : 0,
                    tiktok: hourIndex === 10 ? 5 : 0,
                    google: hourIndex === 10 ? 7.5 : 0,
                })),
            }}
            onRefresh={() => {}}
        />,
    );

    expect(html).toContain("صرفيات منصات الإعلانات");
    expect(html).toContain("سناب شات + ميتا + تيك توك + Google Ads");
    expect(html).toContain("صرفيات يوم 2026-07-15");
    expect(html).toContain("عرض ساعي");
    expect(html).toContain("8,873.97 ر.س");
    expect(html).toContain("axis:hour");
    expect(html).toContain("line:سناب شات");
    expect(html).toContain("line:ميتا");
    expect(html).toContain("line:تيك توك");
    expect(html).toContain("line:Google Ads");
    expect(html).toContain("dashboard-ads-provider-google");
    expect(html).toContain("بيانات ساعية أصلية");
    expect(html).not.toContain("المصروف المحاسبي");
    expect(html).not.toContain("999,999.00 ر.س");
    expect(html).toContain("dashboard-ads-spend-hourly-chart");
});


test("renders four platform lines by day for a multi-day range", () => {
    const html = renderToStaticMarkup(
        <DashboardAdsSpendCardContent
            fromDate="2026-07-14"
            toDate="2026-07-15"
            data={{
                total_sar: 220,
                provider_totals_sar: {
                    snapchat: 50,
                    meta: 70,
                    tiktok: 90,
                    google: 10,
                },
                providers: providers({ hourly: false }),
                daily_spend: [
                    { date: "2026-07-14", snapchat: 10, meta: 20, tiktok: 30, google: 4 },
                    { date: "2026-07-15", snapchat: 40, meta: 50, tiktok: 60, google: 6 },
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
    expect(html).toContain("line:Google Ads");
    expect(html).not.toContain("المصروف المحاسبي");
    expect(html).toContain("220.00 ر.س");
    expect(html).toContain("بيانات يومية أصلية");
});


test("shows a truthful state for a connected platform without hourly facts", () => {
    const html = renderToStaticMarkup(
        <DashboardAdsSpendCardContent
            fromDate="2026-07-15"
            toDate="2026-07-15"
            data={{
                total_sar: 100,
                provider_totals_sar: {
                    snapchat: 100,
                    meta: null,
                    tiktok: null,
                    google: null,
                },
                providers: {
                    ...providers(),
                    google: {
                        connected: true,
                        daily_available: false,
                        hourly_available: false,
                    },
                },
                hourly_spend: Array.from({ length: 24 }, (_, hourIndex) => ({
                    date: "2026-07-15",
                    hour_index: hourIndex,
                    hour: `${String(hourIndex).padStart(2, "0")}:00`,
                    snapchat: 1,
                    meta: null,
                    tiktok: null,
                    google: null,
                })),
            }}
            onRefresh={() => {}}
        />,
    );

    expect(html).toContain("بانتظار البيانات");
    expect(html).toContain("لا بيانات");
    expect(html).not.toContain("line:Google Ads");
});


test("labels the temporary TikTok Make daily-total marker truthfully", () => {
    const html = renderToStaticMarkup(
        <DashboardAdsSpendCardContent
            fromDate="2026-08-26"
            toDate="2026-08-26"
            data={{
                total_sar: 308.17,
                provider_totals_sar: {
                    snapchat: null,
                    meta: null,
                    tiktok: 308.17,
                    google: null,
                },
                providers: {
                    ...providers({ hourly: false }),
                    tiktok: {
                        connected: true,
                        daily_available: true,
                        hourly_available: true,
                        hourly_source: "make_daily_total_marker",
                    },
                },
                hourly_spend: Array.from({ length: 24 }, (_, hourIndex) => ({
                    date: "2026-08-26",
                    hour_index: hourIndex,
                    hour: `${String(hourIndex).padStart(2, "0")}:00`,
                    snapchat: null,
                    meta: null,
                    tiktok: hourIndex === 16 ? 308.17 : null,
                    google: null,
                })),
            }}
            onRefresh={() => {}}
        />,
    );

    expect(html).toContain("line:تيك توك");
    expect(html).toContain("إجمالي Make عند آخر تحديث");
    expect(html).toContain("علامة تيك توك تمثل إجمالي اليوم");
    expect(html).toContain("لا يتم توزيع الإجمالي بالتخمين");
    expect(html).not.toContain("بيانات ساعية أصلية");
});


test("uses Riyadh today and yesterday labels", () => {
    const today = todaySA();
    const yesterday = addDaysISO(today, -1);

    expect(selectedPeriodLabel(today, today)).toContain("صرفيات اليوم");
    expect(selectedPeriodLabel(yesterday, yesterday)).toContain("صرفيات أمس");
    expect(selectedPeriodLabel("2026-08-01", "2026-08-04"))
        .toBe("من 2026-08-01 إلى 2026-08-04");
});

test("shows waiting instead of 0.00 when the new Riyadh day has no provider payload", () => {
    const waitingProviders = Object.fromEntries(
        ["snapchat", "meta", "tiktok", "google"].map((provider) => [
            provider,
            {
                connected: true,
                daily_available: false,
                hourly_available: false,
                data_state: "waiting_incomplete",
            },
        ]),
    );
    const html = renderToStaticMarkup(
        <DashboardAdsSpendCardContent
            fromDate="2026-08-28"
            toDate="2026-08-28"
            data={{
                total_sar: null,
                provider_totals_sar: {
                    snapchat: null,
                    meta: null,
                    tiktok: null,
                    google: null,
                },
                providers: waitingProviders,
                spend_quality: { status: "incomplete", amount_available: false },
                hourly_spend: Array.from({ length: 24 }, (_, hourIndex) => ({
                    date: "2026-08-28",
                    hour_index: hourIndex,
                    hour: `${String(hourIndex).padStart(2, "0")}:00`,
                    snapchat: null,
                    meta: null,
                    tiktok: null,
                    google: null,
                })),
            }}
            onRefresh={() => {}}
        />,
    );

    expect(html).toContain("إجمالي المنصات: بانتظار البيانات");
    expect(html).toContain("بانتظار وصول أول مزامنة مكتملة لليوم الحالي");
    expect(html).not.toContain("إجمالي المنصات: 0.00 ر.س");
});
