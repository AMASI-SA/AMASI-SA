import {
    DASHBOARD_AUTO_REFRESH_MS,
    dashboardOrdersSignature,
    shouldRefreshDashboardForOrders,
} from "./dashboardLiveRefresh";

test("dashboard refresh interval keeps summary close to the live orders feed", () => {
    expect(DASHBOARD_AUTO_REFRESH_MS).toBe(30_000);
});

test("order signature changes for a new or updated order", () => {
    const first = dashboardOrdersSignature([{
        order_number: "278543496",
        updated_at: "2026-08-17T09:00:00Z",
        status: "under_review",
        total_amount: 122.23,
    }]);
    const updated = dashboardOrdersSignature([{
        order_number: "278543496",
        updated_at: "2026-08-17T09:01:00Z",
        status: "reviewed",
        total_amount: 122.23,
    }]);
    const next = dashboardOrdersSignature([{
        order_number: "278544000",
        updated_at: "2026-08-17T09:02:00Z",
        status: "under_review",
        total_amount: 176.12,
    }]);

    expect(updated).not.toBe(first);
    expect(next).not.toBe(first);
    expect(shouldRefreshDashboardForOrders(first, updated, true)).toBe(true);
    expect(shouldRefreshDashboardForOrders(first, next, true)).toBe(true);
});

test("first live order snapshot reconciles once and unchanged snapshots do not duplicate requests", () => {
    const signature = dashboardOrdersSignature([{ order_number: "278543496" }]);
    expect(shouldRefreshDashboardForOrders("", signature, true)).toBe(true);
    expect(shouldRefreshDashboardForOrders(signature, signature, true)).toBe(false);
    expect(shouldRefreshDashboardForOrders(signature, `${signature}-changed`, false)).toBe(false);
});
