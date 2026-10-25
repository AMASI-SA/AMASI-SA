import React from "react";
import fs from "fs";
import path from "path";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";

import {
    AbandonedCartsCard,
    LatestOrders,
    ProfitCard,
    SummaryStrip,
    TopProductsCard,
    aggregateDashboardAdsHistoryByMonth,
    dashboardSpendDisplay,
    loadDashboardPeriodSnapshot,
} from "./AdvancedDashboard";

// Keep this source-focused suite independent of the lockfile-free install's
// newer conditional react-router/dom export, which Jest 27 cannot resolve.
jest.mock("react-router-dom", () => {
    const ReactModule = require("react");
    return {
        Link: ({ children, to, ...props }) => ReactModule.createElement(
            "a",
            { ...props, href: to },
            children,
        ),
        MemoryRouter: ({ children }) => ReactModule.createElement(
            ReactModule.Fragment,
            null,
            children,
        ),
    };
});

const source = fs.readFileSync(path.join(__dirname, "AdvancedDashboard.jsx"), "utf8");

test("latest orders mirrors Orders V2 and keeps each order's original currency", () => {
    const markup = renderToStaticMarkup(
        <MemoryRouter>
            <LatestOrders totals={{ total_orders: 2, total_sales: 337.33 }} orders={[
                {
                    order_number: "278106046",
                    created_at: new Date().toISOString(),
                    is_new: true,
                    status: "under_review",
                    customer: { name: "Rabea Ragheb", avatar_url: "https://cdn.example.com/customer.png" },
                    shipping: { address: { city: "Riyadh" } },
                    items: [{}, {}, {}],
                    payment: { method_native: "credit_card" },
                    source: { channel: "snapchat" },
                    totals: {
                        currency: "QAR",
                        total: 187.33,
                        total_sar: 192.79,
                        conversion_status: "verified",
                    },
                },
                {
                    order_number: "278105072",
                    created_at: new Date().toISOString(),
                    is_new: false,
                    status_native: "بانتظار المراجعة",
                    customer: { name: "هاجر البارقي" },
                    shipping: { address: { city: "الرياض" } },
                    items: [{}, {}],
                    payment: { method_native: "mada" },
                    totals: { total: 150 },
                },
            ]} />
        </MemoryRouter>
    );

    expect(markup).toContain("Rabea Ragheb");
    expect(markup).toContain("https://cdn.example.com/customer.png");
    expect(markup).toContain("بانتظار المراجعة");
    expect(markup).toContain("3 قطعة");
    expect(markup).toContain("credit_card");
    expect(markup).toContain("مصدر الطلب: سناب");
    expect(markup.match(/جديد/g)).toHaveLength(1);
    expect(markup).toContain("returnTo=%2Fdashboard-advanced");
    expect(markup).toContain("2 طلب");
    expect(markup).toContain("متوسط:");
    expect(markup).toContain("168.67 ر.س");
    expect(markup).toContain("187.33 QAR");
    expect(markup).toContain("≈ 192.79 ر.س");
    expect(markup).toContain("150.00 ر.س");
    expect(markup).not.toContain("187.33 ر.س");
});

test("summary strip includes current month order and sales cards", () => {
    const markup = renderToStaticMarkup(
        <MemoryRouter>
            <SummaryStrip
                data={{
                    totals: { total_orders: 5, total_sales: 500, avg_cost_per_order: 20, overall_roas: 3 },
                    product_cost_v2: { missing_products_count: 0 },
                    month_kpis: { total_orders: 903, total_sales: 169155 },
                    ads_v2: {
                        executive_breakdown: {
                            providers: {
                                snapchat: { platform_reported_orders: 4, platform_cost_per_order_sar: 25, spend_sar: 100, salla_orders: 2 },
                                tiktok: { platform_reported_orders: 2, platform_cost_per_order_sar: 20, spend_sar: 40, salla_orders: 4 },
                                meta: { platform_reported_orders: 1, platform_cost_per_order_sar: 30, spend_sar: 30, salla_orders: 3 },
                                google: { platform_reported_orders: null, platform_cost_per_order_sar: null, spend_sar: 0, salla_orders: 0 },
                            },
                        },
                    },
                }}
                filters={{ from: "2026-08-15", to: "2026-08-15" }}
            />
        </MemoryRouter>
    );

    expect(markup).toContain("طلبات الشهر");
    expect(markup).toContain("903");
    expect(markup).toContain("مبيعات الشهر");
    expect(markup).toContain("169,155.00 ر.س");
    expect(markup).toContain("سناب:");
    expect(markup).toContain(">2</b>");
    expect(markup).toContain("متوسط:");
    expect(markup).toContain("25.00 ر.س");
    expect(markup).not.toContain("متوسط قيمة سلة المشتريات");
});

