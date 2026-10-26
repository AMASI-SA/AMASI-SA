import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";

import AdsExecutiveBreakdownTable, {
    buildAdsExecutiveMetricRows,
} from "./AdsExecutiveBreakdownTable";

const data = {
    providers: {
        snapchat: {
            spend_sar: 375.44,
            salla_sales_sar: 550,
            salla_orders: 2,
            platform_cost_per_order_sar: 37.54,
            actual_roas: 1.46,
        },
        tiktok: {
            spend_sar: 0,
            salla_sales_sar: 0,
            salla_orders: 0,
            platform_cost_per_order_sar: null,
            actual_roas: null,
        },
        meta: {
            spend_sar: 200,
            salla_sales_sar: 400,
            salla_orders: 1,
            platform_cost_per_order_sar: 50,
            actual_roas: 2,
        },
        google: {
            spend_sar: 0,
            salla_sales_sar: 0,
            salla_orders: 0,
            platform_cost_per_order_sar: null,
            actual_roas: null,
        },
    },
    total: {
        spend_sar: 575.44,
        salla_sales_sar: 950,
        salla_orders: 3,
        platform_cost_per_order_sar: 41.1,
        actual_roas: 1.65,
    },
    coverage: { salla_unattributed_orders: 1 },
};

test("calculates cost per order from platform spend and Salla orders", () => {
    const rows = buildAdsExecutiveMetricRows(data);

    expect(rows.find((row) => row.key === "orders")).toMatchObject({
        source: "سلة",
        total: 3,
    });
    expect(rows.find((row) => row.key === "sales")).toMatchObject({
        source: "سلة",
        total: 950,
    });
    expect(rows.find((row) => row.key === "cost_per_order")).toMatchObject({
        source: "صرف المنصة ÷ طلبات سلة",
        total: 575.44 / 3,
    });
    expect(rows.find((row) => row.key === "cost_per_order")?.values).toMatchObject({
        snapchat: 375.44 / 2,
        meta: 200,
        tiktok: null,
        google: null,
    });
});

test("ignores Snapchat platform CPA when it conflicts with Salla orders", () => {
    const rows = buildAdsExecutiveMetricRows({
        providers: {
            snapchat: {
                spend_sar: 1942.94,
                salla_orders: 15,
                platform_cost_per_order_sar: 323.82,
            },
        },
        total: { spend_sar: 1942.94, salla_orders: 15 },
    });
    const costPerOrder = rows.find((row) => row.key === "cost_per_order");

    expect(costPerOrder.values.snapchat).toBeCloseTo(129.53, 2);
    expect(costPerOrder.values.snapchat).not.toBe(323.82);
});

test("renders all approved metric rows", () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => root.render(<AdsExecutiveBreakdownTable data={data} />));

    expect(
        container.querySelector('[data-testid="ads-executive-breakdown-table"]'),
    ).not.toBeNull();
    expect(container.textContent).toContain("متوسط تكلفة الطلب");
    expect(container.textContent).toContain("المبيعات / العائد");
    expect(container.textContent).toContain("مبيعات سلة ÷ صرف المنصة");

    act(() => root.unmount());
    container.remove();
    globalThis.IS_REACT_ACT_ENVIRONMENT = false;
});


function accountExpenseView(accounts, inspect) {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
        act(() => root.render(<AdsExecutiveBreakdownTable data={data} snapchatAccounts={accounts} />));
        inspect(container);
    } finally {
        act(() => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
}

const accountExpenses = [
    { account_id: "USD.A", display_name: "US storefront", currency: "USD", total_native: 21.685333, total_sar: 81.32,
        quality: { amount_available: true, provisional: true, status: "provisional", observed_at: "2026-10-26T00:00:00Z" } },
    { account_id: "__proto__", display_name: "Saudi storefront", currency: "SAR", total_native: null, total_sar: null,
        quality: { amount_available: false, status: "incomplete" } },
];

test("separates account expenses and retains server totals and other platforms", () => {
    accountExpenseView(accountExpenses, (container) => {
        const accounts = container.querySelector('[data-testid="ads-executive-snapchat-account-table"]');
        expect(accounts.textContent).toContain("سناب الدولار");
        expect(accounts.textContent).toContain("سناب السعودي");
        expect(accounts.textContent).toContain("21.69 USD");
        expect(accounts.textContent).toContain("81.32 ر.س");
        expect(accounts.textContent).toContain("غير مكتمل");
        expect(accounts.textContent).not.toContain("المبيعات");
        expect(accounts.textContent).not.toContain("الطلبات");
        expect(accounts.querySelector("time").dateTime).toBe("2026-10-26T00:00:00Z");
        const providers = container.querySelector('[data-testid="ads-executive-breakdown-table"]');
        expect(providers.textContent).not.toContain("Snapchat");
        expect(providers.textContent).not.toContain("375.44");
        expect(providers.textContent).toContain("Meta");
        expect(providers.textContent).toContain("TikTok");
        expect(providers.textContent).toContain("Google Ads");
        expect(providers.textContent).toContain("575.44");
        expect(buildAdsExecutiveMetricRows(data).find((row) => row.key === "spend").total).toBe(575.44);
    });
});

test("renders observed account zero without copying platform commerce", () => {
    accountExpenseView([accountExpenses[0], { ...accountExpenses[1], total_native: 0, total_sar: 0,
        quality: { amount_available: true, provisional: true, status: "provisional" } }], (container) => {
        const row = container.querySelector('[data-testid="snapchat-expense-__proto__"]');
        expect(row.textContent).toContain("0.00 SAR");
        expect(row.textContent).toContain("0.00 ر.س");
        expect(row.textContent).toContain("أولي");
        expect(row.textContent).not.toContain("550");
    });
});

test.each([false, null, "", -1, Infinity, {}, []])("rejects invalid account expense %p", (total_sar) => {
    accountExpenseView([{ ...accountExpenses[0], total_sar }], (container) => {
        const row = container.querySelector('[data-testid="snapchat-expense-USD.A"]');
        expect(row.textContent).toContain("غير مكتمل");
        expect(row.textContent).not.toContain("21.69");
        expect(row.textContent).not.toContain("0.00 ر.س");
    });
});

test("unknown native currency and amount are not inferred from SAR", () => {
    accountExpenseView([{ ...accountExpenses[0], currency: null, total_native: null, display_name: { bad: "name" } }], (container) => {
        const row = container.querySelector('[data-testid="snapchat-expense-USD.A"]');
        expect(row.textContent).toContain("81.32 ر.س");
        expect(row.textContent).not.toContain("USD");
        expect(row.textContent).not.toContain("[object Object]");
        expect(row.textContent).toContain("—");
    });
});

test("empty accounts and waiting state suppress the aggregate Snapchat fallback", () => {
    for (const accounts of [[], null]) accountExpenseView(accounts, (container) => {
        expect(container.textContent).not.toContain("375.44");
        expect(container.querySelector('[data-testid="ads-executive-snapchat-account-state"]')).not.toBeNull();
    });
});

test("same-currency identities stay separate and repeated identities do not duplicate cost", () => {
    const second = { ...accountExpenses[0], account_id: "USD.B", display_name: "Second storefront", total_sar: 25 };
    accountExpenseView([accountExpenses[0], second, accountExpenses[0], { ...second, account_id: {} }], (container) => {
        const rows = container.querySelectorAll('[data-testid="ads-executive-snapchat-account-table"] tbody tr');
        expect(rows).toHaveLength(2);
        expect(rows[0].textContent).toContain("81.32");
        expect(rows[1].textContent).toContain("25.00");
    });
});
