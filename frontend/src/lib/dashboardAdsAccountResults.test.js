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
    expect(result.accounts.get("usd")).toMatchObject({ orders: 5, sales: 1500, cpa: 75,
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
    expect(buildDashboardAdsTableMetrics().total).toMatchObject(blank);
    const input = fixture();
    input.executiveSnapshot.range.from_date = "2026-11-14";
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.pending).toBe(true);
    expect(result.accounts.get("usd")).toMatchObject(blank);
    expect(result.providers.google).toMatchObject(blank);
    expect(result.total).toMatchObject(blank);
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
    expect(result.accounts.get("usd")).toMatchObject(blank);
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
    expect(result.accounts.get("usd")).toMatchObject({ orders: null, sales: null, cpa: null,
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
    expect(result.accounts.get("usd")).toMatchObject({ orders: 5, sales: null, cpa: 75,
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
    expect(result.accounts.get("usd")).toMatchObject({ orders: 0, sales: 0, cpa: null, knownOrders: 0, knownSales: 0, partial: false });
    expect(result.total).toMatchObject({ orders: 0, sales: 0, cpa: null, knownOrders: 0, knownSales: 0, partial: false });
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
    expect(result.accounts.get("usd")).toMatchObject({ ...blank, partial: true });
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
    expect(result.providers.google).toMatchObject({ ...blank, partial: true });
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

function costs(pieces, product, shipping, payment) {
    return {
        basis: "executive_order_costs_v1", currency: "SAR", pieces, known_pieces: pieces, pieces_complete: true,
        product_cost_sar: product, known_product_cost_sar: product, product_cost_complete: true,
        shipping_cost_sar: shipping, known_shipping_cost_sar: shipping, shipping_cost_complete: true,
        payment_fees_sar: payment, known_payment_fees_sar: payment, payment_fees_complete: true,
        total_cost_sar: Number((product + shipping + payment).toFixed(2)), complete: true,
        payment_fees_basis: "merchant_settings_cohort", reason_codes: [],
    };
}

function financialFixture(cutoffHour = 16.5) {
    const input = fixture();
    const { executiveSnapshot, spendSnapshot } = input;
    const result = executiveSnapshot.ads_v2.account_results;
    const midnight = Date.parse(`${DAY}T00:00:00+03:00`);
    const at = (hour) => new Date(midnight + hour * 3600000).toISOString();
    const hours = Array.from({ length: Math.ceil(cutoffHour) }, (_, hour_index) => ({
        date: DAY, hour_index, hour: `${String(hour_index).padStart(2, "0")}:00`,
    }));
    const nativeByHour = { 0: 20, 11: 10, 12: 50, 16: 20 };
    const sarByHour = { 0: 75, 11: 37.5, 12: 187.5, 16: 75 };
    spendSnapshot.providers.snapchat.accounts.forEach((account, index) => {
        account.hourly_sar = { [DAY]: hours.map((point) => ({ ...point, status: "confirmed_data",
            spend_sar: index === 0 ? sarByHour[point.hour_index] || 0 : point.hour_index === 11 ? 50 : 0,
            spend_native: index === 0 ? nativeByHour[point.hour_index] || 0 : point.hour_index === 11 ? 50 : 0,
        })) };
        account.total_sar = account.hourly_sar[DAY].reduce((total, point) => total + point.spend_sar, 0);
        account.total_native = account.hourly_sar[DAY].reduce((total, point) => total + point.spend_native, 0);
    });
    spendSnapshot.hourly_spend = hours.map((point) => ({ ...point,
        google: point.hour_index === 0 ? 25 : 0,
        tiktok: point.hour_index === 12 ? 75 : 0,
        meta: point.hour_index === 11 ? 100 : 0,
    }));
    ["google", "tiktok", "meta"].forEach((provider) => {
        spendSnapshot.providers[provider] = { hourly_available: true, hourly_source: "provider_native" };
        spendSnapshot.provider_totals_sar[provider] = spendSnapshot.hourly_spend.reduce((total, point) => total + point[provider], 0);
        executiveSnapshot.ads_v2.breakdown[provider === "google" ? "google_transitional" : provider] = spendSnapshot.provider_totals_sar[provider];
    });
    spendSnapshot.provider_totals_sar.snapchat = input.snapchatAccounts.reduce((total, account) => total + account.total_sar, 0);
    executiveSnapshot.ads_v2.breakdown.snapchat = spendSnapshot.provider_totals_sar.snapchat;
    spendSnapshot.total_sar = Object.values(spendSnapshot.provider_totals_sar).reduce((total, value) => total + value, 0);
    executiveSnapshot.totals.total_ads_cost = spendSnapshot.total_sar;
    executiveSnapshot.ads_v2.bank_commissions.accounts.forEach((row, index) => {
        row.spend_sar = input.snapchatAccounts[index].total_sar;
        row.spend_native = input.snapchatAccounts[index].total_native;
    });
    result.accounts[0].hover_summary = costs(7, 400, 20, 5);
    result.accounts[1].hover_summary = costs(2, 160, 20, 3);
    result.accounts.forEach((row) => { row.time_assignment_complete = true; });
    result.provider_results = {
        google: { ...salla(1, 200), attribution_complete: true, time_assignment_complete: true, hover_summary: costs(1, 80, 10, 2) },
        tiktok: { ...salla(3, 900), attribution_complete: true, time_assignment_complete: true, hover_summary: costs(4, 300, 15, 4) },
        meta: { ...salla(4, 1200), attribution_complete: true, time_assignment_complete: true, hover_summary: costs(6, 500, 20, 6) },
    };
    const segmentRow = (provider, orders, sales, summary, account_id) => ({ provider,
        ...(account_id ? { account_id } : {}), ...salla(orders, sales), hover_summary: summary,
        attribution_complete: true, time_assignment_complete: true });
    result.segments = ["morning", "afternoon"].map((id, index) => {
        const start = index * 12;
        const end = start + 12;
        return {
            id, date_from: DAY, date_to: DAY, timezone: "Asia/Riyadh", source_only: true,
            start_at: at(start), end_at: at(end), cutoff_at: at(Math.max(start, Math.min(end, cutoffHour))),
            status: cutoffHour <= start ? "not_started" : cutoffHour >= end ? "completed" : "active",
            accounts: [
                segmentRow("snapchat", index === 0 ? 2 : 3, index === 0 ? 600 : 900,
                    index === 0 ? costs(3, 160, 8, 2) : costs(4, 240, 12, 3), "usd"),
                segmentRow("snapchat", 1, 250, costs(1, 80, 10, 1.5), "sar"),
            ],
            provider_results: {
                google: segmentRow("google", index === 0 ? 1 : 0, index === 0 ? 200 : 0,
                    index === 0 ? costs(1, 80, 10, 2) : costs(0, 0, 0, 0)),
                tiktok: segmentRow("tiktok", index === 0 ? 1 : 2, index === 0 ? 300 : 600,
                    index === 0 ? costs(1, 100, 5, 1.33) : costs(3, 200, 10, 2.67)),
                meta: segmentRow("meta", 2, 600, costs(3, 250, 10, 3)),
            },
            coverage: { ...result.coverage, time_assignment_complete: true, unassigned_time_orders: 0 },
            reason_codes: [],
        };
    });
    return input;
}

test("daily hover uses the three costs and exact native total without operating or extra ad bank fees", () => {
    const input = financialFixture();
    const summary = input.executiveSnapshot.ads_v2.account_results.accounts[0].hover_summary;
    summary.operating_expenses_sar = 9999;
    summary.ad_bank_fees_sar = 8888;
    summary.operating_ratio = 0.9;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd").profit).toMatchObject({
        spendSar: 375, nativeSpend: 100, currency: "USD", pieces: 7,
        productCost: 400, shippingCost: 20, paymentFees: 5, totalCost: 425,
        netProfit: 700, roas: 4, complete: true, provisional: false,
    });
    expect(result.providers.google.profit).toMatchObject({ spendSar: 25, totalCost: 92, netProfit: 83 });
    expect(result.total.profit).toMatchObject({ spendSar: 625, totalCost: 1545, netProfit: 2130 });
});

test("noon belongs to afternoon; exact native hourly evidence supplies both halves without future hours", () => {
    const input = financialFixture();
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.segments.map((segment) => segment.id)).toEqual(["morning", "afternoon"]);
    expect(row.segments[0]).toMatchObject({ status: "completed", orders: 2, sales: 600, cpa: 56.25,
        profit: { spendSar: 112.5, nativeSpend: 30, totalCost: 170, netProfit: 317.5, complete: true, provisional: false } });
    expect(row.segments[1]).toMatchObject({ status: "active", orders: 3, sales: 900, cpa: 87.5,
        profit: { spendSar: 262.5, nativeSpend: 70, totalCost: 255, netProfit: 382.5, complete: true, provisional: true } });
    expect(input.snapchatAccounts[0].hourly_sar[DAY]).toHaveLength(17);
    expect(row.profit.netProfit).toBe(700);
});

test.each([10.5, 12])("before or exactly at noon (%s), afternoon is blank rather than a zero-cost forecast", (hour) => {
    const input = financialFixture(hour);
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.segments[1]).toMatchObject({ status: "not_started", orders: null, sales: null,
        knownOrders: null, knownSales: null, cpa: null, profit: { spendSar: null, nativeSpend: null, netProfit: null } });
    expect(row.segments[0].profit.complete).toBe(true);
    expect(row.segments[0].profit.spendSar).toBe(hour === 12 ? 112.5 : 75);
});

test("unknown order time affects its account halves only and preserves the daily summary", () => {
    const input = financialFixture();
    input.executiveSnapshot.ads_v2.account_results.segments.forEach((segment) => {
        segment.status = "partial";
        segment.coverage.time_assignment_complete = false;
        segment.coverage.unassigned_time_orders = 1;
        Object.assign(segment.accounts[0], { time_assignment_complete: false, salla_orders: null, salla_sales_sar: null });
    });
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd").profit.netProfit).toBe(700);
    expect(result.accounts.get("usd").segments[0]).toMatchObject({ status: "partial", orders: null, sales: null,
        knownOrders: 2, profit: { netProfit: null, complete: false } });
    expect(result.accounts.get("sar").segments[0]).toMatchObject({ status: "completed", orders: 1,
        profit: { netProfit: 108.5, complete: true } });
});

test("a missing observed hour cannot become zero or be filled from daily spend", () => {
    const input = financialFixture();
    input.snapchatAccounts[0].hourly_sar[DAY] = input.snapchatAccounts[0].hourly_sar[DAY].filter((point) => point.hour_index !== 7);
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.segments[0]).toMatchObject({ status: "partial", cpa: null,
        profit: { spendSar: null, knownSpendSar: 112.5, nativeSpend: null, netProfit: null } });
    expect(row.segments[1].profit.spendSar).toBeNull();
    expect(row.profit.netProfit).toBe(700);
});