test("abandoned carts show customer, product count, image, time and more control", () => {
    const carts = Array.from({ length: 6 }, (_, index) => ({
        cart_id: `cart-${index + 1}`,
        customer_name: index === 0 ? "نورة أحمد" : `عميل ${index + 1}`,
        total: 99 + index,
        currency: "SAR",
        activity_at: new Date().toISOString(),
        items: index === 0
            ? [{ quantity: 2, image_url: "https://cdn.example.com/product.png" }, { quantity: 1 }]
            : [{ quantity: 1 }],
    }));
    const markup = renderToStaticMarkup(<AbandonedCartsCard carts={carts} summary={{ abandoned_count: 24, recovered_count: 6 }} />);

    expect(markup).toContain("نورة أحمد");
    expect(markup).toContain("3 منتجات");
    expect(markup).toContain("https://cdn.example.com/product.png");
    expect(markup).toContain("منذ 1 ثانية");
    expect(markup).toContain("المزيد");
    expect(markup).toContain("متروكة 24");
    expect(markup).toContain("مكتملة 6");
    expect(markup).not.toContain("عميل 6");
    expect(markup).toContain("bg-slate-800");
    expect(markup).toContain("text-teal-700");
    expect(markup).not.toContain("bg-rose-700");
});

test("top products header shows period counts and only five rows initially", () => {
    const rows = Array.from({ length: 7 }, (_, index) => ({
        identity: `product-${index + 1}`,
        name: `منتج ${index + 1}`,
        units_sold: 10 - index,
        total_sales: 1000 - index,
        total_cost: (index + 1) * 10,
        net_profit: 900 - (index * 20),
        cost_status: "complete",
    }));
    const markup = renderToStaticMarkup(
        <TopProductsCard
            rows={rows}
            summary={{
                product_profit_summary: { product_count: 7 },
                salla_fallback_products_count: 3,
                missing_all_cost_products_count: 2,
            }}
        />
    );

    expect(markup).toContain("7 منتجًا خلال الفترة");
    expect(markup).toContain("بتكلفة سلة 3");
    expect(markup).toContain("تكلفة ناقصة 2");
    expect(markup).toContain("المزيد");
    expect(markup).toContain("إجمالي جميع المنتجات");
    expect(markup).toContain("يشمل المنتجات المخفية تحت المزيد");
    expect(markup).toContain("49");
    expect(markup).toContain("6,979.00 ر.س");
    expect(markup).toContain("280.00 ر.س");
    expect(markup).toContain("5,880.00 ر.س");
    expect(markup).not.toContain("منتج 1");
    expect(markup).toContain('data-testid="advanced-top-product-row-product-5"');
    expect(markup).not.toContain('data-testid="advanced-top-product-row-product-6"');
});

test("top products removes visible names and opens an unpriced product in a new Mezan tab", () => {
    const markup = renderToStaticMarkup(
        <TopProductsCard
            filters={{ from: "2026-08-01", to: "2026-08-25" }}
            rows={[{
                identity: "missing-product",
                name: "اسم يجب ألا يظهر",
                image_url: "https://cdn.example.com/missing-product.png",
                mezan_product_id: "m-17",
                salla_product_id: "s-17",
                catalog_product_found: true,
                units_sold: 3,
                total_sales: 300,
                total_cost: null,
                net_profit: null,
                cost_status: "missing",
            }]}
            summary={{ missing_all_cost_products_count: 1 }}
        />
    );

    expect(markup).not.toContain("اسم يجب ألا يظهر");
    expect(markup).toContain("https://cdn.example.com/missing-product.png");
    expect(markup).toContain("بدون تكلفة");
    expect(markup).toContain('target="_blank"');
    expect(markup).toContain('rel="noopener noreferrer"');
    expect(markup).toContain("/products-v2?");
    expect(markup).toContain("product=m-17");
    expect(markup).toContain("focus=cost");
    expect(markup).toContain("from=2026-08-01");
    expect(markup).toContain("to=2026-08-25");
    expect(markup).toContain("صافي الربح غير مكتمل");
});

