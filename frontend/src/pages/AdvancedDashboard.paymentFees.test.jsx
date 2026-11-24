import React, { act } from "react";
import { createRoot } from "react-dom/client";
import { ProfitCard } from "./AdvancedDashboard";

jest.mock("react-router-dom", () => ({
    Link: ({ children, to, ...props }) => <a href={to} {...props}>{children}</a>,
}));

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

function renderTotals(totals = {}, rows = [], financialCohort = undefined) {
    act(() => root.render(<ProfitCard data={{
        totals: {
            total_sales: 1000, total_orders: 1, total_product_cost: 100, total_ads_cost: 50,
            other_payment_fees: 20, net_profit: 830, product_cost_complete: true,
            ads_spend_data_complete: true, ads_spend_amount_available: true, ...totals,
        },
        ads_v2: { spend_quality: { status: "complete", amount_available: true, amount_complete: true } },
        payment_breakdown: rows,
        financial_cohort: financialCohort,
    }} />));
}

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

test.each([null, 830])("incomplete commission total cannot display final profit %s", (netProfit) => {
    renderTotals({ payment_fees_complete: false, payment_fee_review_reasons: ["payment_awaiting_collection"],
        total_payment_fees: null, net_profit: netProfit },
    [{ key: "paypal", name: "باي بال", orders_count: 1, total_sales: 1000, fee_amount: null }]);
    const hero = container.querySelector('[data-testid="advanced-profit-net-hero"]');
    expect(hero.textContent).toContain("غير مكتمل");
    expect(hero.textContent).not.toContain("830.00");
    expect(container.querySelector('[data-testid="advanced-profit-net-margin"]')).toBeNull();
    const hint = container.querySelector('[data-testid="advanced-profit-payment-fees-incomplete-hint"]').textContent;
    expect(hint).toContain("بانتظار تحصيل بعض الطلبات");
    expect(hint).not.toContain("عمولات");
    const trigger = container.querySelector('[data-testid="advanced-profit-row-payment"]');
    expect(trigger.textContent).toContain("غير مكتمل");
    act(() => trigger.click());
    const detail = container.querySelector('[data-testid="advanced-profit-payment-details"]');
    expect(detail.querySelector("tbody tr td:last-child").textContent).toBe("غير مكتمل");
    expect(detail.querySelector("tbody tr:last-child td:last-child").textContent).toBe("غير مكتمل");
});

test("unresolved commission evidence does not claim waiting for customer payment", () => {
    renderTotals({ payment_fees_complete: false, payment_fee_review_reasons: ["fee_policy_missing"], net_profit: null });
    const hint = container.querySelector('[data-testid="advanced-profit-payment-fees-incomplete-hint"]').textContent;
    expect(hint).toContain("بانتظار اكتمال بيانات عمولات الدفع");
    expect(hint).not.toContain("تحصيل");
});

test.each([null, 830])("unreconciled split payments explain the blocker and hide stale profit %s", (netProfit) => {
    renderTotals({ payment_fees_complete: false,
        payment_fee_review_reasons: ["provider_split_payment_review_required"],
        total_payment_fees: null, net_profit: netProfit });
    const hint = container.querySelector('[data-testid="advanced-profit-payment-fees-incomplete-hint"]').textContent;
    expect(hint).toContain("بانتظار مطابقة الدفعات المتعددة لبعض الطلبات");
    expect(hint).not.toContain("اكتمال بيانات عمولات");
    const hero = container.querySelector('[data-testid="advanced-profit-net-hero"]');
    expect(hero.textContent).toContain("غير مكتمل");
    expect(hero.textContent).not.toContain("830.00");
    expect(container.querySelector('[data-testid="advanced-profit-net-margin"]')).toBeNull();
});

test.each([undefined, true])("legacy or complete fee authority %s preserves authoritative profit", (complete) => {
    renderTotals({ payment_fees_complete: complete });
    expect(container.querySelector('[data-testid="advanced-profit-net-hero"]').textContent).toContain("830.00");
    expect(container.querySelector('[data-testid="advanced-profit-row-payment"]').textContent).toContain("20.00");
    expect(container.querySelector('[data-testid="advanced-profit-payment-fees-incomplete-hint"]')).toBeNull();
});

