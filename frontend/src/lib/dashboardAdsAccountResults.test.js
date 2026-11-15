import { buildDashboardAdsTableMetrics } from "./dashboardAdsAccountResults";

const DAY = "2026-11-15";
const salla = (salla_orders, salla_sales_sar) => ({
    salla_orders, salla_sales_sar, known_salla_orders: salla_orders,
    known_salla_sales_sar: salla_sales_sar, sales_currency_conversion_complete: true,
});

function fixture() {
    const accounts = [
        { account_id: "usd", currency: "USD", total_native: 100, total_sar: 375 },
        { account_id: "sar", currency: "SAR", total_native: 50, total_sar: 50 },
    ].map((row) => ({ ...row, display_name: "اسم مشترك", projection_timezone: "Asia/Riyadh",
        quality: { amount_available: true, status: "complete", connected: true } }));
    const spendSnapshot = {
        date_from: DAY, date_to: DAY, timezone: "Asia/Riyadh", total_sar: 625,
        provider_totals_sar: { snapchat: 425, google: 25, tiktok: 75, meta: 100 },
        spend_quality: { status: "complete", amount_available: true, amount_complete: true },
        providers: { snapchat: { accounts }, google: {}, tiktok: {}, meta: {} },
    };
    const executiveSnapshot = {
        range: { from_date: DAY, to_date: DAY }, totals: { total_ads_cost: 625 },
        ads_v2: {
            breakdown: { snapchat: 425, google_transitional: 25, tiktok: 75, meta: 100 },
            spend_quality: { status: "complete" },
            bank_commissions: { accounts: accounts.map((row) => ({
                provider: "snapchat_ads", external_account_id: row.account_id,
                spend_sar: row.total_sar, spend_native: row.total_native, native_currency: row.currency,
            })) },
            executive_breakdown: {
                providers: {
                    snapchat: salla(70, 99999), google: salla(1, 200),
                    tiktok: salla(3, 900), meta: salla(4, 1200),
                },
                total: salla(999, 999999),
            },
            account_results: {
                date_from: DAY, date_to: DAY, timezone: "Asia/Riyadh", source_only: true,
                accounts: [
                    { provider: "snapchat", account_id: "usd", ...salla(5, 1500), attribution_complete: true },
                    { provider: "snapchat", account_id: "sar", ...salla(2, 500), attribution_complete: true },
                ],
                coverage: { unassigned_orders: 0, catalog_complete: true, status: "complete", reason_codes: [] },
            },
        },
    };
    return { executiveSnapshot, spendSnapshot, fromDate: DAY, toDate: DAY, snapchatAccounts: accounts };
}

const blank = { orders: null, sales: null, cpa: null, knownOrders: null, knownSales: null, partial: false };

test("joins independent account identities and uses SAR for CPA and weighted displayed totals", () => {
    const input = fixture();
    input.executiveSnapshot.ads_v2.account_results.accounts.reverse();
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts).toBeInstanceOf(Map);
    expect(result.accounts.get("usd")).toEqual({ orders: 5, sales: 1500, cpa: 75,
        knownOrders: 5, knownSales: 1500, partial: false });
    expect(result.accounts.get("sar")).toMatchObject({ orders: 2, sales: 500, cpa: 25 });
    expect(result.providers.google).toMatchObject({ orders: 1, sales: 200, cpa: 25 });
    expect(result.total).toMatchObject({ orders: 15, sales: 4300, knownOrders: 15, knownSales: 4300, partial: false });
    expect(result.total.cpa).toBeCloseTo(625 / 15, 10);
    expect(result.total.cpa).not.toBe(35);
    expect(result.pending).toBe(false);
    expect(result.unassignedOrders).toBe(0);
});

test("null account mode uses the legacy Snapchat provider once without requiring account results", () => {
    const input = fixture();
    input.snapchatAccounts = null;
    delete input.executiveSnapshot.ads_v2.account_results;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.size).toBe(0);
    expect(result.total.orders).toBe(78);
    expect(result.total.sales).toBe(102299);
    expect(result.total.cpa).toBeCloseTo(625 / 78, 10);
    expect(result.pending).toBe(false);
});

test("missing or previous-period executive snapshots produce pending nulls, never zeros", () => {
    expect(buildDashboardAdsTableMetrics().total).toEqual(blank);
    const input = fixture();
    input.executiveSnapshot.range.from_date = "2026-11-14";
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.pending).toBe(true);
    expect(result.accounts.get("usd")).toEqual(blank);
    expect(result.providers.google).toEqual(blank);
    expect(result.total).toEqual(blank);
    expect(result.unassignedOrders).toBeNull();
});

