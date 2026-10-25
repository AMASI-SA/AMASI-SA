import React, { act } from "react";
import { createRoot } from "react-dom/client";
import CampaignRecommendations from "./CampaignRecommendations";
import api from "../lib/api";
jest.mock("../lib/api", () => ({ get: jest.fn(), post: jest.fn(), put: jest.fn() }));
jest.mock("react-router-dom", () => ({ Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a> }));
import fs from "fs";
import path from "path";

const page = fs.readFileSync(path.join(__dirname, "CampaignRecommendations.jsx"), "utf8");
const dashboard = fs.readFileSync(path.join(__dirname, "AdvancedDashboard.jsx"), "utf8");
const app = fs.readFileSync(path.join(__dirname, "../App.js"), "utf8");

test("all recommendations open in their own protected page", () => {
    expect(dashboard).toContain('to="/ads-manager/recommendations"');
    expect(dashboard).not.toContain("عرض جميع التوصيات في مساعد ميزان");
    expect(app).toContain('path="/ads-manager/recommendations"');
    expect(app).toContain("<Layout><CampaignRecommendations /></Layout>");
});

test("recommendation page explains priority, evidence, observation and financial impact before explicit execution", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    jest.useFakeTimers();
    const recommendation = {
        recommendation_id: "synthetic/high", provider: "meta", entity_level: "campaign", entity_name: "Synthetic priority campaign",
        priority: "high", account_name: "Synthetic ad account", generated_at: "2026-08-02T09:00:00Z",
        recommended_action: "DECREASE_BUDGET", action_type: "ads_write", executable: true, approval_available: true,
        why: "Synthetic reason: cost exceeds verified return", decision_facts: ["Synthetic fact: spend 100 and return 80"],
        observation_plan: "Synthetic observation: wait six hours", recommended_wait_hours: 6,
        success_criteria: ["Synthetic success: recover conversion"], risks: ["Synthetic risk: continued loss"],
        what_would_change_the_decision: ["Synthetic uncertainty: delayed conversion"],
        financial_impact: { period_estimated_contribution_sar: -20, forecast_hours: 6, forecast_delta_sar: 12.5 },
    };
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/latest")
        ? { snapshot_id: "synthetic-snapshot", generated_at: recommendation.generated_at,
            recommendations: [{ ...recommendation, recommendation_id: "low", entity_name: "Synthetic lower priority", priority: "low", executable: false }, recommendation] }
        : {} }));
    api.post.mockReset().mockResolvedValue({ data: { status: "completed" } });
    const confirm = jest.spyOn(window, "confirm").mockReturnValue(false);
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => root.render(<CampaignRecommendations />));
        expect(api.get).toHaveBeenCalledWith("/ads-manager/ai-monitor/latest");
        const cards = container.querySelectorAll('[data-testid="campaign-recommendations-list"] article');
        expect(cards).toHaveLength(2);
        expect(cards[0].textContent).toContain(recommendation.entity_name);
        expect(cards[1].textContent).toContain("Synthetic lower priority");
        expect(cards[0].textContent).toContain(recommendation.account_name);
        expect(cards[0].textContent).toContain("أُنشئت");
        expect(cards[0].textContent).toContain("6 ساعات");
        expect(api.post).not.toHaveBeenCalled();
        const expand = [...cards[0].querySelectorAll("button")].find((button) => button.textContent === "عرض التحليل المتقدم");
        await act(async () => expand.click());
        const explanation = container.querySelector('[data-testid="recommendation-explanation-synthetic/high"]');
        for (const text of [recommendation.why, ...recommendation.decision_facts, recommendation.observation_plan,
            ...recommendation.success_criteria, ...recommendation.risks, ...recommendation.what_would_change_the_decision,
            "أثر الحملة على الربح", "-20.00", "12.50"]) expect(explanation.textContent).toContain(text);
        const approve = [...cards[0].querySelectorAll("button")].find((button) => button.textContent === "موافقة وتنفيذ");
        await act(async () => approve.click());
        expect(confirm).toHaveBeenCalledTimes(1);
        expect(api.post).not.toHaveBeenCalled();
        confirm.mockReturnValue(true);
        await act(async () => approve.click());
        expect(api.post).toHaveBeenCalledTimes(1);
        expect(api.post).toHaveBeenCalledWith("/ads-manager/ai-monitor/recommendations/synthetic%2Fhigh/approve", { snapshot_id: "synthetic-snapshot" });
        expect(approve.disabled).toBe(true);
        expect([...cards[1].querySelectorAll("button")].some((button) => button.textContent === "موافقة وتنفيذ")).toBe(false);
    } finally {
        await act(async () => root.unmount()); container.remove(); confirm.mockRestore();
        jest.clearAllTimers(); jest.useRealTimers(); globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});

test("execution still requires explicit owner confirmation", () => {
    expect(page).toContain("window.confirm");
    expect(page).toContain("موافقة وتنفيذ");
    expect(page).toContain("snapshot_id: snapshot.snapshot_id");
});