test("complete confirmed zero fee does not use a nonzero legacy sum", () => {
    renderTotals({ payment_fees_complete: true, total_payment_fees: 0 });
    const fee = container.querySelector('[data-testid="advanced-profit-row-payment"]').textContent;
    expect(fee).toContain("0.00");
    expect(fee).not.toContain("20.00");
});


test.each([null, 830])("cohort review suppresses recomputed or stale final profit %s independently of valid fees", (netProfit) => {
    renderTotals({ payment_fees_complete: true, total_payment_fees: 20, net_profit: netProfit }, [],
        { complete: false, review_orders: 1 });
    const hero = container.querySelector('[data-testid="advanced-profit-net-hero"]');
    expect(hero.textContent).toContain("غير مكتمل");
    expect(hero.textContent).not.toContain("830.00");
    expect(container.querySelector('[data-testid="advanced-profit-net-margin"]')).toBeNull();
    expect(container.querySelector('[data-testid="advanced-profit-row-payment"]').textContent).toContain("20.00");
    expect(container.querySelector('[data-testid="advanced-profit-payment-fees-incomplete-hint"]')).toBeNull();
    const hint = container.querySelector('[data-testid="advanced-profit-financial-cohort-incomplete-hint"]').textContent;
    expect(hint).toContain("بانتظار مراجعة الإثبات المالي للطلبات");
    expect(hint).not.toContain("عمولات");
});

test.each([false, true, undefined])("totals-only cohort authority %s respects incomplete and legacy responses", (complete) => {
    renderTotals({ financial_cohort_complete: complete, payment_fees_complete: true, total_payment_fees: 20 });
    const hero = container.querySelector('[data-testid="advanced-profit-net-hero"]');
    expect(hero.textContent).toContain(complete === false ? "غير مكتمل" : "830.00");
    expect(Boolean(container.querySelector('[data-testid="advanced-profit-financial-cohort-incomplete-hint"]')))
        .toBe(complete === false);
});


test("proved unpaid exclusion is explained without changing registry count or final profit", () => {
    renderTotals({ total_orders: 3, payment_fees_complete: true, total_payment_fees: 20 }, [],
        { complete: true, excluded_unpaid_orders: 2, total_orders: 3 });
    const note = container.querySelector('[data-testid="advanced-profit-financial-cohort-excluded-note"]').textContent;
    expect(note).toContain("بانتظار الدفع: 2");
    expect(note).toContain("وتبقى ضمن عدد الطلبات");
    expect(note).not.toContain("ملغ");
    expect(container.querySelector('[data-testid="advanced-profit-net-hero"]').textContent).toContain("830.00");
    expect(container.querySelector('[data-testid="advanced-profit-kpis"]').textContent).toContain("عدد الطلبات3");
});

test("weekly commission estimates show numeric totals, reference week and affected orders", () => {
    renderTotals({ total_payment_fees: 40, net_profit: 810,
        payment_fees_complete: true, payment_fees_estimated: true,
        payment_fee_estimate_sources: [weeklySource] }, [weeklyRow]);
    const fee = container.querySelector('[data-testid="advanced-profit-row-payment"]');
    expect(fee.textContent).toContain("40.00");
    expect(fee.textContent).toContain("تقديري · متوسط أسبوعي");
    const hero = container.querySelector('[data-testid="advanced-profit-net-hero"]');
    expect(hero.textContent).toContain("810.00");
    expect(hero.textContent).toContain("تقديري · متوسط أسبوعي");
    act(() => fee.click());
    const details = container.querySelector('[data-testid="advanced-profit-payment-details"]');
    expect(details.textContent).toContain("2026-11-09");
    expect(details.textContent).toContain("2026-11-15");
    expect(details.textContent).toContain("عمليات البيع المرجعية: 35");
    expect(details.textContent).toContain("الطلبات المقدّرة: 2 من 5");
});

