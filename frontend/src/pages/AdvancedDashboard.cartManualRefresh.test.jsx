import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { AbandonedCartsCard, useDashboardCarts } from "./AdvancedDashboard";
jest.mock("react-router-dom", () => ({ Link: ({ children }) => <>{children}</> }));

const page = id => ({ data: { items: [{ cart_id: id, customer_name: id, items: [], total: 10 }],
    pagination: {}, abandoned_count: 1, recovered_count: 0 } });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function Harness({ client, user, from = "2026-10-05", filters = {} }) {
    const state = useDashboardCarts(from, from, client, { user, filters });
    return <AbandonedCartsCard key={state.periodKey} {...state} onMore={state.loadMore} onRefresh={state.refresh} />;
}
let root, host, user;
beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    user = { id: "user-A", tenant_id: "tenant-A" };
    host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); jest.useRealTimers(); delete global.IS_REACT_ACT_ENVIRONMENT; });
const render = props => act(async () => root.render(<Harness user={user} {...props} />));
const click = () => act(async () => host.querySelector('[data-testid="refresh-carts"]').click());

test("opening and five minutes of timers/focus/online do not fetch; one click fetches carts only", async () => {
    const client = { get: jest.fn().mockResolvedValue(page("one")) };
    await render({ client });
    expect(host.textContent).toContain("لا توجد بيانات محفوظة؛ اضغط تحديث البيانات");
    await act(async () => { jest.advanceTimersByTime(300000); window.dispatchEvent(new Event("focus")); window.dispatchEvent(new Event("online")); document.dispatchEvent(new Event("visibilitychange")); });
    expect(client.get).not.toHaveBeenCalled();
    await click();
    expect(client.get).toHaveBeenCalledTimes(1);
    expect(client.get.mock.calls[0][0]).toMatch(/^\/dashboard-v2\/abandoned-carts\/recent\?/);
    expect(host.textContent).toContain("one"); expect(host.textContent).toContain("آخر تحديث:");
    await act(async () => jest.advanceTimersByTime(300000));
    expect(client.get).toHaveBeenCalledTimes(1);
});

test("rapid clicks single-flight; loading and failure retain last successful snapshot and timestamp", async () => {
    const pending = deferred();
    const client = { get: jest.fn().mockResolvedValueOnce(page("old")).mockReturnValueOnce(pending.promise).mockRejectedValueOnce(new Error("offline")) };
    await render({ client }); await click();
    const oldTime = host.querySelector("time").dateTime;
    await act(async () => { const button = host.querySelector('[data-testid="refresh-carts"]'); button.click(); button.click(); button.click(); });
    expect(client.get).toHaveBeenCalledTimes(2); expect(host.textContent).toContain("old");
    expect(host.querySelector('[data-testid="refresh-carts"]').disabled).toBe(true);
    await act(async () => { jest.advanceTimersByTime(1000); pending.resolve(page("new")); });
    expect(host.textContent).toContain("new"); expect(host.querySelector("time").dateTime).not.toBe(oldTime);
    const newTime = host.querySelector("time").dateTime;
    await click(); expect(host.textContent).toContain("new"); expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(host.querySelector("time").dateTime).toBe(newTime);
});

test("period/filter changes do not fetch or expose previous scope; late responses are ignored", async () => {
    const pending = deferred(); const client = { get: jest.fn().mockReturnValueOnce(pending.promise).mockResolvedValue(page("current")) };
    await render({ client }); await click();
    await render({ client, from: "2026-10-04" });
    await act(async () => pending.resolve(page("obsolete")));
    expect(host.textContent).not.toContain("obsolete"); expect(client.get).toHaveBeenCalledTimes(1);
    await click(); expect(host.textContent).toContain("current");
    await render({ client, from: "2026-10-04", filters: { shipping_companies: ["iMile"] } });
    expect(host.textContent).not.toContain("current"); expect(client.get).toHaveBeenCalledTimes(2);
});

test("navigation restores same authenticated scope; another user, tenant or login cannot reuse it", async () => {
    const client = { get: jest.fn().mockResolvedValue(page("private-A")) };
    await render({ client }); await click();
    await act(async () => root.render(null)); await render({ client });
    expect(host.textContent).toContain("private-A"); expect(client.get).toHaveBeenCalledTimes(1);
    await render({ client, user: { id: "user-B", tenant_id: "tenant-A" } }); expect(host.textContent).not.toContain("private-A");
    await render({ client, user: { id: "user-A", tenant_id: "tenant-B" } }); expect(host.textContent).not.toContain("private-A");
    await render({ client, user: null }); expect(host.querySelector('[data-testid="refresh-carts"]').disabled).toBe(true);
    await render({ client, user: { ...user } }); expect(host.textContent).not.toContain("private-A");
    expect(client.get).toHaveBeenCalledTimes(1);
});

test("user switch during request cannot populate a different user's snapshot", async () => {
    const pending = deferred(); const client = { get: jest.fn().mockReturnValue(pending.promise) };
    await render({ client }); await click(); await render({ client, user: { id: "B" } });
    await act(async () => pending.resolve(page("private-A")));
    expect(host.textContent).not.toContain("private-A");
});

test("first fetch failure never presents an empty response as a successful snapshot", async () => {
    const client = { get: jest.fn().mockRejectedValue(new Error("offline")) };
    await render({ client }); await click();
    expect(host.textContent).toContain("لا توجد بيانات محفوظة؛ اضغط تحديث البيانات");
    expect(host.querySelector("time")).toBeNull();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="refresh-carts"]').disabled).toBe(false);
});
