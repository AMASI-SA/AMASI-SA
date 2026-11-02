import api from "../lib/api";
import { getOnboardingDomainContext } from "./onboardingDomainContext";
jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn() }));
beforeEach(() => jest.clearAllMocks());

test("courier operational and MZ2 contracts load independently using GET only", async () => {
    api.get.mockImplementation(path => path.endsWith("courier-bindings") ? Promise.resolve({ data: { items: [{ courier_key: "imile" }] } }) : Promise.reject({ response: { status: 403 } }));
    const result = await getOnboardingDomainContext("courier_balances");
    expect(api.get.mock.calls).toEqual([
        ["/financial-provider-apps/accounting-module/settlements/courier-bindings"],
        ["/accounting-module/shipping-v2/rich-contracts"],
    ]);
    expect(result.sources).toEqual([
        expect.objectContaining({ key: "couriers", status: "ready", data: { items: [{ courier_key: "imile" }] } }),
        expect.objectContaining({ key: "native", status: "error", httpStatus: 403 }),
    ]);
    expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});

test("driver list performs no individual ledger or custody reads", async () => {
    api.get.mockImplementation(path => Promise.resolve({ data: path === "/store-delivery/drivers" ? { items: [{ id: "driver-a" }, { id: "driver-b" }] } : { store_drivers: [], stages: {} } }));
    const result = await getOnboardingDomainContext("drivers");
    expect(result.sources.every(source => source.status === "ready")).toBe(true);
    expect(api.get.mock.calls.map(([path]) => path)).toEqual(["/store-delivery/drivers", "/accounting-module/shipping-v2/context"]);
});

test("selected driver reads exact encoded identity and rejects a foreign native response", async () => {
    api.get.mockImplementation(path => Promise.resolve({ data: path.includes("statements")
        ? { ledger_source: "accounting_v2", party_type: "store_driver", party_id: "other-driver", entries: [] }
        : path.includes("driver-cash") ? { driver_id: "driver/a", read_only: true, scope: "captured_delivered_cash_only", items: [] }
            : { ledger_source: "accounting_v2", read_only: true, items: [{ driver_id: "driver/a" }] } }));
    const result = await getOnboardingDomainContext("drivers", { driverId: "driver/a" });
    expect(api.get.mock.calls).toEqual([
        ["/accounting-module/shipping-v2/statements/store_driver/driver%2Fa"],
        ["/accounting-module/shipping-v2/driver-cash/driver%2Fa"],
        ["/accounting-module/shipping-v2/driver-payment-history", { params: { driver_id: "driver/a", limit: 50 } }],
    ]);
    expect(result.sources[0]).toMatchObject({ status: "error", invalid: true });
    expect(result.sources.slice(1).every(source => source.status === "ready")).toBe(true);
    expect(api.post).not.toHaveBeenCalled();
});

test("identity-free or cross-stage detail calls stop before network", async () => {
    await expect(getOnboardingDomainContext("drivers", { driverId: "" })).rejects.toThrow("identity_required");
    await expect(getOnboardingDomainContext("payment_fees", { driverId: "driver-a" })).rejects.toThrow("identity_required");
    expect(api.get).not.toHaveBeenCalled();
});

test("prepaid contextual read uses native facts and operating obligations, never legacy balances or writes", async () => {
    api.get.mockImplementation(path => Promise.resolve({ data: path === "/recurring-obligations" ? { items: [], source_contract: "operating_recurring_obligations_v2" } : { items: [] } }));
    expect((await getOnboardingDomainContext("prepaid")).sources.every(s => s.status === "ready")).toBe(true);
    expect(api.get.mock.calls).toEqual([["/recurring-obligations"], ["/accounting-module/onboarding/typed-facts"]]);
    expect(api.post).not.toHaveBeenCalled();
});

test("malformed successful response is not displayed as empty", async () => {
    api.get.mockResolvedValue({ data: { unsupported: true } });
    expect((await getOnboardingDomainContext("payment_fees")).sources.every(s => s.status === "error" && s.invalid)).toBe(true);
});
