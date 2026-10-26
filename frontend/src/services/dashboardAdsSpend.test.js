import { loadDashboardPlatformSpend } from "../lib/dashboardPlatformSpendClient";
import {
    DASHBOARD_ADS_PROVIDERS,
    getDashboardAdsSpend,
    normalizeDashboardAdsHourlySpend,
    normalizeDashboardAdsSpend,
} from "./dashboardAdsSpend";

jest.mock("../lib/dashboardPlatformSpendClient", () => ({
    loadDashboardPlatformSpend: jest.fn(),
}));

beforeEach(() => {
    jest.clearAllMocks();
});


function fourPlatformPayload(overrides = {}) {
    return {
        date_from: "2026-08-05",
        date_to: "2026-08-05",
        timezone: "Asia/Riyadh",
        chart_granularity: "hour",
        provider_totals_sar: {
            snapchat: 100,
            meta: 20,
            tiktok: 5,
            google: 7.5,
        },
        total_sar: 132.5,
        providers: Object.fromEntries(
            DASHBOARD_ADS_PROVIDERS.map((provider) => [
                provider,
                {
                    provider,
                    integration_provider: provider === "google"
                        ? "google_ads"
                        : `${provider}_ads`,
                    connection_status: "connected",
                    connected: true,
                    daily_available: true,
                    hourly_available: true,
                    hourly_source: provider === "tiktok"
                        ? "make_daily_total_marker"
                        : "provider_native",
                    total_sar: 1,
                },
            ]),
        ),
        daily_spend: [
            {
                date: "2026-08-05",
                snapchat: 100,
                meta: 20,
                tiktok: 5,
                google: 7.5,
            },
        ],
        hourly_spend: [
            {
                date: "2026-08-05",
                hour_index: 3,
                hour: "03:00",
                snapchat: 30,
                meta: 4,
                tiktok: 1,
                google: 2,
            },
            {
                date: "2026-08-05",
                hour_index: 0,
                hour: "00:00",
                snapchat: 10,
                meta: 2,
                tiktok: 0,
                google: 1,
            },
        ],
        source_only: true,
        provider_write_reached: true,
        accounting_write_reached: true,
        ...overrides,
    };
}


test("normalizes and sorts all four Riyadh hourly provider series", () => {
    const normalized = normalizeDashboardAdsSpend(fourPlatformPayload());

    expect(normalized.hourly_spend.map((row) => row.hour))
        .toEqual(["00:00", "03:00"]);
    expect(normalized.hourly_spend[0]).toMatchObject({
        snapchat: 10,
        meta: 2,
        tiktok: 0,
        google: 1,
    });
    expect(Object.keys(normalized.providers)).toEqual(DASHBOARD_ADS_PROVIDERS);
    expect(normalized.provider_totals_sar.google).toBe(7.5);
    expect(normalized.providers.tiktok.hourly_source)
        .toBe("make_daily_total_marker");
    expect(normalized.total_sar).toBe(132.5);
    expect(normalized.provider_write_reached).toBe(false);
    expect(normalized.accounting_write_reached).toBe(false);
});


test("retains the backwards-compatible hourly normalizer", () => {
    const normalized = normalizeDashboardAdsHourlySpend({
        date: "2026-08-05",
        hourly: fourPlatformPayload().hourly_spend,
    });

    expect(normalized.date).toBe("2026-08-05");
    expect(normalized.granularity).toBe("hour");
    expect(normalized.hourly[0].google).toBe(1);
});


test("refreshes through the shared client and returns four provider facts", async () => {
    loadDashboardPlatformSpend.mockResolvedValue(fourPlatformPayload());

    const result = await getDashboardAdsSpend({
        dateFrom: "2026-08-05",
        dateTo: "2026-08-05",
        refresh: true,
    });

    expect(loadDashboardPlatformSpend).toHaveBeenCalledWith({
        dateFrom: "2026-08-05",
        dateTo: "2026-08-05",
        refresh: true,
        maxAgeMs: 0,
    });
    expect(result.chart_granularity).toBe("hour");
    expect(result.hourly_spend[0].google).toBe(1);
    expect(result.refresh_error).toBe("");
});


