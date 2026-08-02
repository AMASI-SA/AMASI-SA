jest.mock("../lib/api", () => ({
    get: jest.fn(),
    put: jest.fn(),
}));

import api from "../lib/api";
import {
    getAdAccountCostSettingsV2,
    normalizeAdAccountCostSettingsV2,
    saveAdAccountCostSettingsV2,
} from "./adsAccountCostSettingsV2";

beforeEach(() => {
    jest.clearAllMocks();
});

test("normalizes only native Mezan 2 advertising accounts", () => {
    const result = normalizeAdAccountCostSettingsV2({
        items: [
            {
                mezan_integration_account_id: "snap-1",
                provider: "snapchat_ads",
                external_account_id: "external-snap",
                display_name: "Snap USD",
                native_currency: "USD",
                exchange_rate_to_sar: 3.81,
                bank_commission_pct: 2.3,
                apply_bank_commission: true,
                configured: true,
            },
            {
                mezan_integration_account_id: "legacy-1",
                provider: "legacy_ads",
                external_account_id: "legacy",
            },
        ],
        policy: {
            legacy_counterparties_read: false,
            legacy_ads_currency_settings_read: false,
            accounting_write_reached: false,
            qoyod_write_reached: false,
        },
    });

    expect(result.items).toHaveLength(1);
    expect(result.items[0]).toMatchObject({
        provider: "snapchat_ads",
        native_currency: "USD",
        exchange_rate_to_sar: 3.81,
        bank_commission_pct: 2.3,
        apply_bank_commission: true,
    });
    expect(result.policy).toEqual({
        legacy_counterparties_read: false,
        legacy_ads_currency_settings_read: false,
        accounting_write_reached: false,
        qoyod_write_reached: false,
    });
});

test("loads from the Mezan 2 ads manager endpoint", async () => {
    api.get.mockResolvedValue({ data: { items: [] } });

    await getAdAccountCostSettingsV2();

    expect(api.get).toHaveBeenCalledWith("/ads-manager/account-cost-settings");
});

test("saves an independent configuration per integration account", async () => {
    api.put.mockResolvedValue({
        data: {
            mezan_integration_account_id: "meta-account-1",
            provider: "meta_ads",
            external_account_id: "act_1",
            display_name: "Meta",
            native_currency: "SAR",
            exchange_rate_to_sar: 1,
            bank_commission_pct: 0,
            apply_bank_commission: false,
            configured: true,
        },
    });

    const saved = await saveAdAccountCostSettingsV2("meta/account 1", {
        native_currency: "SAR",
        exchange_rate_to_sar: 7,
        bank_commission_pct: 0,
        apply_bank_commission: false,
    });

    expect(api.put).toHaveBeenCalledWith(
        "/ads-manager/account-cost-settings/meta%2Faccount%201",
        {
            native_currency: "SAR",
            exchange_rate_to_sar: 1,
            bank_commission_pct: 0,
            apply_bank_commission: false,
        },
    );
    expect(saved.configured).toBe(true);
});
