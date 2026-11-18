import { dashboardExecutiveProfitState } from "./dashboardExecutiveProfit";

function fixture() {
    const quality = { status: "complete", amount_available: true, amount_complete: true, provisional: false };
    return {
        fromDate: "2026-11-18",
        toDate: "2026-11-18",
        executiveSnapshot: {
            range: { from_date: "2026-11-18", to_date: "2026-11-18" },
            totals: {
                net_profit: 420.75, operating_expenses_total: 75.25, total_ads_cost: 200,
                product_cost_complete: true, sales_currency_conversion_complete: true,
                ads_spend_data_complete: true, ads_spend_amount_available: true, ads_spend_provisional: false,
            },
            currency_conversion: { conversion_complete: true },
            ads_v2: {
                spend_quality: { ...quality },
                breakdown: { snapchat: 100, meta: 60, tiktok: 30, google_transitional: 10 },
                bank_commissions: { accounts: [{ provider: "snapchat_ads", external_account_id: "synthetic-usd",
                    spend_sar: 100, spend_native: 20, native_currency: "USD" }] },
            },
        },
        spendSnapshot: {
            date_from: "2026-11-18", date_to: "2026-11-18", total_sar: 200,
            spend_quality: { ...quality },
            provider_totals_sar: { snapchat: 100, meta: 60, tiktok: 30, google: 10 },
            providers: { snapchat: { accounts: [{ account_id: "synthetic-usd", total_sar: 100,
                total_native: 20, currency: "USD" }] } },
        },
    };
}

test("returns the canonical final profit and operating expense without calculating or deducting again", () => {
    const input = fixture();
    input.executiveSnapshot.totals.ad_bank_commission_fees = 2.3;
    input.executiveSnapshot.totals.operating_expenses = 99999;
    const before = JSON.stringify(input);
    expect(dashboardExecutiveProfitState(input)).toMatchObject({
        ready: true, netProfit: 420.75, operatingExpenses: 75.25, provisional: false, reason: "ready",
    });
    expect(JSON.stringify(input)).toBe(before);
});

test.each([0, -24.08, "0", "-24.08"])("zero and negative canonical profit %s are valid", (value) => {
    const input = fixture();
    input.executiveSnapshot.totals.net_profit = value;
    expect(dashboardExecutiveProfitState(input)).toMatchObject({ ready: true, netProfit: Number(value), reason: "ready" });
});

test.each([null, undefined, true, false, "", "   ", "invalid", NaN, Infinity, [], [12], {}])(
    "invalid canonical profit %p never becomes zero", (value) => {
        const input = fixture();
        input.executiveSnapshot.totals.net_profit = value;
        expect(dashboardExecutiveProfitState(input)).toMatchObject({
            ready: false, netProfit: null, reason: "net_profit_unavailable",
        });
    },
);

test.each([null, undefined, true, false, "", "  ", -1, [], {}])(
    "invalid operating expense %p stays unavailable without replacing the authoritative net", (value) => {
        const input = fixture();
        input.executiveSnapshot.totals.operating_expenses_total = value;
        expect(dashboardExecutiveProfitState(input)).toMatchObject({ ready: true, netProfit: 420.75, operatingExpenses: null });
    },
);

test.each([0, "0", "75.25"])("valid operating expense %s is disclosed as supplied", (value) => {
    const input = fixture();
    input.executiveSnapshot.totals.operating_expenses_total = value;
    expect(dashboardExecutiveProfitState(input).operatingExpenses).toBe(Number(value));
});

test("partial account attribution does not suppress the complete canonical final net", () => {
    const input = fixture();
    input.executiveSnapshot.ads_v2.account_results = {
        coverage: { status: "partial", unassigned_orders: 2 },
        accounts: [{ account_id: "synthetic-usd", attribution_complete: false, hover_summary: { complete: false } }],
    };
    expect(dashboardExecutiveProfitState(input)).toMatchObject({ ready: true, netProfit: 420.75 });
});

