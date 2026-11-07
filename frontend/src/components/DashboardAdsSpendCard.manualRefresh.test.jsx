import React, { act } from "react";
import { createRoot } from "react-dom/client";
import DashboardAdsSpendCard from "./DashboardAdsSpendCard";
import { getDashboardAdsSpend } from "../services/dashboardAdsSpend";
import { refreshSnapchatSpend } from "../services/snapchatManualSpendRefresh";

jest.mock("../services/dashboardAdsSpend", () => ({
    ...jest.requireActual("../services/dashboardAdsSpend"), getDashboardAdsSpend: jest.fn(),
}));
jest.mock("../services/snapchatManualSpendRefresh", () => ({ refreshSnapchatSpend: jest.fn() }));
jest.mock("recharts", () => ({
    ResponsiveContainer: ({ children }) => <div>{children}</div>,
    LineChart: ({ children }) => <div>{children}</div>, Line: () => null, Legend: () => null,
    CartesianGrid: () => null, Tooltip: () => null, XAxis: () => null, YAxis: () => null,
}));

let host, root, amount;
const period = { fromDate: "2026-11-06", toDate: "2026-11-06" };
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers(); jest.clearAllMocks(); amount = 42;
    getDashboardAdsSpend.mockImplementation(async ({ dateFrom, dateTo }) => ({
        date_from: dateFrom, date_to: dateTo, total_sar: amount,
        provider_totals_sar: { snapchat: amount }, providers: { snapchat: { connected: true } },
        spend_quality: { amount_available: true }, daily_spend: [], hourly_spend: [],
    }));
    refreshSnapchatSpend.mockResolvedValue({ run_id: "synthetic-run", status: "complete" });
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.clearAllTimers(); jest.useRealTimers(); });

test("chart has a labelled refresh pill and opens with saved facts; only a click requests fresh Snapchat spend", async () => {
    await act(async () => root.render(<DashboardAdsSpendCard {...period} />));
    const button = host.querySelector('[data-testid="dashboard-ads-spend-refresh"]');
    expect(button.textContent).toContain("تحديث");
    expect(button.className).toContain("rounded-full");
    expect(getDashboardAdsSpend).toHaveBeenLastCalledWith({ dateFrom: period.fromDate, dateTo: period.toDate, refresh: false });
    expect(refreshSnapchatSpend).not.toHaveBeenCalled();
    amount = 73;
    await act(async () => button.click());
    expect(refreshSnapchatSpend).toHaveBeenCalledTimes(1);
    expect(refreshSnapchatSpend.mock.calls[0][0]).toMatchObject({ dateFrom: period.fromDate, dateTo: period.toDate });
    expect(getDashboardAdsSpend).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("73.00");
    expect(getDashboardAdsSpend.mock.calls.every(([input]) => input.refresh === false)).toBe(true);
});

test("a failed manual fetch keeps the last saved amount visible", async () => {
    await act(async () => root.render(<DashboardAdsSpendCard {...period} />));
    refreshSnapchatSpend.mockRejectedValue(new Error("تعذر جلب سناب مؤقتًا"));
    await act(async () => host.querySelector('[data-testid="dashboard-ads-spend-refresh"]').click());
    expect(host.textContent).toContain("42.00");
    expect(host.textContent).toContain("تعذر جلب سناب مؤقتًا");
    expect(getDashboardAdsSpend).toHaveBeenCalledTimes(1);
});

test("changing period cancels manual status observation and rejects a late prior-period completion", async () => {
    let resolve;
    refreshSnapchatSpend.mockImplementation(() => new Promise(done => { resolve = done; }));
    await act(async () => root.render(<DashboardAdsSpendCard {...period} />));
    await act(async () => host.querySelector('[data-testid="dashboard-ads-spend-refresh"]').click());
    const signal = refreshSnapchatSpend.mock.calls[0][0].signal;
    await act(async () => root.render(<DashboardAdsSpendCard fromDate="2026-11-05" toDate="2026-11-05" />));
    expect(signal.aborted).toBe(true);
    await act(async () => resolve({ status: "complete" }));
    expect(getDashboardAdsSpend).toHaveBeenCalledTimes(2);
    expect(host.querySelector('[data-testid="dashboard-ads-spend-card"]').dataset.fromDate).toBe("2026-11-05");
});
