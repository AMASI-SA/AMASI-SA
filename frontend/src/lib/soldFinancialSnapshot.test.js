import { loadSoldFinancialSnapshot } from "./soldFinancialSnapshot";
import { financialMetrics } from "../components/SoldFinancialReport";
jest.mock("./api", () => ({ get: jest.fn() }));
jest.mock("../services/dashboardAdsSpend", () => ({ getDashboardAdsSpend: jest.fn() }));

const range = { from: "2026-11-08", to: "2026-11-08", statuses: "reviewed,in_progress" };
const canonical = () => ({ totals: { total_sales: 23210.85, total_orders: 103,
    total_product_cost: 10308.16, total_ads_cost: 11458.62, net_profit: -3530.94,
    total_shipping_cost: 1878, total_payment_fees: 1202.09, operating_expenses_total: 1894.92,
    product_cost_complete: true, ads_spend_data_complete: true },
    ads_v2: { spend_quality: { status: "complete", amount_complete: true, amount_available: true } } });
const platform = () => ({ date_from: range.from, date_to: range.to, total_sar: 11508.09,
    provider_totals_sar: { snapchat: 9039.59, meta: 2126.54, tiktok: 292.49, google: 49.47 },
    spend_quality: { status: "complete", amount_complete: true, amount_available: true } });

test("report includes Google and matches executive profit with the same period and filters", async () => {
    const apiClient = { get: jest.fn().mockResolvedValue({ data: canonical() }) };
    const spendLoader = jest.fn().mockResolvedValue(platform());
    const result = await loadSoldFinancialSnapshot(range, { apiClient, spendLoader });
    expect(result.totals.total_ads_cost).toBe(11508.09);
    expect(result.totals.net_profit).toBe(-3580.41);
    expect(result.ads_v2.executive_breakdown.providers.google.spend_sar).toBe(49.47);
    expect(financialMetrics(result, [{ cost_status: "complete", total_cost: 10308.16 }]).profit).toBe(-3580.41);
    expect(apiClient.get).toHaveBeenCalledWith("/dashboard-v2", expect.objectContaining({ params: expect.objectContaining({ report_statuses: range.statuses, from_date: range.from, to_date: range.to }) }));
    expect(spendLoader).toHaveBeenCalledWith({ dateFrom: range.from, dateTo: range.to, refresh: false });
});

test("does not merge another period or substitute zero when platform read fails", async () => {
    const apiClient = { get: jest.fn().mockResolvedValue({ data: canonical() }) };
    await expect(loadSoldFinancialSnapshot(range, { apiClient, spendLoader: async () => ({ ...platform(), date_to: "2026-11-07" }) })).rejects.toThrow("period_mismatch");
    await expect(loadSoldFinancialSnapshot(range, { apiClient, spendLoader: async () => { throw new Error("unavailable"); } })).rejects.toThrow("unavailable");
});

test("recomputes unavailable canonical finances once before applying complete saved spend", async () => {
    const incomplete = canonical(); incomplete.totals.total_ads_cost = null; incomplete.totals.net_profit = null;
    const apiClient = { get: jest.fn().mockResolvedValueOnce({ data: incomplete }).mockResolvedValueOnce({ data: canonical() }) };
    const result = await loadSoldFinancialSnapshot(range, { apiClient, spendLoader: async () => platform() });
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(result.totals.net_profit).toBe(-3580.41);
});

test("a stalled spend read fails visibly instead of keeping report loading forever", async () => {
    const apiClient = { get: jest.fn().mockResolvedValue({ data: canonical() }) };
    await expect(loadSoldFinancialSnapshot(range, { apiClient, spendLoader: () => new Promise(() => {}), spendTimeoutMs: 10 })).rejects.toThrow("financial_spend_timeout");
});