test("daily TikTok total markers never become half-day spend even if their numeric sum matches", () => {
    const input = financialFixture();
    input.spendSnapshot.providers.tiktok.hourly_source = "make_daily_total_marker";
    const row = buildDashboardAdsTableMetrics(input).providers.tiktok;
    expect(row.profit.spendSar).toBe(75);
    expect(row.profit.netProfit).toBe(506);
    row.segments.forEach((segment) => {
        expect(segment.profit.spendSar).toBeNull();
        expect(segment.profit.netProfit).toBeNull();
        expect(segment.profit.reasonCodes).toContain("daily_marker_has_no_hourly_split");
    });
});

test.each(["snapchat", "tiktok"])("%s hourly/daily FX or subtotal mismatch blocks half-day financial amounts", (provider) => {
    const input = financialFixture();
    if (provider === "snapchat") input.snapchatAccounts[0].hourly_sar[DAY][0].spend_sar += 5;
    else input.spendSnapshot.hourly_spend[12].tiktok += 5;
    const result = buildDashboardAdsTableMetrics(input);
    const row = provider === "snapchat" ? result.accounts.get("usd") : result.providers.tiktok;
    expect(row.profit.complete).toBe(true);
    row.segments.forEach((segment) => {
        expect(segment.profit.spendSar).toBeNull();
        expect(segment.cpa).toBeNull();
        expect(segment.profit.netProfit).toBeNull();
        expect(segment.profit.reasonCodes).toContain("hourly_daily_spend_mismatch");
    });
});