test("top products shows partial option costs and uses the authoritative all-products total", () => {
    const markup = renderToStaticMarkup(
        <TopProductsCard
            rows={[
                {
                    identity: "partial-product",
                    units_sold: 12,
                    total_sales: 1312.25,
                    total_cost: 45,
                    net_profit: null,
                    cost_status: "missing",
                    cost_is_partial: true,
                },
                {
                    identity: "complete-product",
                    units_sold: 2,
                    total_sales: 200,
                    total_cost: 80,
                    net_profit: 120,
                    cost_status: "complete",
                },
            ]}
            summary={{
                product_profit_summary: {
                    product_count: 2,
                    total_cost: 619.4,
                    has_unpriced_products: true,
                },
                missing_all_cost_products_count: 1,
            }}
        />
    );

    expect(markup).toContain("45.00 ر.س");
    expect(markup).toContain("تكلفة جزئية");
    expect(markup).toContain("619.40 ر.س");
    expect(markup).toContain("تكلفة ناقصة 1");
    expect(markup).toContain("صافي الربح غير مكتمل");
});

test("top products count falls back to the rows instead of showing a stale zero", () => {
    const rows = Array.from({ length: 4 }, (_, index) => ({
        identity: `product-${index + 1}`,
        name: `منتج ${index + 1}`,
        units_sold: 4 - index,
        total_sales: 100 - index,
    }));
    const markup = renderToStaticMarkup(<TopProductsCard rows={rows} summary={{}} />);

    expect(markup).toContain("4 منتجًا خلال الفترة");
    expect(markup).not.toContain("0 منتجًا خلال الفترة");
});

test("initial dashboard loading never presents fake zero profit or empty products", () => {
    const profitMarkup = renderToStaticMarkup(<ProfitCard data={null} loading />);
    const productsMarkup = renderToStaticMarkup(<TopProductsCard rows={undefined} summary={{}} loading />);

    expect(profitMarkup).toContain("جارٍ مزامنة الفترة");
    expect(productsMarkup).toContain("جارٍ مزامنة المنتجات المباعة");
    expect(productsMarkup).not.toContain("لا توجد منتجات مباعة");
});

test("dashboard spend formatting keeps zero, no-data, and unknown distinct", () => {
    expect(dashboardSpendDisplay(0, "confirmed_zero")).toBe("0.00");
    expect(dashboardSpendDisplay(null, "confirmed_no_data")).toBe("لا توجد بيانات");
    expect(dashboardSpendDisplay(null, "unknown_incomplete")).toBe("غير مكتمل");
    expect(dashboardSpendDisplay(false, "confirmed_zero")).toBe("غير مكتمل");
    expect(dashboardSpendDisplay(10, "confirmed_no_data")).toBe("غير مكتمل");
    expect(dashboardSpendDisplay(42.16, "provisional_data")).toBe("42.16");
    expect(dashboardSpendDisplay(0, "provisional_zero")).toBe("0.00");
});

test("monthly advertising history never turns an unknown day into zero", () => {
    expect(aggregateDashboardAdsHistoryByMonth([
        { date: "2026-08-01", snapchat: 10, tiktok: 2, meta: 3, google: 4 },
        { date: "2026-08-02", snapchat: null, tiktok: 5, meta: 6, google: 7 },
    ])).toEqual([{
        label: "2026-08",
        snapchat: null,
        tiktok: 7,
        meta: 9,
        google: 11,
    }]);
});

test("profit summary exposes incomplete advertising instead of rendering zero", () => {
    const markup = renderToStaticMarkup(<ProfitCard data={{
        totals: {
            total_sales: 100,
            total_orders: 1,
            total_product_cost: 20,
            total_ads_cost: null,
            total_shipping_cost: 5,
            total_payment_fees: 2,
            operating_expenses_total: 3,
            net_profit: null,
            avg_cost_per_order: null,
            overall_roas: null,
            ads_spend_data_complete: false,
        },
        ads_v2: {
            spend_quality: {
                status: "incomplete",
                amount_complete: false,
                snapchat: { data_state: "unknown_incomplete" },
            },
        },
    }} />);
    const rowStart = markup.indexOf('data-testid="advanced-profit-row-ads"');
    const adsRow = markup.slice(rowStart, markup.indexOf("</button>", rowStart));

    expect(adsRow).toContain("غير مكتمل");
    expect(adsRow).not.toContain(">0.00<");
    expect(markup).toContain("بانتظار اكتمال بيانات الإعلانات");
});

