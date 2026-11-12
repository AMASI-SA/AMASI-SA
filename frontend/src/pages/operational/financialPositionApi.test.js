import http from "../../lib/api";
import { operationalApi } from "./api";

jest.mock("../../lib/api", () => ({ get: jest.fn(), post: jest.fn() }));

test("financial position reads the operational report without write or unsupported filters", async () => {
    const report = { schema_version: 1, authority: "operational_balance_report", status: "active" };
    http.get.mockResolvedValueOnce({ data: report });
    expect(await operationalApi.financialPosition()).toBe(report);
    expect(http.get).toHaveBeenCalledWith("/operational-balances/financial-position", {timeout: 45000});
    expect(http.post).not.toHaveBeenCalled();
});

test("permission failure remains rejected for the report page to handle", async () => {
    const denied = { response: { status: 403 } };
    http.get.mockRejectedValueOnce(denied);
    await expect(operationalApi.financialPosition()).rejects.toBe(denied);
    expect(http.post).not.toHaveBeenCalled();
});
