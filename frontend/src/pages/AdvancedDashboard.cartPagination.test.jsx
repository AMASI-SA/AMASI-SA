import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { AbandonedCartsCard, useDashboardCarts } from "./AdvancedDashboard";

jest.mock("react-router-dom", () => ({ Link: ({ children }) => <>{children}</> }));

const cart = id => ({ cart_id: id, customer_name: `customer-${id}`, items: [], total: 10 });
const page = (ids, next = null, count = 100) => ({ data: {
    items: ids.map(cart), pagination: { next_cursor: next, has_more: Boolean(next) },
    abandoned_count: count, recovered_count: 7,
} });
function deferred() {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    return { promise, resolve, reject };
}
function Harness({ from = "2026-10-01", client }) {
    const state = useDashboardCarts(from, from, client);
    return <AbandonedCartsCard key={state.periodKey} {...state} onMore={state.loadMore} />;
}
let container, root;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    Object.defineProperty(navigator, "onLine", { configurable: true, value: true });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});
afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    delete global.IS_REACT_ACT_ENVIRONMENT;
});
const render = async props => act(async () => root.render(<Harness {...props} />));
const clickMore = async () => act(async () => container.querySelector("button").click());
const focus = async () => act(async () => window.dispatchEvent(new Event("focus")));

test("More reveals cached rows, then immediately reveals fetched rows without replacing counters", async () => {
    const next = deferred();
    const client = { get: jest.fn().mockResolvedValueOnce(page(Array.from({ length: 50 }, (_, i) => `${i + 1}`), "next-page"))
        .mockReturnValueOnce(next.promise) };
    await render({ client });
    expect(container.textContent).toContain("customer-5");
    expect(container.textContent).not.toContain("customer-6");
    for (let i = 0; i < 9; i += 1) await clickMore();
    expect(client.get).toHaveBeenCalledTimes(1);
    await clickMore();
    await clickMore();
    await focus();
    expect(client.get).toHaveBeenCalledTimes(2);
    const url = new URL(client.get.mock.calls[1][0], "https://example.test");
    expect(url.searchParams.get("limit")).toBe("50");
    expect(url.searchParams.get("cursor")).toBe("next-page");
    await act(async () => next.resolve(page(["50", "51", "51", "52", "53", "54", "55"], null, 999)));
    expect(container.textContent).toContain("customer-55");
    expect(container.textContent.match(/customer-51/g)).toHaveLength(1);
    expect(container.textContent).toContain("متروكة 100");
    expect(container.querySelector("button").textContent).toBe("عرض أقل");
});

test("a failed next page preserves rows and cursor and can be retried", async () => {
    const client = { get: jest.fn().mockResolvedValueOnce(page(["1", "2", "3", "4", "5"], "retry-cursor"))
        .mockRejectedValueOnce(new Error("temporary"))
        .mockResolvedValueOnce(page(["6"], null)) };
    await render({ client });
    await clickMore();
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.textContent).toContain("customer-5");
    expect(container.querySelector("button").disabled).toBe(false);
    await clickMore();
    expect(client.get.mock.calls[2][0]).toBe(client.get.mock.calls[1][0]);
    expect(container.textContent).toContain("customer-6");
    expect(container.querySelector('[role="alert"]')).toBeNull();
});

test("an old period page cannot append rows or clear the new period's in-flight guard", async () => {
    const oldMore = deferred(), newMore = deferred();
    const client = { get: jest.fn().mockResolvedValueOnce(page(["old"], "old-cursor"))
        .mockReturnValueOnce(oldMore.promise)
        .mockResolvedValueOnce(page(["new"], "new-cursor", 20))
        .mockReturnValueOnce(newMore.promise) };
    await render({ client });
    await clickMore();
    await render({ client, from: "2026-10-02" });
    expect(container.textContent).not.toContain("customer-old");
    await clickMore();
    await act(async () => oldMore.resolve(page(["stale"], "stale-cursor")));
    expect(container.querySelector("button").disabled).toBe(true);
    await focus();
    await clickMore();
    expect(client.get).toHaveBeenCalledTimes(4);
    expect(container.textContent).not.toContain("customer-stale");
    await act(async () => newMore.resolve(page(["new-next"], null)));
    expect(container.textContent).toContain("customer-new-next");
    expect(container.textContent).toContain("متروكة 20");
});

test("a stale first-page response is ignored; a refresh failure retains the current snapshot", async () => {
    const oldFirst = deferred();
    const client = { get: jest.fn().mockReturnValueOnce(oldFirst.promise)
        .mockResolvedValueOnce(page(["current"], "current-cursor", 12))
        .mockRejectedValueOnce(new Error("offline")) };
    await render({ client });
    await render({ client, from: "2026-10-02" });
    await act(async () => oldFirst.resolve(page(["obsolete"], "obsolete-cursor")));
    expect(container.textContent).not.toContain("customer-obsolete");
    await focus();
    expect(container.textContent).toContain("customer-current");
    expect(container.textContent).toContain("متروكة 12");
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
});

test("More cannot use a stale cursor during refresh and uses the refreshed cursor afterwards", async () => {
    const refreshed = deferred();
    const client = { get: jest.fn().mockResolvedValueOnce(page(["before"], "old-cursor"))
        .mockReturnValueOnce(refreshed.promise)
        .mockResolvedValueOnce(page(["after-next"], null)) };
    await render({ client });
    await focus();
    await clickMore();
    await focus();
    expect(client.get).toHaveBeenCalledTimes(2);
    await act(async () => refreshed.resolve(page(["after"], "fresh-cursor")));
    await clickMore();
    const url = new URL(client.get.mock.calls[2][0], "https://example.test");
    expect(url.searchParams.get("cursor")).toBe("fresh-cursor");
    expect(container.textContent).toContain("customer-after-next");
    expect(container.textContent).not.toContain("customer-before");
});