test("profit summary still renders a provider-confirmed zero", () => {
    const markup = renderToStaticMarkup(<ProfitCard data={{
        totals: {
            total_sales: 100,
            total_orders: 1,
            total_product_cost: 20,
            total_ads_cost: 0,
            total_shipping_cost: 5,
            total_payment_fees: 2,
            operating_expenses_total: 3,
            net_profit: 70,
            avg_cost_per_order: 0,
            overall_roas: null,
            ads_spend_data_complete: true,
        },
        ads_v2: {
            spend_quality: {
                status: "complete",
                amount_complete: true,
                snapchat: { data_state: "confirmed_zero" },
            },
        },
    }} />);
    const rowStart = markup.indexOf('data-testid="advanced-profit-row-ads"');
    const adsRow = markup.slice(rowStart, markup.indexOf("</button>", rowStart));

    expect(adsRow).toContain(">0.00<");
    expect(adsRow).not.toContain("غير مكتمل");
});

test("profit summary displays open-day ad spend and profit as provisional", () => {
    const markup = renderToStaticMarkup(<ProfitCard data={{
        totals: {
            total_sales: 250,
            total_orders: 2,
            total_product_cost: 50,
            total_ads_cost: 42.16,
            total_shipping_cost: 20,
            total_payment_fees: 5,
            operating_expenses_total: 10,
            net_profit: 122.84,
            avg_cost_per_order: 21.08,
            overall_roas: 5.93,
            ads_spend_data_complete: false,
            ads_spend_amount_available: true,
            ads_spend_provisional: true,
        },
        ads_v2: {
            spend_quality: {
                status: "provisional",
                amount_complete: false,
                amount_available: true,
                provisional: true,
                snapchat: { data_state: "provisional_data" },
            },
        },
    }} />);
    const rowStart = markup.indexOf('data-testid="advanced-profit-row-ads"');
    const adsRow = markup.slice(rowStart, markup.indexOf("</button>", rowStart));

    expect(adsRow).toContain("إجمالي تكاليف الإعلانات (مؤقت)");
    expect(adsRow).toContain(">42.16<");
    expect(adsRow).not.toContain("غير مكتمل");
    expect(markup).toContain(">122.84<");
    expect(markup).toContain("تقديري حتى آخر مزامنة للإعلانات");
});

test("GA active-user bars stay inside their chart area", () => {
    expect(source).toContain('data-testid="advanced-ga-active-chart"');
    expect(source).toContain("overflow-hidden");
    expect(source).toContain("Number(m.active_users || 0) / minuteMax * 100");
    expect(source).toContain("Math.min(100");
});

test("profit summary keeps the four audited breakdowns in scrollable accordions", () => {
    expect(source).toContain('testid="advanced-profit-ads-details"');
    expect(source).toContain('testid="advanced-profit-shipping-details"');
    expect(source).toContain('testid="advanced-profit-payment-details"');
    expect(source).toContain('testid="advanced-profit-operating-details"');
    expect(source).toContain("max-h-72 overflow-auto");
    expect(source).toContain("aria-expanded={row.expandable ? expanded === row.key : undefined}");
});

test("profit rows keep labels on the right and render amount then currency then percentage", () => {
    const markup = renderToStaticMarkup(<ProfitCard data={{
        totals: {
            total_sales: 253.6,
            total_orders: 2,
            total_product_cost: 74,
            total_ads_cost: 411.48,
            total_shipping_cost: 37.25,
            total_payment_fees: 11.36,
            operating_expenses_total: 1436.19,
            net_profit: -1716.68,
            avg_cost_per_order: 205.74,
            overall_roas: 0.62,
            ads_spend_data_complete: true,
        },
        ads_v2: {
            spend_quality: {
                status: "complete",
                amount_complete: true,
                snapchat: { data_state: "confirmed_data" },
            },
        },
    }} />);

    const amountIndex = markup.indexOf(">411.48<");
    const currencyIndex = markup.indexOf(">ر.س<", amountIndex);
    const percentageIndex = markup.indexOf(">162.26%<", currencyIndex);
    expect(amountIndex).toBeGreaterThan(-1);
    expect(currencyIndex).toBeGreaterThan(amountIndex);
    expect(percentageIndex).toBeGreaterThan(currencyIndex);
    expect(markup).toContain("flex min-w-0 flex-1 items-center");
});

