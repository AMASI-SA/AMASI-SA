import React, { act } from "react";
import { createRoot } from "react-dom/client";
import UnifiedPaymentGatewaysCard from "./UnifiedPaymentGatewaysCard";
import BnplSettlements from "../pages/BnplSettlements";
import BnplSettlementsRegister from "../pages/BnplSettlementsRegister";
import api from "../lib/api";

jest.mock("../lib/api", () => ({ get: jest.fn() }));
jest.mock("./ExcludedOrdersModal", () => () => null);

let host, root;
beforeEach(() => {
    jest.resetAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement("div");
    document.body.appendChild(host);
    root = createRoot(host);
});
afterEach(async () => {
    await act(async () => root.unmount());
    host.remove();
});

test("gateway fees and net remain unavailable while the paid gross stays visible", async () => {
    const row = { key: "tabby", name_ar: "تابي", orders_count: 1, gross: 40, fees: null,
        fees_vat: null, net: null, refund_total: 0, pending_gross: 0, fees_complete: false };
    api.get.mockResolvedValue({ data: { rows: [row], totals: { ...row, currency_conversion_complete: true } } });
    await act(async () => root.render(<UnifiedPaymentGatewaysCard />));
    const cells = host.querySelector('[data-testid="unified-gateways-card-row-tabby"]').querySelectorAll("td");
    expect(cells[2].textContent).toContain("40.00");
    expect(cells[3].textContent).toBe("—");
    expect(cells[7].textContent).toBe("—");
    expect(host.querySelector('[data-testid="unified-gateways-card-fee-warning"]')).not.toBeNull();
});

test("BNPL does not turn an unresolved fee into zero or completed reconciliation", async () => {
    const totals = { transactions_count: 1, gross_sales: 40, total_refunds: 0, net_sales: 40,
        commission: null, commission_vat: null, settlement_fee: 0, settlement_fee_vat: 0,
        net_payable: null, transferred_amount: 3, remaining_with_provider: null };
    api.get.mockResolvedValue({ data: { fees_complete: false, totals, computed_at: "2026-06-10T12:00:00Z",
        providers: [{ provider: "tabby", fees_complete: false, totals,
            bank: { is_linked: true, linked_account_name: "حساب تجريبي", transferred_amount: 3,
                remaining_with_provider: null, delta_overpayment: null },
            fee_rates: { fee_source: "mz2_provider_fee_policies", commission_pct: 99, vat_pct: 99 } }] } });
    await act(async () => root.render(<BnplSettlements />));
    expect(host.querySelector('[data-testid="bnpl-settle-total-gross"]').textContent).toBe("40.00");
    expect(host.querySelector('[data-testid="bnpl-settle-total-net"]').textContent).toBe("غير متاح");
    expect(host.querySelector('[data-testid="bnpl-settle-banner"]').textContent).toContain("تحتاج إلى استكمال");
    expect(host.textContent).not.toContain("كل التسويات مكتملة");
    expect(host.textContent).not.toContain("99.00%");
});

test("weekly rows and totals keep the remaining balance unavailable", async () => {
    const totals = { gross_sales: 40, total_refunds: 0, net_sales: 40, commission: null,
        commission_vat: null, settlement_fee: 0, settlement_fee_vat: 0, net_payable: null,
        remaining_with_provider: null, invoices_count: 1 };
    const row = { ...totals, invoice_no: 1, transactions_count: 1, from: "2026-06-01", to: "2026-06-07",
        fees_complete: false };
    api.get.mockImplementation(async (path) => {
        if (path.includes("/weekly/")) return { data: { success: true, rows: [row], totals, fees_complete: false } };
        if (path.includes("/matching/")) return { data: { success: true, invoices: [{ ...row,
            match_status: "review", matched_transfer: null }], unmatched_transfers: [], totals: {} } };
        return { data: { totals, fees_complete: false, providers: [{ provider: "tabby", totals,
            fees_complete: false, bank: {}, fee_rates: { fee_source: "mz2_provider_fee_policies" } }] } };
    });
    await act(async () => root.render(<BnplSettlements />));
    await act(async () => host.querySelector('[data-testid="bnpl-settle-show-weekly-tabby"]').click());
    expect(host.querySelector('[data-testid="bnpl-weekly-row-1"]').textContent).toContain("40.00");
    expect(host.querySelector('[data-testid="bnpl-weekly-remaining-1"]').textContent).toBe("غير متاح");
    expect(host.querySelector('[data-testid="bnpl-weekly-remaining-total"]').textContent).toBe("غير متاح");
    expect(host.querySelector('[data-testid="bnpl-weekly-match-1"]').textContent).toBe("الرسوم تحتاج استكمال");
});

test("registration overview and reconciliation display unavailable estimates with actual receipts", async () => {
    api.get.mockImplementation(async (path) => {
        if (path.endsWith("registration-overview")) return { data: { providers: [{ provider: "tabby",
            expected_total: null, difference: null, current_receivable: 7, received_total: 5, gross_sales: 40,
            fees_complete: false, match_status: "review" }] } };
        if (path.endsWith("reconciliation")) return { data: { rows: [{ provider: "tabby", expected: null,
            actual: 5, difference: null, count: 1, match_status: "review" }] } };
        return { data: { items: [] } };
    });
    await act(async () => root.render(<BnplSettlementsRegister />));
    expect(host.querySelector('[data-testid="metric-expected-tabby"]').textContent).toContain("غير متاح");
    expect(host.querySelector('[data-testid="metric-diff-tabby"]').textContent).toContain("غير متاح");
    expect(host.querySelector('[data-testid="metric-received-tabby"]').textContent).toContain("5.00");
    expect(host.querySelector('[data-testid="provider-card-tabby"]').textContent).toContain("40.00");
    const cells = host.querySelector('[data-testid="reconcile-row-tabby"]').querySelectorAll("td");
    expect(cells[1].textContent).toBe("غير متاح");
    expect(cells[2].textContent).toBe("5.00");
    expect(cells[3].textContent).toBe("غير متاح");
    expect(host.querySelector('[data-testid="match-status-green"]')).toBeNull();
});
