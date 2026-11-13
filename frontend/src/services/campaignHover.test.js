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