test("advanced ads card and profit card use the same selected-period platform spend", () => {
    expect(source).toContain("getDashboardAdsSpend");
    expect(source).toContain("mergeDashboardWithPlatformSpend");
    expect(source).not.toContain("chartData");
    expect(source).toContain("const daily = ads?.history || []");
    expect(source).toContain("const breakdown = ads?.breakdown || {}");
    expect(source).toContain('connectNulls={false}');
});

test("selected-period Google spend is included in executive ad cost and profit", async () => {
    let currentData = null;
    await loadDashboardPeriodSnapshot({
        next: { from: "2026-08-26", to: "2026-08-26" },
        requestSequence: 1,
        isLatest: () => true,
        apiClient: {
            get: jest.fn().mockResolvedValue({
                data: {
                    totals: {
                        total_sales: 3458.85,
                        total_orders: 16,
                        total_product_cost: 1000,
                        total_ads_cost: 723.93,
                        net_profit: 1500,
                        net_sales: 1734.92,
                    },
                    net_sales_config: { deduct_ads: true },
                    ads_v2: { executive_breakdown: { providers: {}, total: {} } },
                },
            }),
        },
        platformSpendLoader: jest.fn().mockResolvedValue({
            date_from: "2026-08-26",
            date_to: "2026-08-26",
            provider_totals_sar: {
                snapchat: 599.46,
                meta: 124.47,
                tiktok: 0,
                google: 34.60,
            },
            total_sar: 758.53,
            spend_quality: { status: "complete", amount_available: true, amount_complete: true },
        }),
        setData: (value) => { currentData = value; },
        setLoading: jest.fn(),
        setLoadError: jest.fn(),
        now: () => 1,
    });

    expect(currentData.totals.total_ads_cost).toBe(758.53);
    expect(currentData.totals.google_ads_spend).toBe(34.60);
    expect(currentData.totals.net_profit).toBe(1465.4);
    expect(currentData.ads_v2.executive_breakdown.providers.google.spend_sar).toBe(34.60);
    expect(currentData.ads_v2.executive_breakdown.total.spend_sar).toBe(758.53);
});

test("a stalled platform spend request never blocks the executive dashboard", async () => {
    let currentData = null;
    await loadDashboardPeriodSnapshot({
        next: { from: "2026-08-26", to: "2026-08-26" },
        requestSequence: 1,
        isLatest: () => true,
        apiClient: {
            get: jest.fn().mockResolvedValue({
                data: { totals: { total_ads_cost: 723.93 } },
            }),
        },
        platformSpendLoader: jest.fn(() => new Promise(() => {})),
        platformSpendTimeoutMs: 1,
        setData: (value) => { currentData = value; },
        setLoading: jest.fn(),
        setLoadError: jest.fn(),
        now: () => 1,
    });

    expect(currentData).toEqual({ totals: { total_ads_cost: 723.93 } });
});


test("manual refresh failure preserves the current verified dashboard snapshot", async () => {
    const verified = { snapshot_id: "verified-snapshot" };
    let currentData = verified;
    let currentError = null;
    const loadingStates = [];

    await loadDashboardPeriodSnapshot({
        next: { from: "2026-08-21", to: "2026-08-21" },
        requestSequence: 1,
        isLatest: (sequence) => sequence === 1,
        apiClient: { get: jest.fn().mockRejectedValue(new Error("network")) },
        setData: (value) => { currentData = value; },
        setLoading: (value) => { loadingStates.push(value); },
        setLoadError: (value) => { currentError = value; },
        now: () => 1,
    });

    expect(currentData).toBe(verified);
    expect(currentError).toBe("تعذر تحميل بيانات الفترة المحددة");
    expect(loadingStates).toEqual([true, false]);
    expect(source).not.toContain("setData(null)");
    expect(source).toContain("تم الاحتفاظ بآخر بيانات موثوقة");
});

