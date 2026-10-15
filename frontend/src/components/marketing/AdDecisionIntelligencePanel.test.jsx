import React, { act } from "react";
import fs from "fs";
import path from "path";
import { createRoot } from "react-dom/client";

import {
    diagnoseAdBusinessChange,
    reviewAdaptiveSnapchat,
} from "../../services/adDecisionLearning";
import AdDecisionIntelligencePanel from "./AdDecisionIntelligencePanel";

jest.mock("../../services/adDecisionLearning", () => ({
    adDecisionError: (error) => error?.message || "تعذر التحليل.",
    diagnoseAdBusinessChange: jest.fn(),
    reviewAdaptiveSnapchat: jest.fn(),
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

function setValue(element, value) {
    const prototype = element instanceof HTMLTextAreaElement
        ? HTMLTextAreaElement.prototype
        : element instanceof HTMLSelectElement
            ? HTMLSelectElement.prototype
            : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
}

function diagnosis(id = "decision-1") {
    return {
        read_only: true,
        headline: { delta_pct: -12.4 },
        likely_contributors: [{
            decision_id: id,
            entity_id: "campaign-1",
            classification: "association",
            confidence: 0.63,
        }],
        decisions: [],
        caveats: ["decision_timing_and_direction_support_association_not_causation"],
    };
}

function adaptiveReview(reason = "راقب يومًا إضافيًا.") {
    return {
        proposals_created: 0,
        provider_write_reached: false,
        judgments: [{
            recommended_action: "observe",
            entity_type: "campaign",
            entity_id: "campaign-1",
            confidence: 0.72,
            reason_ar: reason,
            uncertainties: ["الطلبات اليدوية غير محسومة"],
        }],
    };
}

describe("AdDecisionIntelligencePanel", () => {
    let container;
    let root;

    beforeEach(() => {
        global.IS_REACT_ACT_ENVIRONMENT = true;
        container = document.createElement("div");
        document.body.appendChild(container);
        root = createRoot(container);
        diagnoseAdBusinessChange.mockReset();
        reviewAdaptiveSnapchat.mockReset();
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
        jest.clearAllMocks();
    });

    test("labels uncertainty clearly and calls only the diagnostic and adaptive review contracts", async () => {
        diagnoseAdBusinessChange.mockResolvedValue(diagnosis());
        reviewAdaptiveSnapchat.mockResolvedValue(adaptiveReview());

        await act(async () => root.render(<AdDecisionIntelligencePanel accountId="acc-1" />));

        expect(container.textContent).toContain("اسأل ذكاء ميزان");
        expect(container.textContent).toContain("المبيعات");
        expect(container.textContent).toContain("الطلبات");
        expect(container.textContent).toContain("مكسب المساهمة");
        expect(container.textContent).toContain("العائد الإعلاني ROAS");
        expect(container.textContent).toContain("تكلفة الطلب CPA");
        expect(container.textContent).toContain("اقتراح غير موثّق، وليست حقيقة");
        expect(container.textContent).toContain("لا إنشاء مقترح ولا كتابة إلى Snapchat");

        const [dateFrom, dateTo] = container.querySelectorAll("input[type='date']");
        const metric = container.querySelector("select");
        const suggestion = container.querySelector("textarea");
        await act(async () => {
            setValue(dateFrom, "2026-08-01");
            setValue(dateTo, "2026-08-07");
            setValue(metric, "contribution_profit");
            setValue(suggestion, "ربما سيولة منتصف الشهر أخف");
        });

        await act(async () => {
            container.querySelector("[data-testid='run-ad-business-diagnosis']").click();
            await Promise.resolve();
        });
        expect(diagnoseAdBusinessChange).toHaveBeenCalledWith(expect.objectContaining({
            accountId: "acc-1",
            dateFrom: "2026-08-01",
            dateTo: "2026-08-07",
            metric: "contribution_profit",
            signal: expect.any(AbortSignal),
        }));
        expect(container.textContent).toContain("التصنيف: ارتباط زمني");
        expect(container.textContent).toContain("ليس إثباتًا أن التعديل سبّب النتيجة");
        expect(container.textContent).toContain("توافق التوقيت والاتجاه يدل على ارتباط");

        await act(async () => {
            container.querySelector("[data-testid='run-adaptive-snapchat-review']").click();
            await Promise.resolve();
        });
        expect(reviewAdaptiveSnapchat).toHaveBeenCalledWith(expect.objectContaining({
            accountId: "acc-1",
            maxEntities: 5,
            userSuggestions: ["ربما سيولة منتصف الشهر أخف"],
            signal: expect.any(AbortSignal),
        }));
        expect(container.textContent).toContain("تمت المراجعة دون إنشاء أي مقترح");
        expect(container.textContent).toContain("راقب يومًا إضافيًا");
        expect(container.textContent).toContain("الطلبات اليدوية غير محسومة");

        const source = fs.readFileSync(path.join(__dirname, "AdDecisionIntelligencePanel.jsx"), "utf8");
        expect(source).not.toMatch(/snapchatCampaignManagement/);
        expect(source).not.toMatch(/createSnapchatManagementProposal/);
        expect(source).not.toMatch(/executeSnapchatManagementProposal/);
        expect(source).not.toMatch(/rollbackSnapchatManagementProposal/);
    });

    test("ignores a stale diagnosis after the selected account changes", async () => {
        const oldRequest = deferred();
        diagnoseAdBusinessChange
            .mockReturnValueOnce(oldRequest.promise)
            .mockResolvedValueOnce(diagnosis("decision-new"));

        await act(async () => root.render(<AdDecisionIntelligencePanel accountId="account-old" />));
        await act(async () => {
            container.querySelector("[data-testid='run-ad-business-diagnosis']").click();
        });

        await act(async () => root.render(<AdDecisionIntelligencePanel accountId="account-new" />));
        await act(async () => {
            container.querySelector("[data-testid='run-ad-business-diagnosis']").click();
            await Promise.resolve();
        });
        expect(container.textContent).toContain("decision-new");

        await act(async () => {
            oldRequest.resolve(diagnosis("decision-old"));
            await Promise.resolve();
        });
        expect(container.textContent).toContain("decision-new");
        expect(container.textContent).not.toContain("decision-old");
    });

    test("does not render a missing diagnostic delta as zero", async () => {
        diagnoseAdBusinessChange.mockResolvedValue({
            read_only: true,
            headline: { delta_pct: null },
            likely_contributors: [],
            decisions: [],
            caveats: [
                "snapchat_campaign_performance_sync_incomplete_for_previous_and_selected_windows",
            ],
        });

        await act(async () => root.render(<AdDecisionIntelligencePanel accountId="acc-1" />));
        await act(async () => {
            container.querySelector("[data-testid='run-ad-business-diagnosis']").click();
            await Promise.resolve();
        });

        expect(container.textContent).toContain("التغيّر غير متاح");
        expect(container.textContent).not.toContain("التغيّر 0%");
    });
});