function roundedFinancialFixture({ currency = "USD", rate = 3.75, native = 0.001,
    hourlySar = 0, dailySar = 0.01, includeRate = true } = {}) {
    const input = financialFixture(2.5);
    const { executiveSnapshot, spendSnapshot } = input;
    const account = input.snapchatAccounts[0];
    Object.assign(account, { currency, total_native: Number((3 * native).toFixed(6)), total_sar: dailySar });
    account.hourly_sar[DAY].forEach((point) => { point.spend_native = native; point.spend_sar = hourlySar; });
    Object.assign(executiveSnapshot.ads_v2.bank_commissions.accounts[0], {
        native_currency: currency, spend_native: account.total_native, spend_sar: dailySar,
        ...(includeRate ? { exchange_rate_to_sar: rate } : {}),
    });
    spendSnapshot.provider_totals_sar.snapchat = input.snapchatAccounts.reduce((total, row) => total + row.total_sar, 0);
    executiveSnapshot.ads_v2.breakdown.snapchat = spendSnapshot.provider_totals_sar.snapchat;
    spendSnapshot.total_sar = Object.values(spendSnapshot.provider_totals_sar).reduce((total, value) => total + value, 0);
    executiveSnapshot.totals.total_ads_cost = spendSnapshot.total_sar;
    return input;
}

