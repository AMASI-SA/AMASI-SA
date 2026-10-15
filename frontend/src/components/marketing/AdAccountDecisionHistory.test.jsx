import React, { act } from "react";
import { createRoot } from "react-dom/client";

import {
    getAdDecisionAccountSummaries,
    getAdDecisionHistory,
} from "../../services/adDecisionLearning";
import AdAccountDecisionHistory from "./AdAccountDecisionHistory";

jest.mock("../../services/adDecisionLearning", () => ({
    adDecisionError: () => "تعذر تحميل الحساب الجديد.",
    getAdDecisionAccountSummaries: jest.fn(),
    getAdDecisionHistory: jest.fn(),
}));

function deferred() {
    let resolve;
    let reject;
    const promise = new Promise((accept, decline) => {
        resolve = accept;
        reject = decline;
    });
    return { promise, resolve, reject };
}

function decision(id, name) {
    return {
        decision_id: id,
        action: "campaign.update",
        direct_snapchat: false,
        entity_name: name,
        reason: "سبب موثق",
        before: {},
        after: {},
        changes: {},
        expected: {},
        actual: {},
        execution: { status: "completed" },
        outcome: { status: "pending", deltas: {}, post_attribution: {} },
        evidence: { windows: {} },
        supporting_context: [],
    };
}

describe("AdAccountDecisionHistory", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        getAdDecisionAccountSummaries.mockResolvedValue([]);
        getAdDecisionHistory.mockReset();
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
        jest.clearAllMocks();
    });

    test("removes the previous account report immediately while the next request is pending or fails", async () => {
        const nextAccount = deferred();
        getAdDecisionHistory
            .mockResolvedValueOnce({
                items: [decision("change-a", "حملة الحساب الأول")],
                pagination: { page: 1, pages: 1, total: 1, limit: 5 },
            })
            .mockReturnValueOnce(nextAccount.promise);
        const onPageChange = jest.fn();

        await act(async () => root.render(
            <AdAccountDecisionHistory accountId="account-a" accountName="الحساب الأول" onPageChange={onPageChange} />,
        ));
        expect(container.textContent).toContain("حملة الحساب الأول");

        await act(async () => root.render(
            <AdAccountDecisionHistory accountId="account-b" accountName="الحساب الثاني" onPageChange={onPageChange} />,
        ));
        expect(container.textContent).not.toContain("حملة الحساب الأول");
        expect(container.querySelector("[data-testid='ad-decision-history-loading']")).not.toBeNull();

        await act(async () => {
            nextAccount.reject(new Error("network"));
            await Promise.resolve();
        });
        expect(container.textContent).not.toContain("حملة الحساب الأول");
        expect(container.textContent).toContain("تعذر تحميل الحساب الجديد");
    });
});
