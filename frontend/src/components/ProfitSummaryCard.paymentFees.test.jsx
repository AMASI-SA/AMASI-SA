import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";

jest.mock("react-router-dom", () => ({
    Link: ({ children }) => children,
}));

import ProfitSummaryCard, { buildPaymentFeeRows } from "./ProfitSummaryCard";
import { financialCohortCompleteness } from "../lib/paymentFeeCompleteness";

test("deferred history explicitly blocks certified profit without inventing a fee", () => {
    const state = financialCohortCompleteness({}, {
        complete: false, review_reason_counts: { awaiting_payment_history_deferred: 2 },
    });
    expect(state.complete).toBe(false);
    expect(state.hint).toContain("الأرصدة التشغيلية");
    expect(state.hint).toContain("صافي الربح غير مؤكد");
    expect(state.hint).not.toContain("عمولة");
});

const breakdown = [
    {
        key: "tamara",
        name: "تمارا",
        total_sales: 1500,
        fee_amount: 110,
        orders_count: 5,
        commission_percent: 6.99,
        fixed_fee: 1.5,
        vat_percent: 15,
    },
    {
        key: "ad_bank_commissions",
        name: "عمولات الحسابات الإعلانية",
        total_sales: 3754.4,
        fee_amount: 86.35,
        sub_methods: [{
            key: "ad_bank:snap",
            display: "Snapchat — أماسي الرياض",
            parent_name: "عمولات الحسابات الإعلانية",
            kind: "ad_bank_commission",
            native_currency: "USD",
            exchange_rate_to_sar: 3.7544,
            spend_native: 1000,
            total_sales: 3754.4,
            commission_percent: 2.3,
            fee_amount: 86.35,
            apply_bank_commission: true,
        }],
    },
];

const weeklySource = {
    provider: "tabby", native_currency: "SAR", basis: "weekly_statement_average",
    window_start: "2026-11-09", window_end: "2026-11-15",
    rate_percent: 5.9, fixed_amount_per_sale: 1.5, vat_rate_percent: 15,
    eligible_sale_rows: 35, stale_source: false,
};
const operationalSource = {
    provider: "tabby", native_currency: "SAR", basis: "operational_balance_average",
    period_start: "2026-10-01T00:00:00Z", period_end: "2026-11-22T00:00:00Z",
    source_as_of: "2026-10-31", source_date_basis: "statement_date", accepted_at: "2026-11-01T10:00:00Z",
    rate_percent: "7.57473800", fixed_amount_per_sale: "1.00000000", vat_rate_percent: "15.00000000",
    eligible_sale_rows: 100,
};
const weeklyRow = {
    key: "tabby", name: "تابي", orders_count: 5, total_sales: 1000, fee_amount: 40,
    fees_estimated: true, estimated_orders_count: 2,
    fee_sources: ["statement", "weekly_statement_average"], fee_estimate_sources: [weeklySource],
};

let container;
let root;

beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(() => {
    act(() => root.unmount());
    container.remove();
    globalThis.IS_REACT_ACT_ENVIRONMENT = false;
});

test("normalizes payment methods and ad-account bank commissions", () => {
    const rows = buildPaymentFeeRows(breakdown);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatchObject({ name: "تمارا", feeAmount: 110 });
    expect(rows[1]).toMatchObject({
        name: "Snapchat — أماسي الرياض",
        kind: "ad_bank_commission",
        baseAmount: 3754.4,
        commissionPercent: 2.3,
        feeAmount: 86.35,
    });
});

test("opens the fee table inside the executive profit summary", () => {
    act(() => {
        root.render(
            <ProfitSummaryCard
                totals={{
                    total_sales: 10000,
                    total_product_cost: 1000,
                    total_ads_cost: 3754.4,
                    total_shipping_cost: 300,
                    total_payment_fees: 196.35,
                    tamara_fees: 110,
                    ad_bank_commission_fees: 86.35,
                    net_profit: 4749.25,
                    total_orders: 10,
                }}
                paymentBreakdown={breakdown}
            />,
        );
    });

    const trigger = container.querySelector('[data-testid="profit-line-payment-fees"]');
    expect(trigger).not.toBeNull();

    act(() => trigger.dispatchEvent(new MouseEvent("click", { bubbles: true })));

    expect(container.querySelector('[data-testid="payment-fees-tooltip-content"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="payment-fees-breakdown-table"]')).not.toBeNull();
    expect(container.textContent).toContain("تمارا");
    expect(container.textContent).toContain("Snapchat — أماسي الرياض");
    expect(container.textContent).toContain("86.35");
});

