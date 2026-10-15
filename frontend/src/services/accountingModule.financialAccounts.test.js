import api from "../lib/api";

import {
    advanceFinancialAccountsTransition,
    archiveFinancialAccount,
    createFinancialAccount,
    createOpeningBalanceDraft,
    getFinancialAccountDefinitions,
    getFinancialAccounts,
    getFinancialAccountsTransition,
    getOpeningBalanceDrafts,
    postOpeningBalanceDraft,
    previewOpeningBalanceDraft,
    reverseOpeningBalanceDraft,
    reviewOpeningBalanceDraft,
    updateFinancialAccount,
    uploadOpeningBalanceEvidence,
} from "./accountingModule";

jest.mock("../lib/api", () => ({
    delete: jest.fn(),
    get: jest.fn(),
    patch: jest.fn(),
    post: jest.fn(),
}));

const BASE = "/financial-provider-apps/accounting-module/financial-accounts";

describe("financial accounts API contract", () => {
    beforeEach(() => {
        api.delete.mockReset();
        api.get.mockReset();
        api.patch.mockReset();
        api.post.mockReset();
        api.delete.mockResolvedValue({ data: { ok: true } });
        api.get.mockResolvedValue({ data: { ok: true } });
        api.patch.mockResolvedValue({ data: { ok: true } });
        api.post.mockResolvedValue({ data: { ok: true } });
    });

    test("uses the dedicated definitions, accounts, and transition routes", async () => {
        const createPayload = { name: "صندوق المتجر", idempotency_key: "account-request-1" };
        const updatePayload = { version: 1, name: "الصندوق الرئيسي" };
        const archivePayload = { version: 2, reason: "إغلاق الحساب" };
        const transitionPayload = { target: "transition_blocked", expected_revision: 0 };

        await getFinancialAccountDefinitions();
        await getFinancialAccounts();
        await createFinancialAccount(createPayload);
        await updateFinancialAccount("bank/id", updatePayload);
        await archiveFinancialAccount("bank/id", archivePayload);
        await getFinancialAccountsTransition();
        await advanceFinancialAccountsTransition(transitionPayload);

        expect(api.get).toHaveBeenNthCalledWith(1, `${BASE}/definitions`);
        expect(api.get).toHaveBeenNthCalledWith(2, BASE);
        expect(api.post).toHaveBeenNthCalledWith(1, BASE, createPayload);
        expect(api.patch).toHaveBeenCalledWith(`${BASE}/accounts/bank%2Fid`, updatePayload);
        expect(api.delete).toHaveBeenCalledWith(`${BASE}/accounts/bank%2Fid`, {
            data: archivePayload,
        });
        expect(api.get).toHaveBeenNthCalledWith(3, `${BASE}/transition`);
        expect(api.post).toHaveBeenNthCalledWith(2, `${BASE}/transition`, transitionPayload);
    });

    test("keeps draft, preview, review, post, and reverse as separate endpoints", async () => {
        const draftPayload = { idempotency_key: "opening-draft-1", lines: [] };
        const previewPayload = { version: 1, idempotency_key: "preview-request-1", note: "معاينة" };
        const reviewPayload = { version: 1, idempotency_key: "review-request-1", note: "مراجعة" };
        const postPayload = { version: 2, idempotency_key: "post-request-1", note: "ترحيل" };
        const reversePayload = { version: 3, idempotency_key: "reverse-request-1", note: "عكس" };

        await getOpeningBalanceDrafts();
        await createOpeningBalanceDraft(draftPayload);
        await previewOpeningBalanceDraft("draft/id", previewPayload);
        await reviewOpeningBalanceDraft("draft/id", reviewPayload);
        await postOpeningBalanceDraft("draft/id", postPayload);
        await reverseOpeningBalanceDraft("draft/id", reversePayload);

        const drafts = `${BASE}/opening-balances/drafts`;
        expect(api.get).toHaveBeenCalledWith(drafts);
        expect(api.post).toHaveBeenNthCalledWith(1, drafts, draftPayload);
        expect(api.post).toHaveBeenNthCalledWith(2, `${drafts}/draft%2Fid/preview`, previewPayload);
        expect(api.post).toHaveBeenNthCalledWith(3, `${drafts}/draft%2Fid/review`, reviewPayload);
        expect(api.post).toHaveBeenNthCalledWith(4, `${drafts}/draft%2Fid/post`, postPayload);
        expect(api.post).toHaveBeenNthCalledWith(5, `${drafts}/draft%2Fid/reverse`, reversePayload);
    });

    test("classifies uploaded evidence as an opening-balance source file", async () => {
        const file = new File(["evidence"], "opening.pdf", { type: "application/pdf" });

        await uploadOpeningBalanceEvidence({ purpose: "opening_balance", sectionId: "banks_cash", file });

        expect(api.post).toHaveBeenCalledTimes(1);
        const [url, form, options] = api.post.mock.calls[0];
        expect(url).toBe(`${BASE}/opening-balances/evidence`);
        expect(form.get("purpose")).toBe("opening_balance");
        expect(form.get("section_id")).toBe("banks_cash");
        expect(form.get("file")).toBe(file);
        expect(options).toEqual({ headers: { "Content-Type": "multipart/form-data" } });
    });
});
