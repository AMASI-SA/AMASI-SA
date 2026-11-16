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
            bank_commissions: { total_fee_sar: 0, fee_subject_spend_sar: 0, total_effective_spend_sar: 600,
                coverage: { complete: true, snapchat: { complete: true }, meta_tiktok: { complete: true } },
                accounts: [...accounts.map((row) => ({
                provider: "snapchat_ads", external_account_id: row.account_id,
                spend_sar: row.total_sar, spend_native: row.total_native, native_currency: row.currency,
                exchange_rate_to_sar: row.currency === "USD" ? 3.75 : 1,
                bank_commission_fee_sar: 0, bank_commission_pct: 0, apply_bank_commission: false,
            })), ...[["meta", 100], ["tiktok", 75]].map(([provider, spend]) => ({
                provider: `${provider}_ads`, external_account_id: provider,
                spend_sar: spend, spend_native: spend, native_currency: "SAR", exchange_rate_to_sar: 1,
                bank_commission_fee_sar: 0, bank_commission_pct: 0, apply_bank_commission: false,
            }))] },
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
                direct_result: { provider: "direct", ad_spend_applicable: false, ...salla(0, 0),
                    attribution_complete: true, time_assignment_complete: true, hover_summary: costs(0, 0, 0, 0) },
                coverage: { unassigned_orders: 0, outside_account_scope_orders: 0,
                    catalog_complete: true, status: "complete", reason_codes: [] },
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

test("null account mode uses the legacy Snapchat provider once while missing direct evidence keeps totals partial", () => {
    const input = fixture();
    input.snapchatAccounts = null;
    delete input.executiveSnapshot.ads_v2.account_results;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.size).toBe(0);
    expect(result.total).toMatchObject({ orders: null, sales: null, cpa: null,
        knownOrders: 78, knownSales: 102299, partial: true });
    expect(result.direct).toMatchObject({ orders: null, sales: null, adSpendApplicable: false });
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
    expect(result.total).toMatchObject({ orders: null, sales: null, cpa: null, knownOrders: 8, knownSales: 2300, partial: true });
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
    executiveSnapshot.ads_v2.bank_commissions.accounts.forEach((row) => {
        const account = input.snapchatAccounts.find((item) => item.account_id === row.external_account_id);
        row.spend_sar = account ? account.total_sar : spendSnapshot.provider_totals_sar[row.external_account_id];
        row.spend_native = account ? account.total_native : row.spend_sar;
    });
    executiveSnapshot.ads_v2.bank_commissions.total_effective_spend_sar = spendSnapshot.total_sar - spendSnapshot.provider_totals_sar.google;
    result.accounts[0].hover_summary = costs(7, 400, 20, 5);
    result.accounts[1].hover_summary = costs(2, 160, 20, 3);
    result.accounts.forEach((row) => { row.time_assignment_complete = true; });
    result.provider_results = {
        google: { provider: "google", ...salla(1, 200), attribution_complete: true, time_assignment_complete: true, hover_summary: costs(1, 80, 10, 2) },
        tiktok: { provider: "tiktok", ...salla(3, 900), attribution_complete: true, time_assignment_complete: true, hover_summary: costs(4, 300, 15, 4) },
        meta: { provider: "meta", ...salla(4, 1200), attribution_complete: true, time_assignment_complete: true, hover_summary: costs(6, 500, 20, 6) },
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
            direct_result: { ...segmentRow("direct", 0, 0, costs(0, 0, 0, 0)), ad_spend_applicable: false },
            coverage: { ...result.coverage, time_assignment_complete: true, unassigned_time_orders: 0 },
            reason_codes: [],
        };
    });
    return input;
}

