import api from "../lib/api";

const finite = (value, fallback = 0) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
};

const moneyCoverage = (value = {}) => ({
    known_orders: finite(value.known_orders),
    unknown_orders: finite(value.unknown_orders),
    coverage_pct: finite(value.coverage_pct),
    known_amount_sar: finite(value.known_amount_sar),
    partial: value.partial === true,
    unknown_is_zero: false,
});

export function normalizeCustomerCohortReport(payload = {}) {
    const report = payload?.report && typeof payload.report === "object"
        ? payload.report
        : payload;
    const summary = report?.summary || {};
    const returning = report?.returning || {};
    const normalizeReturning = (value = {}) => ({
        customers: finite(value.customers),
        orders: finite(value.orders),
        contribution_profit: moneyCoverage(value.contribution_profit),
    });
    return {
        contract_version: String(report?.contract_version || ""),
        as_of: String(report?.as_of || ""),
        read_only: report?.read_only === true,
        summary: {
            identified_customers: finite(summary.identified_customers),
            returning_customers_all_time: finite(summary.returning_customers_all_time),
            repeat_orders_all_time: finite(summary.repeat_orders_all_time),
            first_order_sources: {
                confirmed_ad: finite(summary?.first_order_sources?.confirmed_ad),
                explicit_non_ad: finite(summary?.first_order_sources?.explicit_non_ad),
                unresolved: finite(summary?.first_order_sources?.unresolved),
            },
            first_acquisition_cost: moneyCoverage(summary.first_acquisition_cost),
            later_order_contribution_profit: moneyCoverage(
                summary.later_order_contribution_profit,
            ),
        },
        cohorts: Array.isArray(report?.cohorts) ? report.cohorts.map((row) => ({
            key: String(row?.key || ""),
            label: String(row?.label || ""),
            customers: finite(row?.customers),
            first_orders: finite(row?.first_orders),
            first_order_sales_sar: finite(row?.first_order_sales_sar),
            later_orders: finite(row?.later_orders),
            first_acquisition_cost: moneyCoverage(row?.first_acquisition_cost),
            later_order_contribution_profit: moneyCoverage(
                row?.later_order_contribution_profit,
            ),
            first_order_sources: {
                confirmed_ad: finite(row?.first_order_sources?.confirmed_ad),
                explicit_non_ad: finite(row?.first_order_sources?.explicit_non_ad),
                unresolved: finite(row?.first_order_sources?.unresolved),
            },
        })) : [],
        returning: {
            last_30_days: normalizeReturning(returning.last_30_days),
            last_60_days: normalizeReturning(returning.last_60_days),
        },
        coverage: report?.coverage && typeof report.coverage === "object"
            ? report.coverage
            : {},
        definitions: report?.definitions && typeof report.definitions === "object"
            ? report.definitions
            : {},
        guardrails: report?.guardrails && typeof report.guardrails === "object"
            ? report.guardrails
            : {},
    };
}

export async function getCustomerCohortReport(asOf) {
    const response = await api.get("/mezan-attribution-v1/customer-cohorts", {
        params: { as_of: asOf },
    });
    return normalizeCustomerCohortReport(response.data);
}