test("falls back to saved shared facts when provider refresh cannot run", async () => {
    loadDashboardPlatformSpend
        .mockRejectedValueOnce(new Error("provider unavailable"))
        .mockResolvedValueOnce(fourPlatformPayload());

    const result = await getDashboardAdsSpend({
        dateFrom: "2026-08-05",
        dateTo: "2026-08-05",
        refresh: true,
    });

    expect(loadDashboardPlatformSpend).toHaveBeenNthCalledWith(2, {
        dateFrom: "2026-08-05",
        dateTo: "2026-08-05",
        refresh: false,
        maxAgeMs: 0,
    });
    expect(result.total_sar).toBe(132.5);
    expect(result.refresh_error).toContain("provider unavailable");
});


test("reads a multi-day four-platform series without forcing refresh", async () => {
    loadDashboardPlatformSpend.mockResolvedValue(fourPlatformPayload({
        date_from: "2026-08-04",
        date_to: "2026-08-05",
        chart_granularity: "day",
        hourly_spend: [],
        daily_spend: [
            { date: "2026-08-04", snapchat: 1, meta: 2, tiktok: 3, google: 4 },
            { date: "2026-08-05", snapchat: 5, meta: 6, tiktok: 7, google: 8 },
        ],
    }));

    const result = await getDashboardAdsSpend({
        dateFrom: "2026-08-04",
        dateTo: "2026-08-05",
        refresh: false,
    });

    expect(loadDashboardPlatformSpend).toHaveBeenCalledWith({
        dateFrom: "2026-08-04",
        dateTo: "2026-08-05",
        refresh: false,
        maxAgeMs: 0,
    });
    expect(result.chart_granularity).toBe("day");
    expect(result.daily_spend).toHaveLength(2);
    expect(result.daily_spend[1].google).toBe(8);
});


test("preserves incomplete Snapchat proof and null totals without inventing zero", () => {
    const payload = fourPlatformPayload();
    payload.provider_totals_sar.snapchat = null;
    payload.total_sar = null;
    payload.daily_spend[0].snapchat = 81.32;
    payload.hourly_spend[0].snapchat = null;
    payload.providers.snapchat = {
        ...payload.providers.snapchat, total_sar: null, data_quality: "incomplete",
        data_state: "unknown_incomplete", amount_available: false, amount_complete: false,
        coverage_complete: false, provisional: false,
        reason_codes: ["unified_projection_financial_reconciliation_or_fx_incomplete", { unsupported: true }],
    };
    payload.spend_quality = { status: "incomplete", amount_available: false, amount_complete: false, provisional: false };
    const normalized = normalizeDashboardAdsSpend(payload);
    expect(normalized.total_sar).toBeNull();
    expect(normalized.provider_totals_sar.snapchat).toBeNull();
    expect(normalized.daily_spend[0].snapchat).toBe(81.32);
    expect(normalized.hourly_spend.find((hour) => hour.hour_index === 3).snapchat).toBeNull();
    expect(normalized.providers.snapchat).toMatchObject({
        connected: true, data_quality: "incomplete", data_state: "unknown_incomplete",
        amount_available: false, amount_complete: false, coverage_complete: false, provisional: false,
        reason_codes: ["unified_projection_financial_reconciliation_or_fx_incomplete"],
    });
    expect(normalized.spend_quality).toEqual(payload.spend_quality);
    expect(normalized.provider_totals_sar.google).toBe(7.5);
});


