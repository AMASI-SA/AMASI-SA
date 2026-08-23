import { normalizeFinancialProviderApps } from "./financialProviderApps";

test("normalizes financial provider apps without inventing legacy balances", () => {
    const model = normalizeFinancialProviderApps({
        operation_id: "MZ2-FIN-CUTOVER-001",
        apps: [{
            provider_id: "payment:tamara",
            provider_code: "tamara",
            display_name: "تمارا",
            kind: "bnpl",
            configured: true,
            fee_rules: [{ code: "tamara", commission_percent: 6.99 }],
            tax_invoice_count: 2,
            tax_invoice_total: 115,
            legacy_financial_data_included: false,
        }],
        summary: {
            providers: 1,
            payment_providers: 1,
            shipping_companies: 0,
            tax_invoices: 2,
            verified_tax_invoices: 1,
        },
    });

    expect(model.operationId).toBe("MZ2-FIN-CUTOVER-001");
    expect(model.apps[0]).toMatchObject({
        providerId: "payment:tamara",
        taxInvoiceCount: 2,
        taxInvoiceTotal: 115,
        legacyFinancialDataIncluded: false,
    });
    expect(model.apps[0].openingBalance).toBeUndefined();
    expect(model.summary.verifiedTaxInvoices).toBe(1);
});