test("periodic Google advice shares the list but cannot execute through the Snapchat or Meta gateway", async () => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const google = { recommendation_id: "google-cycle", provider: "google", account_id: "123",
        entity_level: "campaign", entity_name: "مراجعة التتبع في جوجل", account_name: "حساب جوجل",
        recommended_action: "REVIEW_TRACKING", action_type: "ads_write", priority: "high",
        executable: true, approval_available: true, diagnosis: "راجع التحويلات المثبتة" };
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/latest")
        ? { snapshot_id: "cycle", recommendations: [google, { ...google, recommendation_id: "snap", provider: "snapchat", entity_name: "سناب", executable: false }] } : {} }));
    api.post.mockReset();
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => root.render(<CampaignRecommendations />));
        const cards = container.querySelectorAll('[data-testid="campaign-recommendations-list"] article');
        expect(cards).toHaveLength(2);
        expect(cards[0].textContent).toContain("جوجل");
        expect(cards[0].querySelector('a[href="/ads-manager?provider=google&tab=ai&account=123"]')).not.toBeNull();
        expect([...cards[0].querySelectorAll("button")].some((button) => button.textContent === "موافقة وتنفيذ")).toBe(false);
        expect([...container.querySelectorAll("option")].some((option) => option.value === "google")).toBe(true);
        expect(api.post).not.toHaveBeenCalled();
    } finally {
        await act(async () => root.unmount()); container.remove(); globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});

test.each(["evidence", "decision_facts"])("structured %s renders beside Snapchat strings without writes", async (field) => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    const fact = (metric, value, currency_code = "USD") => ({
        evidence_id: "private-evidence-id", campaign_id: "private-campaign-id", metric, value,
        source: "private-provider-source", currency_code, time_zone: "Asia/Riyadh",
        date_from: "2026-10-24", date_to: "2026-10-25", observed_at: "2026-10-25T09:00:00Z",
    });
    const google = {
        recommendation_id: "structured-google", provider: "google", account_id: "123",
        entity_level: "campaign", entity_name: "Structured Google campaign", priority: "high",
        recommended_action: "REVIEW_TRACKING", action_type: "diagnostic", confidence: "high",
        [field]: [fact("spend_native", 12.34), fact("clicks", 1234), fact("roas", 2.5), fact("ctr", 0.025),
            fact("spend_native", 25, "SAR"), fact("conversions", 0), fact("ctr_pct", 7.25),
            fact("spend_native", { amount: "nested-value-must-not-render" }), { nested: { detail: "private-detail" } }],
        evidence_for: [fact("orders", 7)],
        evidence_against: [fact("roas", 0)],
        primary_hypothesis: { category: "TRACKING", confidence: "high", statement: "Review the verified measurements",
            evidence_for: [fact("clicks", 1234)], evidence_against: [fact("conversions", 0)] },
    };
    const snapchat = {
        recommendation_id: "string-snapchat", provider: "snapchat", entity_level: "campaign",
        entity_name: "Snapchat string campaign", priority: "medium", recommended_action: "DECREASE_BUDGET",
        action_type: "ads_write", executable: true, approval_available: true,
        evidence: ["صرف سناب 100 ر.س", "المخزون متاح", { text: "دليل سناب النصي محفوظ" }, { value: { nested: true } }],
    };
    api.get.mockImplementation((url) => Promise.resolve({ data: url.endsWith("/latest")
        ? { snapshot_id: "mixed-evidence-snapshot", recommendations: [google, snapchat] } : {} }));
    api.post.mockReset(); api.put.mockReset();
    const container = document.createElement("div"); document.body.appendChild(container);
    const root = createRoot(container);
    try {
        await act(async () => root.render(<CampaignRecommendations />));
        let cards = container.querySelectorAll('[data-testid="campaign-recommendations-list"] article');
        expect(cards).toHaveLength(2);
        for (const text of ["الصرف: 12.34 USD", "النقرات: 1,234", "العائد على الإنفاق: 2.5×", "نسبة النقر: 2.5%"])
            expect(cards[0].textContent).toContain(text);
        for (const text of snapchat.evidence.slice(0, 2)) expect(cards[1].textContent).toContain(text);
        expect(cards[1].textContent).toContain("دليل سناب النصي محفوظ");
        expect(cards[1].textContent).toContain("تفاصيل الدليل غير متاحة");
        const expand = [...cards[0].querySelectorAll("button")].find((button) => button.textContent === "عرض التحليل المتقدم");
        await act(async () => expand.click());
        const explanation = container.querySelector('[data-testid="recommendation-explanation-structured-google"]');
        for (const text of ["الصرف: 25 SAR", "التحويلات: 0", "نسبة النقر: 7.25%", "الطلبات: 7", "العائد على الإنفاق: 0×", "تفاصيل الدليل غير متاحة"])
            expect(explanation.textContent).toContain(text);
        for (const hidden of ["[object Object]", "private-evidence-id", "private-campaign-id", "private-provider-source",
            "nested-value-must-not-render", "private-detail", "evidence_id", "currency_code"])
            expect(container.textContent).not.toContain(hidden);
        const refresh = [...container.querySelectorAll("button")].find((button) => button.textContent === "تحديث");
        await act(async () => refresh.click());
        cards = container.querySelectorAll('[data-testid="campaign-recommendations-list"] article');
        expect(cards).toHaveLength(2);
        expect(cards[1].textContent).toContain("صرف سناب 100 ر.س");
        expect(api.post).not.toHaveBeenCalled();
        expect(api.put).not.toHaveBeenCalled();
    } finally {
        await act(async () => root.unmount()); container.remove(); globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    }
});