test.each([81.32, 0])("preserves available provisional Snapchat amount %s and missing hourly detail", (amount) => {
    const payload = fourPlatformPayload();
    payload.provider_totals_sar.snapchat = amount;
    payload.total_sar = amount + 32.5;
    payload.providers.snapchat = {
        ...payload.providers.snapchat, total_sar: amount, data_quality: "provisional",
        data_state: amount > 0 ? "provisional_data" : "provisional_zero",
        amount_available: true, amount_complete: false, coverage_complete: false, provisional: true,
        reason_codes: ["open_riyadh_day_reconciliation_pending"],
    };
    payload.spend_quality = { status: "provisional", amount_available: true, amount_complete: false, provisional: true };
    payload.hourly_spend = [{ date: payload.date_from, hour_index: 0, hour: "00:00", snapchat: null }];
    const normalized = normalizeDashboardAdsSpend(payload);
    expect(normalized.provider_totals_sar.snapchat).toBe(amount);
    expect(normalized.providers.snapchat).toMatchObject({
        data_state: payload.providers.snapchat.data_state,
        amount_available: true, amount_complete: false, provisional: true,
    });
    expect(normalized.hourly_spend[0].snapchat).toBeNull();
    expect(normalized.spend_quality).toEqual(payload.spend_quality);
    expect(normalized.total_sar).toBe(amount + 32.5);
});


test.each([[81.32, 99.71], [0, 0], [null, null]])(
    "keeps explicit known Snapchat amount %s separate from the unavailable full total", (known, knownAll) => {
        const payload = fourPlatformPayload();
        payload.provider_totals_sar.snapchat = null;
        payload.total_sar = null;
        payload.known_total_sar = knownAll;
        payload.providers.snapchat = {
            ...payload.providers.snapchat, amount_available: false, data_quality: "incomplete",
            data_state: "unknown_incomplete", known_total_sar: known,
            known_account_count: known === null ? 0 : 1, incomplete_account_count: 1,
        };
        payload.hourly_spend[0].snapchat = null;
        const normalized = normalizeDashboardAdsSpend(payload);
        expect(normalized.providers.snapchat).toMatchObject({
            known_total_sar: known, known_account_count: known === null ? 0 : 1,
            incomplete_account_count: 1, amount_available: false,
        });
        expect(normalized.total_sar).toBeNull();
        expect(normalized.provider_totals_sar.snapchat).toBeNull();
        expect(normalized.known_total_sar).toBe(knownAll);
        expect(normalized.hourly_spend.find((hour) => hour.hour_index === 3).snapchat).toBeNull();
    },
);


test.each([false, true, { amount: 81.32 }, -1, NaN])("rejects unsupported known amount %p without making zero", (known) => {
    const payload = fourPlatformPayload();
    payload.known_total_sar = known;
    payload.providers.snapchat = { ...payload.providers.snapchat,
        known_total_sar: known, known_account_count: -1, incomplete_account_count: 1.5 };
    const normalized = normalizeDashboardAdsSpend(payload);
    expect(normalized.known_total_sar).toBeNull();
    expect(normalized.providers.snapchat.known_total_sar).toBeNull();
    expect(normalized.providers.snapchat.known_account_count).toBeNull();
    expect(normalized.providers.snapchat.incomplete_account_count).toBeNull();
});


function accountDisplayPayload() {
    const day = "2026-10-26";
    const point = (hour_index, spend_sar, status = "provisional") => ({ date: day, hour_index,
        hour: String(hour_index).padStart(2, "0") + ":00", spend_sar, status });
    const account = (account_id, currency, total_native, total_sar, available) => ({
        account_id, currency, display_name: account_id, timezone: currency === "USD" ? "America/Los_Angeles" : "Asia/Riyadh",
        projection_timezone: "Asia/Riyadh", total_native, total_sar,
        daily_sar: { [day]: total_sar }, daily_state: { [day]: total_sar === 0 ? "provisional_zero" : "provisional_data" },
        hourly_sar: { [day]: [point(0, total_sar), point(1, 0, "future")] },
        quality: { connected: true, status: available ? "provisional" : "incomplete", amount_available: available,
            amount_complete: false, complete: false, provisional: available, data_state: available ? "provisional_data" : "unknown_incomplete",
            observed_at: "2026-10-25T21:00:00Z", reason_codes: ["snapchat_operational_hour_observation"] },
    });
    return { ...fourPlatformPayload(), date_from: day, date_to: day,
        providers: { snapchat: { connected: true, accounts: [
            account("USD.A", "USD", 21.685333, 81.32, true),
            account("__proto__", "SAR", 0, 0, false),
        ] } } };
}

