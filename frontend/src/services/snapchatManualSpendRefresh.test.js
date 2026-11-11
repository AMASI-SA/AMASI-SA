import api from "../lib/api";
import { clearDashboardPlatformSpendCache } from "../lib/dashboardPlatformSpendClient";
import { refreshSnapchatSpend, snapchatSpendReadbackMatches } from "./snapchatManualSpendRefresh";

jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("../lib/dashboardPlatformSpendClient", () => ({ clearDashboardPlatformSpendCache: jest.fn() }));
const period = { dateFrom: "2026-11-04", dateTo: "2026-11-06" };
const observed = "2026-11-11T17:00:00Z";
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
function job(status = "running", overrides = {}) {
    const completed = status === "complete" ? ["usd", "sar"] : status === "partial" ? ["usd"] : [];
    return { run_id: "synthetic-run", scope: "snapchat_spend_only", source_mode: "manual_account_hour",
        date_from: period.dateFrom, date_to: period.dateTo, requested_account_ids: ["usd", "sar"], status,
        refreshed_count: completed.length, failed_count: 2 - completed.length,
        account_results: ["usd", "sar"].map((account_id) => ({ account_id,
            status: completed.includes(account_id) ? "complete" : "failed",
            observed_at: completed.includes(account_id) ? observed : null,
            snapshot_job_id: completed.includes(account_id) ? "synthetic-run" : null })), ...overrides };
}
function readback(timestamp = observed) {
    return { date_from: period.dateFrom, date_to: period.dateTo, providers: { snapchat: {
        accounts: ["usd", "sar"].map((account_id) => ({ account_id, total_sar: 42,
            quality: { amount_available: true, observed_at: timestamp } })),
    } } };
}
beforeEach(() => {
    jest.useFakeTimers(); jest.clearAllMocks();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    api.post.mockResolvedValue({ data: job() });
});
afterEach(() => { jest.clearAllTimers(); jest.useRealTimers(); });

test("one spend-only POST waits for both account observations before invalidating saved data", async () => {
    api.get.mockResolvedValueOnce({ data: job() }).mockResolvedValueOnce({ data: job("complete") });
    const request = refreshSnapchatSpend(period);
    await flush();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post.mock.calls[0].slice(0, 2)).toEqual([
        "/integrations-v2/ads-auto-sync/snapchat/refresh", { date_from: period.dateFrom, date_to: period.dateTo },
    ]);
    expect(clearDashboardPlatformSpendCache).not.toHaveBeenCalled();
    jest.advanceTimersByTime(2000); await flush();
    expect(clearDashboardPlatformSpendCache).not.toHaveBeenCalled();
    jest.advanceTimersByTime(2000); await flush();
    expect((await request).status).toBe("complete");
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(clearDashboardPlatformSpendCache).toHaveBeenCalledTimes(1);
});

test("partial results stay partial, while failed jobs never replace the saved spend cache", async () => {
    api.get.mockResolvedValueOnce({ data: job("partial") });
    const partial = refreshSnapchatSpend(period); await flush();
    jest.advanceTimersByTime(2000); await flush();
    expect((await partial).status).toBe("partial");
    api.get.mockResolvedValueOnce({ data: job("failed") });
    const failed = refreshSnapchatSpend(period);
    const assertion = expect(failed).rejects.toThrow();
    await flush(); jest.advanceTimersByTime(2000); await flush();
    await assertion;
    expect(clearDashboardPlatformSpendCache).toHaveBeenCalledTimes(1);
});

test.each([
    ["legacy full refresh", { scope: "legacy_full_sync" }],
    ["different dates", { date_to: "2026-11-07" }],
    ["different run", { run_id: "other-run" }],
    ["missing account", { requested_account_ids: ["usd"] }],
    ["unproven completion", { account_results: [] }],
    ["mismatched snapshot", { account_results: job("complete").account_results.map((row) => ({ ...row, snapshot_job_id: "old" })) }],
])("rejects %s instead of announcing success", async (_label, invalid) => {
    api.get.mockResolvedValueOnce({ data: job("complete", invalid) });
    const assertion = expect(refreshSnapchatSpend(period)).rejects.toThrow();
    await flush(); jest.advanceTimersByTime(2000); await flush(); await assertion;
    expect(clearDashboardPlatformSpendCache).not.toHaveBeenCalled();
});

test("rejects an accepted legacy job before polling", async () => {
    api.post.mockResolvedValue({ data: { run_id: "old", status: "running" } });
    await expect(refreshSnapchatSpend(period)).rejects.toThrow();
    expect(api.get).not.toHaveBeenCalled();
});

test("leaving the card cancels polling without starting another provider refresh", async () => {
    const controller = new AbortController();
    const request = refreshSnapchatSpend({ ...period, signal: controller.signal });
    const assertion = expect(request).rejects.toMatchObject({ name: "AbortError" });
    await flush(); controller.abort(); await assertion;
    jest.advanceTimersByTime(60_000); await flush();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.get).not.toHaveBeenCalled();
    expect(clearDashboardPlatformSpendCache).not.toHaveBeenCalled();
});

test("a malformed status reply fails promptly without replacing the saved spend cache", async () => {
    api.get.mockResolvedValueOnce({ data: "unexpected SPA response" });
    const assertion = expect(refreshSnapchatSpend(period)).rejects.toThrow();
    await flush(); jest.advanceTimersByTime(2000); await flush(); await assertion;
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(clearDashboardPlatformSpendCache).not.toHaveBeenCalled();
});

test("readback requires new observations from every successful account, even when amounts are unchanged", () => {
    expect(snapchatSpendReadbackMatches(job("complete"), readback())).toBe(true);
    expect(snapchatSpendReadbackMatches(job("complete"), readback("2026-11-11T16:59:59Z"))).toBe(false);
    const missing = readback(); missing.providers.snapchat.accounts.pop();
    expect(snapchatSpendReadbackMatches(job("complete"), missing)).toBe(false);
    expect(snapchatSpendReadbackMatches(job("partial"), missing)).toBe(true);
    expect(snapchatSpendReadbackMatches(job("complete"), { ...readback(), date_to: "2026-11-05" })).toBe(false);
    const unknown = readback(); unknown.providers.snapchat.accounts[0].total_sar = null;
    expect(snapchatSpendReadbackMatches(job("complete"), unknown)).toBe(false);
    expect(snapchatSpendReadbackMatches(job(), readback())).toBe(false);
});