test("daily hover excludes operating expenses and unverified fee fields in the order summary", () => {
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

function withBankFees(input, fees = { usd: [8.62, 2.3], sar: [1.15, 2.3] }) {
    const bank = input.executiveSnapshot.ads_v2.bank_commissions;
    bank.accounts.forEach((row) => {
        const fee = fees[row.external_account_id];
        if (fee) Object.assign(row, { bank_commission_fee_sar: fee[0], bank_commission_pct: fee[1],
            apply_bank_commission: true, configured: false });
    });
    bank.total_fee_sar = Number(bank.accounts.reduce((total, row) => total + row.bank_commission_fee_sar, 0).toFixed(2));
    bank.fee_subject_spend_sar = Number(bank.accounts.reduce((total, row) => total + (row.apply_bank_commission ? row.spend_sar : 0), 0).toFixed(2));
    input.executiveSnapshot.totals.ad_bank_commission_fees = bank.total_fee_sar;
    return input;
}

test("canonical bank charges enter each account and the total once without changing spend, CPA or ROAS", () => {
    const input = withBankFees(financialFixture());
    input.executiveSnapshot.ads_v2.account_results.accounts[0].hover_summary.ad_bank_fees_sar = 8888;
    input.executiveSnapshot.ads_v2.account_results.accounts[0].hover_summary.operating_expenses_sar = 9999;
    const before = JSON.parse(JSON.stringify(input));
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd")).toMatchObject({ cpa: 75, profit: {
        spendSar: 375, paymentFees: 5, bankFees: 8.62, totalCost: 433.62,
        netProfit: 691.38, roas: 4, complete: true,
    } });
    expect(result.accounts.get("sar").profit).toMatchObject({ bankFees: 1.15, totalCost: 184.15, netProfit: 265.85 });
    expect(result.total.profit).toMatchObject({ bankFees: 9.77, totalCost: 1554.77, netProfit: 2120.23 });
    expect(result.direct.profit).toMatchObject({ bankFees: 0, netProfit: 0 });
    expect(input).toEqual(before);
});

test.each(["missing", "null", "invalid", "duplicate", "coverage", "disabled charge", "zero rate", "missing rate"])(
    "unverified %s bank-fee evidence withholds costs and profit without changing proven ROAS", (fault) => {
        const input = withBankFees(financialFixture());
        const bank = input.executiveSnapshot.ads_v2.bank_commissions;
        if (fault === "missing") delete bank.accounts[0].bank_commission_fee_sar;
        if (fault === "null") bank.accounts[0].bank_commission_fee_sar = null;
        if (fault === "invalid") bank.accounts[0].bank_commission_fee_sar = Infinity;
        if (fault === "duplicate") bank.accounts.push({ ...bank.accounts[0] });
        if (fault === "coverage") bank.coverage.snapchat.complete = false;
        if (fault === "disabled charge") bank.accounts[0].apply_bank_commission = false;
        if (fault === "zero rate") bank.accounts[0].bank_commission_pct = 0;
        if (fault === "missing rate") delete bank.accounts[0].bank_commission_pct;
        const result = buildDashboardAdsTableMetrics(input);
        expect(result.accounts.get("usd")).toMatchObject({ cpa: 75, profit: {
            bankFees: null, totalCost: null, knownTotalCost: 425, netProfit: null, roas: 4, complete: false,
        } });
        expect(result.accounts.get("usd").profit.reasonCodes).toContain("ad_bank_fees_unverified");
        expect(result.total.profit.netProfit).toBeNull();
        expect(result.providers.google.profit.netProfit).toBe(83);
        expect(result.direct.profit.netProfit).toBe(0);
    },
);

test("missing advertising fee authority is unknown while explicit disabled charges and direct orders stay valid", () => {
    const input = financialFixture();
    expect(buildDashboardAdsTableMetrics(input).accounts.get("usd").profit).toMatchObject({ bankFees: 0, netProfit: 700 });
    delete input.executiveSnapshot.ads_v2.bank_commissions;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd").profit).toMatchObject({ bankFees: null, netProfit: null, roas: 4 });
    expect(result.direct.profit).toMatchObject({ bankFees: 0, netProfit: 0 });
});

