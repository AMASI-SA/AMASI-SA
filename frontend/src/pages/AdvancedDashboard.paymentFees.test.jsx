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

function renderTotals(totals = {}, rows = []) {
    act(() => root.render(<ProfitCard data={{
        totals: {
            total_sales: 1000, total_orders: 1, total_product_cost: 100, total_ads_cost: 50,
            other_payment_fees: 20, net_profit: 830, product_cost_complete: true,
            ads_spend_data_complete: true, ads_spend_amount_available: true, ...totals,
        },
        ads_v2: { spend_quality: { status: "complete", amount_available: true, amount_complete: true } },
        payment_breakdown: rows,
    }} />));
}

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
