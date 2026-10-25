import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { addDaysISO, todaySA } from "../lib/dates";
import {
    DashboardAdsSpendCardContent,
    selectedPeriodLabel,
    default as DashboardAdsSpendCard,
} from "./DashboardAdsSpendCard";
import { getDashboardAdsSpend, normalizeDashboardAdsSpend } from "../services/dashboardAdsSpend";

jest.mock("../services/dashboardAdsSpend", () => ({
    ...jest.requireActual("../services/dashboardAdsSpend"), getDashboardAdsSpend: jest.fn(),
}));

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

test("mounted card reloads saved Google spend without a page refresh and stops polling on unmount", async () => {
    jest.useFakeTimers(); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const previousVisibility = Object.getOwnPropertyDescriptor(document, "visibilityState");
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    let google = 7.5;
    getDashboardAdsSpend.mockImplementation(async () => ({ provider_totals_sar: { snapchat: 0, meta: 0, tiktok: 0, google }, providers: providers(), daily_spend: [], hourly_spend: [], total_spend_sar: google, currency: "SAR" }));
    const host = document.createElement("div"); document.body.appendChild(host); const root = createRoot(host);
    try {
        await act(async () => root.render(<DashboardAdsSpendCard fromDate="2026-10-13" toDate="2026-10-13" />));
        expect(host.textContent).toContain("7.5");
        google = 14.25;
        await act(async () => jest.advanceTimersByTime(5 * 60_000));
        expect(host.textContent).toContain("14.25");
        expect(getDashboardAdsSpend.mock.calls.some(([input]) => input.dateFrom === "2026-10-13" && input.dateTo === "2026-10-13" && input.refresh === false)).toBe(true);
        await act(async () => root.unmount());
        const completed = getDashboardAdsSpend.mock.calls.length;
        await act(async () => jest.advanceTimersByTime(10 * 60_000));
        expect(getDashboardAdsSpend).toHaveBeenCalledTimes(completed);
    } finally {
        host.remove(); jest.useRealTimers();
        if (previousVisibility) Object.defineProperty(document, "visibilityState", previousVisibility); else delete document.visibilityState;
    }
});


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


function midnightSpend({ amount = null, available = false, provisional = false, connected = true, hour = null,
    knownTotal = null, knownAllTotal = null } = {}) {
    return normalizeDashboardAdsSpend({
        date_from: "2026-10-26", date_to: "2026-10-26", chart_granularity: "hour",
        provider_totals_sar: { snapchat: amount, meta: 20, tiktok: 5, google: 7.5 },
        total_sar: amount === null ? null : amount + 32.5,
        known_total_sar: knownAllTotal,
        providers: {
            ...providers(),
            snapchat: { ...providers().snapchat, connected,
                amount_available: available, amount_complete: available && !provisional,
                provisional, data_quality: available ? provisional ? "provisional" : "complete" : "incomplete",
                known_total_sar: knownTotal, known_account_count: knownTotal === null ? 0 : 1,
                incomplete_account_count: available ? 0 : 1,
                data_state: available ? provisional ? "provisional_data" : "confirmed_zero" : "unknown_incomplete",
                reason_codes: available ? ["open_riyadh_day_reconciliation_pending"]
                    : ["unified_projection_financial_reconciliation_or_fx_incomplete"] },
        },
        spend_quality: { status: available ? provisional ? "provisional" : "complete" : "incomplete",
            amount_available: available, amount_complete: available && !provisional, provisional },
        daily_spend: [{ date: "2026-10-26", snapchat: 81.32, meta: 20, tiktok: 5, google: 7.5 }],
        hourly_spend: [{ date: "2026-10-26", hour_index: 0, hour: "00:00", snapchat: hour, meta: 20, tiktok: 5, google: 7.5 }],
    });
}


function midnightMarkup(data) {
    const host = document.createElement("div");
    host.innerHTML = renderToStaticMarkup(<DashboardAdsSpendCardContent
        fromDate="2026-10-26" toDate="2026-10-26" data={data} onRefresh={() => {}} />);
    return host;
}


test.each([null, 0, 81.32])("connected incomplete Snapchat never presents %s as a full amount or no data", (amount) => {
    const host = midnightMarkup(midnightSpend({ amount }));
    const snapchat = host.querySelector('[data-testid="dashboard-ads-provider-snapchat"]');
    expect(snapchat.textContent).toContain("بيانات غير مكتملة");
    expect(snapchat.textContent).toContain("بانتظار اكتمال بيانات سناب");
    expect(snapchat.textContent).not.toContain("لا بيانات");
    expect(snapchat.textContent).not.toContain("81.32");
    expect(snapchat.textContent).not.toContain("0.00 ر.س");
    expect(host.querySelector('[data-testid="dashboard-ads-spend-total"]').textContent)
        .toBe("إجمالي المنصات: بيانات غير مكتملة");
    expect(host.querySelector('[data-testid="dashboard-ads-provider-meta"]').textContent).toContain("20.00 ر.س");
    expect(host.querySelector('[data-testid="dashboard-ads-provider-tiktok"]').textContent).toContain("5.00 ر.س");
    expect(host.querySelector('[data-testid="dashboard-ads-provider-google"]').textContent).toContain("7.50 ر.س");
    expect(host.textContent).not.toContain("line:سناب شات");
    expect(host.textContent).toContain("line:Google Ads");
});