test("a stale dashboard response never replaces the latest successful response", async () => {
    const requests = [];
    const apiClient = {
        get: jest.fn(() => new Promise((resolve) => requests.push(resolve))),
    };
    let latestSequence = 1;
    let currentData = { snapshot_id: "verified-snapshot" };
    const shared = {
        next: { from: "2026-08-21", to: "2026-08-21" },
        apiClient,
        isLatest: (sequence) => sequence === latestSequence,
        setData: (value) => { currentData = value; },
        setLoading: jest.fn(),
        setLoadError: jest.fn(),
        now: () => 1,
    };

    const staleRequest = loadDashboardPeriodSnapshot({ ...shared, requestSequence: 1 });
    latestSequence = 2;
    const latestRequest = loadDashboardPeriodSnapshot({ ...shared, requestSequence: 2 });
    requests[1]({ data: { snapshot_id: "latest-snapshot" } });
    await latestRequest;
    requests[0]({ data: { snapshot_id: "stale-snapshot" } });
    await staleRequest;

    expect(currentData).toEqual({ snapshot_id: "latest-snapshot" });
});


function currentProvisionalPlatform(snapchat = 307.11) {
    return { date_from: "2026-10-26", date_to: "2026-10-26", total_sar: snapchat + 18.39,
        provider_totals_sar: { snapchat, meta: 18.39, tiktok: 0, google: 0 },
        spend_quality: { status: "provisional", amount_available: true, amount_complete: false, provisional: true } };
}


function pendingAdDashboard() {
    return { totals: { total_sales: 1000, total_orders: 10, total_ads_cost: null, net_profit: null,
        net_sales: null, ads_spend_amount_available: false, ads_spend_data_complete: false },
        ads_v2: { total: null, spend_quality: { status: "incomplete", amount_available: false } } };
}


function recomputedProvisionalDashboard(snapchat = 307.11) {
    return { totals: { total_sales: 1000, total_orders: 10, total_product_cost: 100,
        total_ads_cost: snapchat + 18.39, net_profit: 600.23, net_sales: 650.23,
        total_payment_fees: 6.14, ads_spend_amount_available: true,
        ads_spend_data_complete: false, ads_spend_provisional: true },
        ads_v2: { total: snapchat + 18.39, spend_quality: currentProvisionalPlatform(snapchat).spend_quality } };
}


test.each([0, 307.11])("new full provisional spend %s triggers one same-filter canonical GET and preserves backend profit", async (amount) => {
    const pending = pendingAdDashboard();
    const recomputed = recomputedProvisionalDashboard(amount);
    const apiClient = { get: jest.fn(async (url) => ({ config: { method: "get", url },
        data: url.includes("spend-recompute") ? recomputed : pending })) };
    const platformSpendLoader = jest.fn().mockResolvedValue(currentProvisionalPlatform(amount));
    const setData = jest.fn();
    await loadDashboardPeriodSnapshot({
        next: { from: "2026-10-26", to: "2026-10-26", payment_methods: ["mada"], shipping_companies: ["carrier"] },
        requestSequence: 1, isLatest: () => true, apiClient, platformSpendLoader,
        setData, setLoading: jest.fn(), setLoadError: jest.fn(), now: () => 1,
    });
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    for (const [url] of apiClient.get.mock.calls) {
        const query = new URLSearchParams(url.split("?")[1]);
        expect(query.get("from_date")).toBe("2026-10-26");
        expect(query.get("to_date")).toBe("2026-10-26");
        expect(query.get("payment_methods")).toBe("mada");
        expect(query.get("shipping_companies")).toBe("carrier");
    }
    expect(platformSpendLoader).toHaveBeenCalledTimes(1);
    expect(platformSpendLoader).toHaveBeenCalledWith({ dateFrom: "2026-10-26", dateTo: "2026-10-26", refresh: false });
    const result = setData.mock.calls[0][0];
    expect(result.totals).toBe(recomputed.totals);
    expect(result.totals.net_profit).toBe(600.23);
    expect(result.totals.ads_spend_amount_available).toBe(true);
    expect(result.totals.ads_spend_data_complete).toBe(false);
    expect(result.ads_v2.spend_quality).toBe(recomputed.ads_v2.spend_quality);
    const markup = renderToStaticMarkup(<ProfitCard data={result} />);
    expect(markup).toContain("مؤقت");
    expect(markup).not.toContain("غير مكتمل");
    expect(markup).toContain("600.23");
});