test.each([true, false])("three rounded hourly native amounts retain financial results without allocating the halala (explicit rate: %s)", (includeRate) => {
    const input = roundedFinancialFixture({ includeRate });
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.profit).toMatchObject({ spendSar: 0.01, nativeSpend: 0.003, netProfit: 1074.99, complete: true });
    expect(row.segments[0]).toMatchObject({ cpa: 0, profit: {
        spendSar: 0, nativeSpend: 0.003, knownSpendSar: 0, netProfit: 430, complete: true,
    } });
    expect(row.segments[1].status).toBe("not_started");
    expect(input.snapchatAccounts[0].hourly_sar[DAY].map((point) => point.spend_sar)).toEqual([0, 0, 0]);
});

test("the fallback rounding allowance counts elapsed hours only, not all 24 hours", () => {
    const input = roundedFinancialFixture({ dailySar: 0.02, includeRate: false });
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.profit.complete).toBe(true);
    expect(row.segments[0].profit).toMatchObject({ spendSar: null, nativeSpend: null, netProfit: null });
    expect(row.segments[0].profit.reasonCodes).toContain("hourly_daily_spend_mismatch");
});

test("a matching hourly SAR sum cannot conceal a different per-hour FX basis", () => {
    const input = roundedFinancialFixture();
    input.snapchatAccounts[0].hourly_sar[DAY][0].spend_sar = 0.01;
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.profit.complete).toBe(true);
    expect(row.segments[0]).toMatchObject({ cpa: null,
        profit: { spendSar: null, knownSpendSar: 0.01, netProfit: null } });
    expect(row.segments[0].profit.reasonCodes).toContain("hourly_fx_basis_mismatch");
});