test.each(["fee total", "executive fee", "spend total", "subject spend", "extra provider"])(
    "a contradictory canonical %s cannot certify the full cohort's profit", (fault) => {
        const input = withBankFees(allCohortFixture());
        const bank = input.executiveSnapshot.ads_v2.bank_commissions;
        if (fault === "fee total") bank.total_fee_sar += 1;
        if (fault === "executive fee") input.executiveSnapshot.totals.ad_bank_commission_fees += 1;
        if (fault === "spend total") bank.total_effective_spend_sar += 1;
        if (fault === "subject spend") bank.fee_subject_spend_sar += 1;
        if (fault === "extra provider") bank.accounts.push({ ...bank.accounts[0], provider: "other_ads" });
        const result = buildDashboardAdsTableMetrics(input);
        expect(result.total).toMatchObject({ orders: 19, sales: 5100,
            profit: { bankFees: null, totalCost: null, netProfit: null, complete: false } });
        expect(result.total.profit.roas).toBeCloseTo(5100 / 625, 10);
        result.total.segments.forEach((segment) => expect(segment.profit.netProfit).toBeNull());
    },
);

test("legacy totals and half-day totals cannot bypass the canonical bank-fee contract", () => {
    const input = withBankFees(financialFixture());
    input.executiveSnapshot.ads_v2.bank_commissions.total_fee_sar += 1;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.total.profit.netProfit).toBeNull();
    result.total.segments.forEach((segment) => expect(segment.profit.netProfit).toBeNull());
    expect(result.accounts.get("usd").profit.netProfit).toBe(691.38);
});

test("provider and legacy Snapchat rows use only their own authoritative account charges", () => {
    const input = withBankFees(financialFixture(), { usd: [8.62, 2.3], sar: [1.15, 2.3], meta: [1, 1], tiktok: [1.5, 2] });
    let result = buildDashboardAdsTableMetrics(input);
    expect(result.providers.meta.profit).toMatchObject({ bankFees: 1, netProfit: 573 });
    expect(result.providers.tiktok.profit).toMatchObject({ bankFees: 1.5, netProfit: 504.5 });
    expect(result.providers.meta.segments[0].profit.bankFees).toBe(1);
    expect(result.providers.tiktok.segments[1].profit.bankFees).toBe(1.5);
    expect(result.total.profit).toMatchObject({ bankFees: 12.27, netProfit: 2117.73 });
    const source = input.executiveSnapshot.ads_v2.account_results;
    source.provider_results.snapchat = { provider: "snapchat", ...salla(7, 2000),
        attribution_complete: true, time_assignment_complete: true, hover_summary: costs(9, 560, 40, 8) };
    input.executiveSnapshot.ads_v2.executive_breakdown.providers.snapchat = salla(7, 2000);
    input.snapchatAccounts = null;
    result = buildDashboardAdsTableMetrics(input);
    expect(result.providers.snapchat.profit).toMatchObject({ bankFees: 9.77, totalCost: 617.77, netProfit: 957.23 });
    expect(result.total.profit.bankFees).toBe(12.27);
});

test("multiple provider accounts retain daily charges without inventing a weighted half-day fee", () => {
    const input = withBankFees(financialFixture(), { meta: [0.6, 1] });
    const bank = input.executiveSnapshot.ads_v2.bank_commissions;
    const meta = bank.accounts.find((row) => row.provider === "meta_ads");
    Object.assign(meta, { spend_sar: 60, spend_native: 60 });
    bank.accounts.push({ ...meta, external_account_id: "meta-second", spend_sar: 40, spend_native: 40,
        bank_commission_pct: 3, bank_commission_fee_sar: 1.2 });
    bank.total_fee_sar = input.executiveSnapshot.totals.ad_bank_commission_fees = 1.8;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.providers.meta.profit).toMatchObject({ bankFees: 1.8, netProfit: 572.2 });
    expect(result.providers.meta.segments[0].profit).toMatchObject({ bankFees: null, totalCost: null, netProfit: null });
    expect(result.total.profit.bankFees).toBe(1.8);
    expect(result.total.segments[0].profit.netProfit).toBeNull();
});