test("known-only or wrong-range platform data never initiates canonical profit recomputation", async () => {
    for (const platform of [
        { ...currentProvisionalPlatform(), total_sar: null, known_total_sar: 307.11,
            spend_quality: { status: "incomplete", amount_available: false } },
        { ...currentProvisionalPlatform(), date_from: "2026-10-25", date_to: "2026-10-25" },
    ]) {
        const pending = pendingAdDashboard();
        const apiClient = { get: jest.fn(async (url) => ({ config: { url }, data: pending })) };
        const setData = jest.fn();
        await loadDashboardPeriodSnapshot({ next: { from: "2026-10-26", to: "2026-10-26" },
            requestSequence: 1, isLatest: () => true, apiClient,
            platformSpendLoader: jest.fn().mockResolvedValue(platform),
            setData, setLoading: jest.fn(), setLoadError: jest.fn(), now: () => 1 });
        expect(apiClient.get).toHaveBeenCalledTimes(1);
        expect(setData.mock.calls[0][0].totals).toBe(pending.totals);
        expect(setData.mock.calls[0][0].totals.net_profit).toBeNull();
    }
});


test("a late canonical recomputation cannot overwrite a newer period", async () => {
    let resolveRecomputed, entered;
    const recomputationEntered = new Promise((resolve) => { entered = resolve; });
    const apiClient = { get: jest.fn((url) => {
        if (!url.includes("spend-recompute")) return Promise.resolve({ config: { url }, data: pendingAdDashboard() });
        entered();
        return new Promise((resolve) => { resolveRecomputed = (data) => resolve({ config: { url }, data }); });
    }) };
    let latest = 1;
    const setData = jest.fn();
    const request = loadDashboardPeriodSnapshot({ next: { from: "2026-10-26", to: "2026-10-26" },
        requestSequence: 1, isLatest: (sequence) => sequence === latest, apiClient,
        platformSpendLoader: jest.fn().mockResolvedValue(currentProvisionalPlatform()),
        setData, setLoading: jest.fn(), setLoadError: jest.fn(), now: () => 1 });
    await recomputationEntered;
    latest = 2;
    resolveRecomputed(recomputedProvisionalDashboard());
    await request;
    expect(setData).not.toHaveBeenCalled();
    expect(apiClient.get).toHaveBeenCalledTimes(2);
});


test("broker-returned canonical data for another range is never applied", async () => {
    const setData = jest.fn();
    const apiClient = { get: jest.fn()
        .mockResolvedValueOnce({ config: { url: "/dashboard-v2?from_date=2026-10-26&to_date=2026-10-26" }, data: pendingAdDashboard() })
        .mockResolvedValueOnce({ config: { url: "/dashboard-v2?from_date=2026-10-27&to_date=2026-10-27" }, data: recomputedProvisionalDashboard() }) };
    await loadDashboardPeriodSnapshot({ next: { from: "2026-10-26", to: "2026-10-26" },
        requestSequence: 1, isLatest: () => true, apiClient,
        platformSpendLoader: jest.fn().mockResolvedValue(currentProvisionalPlatform()),
        setData, setLoading: jest.fn(), setLoadError: jest.fn(), now: () => 1 });
    expect(setData).not.toHaveBeenCalled();
    expect(apiClient.get).toHaveBeenCalledTimes(2);
});


test("still-incomplete canonical evidence stops after one recomputation and keeps profit null", async () => {
    const pending = pendingAdDashboard();
    const apiClient = { get: jest.fn(async (url) => ({ config: { url }, data: pending })) };
    const setData = jest.fn();
    await loadDashboardPeriodSnapshot({ next: { from: "2026-10-26", to: "2026-10-26" },
        requestSequence: 1, isLatest: () => true, apiClient,
        platformSpendLoader: jest.fn().mockResolvedValue(currentProvisionalPlatform()),
        setData, setLoading: jest.fn(), setLoadError: jest.fn(), now: () => 1 });
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(setData.mock.calls[0][0].totals.net_profit).toBeNull();
    expect(setData.mock.calls[0][0].totals.ads_spend_amount_available).toBe(false);
});


test("canonical provisional profit explains an observed no-activity account without claiming final reporting", () => {
    const data = recomputedProvisionalDashboard();
    data.ads_v2.spend_quality.snapchat = { reason_codes: ["snapchat_operational_no_activity_observed"] };
    const markup = renderToStaticMarkup(<ProfitCard data={data} />);
    expect(markup).toContain("advanced-profit-no-activity-hint");
    expect(markup).toContain("لم يُرصد نشاط في أحد الحسابات حتى آخر فحص. الأرقام أولية وقد تتأخر تقارير سناب.");
    expect(markup).toContain("600.23");
    expect(markup).toContain("مؤقت");
    expect(markup).not.toContain("غير مكتمل");
});