test.each(["explicit", "legacy totals", "legacy product summary"])(
    "%s incomplete product cost hides even a stale numeric canonical net", (source) => {
        const input = fixture();
        if (source === "explicit") input.executiveSnapshot.totals.product_cost_complete = false;
        else {
            delete input.executiveSnapshot.totals.product_cost_complete;
            if (source === "legacy totals") input.executiveSnapshot.totals.incomplete_profit_orders_count = 1;
            else input.executiveSnapshot.product_cost_v2 = { incomplete_orders_count: 1 };
        }
        expect(dashboardExecutiveProfitState(input)).toMatchObject({
            ready: false, netProfit: null, productCostComplete: false, reason: "product_cost_incomplete",
        });
    },
);

test.each(["totals", "conversion"])("incomplete %s FX hides numeric profit", (source) => {
    const input = fixture();
    if (source === "totals") input.executiveSnapshot.totals.sales_currency_conversion_complete = false;
    else input.executiveSnapshot.currency_conversion.conversion_complete = false;
    expect(dashboardExecutiveProfitState(input)).toMatchObject({
        ready: false, netProfit: null, salesConversionComplete: false, reason: "sales_currency_incomplete",
    });
});

test.each(["different day", "different end", "missing range", "invalid selection"])(
    "%s cannot display profit or operating expense from an unverified period", (change) => {
        const input = fixture();
        if (change === "different day") input.executiveSnapshot.range.from_date = "2026-11-17";
        if (change === "different end") input.toDate = "2026-11-19";
        if (change === "missing range") delete input.executiveSnapshot.range;
        if (change === "invalid selection") input.fromDate = "not-a-date";
        expect(dashboardExecutiveProfitState(input)).toMatchObject({
            ready: false, netProfit: null, operatingExpenses: null, periodMatches: false,
            reason: "executive_period_pending",
        });
    },
);

test("an omitted end date uses the selected start date", () => {
    const input = fixture();
    delete input.toDate;
    expect(dashboardExecutiveProfitState(input)).toMatchObject({ ready: true, netProfit: 420.75, periodMatches: true });
});

test("legacy callers without date props retain the executive availability contract", () => {
    const input = fixture();
    delete input.fromDate;
    delete input.toDate;
    delete input.executiveSnapshot.range;
    expect(dashboardExecutiveProfitState(input)).toMatchObject({ ready: true, netProfit: 420.75 });
});

test.each(["null", "missing dates", "previous day", "different end"])(
    "explicit shared spend with %s cannot validate the selected period's final net", (change) => {
        const input = fixture();
        if (change === "null") input.spendSnapshot = null;
        if (change === "missing dates") {
            delete input.spendSnapshot.date_from;
            delete input.spendSnapshot.date_to;
        }
        if (change === "previous day") Object.assign(input.spendSnapshot, { date_from: "2026-11-17", date_to: "2026-11-17" });
        if (change === "different end") input.spendSnapshot.date_to = "2026-11-19";
        expect(dashboardExecutiveProfitState(input)).toMatchObject({
            ready: false, netProfit: null, operatingExpenses: 75.25, sharedSpendPeriodPending: true,
            reason: "spend_period_pending",
        });
    },
);

test.each(["total", "provider split", "native amount", "native currency"])(
    "changed %s spend evidence waits for the canonical executive calculation", (change) => {
        const input = fixture();
        if (change === "total") input.spendSnapshot.total_sar = 201;
        if (change === "provider split") Object.assign(input.spendSnapshot.provider_totals_sar, { snapchat: 101, meta: 59 });
        if (change === "native amount") input.spendSnapshot.providers.snapchat.accounts[0].total_native = 21;
        if (change === "native currency") input.spendSnapshot.providers.snapchat.accounts[0].currency = "SAR";
        expect(dashboardExecutiveProfitState(input)).toMatchObject({
            ready: false, netProfit: null, sharedAdsChanged: true, summarySpendPending: true,
            reason: "spend_basis_pending",
        });
    },
);

test("a current incomplete platform snapshot hides stale available executive profit", () => {
    const input = fixture();
    input.spendSnapshot.total_sar = null;
    input.spendSnapshot.known_total_sar = 100;
    input.spendSnapshot.spend_quality = { status: "incomplete", amount_available: false };
    expect(dashboardExecutiveProfitState(input)).toMatchObject({
        ready: false, netProfit: null, currentPlatformIncomplete: true, knownAdsTotal: 100,
        reason: "ads_spend_incomplete",
    });
});

