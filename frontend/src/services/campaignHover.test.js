import api from "../lib/api";
import { getCampaignHoverBatch } from "./campaignHover";
jest.mock("../lib/api", () => ({ __esModule: true, default: { get: jest.fn() } }));
beforeEach(() => jest.resetAllMocks());

test("deduplicates identifiers into one bounded request", async () => {
    api.get.mockResolvedValue({ data: { campaigns: { a: { orders: 2 }, b: { orders: 1 } } } });
    const result = await getCampaignHoverBatch(["b", "a", "a"]);
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get).toHaveBeenCalledWith("/orders-v2/campaign-hover", { params: { campaign_ids: "a,b" }, timeout: 12000 });
    expect(result.a.orders).toBe(2);
});

test("does not retain financial responses between browser sessions", async () => {
    api.get.mockResolvedValueOnce({ data: { campaigns: { a: { sales_sar: 100 } } } });
    api.get.mockResolvedValueOnce({ data: { campaigns: { a: { sales_sar: 200 } } } });
    expect((await getCampaignHoverBatch(["a"])).a.sales_sar).toBe(100);
    expect((await getCampaignHoverBatch(["a"])).a.sales_sar).toBe(200);
    expect(api.get).toHaveBeenCalledTimes(2);
});

test("failure is an explicit unavailable result, not zero or endless loading", async () => {
    api.get.mockRejectedValue(new Error("offline"));
    const value = (await getCampaignHoverBatch(["a"])).a;
    expect(value.status).toBe("report_unavailable");
    expect(value.sales_sar).toBeUndefined();
    expect(await getCampaignHoverBatch([])).toEqual({});
});

test("order revision reaches the server snapshot", async () => {
  api.get.mockResolvedValue({ data: { campaigns: { a: { orders: 31 } } } });
  await getCampaignHoverBatch(["a"], { snapshotRevision: "r:31" });
  expect(api.get).toHaveBeenCalledWith("/orders-v2/campaign-hover", {
    params: { campaign_ids: "a", snapshot_revision: "r:31" }, timeout: 12000,
  });
});

test("forwards the complete normalized executive scope, never client financial inputs", async () => {
    api.get.mockResolvedValue({ data: { campaigns: { a: { orders: 2 } } } });
    await getCampaignHoverBatch(["a"], { snapshotRevision: "r:31", operatingContext: {
        from: "2026-11-01", to: "2026-11-13", payment_methods: [" mada ", "cash", "cash"],
        shipping_companies: ["b", "a", "b"], report_statuses: ["reviewed", "in_progress", "reviewed"],
        operating_expenses_ratio: 0.99, operating_expenses_total: 999, total_sales: 1,
    } });
    expect(api.get).toHaveBeenCalledWith("/orders-v2/campaign-hover", {
        params: { campaign_ids: "a", snapshot_revision: "r:31",
            operating_from_date: "2026-11-01", operating_to_date: "2026-11-13",
            operating_payment_methods: "cash,mada", operating_shipping_companies: "a,b",
            operating_report_statuses: "in_progress,reviewed" }, timeout: 12000,
    });
});

test("default report statuses stay omitted instead of becoming an explicit status override", async () => {
    api.get.mockResolvedValue({ data: { campaigns: {} } });
    await getCampaignHoverBatch(["a"], { operatingContext: { from: "2026-11-13", report_statuses: [] } });
    expect(api.get.mock.calls[0][1].params).toEqual({ campaign_ids: "a",
        operating_from_date: "2026-11-13", operating_to_date: "2026-11-13" });
});

test.each([null, {}, { payment_methods: ["cash"] }])("a pending or missing period never invokes a silent default: %j", async (operatingContext) => {
    expect(await getCampaignHoverBatch(["a"], { operatingContext })).toEqual({});
    expect(api.get).not.toHaveBeenCalled();
});