test.each([81.32, 0])("labels available Snapchat amount %s as provisional without inventing hour zero", (amount) => {
    const data = midnightSpend({ amount, available: true, provisional: true });
    const host = midnightMarkup(data);
    const snapchat = host.querySelector('[data-testid="dashboard-ads-provider-snapchat"]');
    expect(snapchat.textContent).toContain(`${amount.toFixed(2)} ر.س · أولي`);
    expect(snapchat.textContent).toContain("تفصيل سناب الساعي غير مكتمل");
    expect(snapchat.textContent).not.toContain("لا بيانات");
    expect(host.querySelector('[data-testid="dashboard-ads-spend-total"]').textContent)
        .toContain(`${(amount + 32.5).toFixed(2)} ر.س · أولي`);
    expect(data.hourly_spend[0].snapchat).toBeNull();
    expect(host.textContent).not.toContain("line:سناب شات");
});


test("preserves confirmed Snapchat zero and disconnected status", () => {
    const zero = midnightMarkup(midnightSpend({ amount: 0, available: true, hour: 0 }));
    const zeroCard = zero.querySelector('[data-testid="dashboard-ads-provider-snapchat"]');
    expect(zeroCard.textContent).toContain("0.00 ر.س");
    expect(zeroCard.textContent).not.toContain("أولي");
    expect(zeroCard.textContent).not.toContain("غير مكتمل");
    expect(zero.textContent).toContain("line:سناب شات");
    const disconnected = midnightMarkup(midnightSpend({ connected: false }));
    const disconnectedCard = disconnected.querySelector('[data-testid="dashboard-ads-provider-snapchat"]');
    expect(disconnectedCard.textContent).toContain("غير مربوط");
    expect(disconnectedCard.textContent).not.toContain("بيانات غير مكتملة");
});


test.each([[81.32, 99.71, 18.39], [0, 0, 0]])(
    "shows only explicit known Snapchat %s and platform %s amounts as incomplete", (known, knownAll, meta) => {
        const data = midnightSpend({ knownTotal: known, knownAllTotal: knownAll });
        // Match the known all-platform amount supplied by the backend; no
        // missing Snapchat account or hour is counted as an observed zero.
        Object.assign(data.provider_totals_sar, { meta, tiktok: 0, google: 0 });
        Object.assign(data.daily_spend[0], { meta, tiktok: 0, google: 0 });
        Object.assign(data.hourly_spend[0], { meta, tiktok: 0, google: 0 });
        const host = midnightMarkup(data);
        const snapchat = host.querySelector('[data-testid="dashboard-ads-provider-snapchat"]');
        expect(snapchat.textContent).toContain(`المتاح ${known.toFixed(2)} ر.س · غير مكتمل`);
        expect(snapchat.textContent).toContain("بيانات الحسابات غير مكتملة");
        expect(snapchat.textContent).not.toContain("لا بيانات");
        expect(snapchat.textContent).not.toContain("أولي");
        expect(host.querySelector('[data-testid="dashboard-ads-spend-total"]').textContent)
            .toBe(`إجمالي المنصات: غير مكتمل، المتاح ${knownAll.toFixed(2)} ر.س`);
        expect(data.total_sar).toBeNull();
        expect(data.provider_totals_sar.snapchat).toBeNull();
        expect(data.hourly_spend[0].snapchat).toBeNull();
        expect(host.textContent).not.toContain("line:سناب شات");
    },
);


test("known other-platform spend never supplies a missing Snapchat known amount", () => {
    const host = midnightMarkup(midnightSpend({ knownAllTotal: 32.5 }));
    const snapchat = host.querySelector('[data-testid="dashboard-ads-provider-snapchat"]');
    expect(snapchat.textContent).toContain("بيانات غير مكتملة");
    expect(snapchat.textContent).not.toContain("المتاح");
    expect(snapchat.textContent).not.toContain("0.00 ر.س");
    expect(host.querySelector('[data-testid="dashboard-ads-spend-total"]').textContent)
        .toBe("إجمالي المنصات: غير مكتمل، المتاح 32.50 ر.س");
});