test.each([null, 850])("incomplete fee authority suppresses fallback or stale numeric profit %s", (netProfit) => {
    act(() => root.render(<ProfitSummaryCard totals={{
        total_sales: 1000, total_product_cost: 100, other_payment_fees: 20,
        payment_fees_complete: false, payment_fee_review_reasons: ["payment_awaiting_collection"],
        total_payment_fees: null, net_profit: netProfit,
    }} paymentBreakdown={[{ key: "paypal", name: "باي بال", orders_count: 1, total_sales: 1000, fee_amount: null }]} />));
    expect(container.querySelector('[data-testid="profit-summary-net"]').textContent).toBe("غير مكتمل");
    expect(container.querySelector('[data-testid="profit-summary-margin"]')).toBeNull();
    expect(container.querySelector('[data-testid="profit-line-payment-fees"]').textContent).toContain("غير مكتمل");
    const hint = container.querySelector('[data-testid="profit-payment-fees-incomplete-hint"]').textContent;
    expect(hint).toContain("بانتظار تحصيل بعض الطلبات");
    expect(hint).not.toContain("عمولات");
    act(() => container.querySelector('[data-testid="profit-line-payment-fees"]').click());
    const table = container.querySelector('[data-testid="payment-fees-breakdown-table"]');
    expect(table.querySelector("tbody tr td:last-child").textContent).toBe("غير مكتمل");
    expect(table.querySelector("tbody tr:last-child td:last-child").textContent).toBe("غير مكتمل");
});

test.each([
    [[], "بانتظار اكتمال بيانات عمولات الدفع"],
    [["fee_policy_missing"], "بانتظار اكتمال بيانات عمولات الدفع"],
    [["provider_split_payment_review_required"], "بانتظار مطابقة الدفعات المتعددة لبعض الطلبات"],
    [["provider_split_payment_review_required", "fee_policy_missing"], "بانتظار مطابقة الدفعات المتعددة لبعض الطلبات واستكمال بيانات عمولات الدفع"],
    [["payment_awaiting_collection", "provider_split_payment_review_required"], "بانتظار تحصيل بعض الطلبات ومطابقة الدفعات المتعددة"],
    [["payment_awaiting_collection", "fee_policy_missing"], "بانتظار تحصيل بعض الطلبات واستكمال بيانات عمولات الدفع"],
])("fee review reason %p stays distinct from collection pending", (reasons, message) => {
    act(() => root.render(<ProfitSummaryCard totals={{ payment_fees_complete: false, payment_fee_review_reasons: reasons }} />));
    expect(container.querySelector('[data-testid="profit-payment-fees-incomplete-hint"]').textContent).toContain(message);
});

test.each([undefined, true])("preserves fee and profit fallback for legacy or complete authority %s", (complete) => {
    act(() => root.render(<ProfitSummaryCard totals={{
        total_sales: 1000, total_product_cost: 100, other_payment_fees: 20,
        payment_fees_complete: complete, total_payment_fees: null, net_profit: null,
    }} />));
    expect(container.querySelector('[data-testid="profit-summary-net"]').textContent).toBe("880.00 ر.س");
    expect(container.querySelector('[data-testid="profit-line-payment-fees"]').textContent).toContain("20.00");
    expect(container.querySelector('[data-testid="profit-payment-fees-incomplete-hint"]')).toBeNull();
});

test("complete confirmed zero commission remains zero", () => {
    act(() => root.render(<ProfitSummaryCard totals={{
        total_sales: 1000, payment_fees_complete: true, total_payment_fees: 0,
        other_payment_fees: 20, net_profit: 1000,
    }} />));
    expect(container.querySelector('[data-testid="profit-line-payment-fees"]').textContent).toContain("0.00");
    expect(container.querySelector('[data-testid="profit-summary-net"]').textContent).toBe("1,000.00 ر.س");
});


