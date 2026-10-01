import api from "../../../lib/api";
import { ADVERTISING_BASE, advertisingReadFailure, readAdvertisingContext, readAdvertisingDueItems, readAdvertisingSource } from "./advertisingAdapter";
jest.mock("../../../lib/api", () => ({ get: jest.fn() }));
const account = { platform: "meta", integration_account_id: "SYN-E-1", currency: "USD" };
beforeEach(() => jest.resetAllMocks());
test("context accepts only native identity contract and preserves unknown balances", async () => {
    const data = { stage: 12, identity_source: "mezan_integration_accounts_v2", items: [{ ...account, original_wallet: { posted_original_balance: null } }] };
    api.get.mockResolvedValue({ data });
    expect(await readAdvertisingContext()).toBe(data);
    expect(api.get).toHaveBeenCalledWith(`${ADVERTISING_BASE}/stage-12`);
    expect(api.get).toHaveBeenCalledTimes(1);
});
test.each([{}, { stage: 12, identity_source: "old_ad_accounts", items: [] }, { stage: 12, identity_source: "mezan_integration_accounts_v2", items: [{ platform: "meta" }] }])("rejects a non-native or malformed context without fallback", async data => {
    api.get.mockResolvedValue({ data });
    await expect(readAdvertisingContext()).rejects.toThrow("ad_native_read_contract_invalid");
    expect(api.get).toHaveBeenCalledTimes(1);
});
test("due queue preserves backend date and uses its bounded native endpoint", async () => {
    const items = [{ ...account, business_date: "2026-09-29", due_at: "2026-09-30T01:00:00+03:00" }];
    api.get.mockResolvedValue({ data: { items } });
    expect(await readAdvertisingDueItems()).toBe(items);
    expect(api.get).toHaveBeenCalledWith(`${ADVERTISING_BASE}/due-items`, { params: { limit: 100 } });
});
test("daily source preserves explicit zero as source evidence, never invents CLOSED_ZERO or SAR", async () => {
    const data = { ...account, business_date: "2026-09-29", original_currency: "USD", original_amount: "0", source_revision: "SYN-REV", native_approval_required: true };
    api.get.mockResolvedValue({ data });
    expect(await readAdvertisingSource(account, "2026-09-29")).toBe(data);
    expect(data.status).toBeUndefined();
    expect(api.get).toHaveBeenCalledWith(`${ADVERTISING_BASE}/daily-source`, { params: { platform: "meta", integration_account_id: "SYN-E-1", business_date: "2026-09-29" } });
});
test("rejects cross-account daily source", async () => {
    api.get.mockResolvedValue({ data: { ...account, integration_account_id: "OTHER", business_date: "2026-09-29", original_currency: "USD", source_revision: "SYN", native_approval_required: true } });
    await expect(readAdvertisingSource(account, "2026-09-29")).rejects.toThrow("ad_native_read_contract_invalid");
});
test.each([404, 409, 422, 423, 503])("%s is unavailable, never a zero balance", status => {
    expect(advertisingReadFailure({ response: { status, data: { detail: { code: "ad_source_day_not_closed" } } } })).toEqual({ blocked: true, reason: "ad_source_day_not_closed" });
});
test("transport and auth failure remain errors", () => {
    expect(advertisingReadFailure(new Error("offline")).blocked).toBe(false);
    expect(advertisingReadFailure({ response: { status: 403 } }).blocked).toBe(false);
});
