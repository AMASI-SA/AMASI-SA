import http from "../../lib/api";
import { operationalApi } from "./api";

jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));

test("financial position reads the operational report without write or unsupported filters", async () => {
    const report = { schema_version: 1, authority: "operational_balance_report", status: "active" };
    http.get.mockResolvedValueOnce({ data: report });
    expect(await operationalApi.financialPosition()).toBe(report);
    expect(http.get).toHaveBeenCalledWith("/operational-balances/financial-position", {timeout: 60000});
    expect(http.post).not.toHaveBeenCalled();
});

test("permission failure remains rejected for the report page to handle", async () => {
    const denied = { response: { status: 403 } };
    http.get.mockRejectedValueOnce(denied);
    await expect(operationalApi.financialPosition()).rejects.toBe(denied);
    expect(http.post).not.toHaveBeenCalled();
});

test("scoped and bound full report reads forward only explicit GET configuration", async () => {
    const controller = new AbortController();
    const params = { view: "all", expected_revision: 7, expected_as_of: "2026-11-01T09:00:00Z", expected_started_at: "2026-10-31T21:00:00Z" };
    http.get.mockResolvedValueOnce({ data: { view: "all", revision: 7 } });
    await operationalApi.financialPosition({ params, signal: controller.signal });
    expect(http.get).toHaveBeenLastCalledWith("/operational-balances/financial-position", { timeout: 60000, params, signal: controller.signal });
    expect(http.post).not.toHaveBeenCalled();
});

test("targeted courier refresh binds the selected order and session in the body, not transport config", async () => {
    const controller = new AbortController(), scope = "a".repeat(64);
    const response = { status: "refreshed", session_scope: scope };
    http.post.mockResolvedValueOnce({ data: response });
    expect(await operationalApi.refreshBalances({ courierOrderId: "8101", expectedSessionScope: scope,
        signal: controller.signal, _mezanAuthDeadlineAt: 12345 })).toBe(response);
    expect(http.post).toHaveBeenLastCalledWith("/operational-balances/refresh", {
        visible: true, courier_order_id: "8101", expected_session_scope: scope,
    }, { timeout: 90000, signal: controller.signal, _mezanAuthDeadlineAt: 12345 });
});

test("ordinary visible refresh retains its original unscoped body", async () => {
    http.post.mockResolvedValueOnce({ data: { status: "refreshed" } });
    await operationalApi.refreshBalances();
    expect(http.post).toHaveBeenLastCalledWith("/operational-balances/refresh", { visible: true }, { timeout: 90000 });
});
