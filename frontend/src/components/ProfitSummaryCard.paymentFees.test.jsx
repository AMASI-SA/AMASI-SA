import { act } from "react-dom/test-utils";
import { createRoot } from "react-dom/client";

jest.mock("react-router-dom", () => ({
    Link: ({ children }) => children,
}));

import ProfitSummaryCard, { buildPaymentFeeRows } from "./ProfitSummaryCard";

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
