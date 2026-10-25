import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleAnalysisAttempts from "./GoogleAnalysisAttempts";
import { selectGoogleAnalysisAttempts } from "../../services/googleAnalysisAttempts";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
const scope = { accountId: "123", dateFrom: "2026-10-25", dateTo: "2026-10-25" };
const attempt = { account_id: "123", campaign_id: null, date_from: scope.dateFrom, date_to: scope.dateTo,
    analysis_id: "attempt1", created_at: "2026-10-25T22:30:00Z", status: "failed", phase: "structured_analysis",
    error_code: "google_ai_recommendation_evidence_unknown", provider_writes: 0, duration_ms: 16885 };
let container, root;
beforeEach(() => {
    api.get.mockReset(); api.post.mockReset(); api.put.mockReset();
    api.get.mockResolvedValue({ data: { items: [attempt] } });
    container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GoogleAnalysisAttempts {...scope} {...props} />)); }

test("reads the selected period and exposes a safe failed phase without retrying AI", async () => {
    api.get.mockResolvedValue({ data: { items: [{ ...attempt, message: "private response", prompt: "private prompt", exception_type: "PrivateType" }] } });
    await render();
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(api.get.mock.calls[0][0]).toBe("/integrations-v2/google_ads/ai/analysis-attempts");
    expect(api.get.mock.calls[0][1].params).toEqual({ account_id: "123", date_from: scope.dateFrom, date_to: scope.dateTo });
    expect(container.textContent).toContain("لم يكتمل التحليل");
    expect(container.textContent).toContain("مراجعة توصيات الذكاء");
    expect(container.textContent).toContain(attempt.error_code);
    expect(container.textContent).toContain("لم تُغيّر هذه المحاولة حملات جوجل");
    expect(container.textContent).not.toMatch(/private|PrivateType/);
    expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});

test("filters account, period and campaign scope, and discards raw diagnostic fields", () => {
    const foreign = [{ ...attempt, account_id: "999" }, { ...attempt, date_to: "2026-10-24" }, { ...attempt, campaign_id: "456" }];
    expect(selectGoogleAnalysisAttempts({ items: [...foreign, { ...attempt, prompt: "hidden", error_code: "sensitive raw text" }] }, scope))
        .toEqual([{ analysisId: "attempt1", createdAt: attempt.created_at, status: "failed", phase: "structured_analysis", durationMs: 16885, errorCode: null, providerWrites: 0 }]);
    expect(selectGoogleAnalysisAttempts({ items: [attempt, foreign[2]] }, { ...scope, campaignId: "456" })).toHaveLength(1);
});

test("status display distinguishes running, complete and cancelled; zero writes is not inferred", async () => {
    api.get.mockResolvedValue({ data: { items: [
        { ...attempt, analysis_id: "running", status: "running", phase: "native_workspace", provider_writes: undefined },
        { ...attempt, analysis_id: "complete", status: "complete", phase: "save_analysis", provider_writes: 0 },
        { ...attempt, analysis_id: "cancelled", status: "cancelled", phase: "policy_read", provider_writes: 1 },
    ] } });
    await render();
    expect(container.textContent).toContain("التحليل جارٍ");
    expect(container.textContent).toContain("اكتمل التحليل");
    expect(container.textContent).toContain("توقفت محاولة التحليل");
    expect(container.textContent.match(/لم تُغيّر هذه المحاولة حملات جوجل/g)).toHaveLength(1);
});

test("account and campaign changes abort and discard older responses", async () => {
    let resolveOld;
    api.get.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    await render(); const oldSignal = api.get.mock.calls[0][1].signal;
    await render({ accountId: "222", campaignId: "789" });
    expect(oldSignal.aborted).toBe(true);
    await act(async () => resolveOld({ data: { items: [attempt] } }));
    expect(container.textContent).not.toContain(attempt.error_code);
    expect(container.textContent).toContain("لا توجد محاولات مسجلة");
});

test("diagnostic read failure is nonfatal and manual refresh performs only another GET", async () => {
    api.get.mockRejectedValueOnce({ response: { data: { detail: { message: "private backend text" } } } });
    await render();
    expect(container.textContent).toContain("تعذر تحميل سجل المحاولات");
    expect(container.textContent).not.toContain("private backend text");
    await act(async () => container.querySelector("button").click());
    expect(api.get).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain(attempt.error_code);
    expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});

test("missing scope makes no request and a period change clears the former records", async () => {
    await render({ dateTo: "" }); expect(api.get).not.toHaveBeenCalled();
    await render(); expect(container.textContent).toContain(attempt.error_code);
    await render({ dateTo: "2026-10-26" });
    expect(container.textContent).not.toContain(attempt.error_code);
});
