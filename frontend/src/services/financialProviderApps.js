import api from "../lib/api";

const safeNumber = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
};

export function normalizeFinancialProviderApps(payload = {}) {
    const apps = Array.isArray(payload?.apps) ? payload.apps : [];
    return {
        operationId: String(payload?.operation_id || ""),
        apps: apps.map((app) => ({
            providerId: String(app?.provider_id || ""),
            providerCode: String(app?.provider_code || ""),
            displayName: String(app?.display_name || ""),
            kind: String(app?.kind || "unknown"),
            configured: Boolean(app?.configured),
            paymentMode: app?.payment_mode || null,
            feeRules: Array.isArray(app?.fee_rules) ? app.fee_rules : [],
            taxInvoiceCount: safeNumber(app?.tax_invoice_count),
            taxInvoiceTotal: safeNumber(app?.tax_invoice_total),
            latestTaxInvoice: app?.latest_tax_invoice || null,
            operation: app?.operation || {},
            legacyFinancialDataIncluded: app?.legacy_financial_data_included === true,
        })),
        summary: {
            providers: safeNumber(payload?.summary?.providers),
            paymentProviders: safeNumber(payload?.summary?.payment_providers),
            shippingCompanies: safeNumber(payload?.summary?.shipping_companies),
            taxInvoices: safeNumber(payload?.summary?.tax_invoices),
            verifiedTaxInvoices: safeNumber(payload?.summary?.verified_tax_invoices),
        },
        accountingPolicy: payload?.accounting_policy || {},
    };
}

export async function getFinancialProviderApps() {
    const response = await api.get("/financial-provider-apps");
    return normalizeFinancialProviderApps(response.data);
}

export async function createProviderTaxInvoice(providerId, payload) {
    if (!providerId) throw new Error("provider_id_required");
    const response = await api.post(
        `/financial-provider-apps/${encodeURIComponent(providerId)}/tax-invoices`,
        payload,
    );
    return response.data;
}
