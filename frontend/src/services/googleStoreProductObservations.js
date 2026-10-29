import api from "../lib/api";
import { GOOGLE_ADS_ROOT } from "./googleAds";
import { pinnedCatalogReviewScope } from "./googleCatalogReview";

const PERIODS = { last_14_days: [13, 0], previous_14_days: [27, 14], last_30_days: [29, 0], last_90_days: [89, 0] };
function unproved() { const error = new Error("تعذر إثبات مطابقة مشاهدات الطلبات للخطة."); error.code = "GOOGLE_STORE_OBSERVATIONS_UNPROVED"; return error; }
function expectedWindows(asOf) {
    if (typeof asOf !== "string" || !/T.*(?:Z|[+-]\d{2}:\d{2})$/.test(asOf) || !Number.isFinite(Date.parse(asOf))) throw unproved();
    const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Riyadh", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date(asOf));
    const value = (type) => parts.find((p) => p.type === type).value;
    const midnight = Date.UTC(Number(value("year")), Number(value("month")) - 1, Number(value("day")));
    const day = (offset) => new Date(midnight - (offset + 1) * 86400000).toISOString().slice(0, 10);
    return Object.fromEntries(Object.entries(PERIODS).map(([name, [first, last]]) => [name, { from: day(first), to: day(last), timezone: "Asia/Riyadh" }]));
}
export function validateStoreProductObservations(response, scope) {
    const out = response?.observations;
    if (response?.plan_id !== scope.planId || response?.plan_revision !== scope.revision || response?.execution_authorized !== false || response?.provider_writes !== 0
        || !Array.isArray(response.product_ids_reviewed) || response.product_ids_reviewed.join("|") !== scope.productIds.join("|")
        || out?.mode !== "stored_product_line_observations" || out.metric_basis !== "lower_bound_on_qualifying_saved_gross_line_observations_not_sales"
        || out.coverage_complete !== false || out.refunds_reconciled !== false || out.snapshot_consistent !== false || out.source_snapshot_immutable !== false
        || out.platform_conversions_added !== false || out.execution_authorized !== false || out.source_writes !== 0 || out.provider_writes !== 0
        || typeof out.truncated !== "boolean" || !Array.isArray(out.products) || out.products.length !== scope.productIds.length) throw unproved();
    const expected = expectedWindows(out.as_of);
    if (!out.windows || Object.keys(out.windows).length !== 4) throw unproved();
    for (const [name, period] of Object.entries(expected)) {
        const actual = out.windows[name];
        if (!actual || Object.keys(actual).length !== 3 || Object.entries(period).some(([key, value]) => actual[key] !== value)) throw unproved();
    }
    const seen = new Set(); let totalGroups = 0;
    for (const product of out.products) {
        if (!scope.productIds.includes(product?.product_id) || seen.has(product.product_id) || !Array.isArray(product.currency_groups)
            || product.sales_native !== null || product.profitability !== null || product.growth_14_days_percent !== null || product.success_classification !== "not_established") throw unproved();
        seen.add(product.product_id); const currencies = new Set(); totalGroups += product.currency_groups.length;
        if (totalGroups > 200 || (out.truncated && product.currency_groups.length)) throw unproved();
        for (const group of product.currency_groups) {
            if (typeof group.currency_code !== "string" || !/^[A-Z]{3}$/.test(group.currency_code) || currencies.has(group.currency_code)
                || group.account_scope !== "owner_store_projection" || group.platform_account_id !== null || !group.windows || Object.keys(group.windows).length !== 4) throw unproved();
            currencies.add(group.currency_code);
            for (const name of Object.keys(PERIODS)) {
                const row = group.windows[name];
                if (!row || Object.keys(row).length !== 3) throw unproved();
                const missing = row.observed_orders === null && row.observed_lines === null && row.observed_units === null;
                const present = Number.isInteger(row.observed_orders) && row.observed_orders > 0 && row.observed_orders <= 1000
                    && Number.isInteger(row.observed_lines) && row.observed_lines >= row.observed_orders && row.observed_lines <= 100000
                    && typeof row.observed_units === "string" && /^[0-9]{1,20}(?:\.[0-9]{1,6})?$/.test(row.observed_units) && Number(row.observed_units) > 0;
                if (!missing && !present) throw unproved();
            }
        }
    }
    if (!Array.isArray(response.catalog_missing_or_unverified_ids) || new Set(response.catalog_missing_or_unverified_ids).size !== response.catalog_missing_or_unverified_ids.length
        || response.catalog_missing_or_unverified_ids.some((id) => !scope.productIds.includes(id))) throw unproved();
    return response;
}
export async function getGoogleStoreProductObservations(plan, { signal } = {}) {
    const scope = pinnedCatalogReviewScope(plan);
    const response = (await api.get(`${GOOGLE_ADS_ROOT}/commercial-plans/${scope.planId}/store-observations`, { params: { expected_revision: scope.revision }, signal })).data;
    return validateStoreProductObservations(response, scope);
}
