import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { useOrder, useOrders } from "./useOrders";
import { getOrder, listOrders, refreshOrderFromSalla } from "../services/orderEngine";

jest.mock("../services/orderEngine", () => ({
    getOrder: jest.fn(), listOrders: jest.fn(), refreshOrderFromSalla: jest.fn(), ORDER_PAGE_SIZE: 15,
}));

let host;
let root;
let latest;
const deferred = () => {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
};
function Detail({ number }) {
    latest = useOrder(number);
    return <span>{latest.order?.shipping?.company || ""}</span>;
}
function List() { latest = useOrders(); return null; }
async function render(component) { await act(async () => { root.render(component); }); }
async function advance(ms) { await act(async () => { jest.advanceTimersByTime(ms); }); }

beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    jest.clearAllMocks();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(async () => {
    await act(async () => { root.unmount(); });
    host.remove();
    jest.useRealTimers();
});

test("details automatically read the new local carrier within three seconds without a Salla refresh", async () => {
    getOrder.mockResolvedValueOnce({ shipping: { company: "iMile للتوصيل" } })
        .mockResolvedValue({ shipping: { company: "مندوب الرياض" } });
    await render(<Detail number="275812811" />);
    expect(host.textContent).toBe("iMile للتوصيل");
    await advance(3000);
    expect(host.textContent).toBe("مندوب الرياض");
    expect(getOrder).toHaveBeenLastCalledWith("275812811");
    expect(refreshOrderFromSalla).not.toHaveBeenCalled();
});

test("detail polling skips hidden, offline and overlapping requests", async () => {
    getOrder.mockResolvedValueOnce({ shipping: { company: "iMile" } });
    await render(<Detail number="A" />);
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    await advance(3000);
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: false });
    await advance(3000);
    expect(getOrder).toHaveBeenCalledTimes(1);
    const pending = deferred();
    getOrder.mockReturnValue(pending.promise);
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    await advance(3000);
    await advance(9000);
    expect(getOrder).toHaveBeenCalledTimes(2);
    await act(async () => { pending.resolve({ shipping: { company: "مندوب" } }); });
    expect(host.textContent).toBe("مندوب");
});

test("changing the order starts its own request and ignores a late response for the prior order", async () => {
    const first = deferred();
    const second = deferred();
    getOrder.mockImplementation((number) => number === "A" ? first.promise : second.promise);
    await render(<Detail number="A" />);
    await render(<Detail number="B" />);
    expect(getOrder).toHaveBeenCalledWith("B");
    await act(async () => { second.resolve({ shipping: { company: "B courier" } }); });
    expect(host.textContent).toBe("B courier");
    await act(async () => { first.resolve({ shipping: { company: "A old courier" } }); });
    expect(host.textContent).toBe("B courier");
    expect(latest.loading).toBe(false);
});

test("list polling keeps its ten second cadence", async () => {
    listOrders.mockResolvedValue({ items: [], nextCursor: null });
    await render(<List />);
    await advance(9999);
    expect(listOrders).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(listOrders).toHaveBeenCalledTimes(2);
});

test("returning to an order cannot revive its earlier in-flight response", async () => {
    const oldA = deferred();
    getOrder.mockReturnValueOnce(oldA.promise)
        .mockResolvedValueOnce({ shipping: { company: "B courier" } })
        .mockResolvedValueOnce({ shipping: { company: "A current courier" } });
    await render(<Detail number="A" />);
    await render(<Detail number="B" />);
    await render(<Detail number="A" />);
    await act(async () => { oldA.resolve({ shipping: { company: "A stale courier" } }); });
    expect(host.textContent).toBe("A current courier");
    expect(getOrder).toHaveBeenCalledTimes(3);
});

test("combined search loads additional pages only on demand with the same filters", async () => {
    listOrders.mockResolvedValueOnce({ items: [], nextCursor: null });
    await render(<List />);
    const filters = { campaign_id: "campaign-1" };
    listOrders.mockResolvedValueOnce({ items: [{ order_number: "1" }], nextCursor: "search:hash:50", resultSummary: { orders: 51 } });
    await act(async () => latest.searchCombined(filters));
    expect(latest.hasMore).toBe(true);
    listOrders.mockResolvedValueOnce({ items: [{ order_number: "2" }], nextCursor: null });
    await act(async () => latest.loadMore());
    expect(listOrders).toHaveBeenLastCalledWith(expect.objectContaining({ filters, cursor: "search:hash:50", limit: 50 }));
    expect(latest.orders.map(row => row.order_number)).toEqual(["1", "2"]);
    expect(latest.hasMore).toBe(false);
});

test.each([{ campaign_id: 'campaign-1' }, { sku: 'AMS11889' }])('automatic refresh preserves combined results and pagination for %j', async (filters) => {
    listOrders.mockResolvedValue({ items: [], nextCursor: null });
    await render(<List />);
    const rows = Array.from({ length: 50 }, (_, i) => ({ order_number: String(i + 1) }));
    listOrders.mockResolvedValueOnce({ items: rows, nextCursor: 'search:hash:50', resultSummary: { orders: 408 } });
    getOrder.mockResolvedValue(rows[0]);
    await act(async () => latest.searchCombined(filters));
    await advance(30000);
    await act(async () => {
        window.dispatchEvent(new Event('focus'));
        window.dispatchEvent(new Event('online'));
        document.dispatchEvent(new Event('visibilitychange'));
    });
    expect(latest.orders).toEqual(rows);
    expect(latest.hasMore).toBe(true);
    expect(latest.resultSummary).toEqual({ orders: 408 });
    expect(getOrder).not.toHaveBeenCalled();
    expect(listOrders).toHaveBeenCalledTimes(2);
    listOrders.mockResolvedValueOnce({ items: [{ order_number: '51' }], nextCursor: null });
    await act(async () => latest.loadMore());
    expect(latest.orders).toHaveLength(51);
});

test('exact order refresh resumes after combined search and ignores responses from a prior search', async () => {
    listOrders.mockResolvedValue({ items: [], nextCursor: null });
    await render(<List />);
    await act(async () => latest.searchCombined({ sku: 'old' }));
    getOrder.mockResolvedValue({ order_number: 'A', status: 'old' });
    await act(async () => latest.searchExactOrder('A'));
    getOrder.mockResolvedValueOnce({ order_number: 'A', status: 'new' });
    await advance(10000);
    expect(latest.orders[0].status).toBe('new');
    const pending = deferred();
    getOrder.mockReturnValueOnce(pending.promise);
    await advance(10000);
    const rows = [{ order_number: 'B' }, { order_number: 'C' }];
    listOrders.mockResolvedValueOnce({ items: rows, nextCursor: null, resultSummary: { orders: 2 } });
    await act(async () => latest.searchCombined({ sku: 'new' }));
    await act(async () => pending.resolve({ order_number: 'A', status: 'stale' }));
    expect(latest.orders).toEqual(rows);
    await act(async () => latest.reload());
    const calls = listOrders.mock.calls.length;
    await advance(10000);
    expect(listOrders).toHaveBeenCalledTimes(calls + 1);
});