test.each([weeklySource, operationalSource])("an actual fee refresh removes $basis labels and keeps ad bank fees", (source) => {
    const adFee = { key: "ad_bank_commissions", name: "عمولات الحسابات الإعلانية", fee_amount: 5, total_sales: 50 };
    renderTotals({ total_payment_fees: 45, net_profit: 805,
        payment_fees_complete: true, payment_fees_estimated: true,
        payment_fee_estimate_sources: [source] }, [{ ...weeklyRow, fee_estimate_sources: [source] }, adFee]);
    act(() => container.querySelector('[data-testid="advanced-profit-row-payment"]').click());
    expect(container.textContent).toContain("تقديري");
    renderTotals({ total_payment_fees: 43, net_profit: 807,
        payment_fees_complete: true, payment_fees_estimated: false,
        payment_fee_estimate_sources: [] }, [{ ...weeklyRow, fee_amount: 38, fees_estimated: false,
        estimated_orders_count: 0, fee_estimate_sources: [] }, adFee]);
    expect(container.textContent).not.toContain("متوسط أسبوعي");
    expect(container.textContent).not.toContain("نسبة الأرصدة التشغيلية");
    expect(container.textContent).not.toContain("2026-11-09");
    expect(container.querySelector('[data-testid="advanced-profit-row-payment"]').textContent).toContain("43.00");
    expect(container.querySelector('[data-testid="advanced-profit-net-hero"]').textContent).toContain("807.00");
    expect(container.querySelector('[data-testid="advanced-profit-payment-details"]').textContent).toContain("عمولات الحسابات الإعلانية");
});

test("without a current week, the existing operational percentage remains estimated with its original period", () => {
    renderTotals({ total_payment_fees: 88.26, net_profit: 761.74,
        payment_fees_complete: true, payment_fees_estimated: true,
        payment_fee_estimate_sources: [operationalSource] }, [{ ...weeklyRow, fee_amount: 88.26,
        estimated_orders_count: 5, fee_estimate_sources: [operationalSource], fee_sources: ["operational_balance_average"] }]);
    const fee = container.querySelector('[data-testid="advanced-profit-row-payment"]');
    expect(fee.textContent).toContain("88.26");
    expect(fee.textContent).toContain("تقديري · نسبة الأرصدة التشغيلية");
    const hero = container.querySelector('[data-testid="advanced-profit-net-hero"]');
    expect(hero.textContent).toContain("761.74");
    expect(hero.textContent).toContain("تقديري · نسبة الأرصدة التشغيلية");
    act(() => fee.click());
    const details = container.querySelector('[data-testid="advanced-profit-payment-details"]');
    expect(details.textContent).toContain("نسبة العمولة: 7.574738%");
    expect(details.textContent).toContain("فترة الأرصدة التشغيلية: 2026-10-01T00:00:00Z إلى 2026-11-22T00:00:00Z");
    expect(details.textContent).toContain("تاريخ الكشف: 2026-10-31");
    expect(details.textContent).toContain("وقت اعتماد المصدر: 2026-11-01T10:00:00Z");
    expect(container.textContent).not.toContain("متوسط أسبوعي");
    expect(container.textContent).not.toContain("أسبوع المرجع");
});

test("weekly coverage cannot turn an incomplete total into an estimated profit", () => {
    renderTotals({ total_payment_fees: null, net_profit: 810,
        payment_fees_complete: false, payment_fees_estimated: true,
        payment_fee_review_reasons: ["fee_policy_missing"],
        payment_fee_estimate_sources: [weeklySource] }, [weeklyRow]);
    expect(container.querySelector('[data-testid="advanced-profit-row-payment"]').textContent).toContain("غير مكتمل");
    const hero = container.querySelector('[data-testid="advanced-profit-net-hero"]');
    expect(hero.textContent).toContain("غير مكتمل");
    expect(hero.textContent).not.toContain("810.00");
    expect(hero.textContent).not.toContain("متوسط أسبوعي");
    expect(container.querySelector('[data-testid="advanced-profit-net-margin"]')).toBeNull();
});