test("half-day bank charges use verified account spend and preserve the server daily rounding", () => {
    const input = withBankFees(financialFixture());
    const result = buildDashboardAdsTableMetrics(input);
    const row = result.accounts.get("usd");
    expect(row.segments[0].profit).toMatchObject({ bankFees: 2.59, totalCost: 172.59, netProfit: 314.91 });
    expect(row.segments[1].profit).toMatchObject({ bankFees: 6.03, totalCost: 261.03, netProfit: 376.47 });
    expect(Number(row.segments.reduce((total, segment) => total + segment.profit.bankFees, 0).toFixed(2))).toBe(8.62);
    expect(result.total.segments[0].profit.bankFees).toBe(3.74);
    expect(result.total.segments[1].profit.bankFees).toBe(6.03);
    const early = buildDashboardAdsTableMetrics(withBankFees(financialFixture(10.5), { usd: [1.72, 2.3], sar: [0, 2.3] }));
    expect(early.accounts.get("usd").segments[0].profit.bankFees).toBe(1.72);
    expect(early.accounts.get("usd").segments[1]).toMatchObject({ status: "not_started", profit: { netProfit: null } });
});

test("an elapsed zero-spend afternoon receives no bank charge despite a daily midpoint rounding tie", () => {
    const input = withBankFees(financialFixture(24));
    input.snapchatAccounts[0].hourly_sar[DAY].forEach((point) => {
        point.spend_native = point.hour_index === 0 ? 100 : 0;
        point.spend_sar = point.hour_index === 0 ? 375 : 0;
    });
    const row = buildDashboardAdsTableMetrics(input).accounts.get("usd");
    expect(row.segments[0].profit).toMatchObject({ bankFees: 8.62, complete: true });
    expect(row.segments[1].profit).toMatchObject({ spendSar: 0, bankFees: 0, complete: true });
});