test("normalizes independent account evidence without rescuing an unknown account with zero", () => {
    const result = normalizeDashboardAdsSpend(accountDisplayPayload());
    const [usd, sar] = result.providers.snapchat.accounts;
    expect(usd.total_native).toBe(21.685333);
    expect(usd.total_sar).toBe(81.32);
    expect(usd.hourly_sar["2026-10-26"][0].spend_sar).toBe(81.32);
    expect(usd.hourly_sar["2026-10-26"][1].spend_sar).toBeNull();
    expect(usd.quality.observed_at).toBe("2026-10-25T21:00:00Z");
    expect(sar.account_id).toBe("__proto__");
    expect(sar.total_sar).toBeNull();
    expect(sar.total_native).toBeNull();
    expect(sar.hourly_sar["2026-10-26"][0].spend_sar).toBeNull();
    expect(sar.quality.amount_available).toBe(false);
});

test("preserves explicit empty accounts and real observed zero with future null", () => {
    const payload = accountDisplayPayload();
    const sar = payload.providers.snapchat.accounts[1];
    sar.quality = { ...sar.quality, amount_available: true, provisional: true, status: "provisional", data_state: "provisional_zero" };
    const normalized = normalizeDashboardAdsSpend(payload).providers.snapchat.accounts[1];
    expect(normalized.total_native).toBe(0);
    expect(normalized.total_sar).toBe(0);
    expect(normalized.hourly_sar["2026-10-26"].map((point) => point.spend_sar)).toEqual([0, null]);
    payload.providers.snapchat.accounts = [];
    expect(normalizeDashboardAdsSpend(payload).providers.snapchat.accounts).toEqual([]);
    delete payload.providers.snapchat.accounts;
    expect(normalizeDashboardAdsSpend(payload).providers.snapchat.accounts).toBeUndefined();
});

test("account IDs stay exact and duplicates or non-scalar metadata cannot invent native currency", () => {
    const payload = accountDisplayPayload();
    const first = payload.providers.snapchat.accounts[0];
    payload.providers.snapchat.accounts = [first, { ...first, account_id: "USD.B", display_name: {}, currency: null },
        first, { ...first, account_id: {} }];
    const accounts = normalizeDashboardAdsSpend(payload).providers.snapchat.accounts;
    expect(accounts.map((row) => row.account_id)).toEqual(["USD.A", "USD.B"]);
    expect(accounts[1].display_name).toBe("");
    expect(accounts[1].currency).toBeNull();
    expect(accounts[1].total_native).toBeNull();
    expect(accounts[1].total_sar).toBe(81.32);
});

test("rejects wrong projection windows, boolean amounts and duplicate hourly evidence", () => {
    const payload = accountDisplayPayload();
    const first = payload.providers.snapchat.accounts[0];
    first.projection_timezone = "UTC";
    expect(normalizeDashboardAdsSpend(payload).providers.snapchat.accounts[0].total_sar).toBeNull();
    first.projection_timezone = "Asia/Riyadh";
    first.total_sar = false;
    expect(normalizeDashboardAdsSpend(payload).providers.snapchat.accounts[0].quality.amount_available).toBe(false);
    first.total_sar = 81.32;
    first.hourly_sar["2026-10-26"].push({ ...first.hourly_sar["2026-10-26"][0], spend_sar: 1 });
    const points = normalizeDashboardAdsSpend(payload).providers.snapchat.accounts[0].hourly_sar["2026-10-26"];
    expect(points[0].spend_sar).toBeNull();
    expect(points[1].spend_sar).toBeNull();
});