test("the canonical rate must also explain daily SAR within half a halala", () => {
    const input = roundedFinancialFixture({ dailySar: 0.02 });
    const segment = buildDashboardAdsTableMetrics(input).accounts.get("usd").segments[0];
    expect(segment.profit.spendSar).toBeNull();
    expect(segment.profit.netProfit).toBeNull();
    expect(segment.profit.reasonCodes).toContain("hourly_fx_basis_mismatch");
});

test("explicit native and rate evidence proves independent midpoint rounding without reallocating its remainder", () => {
    const input = roundedFinancialFixture({ native: 0.004, rate: 1.25, hourlySar: 0.01, dailySar: 0.01 });
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.profit).toMatchObject({ spendSar: 0.01, nativeSpend: 0.012 });
    expect(row.segments[0].profit).toMatchObject({ spendSar: 0.03, nativeSpend: 0.012,
        netProfit: 429.97, complete: true });
});

test("SAR account native precision is checked before the hourly display rounding", () => {
    const input = roundedFinancialFixture({ currency: "SAR", rate: 1, native: 0.003 });
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.profit.nativeSpend).toBe(0.009);
    expect(row.segments[0].profit).toMatchObject({ spendSar: 0, nativeSpend: 0.009,
        currency: "SAR", netProfit: 430, complete: true });
});

test("partial native evidence never invents USD with a daily FX ratio", () => {
    const input = financialFixture();
    delete input.snapchatAccounts[0].hourly_sar[DAY][4].spend_native;
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.profit.nativeSpend).toBe(100);
    expect(row.segments[0].profit).toMatchObject({ spendSar: 112.5, nativeSpend: null, netProfit: 317.5 });
    expect(row.segments[1].profit.nativeSpend).toBeNull();
});

test("a native headline mismatch also prevents a misleading SAR half-day profit", () => {
    const input = financialFixture();
    input.snapchatAccounts[0].hourly_sar[DAY][0].spend_native += 1;
    const segment = buildDashboardAdsTableMetrics(input).accounts.get("usd").segments[0];
    expect(segment.profit).toMatchObject({ spendSar: null, nativeSpend: null, netProfit: null });
    expect(segment.profit.reasonCodes).toContain("hourly_daily_spend_mismatch");
});

test("half-day raw native precision is retained until rendering", () => {
    const input = financialFixture();
    input.snapchatAccounts[0].hourly_sar[DAY][0].spend_native = 20.123456;
    input.snapchatAccounts[0].total_native = 100.123456;
    input.executiveSnapshot.ads_v2.bank_commissions.accounts[0].spend_native = 100.123456;
    expect(buildDashboardAdsTableMetrics(input).accounts.get("usd").segments[0].profit.nativeSpend).toBe(30.123456);
});

test("cost incompleteness retains known costs while withholding net profit", () => {
    const input = financialFixture();
    Object.assign(input.executiveSnapshot.ads_v2.account_results.accounts[0].hover_summary, {
        product_cost_complete: false, product_cost_sar: null, known_product_cost_sar: 300,
        total_cost_sar: null, complete: false,
    });
    const profit = buildDashboardAdsTableMetrics(input).accounts.get("usd").profit;
    expect(profit).toMatchObject({ productCost: null, knownProductCost: 300, shippingCost: 20,
        paymentFees: 5, totalCost: null, knownTotalCost: 325, netProfit: null, complete: false });
});

test("a changed canonical basis withholds both daily and half-day derived profits", () => {
    const input = financialFixture();
    input.executiveSnapshot.totals.total_ads_cost = 600;
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.profit.netProfit).toBeNull();
    expect(row.profit.roas).toBeNull();
    expect(row.segments[0].profit.netProfit).toBeNull();
    expect(row.segments[0].cpa).toBeNull();
});

test("negative net profit is preserved and no cost category outside the three is subtracted", () => {
    const input = financialFixture();
    input.executiveSnapshot.ads_v2.account_results.accounts[0].hover_summary = costs(7, 1400, 20, 5);
    expect(buildDashboardAdsTableMetrics(input).accounts.get("usd").profit.netProfit).toBe(-300);
});