test("SAR half-day fees use original native precision without moving a rounded halala into zero-spend hours", () => {
    const input = financialFixture(24);
    const account = input.snapchatAccounts[1];
    account.hourly_sar[DAY].forEach((point) => {
        point.spend_native = point.hour_index < 12 ? 1.7949 : 0;
        point.spend_sar = point.hour_index < 12 ? 1.79 : 0;
    });
    Object.assign(account, { total_native: 21.5388, total_sar: 21.54 });
    input.spendSnapshot.provider_totals_sar.snapchat = input.executiveSnapshot.ads_v2.breakdown.snapchat = 396.54;
    input.spendSnapshot.total_sar = input.executiveSnapshot.totals.total_ads_cost = 596.54;
    const bank = input.executiveSnapshot.ads_v2.bank_commissions;
    Object.assign(bank.accounts[1], { spend_native: 21.5388, spend_sar: 21.54 });
    bank.total_effective_spend_sar = 571.54;
    withBankFees(input, { sar: [0.5, 2.3] });
    const row = buildDashboardAdsTableMetrics(input).accounts.get("sar");
    expect(row.segments[0].profit).toMatchObject({ bankFees: 0.5, complete: true });
    expect(row.segments[1].profit).toMatchObject({ spendSar: 0, bankFees: 0, complete: true });
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
    // This legacy precision-only fixture intentionally has no explicit FX proof.
    delete input.executiveSnapshot.ads_v2.bank_commissions.accounts[0].exchange_rate_to_sar;
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

test.each([false, true])("matching union totals preserve server gateway rounding (with bank fees: %s)", (chargeBankFees) => {
    const input = financialFixture();
    if (chargeBankFees) withBankFees(input);
    const result = input.executiveSnapshot.ads_v2.account_results;
    result.daily_total = { ...salla(15, 4300), attribution_complete: true, time_assignment_complete: true,
        hover_summary: costs(20, 1440, 85, 19.99) };
    result.segments[0].total = { ...salla(7, 1950), attribution_complete: true, time_assignment_complete: true,
        hover_summary: costs(9, 670, 43, 9.82) };
    const metrics = buildDashboardAdsTableMetrics(input);
    expect(metrics.total.profit).toMatchObject({ paymentFees: 19.99,
        totalCost: chargeBankFees ? 1554.76 : 1544.99, netProfit: chargeBankFees ? 2120.24 : 2130.01 });
    expect(metrics.total.segments[0].profit).toMatchObject({ paymentFees: 9.82,
        totalCost: chargeBankFees ? 726.56 : 722.82, netProfit: chargeBankFees ? 935.94 : 939.68 });
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

function allCohortFixture() {
    const input = financialFixture();
    const result = input.executiveSnapshot.ads_v2.account_results;
    const direct = (orders, sales, summary) => ({ provider: "direct", ad_spend_applicable: false,
        ...salla(orders, sales), attribution_complete: true, time_assignment_complete: true, hover_summary: summary });
    const total = (orders, sales, summary) => ({ cohort_basis: "executive_filtered_orders",
        ...salla(orders, sales), attribution_complete: true, time_assignment_complete: true, hover_summary: summary });
    result.direct_result = direct(4, 800, costs(5, 300, 25, 5));
    result.daily_total = total(19, 5100, costs(25, 1740, 110, 25));
    result.coverage.unrepresented_orders = 0;
    result.segments[0].direct_result = direct(1, 200, costs(1, 75, 5, 1));
    result.segments[1].direct_result = direct(3, 600, costs(4, 225, 20, 4));
    result.segments[0].total = total(8, 2150, costs(10, 745, 48, 10.83));
    result.segments[1].total = total(11, 2950, costs(15, 995, 62, 14.17));
    result.segments.forEach((segment) => { segment.coverage.unrepresented_orders = 0; });
    // The source cohorts, not these independent executive headline values,
    // govern account/direct counts and sales.
    input.executiveSnapshot.totals.total_orders = 999;
    input.executiveSnapshot.totals.total_sales = 99999;
    return input;
}

test("the reported daily reconciliation deducts both Snapchat bank charges without allocating unmatched orders or operating expenses", () => {
    const input = allCohortFixture();
    const { executiveSnapshot, spendSnapshot } = input;
    const source = executiveSnapshot.ads_v2.account_results;
    const bank = executiveSnapshot.ads_v2.bank_commissions;
    const day = "2026-11-13";
    input.fromDate = input.toDate = day;
    executiveSnapshot.range = { from_date: day, to_date: day };
    Object.assign(spendSnapshot, { date_from: day, date_to: day, total_sar: 32875.20,
        provider_totals_sar: { snapchat: 27607.19, google: 99.67, tiktok: 1100.81, meta: 4067.53 } });
    Object.assign(source, { date_from: day, date_to: day, segments: [] });
    Object.assign(executiveSnapshot.totals, { total_ads_cost: 32875.20, operating_expenses: 1894.92, net_profit: 23360.20 });
    executiveSnapshot.ads_v2.breakdown = { snapchat: 27607.19, google_transitional: 99.67, tiktok: 1100.81, meta: 4067.53 };
    const snap = { usd: [7232.89, 27155.16, 261, 71073.24], sar: [452.03, 452.03, 2, 492.66] };
    input.snapchatAccounts.forEach((row) => {
        [row.total_native, row.total_sar] = snap[row.account_id];
    });
    source.accounts.forEach((row) => Object.assign(row, salla(...snap[row.account_id].slice(2)), {
        attribution_complete: false, salla_orders: null, salla_sales_sar: null,
    }));
    bank.accounts.forEach((row) => {
        const account = input.snapchatAccounts.find((item) => item.account_id === row.external_account_id);
        row.spend_sar = account ? account.total_sar : spendSnapshot.provider_totals_sar[row.external_account_id];
        row.spend_native = account ? account.total_native : row.spend_sar;
        if (row.external_account_id === "usd") row.exchange_rate_to_sar = 3.7544;
    });
    bank.total_effective_spend_sar = 32775.53;
    Object.entries({ google: [3, 706.16], tiktok: [13, 3389.04], meta: [66, 19362.36] }).forEach(([provider, values]) => {
        Object.assign(source.provider_results[provider], salla(...values));
        Object.assign(executiveSnapshot.ads_v2.executive_breakdown.providers[provider], salla(...values));
    });
    Object.assign(source.direct_result, salla(46, 13769.45));
    Object.assign(source.coverage, { status: "partial", unassigned_orders: 2, unrepresented_orders: 2 });
    Object.assign(source.daily_total, salla(393, 109199.23), {
        hover_summary: { ...costs(393, 38190.95, 7458.75, 4784.24), operating_expenses_sar: 1894.92 },
    });
    withBankFees(input, { usd: [624.57, 2.3], sar: [10.40, 2.3] });
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.total).toMatchObject({ orders: 393, sales: 109199.23, profit: {
        spendSar: 32875.20, paymentFees: 4784.24, bankFees: 634.97,
        totalCost: 51068.91, netProfit: 25255.12, complete: true,
    } });
    expect(result.total.profit.netProfit - executiveSnapshot.totals.operating_expenses).toBeCloseTo(executiveSnapshot.totals.net_profit, 2);
    expect(result.unassignedOrders).toBe(2);
    expect(result.accounts.get("usd").profit).toMatchObject({ bankFees: 624.57, netProfit: null });
    expect(result.accounts.get("sar").profit).toMatchObject({ bankFees: 10.40, netProfit: null });
    expect(result.direct.profit.bankFees).toBe(0);
});

test("direct Salla outcomes and source cohort totals do not copy executive counts or count paid rows twice", () => {
    const input = allCohortFixture();
    const before = JSON.parse(JSON.stringify(input));
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.direct).toMatchObject({ orders: 4, sales: 800, cpa: null, adSpendApplicable: false,
        profit: { adSpendApplicable: false, spendSar: null, knownSpendSar: null, nativeSpend: null,
            totalCost: 330, netProfit: 470, roas: null, complete: true } });
    expect(result.direct.segments[0]).toMatchObject({ orders: 1, sales: 200, cpa: null,
        profit: { adSpendApplicable: false, spendSar: null, netProfit: 119, roas: null, complete: true } });
    expect(result.direct.segments[1].profit).toMatchObject({ netProfit: 351, provisional: false });
    expect(result.accounts.get("usd").orders).toBe(5);
    expect(result.providers.google.orders).toBe(1);
    expect(result.total).toMatchObject({ orders: 19, sales: 5100, partial: false,
        profit: { spendSar: 625, totalCost: 1875, netProfit: 2600, complete: true } });
    expect(result.total.cpa).toBeCloseTo(625 / 19, 10);
    expect(result.total.segments[0]).toMatchObject({ orders: 8, sales: 2150,
        profit: { spendSar: 287.5, netProfit: 1058.67, complete: true } });
    expect(result.total.segments[1].profit.netProfit).toBe(1541.33);
    expect(result.unrepresentedOrders).toBe(0);
    expect(input).toEqual(before);
});

test.each(["missing", "changed"])("direct profit is independent of %s advertising evidence while paid profits remain guarded", (fault) => {
    const input = allCohortFixture();
    if (fault === "missing") input.spendSnapshot = null;
    else input.executiveSnapshot.totals.total_ads_cost = 999;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.direct.profit).toMatchObject({ spendSar: null, nativeSpend: null, netProfit: 470, complete: true });
    expect(result.direct.segments[0].profit.netProfit).toBe(119);
    expect(result.accounts.get("usd").profit.netProfit).toBeNull();
    expect(result.total).toMatchObject({ orders: 19, sales: 5100, cpa: null, profit: { netProfit: null } });
});

test.each([false, true])("unrepresented Snapchat outcomes stay accurate while only proven outside scope blocks total finance (outside: %s)", (outside) => {
    const input = allCohortFixture();
    const source = input.executiveSnapshot.ads_v2.account_results;
    Object.assign(source.coverage, { status: "partial", unassigned_orders: outside ? 1 : 2,
        outside_account_scope_orders: outside ? 1 : 0, unrepresented_orders: 2 });
    source.accounts.forEach((row) => Object.assign(row, {
        attribution_complete: false, salla_orders: null, salla_sales_sar: null,
    }));
    Object.assign(source.daily_total, salla(21, 5400), { hover_summary: costs(27, 1780, 116, 27) });
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.get("usd")).toMatchObject({ orders: null, knownOrders: 5, cpa: null,
        partial: true, profit: { netProfit: null } });
    expect(result.direct).toMatchObject({ orders: 4, sales: 800, profit: { netProfit: 470 } });
    expect(result.total).toMatchObject({ orders: 21, sales: 5400, knownOrders: 21, partial: false,
        profit: { spendSar: 625, totalCost: 1923, netProfit: outside ? null : 2852, complete: !outside } });
    if (outside) {
        expect(result.total.cpa).toBeNull();
        expect(result.total.profit.roas).toBeNull();
    } else {
        expect(result.total.cpa).toBeCloseTo(625 / 21, 10);
        expect(result.total.profit.roas).toBeCloseTo(5400 / 625, 10);
    }
    expect(result.unassignedOrders).toBe(outside ? 1 : 2);
    expect(result.unrepresentedOrders).toBe(2);
});

