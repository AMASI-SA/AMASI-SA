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
