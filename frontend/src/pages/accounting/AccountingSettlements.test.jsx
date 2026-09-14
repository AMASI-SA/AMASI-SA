import fs from "fs";
import path from "path";
import React, { act } from "react";
import { createRoot } from "react-dom/client";

import { ACCOUNTING_PAGES } from "./accountingPages";

jest.mock("../../services/accountingModule", () => ({
    getAccountingSettlementContext: jest.fn(),
    getAccountingSettlementDrafts: jest.fn(),
    matchAccountingSettlementEntry: jest.fn(),
    postAccountingSettlementDraft: jest.fn(),
    rejectAccountingSettlementDraft: jest.fn(),
    reviewAccountingSettlementDraft: jest.fn(),
    saveAccountingProviderBankBinding: jest.fn(),
    submitAccountingSettlementDraft: jest.fn(),
    updateAccountingSettlementDraft: jest.fn(),
    uploadAccountingSettlementDraft: jest.fn(),
}));

jest.mock("sonner", () => ({
    toast: { success: jest.fn(), error: jest.fn(), info: jest.fn(), warning: jest.fn() },
}));

import AccountingSettlements from "./AccountingSettlements";
import {
    getAccountingSettlementContext,
    getAccountingSettlementDrafts,
    postAccountingSettlementDraft,
} from "../../services/accountingModule";

beforeEach(() => {
    jest.clearAllMocks();
});

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

test("reviewed settlement renders no privileged controls without post permission", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    getAccountingSettlementContext.mockResolvedValue({
        can_create_draft: true,
        can_post: false,
        can_manage_rules: false,
        bindings: [],
        banks: [],
        providers: [],
    });
    getAccountingSettlementDrafts.mockResolvedValue({
        items: [{
            id: "draft-reviewed-1",
            status: "reviewed",
            provider: "salla",
            provider_label: "سلة",
            statement_reference: "SALLA-SYNTHETIC-001",
            bank_account_name: "بنك اختباري",
            amounts: { reported_net: 100 },
            review_reasons: [],
        }],
    });

    try {
        await act(async () => {
            root.render(<AccountingSettlements accountingPermissions={[
                "accounting.settlements.view",
                "accounting.drafts.create",
            ]} />);
            await Promise.resolve();
        });
        const row = Array.from(container.querySelectorAll("button"))
            .find((button) => button.textContent.includes("SALLA-SYNTHETIC-001"));
        expect(row).toBeDefined();
        await act(async () => row.click());

        expect(container.querySelector('[data-testid="settlement-draft-detail"]').textContent)
            .toContain("SALLA-SYNTHETIC-001");
        expect(container.querySelector('[data-testid="review-settlement-draft"]')).toBeNull();
        expect(container.querySelector('[data-testid="post-settlement-draft"]')).toBeNull();
        expect(Array.from(container.querySelectorAll("button"))
            .some((button) => button.textContent.includes("إعادة للمعالجة"))).toBe(false);
        expect(postAccountingSettlementDraft).not.toHaveBeenCalled();
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});

test("reviewed settlement renders the post action for an authorized user", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);
    getAccountingSettlementContext.mockResolvedValue({
        can_create_draft: true,
        can_post: true,
        can_manage_rules: false,
        bindings: [],
        banks: [],
        providers: [],
    });
    getAccountingSettlementDrafts.mockResolvedValue({
        items: [{
            id: "draft-authorized-1",
            status: "reviewed",
            provider: "salla",
            provider_label: "سلة",
            statement_reference: "SALLA-AUTHORIZED-001",
            bank_account_name: "بنك اختباري",
            amounts: { reported_net: 100 },
            review_reasons: [],
        }],
    });

    try {
        await act(async () => {
            root.render(<AccountingSettlements accountingPermissions={[
                "accounting.settlements.view",
            ]} />);
            await Promise.resolve();
        });
        const row = Array.from(container.querySelectorAll("button"))
            .find((button) => button.textContent.includes("SALLA-AUTHORIZED-001"));
        expect(row).toBeDefined();
        await act(async () => row.click());

        expect(container.querySelector('[data-testid="settlement-draft-detail"]').textContent)
            .toContain("SALLA-AUTHORIZED-001");
        expect(container.querySelector('[data-testid="post-settlement-draft"]')).not.toBeNull();
        expect(container.querySelector('[data-testid="review-settlement-draft"]')).toBeNull();
        expect(Array.from(container.querySelectorAll("button"))
            .some((button) => button.textContent.includes("إعادة للمعالجة"))).toBe(true);
        expect(postAccountingSettlementDraft).not.toHaveBeenCalled();
    } finally {
        await act(async () => root.unmount());
        container.remove();
        globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});
