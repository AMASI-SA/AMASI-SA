import React, { act } from "react";
import { createRoot } from "react-dom/client";
import AccountingSettlements from "./AccountingSettlements";
import { getAccountingSettlementContext, getAccountingSettlementDrafts } from "../../services/accountingModule";
jest.mock("../../services/accountingModule", () => ({ getAccountingSettlementContext: jest.fn(), getAccountingSettlementDrafts: jest.fn() }));
jest.mock("./AccountingSettlementRegister", () => () => null);
jest.mock("./AccountingReceivables", () => () => null);
jest.mock("./AccountingBankReceipts", () => ({ SettlementReceiptLink: () => null }));
import fs from "fs";
import path from "path";

import { ACCOUNTING_PAGES } from "./accountingPages";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingSettlements.jsx"),
    "utf8",
).replace(/\r\n/g, "\n");
const workspaceSource = fs.readFileSync(
    path.join(__dirname, "AccountingWorkspace.jsx"),
    "utf8",
).replace(/\r\n/g, "\n");
const serviceSource = fs.readFileSync(
    path.join(__dirname, "../../services/accountingModule.js"),
    "utf8",
).replace(/\r\n/g, "\n");

test("P01 settlements page remains implemented in the nine-page order", () => {
    expect(ACCOUNTING_PAGES).toHaveLength(9);
    expect(ACCOUNTING_PAGES.map((page) => page.id)).toEqual([
        "home",
        "financial-accounts",
        "settlements",
        "shipping-cod",
        "inventory-purchases",
        "financial-movements",
        "payroll-obligations",
        "opening-balances",
        "journals-reports",
    ]);
    expect(ACCOUNTING_PAGES.find((page) => page.id === "settlements")?.implementationStatus)
        .toBe("implemented");
});

test("settlements workspace enforces upload, matching, preview, review, then posting", () => {
    expect(source).toContain('data-testid="settlement-upload-form"');
    expect(source).toContain('data-testid="settlement-unmatched-entries"');
    expect(source).toContain('data-testid="submit-settlement-draft"');
    expect(source).toContain('data-testid="review-settlement-draft"');
    expect(source).toContain('data-testid="post-settlement-draft"');
    expect(source).toContain("معاينة القيد");
    expect(source).toContain("القيد المرحّل لا يُعدل أو يُحذف");
    expect(source).toContain("source_review_acknowledged");
});

test("accounting workspace no longer renders the legacy provider catalogue as P01", () => {
    expect(workspaceSource).toContain('import AccountingSettlements from "./AccountingSettlements"');
    expect(workspaceSource).toContain("<AccountingSettlements accountingPermissions={permissions} />");
    expect(workspaceSource).not.toContain("LegacyFinancialProviderAppsWorkspace");
});

test("frontend client exposes separate identity, draft, match, review, and post operations", () => {
    expect(serviceSource).toContain("uploadAccountingSettlementDraft");
    expect(serviceSource).toContain("updateAccountingSettlementIdentity");
    expect(serviceSource).toContain("matchAccountingSettlementEntry");
    expect(serviceSource).toContain("submitAccountingSettlementDraft");
    expect(serviceSource).toContain("reviewAccountingSettlementDraft");
    expect(serviceSource).toContain("postAccountingSettlementDraft");
    expect(serviceSource).toContain("/identity");
    expect(serviceSource).toContain("/match-entry");
    expect(serviceSource.indexOf("updateAccountingSettlementIdentity"))
        .toBeLessThan(serviceSource.indexOf("api.patch(\n        `${SETTLEMENTS}/drafts/${encodeURIComponent(draftId)}`,"));
});


test("provider binding excludes cash and invalid legacy binding is never approved or preselected", async () => {
    getAccountingSettlementContext.mockResolvedValue({
        providers: [{ id: "salla", label: "Salla" }],
        banks: [{ id: "bank", name: "Canonical bank", account_type: "bank" }, { id: "cash", name: "Canonical cash", account_type: "cash" }],
        bindings: [{ provider: "salla", provider_label: "Salla", configured: false, bank_account_id: "bank", verification_status: "verified", code: "MZ2_LINK_REQUIRED" }],
    });
    getAccountingSettlementDrafts.mockResolvedValue({ items: [] });
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const node = document.createElement("div"); document.body.appendChild(node);
    const root = createRoot(node);
    try {
        await act(async () => root.render(<AccountingSettlements />));
        const binding = node.querySelector('[data-testid="provider-binding-bank"]');
        const draft = node.querySelector('[data-testid="settlement-bank"]');
        expect([...binding.options].map(o => o.value)).toEqual(["", "bank"]);
        expect([...draft.options].map(o => o.value)).toEqual(["", "bank", "cash"]);
        expect(binding.value).toBe(""); expect(draft.value).toBe("");
        expect(node.textContent).toContain("MZ2_LINK_REQUIRED");
        expect(node.textContent).not.toContain("بنك معتمد للتسويات الجديدة");
    } finally { act(() => root.unmount()); node.remove(); delete global.IS_REACT_ACT_ENVIRONMENT; }
});
