import { fireEvent, render, screen } from "@testing-library/react";

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
    render(
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

    fireEvent.click(screen.getByTestId("profit-line-payment-fees"));
    expect(screen.getByTestId("payment-fees-tooltip-content")).toBeInTheDocument();
    expect(screen.getByTestId("payment-fees-breakdown-table")).toBeInTheDocument();
    expect(screen.getByText("تمارا")).toBeInTheDocument();
    expect(screen.getByText("Snapchat — أماسي الرياض")).toBeInTheDocument();
    expect(screen.getAllByText("86.35").length).toBeGreaterThan(0);
});