test.each([
    ["date_from", "2026-11-14"], ["date_to", null],
    ["timezone", "America/Los_Angeles"], ["source_only", false], ["accounts", {}],
])("invalid account result %s cannot leak account metrics while provider facts remain known", (key, value) => {
    const input = fixture();
    input.executiveSnapshot.ads_v2.account_results[key] = value;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.pending).toBe(true);
    expect(result.accounts.get("usd")).toEqual(blank);
    expect(result.providers.google.orders).toBe(1);
    expect(result.total).toMatchObject({ orders: null, sales: null, cpa: null,
        knownOrders: 8, knownSales: 2300, partial: true });
    expect(result.unassignedOrders).toBeNull();
});

test("a prior-period spend snapshot hides CPA without erasing current Salla sales or orders", () => {
    const input = fixture();
    input.spendSnapshot.date_from = "2026-11-14";
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd")).toMatchObject({ orders: 5, sales: 1500, cpa: null });
    expect(result.providers.google.cpa).toBeNull();
    expect(result.total).toMatchObject({ orders: 15, sales: 4300, cpa: null });
});

test.each(["partial", "unavailable"])("%s account attribution keeps confirmed subtotals and excludes unassigned orders", (status) => {
    const input = fixture();
    const results = input.executiveSnapshot.ads_v2.account_results;
    results.coverage = { status, unassigned_orders: 3, catalog_complete: status !== "unavailable" };
    results.accounts.forEach((row) => { row.attribution_complete = false; row.salla_orders = null; row.salla_sales_sar = null; });
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd")).toEqual({ orders: null, sales: null, cpa: null,
        knownOrders: 5, knownSales: 1500, partial: true });
    expect(result.total).toMatchObject({ orders: null, sales: null, cpa: null,
        knownOrders: 15, knownSales: 4300, partial: true });
    expect(result.unassignedOrders).toBe(3);
    expect(result.pending).toBe(false);
});

test("incomplete catalog coverage cannot promote contradictory canonical account values", () => {
    const input = fixture();
    input.executiveSnapshot.ads_v2.account_results.coverage.catalog_complete = false;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd")).toMatchObject({ orders: null, sales: null, cpa: null, knownOrders: 5, partial: true });
});

test("unknown sales conversion retains a known order count and subtotal without fabricating complete sales", () => {
    const input = fixture();
    Object.assign(input.executiveSnapshot.ads_v2.account_results.accounts[0], {
        sales_currency_conversion_complete: false, salla_sales_sar: null, known_salla_sales_sar: 1000,
    });
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd")).toEqual({ orders: 5, sales: null, cpa: 75,
        knownOrders: 5, knownSales: 1000, partial: true });
    expect(result.total).toMatchObject({ orders: 15, sales: null, cpa: null, knownSales: 3800, partial: true });
});

test("a changed account basis blocks CPA even when portfolio and provider spend totals are unchanged", () => {
    const input = fixture();
    input.spendSnapshot.providers.snapchat.accounts[0].total_sar = 350;
    input.spendSnapshot.providers.snapchat.accounts[1].total_sar = 75;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.pending).toBe(false);
    expect(result.accounts.get("usd")).toMatchObject({ orders: 5, sales: 1500, cpa: null });
    expect(result.providers.meta.cpa).toBeNull();
    expect(result.total.cpa).toBeNull();
});

test("confirmed zero spend with positive orders has zero CPA; zero orders have no CPA", () => {
    const input = fixture();
    input.spendSnapshot.total_sar = 0;
    input.executiveSnapshot.totals.total_ads_cost = 0;
    Object.keys(input.spendSnapshot.provider_totals_sar).forEach((key) => { input.spendSnapshot.provider_totals_sar[key] = 0; });
    Object.keys(input.executiveSnapshot.ads_v2.breakdown).forEach((key) => { input.executiveSnapshot.ads_v2.breakdown[key] = 0; });
    input.snapchatAccounts.forEach((row) => { row.total_sar = 0; row.total_native = 0; });
    input.executiveSnapshot.ads_v2.bank_commissions.accounts.forEach((row) => { row.spend_sar = 0; row.spend_native = 0; });
    let result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd").cpa).toBe(0);
    expect(result.total.cpa).toBe(0);
    input.executiveSnapshot.ads_v2.account_results.accounts.forEach((row) => Object.assign(row, salla(0, 0)));
    Object.values(input.executiveSnapshot.ads_v2.executive_breakdown.providers).forEach((row) => Object.assign(row, salla(0, 0)));
    result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd")).toEqual({ orders: 0, sales: 0, cpa: null, knownOrders: 0, knownSales: 0, partial: false });
    expect(result.total).toEqual({ orders: 0, sales: 0, cpa: null, knownOrders: 0, knownSales: 0, partial: false });
});