test("an outside-scope afternoon order blocks daily and afternoon finance without hiding a sound morning", () => {
    const input = allCohortFixture();
    const source = input.executiveSnapshot.ads_v2.account_results;
    const afternoon = source.segments[1];
    [source.coverage, afternoon.coverage].forEach((coverage) => Object.assign(coverage, {
        outside_account_scope_orders: 1, unrepresented_orders: 1,
    }));
    Object.assign(source.daily_total, salla(20, 5200), { hover_summary: costs(26, 1760, 112, 26) });
    Object.assign(afternoon.total, salla(12, 3050), { hover_summary: costs(16, 1015, 64, 15.17) });
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.total).toMatchObject({ orders: 20, sales: 5200, cpa: null,
        profit: { spendSar: 625, totalCost: 1898, netProfit: null, roas: null } });
    expect(result.total.segments[0]).toMatchObject({ orders: 8, sales: 2150,
        profit: { netProfit: 1058.67, complete: true } });
    expect(result.total.segments[0].cpa).toBeCloseTo(287.5 / 8, 10);
    expect(result.total.segments[1]).toMatchObject({ orders: 12, sales: 3050, cpa: null,
        profit: { spendSar: 337.5, totalCost: 1094.17, netProfit: null, roas: null } });
    expect(result.accounts.get("usd").segments[1].profit.netProfit).toBe(382.5);
    expect(result.direct.profit.netProfit).toBe(470);
});