test("multiple selected days retain range totals but require one day for half-day panels", () => {
    const input = financialFixture();
    input.toDate = "2026-11-16";
    input.executiveSnapshot.range.to_date = input.toDate;
    input.executiveSnapshot.ads_v2.account_results.date_to = input.toDate;
    input.spendSnapshot.date_to = input.toDate;
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.profit.netProfit).toBe(700);
    row.segments.forEach((segment) => {
        expect(segment.status).toBe("unavailable");
        expect(segment.reasonCodes).toEqual(["single_day_required"]);
        expect(segment.profit.netProfit).toBeNull();
    });
});

test("unverified or conflicting segment boundaries are not interpreted as calendar halves", () => {
    const input = financialFixture();
    input.executiveSnapshot.ads_v2.account_results.segments[0].start_at = `${DAY}T00:00:00`;
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.segments[0].status).toBe("unavailable");
    expect(row.segments[0].reasonCodes).toContain("segment_boundary_unverified");
    expect(row.segments[1].profit.netProfit).toBeNull();
});

test("completed half-days retain provisional source labeling", () => {
    const input = financialFixture();
    input.spendSnapshot.spend_quality = { status: "provisional", amount_available: true, provisional: true };
    input.executiveSnapshot.ads_v2.spend_quality.status = "provisional";
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.profit.provisional).toBe(true);
    expect(row.segments[0].profit.provisional).toBe(true);
    expect(row.segments[0].profit.netProfit).toBe(317.5);
});

test("matching union totals preserve server gateway rounding without redistributing a halala", () => {
    const input = financialFixture();
    const result = input.executiveSnapshot.ads_v2.account_results;
    result.daily_total = { ...salla(15, 4300), attribution_complete: true, time_assignment_complete: true,
        hover_summary: costs(20, 1440, 85, 19.99) };
    result.segments[0].total = { ...salla(7, 1950), attribution_complete: true, time_assignment_complete: true,
        hover_summary: costs(9, 670, 43, 9.82) };
    const metrics = buildDashboardAdsTableMetrics(input);
    expect(metrics.total.profit).toMatchObject({ paymentFees: 19.99, totalCost: 1544.99, netProfit: 2130.01 });
    expect(metrics.total.segments[0].profit).toMatchObject({ paymentFees: 9.82, totalCost: 722.82, netProfit: 939.68 });
    expect(metrics.accounts.get("usd").profit.paymentFees).toBe(5);
    expect(metrics.accounts.get("sar").profit.paymentFees).toBe(3);
    expect(metrics.accounts.get("usd").segments[0].profit.paymentFees).toBe(2);
});

test("a subset cannot borrow the full cohort's totals even if a forged aggregate repeats its counts", () => {
    const input = financialFixture();
    input.snapchatAccounts = [input.snapchatAccounts[0]];
    input.executiveSnapshot.ads_v2.account_results.daily_total = {
        ...salla(13, 3800), attribution_complete: true, time_assignment_complete: true,
        hover_summary: costs(20, 1440, 85, 19.99),
    };
    const total = buildDashboardAdsTableMetrics(input).total;
    expect(total.profit).toMatchObject({ spendSar: 575, productCost: 1280, shippingCost: 65,
        paymentFees: 17, totalCost: 1362, netProfit: 1863 });
});

test("contradictory union metrics do not replace valid row sales with a fabricated total profit", () => {
    const input = financialFixture();
    input.executiveSnapshot.ads_v2.account_results.daily_total = {
        ...salla(999, 99999), attribution_complete: true, time_assignment_complete: true,
        hover_summary: costs(20, 1440, 85, 19.99),
    };
    const total = buildDashboardAdsTableMetrics(input).total;
    expect(total).toMatchObject({ orders: 15, sales: 4300 });
    expect(total.profit.netProfit).toBeNull();
    expect(total.profit.reasonCodes).toContain("row_metrics_mismatch");
});
