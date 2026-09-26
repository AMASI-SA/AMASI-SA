import React, { act } from "react";
import { createRoot } from "react-dom/client";

import {
    createFinancialAccount,
    getFinancialAccountDefinitions,
    getFinancialAccounts,
    getFinancialAccountsTransition,
    getOpeningBalanceDrafts,
    reverseOpeningBalanceDraft,
    uploadOpeningBalanceEvidence,
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

test("account creation retry keeps one request identity after a lost response", async () => {
    const stored = new Map();
    let loseFirstResponse = true;
    createFinancialAccount.mockImplementation(async (payload) => {
        if (!stored.has(payload.idempotency_key)) {
            stored.set(payload.idempotency_key, { ...ACCOUNT, id: "account-2" });
        }
        if (loseFirstResponse) {
            loseFirstResponse = false;
            throw new Error("response_lost_after_commit");
        }
        return stored.get(payload.idempotency_key);
    });
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
    await act(async () => { await Promise.resolve(); });
    await act(async () => {
        node.querySelector('[data-testid="financial-account-create-form"]').dispatchEvent(
            new Event("submit", { bubbles: true, cancelable: true }),
        );
        await Promise.resolve();
    });

    expect(createFinancialAccount).toHaveBeenCalledTimes(2);
    expect(createFinancialAccount.mock.calls[0][0]).toEqual(expect.objectContaining({
        name: "صندوق المتجر",
        account_type: "bank",
        currency: "SAR",
    }));
    expect(createFinancialAccount.mock.calls[0][0].idempotency_key).toMatch(/^financial-account-/);
    expect(createFinancialAccount.mock.calls[1][0]).toEqual(createFinancialAccount.mock.calls[0][0]);
    expect(stored.size).toBe(1);
    expect([...stored.values()].map((account) => account.id)).toEqual(["account-2"]);
});

test("changing account facts after a failed request rotates the request identity", async () => {
    createFinancialAccount.mockRejectedValue(new Error("network_error"));
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.financial_accounts.manage",
    ]);
    const input = node.querySelector('[aria-label="اسم الحساب المالي"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
    await act(async () => {
        setter.call(input, "الصندوق الأول");
        input.dispatchEvent(new Event("input", { bubbles: true }));
        node.querySelector('[data-testid="financial-account-create-form"]').dispatchEvent(
            new Event("submit", { bubbles: true, cancelable: true }),
        );
        await Promise.resolve();
    });
    await act(async () => {
        setter.call(input, "الصندوق الثاني");
        input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => {
        node.querySelector('[data-testid="financial-account-create-form"]').dispatchEvent(
            new Event("submit", { bubbles: true, cancelable: true }),
        );
        await Promise.resolve();
    });
    expect(createFinancialAccount.mock.calls[1][0].idempotency_key).not.toBe(
        createFinancialAccount.mock.calls[0][0].idempotency_key,
    );
});

test("reviewer sees stored FX identity and explicit zero balances including an all-zero opening", async () => {
    const permissions = [
        "accounting.financial_accounts.view",
        "accounting.opening_balances.view",
        "accounting.opening_balances.review",
    ];
    const foreign = {
        id: "draft-fx-zero", status: "previewed", version: 2,
        debit_total: "375.00", credit_total: "375.00",
        preview_entries: [{
            line_no: 1, entity_type: "bank", entity_id: "usd-bank", sub_account: "main",
            label: "بنك الدولار", original_currency: "USD", original_amount: "100.00",
            sar_amount: "375.00", side: "debit", evidence_file_id: "bank-evidence",
            fx_snapshot: {
                rate_to_sar: "3.75", fx_at: "2026-09-14T21:00:00Z",
                source: "Saudi Central Bank", evidence_file_id: "fx-evidence",
            },
        }],
        lines: [{
            line_no: 2, entity_type: "bank", entity_id: "cash-zero", sub_account: "main",
            label: "صندوق بلا رصيد", meaning: "zero", original_currency: "SAR",
            evidence_file_id: "zero-evidence",
            fx_snapshot: { rate_to_sar: "1", fx_at: "2026-09-14T21:00:00Z" },
        }],
    };
    await renderPage(permissions, [foreign]);
    expect(node.querySelector('[data-testid="opening-fx-review-table"]')).not.toBeNull();
    expect(node.textContent).toContain("3.75");
    expect(node.textContent).toContain("Saudi Central Bank");
    expect(node.textContent).toContain("usd-bank");
    expect(node.querySelector('[data-testid="opening-zero-balances"]')).not.toBeNull();
    expect(node.textContent).toContain("صندوق بلا رصيد");
    expect(node.textContent).toContain("zero-evidence");
    expect(node.querySelector('[data-testid="opening-draft-form"]')).toBeNull();

    act(() => root.unmount());
    node.replaceChildren();
    root = createRoot(node);
    await renderPage(permissions, [{
        ...foreign, id: "draft-all-zero", debit_total: "0.00", credit_total: "0.00",
        preview_entries: [],
    }]);
    expect(node.querySelector('[data-testid="opening-preview-table"]')).toBeNull();
    expect(node.querySelector('[data-testid="opening-zero-balances"]')).not.toBeNull();
    expect(hasButton("مراجعة وقفل الأدلة")).toBe(true);
});

test("reverse-only user uploads reversal evidence and completes reverse without draft management", async () => {
    uploadOpeningBalanceEvidence.mockResolvedValue({ source_file_id: "reversal-evidence-1", size: 42 });
    reverseOpeningBalanceDraft.mockResolvedValue({ id: "posted-1", status: "reversed" });
    await renderPage([
        "accounting.financial_accounts.view",
        "accounting.opening_balances.view",
        "accounting.journals.reverse",
    ], [{
        id: "posted-1", status: "posted", version: 3,
        debit_total: "115.00", credit_total: "115.00",
    }]);
    expect(node.querySelector('[data-testid="opening-draft-form"]')).toBeNull();
    const evidenceInput = node.querySelector('[aria-label="دليل سبب عكس الافتتاحية"]');
    const file = new File(["reason"], "reason.pdf", { type: "application/pdf" });
    Object.defineProperty(evidenceInput, "files", { value: [file] });
    await act(async () => {
        evidenceInput.dispatchEvent(new Event("change", { bubbles: true }));
        await Promise.resolve();
    });
    const note = node.querySelector('[aria-label="ملاحظة إجراء الافتتاحية"]');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    await act(async () => {
        setter.call(note, "سبب عكس موثق");
        note.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const reverseButton = [...node.querySelectorAll("button")].find((button) => (
        button.textContent.includes("عكس عند لحظة القطع نفسها")
    ));
    expect(reverseButton.disabled).toBe(false);
    await act(async () => {
        reverseButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
        await Promise.resolve();
    });
    expect(uploadOpeningBalanceEvidence).toHaveBeenCalledWith(expect.objectContaining({
        purpose: "opening_reversal_reason", file,
    }));
    expect(reverseOpeningBalanceDraft).toHaveBeenCalledWith("posted-1", expect.objectContaining({
        evidence_file_id: "reversal-evidence-1",
    }));
});
