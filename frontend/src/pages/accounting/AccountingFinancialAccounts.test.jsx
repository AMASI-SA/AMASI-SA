import React, { act } from "react";
import { createRoot } from "react-dom/client";

import {
    createFinancialAccount,
    getFinancialAccountDefinitions,
    getFinancialAccounts,
    getFinancialAccountsTransition,
    getOpeningBalanceDrafts,
} from "../../services/accountingModule";
import AccountingFinancialAccounts from "./AccountingFinancialAccounts";

jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));
jest.mock("../../services/accountingModule", () => ({
    advanceFinancialAccountsTransition: jest.fn(),
    archiveFinancialAccount: jest.fn(),
    createFinancialAccount: jest.fn(),
    createOpeningBalanceDraft: jest.fn(),
    getFinancialAccountDefinitions: jest.fn(),
    getFinancialAccounts: jest.fn(),
    getFinancialAccountsTransition: jest.fn(),
    getOpeningBalanceDrafts: jest.fn(),
    postOpeningBalanceDraft: jest.fn(),
    previewOpeningBalanceDraft: jest.fn(),
    reverseOpeningBalanceDraft: jest.fn(),
    reviewOpeningBalanceDraft: jest.fn(),
    updateFinancialAccount: jest.fn(),
    uploadOpeningBalanceEvidence: jest.fn(),
}));

const DEFINITIONS = {
    account_types: ["bank", "cash"],
    opening_categories: [
        { id: "financial_account", label: "حساب مالي معرف", dynamic_rule: true },
        { id: "other_payable", label: "التزام آخر", section_id: "equity", meaning: "owed_by_us" },
    ],
    financial_account_rules: {
        bank: { section_id: "banks_cash", meaning: "available_to_us" },
        cash: { section_id: "banks_cash", meaning: "available_to_us" },
    },
    evidence_sections: [
        { id: "banks_cash", label: "البنوك والصندوق" },
        { id: "equity", label: "حقوق الملكية" },
    ],
};

const ACCOUNT = {
    id: "account-1",
    name: "بنك الإنماء",
    account_type: "bank",
    currency: "SAR",
    external_ref: "BANK-1",
    status: "active",
    version: 1,
};

let root;
let node;

beforeEach(() => {
    jest.resetAllMocks();
    global.IS_REACT_ACT_ENVIRONMENT = true;
    node = document.createElement("div");
    document.body.appendChild(node);
    root = createRoot(node);
    getFinancialAccountDefinitions.mockResolvedValue(DEFINITIONS);
    getFinancialAccounts.mockResolvedValue({ items: [ACCOUNT] });
    getFinancialAccountsTransition.mockResolvedValue({
        state: "legacy_active",
        state_revision: 0,
        contract_revision: 1,
    });
    getOpeningBalanceDrafts.mockResolvedValue({ items: [] });
});

afterEach(() => {
    act(() => root.unmount());
    node.remove();
});

async function renderPage(permissions, drafts = []) {
    getOpeningBalanceDrafts.mockResolvedValue({ items: drafts });
    await act(async () => {
        root.render(<AccountingFinancialAccounts accountingPermissions={permissions} />);
        await Promise.resolve();
    });
    await act(async () => { await Promise.resolve(); });
}

function hasButton(label) {
    return [...node.querySelectorAll("button")].some((button) => button.textContent.includes(label));
}

test("renders the real financial accounts page and keeps explicit authorities separate", async () => {
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.opening_balances.approve",
    ]);

    expect(node.querySelector('[data-testid="financial-accounts-page"]')).not.toBeNull();
    expect(node.textContent).toContain("بنك الإنماء");
    expect(node.querySelector('[data-testid="financial-account-create-form"]')).toBeNull();
    expect(node.querySelector('[data-testid="unified-opening-balances"]')).toBeNull();
    expect(hasButton("إيقاف الكتابات للانتقال")).toBe(false);
    expect(getOpeningBalanceDrafts).not.toHaveBeenCalled();
});

test("manage enables account CRUD without granting opening review or post", async () => {
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.financial_accounts.manage",
        "accounting.opening_balances.view",
    ], [{
        id: "draft-1",
        status: "draft",
        version: 1,
        debit_total: "100.00",
        credit_total: "100.00",
    }]);

    expect(node.querySelector('[data-testid="financial-account-create-form"]')).not.toBeNull();
    expect(node.querySelector('[data-testid="unified-opening-balances"]')).not.toBeNull();
    expect(node.querySelector('[data-testid="opening-draft-form"]')).toBeNull();
    expect(hasButton("مراجعة وقفل الأدلة")).toBe(false);
    expect(hasButton("ترحيل عبر ميزان 2")).toBe(false);
    expect(hasButton("إيقاف الكتابات للانتقال")).toBe(false);
});