test("displaying a subset keeps source counts while blocking full-cohort financial ratios and profit", () => {
    const input = allCohortFixture();
    input.snapchatAccounts = [input.snapchatAccounts[0]];
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.accounts.has("sar")).toBe(false);
    expect(result.total).toMatchObject({ orders: 19, sales: 5100, cpa: null,
        profit: { netProfit: null, complete: false } });
    expect(result.total.profit.reasonCodes).toContain("displayed_account_scope_mismatch");
    expect(result.direct.profit.netProfit).toBe(470);
});

test.each(["count", "coverage", "coverage counts", "duplicate account", "provider identity", "direct applicability"])(
    "contradictory source %s cannot certify a complete cohort total", (fault) => {
        const input = allCohortFixture();
        const source = input.executiveSnapshot.ads_v2.account_results;
        if (fault === "count") source.daily_total.salla_orders = 20;
        if (fault === "coverage") delete source.coverage.unrepresented_orders;
        if (fault === "coverage counts") source.coverage.unassigned_orders = 1;
        if (fault === "duplicate account") source.accounts.push({ ...source.accounts[0] });
        if (fault === "provider identity") source.provider_results.google.provider = "meta";
        if (fault === "direct applicability") delete source.direct_result.ad_spend_applicable;
        const result = buildDashboardAdsTableMetrics(input);
        expect(result.total.orders).toBeNull();
        expect(result.total.sales).toBeNull();
        expect(result.total.cpa).toBeNull();
        expect(result.total.profit.netProfit).toBeNull();
        expect(result.total.profit.reasonCodes).toContain("cohort_total_unverified");
        if (fault === "direct applicability") {
            expect(result.direct.orders).toBeNull();
            expect(result.direct.profit.netProfit).toBeNull();
        }
    },
);