test.each(["unavailable", "missing_amount", "confirmed", "hour_only", "absent"])(
    "profit no-activity hint does not authorize %s financial data", (scenario) => {
        const data = scenario === "unavailable" ? pendingAdDashboard() : recomputedProvisionalDashboard();
        data.ads_v2.spend_quality.snapchat = { reason_codes: scenario === "hour_only"
            ? ["snapchat_operational_hour_observation"] : scenario === "absent" ? []
            : ["snapchat_operational_no_activity_observed"] };
        if (scenario === "missing_amount") data.totals.total_ads_cost = null;
        if (scenario === "confirmed") {
            Object.assign(data.totals, { ads_spend_data_complete: true, ads_spend_provisional: false });
            Object.assign(data.ads_v2.spend_quality, { status: "complete", amount_complete: true, provisional: false });
        }
        const profit = data.totals.net_profit;
        const available = data.totals.ads_spend_amount_available;
        const markup = renderToStaticMarkup(<ProfitCard data={data} />);
        expect(markup).not.toContain("advanced-profit-no-activity-hint");
        expect(data.totals.net_profit).toBe(profit);
        expect(data.totals.ads_spend_amount_available).toBe(available);
        if (scenario === "unavailable") expect(data.totals.net_profit).toBeNull();
    },
);


test("provisional operational Snapchat to complete spend uses one backend read without fabricating conversion denominators", async () => {
    const initial = recomputedProvisionalDashboard();
    initial.ads_v2.providers = { snapchat: { spend: 307.11, orders: null, revenue: null,
        impressions: null, clicks: null, cpa: null, roas: null, platform_reported_metrics_complete: false } };
    initial.ads_v2.executive_breakdown = { providers: { snapchat: { salla_orders: 3, salla_sales_sar: 500,
        platform_reported_orders: null, platform_cost_per_order_sar: null } },
        total: { platform_reported_orders: null }, coverage: { platform_cpa_denominator_complete: false } };
    const platform = { ...currentProvisionalPlatform(400),
        spend_quality: { status: "complete", amount_available: true, amount_complete: true, provisional: false } };
    const recomputed = { ...initial, totals: { ...initial.totals, total_ads_cost: 418.39,
        net_profit: 511.25, total_payment_fees: 7.66, ads_spend_data_complete: true, ads_spend_provisional: false },
        ads_v2: { ...initial.ads_v2, total: 418.39, spend_quality: platform.spend_quality } };
    const apiClient = { get: jest.fn(async (url) => ({ config: { url },
        data: url.includes("spend-recompute") ? recomputed : initial })) };
    const setData = jest.fn();
    const loader = jest.fn().mockResolvedValue(platform);
    await loadDashboardPeriodSnapshot({ next: { from: "2026-10-26", to: "2026-10-26" },
        requestSequence: 1, isLatest: () => true, apiClient, platformSpendLoader: loader,
        setData, setLoading: jest.fn(), setLoadError: jest.fn(), now: () => 1 });
    expect(apiClient.get).toHaveBeenCalledTimes(2);
    expect(loader).toHaveBeenCalledTimes(1);
    expect(loader).toHaveBeenCalledWith({ dateFrom: "2026-10-26", dateTo: "2026-10-26", refresh: false });
    const result = setData.mock.calls[0][0];
    expect(result.totals).toBe(recomputed.totals);
    expect(result.totals.net_profit).toBe(511.25);
    expect(result.totals.total_payment_fees).toBe(7.66);
    expect(result.ads_v2.spend_quality).toBe(platform.spend_quality);
    expect(result.ads_v2.providers.snapchat.orders).toBeNull();
    expect(result.ads_v2.providers.snapchat.cpa).toBeNull();
    expect(result.ads_v2.providers.snapchat.roas).toBeNull();
    expect(result.ads_v2.executive_breakdown.providers.snapchat.platform_reported_orders).toBeNull();
    expect(result.ads_v2.executive_breakdown.providers.snapchat.salla_orders).toBe(3);
    expect(result.ads_v2.executive_breakdown.providers.snapchat.salla_sales_sar).toBe(500);
    expect(result.ads_v2.executive_breakdown.coverage.platform_cpa_denominator_complete).toBe(false);
});