test.each([null, 880])("cohort review prevents invented or stale final profit %s while retaining known commission", (netProfit) => {
    act(() => root.render(<ProfitSummaryCard totals={{
        total_sales: 1000, total_product_cost: 100, total_payment_fees: 20,
        payment_fees_complete: true, net_profit: netProfit,
    }} financialCohort={{ complete: false, review_orders: 1 }} />));
    expect(container.querySelector('[data-testid="profit-summary-net"]').textContent).toBe("غير مكتمل");
    expect(container.querySelector('[data-testid="profit-summary-margin"]')).toBeNull();
    expect(container.querySelector('[data-testid="profit-line-payment-fees"]').textContent).toContain("20.00");
    expect(container.querySelector('[data-testid="profit-payment-fees-incomplete-hint"]')).toBeNull();
    const hint = container.querySelector('[data-testid="profit-financial-cohort-incomplete-hint"]').textContent;
    expect(hint).toContain("بانتظار مراجعة الإثبات المالي للطلبات");
    expect(hint).not.toContain("عمولات");
});

test.each([false, true, undefined])("totals-only cohort authority %s respects incomplete and legacy responses", (complete) => {
    act(() => root.render(<ProfitSummaryCard totals={{
        total_sales: 1000, total_product_cost: 100, total_payment_fees: 20,
        financial_cohort_complete: complete, payment_fees_complete: true, net_profit: null,
    }} />));
    expect(container.querySelector('[data-testid="profit-summary-net"]').textContent)
        .toBe(complete === false ? "غير مكتمل" : "880.00 ر.س");
});


test("proved unpaid exclusion explains financial totals while preserving full order count", () => {
    act(() => root.render(<ProfitSummaryCard totals={{
        total_sales: 1000, total_orders: 3, total_payment_fees: 20,
        financial_cohort_complete: true, excluded_unpaid_orders: 2, net_profit: 980,
    }} />));
    const note = container.querySelector('[data-testid="profit-financial-cohort-excluded-note"]').textContent;
    expect(note).toContain("بانتظار الدفع: 2");
    expect(note).toContain("وتبقى ضمن عدد الطلبات");
    expect(note).not.toContain("ملغ");
    expect(container.querySelector('[data-testid="profit-summary-net"]').textContent).toBe("980.00 ر.س");
    expect(container.querySelector('[data-testid="profit-kpi-orders"]').textContent).toContain("3");
});

test("weekly estimates keep numeric fees and profit with their period and partial order count", () => {
    act(() => root.render(<ProfitSummaryCard totals={{
        total_sales: 1000, total_product_cost: 100, total_payment_fees: 40, net_profit: 860,
        payment_fees_complete: true, payment_fees_estimated: true,
        payment_fee_estimate_sources: [weeklySource],
    }} paymentBreakdown={[weeklyRow]} />));
    const fee = container.querySelector('[data-testid="profit-line-payment-fees"]');
    expect(fee.textContent).toContain("40.00");
    expect(fee.textContent).toContain("تقديري · متوسط أسبوعي");
    expect(container.querySelector('[data-testid="profit-summary-net"]').textContent).toBe("860.00 ر.س");
    expect(container.querySelector('[data-testid="profit-summary-net-estimate"]').textContent).toBe("تقديري · متوسط أسبوعي");
    act(() => fee.click());
    const details = container.querySelector('[data-testid="payment-fees-tooltip-content"]');
    expect(details.textContent).toContain("2026-11-09");
    expect(details.textContent).toContain("2026-11-15");
    expect(details.textContent).toContain("عمليات البيع المرجعية: 35");
    expect(details.textContent).toContain("الطلبات المقدّرة: 2 من 5");
});