test.each(["null total", "malformed total", "missing quality", "unknown quality", "contradictory incomplete", "unavailable complete"])(
    "explicit current shared spend with %s cannot validate stale canonical profit", (change) => {
        const input = fixture();
        if (change === "null total") input.spendSnapshot.total_sar = null;
        if (change === "malformed total") input.spendSnapshot.total_sar = "not-a-number";
        if (change === "missing quality") input.spendSnapshot.spend_quality = null;
        if (change === "unknown quality") input.spendSnapshot.spend_quality = { status: "unknown", amount_available: true };
        if (change === "contradictory incomplete") input.spendSnapshot.spend_quality = {
            status: "incomplete", amount_available: true, amount_complete: true,
        };
        if (change === "unavailable complete") input.spendSnapshot.spend_quality = {
            status: "complete", amount_available: false, amount_complete: false,
        };
        expect(dashboardExecutiveProfitState(input)).toMatchObject({
            ready: false, netProfit: null, operatingExpenses: 75.25,
            adsSpendAvailable: false, reason: "ads_spend_incomplete",
        });
    },
);

test("an explicitly available zero spend is valid current evidence", () => {
    const input = fixture();
    input.executiveSnapshot.totals.total_ads_cost = 0;
    input.executiveSnapshot.ads_v2.breakdown = { snapchat: 0, meta: 0, tiktok: 0, google_transitional: 0 };
    Object.assign(input.executiveSnapshot.ads_v2.bank_commissions.accounts[0], { spend_sar: 0, spend_native: 0 });
    input.spendSnapshot.total_sar = 0;
    input.spendSnapshot.provider_totals_sar = { snapchat: 0, meta: 0, tiktok: 0, google: 0 };
    Object.assign(input.spendSnapshot.providers.snapchat.accounts[0], { total_sar: 0, total_native: 0 });
    expect(dashboardExecutiveProfitState(input)).toMatchObject({ ready: true, netProfit: 420.75, sharedAdsTotal: 0 });
});

test("available provisional spend preserves the canonical net and its provisional label", () => {
    const input = fixture();
    const quality = { status: "provisional", amount_available: true, amount_complete: false, provisional: true };
    input.spendSnapshot.spend_quality = { ...quality };
    input.executiveSnapshot.ads_v2.spend_quality = { ...quality };
    Object.assign(input.executiveSnapshot.totals, { ads_spend_data_complete: false, ads_spend_provisional: true });
    expect(dashboardExecutiveProfitState(input)).toMatchObject({
        ready: true, netProfit: 420.75, provisional: true, adsSpendComplete: false, reason: "ready",
    });
});

test("a numeric net without canonical advertising availability is withheld", () => {
    const input = fixture();
    input.spendSnapshot = undefined;
    input.executiveSnapshot.ads_v2.spend_quality = { status: "incomplete", amount_available: false };
    Object.assign(input.executiveSnapshot.totals, { ads_spend_data_complete: false, ads_spend_amount_available: false });
    expect(dashboardExecutiveProfitState(input)).toMatchObject({ ready: false, netProfit: null, reason: "ads_spend_incomplete" });
});

test("undefined and explicitly supplied shared snapshots preserve the existing ProfitCard distinction", () => {
    const input = fixture();
    input.spendSnapshot.total_sar = 201;
    input.executiveSnapshot.dashboard_platform_spend = input.spendSnapshot;
    expect(dashboardExecutiveProfitState({ ...input, spendSnapshot: undefined })).toMatchObject({
        ready: true, netProfit: 420.75, sharedAdsTotal: null, sharedAdsChanged: false,
    });
    expect(dashboardExecutiveProfitState(input)).toMatchObject({ ready: false, netProfit: null, reason: "spend_basis_pending" });
});

test("missing executive data remains pending", () => {
    expect(dashboardExecutiveProfitState({ fromDate: "2026-11-18" })).toMatchObject({
        ready: false, netProfit: null, operatingExpenses: null, provisional: false, reason: "executive_pending",
    });
});
