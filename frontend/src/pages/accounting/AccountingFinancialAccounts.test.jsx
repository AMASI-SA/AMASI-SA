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
    reverseOpeningBalanceDraft: jest.fn(),
    reviewOpeningBalanceDraft: jest.fn(),
    updateFinancialAccount: jest.fn(),
    uploadOpeningBalanceEvidence: jest.fn(),
}));

const DEFINITIONS = {
    account_types: ["bank", "cash"],
    opening_categories: ["banks_cash", "equity"],
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

test("renders the real financial accounts page and keeps explicit authorities separate", async () => {
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.opening_balances.approve",
    ]);

    expect(node.querySelector('[data-testid="financial-accounts-page"]')).not.toBeNull();
    expect(node.textContent).toContain("بنك الإنماء");
    expect(node.querySelector('[data-testid="financial-account-create-form"]')).toBeNull();
    expect(node.querySelector('[data-testid="unified-opening-balances"]')).toBeNull();
    expect(node.textContent).not.toContain("إيقاف الكتابات للانتقال");
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
    expect(node.textContent).not.toContain("مراجعة وقفل الأدلة");
    expect(node.textContent).not.toContain("ترحيل عبر ميزان 2");
    expect(node.textContent).toContain("إيقاف الكتابات للانتقال");
});

test("review post and reverse controls require their exact permission and draft state", async () => {
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.opening_balances.view",
        "accounting.opening_balances.review",
    ], [{
        id: "draft-review",
        status: "draft",
        version: 1,
        debit_total: "115.00",
        credit_total: "115.00",
    }]);
    expect(node.textContent).toContain("مراجعة وقفل الأدلة");
    expect(node.textContent).not.toContain("ترحيل عبر ميزان 2");

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
    expect(node.textContent).toContain("ترحيل عبر ميزان 2");
    expect(node.textContent).not.toContain("إنشاء قيد عكس إلحاقي");

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
    expect(node.textContent).toContain("إنشاء قيد عكس إلحاقي");
    expect(node.textContent).not.toContain("ترحيل عبر ميزان 2");
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
