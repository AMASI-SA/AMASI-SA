import React from "react";
import { createRoot } from "react-dom/client";
import { act } from "react-dom/test-utils";
import useCampaignHover, { campaignSnapshotRevision } from "./useCampaignHover";
import { getCampaignHoverBatch } from "../services/campaignHover";

jest.mock("../services/campaignHover", () => ({ getCampaignHoverBatch: jest.fn() }));
const CID = "11111111-1111-4111-8111-111111111111";
const order = (id, extra = {}) => ({ order_number: id, campaign_id: CID,
    utm_source: "snapchat", order_date: "2026-11-13T12:00:00Z",
    status: "processing", total_amount: 100, items: [{ id: "piece", quantity: 1 }], ...extra });
const NOW = new Date("2026-11-13T12:00:10Z").getTime();
let host, root;

function View({ orders, totals }) {
    const reports = useCampaignHover(orders, { totals });
    return <span>{reports[CID]?.orders ?? "pending"}</span>;
}
async function settle() { await act(async () => { jest.advanceTimersByTime(400); }); }

beforeEach(() => {
    jest.useFakeTimers(); jest.setSystemTime(NOW); jest.clearAllMocks();
    host = document.createElement("div"); document.body.appendChild(host);
    root = createRoot(host);
    getCampaignHoverBatch.mockResolvedValue({ [CID]: { orders: 30 } });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.useRealTimers(); });

test("same campaign ID with a new Salla order fetches a new revision and updated count", async () => {
    await act(async () => root.render(<View orders={[order("30")]} totals={{ count: 30 }} />));
    await settle();
    expect(host.textContent).toBe("30");
    const previous = getCampaignHoverBatch.mock.calls[0][1].snapshotRevision;
    getCampaignHoverBatch.mockResolvedValue({ [CID]: { orders: 31 } });
    await act(async () => root.render(<View orders={[order("31"), order("30")]} totals={{ count: 31 }} />));
    await settle();
    expect(getCampaignHoverBatch).toHaveBeenCalledTimes(2);
    expect(getCampaignHoverBatch.mock.calls[1][0]).toEqual([CID]);
    expect(getCampaignHoverBatch.mock.calls[1][1].snapshotRevision).not.toBe(previous);
    expect(host.textContent).toBe("31");
});

test("unchanged refreshed orders coalesce, and no timer polls on its own", async () => {
    await act(async () => root.render(<View orders={[order("30")]} totals={{ count: 30 }} />));
    await settle();
    await act(async () => root.render(<View orders={[order("30")]} totals={{ count: 30 }} />));
    await settle();
    await act(async () => { jest.advanceTimersByTime(600000); });
    expect(getCampaignHoverBatch).toHaveBeenCalledTimes(1);
});

test("an existing order refresh after expiry can refresh spend without a new polling loop", async () => {
    await act(async () => root.render(<View orders={[order("30")]} totals={{ count: 30 }} />));
    await settle();
    jest.setSystemTime(NOW + 181000);
    await act(async () => root.render(<View orders={[order("30")]} totals={{ count: 30 }} />));
    await settle();
    expect(getCampaignHoverBatch).toHaveBeenCalledTimes(2);
});

test("an older delayed result cannot overwrite the newly requested order snapshot", async () => {
    let oldResult;
    getCampaignHoverBatch.mockImplementationOnce(() => new Promise((resolve) => { oldResult = resolve; }));
    await act(async () => root.render(<View orders={[order("30")]} totals={{ count: 30 }} />));
    await settle();
    getCampaignHoverBatch.mockResolvedValue({ [CID]: { orders: 31 } });
    await act(async () => root.render(<View orders={[order("31"), order("30")]} totals={{ count: 31 }} />));
    await settle();
    await act(async () => oldResult({ [CID]: { orders: 30 } }));
    expect(host.textContent).toBe("31");
});

test("changes to cancellation, quantity or total invalidate but customer details do not", () => {
    const base = campaignSnapshotRevision([order("30")], { count: 30 }, NOW);
    for (const changed of [
        { status: "cancelled" }, { total_amount: 200 }, { items: [{ id: "piece", quantity: 2 }] },
    ]) expect(campaignSnapshotRevision([order("30", changed)], { count: 30 }, NOW)).not.toBe(base);
    expect(campaignSnapshotRevision([order("30", { customer_name: "private", phone: "private" })],
        { count: 30 }, NOW)).toBe(base);
    expect(base).toMatch(/^[a-z0-9]+:[a-f0-9]{16}$/);
});

test("no trusted campaign identifier means no guessed financial request", async () => {
    await act(async () => root.render(<View orders={[{ order_number: "30", campaign_name: "winter" }]} />));
    await settle();
    expect(getCampaignHoverBatch).not.toHaveBeenCalled();
});


test("canonical OrdersV2 money, item and result-summary changes invalidate the snapshot", () => {
    const dto = {
        order_number: "30", created_at: "2026-11-13T12:00:00Z", status: "processing",
        source: { campaign_id: CID, utm_source: "snapchat" },
        totals: { total: 100, total_sar: 100, currency: "SAR", exchange_rate_to_sar: "1" },
        items: [{ order_item_id: "line-1", quantity: 1, type: "product" }],
    };
    const base = campaignSnapshotRevision([dto], { orders: 30 }, NOW);
    for (const totals of [
        { ...dto.totals, total: 200 }, { ...dto.totals, total_sar: 200 },
        { ...dto.totals, currency: "QAR" }, { ...dto.totals, exchange_rate_to_sar: "1.03" },
    ]) expect(campaignSnapshotRevision([{ ...dto, totals }], { orders: 30 }, NOW)).not.toBe(base);
    expect(campaignSnapshotRevision([dto], { orders: 31 }, NOW)).not.toBe(base);
    expect(campaignSnapshotRevision([{ ...dto, items: [{ ...dto.items[0], type: "service" }] }],
        { orders: 30 }, NOW)).not.toBe(base);
    expect(campaignSnapshotRevision([{ ...dto, customer: { name: "private" },
        source: { ...dto.source, fetched_at: "2026-11-13T12:01:00Z" } }], { orders: 30 }, NOW)).toBe(base);
});
