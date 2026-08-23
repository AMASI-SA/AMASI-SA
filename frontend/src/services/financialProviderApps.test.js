import { normalizeFinancialProviderApps } from "./financialProviderApps";

test("normalizes financial provider apps without inventing legacy balances", () => {
    const model = normalizeFinancialProviderApps({
        operation_id: "MZ2-FIN-CUTOVER-001",
        apps: [{
            provider_id: "shipping:smsa",
            provider_code: "smsa",
            display_name: "سمسا",
            kind: "shipping_company",
            configured: true,
            fee_rules: [{ code: "cod_tier_1", commission_percent: 1 }],
            cod_fee_rule_mode: "tiered",
            settlement_netting_supported: true,
            bank_transfer_optional: true,
            tax_invoice_count: 2,
            tax_invoice_total: 115,
            legacy_financial_data_included: false,
        }],
        summary: {
            providers: 1,
            payment_providers: 0,
            shipping_companies: 1,
            tax_invoices: 2,
            verified_tax_invoices: 1,
        },
    });

    expect(model.operationId).toBe("MZ2-FIN-CUTOVER-001");
    expect(model.apps[0]).toMatchObject({
        providerId: "shipping:smsa",
        taxInvoiceCount: 2,
        taxInvoiceTotal: 115,
        legacyFinancialDataIncluded: false,
        codFeeRuleMode: "tiered",
        settlementNettingSupported: true,
        bankTransferOptional: true,
    });
    expect(model.apps[0].openingBalance).toBeUndefined();
    expect(model.summary.verifiedTaxInvoices).toBe(1);
});
