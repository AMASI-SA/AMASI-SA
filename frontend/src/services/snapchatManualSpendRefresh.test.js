import api from "../lib/api";
import { clearDashboardPlatformSpendCache } from "../lib/dashboardPlatformSpendClient";
import { refreshSnapchatSpend } from "./snapchatManualSpendRefresh";

jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));
jest.mock("../lib/dashboardPlatformSpendClient", () => ({ clearDashboardPlatformSpendCache: jest.fn() }));
const period = { dateFrom: "2026-11-04", dateTo: "2026-11-06" };
const flush = async () => { for (let i = 0; i < 8; i += 1) await Promise.resolve(); };
beforeEach(() => {
    jest.useFakeTimers(); jest.clearAllMocks();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    api.post.mockResolvedValue({ data: { run_id: "synthetic-run", status: "running" } });
});
afterEach(() => { jest.clearAllTimers(); jest.useRealTimers(); });

test("one explicit POST starts the job; status reads wait for canonical completion and invalidate old spend", async () => {
    api.get.mockResolvedValueOnce({ data: { run_id: "synthetic-run", status: "running" } })
        .mockResolvedValueOnce({ data: { run_id: "synthetic-run", status: "complete" } });
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

test("partial results remain partial and failed jobs never masquerade as a successful refresh", async () => {
    api.get.mockResolvedValueOnce({ data: { status: "partial" } });
    const partial = refreshSnapchatSpend(period); await flush();
    jest.advanceTimersByTime(2000); await flush();
    expect((await partial).status).toBe("partial");
    api.get.mockResolvedValueOnce({ data: { status: "failed", error: { code: "synthetic_provider_failure" } } });
    const failed = refreshSnapchatSpend(period);
    const assertion = expect(failed).rejects.toThrow();
    await flush(); jest.advanceTimersByTime(2000); await flush();
    await assertion;
    expect(clearDashboardPlatformSpendCache).toHaveBeenCalledTimes(1);
});

test("leaving the card cancels status reads without starting another provider refresh", async () => {
    const controller = new AbortController();
    const request = refreshSnapchatSpend({ ...period, signal: controller.signal });
    const assertion = expect(request).rejects.toMatchObject({ name: "AbortError" });
    await flush(); controller.abort(); await assertion;
    jest.advanceTimersByTime(60_000); await flush();
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.get).not.toHaveBeenCalled();
    expect(clearDashboardPlatformSpendCache).not.toHaveBeenCalled();
});
