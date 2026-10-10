import React, { act } from "react";
import { createRoot } from "react-dom/client";
import TikTokCampaignAnalysis from "./TikTokCampaignAnalysis";
import api from "../../lib/api";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { post: jest.fn() } }));
let container, root;
const campaign = { accountId: "70001", campaignId: "campaign-1", name: "Campaign 1" };
const result = (summary) => ({ data: { recommendation: { focus: "tracking", summary, next_step: "راجع التتبع." },
    context: { salla_evidence: { exact_campaign_id_records: 1, financial_orders: null, sales_sar: null, profit_sar: null } } } });
beforeEach(() => {
    api.post.mockReset();
    container = document.createElement("div"); document.body.appendChild(container);
    root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true;
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

test("changing campaign and dates cancels the request and rejects its late result", async () => {
    let oldResponse;
    api.post.mockImplementationOnce(() => new Promise((resolve) => { oldResponse = resolve; }))
        .mockResolvedValueOnce(result("Current analysis"));
    await act(async () => root.render(<TikTokCampaignAnalysis campaign={campaign} dateFrom="2026-10-03" dateTo="2026-10-09" />));
    const oldSignal = api.post.mock.calls[0][2].signal;
    await act(async () => root.render(<TikTokCampaignAnalysis campaign={{ ...campaign, campaignId: "campaign-2" }} dateFrom="2026-10-10" dateTo="2026-10-10" />));
    expect(oldSignal.aborted).toBe(true);
    await act(async () => oldResponse(result("Stale analysis")));
    expect(container.textContent).toContain("Current analysis");
    expect(container.textContent).not.toContain("Stale analysis");
    expect(api.post.mock.calls[1][1]).toEqual({ account_id: "70001", campaign_id: "campaign-2", from_date: "2026-10-10", to_date: "2026-10-10" });
    expect(container.querySelectorAll("video, img")).toHaveLength(0);
});

test("resource refusal is visible and does not manufacture financial results", async () => {
    api.post.mockRejectedValue({ response: { data: { detail: { message: "خدمة التحليل مشغولة؛ حاول بعد قليل." } } } });
    await act(async () => root.render(<TikTokCampaignAnalysis campaign={campaign} dateFrom="2026-10-03" dateTo="2026-10-09" />));
    expect(container.querySelector('[role="alert"]').textContent).toContain("خدمة التحليل مشغولة");
    expect(container.textContent).toContain("نتائج سلة المالية والأرباح غير مكتملة");
    expect(container.textContent).not.toContain("رابحة");
    expect(container.querySelectorAll("video, img")).toHaveLength(0);
});