test.each([weeklySource, operationalSource])("actual commission replacement clears $basis estimate labels", (source) => {
    const render = (estimated) => act(() => root.render(<ProfitSummaryCard totals={{
        total_sales: 1000, total_payment_fees: estimated ? 40 : 38, net_profit: estimated ? 960 : 962,
        payment_fees_complete: true, payment_fees_estimated: estimated,
        payment_fee_estimate_sources: estimated ? [source] : [],
    }} paymentBreakdown={[{ ...weeklyRow, fees_estimated: estimated,
        fee_amount: estimated ? 40 : 38, fee_estimate_sources: estimated ? [source] : [],
        estimated_orders_count: estimated ? 2 : 0,
    }]} />));
    render(true);
    act(() => container.querySelector('[data-testid="profit-line-payment-fees"]').click());
    expect(container.textContent).toContain("تقديري");
    render(false);
    expect(container.textContent).not.toContain("متوسط أسبوعي");
    expect(container.textContent).not.toContain("نسبة الأرصدة التشغيلية");
    expect(container.textContent).not.toContain("2026-11-09");
    expect(container.querySelector('[data-testid="profit-line-payment-fees"]').textContent).toContain("38.00");
    expect(container.querySelector('[data-testid="profit-summary-net"]').textContent).toBe("962.00 ر.س");
});

test("an operational percentage fallback keeps its exact rate and source period without a weekly claim", () => {
    act(() => root.render(<ProfitSummaryCard totals={{
        total_sales: 1000, total_product_cost: 100, total_ads_cost: 50, total_payment_fees: 88.26, net_profit: 761.74,
        payment_fees_complete: true, payment_fees_estimated: true,
        payment_fee_estimate_sources: [operationalSource],
    }} paymentBreakdown={[{ ...weeklyRow, fee_amount: 88.26, estimated_orders_count: 5,
        fee_estimate_sources: [operationalSource], fee_sources: ["operational_balance_average"] }]} />));
    const fee = container.querySelector('[data-testid="profit-line-payment-fees"]');
    expect(fee.textContent).toContain("88.26");
    expect(fee.textContent).toContain("تقديري · نسبة الأرصدة التشغيلية");
    expect(container.querySelector('[data-testid="profit-summary-net"]').textContent).toBe("761.74 ر.س");
    expect(container.querySelector('[data-testid="profit-summary-net-estimate"]').textContent).toBe("تقديري · نسبة الأرصدة التشغيلية");
    act(() => fee.click());
    const details = container.querySelector('[data-testid="payment-fees-tooltip-content"]');
    expect(details.textContent).toContain("نسبة العمولة: 7.574738%");
    expect(details.textContent).toContain("فترة الأرصدة التشغيلية: 2026-10-01T00:00:00Z إلى 2026-11-22T00:00:00Z");
    expect(details.textContent).toContain("تاريخ الكشف: 2026-10-31");
    expect(details.textContent).toContain("وقت اعتماد المصدر: 2026-11-01T10:00:00Z");
    expect(container.textContent).not.toContain("متوسط أسبوعي");
    expect(container.textContent).not.toContain("أسبوع المرجع");
});

test("a weekly estimate never clears another incomplete commission", () => {
    act(() => root.render(<ProfitSummaryCard totals={{
        total_sales: 1000, total_payment_fees: null, net_profit: 960,
        payment_fees_complete: false, payment_fees_estimated: true,
        payment_fee_estimate_sources: [weeklySource], payment_fee_review_reasons: ["fee_policy_missing"],
    }} paymentBreakdown={[weeklyRow]} />));
    expect(container.querySelector('[data-testid="profit-line-payment-fees"]').textContent).toContain("غير مكتمل");
    expect(container.querySelector('[data-testid="profit-summary-net"]').textContent).toBe("غير مكتمل");
    expect(container.querySelector('[data-testid="profit-summary-net-estimate"]')).toBeNull();
    expect(container.querySelector('[data-testid="profit-summary-margin"]')).toBeNull();
});

test("aggregate weekly metadata does not mark actual sub-methods as estimated", () => {
    const rows = buildPaymentFeeRows([{ ...weeklyRow, sub_methods: [
        { key: "tabby_actual", display: "تابي الفعلي", orders_count: 3, fee_amount: 25, fees_estimated: false },
        { key: "tabby_weekly", display: "تابي المقدّر", orders_count: 2, fee_amount: 15,
            fees_estimated: true, estimated_orders_count: 2, fee_estimate_sources: [weeklySource] },
    ] }]);
    expect(rows[0]).toMatchObject({ feesEstimated: false, estimatedOrdersCount: null, feeEstimateSources: [] });
    expect(rows[1]).toMatchObject({ feesEstimated: true, estimatedOrdersCount: 2, feeEstimateSources: [weeklySource] });
});
