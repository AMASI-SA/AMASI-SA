import api from "../../lib/api";
import { operationalApi } from "./api";
jest.mock("../../lib/api", () => ({ get: jest.fn() }));
test("daily feed uses dedicated read endpoint and preserves server snapshot pagination", async () => {
    const params = { from_date: "2026-11-01", to_date: "2026-11-07", page: 2, page_size: 10, snapshot: "a".repeat(64) };
    api.get.mockResolvedValue({ data: { items: [], total: 0 } });
    expect(await operationalApi.dailyFinancialMovements(params)).toEqual({ items: [], total: 0 });
    expect(api.get).toHaveBeenCalledWith("/operational-balances/daily-financial-movements", { params });
});