test.each([null, undefined, "", " ", true, [], {}, Infinity, -1, 1.5, Number.MAX_SAFE_INTEGER + 1])(
    "invalid order count %p does not become a usable zero or denominator", (invalid) => {
        const input = fixture();
        Object.assign(input.executiveSnapshot.ads_v2.executive_breakdown.providers.google, {
            salla_orders: invalid, known_salla_orders: invalid,
        });
        const row = buildDashboardAdsTableMetrics(input).providers.google;
        expect(row.orders).toBeNull();
        expect(row.knownOrders).toBeNull();
        expect(row.cpa).toBeNull();
        expect(row.partial).toBe(true);
    },
);

test.each([null, " ", true, [], {}, Infinity, -1, Number.MAX_SAFE_INTEGER + 1])(
    "invalid sales %p remain unknown rather than coerced", (invalid) => {
        const input = fixture();
        Object.assign(input.executiveSnapshot.ads_v2.executive_breakdown.providers.google, {
            salla_sales_sar: invalid, known_salla_sales_sar: invalid,
        });
        const row = buildDashboardAdsTableMetrics(input).providers.google;
        expect(row.sales).toBeNull();
        expect(row.knownSales).toBeNull();
    },
);

test("explicit numeric strings are accepted without weakening integer validation", () => {
    const input = fixture();
    Object.assign(input.executiveSnapshot.ads_v2.executive_breakdown.providers.google, {
        salla_orders: "2", salla_sales_sar: "120.50",
    });
    expect(buildDashboardAdsTableMetrics(input).providers.google).toMatchObject({ orders: 2, sales: 120.5, cpa: 12.5 });
});

test("display identities deduplicate strictly and hostile object keys remain ordinary Map keys", () => {
    const input = fixture();
    input.snapchatAccounts[0].account_id = "__proto__";
    input.executiveSnapshot.ads_v2.account_results.accounts[0].account_id = "__proto__";
    input.executiveSnapshot.ads_v2.bank_commissions.accounts[0].external_account_id = "__proto__";
    input.snapchatAccounts = [...input.snapchatAccounts, input.snapchatAccounts[0],
        null, [], { account_id: {} }, { account_id: " " }, { account_id: 42 }];
    input.executiveSnapshot.ads_v2.account_results.accounts.push({
        provider: "meta", account_id: "__proto__", ...salla(1000, 100000), attribution_complete: true,
    });
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.size).toBe(2);
    expect(result.accounts.get("__proto__").orders).toBe(5);
    expect(result.total.orders).toBe(15);
    expect(Object.prototype.salla_orders).toBeUndefined();
});

test("conflicting duplicate account results fail closed instead of summing or selecting one", () => {
    const input = fixture();
    input.executiveSnapshot.ads_v2.account_results.accounts.push({
        provider: "snapchat", account_id: "usd", ...salla(999, 999999), attribution_complete: true,
    });
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd")).toEqual({ ...blank, partial: true });
    expect(result.total.orders).toBeNull();
    expect(result.total.knownOrders).toBe(10);
});

test("unshown account results and outside-scope orders do not enter displayed totals", () => {
    const input = fixture();
    input.snapchatAccounts = [input.snapchatAccounts[0]];
    input.executiveSnapshot.ads_v2.account_results.coverage.outside_account_scope_orders = 20;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.has("sar")).toBe(false);
    expect(result.total).toMatchObject({ orders: 13, sales: 3800, knownOrders: 13, partial: false });
    expect(result.total.cpa).toBeCloseTo(575 / 13, 10);
});

test("an explicit empty display list excludes combined Snapchat metrics and needs no account read", () => {
    const input = fixture();
    input.snapchatAccounts = [];
    delete input.executiveSnapshot.ads_v2.account_results;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.size).toBe(0);
    expect(result.total).toMatchObject({ orders: 8, sales: 2300, cpa: 25, partial: false });
    expect(result.pending).toBe(false);
});

test("missing provider facts and unsafe aggregate counts cannot claim complete totals", () => {
    const input = fixture();
    delete input.executiveSnapshot.ads_v2.executive_breakdown.providers.google;
    let result = buildDashboardAdsTableMetrics(input);
    expect(result.providers.google).toEqual({ ...blank, partial: true });
    expect(result.total).toMatchObject({ orders: null, sales: null, cpa: null, knownOrders: 14, partial: true });
    input.executiveSnapshot.ads_v2.account_results.accounts.forEach((row) => {
        row.salla_orders = Number.MAX_SAFE_INTEGER;
        row.known_salla_orders = Number.MAX_SAFE_INTEGER;
    });
    result = buildDashboardAdsTableMetrics(input);
    expect(result.total.orders).toBeNull();
    expect(result.total.knownOrders).toBeNull();
    expect(result.total.cpa).toBeNull();
});

test("malformed canonical spending evidence hides CPA without throwing or changing source objects", () => {
    const input = fixture();
    input.executiveSnapshot.ads_v2.bank_commissions.accounts = [null];
    const before = JSON.stringify(input);
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd")).toMatchObject({ orders: 5, sales: 1500, cpa: null });
    expect(JSON.stringify(input)).toBe(before);
});