test("a missing direct cohort stays unknown instead of being inferred from executive minus paid orders", () => {
    const input = allCohortFixture();
    delete input.executiveSnapshot.ads_v2.account_results.direct_result;
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.direct).toMatchObject({ orders: null, sales: null, knownOrders: null,
        profit: { spendSar: null, netProfit: null, adSpendApplicable: false } });
    expect(result.total.orders).toBeNull();
    expect(result.total.knownOrders).toBe(15);
});

test("a stale account-result period cannot leak direct or complete cohort values", () => {
    const input = allCohortFixture();
    input.executiveSnapshot.ads_v2.account_results.date_from = "2026-11-14";
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.direct.orders).toBeNull();
    expect(result.direct.profit.netProfit).toBeNull();
    expect(result.direct.segments[0].profit.netProfit).toBeNull();
    expect(result.total.orders).toBeNull();
    expect(result.unrepresentedOrders).toBeNull();
});

test("unknown direct order time affects the halves while daily source counts and costs remain available", () => {
    const input = allCohortFixture();
    const segment = input.executiveSnapshot.ads_v2.account_results.segments[0];
    [segment.direct_result, segment.total].forEach((row) => Object.assign(row, {
        time_assignment_complete: false, salla_orders: null, salla_sales_sar: null,
    }));
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.direct.profit.netProfit).toBe(470);
    expect(result.total.orders).toBe(19);
    expect(result.direct.segments[0]).toMatchObject({ orders: null, knownOrders: 1, profit: { netProfit: null } });
    expect(result.total.segments[0]).toMatchObject({ orders: null, knownOrders: 8, profit: { netProfit: null } });
    expect(result.accounts.get("usd").segments[0].orders).toBe(2);
});

test("missing direct cost evidence withholds only its profit and keeps attributed counts", () => {
    const input = allCohortFixture();
    Object.assign(input.executiveSnapshot.ads_v2.account_results.direct_result.hover_summary, {
        shipping_cost_complete: false, shipping_cost_sar: null, total_cost_sar: null, complete: false,
    });
    const result = buildDashboardAdsTableMetrics(input);
    expect(result.direct).toMatchObject({ orders: 4, sales: 800,
        profit: { spendSar: null, shippingCost: null, knownShippingCost: 25, netProfit: null, complete: false } });
    expect(result.accounts.get("usd").profit.netProfit).toBe(700);
});

test("a proven empty direct cohort has zero outcomes and profit but no reported ad spend or ratio", () => {
    const result = buildDashboardAdsTableMetrics(financialFixture());
    expect(result.direct).toMatchObject({ orders: 0, sales: 0, cpa: null, partial: false,
        profit: { spendSar: null, nativeSpend: null, totalCost: 0, netProfit: 0, roas: null, complete: true } });
    expect(result.direct.segments[0].profit).toMatchObject({ spendSar: null, netProfit: 0, complete: true });
});

test("legacy warning metadata uses unassigned count without inventing a larger unrepresented count", () => {
    const input = fixture();
    input.executiveSnapshot.ads_v2.account_results.coverage.unassigned_orders = 3;
    expect(buildDashboardAdsTableMetrics(input).unrepresentedOrders).toBe(3);
});