test("review post and reverse controls require their exact permission and draft state", async () => {
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.opening_balances.view",
        "accounting.opening_balances.review",
    ], [{
        id: "draft-review",
        status: "previewed",
        version: 2,
        debit_total: "115.00",
        credit_total: "115.00",
        preview_entries: [{
            line_no: 1,
            entity_type: "bank",
            entity_id: "account-1",
            label: "بنك الإنماء",
            original_currency: "SAR",
            original_amount: "115.00",
            sar_amount: "115.00",
            side: "debit",
        }],
    }]);
    expect(hasButton("مراجعة وقفل الأدلة")).toBe(true);
    expect(hasButton("ترحيل عبر ميزان 2")).toBe(false);
    expect(node.querySelector('[data-testid="opening-preview-table"]')).not.toBeNull();

    act(() => root.unmount());
    node.replaceChildren();
    root = createRoot(node);
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.opening_balances.view",
        "accounting.opening_balances.post",
    ], [{
        id: "draft-post",
        status: "reviewed",
        version: 2,
        debit_total: "115.00",
        credit_total: "115.00",
    }]);
    expect(hasButton("ترحيل عبر ميزان 2")).toBe(true);
    expect(hasButton("عكس عند لحظة القطع نفسها")).toBe(false);

    act(() => root.unmount());
    node.replaceChildren();
    root = createRoot(node);
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.opening_balances.view",
        "accounting.journals.reverse",
    ], [{
        id: "draft-reverse",
        status: "posted",
        version: 3,
        debit_total: "115.00",
        credit_total: "115.00",
    }]);
    expect(hasButton("عكس عند لحظة القطع نفسها")).toBe(true);
    expect(hasButton("ترحيل عبر ميزان 2")).toBe(false);
});

test("draft manager previews but cannot review, post, or transition", async () => {
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.opening_balances.view",
        "accounting.opening_balances.drafts.manage",
    ], [{
        id: "draft-preview",
        status: "draft",
        version: 1,
        debit_total: "115.00",
        credit_total: "115.00",
    }]);
    expect(hasButton("إنشاء المعاينة الكاملة")).toBe(true);
    expect(hasButton("مراجعة وقفل الأدلة")).toBe(false);
    expect(hasButton("ترحيل عبر ميزان 2")).toBe(false);
    expect(hasButton("إيقاف الكتابات للانتقال")).toBe(false);
});

test("writer transition control requires its independent permission", async () => {
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.ledger_transition.manage",
    ]);
    expect(hasButton("إيقاف الكتابات للانتقال")).toBe(true);
});

test("an older reversed opening is not offered while a newer opening is posted", async () => {
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.opening_balances.view",
        "accounting.opening_balances.drafts.manage",
    ], [
        {
            id: "latest-posted",
            status: "posted",
            version: 4,
            debit_total: "120.00",
            credit_total: "120.00",
        },
        {
            id: "older-reversed",
            status: "reversed",
            version: 5,
            debit_total: "115.00",
            credit_total: "115.00",
        },
    ]);
    expect(node.textContent).not.toContain("سترتبط المسودة الجديدة بالمسودة السابقة");
});

test("account creation calls the dedicated financial account endpoint", async () => {
    createFinancialAccount.mockResolvedValue({ ...ACCOUNT, id: "account-2" });
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.financial_accounts.manage",
    ]);

    const input = node.querySelector('[aria-label="اسم الحساب المالي"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    await act(async () => {
        setter.call(input, "صندوق المتجر");
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
        node.querySelector('[data-testid="financial-account-create-form"]').dispatchEvent(
            new Event("submit", { bubbles: true, cancelable: true }),
        );
        await Promise.resolve();
    });

    expect(createFinancialAccount).toHaveBeenCalledTimes(1);
    expect(createFinancialAccount.mock.calls[0][0]).toEqual(expect.objectContaining({
        name: "صندوق المتجر",
        account_type: "bank",
        currency: "SAR",
    }));
    expect(createFinancialAccount.mock.calls[0][0].idempotency_key).toMatch(/^financial-account-/);
});
