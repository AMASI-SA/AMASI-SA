import React, { act } from "react";
import { createRoot } from "react-dom/client";
import DashboardAdsSpendCard from "./DashboardAdsSpendCard";
import { getDashboardAdsSpend } from "../services/dashboardAdsSpend";
import { refreshSnapchatSpend } from "../services/snapchatManualSpendRefresh";

jest.mock("../services/dashboardAdsSpend", () => ({
    ...jest.requireActual("../services/dashboardAdsSpend"), getDashboardAdsSpend: jest.fn(),
}));
jest.mock("../services/snapchatManualSpendRefresh", () => ({
    ...jest.requireActual("../services/snapchatManualSpendRefresh"), refreshSnapchatSpend: jest.fn(),
}));
jest.mock("recharts", () => ({
    ResponsiveContainer: ({ children }) => <div>{children}</div>,
    LineChart: ({ children }) => <div>{children}</div>, Line: () => null, Legend: () => null,
    CartesianGrid: () => null, Tooltip: () => null, XAxis: () => null, YAxis: () => null,
}));

let host, root, amount, observed;
const period = { fromDate: "2026-11-06", toDate: "2026-11-06" };
const refreshedAt = "2026-11-11T17:00:00Z";
const completedJob = () => ({ run_id: "synthetic-run", scope: "snapchat_spend_only", status: "complete",
    date_from: period.fromDate, date_to: period.toDate, requested_account_ids: ["synthetic-account"],
    refreshed_count: 1, failed_count: 0, account_results: [{ account_id: "synthetic-account", status: "complete",
        snapshot_job_id: "synthetic-run", observed_at: refreshedAt }] });
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers(); jest.clearAllMocks(); amount = 42;
    observed = "2026-11-11T16:00:00Z";
    getDashboardAdsSpend.mockImplementation(async ({ dateFrom, dateTo }) => ({
        date_from: dateFrom, date_to: dateTo, total_sar: amount,
        provider_totals_sar: { snapchat: amount }, providers: { snapchat: { connected: true, accounts: [
            { account_id: "synthetic-account", currency: "SAR", total_native: amount, total_sar: amount,
                quality: { amount_available: true, observed_at: observed } },
        ] } },
        spend_quality: { amount_available: true }, daily_spend: [], hourly_spend: [],
    }));
    refreshSnapchatSpend.mockImplementation(async () => { amount = 73; observed = refreshedAt; return completedJob(); });
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.clearAllTimers(); jest.useRealTimers(); });

test("chart has a labelled refresh pill and opens with saved facts; only a click requests fresh Snapchat spend", async () => {
    await act(async () => root.render(<DashboardAdsSpendCard {...period} />));
    const button = host.querySelector('[data-testid="dashboard-ads-spend-refresh"]');
    expect(button.textContent).toContain("تحديث");
    expect(button.getAttribute("aria-label")).toBe("تحديث صرف سناب فقط");
    expect(button.className).toContain("rounded-full");
    expect(getDashboardAdsSpend).toHaveBeenLastCalledWith({ dateFrom: period.fromDate, dateTo: period.toDate, refresh: false });
    expect(refreshSnapchatSpend).not.toHaveBeenCalled();
    await act(async () => button.click());
    expect(refreshSnapchatSpend).toHaveBeenCalledTimes(1);
    expect(refreshSnapchatSpend.mock.calls[0][0]).toMatchObject({ dateFrom: period.fromDate, dateTo: period.toDate });
    expect(getDashboardAdsSpend).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain("73.00");
    expect(host.textContent).toContain("اكتمل جلب صرف سناب.");
    expect(getDashboardAdsSpend.mock.calls.every(([input]) => input.refresh === false)).toBe(true);
});

test("a completed job with a stale dashboard read does not announce success", async () => {
    refreshSnapchatSpend.mockResolvedValue(completedJob());
    await act(async () => root.render(<DashboardAdsSpendCard {...period} />));
    await act(async () => host.querySelector('[data-testid="dashboard-ads-spend-refresh"]').click());
    expect(host.textContent).toContain("42.00");
    expect(host.textContent).toContain("لم يتأكد ظهور القراءة الجديدة");
    expect(host.textContent).not.toContain("اكتمل جلب صرف سناب.");
});

test("a proven new observation with unchanged spend is a successful refresh", async () => {
    refreshSnapchatSpend.mockImplementation(async () => { observed = refreshedAt; return completedJob(); });
    await act(async () => root.render(<DashboardAdsSpendCard {...period} />));
    await act(async () => host.querySelector('[data-testid="dashboard-ads-spend-refresh"]').click());
    expect(host.textContent).toContain("42.00");
    expect(host.textContent).toContain("اكتمل جلب صرف سناب.");
});

test("partial refresh names the incomplete account count without claiming whole-job success", async () => {
    refreshSnapchatSpend.mockImplementation(async () => {
        amount = 73; observed = refreshedAt;
        const job = completedJob();
        return { ...job, status: "partial", failed_count: 1,
            requested_account_ids: [...job.requested_account_ids, "failed-account"],
            account_results: [...job.account_results, { account_id: "failed-account", status: "failed" }] };
    });
    await act(async () => root.render(<DashboardAdsSpendCard {...period} />));
    await act(async () => host.querySelector('[data-testid="dashboard-ads-spend-refresh"]').click());
    expect(host.textContent).toContain("تعذر تحديث 1 حساب");
    expect(host.textContent).toContain("73.00");
    expect(host.textContent).not.toContain("اكتمل جلب صرف سناب.");
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
