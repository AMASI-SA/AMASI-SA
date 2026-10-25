import React, { act } from "react";
import { createRoot } from "react-dom/client";
import api from "../../lib/api";
import GoogleAdsIntelligenceCard from "./GoogleAdsIntelligenceCard";

jest.mock("../../lib/api", () => ({ __esModule: true, default: { get: jest.fn(), post: jest.fn(), put: jest.fn() } }));
let container, root;
const record = { analysis_id: "analysis_1", account_id: "123", date_from: "2026-10-13", date_to: "2026-10-13", created_at: "2026-10-13T12:00:00Z", result: { summary: "تحليل جوجل", period: { date_from: "2026-10-13", date_to: "2026-10-13", currency_code: "USD", time_zone: "America/New_York" }, recommendations: [{ recommendation_id: "rec_1", title: "ضبط الميزانية", rationale: "تحسن معدل التحويل", proposed_budget_native: "123.45" }], limitations: ["مبيعات سلة غير مسندة بالكامل"] } };
beforeEach(() => { api.get.mockReset(); api.post.mockReset(); api.put.mockReset(); container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container); globalThis.IS_REACT_ACT_ENVIRONMENT = true; });
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function render(props = {}) { await act(async () => root.render(<GoogleAdsIntelligenceCard dateFrom="2026-10-13" dateTo="2026-10-13" {...props} />)); }

test("dashboard Google intelligence reads saved scoped facts and links to the native governed workspace", async () => {
    api.get.mockResolvedValue({ data: { items: [record] } });
    await render();
    expect(api.get.mock.calls[0][0]).toBe("/integrations-v2/google_ads/ai/analyses"); expect(api.get.mock.calls[0][1].params).toMatchObject({ date_from: "2026-10-13", date_to: "2026-10-13" });
    expect(container.textContent).toContain("تحسن معدل التحويل"); expect(container.textContent).toContain("USD"); expect(container.textContent).toContain("America/New_York");
    expect(container.querySelector('a[href="/ads-manager?provider=google&tab=ai&account=123"]')).not.toBeNull();
    expect(container.textContent).not.toContain("123.45 ر.س"); expect(container.querySelector('button[aria-label*="اعتماد"]')).toBeNull(); expect(api.post).not.toHaveBeenCalled(); expect(api.put).not.toHaveBeenCalled();
});

test("old period analyses are not repurposed as recommendations for today", async () => {
    api.get.mockResolvedValue({ data: { items: [{ ...record, date_from: "2026-10-12", date_to: "2026-10-12", result: { ...record.result, period: { date_from: "2026-10-12", date_to: "2026-10-12" } } }] } });
    await render(); expect(container.textContent).toContain("لا يوجد تحليل محفوظ لهذه الفترة"); expect(container.textContent).not.toContain("تحسن معدل التحويل");
});

test("a stale dashboard analysis response cannot cross account or period boundaries", async () => {
    let finish;
    api.get.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; })).mockResolvedValueOnce({ data: { items: [{ ...record, account_id: "456", result: { ...record.result, summary: "الحساب الجديد" } }] } });
    await render({ accountId: "123" }); const signal = api.get.mock.calls[0][1].signal;
    await render({ accountId: "456" }); expect(signal.aborted).toBe(true);
    await act(async () => finish({ data: { items: [record] } })); expect(container.textContent).toContain("الحساب الجديد"); expect(container.textContent).not.toContain("تحليل جوجل");
});

test("the standalone Google card belongs to Google workspace and the dashboard uses shared periodic advice", () => {
    const source = require("fs").readFileSync(require("path").join(__dirname, "../../pages/AdvancedDashboard.jsx"), "utf8");
    const workspace = require("fs").readFileSync(require("path").join(__dirname, "GoogleAdsWorkspace.jsx"), "utf8");
    expect(source).toContain("<CampaignAdvisorCard />"); expect(source).not.toContain("<GoogleAdsIntelligenceCard");
    expect(workspace).toContain("<GoogleAdsIntelligenceCard accountId={accountId} dateFrom={dateFrom} dateTo={dateTo} onPreview={openRecommendationProposal} />");
    expect(source).toContain('["snapchat", "meta"].includes(item.provider) && item.action_type');
});


