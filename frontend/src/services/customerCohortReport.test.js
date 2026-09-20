import { normalizeCustomerCohortReport } from "./customerCohortReport";

test("normalizes cohort money coverage without turning missing values into claims", () => {
    const report = normalizeCustomerCohortReport({
        report: {
            contract_version: "mezan_customer_cohort_profit_report_v1",
            as_of: "2026-09-20",
            read_only: true,
            summary: {
                identified_customers: 12,
                returning_customers_all_time: 4,
                repeat_orders_all_time: 6,
                first_order_sources: {
                    confirmed_ad: 7,
                    explicit_non_ad: 3,
                    unresolved: 2,
                },
                first_acquisition_cost: {
                    known_orders: 6,
                    unknown_orders: 1,
                    coverage_pct: 85.71,
                    known_amount_sar: 330,
                    partial: true,
                },
                later_order_contribution_profit: {
                    known_orders: 5,
                    unknown_orders: 1,
                    coverage_pct: 83.33,
                    known_amount_sar: 410,
                    partial: true,
                },
            },
            cohorts: [{
                key: "first_purchase_0_30_days",
                label: "أول شراء خلال آخر 0–30 يومًا",
                customers: 8,
                first_orders: 8,
                first_order_sales_sar: 1200,
                later_orders: 2,
                first_order_sources: { confirmed_ad: 5, explicit_non_ad: 2, unresolved: 1 },
                first_acquisition_cost: { known_orders: 5, unknown_orders: 0, coverage_pct: 100, known_amount_sar: 250 },
                later_order_contribution_profit: { known_orders: 1, unknown_orders: 1, coverage_pct: 50, known_amount_sar: 75, partial: true },
            }],
            returning: {
                last_30_days: { customers: 3, orders: 4, contribution_profit: { known_orders: 3, unknown_orders: 1, coverage_pct: 75, known_amount_sar: 220, partial: true } },
                last_60_days: { customers: 4, orders: 6, contribution_profit: { known_orders: 5, unknown_orders: 1, coverage_pct: 83.33, known_amount_sar: 410, partial: true } },
            },
        },
    });

    expect(report.read_only).toBe(true);
    expect(report.summary.first_order_sources.confirmed_ad).toBe(7);
    expect(report.summary.first_acquisition_cost.known_amount_sar).toBe(330);
    expect(report.summary.first_acquisition_cost.unknown_is_zero).toBe(false);
    expect(report.cohorts[0].later_order_contribution_profit.partial).toBe(true);
    expect(report.returning.last_30_days.customers).toBe(3);
});

test("uses safe empty defaults for incomplete responses", () => {
    const report = normalizeCustomerCohortReport({});
    expect(report.summary.identified_customers).toBe(0);
    expect(report.summary.first_acquisition_cost.known_amount_sar).toBe(0);
    expect(report.cohorts).toEqual([]);
    expect(report.returning.last_60_days.orders).toBe(0);
});
