import fs from "fs";
import path from "path";

import { ACCOUNTING_PAGES } from "./accountingPages";

const source = fs.readFileSync(
    path.join(__dirname, "AccountingSettlements.jsx"),
    "utf8",
);
const workspaceSource = fs.readFileSync(
    path.join(__dirname, "AccountingWorkspace.jsx"),
    "utf8",
);
const serviceSource = fs.readFileSync(
    path.join(__dirname, "../../services/accountingModule.js"),
    "utf8",
);

test("P01 settlements page is marked implemented without changing the eight-page order", () => {
    expect(ACCOUNTING_PAGES).toHaveLength(8);
    expect(ACCOUNTING_PAGES.map((page) => page.id)).toEqual([
        "home",
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
